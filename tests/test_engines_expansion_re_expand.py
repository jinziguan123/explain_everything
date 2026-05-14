"""Wave C.2: expansion.re_expand — 绕过 frontier check 加 driver."""

import pytest

from explain_engine.engines.expansion import re_expand
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_already_expanded() -> CognitiveState:
    """c_001 已被 d_001 cover (incoming causes), Phase 5 frontier_nodes 不返."""
    g = ExplanationGraph(root_question="q")
    g.add_node(VariableNode(
        id="c_001", name="c", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="d_001", name="d", description="d",
        abstraction_level=2, confidence=0.6, epistemic="inference",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="d_001", target_node="c_001",
        relation_type="causes", confidence=0.6,
        mechanism_description="m",
    ))
    return CognitiveState(graph=g, budget_remaining=10, root_question="q")


class TestReExpand:
    @pytest.mark.asyncio
    async def test_re_expand_accepts_already_covered_L1(self, mocker) -> None:
        state = _make_already_expanded()
        from explain_engine.engines.expansion import ExpansionOutput, _DriverCandidate
        mocker.patch(
            "explain_engine.engines.expansion._call_with_retry",
            return_value=ExpansionOutput(drivers=[
                _DriverCandidate(name="d2", description="d", mechanism="m", plausibility=4),
            ]),
        )
        new_ids, _gain = await re_expand(state, "c_001", mocker.AsyncMock())
        assert len(new_ids) == 1

    @pytest.mark.asyncio
    async def test_re_expand_rejects_L0_target(self, mocker) -> None:
        state = _make_already_expanded()
        state.graph.add_node(VariableNode(
            id="p_001", name="p", description="d",
            abstraction_level=0, confidence=0.7, epistemic="observation",
        ))
        with pytest.raises(ValueError, match="level"):
            await re_expand(state, "p_001", mocker.AsyncMock())

    @pytest.mark.asyncio
    async def test_re_expand_rejects_nonexistent(self, mocker) -> None:
        state = _make_already_expanded()
        with pytest.raises(ValueError, match="not found"):
            await re_expand(state, "x_999", mocker.AsyncMock())

    @pytest.mark.asyncio
    async def test_re_expand_max_drivers_2_default(self, mocker) -> None:
        state = _make_already_expanded()
        from explain_engine.engines.expansion import ExpansionOutput, _DriverCandidate
        mocker.patch(
            "explain_engine.engines.expansion._call_with_retry",
            return_value=ExpansionOutput(drivers=[
                _DriverCandidate(name=f"d{i}", description="d", mechanism="m", plausibility=4)
                for i in range(5)
            ]),
        )
        new_ids, _ = await re_expand(state, "c_001", mocker.AsyncMock())
        assert len(new_ids) <= 2

    @pytest.mark.asyncio
    async def test_re_expand_writes_confidence_wave_a_mapping(self, mocker) -> None:
        state = _make_already_expanded()
        from explain_engine.engines.expansion import ExpansionOutput, _DriverCandidate
        mocker.patch(
            "explain_engine.engines.expansion._call_with_retry",
            return_value=ExpansionOutput(drivers=[
                _DriverCandidate(name="d2", description="d", mechanism="m", plausibility=4),
            ]),
        )
        await re_expand(state, "c_001", mocker.AsyncMock())
        new_edges = [
            e for e in state.graph.edges.values()
            if e.target_node == "c_001" and e.relation_type == "causes"
            and e.id != "e_001"
        ]
        assert len(new_edges) == 1
        assert new_edges[0].confidence == pytest.approx(0.8)   # 4/5 (Wave A.2 mapping)
