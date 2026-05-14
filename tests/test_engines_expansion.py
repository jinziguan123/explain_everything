"""ExpansionEngine.expand_one_frontier — Phase 5 上溯 driver。

Plan §step 3-4 修正: 返 (new_ids, gain) 二元组,
gain = mean(plausibility) / 5.0 (plausibility in-memory only, 不落盘)。
"""

from typing import Any

import pytest

from explain_engine.engines import expansion
from explain_engine.llm.client import Response
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


class FakeLLM:
    """Mock LLM 返预设 driver list。"""

    def __init__(self, drivers_data: list[dict[str, Any]], parsed: dict | None = None) -> None:
        self._drivers_data = drivers_data
        self._explicit_parsed = parsed
        self.call_count = 0
        self.last_messages: list = []

    async def chat(self, messages, schema=None, model=None):
        self.call_count += 1
        self.last_messages = messages
        parsed = self._explicit_parsed if self._explicit_parsed is not None else {
            "drivers": self._drivers_data
        }
        return Response(
            text="ignored",
            parsed=parsed,
            model="fake",
            usage={"input_tokens": 0, "output_tokens": 0},
        )


def _state_with_frontier() -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(id="p_001", name="p1", description="d", abstraction_level=0,
                            confidence=0.8, epistemic="observation"))
    g.add_node(VariableNode(id="p_002", name="p2", description="d", abstraction_level=0,
                            confidence=0.8, epistemic="observation"))
    g.add_node(VariableNode(id="c_001", name="abstract", description="abs",
                            abstraction_level=1, confidence=0.7, epistemic="insight"))
    g.add_edge(RelationEdge(id="e_001", source_node="c_001", target_node="p_001",
                            relation_type="manifests_as", confidence=0.7,
                            mechanism_description="m1"))
    g.add_edge(RelationEdge(id="e_002", source_node="c_001", target_node="p_002",
                            relation_type="manifests_as", confidence=0.7,
                            mechanism_description="m2"))
    state = CognitiveState(graph=g, budget_remaining=10, root_question="why")
    return state


@pytest.mark.asyncio
async def test_happy_path_creates_drivers() -> None:
    state = _state_with_frontier()
    llm = FakeLLM([
        {"name": "driver A", "description": "da", "mechanism": "ma", "plausibility": 4},
        {"name": "driver B", "description": "db", "mechanism": "mb", "plausibility": 3},
    ])
    new_ids, gain = await expansion.expand_one_frontier(state, "c_001", llm)

    assert new_ids == ["d_001", "d_002"]
    assert state.graph.nodes["d_001"].abstraction_level == 2
    assert state.graph.nodes["d_001"].epistemic == "inference"
    assert state.graph.nodes["d_001"].source == "llm"
    assert state.graph.nodes["d_001"].name == "driver A"
    # causes edge
    causes_edges = [e for e in state.graph.edges.values() if e.relation_type == "causes"]
    assert len(causes_edges) == 2
    causes_sources = [e.source_node for e in causes_edges]
    assert "d_001" in causes_sources
    assert "d_002" in causes_sources
    assert all(e.target_node == "c_001" for e in causes_edges)
    # gain = mean(4, 3) / 5.0 = 0.7
    assert abs(gain - 0.7) < 1e-9


@pytest.mark.asyncio
async def test_truncate_to_3_drivers() -> None:
    state = _state_with_frontier()
    llm = FakeLLM([
        {"name": f"d{i}", "description": "x", "mechanism": "m", "plausibility": 3}
        for i in range(5)
    ])
    new_ids, gain = await expansion.expand_one_frontier(state, "c_001", llm)
    assert len(new_ids) == 3
    # gain = mean(3, 3, 3) / 5.0 = 0.6
    assert abs(gain - 0.6) < 1e-9


@pytest.mark.asyncio
async def test_zero_drivers_returns_empty_no_raise(caplog) -> None:
    state = _state_with_frontier()
    llm = FakeLLM([])
    new_ids, gain = await expansion.expand_one_frontier(state, "c_001", llm)
    assert new_ids == []
    assert gain == 0.0
    assert "0 driver" in caplog.text.lower() or "no driver" in caplog.text.lower()


@pytest.mark.asyncio
async def test_target_not_in_graph_raises() -> None:
    state = _state_with_frontier()
    llm = FakeLLM([])
    with pytest.raises(ValueError, match=r"not.*found|不存在"):
        await expansion.expand_one_frontier(state, "c_999", llm)


