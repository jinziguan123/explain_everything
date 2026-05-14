"""EvaluationEngine.score_all 末尾把 gain dict 持久化进 state.last_gains。"""

import pytest

from explain_engine.engines import evaluation
from explain_engine.llm.client import Response
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


class FakeLLM:
    """Mock client: 永远返 score=4。"""

    async def chat(self, messages, schema=None, model=None):
        return Response(
            text='{"score": 4}',
            parsed={"score": 4, "rationale": "ok"},
            model="fake",
            usage={"input_tokens": 0, "output_tokens": 0},
        )


def _make_state_with_one_candidate() -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(id="p_001", name="p1", description="d", abstraction_level=0,
                            confidence=0.8, epistemic="observation"))
    g.add_node(VariableNode(id="p_002", name="p2", description="d", abstraction_level=0,
                            confidence=0.8, epistemic="observation"))
    g.add_node(VariableNode(id="c_001", name="c1", description="d", abstraction_level=1,
                            confidence=0.7, epistemic="insight"))
    g.add_edge(RelationEdge(id="e_001", source_node="c_001", target_node="p_001",
                            relation_type="manifests_as", confidence=0.7,
                            mechanism_description="m1"))
    g.add_edge(RelationEdge(id="e_002", source_node="c_001", target_node="p_002",
                            relation_type="manifests_as", confidence=0.7,
                            mechanism_description="m2"))
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    state.insight_candidates = ["c_001"]
    return state


@pytest.mark.asyncio
async def test_score_all_writes_last_gains() -> None:
    state = _make_state_with_one_candidate()
    gains = await evaluation.score_all(state, FakeLLM())
    assert state.last_gains == gains
    assert "c_001" in state.last_gains
    # representation_reduction=1.0 (covers 2/2), explanatory_preservation=4/5=0.8
    assert abs(state.last_gains["c_001"] - 0.8) < 1e-9


@pytest.mark.asyncio
async def test_score_all_overwrites_old_last_gains() -> None:
    """重跑 score_all 应该覆盖旧 last_gains（不留陈数据）。"""
    state = _make_state_with_one_candidate()
    state.last_gains = {"c_999_stale": 0.5}   # 陈数据
    await evaluation.score_all(state, FakeLLM())
    assert "c_999_stale" not in state.last_gains
    assert "c_001" in state.last_gains
