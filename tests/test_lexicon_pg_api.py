"""Phase 17.1 Wave 4: lexicon_pg.py 公共 API tests (替老 lexicon.py JSON 行为).

每 task 一个 TestXxx class, fixture `reset_pg` (TRUNCATE per test) + EXPLAIN_DB_URL
透明指向 explain_test 库. 没设 EXPLAIN_TEST_DB_URL 时自动 skip.
"""
from __future__ import annotations

import os

import pytest

from explain_engine.schema.nodes import VariableNode

# 没设 EXPLAIN_TEST_DB_URL 时 skip (同 test_lexicon_pg_pool.py)
_skip_no_test_db = pytest.mark.skipif(
    os.environ.get("EXPLAIN_TEST_DB_URL") is None,
    reason="EXPLAIN_TEST_DB_URL not set (见 deploy/postgres/README.md '建 test db' 一节)",
)


def _make_node(
    nid: str = "c_001",
    name: str = "长期不确定性",
    abstraction_level: int = 1,
    activation: float = 0.8,
    lifecycle_state: str = "active",
    epistemic: str = "insight",
    stability: float = 0.0,
) -> VariableNode:
    """Helper: 建 VariableNode (复用 tests/test_engines_lexicon.py _make_node 风格)."""
    return VariableNode(
        id=nid,
        name=name,
        description=f"{name} 的描述",
        abstraction_level=abstraction_level,  # type: ignore[arg-type]
        confidence=0.7,
        epistemic=epistemic,  # type: ignore[arg-type]
        activation=activation,
        stability=stability,
        lifecycle_state=lifecycle_state,  # type: ignore[arg-type]
    )


# ── Task 4.1: _should_promote ───────────────────────────────────────────


class TestShouldPromote:
    """Phase 17.1 Task 4.1: _should_promote (跟老 lexicon._should_promote 同语义).

    Promote 条件:
      - abstraction_level >= 1 (L0 observation 不进 lexicon)
      - lifecycle_state == 'active' (stale/decayed 不 promote)
      - activation >= 0.5 (conservative threshold)
    """

    def test_should_promote_l1_high_activation_active(self):
        from explain_engine.persistence.lexicon_pg import _should_promote

        node = _make_node(abstraction_level=1, activation=0.8, lifecycle_state="active")
        assert _should_promote(node) is True

    def test_should_promote_l0_returns_false(self):
        from explain_engine.persistence.lexicon_pg import _should_promote

        node = _make_node(
            abstraction_level=0, activation=0.9, lifecycle_state="active",
            epistemic="observation",
        )
        assert _should_promote(node) is False

    def test_should_promote_stale_returns_false(self):
        from explain_engine.persistence.lexicon_pg import _should_promote

        node = _make_node(
            abstraction_level=2, activation=0.9, lifecycle_state="stale",
        )
        assert _should_promote(node) is False

    def test_should_promote_low_activation_returns_false(self):
        from explain_engine.persistence.lexicon_pg import _should_promote

        node = _make_node(
            abstraction_level=1, activation=0.3, lifecycle_state="active",
        )
        assert _should_promote(node) is False
