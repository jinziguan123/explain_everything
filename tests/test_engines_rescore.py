"""Wave D.1 follow-up: real engine integration tests for rescore_session.

Existing test_cli_rescore.py mocks rescore_session itself. These tests verify
the actual engine logic (relation_type filter, semantic mapping, concurrency).
"""

from __future__ import annotations

import asyncio

import pytest

from explain_engine.engines import rescore
from explain_engine.engines.rescore import rescore_session
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state_with_both_edge_types() -> CognitiveState:
    """1 manifests_as edge (c_001 → p_001) + 1 causes edge (d_001 → c_001)."""
    g = ExplanationGraph(root_question="q")
    g.add_node(VariableNode(
        id="p_001", name="phenom_1", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_node(VariableNode(
        id="c_001", name="abstract_1", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="d_001", name="driver_1", description="d",
        abstraction_level=2, confidence=0.6, epistemic="inference",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m_manifests",
    ))
    g.add_edge(RelationEdge(
        id="e_002", source_node="d_001", target_node="c_001",
        relation_type="causes", confidence=0.6,
        mechanism_description="m_causes",
    ))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="q",
        insight_candidates=["c_001"],
    )


class TestRescoreSession:
    @pytest.mark.asyncio
    async def test_overwrites_both_edge_types(self, mocker) -> None:
        """实测 engine: manifests_as + causes 两类 edge 都被处理."""
        state = _make_state_with_both_edge_types()
        # mock _score_edge return value=4 → conf=0.8
        mocker.patch.object(rescore, "_score_edge", return_value=4)
        result = await rescore_session(state, llm=mocker.AsyncMock())
        # Both edges rescored to 0.8
        assert state.graph.edges["e_001"].confidence == pytest.approx(0.8)
        assert state.graph.edges["e_002"].confidence == pytest.approx(0.8)
        assert result == {"e_001": 0.8, "e_002": 0.8}

    @pytest.mark.asyncio
    async def test_causes_uses_source_as_abstract(self, mocker) -> None:
        """semantic mapping: causes edge 把 source (driver) 当 abstract 传给 _score_edge.
        per design §7.2.1 简化复用 scoring.yaml.
        """
        state = _make_state_with_both_edge_types()
        score_calls = []
        async def fake_score_edge(*args, **kwargs):
            score_calls.append(kwargs)
            return 3
        mocker.patch.object(rescore, "_score_edge", side_effect=fake_score_edge)
        await rescore_session(state, llm=mocker.AsyncMock())
        # Find the causes-edge call (e_002 = d_001 → c_001)
        causes_call = next(
            c for c in score_calls
            if c["abstract_name"] == "driver_1"
        )
        # driver_1 (source/L2) 当 abstract, abstract_1 (target/L1) 当 concrete
        assert causes_call["abstract_name"] == "driver_1"
        assert causes_call["concrete_name"] == "abstract_1"
        assert causes_call["mechanism"] == "m_causes"

    @pytest.mark.asyncio
    async def test_empty_graph_returns_empty_dict(self, mocker) -> None:
        """空 graph (无 edges) → 立即返 {}, 不调 _score_edge."""
        g = ExplanationGraph(root_question="q")
        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
        spy = mocker.patch.object(rescore, "_score_edge")
        result = await rescore_session(state, llm=mocker.AsyncMock())
        assert result == {}
        assert spy.call_count == 0

    @pytest.mark.asyncio
    async def test_respects_concurrency_semaphore(self, mocker) -> None:
        """LLM_MAX_CONCURRENCY 真 bound 并发数."""
        # 5 manifests_as edges
        g = ExplanationGraph(root_question="q")
        g.add_node(VariableNode(
            id="c_001", name="c", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
        for i in range(5):
            pid = f"p_{i+1:03d}"
            g.add_node(VariableNode(
                id=pid, name=f"p_{i}", description="d",
                abstraction_level=0, confidence=0.7, epistemic="observation",
            ))
            g.add_edge(RelationEdge(
                id=f"e_{i+1:03d}", source_node="c_001", target_node=pid,
                relation_type="manifests_as", confidence=0.7,
                mechanism_description="m",
            ))
        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")

        in_flight = 0
        max_in_flight = 0
        lock = asyncio.Lock()

        async def fake_score_edge(*args, **kwargs):
            nonlocal in_flight, max_in_flight
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.01)
            async with lock:
                in_flight -= 1
            return 5

        mocker.patch.object(rescore, "LLM_MAX_CONCURRENCY", 2)
        mocker.patch.object(rescore, "_score_edge", side_effect=fake_score_edge)
        await rescore_session(state, llm=mocker.AsyncMock())
        assert max_in_flight <= 2
