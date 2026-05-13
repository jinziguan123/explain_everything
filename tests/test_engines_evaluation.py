"""EvaluationEngine.score_all 测试。"""

from unittest.mock import AsyncMock

import pytest

from explain_engine.engines.evaluation import score_all
from explain_engine.llm.client import Response
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _build_state(
    n_concrete: int,
    candidates: list[tuple[str, list[str]]],  # (c_id, [covered_concrete_ids])
) -> CognitiveState:
    state = CognitiveState.bootstrap("q", budget=20)
    for i in range(1, n_concrete + 1):
        state.graph.add_node(
            VariableNode(
                id=f"p_{i:03d}",
                name=f"p{i}",
                description="",
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
        )
    next_eid = 1
    for cid, covered in candidates:
        state.graph.add_node(
            VariableNode(
                id=cid,
                name=cid,
                description=f"{cid}的定义",
                abstraction_level=1,
                confidence=0.7,
                epistemic="insight",
                source="llm",
            )
        )
        for pid in covered:
            state.graph.add_edge(
                RelationEdge(
                    id=f"e_{next_eid:03d}",
                    source_node=cid,
                    target_node=pid,
                    relation_type="manifests_as",
                    confidence=0.7,
                    mechanism_description=f"{cid}->{pid}",
                )
            )
            next_eid += 1
    state.insight_candidates = [cid for cid, _ in candidates]
    return state


def _score_resp(score: int) -> Response:
    return Response(
        text="",
        parsed={"score": score, "rationale": "x"},
        model="test",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


@pytest.mark.asyncio
class TestScoreAll:
    async def test_representation_reduction_pure(self) -> None:
        """覆盖 6/12，mechanism 全 5 分 → gain = 0.5 × 1.0 = 0.5"""
        state = _build_state(12, [("c_001", [f"p_{i:03d}" for i in range(1, 7)])])
        llm = AsyncMock()
        llm.chat.return_value = _score_resp(5)

        gains = await score_all(state, llm)
        assert gains["c_001"] == pytest.approx(0.5)

    async def test_full_coverage_low_mechanism(self) -> None:
        """覆盖 12/12，mechanism 全 1 分 → gain = 1.0 × 0.2 = 0.2（空洞抽象兜底）"""
        state = _build_state(12, [("c_001", [f"p_{i:03d}" for i in range(1, 13)])])
        llm = AsyncMock()
        llm.chat.return_value = _score_resp(1)
        gains = await score_all(state, llm)
        assert gains["c_001"] == pytest.approx(0.2)

    async def test_sort_descending(self) -> None:
        state = _build_state(
            12,
            [
                ("c_001", ["p_001", "p_002", "p_003"]),  # 3/12
                ("c_002", [f"p_{i:03d}" for i in range(1, 10)]),  # 9/12
                ("c_003", ["p_001", "p_002"]),  # 2/12
            ],
        )
        llm = AsyncMock()
        llm.chat.return_value = _score_resp(4)  # 全部 mechanism=4
        gains = await score_all(state, llm)
        # 0.75 × 0.8 = 0.6 > 0.25 × 0.8 = 0.2 > 0.167 × 0.8 = 0.133
        assert state.insight_candidates == ["c_002", "c_001", "c_003"]
        assert gains["c_002"] > gains["c_001"] > gains["c_003"]

    async def test_invalid_score_raises(self) -> None:
        state = _build_state(12, [("c_001", ["p_001", "p_002"])])
        llm = AsyncMock()
        bad = Response(
            text="",
            parsed={"score": 99, "rationale": "x"},
            model="test",
            usage={"input_tokens": 0, "output_tokens": 0},
        )
        llm.chat.side_effect = [bad, bad]
        with pytest.raises(SchemaValidationError):
            await score_all(state, llm)
        assert llm.chat.await_count == 2

    async def test_retry_succeeds_on_second_attempt(self) -> None:
        """第一次返坏数据（score=99），retry → 第二次返好数据（score=4）→ 成功。"""
        state = _build_state(12, [("c_001", ["p_001", "p_002"])])
        llm = AsyncMock()
        bad = Response(
            text="",
            parsed={"score": 99, "rationale": "x"},
            model="test",
            usage={"input_tokens": 0, "output_tokens": 0},
        )
        good = _score_resp(4)
        # edge1: [bad, good]（retry success），edge2: [good]
        llm.chat.side_effect = [bad, good, good]
        gains = await score_all(state, llm)
        # 两 edge 都 score=4 → preservation = 4/5 = 0.8
        # coverage 2/12 → repr = 2/12
        # gain = 2/12 × 0.8
        assert gains["c_001"] == pytest.approx(2 / 12 * 0.8, rel=1e-3)
        assert llm.chat.await_count == 3

    async def test_zero_coverage_skipped(self) -> None:
        """candidate 无 outgoing edge → gain=0（理论上 Compression 已淘汰，但 Evaluation 兜底）"""
        state = _build_state(12, [])
        state.graph.add_node(
            VariableNode(
                id="c_001",
                name="x",
                description="x",
                abstraction_level=1,
                confidence=0.7,
                epistemic="insight",
            )
        )
        state.insight_candidates = ["c_001"]
        llm = AsyncMock()
        gains = await score_all(state, llm)
        assert gains["c_001"] == 0.0
