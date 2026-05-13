"""CompressionEngine.propose_candidates 测试。"""

from unittest.mock import AsyncMock

import pytest

from explain_engine.engines.compression import propose_candidates
from explain_engine.llm.client import Response
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _setup_state(n_concrete: int = 12) -> CognitiveState:
    state = CognitiveState.bootstrap("为什么年轻人不消费", budget=20)
    for i in range(1, n_concrete + 1):
        state.graph.add_node(
            VariableNode(
                id=f"p_{i:03d}",
                name=f"现象{i}",
                description=f"现象{i}描述",
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
        )
    return state


def _candidate(name: str, coverage_ids: list[str]) -> dict:
    return {
        "name": name,
        "description": f"{name}的定义",
        "coverage": [
            {"concrete_id": cid, "mechanism": f"{name} → {cid}"}
            for cid in coverage_ids
        ],
    }


def _mock_llm_response(candidates: list[dict]) -> Response:
    return Response(
        text="",
        parsed={"candidates": candidates},
        model="test",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


@pytest.mark.asyncio
class TestPropose:
    async def test_basic_5_candidates(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("长期不确定性", [f"p_{i:03d}" for i in range(1, 10)]),
            _candidate("社会竞争结构", [f"p_{i:03d}" for i in range(2, 9)]),
            _candidate("生活成本上涨", [f"p_{i:03d}" for i in range(1, 6)]),
            _candidate("传统价值观瓦解", [f"p_{i:03d}" for i in range(5, 9)]),
            _candidate("技术替代消费", [f"p_{i:03d}" for i in range(4, 7)]),
        ])

        await propose_candidates(state, llm)

        # 5 abstract nodes 灌入 graph
        abstracts = [n for n in state.graph.nodes.values() if n.abstraction_level == 1]
        assert len(abstracts) == 5
        assert all(n.id.startswith("c_") for n in abstracts)
        assert all(n.epistemic == "insight" for n in abstracts)
        assert all(n.source == "llm" for n in abstracts)
        # insight_candidates 列出 5 个 c_id
        assert sorted(state.insight_candidates) == [f"c_{i:03d}" for i in range(1, 6)]

    async def test_truncate_over_5(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate(f"abs_{i}", ["p_001", "p_002"]) for i in range(8)
        ])
        await propose_candidates(state, llm)
        abstracts = [n for n in state.graph.nodes.values() if n.abstraction_level == 1]
        assert len(abstracts) == 5

    async def test_accept_3_warn_on_low(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate(f"abs_{i}", ["p_001", "p_002"]) for i in range(3)
        ])
        await propose_candidates(state, llm)
        abstracts = [n for n in state.graph.nodes.values() if n.abstraction_level == 1]
        assert len(abstracts) == 3

    async def test_drop_coverage_below_2(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("good_1", ["p_001", "p_002"]),
            _candidate("solo", ["p_003"]),  # 只 1 个 coverage，应淘汰
            _candidate("good_2", ["p_004", "p_005"]),
        ])
        await propose_candidates(state, llm)
        abstracts = [n for n in state.graph.nodes.values() if n.abstraction_level == 1]
        assert len(abstracts) == 2
        assert {n.name for n in abstracts} == {"good_1", "good_2"}

    async def test_invalid_concrete_id_raises(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        # retry 2 次都返同样的坏数据
        llm.chat.return_value = _mock_llm_response([
            _candidate("bad", ["p_999", "p_888"]),  # 不存在的 id
        ])
        with pytest.raises(SchemaValidationError, match="concrete_id"):
            await propose_candidates(state, llm)

    async def test_coverage_overlap_allowed(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("a", ["p_001", "p_002"]),
            _candidate("b", ["p_001", "p_003"]),  # p_001 被两个 abstract 覆盖
        ])
        await propose_candidates(state, llm)
        # p_001 有 2 条 incoming manifests_as
        incoming_count = sum(
            1 for e in state.graph.edges.values()
            if e.target_node == "p_001" and e.relation_type == "manifests_as"
        )
        assert incoming_count == 2

    async def test_edges_created_with_mechanism(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("X", ["p_001", "p_002"]),
        ])
        await propose_candidates(state, llm)
        x_edges = [e for e in state.graph.edges.values() if e.source_node.startswith("c_")]
        assert len(x_edges) == 2
        assert all(e.relation_type == "manifests_as" for e in x_edges)
        assert all(e.mechanism_description for e in x_edges)
