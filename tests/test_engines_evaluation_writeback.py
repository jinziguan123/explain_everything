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
        # Wave C 补丁: 改前 scores = iter([5, 3, 1]); side_effect=next(scores)
        # 依赖 call 顺序; 并发后顺序非确定 -> 改成按 concrete_name 查表 (顺序无关).
        score_map = {"phenom_1": 5, "phenom_2": 3, "phenom_3": 1}
        from explain_engine.engines import evaluation
        mocker.patch.object(
            evaluation, "_score_edge",
            side_effect=lambda *a, **kw: score_map[kw["concrete_name"]],
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

    @pytest.mark.asyncio
    async def test_score_all_uses_concurrency_with_semaphore_bound(
        self, mocker
    ) -> None:
        """Wave C 补丁: 并发 scoring 不超过 LLM_MAX_CONCURRENCY."""
        import asyncio

        from explain_engine.engines import evaluation

        # 5 edges, semaphore=2 -> max 2 concurrent
        state = _make_state_with_candidate([3, 3, 3, 3, 3])
        monkeypatch_value = 2
        mocker.patch.object(evaluation, "LLM_MAX_CONCURRENCY", monkeypatch_value)

        in_flight = 0
        max_in_flight = 0
        lock = asyncio.Lock()

        async def fake_score(*args, **kwargs):
            nonlocal in_flight, max_in_flight
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.01)   # simulate LLM latency
            async with lock:
                in_flight -= 1
            return 3

        mocker.patch.object(evaluation, "_score_edge", side_effect=fake_score)
        await score_all(state, llm=mocker.AsyncMock())

        # 5 edges, semaphore=2 -> max in-flight 应该 <= 2
        assert max_in_flight <= monkeypatch_value
        # 也应该 >= 2 (真并发, 不退化串行)
        assert max_in_flight >= 2

    @pytest.mark.asyncio
    async def test_serial_mode_via_concurrency_1(self, mocker) -> None:
        """Wave C 补丁: monkeypatch LLM_MAX_CONCURRENCY=1 退化串行
        (向后兼容旧 iter-based mock pattern)."""
        state = _make_state_with_candidate([5, 3, 1])
        scores = iter([5, 3, 1])
        from explain_engine.engines import evaluation
        mocker.patch.object(evaluation, "LLM_MAX_CONCURRENCY", 1)
        mocker.patch.object(
            evaluation, "_score_edge",
            side_effect=lambda *a, **kw: next(scores),
        )
        await score_all(state, llm=mocker.AsyncMock())
        # Semaphore=1 → 串行, gather 仍按 task 入队顺序逐个执行 → iter 顺序确定
        assert state.graph.edges["e_001"].confidence == pytest.approx(1.0)
        assert state.graph.edges["e_002"].confidence == pytest.approx(0.6)
        assert state.graph.edges["e_003"].confidence == pytest.approx(0.2)
