"""Wave 1 Task 1.1: engines.expansion.expand_downward 单元测试.

design §4.2: expand_downward 给 L1 加 outgoing manifests_as L0 子节点.
与 expand_one_frontier (Phase 5) 对称但反方向.
"""

import pytest

from explain_engine.engines import expansion
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state_with_l1(l1_id: str = "c_001") -> CognitiveState:
    g = ExplanationGraph(root_question="why X")
    g.add_node(VariableNode(
        id=l1_id, name="L1 abstract", description="abstraction d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="why X",
        insight_candidates=[l1_id],
    )


class _FakeLLMOutput:
    def __init__(self, parsed):
        self.parsed = parsed


class _FakeLLM:
    def __init__(self, response_dict):
        self.response_dict = response_dict
        self.call_count = 0

    async def chat(self, messages, schema):
        self.call_count += 1
        return _FakeLLMOutput(parsed=self.response_dict)


class TestExpandDownward:
    @pytest.mark.asyncio
    async def test_creates_l0_children_with_manifests_as_edges(self) -> None:
        state = _make_state_with_l1("c_001")
        llm = _FakeLLM({
            "predicted_L0": [
                {"name": "phenom_a", "description": "da", "mechanism": "ma", "plausibility": 4},
                {"name": "phenom_b", "description": "db", "mechanism": "mb", "plausibility": 5},
            ],
        })
        new_l0_ids = await expansion.expand_downward(state, "c_001", llm)
        assert len(new_l0_ids) == 2
        for nid in new_l0_ids:
            assert state.graph.nodes[nid].abstraction_level == 0
            assert state.graph.nodes[nid].epistemic == "speculation"
            assert state.graph.nodes[nid].source == "llm"
        edges = [e for e in state.graph.edges.values()
                 if e.source_node == "c_001" and e.relation_type == "manifests_as"]
        confs = sorted([e.confidence for e in edges])
        assert confs == [0.8, 1.0]

    @pytest.mark.asyncio
    async def test_invalid_l1_id_raises(self) -> None:
        state = _make_state_with_l1()
        llm = _FakeLLM({"predicted_L0": []})
        with pytest.raises(ValueError, match="not found in graph"):
            await expansion.expand_downward(state, "c_999", llm)

    @pytest.mark.asyncio
    async def test_non_l1_node_raises(self) -> None:
        state = _make_state_with_l1("c_001")
        state.graph.add_node(VariableNode(
            id="p_001", name="L0 phenom", description="d",
            abstraction_level=0, confidence=0.7, epistemic="observation",
        ))
        llm = _FakeLLM({"predicted_L0": []})
        with pytest.raises(ValueError, match="must be 1"):
            await expansion.expand_downward(state, "p_001", llm)

    @pytest.mark.asyncio
    async def test_max_l0_limit_respected(self) -> None:
        state = _make_state_with_l1()
        llm = _FakeLLM({
            "predicted_L0": [
                {"name": f"phenom_{i}", "description": "d", "mechanism": "m", "plausibility": 3}
                for i in range(5)
            ],
        })
        new_l0_ids = await expansion.expand_downward(state, "c_001", llm, max_l0=3)
        assert len(new_l0_ids) == 3

    @pytest.mark.asyncio
    async def test_zero_l0_raises_validation(self) -> None:
        state = _make_state_with_l1()
        llm = _FakeLLM({"predicted_L0": []})
        from explain_engine.llm.errors import SchemaValidationError
        with pytest.raises(SchemaValidationError):
            await expansion.expand_downward(state, "c_001", llm)

    @pytest.mark.asyncio
    async def test_confidence_writeback_linear(self) -> None:
        """plausibility=1→0.2, plausibility=5→1.0 (linear /5.0)."""
        state = _make_state_with_l1()
        llm = _FakeLLM({
            "predicted_L0": [
                {"name": "low", "description": "d", "mechanism": "m", "plausibility": 1},
                {"name": "high", "description": "d", "mechanism": "m", "plausibility": 5},
            ],
        })
        new_ids = await expansion.expand_downward(state, "c_001", llm)
        edges_by_target = {
            e.target_node: e.confidence
            for e in state.graph.edges.values()
            if e.source_node == "c_001"
        }
        low_id = next(nid for nid in new_ids if state.graph.nodes[nid].name == "low")
        high_id = next(nid for nid in new_ids if state.graph.nodes[nid].name == "high")
        assert edges_by_target[low_id] == 0.2
        assert edges_by_target[high_id] == 1.0
