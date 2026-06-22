"""Phase 9 Wave C.1: ChatSession outer orchestrator + persistence integration.

ChatSession 是 Phase 9 conversational engine 的最外层"循环宿主":
- __init__ 加载 5 sidecar files (graph + metadata + transcript + memory + chat_state)
- handle_user_input(text) 路由 slash command / 非 slash 对话
- persist() / close() flush chat_state 到 storage_v2

C.1 不实现 LLM ↔ tools while-loop (= query_loop),只搭骨架 + persistence;
Task C.2 会把 query_loop 接进 handle_user_input 的非 slash 分支。

参考:
- docs/plans/2026-05-17-conversational-cognitive-engine-design.md
- docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave C.1
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING, Any

from explain_engine.chat.budget import BudgetCounter
from explain_engine.chat.chat_copy import STATUS_THINKING
from explain_engine.chat.hooks import (
    SESSION_MEMORY_TRIGGER_INTERVAL,
    lifecycle_post_turn,
    reflect_post_turn,
    session_memory_writer,
)
from explain_engine.persistence.session import SessionStore
from explain_engine.persistence.storage_v2 import StorageV2
from explain_engine.schema.state import CognitiveState

if TYPE_CHECKING:
    from explain_engine.llm.client import LLMClient


class ChatSessionLoadError(Exception):
    """Phase 9 Wave C.1: 加载 session 的 5 sidecar files 之一失败.

    Attributes:
        sid: session id
        file: 哪个 file (transcript / memory / chat_state / metadata / graph)
        cause: 底层 exception

    用途: 给用户清晰错误信息 "failed to load chat_state.json for session 's_xxx'"
    而非裸 json.decoder traceback.
    """

    def __init__(self, sid: str, file: str, cause: Exception):
        self.sid = sid
        self.file = file
        self.cause = cause
        super().__init__(
            f"failed to load {file} for session {sid!r}: {type(cause).__name__}: {cause}"
        )


@dataclass
class ChatStateDict:
    """Phase 9 chat-specific state (persisted to chat_state.json).

    字段:
    - budget_per_turn_remaining / budget_per_turn_limit:
        单 turn 内 tool 调用 tracking. 每 turn 开始重置为 limit.
        2026-05-20 重构: per_turn 仅 tracking, 不 enforce — 删 mid-loop check.
    - budget_per_session_remaining / budget_per_session_limit:
        整 session 跨 turn 累计预算. 不重置. 由 handle_user_input 在 turn-start
        check session_exhausted(), 拒受新 user 输入. mid-turn 不打断.
    - last_compact_at_turn: 上次 memory.md compact 在哪个 turn (Phase 9 Wave E).
    - turn_count: 累计 user input 次数.
    - last_input_alignment: Phase 8 input_validation 报告 (Phase 8 B fix 折叠位置).
        等价于 Phase 8 CognitiveState.last_input_alignment_report,
        Wave E.2 完成迁移后从 state 移除, 仅保留在此. 字段名缩写不带 _report
        以减少 chat_state.json 噪声.

    Budget sentinel 约定 (2026-05-20 hotfix):
    - limit == 0 → unlimited (default). exhausted() 永 False. /budget 显 "无限".
    - limit > 0  → finite. exhausted at remaining <= 0. turn-start enforce.
    - 默 0/0 = 全 unlimited. 用户 /budget 可设有限值 (含 0 = 还原 unlimited).
    """

    budget_per_turn_remaining: int = 0
    budget_per_session_remaining: int = 0
    budget_per_turn_limit: int = 0
    budget_per_session_limit: int = 0
    last_compact_at_turn: int = 0
    turn_count: int = 0
    last_input_alignment: dict | None = None


@dataclass
class ChatEvent:
    """Base event yielded from query_loop or slash handlers.

    Phase 9 Wave C.2 派生子类 (AssistantText / ToolUse / ToolResult / TurnComplete).
    C.1 + Wave F.1 + 2026-05-18 slash handler 用 type 字段做 stub event 区分.

    `content` payload contract per event type:
    - assistant_text / slash_help / slash_show / slash_save / slash_compact /
      slash_resume: str (user-visible info)
    - slash_quit: str (farewell text)
    - slash_error / slash_unknown: str (error text)
    - tool_use: dict (Wave C.2 ToolUseEvent 子类替代)
    - tool_result: dict (Wave C.2 ToolResultEvent 子类替代)
    - slash_switch_session: dict {"sid": str} — REPL 据此 in-process 切到新 sid.
      Producer: _handle_resume (Wave 4).
      Consumer: cli._run_chat_repl_async (Wave 2) / repl_entry.enter_repl_async.
    - slash_reset_to_ephemeral: None — REPL 据此清屏 + aclose 当前 chat +
      回 EphemeralChatSession 启动态 + reprint banner. 不带 payload.
      Producer: _handle_new (2026-05-20 重构后).
      Consumer: repl_entry.enter_repl_async (主路径) /
                cli._run_chat_repl_async (子命令路径 — 仅 exit, 提示重启).
    - slash_next_step_hint: str — 灰色 dim 渲染. 在普通 slash output event 之后,
      给用户提示当前 stage 下推荐的下一步命令. Producer: with_stage_gate decorator
      (chat/slash_stage_rules.py, Phase 14).
    - slash_deepen_promoted: str (info text) + metadata={"sid": real_chat.sid}
      — Phase 18 起新约定: REPL outer loop 用 metadata.sid 切 chat var 到新建的
      ChatSession (跟 slash_switch_session 用 content["sid"] 不同 pattern,
      因为 slash_deepen_promoted 还要带人话 info 给用户看, content 留给字符串).
      Producer: _handle_deepen (Phase 18).
      Consumer: repl_entry.enter_repl_async (Phase 18 Task 17).
    - slash_thinking_toggle: str (zh msg echo) + metadata={"visible": bool}.
      Phase 19 Wave 4 Task 21: /thinking on|off slash 触发, REPL 接 → 强制 set
      _thinking_visible = metadata["visible"] (跟 action_toggle_thinking
      Ctrl+O 等价路径, 但 set 而非 toggle — 保证 on/off 幂等), 同步现 mount
      Collapsible.collapsed, 再 echo 中文 msg 给用户.
      Producer: _handle_thinking (chat/slash_commands.py).
      Consumer: tui_app._render_event 走 _sync_thinking_collapsibles helper.
      textual 框架渲染.
    - thinking_text: str — LLM reasoning 段内容 (extended thinking / reasoning_content).
      Phase 19 起约定: 在 assistant_text 之前 yield, 跟 Response.reasoning 字段对齐.
      Producer: ephemeral.handle_user_input (resp.reasoning 非 None 时), ChatSession
      query_loop 末尾 (Phase 20 实装 — ToolsResponse 加 reasoning field 后, 现 Phase 19
      ChatSession 保守只 yield status_start/end, 不 yield thinking_text).
      Consumer: tui_app._render_event 走 Collapsible mount (dim color, 默 expand,
      Ctrl+O 折叠). textual 框架渲染.
    - status_start: str — 描述当前 LLM / 长时操作 ("思考中..." / "启动深度建模 — classify 中...").
      Phase 19 起约定: 跟 status_end 配对, 中间夹 long-running 操作. 单 turn 可有多对.
      Producer: ephemeral / ChatSession handle_user_input 调 LLM 前 yield, _handle_deepen
      调 promote 前 yield.
      Consumer: tui_app._render_event mount LoadingIndicator widget + 灰 Static label.
    - status_end: None — 清掉前一个 status_start 显示 (LLM 调用 / 长时操作完成 signal).
      Phase 19 起约定: try/finally 包 LLM 调 — 错误也 yield (保证 spinner 一定清).
      Producer: ephemeral / ChatSession / _handle_deepen, 跟 status_start 配对.
      Consumer: tui_app._render_event unmount LoadingIndicator + Static label.
    """

    type: str
    content: Any = None
    metadata: dict | None = None
    # Phase 16.2 Wave 4: optional metadata, 仅 /predict /counterfactual 等需要
    # wrapper 反解信息时由 handler 显式塞 (例 {"intervention": text}). 其他 19
    # 个 handler 完全不感知该字段. _wrap_handler 用 getattr 兼容老 event.


class ChatSession:
    """Phase 9 outer orchestrator. Wraps query_loop (Task C.2)."""

    def __init__(self, sid: str, llm: LLMClient | None = None):
        """加载 session 的 5 sidecar files.

        Args:
            sid: session id
            llm: optional LLMClient — slash handler 调 bootstrap / 其他需 LLM
                 的操作时通过 chat.llm 访问. 默认 None (backward compat: 老 caller +
                 不需 LLM 的 slash 不受影响).

        Phase 9 Wave C.1 fix · I1: 去掉了 storage 参数 — 内部 SessionStore
        本来就 env-driven (EXPLAIN_HOME / EXPLAIN_PROJECT_ID), 传 custom
        storage 会和内部 SessionStore() 的 StorageV2 silent split.

        Raises:
            FileNotFoundError: session 不存在 (metadata/graph 缺失)
            ChatSessionLoadError: 5 sidecar 中任一损坏 (I2)
        """
        self.sid = sid
        self.llm = llm  # NEW: for /new handler chain (2026-05-18 slash 扩展)
        # F-1 (2026-05-18 Wave 5 review follow-up): optional async input callable
        # for slash handler 需 sub-prompt (e.g. /resume picker 收选号). REPL caller
        # (cli._run_chat_repl_async) 启动时 set →
        # lambda prompt: read_input(pt_session, prompt_text=prompt).
        # Handler 用 `await chat.input_provider(prompt)` 拿用户输入, 自动走
        # prompt_toolkit (bottom toolbar / ctrl+o / 中文 backspace fix).
        # 默认 None — 旧 caller (test / cli new 路径) 不影响, handler fallback
        # to_thread(input).
        self.input_provider: Callable[[str], Awaitable[str]] | None = None
        self.storage = StorageV2()  # env-based default
        self._session_store = SessionStore()  # for graph + metadata
        # Load Session (metadata + state.graph)
        try:
            self._session = self._session_store.load(sid)
        except FileNotFoundError:
            # session 不存在是 expected 语义, 不 wrap
            raise
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ChatSessionLoadError(sid, "metadata or graph", exc) from exc

        self.state: CognitiveState = self._session.state

        # Load sidecars (I2: wrap each with ChatSessionLoadError naming the file)
        try:
            self.transcript: list[dict] = self.storage.load_transcript(sid)
        except json.JSONDecodeError as exc:
            raise ChatSessionLoadError(sid, "transcript.jsonl", exc) from exc

        try:
            self.memory_md: str = self.storage.load_memory(sid)
        except (OSError, UnicodeDecodeError) as exc:
            raise ChatSessionLoadError(sid, "memory.md", exc) from exc

        try:
            chat_state_dict = self.storage.load_chat_state(sid)
        except json.JSONDecodeError as exc:
            raise ChatSessionLoadError(sid, "chat_state.json", exc) from exc

        self.chat_state: ChatStateDict = (
            ChatStateDict(**chat_state_dict) if chat_state_dict
            else ChatStateDict()
        )

        # Phase 9 E.2 B fix: hydrate state.last_input_alignment_report from
        # persisted chat_state.last_input_alignment dict. Phase 8 留下的 in-memory
        # only 限制现在通过 chat_state sidecar 跨进程持久化 (用户 Layer 1 真 LLM
        # run 后 explain check 看到 "(not checked)" fallback bug 由此修复).
        if self.chat_state.last_input_alignment is not None:
            try:
                from explain_engine.engines.input_validation import (
                    InputAlignmentReport,
                )
                self.state.last_input_alignment_report = (
                    InputAlignmentReport.model_validate(
                        self.chat_state.last_input_alignment
                    )
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    f"failed to hydrate last_input_alignment for {sid}: "
                    f"{type(exc).__name__}: {exc}"
                )

        # Wave D.2: hint set by reflect_post_turn 上一 turn, 下一 turn 的
        # assemble_system_prompt 读取 → 用一次后清 None (one-shot consumption,
        # 防 LLM 一直被同一 stale hint 干扰).
        self.next_turn_hint: str | None = None

        # Wave D.2: hold strong refs to background tasks (session_memory_writer)
        # 防 asyncio GC 在 task done 前杀掉. task.add_done_callback(discard)
        # 自清, 不会无界增长. 同时也满足 RUF006 lint (asyncio.create_task return
        # value 必须被引用).
        self._background_tasks: set[asyncio.Task[None]] = set()

        # Phase 19 真终端 Bug C: textual TUI app 反向引用. ExplainChatApp.__init__
        # 或 _switch_to_chat_session 时 set self.chat.tui_app = self. slash handler
        # 用 `chat.tui_app._mount_status(label)` / `_unmount_status()` 替老 Rich
        # `Console().status(...)` — Rich spinner 在 textual alt screen 不渲染,
        # 用户 5-15s LLM 调用期间看屏幕静止以为卡死. helper fallback 检测
        # tui_app=None → 走 Rich path 保 backward compat (CLI batch / test).
        # 类型 `object | None` 避 circular import (tui_app.py imports session.py).
        self.tui_app: object | None = None

    @property
    def turn_count(self) -> int:
        return self.chat_state.turn_count

    @property
    def budget(self) -> BudgetCounter:
        """Wave D.1: thin BudgetCounter wrapper over chat_state's 4 budget fields.

        每次 read 新建一个 BudgetCounter (无 state, 廉价); ChatStateDict 仍是
        source of truth, 所以多个 BudgetCounter view 看到的值一致.
        """
        return BudgetCounter(self.chat_state)

    def is_slash_command(self, text: str) -> bool:
        return text.strip().startswith("/")

    async def handle_user_input(
        self,
        text: str,
        llm: LLMClient | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """Process one user input. Yields events (text / tool_use / tool_result / TurnComplete).

        - slash command → dispatch_slash 本地 intercept (Wave F.1), 不计入
          transcript / turn_count, 不消耗 budget (因为没调 LLM).
        - 非 slash + llm=None → C.1 backward compat: yield placeholder event
        - 非 slash + llm 不为 None → C.2: append transcript + bump turn + reset budget
            + dispatch 到 query_loop (LLM ↔ tools while-loop), 最后 persist.
        """
        # Web 端 llm 通过参数传入; slash handler / _auto_deepen 等通过
        # chat.llm 访问. 确保注入的 llm 对所有下游路径可见.
        if llm is not None and self.llm is None:
            self.llm = llm

        if self.is_slash_command(text):
            cmd_name = text.strip().split()[0].lstrip("/")
            if cmd_name == "deepen":
                # /deepen 走流式路径: status 事件边产生边推给 web 前端,
                # 而非等整条管线跑完再一次性返回.
                async for ev in self._stream_deepen(text):
                    yield ev
            else:
                from explain_engine.chat.slash_commands import dispatch_slash
                events = await dispatch_slash(self, text)
                for ev in events:
                    yield ev
            # Web SSE 前端依赖 turn_complete 退出 streaming 状态;
            # slash 路径绕过 query_loop, 需手动补发.
            # TUI 的 _consume_slash_events 直接调 dispatch_slash, 不走此路径.
            yield ChatEvent(type="turn_complete", content=None)
            return

        # 2026-05-20 hotfix: turn-start budget enforce. 用户设有限值且 session
        # 已用尽 → 拒受新 user 输入 (不调 query_loop, 不进 LLM 流). 默 unlimited
        # (limit==0) → session_exhausted 永 False, 不会 hit 这条. mid-turn 不
        # enforce (query_loop 已删 mid-loop check), 保证当前 turn 不被打断.
        if self.budget.session_exhausted():
            from explain_engine.chat.loop import BudgetExhaustedEvent
            yield BudgetExhaustedEvent(scope="per_session")
            return

        # Append user message
        self.transcript.append({
            "role": "user",
            "content": text,
            "turn": self.chat_state.turn_count,
        })
        self.storage.append_transcript(self.sid, self.transcript[-1])
        # 记下本次"用户提问"时间 (内存; turn 收尾 persist 随 metadata 落盘)。
        # 会话列表据此降序 → 最近提问的会话置顶。只在此一处内存赋值, 不新增并发
        # metadata 写入者, 以免与 autotitle 的标题写入互相覆盖 (复用单写者 persist)。
        import time as _time
        self._session.meta.last_user_message_at = _time.time()
        # Increment turn
        self.chat_state.turn_count += 1
        # Reset per-turn budget (Wave D.1: 走 BudgetCounter)
        self.budget.reset_turn()

        if llm is None:
            # C.1 backward-compat: 无 LLM → 占位 event (CLI 端测 / 没 API key 时)
            yield ChatEvent(
                type="placeholder",
                content="no llm client provided (C.1 backward compat)",
            )
        else:
            # Phase 19 Task 9: 调 LLM 前 yield status_start (spinner mount signal).
            # 保守路线: 只 yield status_start/end, 不实装 thinking_text — 留 Phase 20
            # 待 ToolsResponse 加 reasoning field 后跟进 (ChatEvent docstring 已说明).
            yield ChatEvent(type="status_start", content=STATUS_THINKING)
            try:
                # C.2: dispatch 到 query_loop (LLM ↔ tools while-loop).
                # local import 避 circular: loop.py imports ChatEvent from this module.
                from explain_engine.chat.loop import query_loop
                # Phase 16.2 Wave 6: 累 assistant_text 各 chunk, 末尾拼写 llm_turn 入
                # repl_history.jsonl. LLM 异常 / SIGINT 不到 append 点 (设计契约).
                _assistant_chunks: list[str] = []
                _spinner_closed = False
                async for ev in query_loop(self, llm):
                    ev_type = getattr(ev, "type", None)
                    # Phase 20.3: 正文 (整段 fallback) 或 delta 都累进 repl_history.
                    if ev_type in ("assistant_text", "assistant_text_delta"):
                        _assistant_chunks.append(getattr(ev, "content", "") or "")
                    # Phase 20.3: 首个流式 delta 到达 → 提前关 spinner, 让文字
                    # 紧接 spinner 流出, 而非 spinner 悬到整 turn 跑完 (finally
                    # 仍会再 yield 一次 status_end, TUI _unmount_status 幂等).
                    if not _spinner_closed and ev_type in (
                        "assistant_text_delta", "thinking_delta", "assistant_text",
                    ):
                        yield ChatEvent(type="status_end", content=None)
                        _spinner_closed = True
                    yield ev

                # Phase 16.2 Wave 6: append llm_turn entry to repl_history.jsonl.
                # 仅 LLM 全 yield 完才写; ephemeral / sid=None 跳过; storage 失败 silent.
                if not getattr(self, "is_ephemeral", False) and self.sid is not None:
                    from datetime import datetime
                    try:
                        self.storage.append_repl_history(self.sid, {
                            "ts": datetime.now(UTC).astimezone().isoformat(),
                            "type": "llm_turn",
                            "user_input": text,
                            "assistant_text": "".join(_assistant_chunks),
                        })
                    except Exception as exc:
                        import logging
                        logging.getLogger(__name__).warning(
                            f"append llm_turn history failed for {self.sid}: "
                            f"{type(exc).__name__}: {exc}"
                        )

                # Wave D.2 fix · post-turn hooks. 两点修复:
                # C1: lifecycle BEFORE reflect — 对齐 runtime.py:86-89 契约
                #     (lifecycle 先推进 decay state, reflect 才能看到 fresh decayed
                #     nodes 做正确 pick_decay_target / 决策).
                # I1: 包 try/except — hook 抛异常不阻断 self.persist(),
                #     防半状态持久化 (turn_count 已 bump 但 graph 没存).
                try:
                    # 1. lifecycle — 推进 active → stale → decayed
                    #    tick 用 chat_state.turn_count (Wave D.2 chat 把 turn 视为 lifecycle tick)
                    lifecycle_post_turn(
                        self.state, current_tick=self.chat_state.turn_count
                    )
                    # 2. reflect — 刷新 acceptance + 返人话 hint, 写 next_turn_hint
                    #    (下一 turn assemble_system_prompt 会读, 用完清 None)
                    self.next_turn_hint = reflect_post_turn(self.state)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"post-turn hook failed for {self.sid}: "
                        f"{type(exc).__name__}: {exc}"
                    )

                # 3. session_memory — async fire-and-forget (每 5 turn 跑一次)
                #    不 await, errors logged in writer 自己, 不阻塞 user.
                #    RUF006: hold strong ref in self._background_tasks 防 GC.
                #    errors 已 self-contained in session_memory_writer 内部 try/except,
                #    所以不在外层 try/except 内 (also asyncio.create_task 不会抛同步异常).
                if self.chat_state.turn_count % SESSION_MEMORY_TRIGGER_INTERVAL == 0:
                    task = asyncio.create_task(session_memory_writer(self, llm))
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
            finally:
                # Phase 19 Task 9: try/finally 保 spinner 一定关 — LLMError 经 finally
                # 后冒到 REPL outer loop (跟现错误传播契约一致, 不改吞异常行为).
                # async gen finally yield 行为: caller 仍能 async-for 拿到 status_end
                # event, 再 re-raise 异常.
                yield ChatEvent(type="status_end", content=None)
        # Persist after turn (chat_state + graph 全 flush). I1: 即使 hook 抛
        # 异常也保证执行, 避免 turn_count bump 但 graph 不存的半态.
        self.persist()

        # ── Auto-deepen: 首次对话后自动跑 bootstrap + 分析管线 ──
        if (
            llm is not None
            and not getattr(self, "is_ephemeral", False)
            and getattr(getattr(self._session, "meta", None), "stage", None) == "bootstrap_pending"
        ):
            async for ev in self._auto_deepen(llm):
                yield ev

    async def _stream_deepen(self, text: str) -> AsyncIterator["ChatEvent"]:
        """流式 /deepen: 复用 _handle_deepen 的前置检查, 管线部分走 async generator."""
        from explain_engine.chat.slash_commands import _auto_pipeline_stream

        llm = self.llm
        if llm is None:
            yield ChatEvent(
                type="slash_error",
                content="LLM 未配置, 无法 /deepen. 设 LLM_* env 后重启.",
            )
            return

        async for ev in _auto_pipeline_stream(self):
            yield ev

    async def _auto_deepen(
        self, llm: "LLMClient",
    ) -> AsyncIterator["ChatEvent"]:
        """首轮对话后自动跑 bootstrap + compress → run → ground.

        条件: session stage == bootstrap_pending. 跑完后 stage → converged.
        失败不阻断对话 — 用户可随后手动 /deepen 重试.
        """
        import logging as _logging

        from explain_engine.chat.chat_copy import STATUS_AUTO_COMPRESS
        from explain_engine.config import make_light_llm_client

        _log = _logging.getLogger(__name__)
        question = self._session.meta.question

        yield ChatEvent(type="status_start", content="自动深度分析 — bootstrap 中...")
        try:
            # 1. Bootstrap: 用 variable extraction 生成 L0 现象
            from explain_engine.engines.bootstrap import bootstrap_phenomena

            try:
                light_llm = make_light_llm_client()
            except Exception:
                light_llm = None

            phenomena = await bootstrap_phenomena(
                question, llm, light_llm=light_llm,
            )

            # 重编号避免与 query_loop 中 add_observation 产生的 p_NNN 冲突
            existing_p = [
                int(nid.split("_")[1])
                for nid in self.state.graph.nodes
                if nid.startswith("p_") and nid[2:].isdigit()
            ]
            start = (max(existing_p) + 1) if existing_p else 1
            added = 0
            for i, node in enumerate(phenomena):
                node.id = f"p_{start + i:03d}"
                try:
                    self.state.graph.add_node(node)
                    added += 1
                except ValueError:
                    pass  # 极端情况下 ID 仍冲突, 跳过
            self.persist()
            _log.info("auto-deepen bootstrap: added %d L0 nodes for %r", added, question)

            yield ChatEvent(type="status_end", content=None)

            # 2. Auto-pipeline: compress → run → ground (流式)
            from explain_engine.chat.slash_commands import _auto_pipeline_stream

            async for ev in _auto_pipeline_stream(self):
                yield ev

        except Exception as exc:
            _log.warning("auto-deepen failed: %s: %s", type(exc).__name__, exc)
            yield ChatEvent(type="status_end", content=None)
            yield ChatEvent(
                type="slash_error",
                content=f"自动深度分析失败 ({type(exc).__name__}), 可手动 /deepen 重试.",
            )

    def persist(self) -> None:
        """Flush chat_state.json + graph (transcript already appended via storage_v2).

        TODO(Phase 9 plan §C.1 line 1320): debounced persist — currently writes
        whole graph per turn (50-turn session = 50 full-graph writes). For MVP OK
        since graphs small. 考虑 5-sec debounce 或 persist-on-TurnComplete-only
        after C.2 + D.1 land.
        """
        # Phase 9 E.2 B fix: serialize state.last_input_alignment_report →
        # chat_state.last_input_alignment dict (使下次 __init__ 可 hydrate 回来).
        if self.state.last_input_alignment_report is not None:
            try:
                self.chat_state.last_input_alignment = (
                    self.state.last_input_alignment_report.model_dump()
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    f"failed to serialize last_input_alignment for {self.sid}: "
                    f"{type(exc).__name__}: {exc}"
                )

        chat_state_dict = {
            "budget_per_turn_remaining": self.chat_state.budget_per_turn_remaining,
            "budget_per_session_remaining": self.chat_state.budget_per_session_remaining,
            "budget_per_turn_limit": self.chat_state.budget_per_turn_limit,
            "budget_per_session_limit": self.chat_state.budget_per_session_limit,
            "last_compact_at_turn": self.chat_state.last_compact_at_turn,
            "turn_count": self.chat_state.turn_count,
            "last_input_alignment": self.chat_state.last_input_alignment,
        }
        self.storage.save_chat_state(self.sid, chat_state_dict)
        # 标题 (meta.question) 由 create/autotitle 拥有, 对话流不拥有它。
        # autotitle 可能在本 turn 进行中并发改了磁盘标题; 这里 persist 前先把
        # 磁盘最新 question 读回, 避免用 turn 开始时载入的旧值 ("新会话") 覆盖,
        # 否则刷新页面后标题会丢 (回退成"新会话")。
        disk_q = self._session_store.load_question(self.sid)
        if disk_q:
            self._session.meta.question = disk_q
        # Save graph (may have mutated from tool calls in C.2)
        self._session.state = self.state
        self._session_store.save(self._session)

    def close(self) -> None:
        """Sync close. ⚠️ Does NOT await background tasks (use aclose() in async ctx).

        For shell exit / test cleanup, this is fine. For chat F.2 CLI, prefer aclose().
        """
        self.persist()

    async def aclose(self) -> None:
        """Phase 9 Wave D.2: async close — awaits in-flight background tasks before flushing.

        Use this in CLI / shell shutdown path. Sync close() retained for backward compat
        but does NOT await background tasks (silent cancellation on event loop close).
        """
        if self._background_tasks:
            # Wait for in-flight session_memory_writer / future bg tasks
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self.persist()
