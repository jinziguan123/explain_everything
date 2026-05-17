"""Phase 9 Wave C.1: ChatSession outer orchestrator + persistence integration.

ChatSession 是 Phase 9 conversational engine 的最外层"循环宿主":
- __init__ 加载 5 sidecar files (graph + metadata + transcript + memory + chat_state)
- handle_user_input(text) 路由 slash command / 非 slash 对话
- persist() / close() flush chat_state 到 storage_v2

C.1 不实现 LLM ↔ tools while-loop (= query_loop),只搭骨架 + persistence;
Task C.2 会把 query_loop 接进 handle_user_input 的非 slash 分支。

参考:
- docs/plans/2026-05-14-cognitive-engine-phase-9-design.md
- docs/plans/2026-05-14-cognitive-engine-phase-9-plan.md Wave C.1
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from explain_engine.persistence.session import SessionStore
from explain_engine.persistence.storage_v2 import StorageV2
from explain_engine.schema.state import CognitiveState


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

    def __init__(self, sid: str, storage: StorageV2 | None = None):
        self.sid = sid
        self.storage = storage or StorageV2()
        self._session_store = SessionStore()  # for graph + metadata
        # Load Session (metadata + state.graph) — raises FileNotFoundError if missing
        self._session = self._session_store.load(sid)
        self.state: CognitiveState = self._session.state
        # Load sidecars
        self.transcript: list[dict] = self.storage.load_transcript(sid)
        self.memory_md: str = self.storage.load_memory(sid)
        chat_state_dict = self.storage.load_chat_state(sid)
        self.chat_state: ChatStateDict = (
            ChatStateDict(**chat_state_dict) if chat_state_dict
            else ChatStateDict()
        )

    @property
    def turn_count(self) -> int:
        return self.chat_state.turn_count

    def is_slash_command(self, text: str) -> bool:
        return text.strip().startswith("/")

    async def handle_user_input(self, text: str) -> AsyncIterator[ChatEvent]:
        """Process one user input. Yields events (text / tool_use / tool_result / TurnComplete).

        C.1 stub:
        - slash command → yield 占位 event ("slash_unimplemented"), Task F.1 实现 dispatch
        - 非 slash → append transcript + bump turn_count + reset per-turn budget
                    + yield 占位 event ("placeholder"), Task C.2 接入 query_loop
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
        # Reset per-turn budget
        self.chat_state.budget_per_turn_remaining = self.chat_state.budget_per_turn_limit
        # Task C.2 will call query_loop here; C.1 stub yields placeholder
        yield ChatEvent(
            type="placeholder",
            content="query_loop not implemented yet (Task C.2)",
        )
        # Persist after turn
        self.persist()

    def persist(self) -> None:
        """Flush chat_state.json + graph (transcript already appended)."""
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