@pytest.mark.asyncio
async def test_target_not_level_1_raises() -> None:
    """expand 一个 concrete (level=0) 应该报错。"""
    state = _state_with_frontier()
    llm = FakeLLM([])
    with pytest.raises(ValueError, match="level"):
        await expansion.expand_one_frontier(state, "p_001", llm)


@pytest.mark.asyncio
async def test_target_already_has_causes_raises() -> None:
    """expand 一个已经有 incoming causes 的 abstract 应该报错。"""
    state = _state_with_frontier()
    state.graph.add_node(VariableNode(
        id="d_existing", name="existing", description="d", abstraction_level=2,
        confidence=0.6, epistemic="inference",
    ))
    state.graph.add_edge(RelationEdge(
        id="e_existing", source_node="d_existing", target_node="c_001",
        relation_type="causes", confidence=0.7, mechanism_description="m",
    ))
    llm = FakeLLM([])
    with pytest.raises(ValueError, match=r"frontier|causes"):
        await expansion.expand_one_frontier(state, "c_001", llm)


@pytest.mark.asyncio
async def test_id_continuation() -> None:
    """已有 d_001/d_002 时新建从 d_003 开始。"""
    state = _state_with_frontier()
    state.graph.add_node(VariableNode(
        id="d_001", name="old1", description="d", abstraction_level=2,
        confidence=0.6, epistemic="inference",
    ))
    state.graph.add_node(VariableNode(
        id="d_002", name="old2", description="d", abstraction_level=2,
        confidence=0.6, epistemic="inference",
    ))
    llm = FakeLLM([
        {"name": "new", "description": "d", "mechanism": "m", "plausibility": 4},
    ])
    new_ids, _ = await expansion.expand_one_frontier(state, "c_001", llm)
    assert new_ids == ["d_003"]


@pytest.mark.asyncio
async def test_invalid_schema_retry_then_raise() -> None:
    """LLM 第一次返非法 schema → retry 1 次仍失败 → 抛 SchemaValidationError。"""
    state = _state_with_frontier()

    class FailingLLM:
        async def chat(self, messages, schema=None, model=None):
            return Response(
                text="bad",
                parsed={"wrong_key": "x"},
                model="fake",
                usage={"input_tokens": 0, "output_tokens": 0},
            )

    with pytest.raises(SchemaValidationError):
        await expansion.expand_one_frontier(state, "c_001", FailingLLM())


@pytest.mark.asyncio
async def test_invalid_plausibility_retry_then_raise() -> None:
    state = _state_with_frontier()
    llm = FakeLLM(
        [],
        parsed={"drivers": [
            {"name": "d", "description": "d", "mechanism": "m", "plausibility": 7},
        ]},
    )
    with pytest.raises(SchemaValidationError):
        await expansion.expand_one_frontier(state, "c_001", llm)


@pytest.mark.asyncio
async def test_existing_driver_name_match_reused() -> None:
    """LLM 出的 driver name 跟现有 node 完全相同 → 复用现有 id，只加 edge。"""
    state = _state_with_frontier()
    state.graph.add_node(VariableNode(
        id="d_existing", name="重复 driver", description="d", abstraction_level=2,
        confidence=0.6, epistemic="inference",
    ))
    llm = FakeLLM([
        {"name": "重复 driver", "description": "d2", "mechanism": "m", "plausibility": 4},
    ])
    new_ids, _ = await expansion.expand_one_frontier(state, "c_001", llm)
    assert new_ids == ["d_existing"]
    # 没有新建 d_NNN
    d_nodes = [nid for nid in state.graph.nodes if nid.startswith("d_")]
    assert len(d_nodes) == 1
    # 但加了一条 edge d_existing → c_001
    causes = [e for e in state.graph.edges.values() if e.relation_type == "causes"]
    assert any(e.source_node == "d_existing" and e.target_node == "c_001" for e in causes)


@pytest.mark.asyncio
async def test_prompt_passes_existing_drivers() -> None:
    state = _state_with_frontier()
    state.graph.add_node(VariableNode(
        id="d_001", name="prior driver", description="d", abstraction_level=2,
        confidence=0.6, epistemic="inference",
    ))
    llm = FakeLLM([
        {"name": "new", "description": "d", "mechanism": "m", "plausibility": 4},
    ])
    await expansion.expand_one_frontier(state, "c_001", llm)
    # 验证 prompt 里 existing_drivers placeholder 含 "prior driver"
    user_msg = next(m for m in llm.last_messages if m.role == "user")
    assert "prior driver" in user_msg.content
