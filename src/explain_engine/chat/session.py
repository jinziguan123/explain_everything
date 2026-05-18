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
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from explain_engine.chat.budget import BudgetCounter
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
        单 turn 内最多 N 次 LLM 调用. 每 turn 开始重置为 limit.
    - budget_per_session_remaining / budget_per_session_limit:
        整 session 跨 turn 累计预算. 不重置.
    - last_compact_at_turn: 上次 memory.md compact 在哪个 turn (Phase 9 Wave E).
    - turn_count: 累计 user input 次数.
    - last_input_alignment: Phase 8 input_validation 报告 (Phase 8 B fix 折叠位置).
        等价于 Phase 8 CognitiveState.last_input_alignment_report,
        Wave E.2 完成迁移后从 state 移除, 仅保留在此. 字段名缩写不带 _report
        以减少 chat_state.json 噪声.
    """

    budget_per_turn_remaining: int = 10
    budget_per_session_remaining: int = 50
    budget_per_turn_limit: int = 10
    budget_per_session_limit: int = 50
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
      slash_new / slash_resume: str (user-visible info)
    - slash_quit: str (farewell text)
    - slash_error / slash_unknown: str (error text)
    - tool_use: dict (Wave C.2 ToolUseEvent 子类替代)
    - tool_result: dict (Wave C.2 ToolResultEvent 子类替代)
    - slash_switch_session: dict {"sid": str} — REPL 据此 in-process 切到新 sid.
      Producer: _handle_new (Wave 3), _handle_resume (Wave 4).
      Consumer: cli._run_chat_repl_async (Wave 2).
    """

    type: str
    content: Any = None


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
        if self.is_slash_command(text):
            # Wave F.1: 本地 dispatch — 6 default commands bypass LLM.
            from explain_engine.chat.slash_commands import dispatch_slash
            events = await dispatch_slash(self, text)
            for ev in events:
                yield ev
            # NOTE: slash 不 append transcript / 不 bump turn_count —
            # 这些是管理命令, 非真正 user→assistant 对话.
            return
        # Append user message
        self.transcript.append({
            "role": "user",
            "content": text,
            "turn": self.chat_state.turn_count,
        })
        self.storage.append_transcript(self.sid, self.transcript[-1])
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
            # C.2: dispatch 到 query_loop (LLM ↔ tools while-loop).
            # local import 避 circular: loop.py imports ChatEvent from this module.
            from explain_engine.chat.loop import query_loop
            async for ev in query_loop(self, llm):
                yield ev

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
        # Persist after turn (chat_state + graph 全 flush). I1: 即使 hook 抛
        # 异常也保证执行, 避免 turn_count bump 但 graph 不存的半态.
        self.persist()

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
