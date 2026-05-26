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


# ── Task 4.2: _build_canonical_mechanism ────────────────────────────────


class _FakeEdge:
    def __init__(self, source_node: str, target_node: str):
        self.source_node = source_node
        self.target_node = target_node


class _FakeGraph:
    def __init__(self, nodes: dict, edges: dict):
        self.nodes = nodes
        self.edges = edges


class _FakeState:
    def __init__(self, graph: _FakeGraph):
        self.graph = graph


class _FakeMeta:
    def __init__(self, session_id: str):
        self.session_id = session_id


class _FakeSession:
    def __init__(self, graph: _FakeGraph, session_id: str = "s_fake0001"):
        self.state = _FakeState(graph)
        self.meta = _FakeMeta(session_id)


def _build_simple_session(node_name: str = "n_a") -> _FakeSession:
    """造一个 session: node_name 有 1 outgoing (n_b) + 1 incoming (n_c)."""
    n_a = _make_node(nid=node_name, name=node_name)
    n_b = _make_node(nid="n_b", name="n_b 后继")
    n_c = _make_node(nid="n_c", name="n_c 前驱")
    edges = {
        "e1": _FakeEdge(source_node=node_name, target_node="n_b"),
        "e2": _FakeEdge(source_node="n_c", target_node=node_name),
    }
    nodes = {node_name: n_a, "n_b": n_b, "n_c": n_c}
    return _FakeSession(_FakeGraph(nodes, edges))


class TestBuildCanonicalMechanism:
    """Phase 17.1 Task 4.2: _build_canonical_mechanism edge fallback + LLM 路径."""

    @pytest.mark.asyncio
    async def test_build_canonical_mechanism_no_llm(self):
        """llm=None 走 edge fallback, 返非空 str (含 outgoing / incoming neighbor name)."""
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism,
        )

        session = _build_simple_session("n_a")
        node = session.state.graph.nodes["n_a"]
        mech = await _build_canonical_mechanism(node, session, llm=None)
        assert isinstance(mech, str)
        assert len(mech) > 0
        assert "n_b 后继" in mech
        assert "n_c 前驱" in mech

    @pytest.mark.asyncio
    async def test_build_canonical_mechanism_no_neighbors_fallback(self):
        """孤立 node (无 edge) 时返默认 '<name> (无 edge 上下文)'."""
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism,
        )

        n_iso = _make_node(nid="n_iso", name="孤岛")
        session = _FakeSession(_FakeGraph({"n_iso": n_iso}, {}))
        mech = await _build_canonical_mechanism(n_iso, session, llm=None)
        assert "孤岛" in mech

    @pytest.mark.asyncio
    async def test_build_canonical_mechanism_with_llm_mock(self, mock_llm_response):
        """llm 非 None 时调 LLM, 返 response.text (cap 100 chars / 第 1 行)."""
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism,
        )

        # mock LLM: chat() 返一个 Response, text = '通常 cause X; 由 Y cause'
        class _MockLLM:
            async def chat(self, messages, schema=None, model=None):
                return mock_llm_response({}, raw_text="通常 cause X; 由 Y cause")

        session = _build_simple_session("n_a")
        node = session.state.graph.nodes["n_a"]
        mech = await _build_canonical_mechanism(node, session, llm=_MockLLM())
        assert mech == "通常 cause X; 由 Y cause"

    @pytest.mark.asyncio
    async def test_build_canonical_mechanism_llm_error_fallback(self):
        """LLM 调 raise LLMError → fall back to edge-based summary."""
        from explain_engine.llm.errors import LLMError
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism,
        )

        class _BadLLM:
            async def chat(self, messages, schema=None, model=None):
                raise LLMError("boom")

        session = _build_simple_session("n_a")
        node = session.state.graph.nodes["n_a"]
        mech = await _build_canonical_mechanism(node, session, llm=_BadLLM())
        # fallback 应含 neighbor name
        assert "n_b 后继" in mech
