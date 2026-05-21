"""Phase 9 Wave D.1: BudgetCounter — wraps per-turn + per-session counters.

ChatStateDict (in chat/session.py) holds the raw counts as ints for persistence.
BudgetCounter wraps with convenience methods. The ChatStateDict counts are the
source of truth; BudgetCounter mutates them via property access.

设计动机:
- 直接在 loop.py 里写 `chat.chat_state.budget_per_turn_remaining -= 1` 这种
  四字段散落访问太脆 (重命名 / 改语义都得 grep 整个 codebase).
- 包成 BudgetCounter 后, consume / turn_exhausted / reset_turn 是稳定 API,
  内部 ChatStateDict schema 可独立演化 (e.g. 加 budget_per_tool_remaining).
- 仍以 ChatStateDict 为 source of truth (persistence 走 dataclass 序列化),
  BudgetCounter 自身无 state, 任意 new() 都安全.

参考 docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave D.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from explain_engine.chat.session import ChatStateDict


@dataclass
class BudgetCounter:
    """Wraps chat_state's 4 budget fields with convenience methods.

    Holds a reference to ChatStateDict; mutations go through to the underlying dict.
    """

    chat_state: ChatStateDict

    @property
    def per_turn_remaining(self) -> int:
        return self.chat_state.budget_per_turn_remaining

    @property
    def per_session_remaining(self) -> int:
        return self.chat_state.budget_per_session_remaining

    @property
    def per_turn_limit(self) -> int:
        return self.chat_state.budget_per_turn_limit

    @property
    def per_session_limit(self) -> int:
        return self.chat_state.budget_per_session_limit

    def consume(self, n: int = 1) -> None:
        """Decrement both per-turn and per-session counters by n.

        2026-05-20 hotfix: 即便 unlimited (limit==0) 也 decrement, 形成
        负值 tracking (绝对值 = 已用次数). 显示层调 abs() 渲 "已用 K".
        """
        self.chat_state.budget_per_turn_remaining -= n
        self.chat_state.budget_per_session_remaining -= n

    def turn_exhausted(self) -> bool:
        """2026-05-20 hotfix: limit==0 = unlimited 永 False."""
        if self.per_turn_limit == 0:
            return False
        return self.per_turn_remaining <= 0

    def session_exhausted(self) -> bool:
        """2026-05-20 hotfix: limit==0 = unlimited 永 False."""
        if self.per_session_limit == 0:
            return False
        return self.per_session_remaining <= 0

    def reset_turn(self) -> None:
        """Restore per-turn budget to limit. Call on each new user turn.

        2026-05-20 hotfix: limit==0 (unlimited) → remaining=0 (tracking 起点).
        每 turn 起将 tracking counter 归零, 让 per_turn_remaining 始终反映
        "本 turn 已用次数的负值" (display 层可 abs() 渲).
        """
        self.chat_state.budget_per_turn_remaining = self.chat_state.budget_per_turn_limit

    def as_dict(self) -> dict[str, int]:
        """Snapshot for system_prompt budget section."""
        return {
            "per_turn_remaining": self.per_turn_remaining,
            "per_turn_limit": self.per_turn_limit,
            "per_session_remaining": self.per_session_remaining,
            "per_session_limit": self.per_session_limit,
        }
