"""Wave A.1: evaluation.score_all 写回 edge.confidence 测试。

design §4.2: linear mapping `conf = score / 5.0`. 改 Phase 4 evaluation, 让 LLM 评的
score 写回对应 manifests_as edge.confidence (不再 default 0.7).
"""

import pytest

from explain_engine.engines.evaluation import score_all
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state_with_candidate(plausibility_scores: list[int]) -> CognitiveState:
    """1 c_001 candidate + N p_NNN concrete + N manifests_as edges."""
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id="c_001", name="abstract", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    for i, _ in enumerate(plausibility_scores):
        pid = f"p_{i+1:03d}"
        g.add_node(VariableNode(
            id=pid, name=f"phenom_{i+1}", description="d",
            abstraction_level=0, confidence=0.7, epistemic="observation",
        ))
        g.add_edge(RelationEdge(
            id=f"e_{i+1:03d}",
            source_node="c_001", target_node=pid,
            relation_type="manifests_as",
            confidence=0.7,   # default, 待被 score_all 覆写
            mechanism_description="m",
        ))
    state = CognitiveState(
        graph=g, budget_remaining=10, root_question="why",
        insight_candidates=["c_001"],
    )
    return state


class TestEvaluationWriteback:
    @pytest.mark.asyncio
    async def test_score_5_writes_confidence_1_0(self, mocker) -> None:
        state = _make_state_with_candidate([5])
        from explain_engine.engines import evaluation
        mocker.patch.object(evaluation, "_score_edge", return_value=5)
        await score_all(state, llm=mocker.AsyncMock())
        assert state.graph.edges["e_001"].confidence == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_score_3_writes_confidence_0_6(self, mocker) -> None:
        state = _make_state_with_candidate([3])
        from explain_engine.engines import evaluation
        mocker.patch.object(evaluation, "_score_edge", return_value=3)
        await score_all(state, llm=mocker.AsyncMock())
        assert state.graph.edges["e_001"].confidence == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_score_1_writes_confidence_0_2(self, mocker) -> None:
        state = _make_state_with_candidate([1])
        from explain_engine.engines import evaluation
        mocker.patch.object(evaluation, "_score_edge", return_value=1)
        await score_all(state, llm=mocker.AsyncMock())
        assert state.graph.edges["e_001"].confidence == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_multi_edge_independent_writeback(self, mocker) -> None:
        state = _make_state_with_candidate([5, 3, 1])
        scores = iter([5, 3, 1])
        from explain_engine.engines import evaluation
        mocker.patch.object(
            evaluation, "_score_edge",
            side_effect=lambda *a, **kw: next(scores),
        )
        await score_all(state, llm=mocker.AsyncMock())
        assert state.graph.edges["e_001"].confidence == pytest.approx(1.0)
        assert state.graph.edges["e_002"].confidence == pytest.approx(0.6)
        assert state.graph.edges["e_003"].confidence == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_score_edge_failure_keeps_original_confidence(self, mocker) -> None:
        state = _make_state_with_candidate([5])
        from explain_engine.engines import evaluation
        from explain_engine.llm.errors import SchemaValidationError
        mocker.patch.object(
            evaluation, "_score_edge",
            side_effect=SchemaValidationError("LLM 失败"),
        )
        with pytest.raises(SchemaValidationError):
            await score_all(state, llm=mocker.AsyncMock())
        # 异常时不写回, edge.confidence 保持原 default 0.7
        assert state.graph.edges["e_001"].confidence == pytest.approx(0.7)
