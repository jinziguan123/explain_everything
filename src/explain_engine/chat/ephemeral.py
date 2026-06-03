"""Phase 11 Wave 1 + Phase 18: EphemeralChatSession — in-memory chat REPL pre-persistent /deepen.

REPL 启动时建. user 输自然语言时调 handle_user_input(text, llm) 走 LLM system-1
chat (transcript in-memory, 不持久). 显式 /deepen [Q] 触发 promote_to_persistent
→ 进 bootstrap pipeline (Phase 17.2 classify + dispatch yaml + light_llm) → 切到
真 ChatSession (sid 落盘).

Phase 18 起 user 输自然语言不再自动 promote — system-1 chat 不写 storage_v2,
显式 escalate 才进 reasoning pipeline. 见 docs/plans/2026-05-26-phase-18-deepen-design.md.

设计: docs/plans/2026-05-18-phase11-repl-unification-design.md §5
+ docs/plans/2026-05-26-phase-18-deepen-design.md
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from explain_engine.chat.chat_copy import STATUS_THINKING
from explain_engine.chat.session import ChatEvent, ChatSession, ChatStateDict
from explain_engine.config import make_light_llm_client
from explain_engine.engines.bootstrap import bootstrap_phenomena
from explain_engine.hitl.cli_interactive import review_phenomena_async
from explain_engine.llm.client import Message
from explain_engine.llm.errors import LLMError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.state import CognitiveState

if TYPE_CHECKING:
    from explain_engine.llm.client import LLMClient
    from explain_engine.persistence.storage_v2 import StorageV2


@dataclass
class EphemeralChatSession:
    """In-memory chat session pre-persistence.

    REPL 启动时建. 字段:
    - storage: StorageV2 实例 (用于 promote 时读 knowledge/variables.json lexicon)
    - state: 空 CognitiveState (graph empty, budget=0)
    - chat_state: 默认 ChatStateDict (budget 10/50) — promote 后拷到 real ChatSession
    - transcript: 空 list (ephemeral 不持久化)
    - input_provider: REPL 注入 async input callable (slash handler / HITL 用)
    - memory_md: 空 (ephemeral 不写 memory)

    Properties:
    - sid: 永 None
    - is_ephemeral: 永 True

    Method:
    - handle_user_input(text, llm): Ephemeral 下 LLM system-1 chat (Phase 18).
    - promote_to_persistent(question, llm): 跑 bootstrap + HITL + save → real ChatSession.
      失败 (LLMError / SchemaValidationError) → 抛, caller 留 ephemeral.

    Note: LLM client 在 promote_to_persistent(llm=...) 显式传入仍 work;
    Phase 18 Task 12 加 llm dataclass field 让 REPL outer loop 构造 ephemeral
    时注入, /deepen slash 内 fallback `chat.llm` 调 promote. 两路兼存 (显式 arg
    优先 — promote 内不读 self.llm), 简化 REPL 注入唯一通道.
    """

    storage: StorageV2
    state: CognitiveState = field(
        default_factory=lambda: CognitiveState.bootstrap("", budget=0)
    )
    chat_state: ChatStateDict = field(default_factory=ChatStateDict)
    transcript: list[dict] = field(default_factory=list)
    input_provider: Callable[[str], Awaitable[str]] | None = None
    memory_md: str = ""
    # Phase 18 Task 12: REPL outer loop 注入用. /deepen slash handler 通过
    # `getattr(chat, 'llm', None)` 取此 field 调 promote_to_persistent.
    # Wave 2 不动 repl_entry.py — production 路径需 Wave 3 Task 16 接.
    llm: LLMClient | None = None

    # Phase 19 真终端 Bug C: textual TUI app 反向引用. ExplainChatApp.__init__
    # set self.chat.tui_app = self, 之后切 chat var (promote / reset) 时同步.
    # slash handler 用 `chat.tui_app._mount_status(label) / _unmount_status()`
    # 替老 Rich `Console().status(...)` — Rich spinner 在 textual alt screen
    # 不渲染, 用户 5-15s LLM 调用期间看屏幕静止以为卡死. helper 内部 fallback
    # 检测 tui_app=None → 走 Rich path 保 backward compat (CLI batch / test).
    # 类型 `Any` 避 circular import (tui_app.py imports session.py).
    tui_app: object | None = None

    @property
    def sid(self) -> None:
        return None

    @property
    def is_ephemeral(self) -> bool:
        return True

    async def handle_user_input(
        self,
        text: str,
        llm: LLMClient,
    ):
        """Phase 18: Ephemeral 下 LLM system-1 chat (Phase 20.3 端到端流式).

        yield 流 (success, 流式):
            status_start("思考中...") → [首 chunk 到达] status_end →
            thinking_delta* → assistant_text_delta* → turn_complete
        yield 流 (provider 不流式, e.g. openai): status_start → status_end →
            [thinking_text] → assistant_text → turn_complete  (整段 fallback)
        yield 流 (LLMError):
            status_start → [LLM raise] → status_end → slash_error → return

        transcript in-memory append (不持久 — storage_v2 不写 transcript.jsonl).
        LLM 失败 (LLMError) transcript 不变 (retry 友好).

        Phase 20.3 流式设计:
        - llm.chat(..., on_delta=emit) 逐 chunk 回调, 经 stream_llm 桥成
          (kind, payload) 事件流. kind=text → assistant_text_delta;
          kind=thinking → thinking_delta; kind=done → 完整 Response.
        - status_end 在首个 delta 到达时 yield (spinner 一关, 文字接着流);
          无任何 delta (provider 非流式 / 空回复) 时在 done 后 yield, 再走整段
          assistant_text fallback (TUI 同时认 delta 与整段两种事件).
        - 错误路径也保证 status_end (TUI _unmount_status 幂等, 重复 yield 安全).
        """
        # local import 避 module-load 期循环 (streaming 仅 chat 层用)
        from explain_engine.chat.streaming import stream_llm

        # Phase 19 Task 8: 调 LLM 前 yield status_start (spinner mount signal)
        yield ChatEvent(type="status_start", content=STATUS_THINKING)

        prompt = load_prompt("ephemeral_chat")
        messages: list[Message] = [Message(role="system", content=prompt["system"])]
        # 拼现有 transcript (in-memory)
        for msg in self.transcript:
            messages.append(Message(role=msg["role"], content=msg["content"]))
        messages.append(Message(role="user", content=text))

        resp = None
        any_text_delta = False
        spinner_closed = False
        try:
            async for kind, payload in stream_llm(
                lambda emit: llm.chat(messages, on_delta=emit)
            ):
                if kind == "done":
                    resp = payload
                    break
                # 首个 delta (text / thinking) 到达 → 关 spinner
                if not spinner_closed:
                    yield ChatEvent(type="status_end", content=None)
                    spinner_closed = True
                if kind == "text":
                    any_text_delta = True
                    yield ChatEvent(type="assistant_text_delta", content=payload)
                elif kind == "thinking":
                    yield ChatEvent(type="thinking_delta", content=payload)
        except LLMError as exc:
            if not spinner_closed:
                yield ChatEvent(type="status_end", content=None)
            yield ChatEvent(
                type="slash_error",
                content=f"LLM 调用失败: {type(exc).__name__}: {exc}",
            )
            return

        # 无任何 delta 涌出 (provider 非流式 / 空回复) → 现在才关 spinner
        if not spinner_closed:
            yield ChatEvent(type="status_end", content=None)

        assert resp is not None  # stream_llm 必 yield ("done", result) 或抛
        assistant_text = resp.text
        # transcript append (in-memory, 不持久)
        self.transcript.append({"role": "user", "content": text})
        self.transcript.append({"role": "assistant", "content": assistant_text})

        # Fallback: provider 没流式 (无 text delta) → 整段补 thinking_text +
        # assistant_text (TUI 老分支). 已流式 (any_text_delta) 时不重复发整段.
        if not any_text_delta:
            if resp.reasoning:
                yield ChatEvent(type="thinking_text", content=resp.reasoning)
            yield ChatEvent(type="assistant_text", content=assistant_text)

        yield ChatEvent(type="turn_complete", content=None)

    async def promote_to_persistent(
        self,
        question: str,
        llm: LLMClient,
    ) -> ChatSession:
        """Trigger bootstrap + HITL + save, 返 real ChatSession.

        失败 (bootstrap LLM error / SchemaValidationError) → 抛, caller 留 ephemeral.

        步骤:
        1. Load lexicon prior (Phase 10) — 没 lexicon 时 lexicon=[]
        2. bootstrap_phenomena(question, llm, lexicon=...) — LLM call (可抛 LLMError)
        3. review_phenomena_async(phenomena, input_provider) — Wave 1 stub 全 accept;
           Wave 2 接 k/e/d interactive 流
        4. Build CognitiveState + Session + save (SessionStore)
        5. Build real ChatSession(sid, llm) + 拷 ephemeral.chat_state (含 /budget 改的)
        6. 拷 input_provider 让 sub-prompt 仍走 prompt_toolkit
        """
        # Phase 10 lexicon prior. 本地 import 避 module-load 时拉 lexicon (engines).
        from explain_engine.engines.lexicon import _load_lexicon

        lexicon_path = self.storage.knowledge_dir() / "variables.json"
        lexicon_data = _load_lexicon(lexicon_path)
        lexicon = lexicon_data["variables"]

        # Phase 16: 取 stable theories (filter rejected), fallback 空 list 不阻塞
        stable_theories: list = []
        try:
            from explain_engine.engines.theory.cache import get_active_theories

            try:
                from explain_engine.embedding.bge_m3 import get_embedder
                embedder = get_embedder()
            except Exception:
                embedder = None  # BGE-M3 unavailable → stale cache OK
            cache = get_active_theories(self.storage, embedder=embedder)
            stable_theories = [
                t for t in cache.stable_theories
                if t.id not in cache.rejected_theory_ids
            ]
        except Exception as exc:
            import logging
            logging.warning(
                f"Phase 16 theory cache load failed: {type(exc).__name__}: {exc}"
            )
            stable_theories = []  # 失败不阻塞 bootstrap

        # bootstrap (raise → caller 留 ephemeral; ephemeral.state 不动)
        # Phase 17.2 Feature A: light_llm for classify (haiku 走 fallback 主 LLM
        # 若未配 LLM_LIGHT_*).
        light_llm = make_light_llm_client()
        # Phase 19 真终端 Bug C: _spinner helper — textual tui_app spinner 真显;
        # batch / test 模式 fallback Rich Console.status. 替老 `_console.status`
        # 直接调 (Rich Spinner 在 textual alt screen 不渲染, 用户 5-15s LLM 期间
        # 看屏幕静止以为卡死). chat.tui_app 由 ExplainChatApp.__init__ 注入.
        from explain_engine.chat.slash_commands import _spinner
        async with _spinner(self, "[bold green]调 LLM 生现象...[/bold green]"):
            phenomena = await bootstrap_phenomena(
                question,
                llm,
                lexicon=lexicon if lexicon else None,
                lexicon_top_k=20,
                theories=stable_theories,  # Phase 16
                light_llm=light_llm,  # Phase 17.2 Feature A
            )

        # HITL — Wave 1 stub: 全 accept. Wave 2 接 input_provider + k/e/d.
        final_phenomena = await review_phenomena_async(
            phenomena,
            self.input_provider,
        )

        # Build state + meta + save
        from explain_engine.config import Settings

        settings = Settings()
        new_state = CognitiveState.bootstrap(
            question, budget=settings.default_budget
        )
        for p in final_phenomena:
            new_state.graph.add_node(p)

        meta = SessionMeta.new(question=question)
        session = Session(meta=meta, state=new_state)
        SessionStore().save(session)

        # Build real ChatSession + 拷 ephemeral.chat_state (含 /budget 改的 limit)
        real_chat = ChatSession(meta.session_id, llm=llm)
        real_chat.chat_state = self.chat_state
        real_chat.input_provider = self.input_provider
        # Phase 18 Wave 3 hotfix (C-1): 持久化 chat_state 让 outer loop 重 build 时能 load 回.
        # 否则 outer loop 用 metadata.sid 重 build ChatSession → load_chat_state 返 None →
        # 用户在 ephemeral 设的 /budget 限制 silent 丢失.
        real_chat.persist()
        return real_chat
