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

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from explain_engine.chat.budget import BudgetCounter
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
    """Base event yielded from query_loop.

    Task C.2 将派生子类 (AssistantText / ToolUse / ToolResult / TurnComplete).
    C.1 仅用 type 字段做 stub event 区分.
    """

    type: str
    content: Any = None


class ChatSession:
    """Phase 9 outer orchestrator. Wraps query_loop (Task C.2)."""

    def __init__(self, sid: str):
        """加载 session 的 5 sidecar files.

        Phase 9 Wave C.1 fix · I1: 去掉了 storage 参数 — 内部 SessionStore
        本来就 env-driven (EXPLAIN_HOME / EXPLAIN_PROJECT_ID), 传 custom
        storage 会和内部 SessionStore() 的 StorageV2 silent split.

        Raises:
            FileNotFoundError: session 不存在 (metadata/graph 缺失)
            ChatSessionLoadError: 5 sidecar 中任一损坏 (I2)
        """
        self.sid = sid
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

        - slash command → yield 占位 event ("slash_unimplemented"), Task F.1 实现 dispatch
        - 非 slash + llm=None → C.1 backward compat: yield placeholder event
        - 非 slash + llm 不为 None → C.2: append transcript + bump turn + reset budget
            + dispatch 到 query_loop (LLM ↔ tools while-loop), 最后 persist.
        """
        if self.is_slash_command(text):
            # Slash dispatch (Task F.1 will implement); C.1 stub just yields
            yield ChatEvent(type="slash_unimplemented", content=text)
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
        # Persist after turn (chat_state + graph 全 flush)
        self.persist()

    def persist(self) -> None:
        """Flush chat_state.json + graph (transcript already appended via storage_v2).

        TODO(Phase 9 plan §C.1 line 1320): debounced persist — currently writes
        whole graph per turn (50-turn session = 50 full-graph writes). For MVP OK
        since graphs small. 考虑 5-sec debounce 或 persist-on-TurnComplete-only
        after C.2 + D.1 land.
        """
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
        """Explicit cleanup. Flushes everything one more time."""
        self.persist()
