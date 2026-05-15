"""Wave A.2: expansion.expand_one_frontier 写回 causes edge.confidence。

design §4.3: linear mapping `conf = plausibility / 5.0` (跟 evaluation 同).
"""

import pytest

from explain_engine.engines.expansion import expand_one_frontier
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state_with_frontier(target_id: str = "c_001") -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id=target_id, name="abstract", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="phenom", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node=target_id, target_node="p_001",
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m",
    ))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="why",
    )


def _mock_expansion_output(mocker, drivers: list[dict]):
    """Mock LLM 返 ExpansionOutput with given driver list."""
    from explain_engine.engines.expansion import ExpansionOutput, _DriverCandidate
    output = ExpansionOutput(drivers=[_DriverCandidate(**d) for d in drivers])

    async def fake_call_with_retry(*args, **kwargs):
        return output
    mocker.patch(
        "explain_engine.engines.expansion.call_with_retry",
        side_effect=fake_call_with_retry,
    )


class TestExpansionWriteback:
    @pytest.mark.asyncio
    async def test_plausibility_5_writes_confidence_1_0(self, mocker) -> None:
        state = _make_state_with_frontier()
        _mock_expansion_output(mocker, [{
            "name": "d1", "description": "d",
            "mechanism": "m", "plausibility": 5,
        }])
        new_ids, _gain = await expand_one_frontier(state, "c_001", mocker.AsyncMock())
        assert len(new_ids) == 1
        new_edges = [e for e in state.graph.edges.values() if e.relation_type == "causes"]
        assert len(new_edges) == 1
        assert new_edges[0].confidence == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_plausibility_3_writes_confidence_0_6(self, mocker) -> None:
        state = _make_state_with_frontier()
        _mock_expansion_output(mocker, [{
            "name": "d1", "description": "d", "mechanism": "m", "plausibility": 3,
        }])
        await expand_one_frontier(state, "c_001", mocker.AsyncMock())
        new_edges = [e for e in state.graph.edges.values() if e.relation_type == "causes"]
        assert new_edges[0].confidence == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_multi_driver_independent_confidence(self, mocker) -> None:
        state = _make_state_with_frontier()
        _mock_expansion_output(mocker, [
            {"name": "d1", "description": "d", "mechanism": "m", "plausibility": 5},
            {"name": "d2", "description": "d", "mechanism": "m", "plausibility": 3},
            {"name": "d3", "description": "d", "mechanism": "m", "plausibility": 1},
        ])
        await expand_one_frontier(state, "c_001", mocker.AsyncMock())
        new_edges = sorted(
            [e for e in state.graph.edges.values() if e.relation_type == "causes"],
            key=lambda e: e.id,
        )
        assert len(new_edges) == 3
        assert new_edges[0].confidence == pytest.approx(1.0)
        assert new_edges[1].confidence == pytest.approx(0.6)
        assert new_edges[2].confidence == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_reused_driver_new_edge_uses_new_plausibility(self, mocker) -> None:
        """Driver 同名复用 node, 但新 causes edge 用新 plausibility."""
        state = _make_state_with_frontier()
        # 先加一个 d_999 同名 node + edge (模拟 existing driver)
        state.graph.add_node(VariableNode(
            id="d_999", name="d1", description="d",
            abstraction_level=2, confidence=0.6, epistemic="inference",
        ))
        # 加一个 c_002 来给 d_999 当 target, 让 d_999 不是 isolated
        state.graph.add_node(VariableNode(
            id="c_002", name="other", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
        state.graph.add_edge(RelationEdge(
            id="e_999", source_node="d_999", target_node="c_002",
            relation_type="causes", confidence=0.6,
            mechanism_description="old",
        ))
        _mock_expansion_output(mocker, [{
            "name": "d1", "description": "d",
            "mechanism": "m", "plausibility": 5,
        }])
        new_ids, _ = await expand_one_frontier(state, "c_001", mocker.AsyncMock())
        # 应复用 d_999 (同名), 加新 edge to c_001 with conf=1.0
        assert new_ids == ["d_999"]
        new_edges_to_c001 = [
            e for e in state.graph.edges.values()
            if e.relation_type == "causes" and e.target_node == "c_001"
        ]
        assert len(new_edges_to_c001) == 1
        assert new_edges_to_c001[0].confidence == pytest.approx(1.0)
        # 旧 edge e_999 不变
        assert state.graph.edges["e_999"].confidence == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_zero_drivers_no_edge_written(self, mocker) -> None:
        state = _make_state_with_frontier()
        _mock_expansion_output(mocker, [])
        new_ids, gain = await expand_one_frontier(state, "c_001", mocker.AsyncMock())
        assert new_ids == []
        assert gain == 0.0
        causes_edges = [e for e in state.graph.edges.values() if e.relation_type == "causes"]
        assert causes_edges == []
