"""Phase 9 Wave D.1: BudgetCounter tests.

BudgetCounter 是 ChatStateDict 的 4 个 budget 字段的 thin wrapper.
ChatStateDict 仍是 source of truth, 改 BudgetCounter property 等于改底层 dict.
"""

from __future__ import annotations

from explain_engine.chat.budget import BudgetCounter
from explain_engine.chat.session import ChatStateDict


class TestBudgetCounterInit:
    def test_initial_values_from_chat_state(self) -> None:
        """BudgetCounter property 读出 ChatStateDict 的当前值."""
        cs = ChatStateDict(
            budget_per_turn_remaining=7,
            budget_per_turn_limit=10,
            budget_per_session_remaining=30,
            budget_per_session_limit=50,
        )
        b = BudgetCounter(cs)
        assert b.per_turn_remaining == 7
        assert b.per_turn_limit == 10
        assert b.per_session_remaining == 30
        assert b.per_session_limit == 50


class TestBudgetCounterConsume:
    def test_consume_decrements_both(self) -> None:
        """consume(n) 同时扣 per-turn + per-session."""
        cs = ChatStateDict(
            budget_per_turn_remaining=10,
            budget_per_session_remaining=50,
        )
        b = BudgetCounter(cs)
        b.consume(1)
        assert cs.budget_per_turn_remaining == 9
        assert cs.budget_per_session_remaining == 49
        b.consume(3)
        assert cs.budget_per_turn_remaining == 6
        assert cs.budget_per_session_remaining == 46

    def test_consume_default_is_one(self) -> None:
        cs = ChatStateDict()
        b = BudgetCounter(cs)
        before_turn = cs.budget_per_turn_remaining
        before_sess = cs.budget_per_session_remaining
        b.consume()
        assert cs.budget_per_turn_remaining == before_turn - 1
        assert cs.budget_per_session_remaining == before_sess - 1


class TestBudgetCounterExhausted:
    def test_turn_exhausted_at_zero(self) -> None:
        cs = ChatStateDict(budget_per_turn_remaining=1)
        b = BudgetCounter(cs)
        assert not b.turn_exhausted()
        b.consume()
        assert b.turn_exhausted()

    def test_session_exhausted_at_zero(self) -> None:
        cs = ChatStateDict(budget_per_session_remaining=1)
        b = BudgetCounter(cs)
        assert not b.session_exhausted()
        b.consume()
        assert b.session_exhausted()


class TestBudgetCounterReset:
    def test_reset_turn_restores_limit(self) -> None:
        """reset_turn() 把 per-turn 拉回 limit; per-session 不动."""
        cs = ChatStateDict(
            budget_per_turn_remaining=2,
            budget_per_turn_limit=10,
            budget_per_session_remaining=30,
            budget_per_session_limit=50,
        )
        b = BudgetCounter(cs)
        b.reset_turn()
        assert cs.budget_per_turn_remaining == 10
        assert cs.budget_per_session_remaining == 30  # untouched


class TestBudgetCounterAsDict:
    def test_as_dict_snapshot(self) -> None:
        """as_dict() 返 system_prompt 用的 4-field 快照."""
        cs = ChatStateDict(
            budget_per_turn_remaining=8,
            budget_per_turn_limit=10,
            budget_per_session_remaining=40,
            budget_per_session_limit=50,
        )
        b = BudgetCounter(cs)
        d = b.as_dict()
        assert d == {
            "per_turn_remaining": 8,
            "per_turn_limit": 10,
            "per_session_remaining": 40,
            "per_session_limit": 50,
        }
