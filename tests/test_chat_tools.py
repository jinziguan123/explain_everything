"""Phase 9 Wave B Task B.1: Tool dataclass + 5 engine tool wrappers.

测试覆盖:
- Tool dataclass 默认字段
- ALL_TOOLS 注册 5 个 engine tools
- 每个 tool 的 input_schema / dynamic description / call dispatch

设计参考 docs/plans/2026-05-14-cognitive-engine-phase-9-plan.md Wave B Task B.1.
"""

from __future__ import annotations

import pytest

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state_with_l1(l1_id: str = "c_001", l0_id: str = "p_001") -> CognitiveState:
    """构造一个带 1 L1 + 1 L0 + manifests_as edge 的简单 state."""
    g = ExplanationGraph(root_question="why X")
    g.add_node(VariableNode(
        id=l1_id, name="L1 abstract", description="abstraction d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id=l0_id, name="L0 phenom", description="phenom d",
        abstraction_level=0, confidence=0.8, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node=l1_id, target_node=l0_id,
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m",
    ))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="why X",
        insight_candidates=[l1_id],
    )


class TestToolDataclass:
    def test_tool_has_required_fields(self) -> None:
        """Tool 最小构造: name + input_schema + description + call."""
        from pydantic import BaseModel

        from explain_engine.chat.tools import Tool, ToolContext

        class _DummyInput(BaseModel):
            pass

        async def _dummy_call(input: _DummyInput, ctx: ToolContext) -> str:
            return "ok"

        t = Tool(
            name="dummy",
            input_schema=_DummyInput,
            description=lambda ctx: "dummy tool",
            call=_dummy_call,
        )
        assert t.name == "dummy"
        assert t.input_schema is _DummyInput
        # 默认 flag
        assert t.is_readonly is False
        assert t.is_destructive is False
        assert t.requires_hitl is False


class TestALL_TOOLS:
    def test_contains_5_engine_tools(self) -> None:
        """B.1 baseline: 5 engine tools registered."""
        from explain_engine.chat.tools import ALL_TOOLS

        names = {t.name for t in ALL_TOOLS}
        # B.2 added 2 more (add_observation + read_node); engine tools still present.
        assert {"expand", "compress", "check", "predict", "counterfactual"} <= names


class TestExpandTool:
    def test_description_includes_graph_hint(self) -> None:
        from explain_engine.chat.tools import ToolContext, expand_tool

        state = _make_state_with_l1()
        ctx = ToolContext(state=state, llm=None)
        desc = expand_tool.description(ctx)
        # 描述应该包含 L0/L1 计数提示
        assert "L0" in desc
        assert "L1" in desc

    def test_input_schema_validates_direction(self) -> None:
        from pydantic import ValidationError

        from explain_engine.chat.tools import expand_tool

        # 合法 direction
        for d in ("downward", "upward", "auto"):
            inst = expand_tool.input_schema(l1_id="c_001", direction=d)
            assert inst.direction == d
        # 非法 direction
        with pytest.raises(ValidationError):
            expand_tool.input_schema(l1_id="c_001", direction="sideways")

    @pytest.mark.asyncio
    async def test_call_dispatches_to_expand_downward(self, mocker) -> None:
        """direction=downward + l1_id → dispatch 到 expansion.expand_downward."""
        from explain_engine.chat.tools import ToolContext, expand_tool

        state = _make_state_with_l1("c_001")
        # mock expansion.expand_downward 返新 L0 id list
        mock_downward = mocker.patch(
            "explain_engine.chat.tools.expansion.expand_downward",
            new_callable=mocker.AsyncMock,
            return_value=["p_002", "p_003"],
        )
        ctx = ToolContext(state=state, llm=mocker.AsyncMock())
        result = await expand_tool.call(
            expand_tool.input_schema(l1_id="c_001", direction="downward"),
            ctx,
        )
        mock_downward.assert_awaited_once()
        # 第一个 positional 是 state, 第二个是 l1_id
        call_args = mock_downward.await_args
        assert call_args.args[1] == "c_001"
        # 返回字符串包含新加 L0 ids
        assert "p_002" in result
        assert "p_003" in result


class TestCompressTool:
    @pytest.mark.asyncio
    async def test_call_dispatches_to_compression(self, mocker) -> None:
        """compress_tool 调 compression.propose_candidates, 返 state.insight_candidates summary."""
        from explain_engine.chat.tools import ToolContext, compress_tool

        state = _make_state_with_l1("c_001")

        async def _fake_propose(s, llm, **kw):
            # 模拟 propose_candidates 副作用: 加 L1 候选 id 到 insight_candidates
            s.insight_candidates = ["c_001", "c_002", "c_003"]

        mock_compress = mocker.patch(
            "explain_engine.chat.tools.compression.propose_candidates",
            new_callable=mocker.AsyncMock,
            side_effect=_fake_propose,
        )
        ctx = ToolContext(state=state, llm=mocker.AsyncMock())
        result = await compress_tool.call(compress_tool.input_schema(), ctx)
        mock_compress.assert_awaited_once()
        # 结果应该包含 3 个候选
        assert "3" in result
        assert "c_001" in result or "candidates" in result.lower()


class TestCheckTool:
    def test_call_returns_acceptance_summary(self, mocker) -> None:
        """check_tool (target_id=None) → aggregate_acceptance, 返回带 multi-signal 的 summary."""
        import asyncio

        from explain_engine.chat.tools import ToolContext, check_tool
        from explain_engine.engines.simulation import AcceptanceReport

        state = _make_state_with_l1("c_001")
        fake_report = AcceptanceReport(
            avg_consistency=0.85,
            avg_essentialness=0.42,
            per_l1={"c_001": 0.85},
            per_l2={},
            weak_chain_l1s=[],
            lowest_l1=("c_001", 0.85),
            consistency_spread=0.0,
            essentialness_spread=0.0,
            rollout_coverage=0.9,
            missing_l0=[],
        )
        mocker.patch(
            "explain_engine.chat.tools.aggregate_acceptance",
            return_value=fake_report,
        )
        ctx = ToolContext(state=state, llm=None)
        result = asyncio.run(check_tool.call(check_tool.input_schema(), ctx))
        assert "0.85" in result or "consistency" in result.lower()
        assert "0.42" in result or "essentialness" in result.lower()
        assert "rollout" in result.lower() or "0.9" in result

    def test_check_tool_is_readonly(self) -> None:
        from explain_engine.chat.tools import check_tool

        assert check_tool.is_readonly is True


class TestPredictAndCounterfactualTools:
    def test_predict_has_intervention_text_param(self) -> None:
        from pydantic import ValidationError

        from explain_engine.chat.tools import predict_tool

        # 合法
        inst = predict_tool.input_schema(intervention_text="如果加入 X")
        assert inst.intervention_text == "如果加入 X"
        # 空字符串非法 (min_length=1)
        with pytest.raises(ValidationError):
            predict_tool.input_schema(intervention_text="")

    def test_counterfactual_has_intervention_text_param(self) -> None:
        from pydantic import ValidationError

        from explain_engine.chat.tools import counterfactual_tool

        inst = counterfactual_tool.input_schema(intervention_text="如果移除 Y")
        assert inst.intervention_text == "如果移除 Y"
        with pytest.raises(ValidationError):
            counterfactual_tool.input_schema(intervention_text="")


class TestPickFrontierL1:
    """I1 regression: _pick_frontier_l1 prefers low consistency_score."""

    def test_picks_lowest_consistency_when_cache_available(self) -> None:
        from explain_engine.chat.tools import _pick_frontier_l1
        from explain_engine.engines.simulation import AcceptanceReport

        g = ExplanationGraph(root_question="why")
        # 2 frontier L1s (no incoming causes): c_001 (high score), c_002 (low score)
        for nid in ("c_001", "c_002"):
            g.add_node(VariableNode(
                id=nid, name=nid, description="d",
                abstraction_level=1, confidence=0.7, epistemic="insight",
            ))
        state = CognitiveState(
            graph=g, budget_remaining=10, root_question="why",
            insight_candidates=["c_001", "c_002"],
        )
        # Inject acceptance report with per_l1
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=0.5, avg_essentialness=0.5,
            per_l1={"c_001": 0.9, "c_002": 0.1},  # c_002 weaker
        )
        # Should pick c_002 (lower score), not c_001 (insertion order would pick c_001)
        assert _pick_frontier_l1(state) == "c_002"

    def test_falls_back_to_insertion_order_without_cache(self) -> None:
        from explain_engine.chat.tools import _pick_frontier_l1

        g = ExplanationGraph(root_question="why")
        for nid in ("c_001", "c_002"):
            g.add_node(VariableNode(
                id=nid, name=nid, description="d",
                abstraction_level=1, confidence=0.7, epistemic="insight",
            ))
        state = CognitiveState(
            graph=g, budget_remaining=10, root_question="why",
            insight_candidates=["c_001", "c_002"],
        )
        # No cache → insertion order → c_001 first
        assert _pick_frontier_l1(state) == "c_001"

    def test_skips_decayed_and_non_frontier(self) -> None:
        from explain_engine.chat.tools import _pick_frontier_l1

        g = ExplanationGraph(root_question="why")
        # c_001: decayed (skip)
        # c_002: has incoming causes (not frontier, skip)
        # c_003: active frontier (pick)
        g.add_node(VariableNode(
            id="c_001", name="c_001", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
            lifecycle_state="decayed",
        ))
        g.add_node(VariableNode(
            id="c_002", name="c_002", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
        g.add_node(VariableNode(
            id="c_003", name="c_003", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
        g.add_node(VariableNode(
            id="d_001", name="d_001", description="d",
            abstraction_level=2, confidence=0.7, epistemic="inference",
        ))
        # d_001 → causes → c_002 (c_002 not frontier)
        g.add_edge(RelationEdge(
            id="e_001", source_node="d_001", target_node="c_002",
            relation_type="causes", confidence=0.7, mechanism_description="m",
        ))
        state = CognitiveState(
            graph=g, budget_remaining=10, root_question="why",
        )
        assert _pick_frontier_l1(state) == "c_003"


class TestAddObservationTool:
    def test_input_schema_source_literal(self) -> None:
        from explain_engine.chat.tools import add_observation_tool
        input = add_observation_tool.input_schema(
            name="new obs", description="d", source="user_explicit",
        )
        assert input.source == "user_explicit"

    def test_input_schema_rejects_invalid_source(self) -> None:
        from pydantic import ValidationError

        from explain_engine.chat.tools import add_observation_tool
        with pytest.raises(ValidationError):
            add_observation_tool.input_schema(
                name="x", description="d", source="other",
            )

    @pytest.mark.asyncio
    async def test_call_adds_l0_node(self) -> None:
        from explain_engine.chat.tools import ToolContext, add_observation_tool
        state = _make_state_with_l1()
        ctx = ToolContext(state=state)
        input = add_observation_tool.input_schema(
            name="新观察", description="desc", source="user_explicit",
        )
        result = await add_observation_tool.call(input, ctx)
        new_nodes = [n for n in state.graph.nodes.values()
                     if n.name == "新观察" and n.abstraction_level == 0]
        assert len(new_nodes) == 1
        assert new_nodes[0].epistemic == "observation"
        assert "added" in result.lower()

    def test_requires_hitl_flag(self) -> None:
        from explain_engine.chat.tools import add_observation_tool
        assert add_observation_tool.requires_hitl is True

    @pytest.mark.asyncio
    async def test_call_increments_p_id(self) -> None:
        """Sequential add_observation calls produce p_001, p_002, etc."""
        from explain_engine.chat.tools import ToolContext, add_observation_tool
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.state import CognitiveState

        g = ExplanationGraph(root_question="q")
        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
        ctx = ToolContext(state=state)

        await add_observation_tool.call(
            add_observation_tool.input_schema(name="o1", description="d", source="user_explicit"),
            ctx,
        )
        await add_observation_tool.call(
            add_observation_tool.input_schema(name="o2", description="d", source="user_explicit"),
            ctx,
        )
        ids = sorted([nid for nid in state.graph.nodes if nid.startswith("p_")])
        assert ids == ["p_001", "p_002"]


class TestReadNodeTool:
    @pytest.mark.asyncio
    async def test_call_returns_full_description(self) -> None:
        from explain_engine.chat.tools import ToolContext, read_node_tool
        state = _make_state_with_l1()
        ctx = ToolContext(state=state)
        input = read_node_tool.input_schema(node_id="c_001")
        result = await read_node_tool.call(input, ctx)
        assert "c_001" in result
        # Verify it includes lifecycle_state, level, etc.
        assert "level=1" in result
        assert "lifecycle_state=active" in result
        # M5: assert actual description text appears (regression guard)
        assert "abstraction d" in result  # description from _make_state_with_l1

    @pytest.mark.asyncio
    async def test_call_missing_node_returns_error_message(self) -> None:
        from explain_engine.chat.tools import ToolContext, read_node_tool
        state = _make_state_with_l1()
        ctx = ToolContext(state=state)
        input = read_node_tool.input_schema(node_id="c_999")
        result = await read_node_tool.call(input, ctx)
        assert "not found" in result.lower()

    def test_is_readonly(self) -> None:
        from explain_engine.chat.tools import read_node_tool
        assert read_node_tool.is_readonly is True


class TestALL_TOOLS_v2:
    def test_contains_all_registered_tools(self) -> None:
        from explain_engine.chat.tools import ALL_TOOLS
        names = {t.name for t in ALL_TOOLS}
        assert names == {
            "expand", "compress", "check", "predict", "counterfactual",
            "add_observation", "read_node",
            # 联网工具 (免费无 key DuckDuckGo)
            "web_search", "web_read",
        }

    def test_web_tools_are_readonly(self) -> None:
        from explain_engine.chat.tools import web_read_tool, web_search_tool
        assert web_search_tool.is_readonly is True
        assert web_read_tool.is_readonly is True


class TestAddObservationSourceLabel:
    """I1 regression: source field must reflect authoring agent."""

    @pytest.mark.asyncio
    async def test_user_explicit_sets_node_source_user(self) -> None:
        from explain_engine.chat.tools import ToolContext, add_observation_tool
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.state import CognitiveState

        g = ExplanationGraph(root_question="q")
        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
        ctx = ToolContext(state=state)

        await add_observation_tool.call(
            add_observation_tool.input_schema(
                name="o1", description="d", source="user_explicit",
            ),
            ctx,
        )
        new_node = next(n for n in state.graph.nodes.values() if n.name == "o1")
        assert new_node.source == "user"

    @pytest.mark.asyncio
    async def test_llm_inferred_sets_node_source_llm(self) -> None:
        from explain_engine.chat.tools import ToolContext, add_observation_tool
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.state import CognitiveState

        g = ExplanationGraph(root_question="q")
        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
        ctx = ToolContext(state=state)

        await add_observation_tool.call(
            add_observation_tool.input_schema(
                name="o2", description="d", source="llm_inferred",
            ),
            ctx,
        )
        new_node = next(n for n in state.graph.nodes.values() if n.name == "o2")
        assert new_node.source == "llm"


class TestReadNodeTruncation:
    """I2 regression: read_node soft-caps long descriptions."""

    @pytest.mark.asyncio
    async def test_long_description_truncated(self) -> None:
        from explain_engine.chat.tools import ToolContext, read_node_tool
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.nodes import VariableNode
        from explain_engine.schema.state import CognitiveState

        long_desc = "x" * 5000
        g = ExplanationGraph(root_question="q")
        g.add_node(VariableNode(
            id="c_001", name="big", description=long_desc,
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
        ctx = ToolContext(state=state)

        result = await read_node_tool.call(
            read_node_tool.input_schema(node_id="c_001"), ctx,
        )
        # Result should contain truncation marker
        assert "truncated" in result.lower()
        assert "5000 chars total" in result or "5000 chars" in result
        # Result should NOT contain the full 5000-char description
        assert len(result) < 5500  # well under 5000 + format string overhead

    @pytest.mark.asyncio
    async def test_short_description_not_truncated(self) -> None:
        from explain_engine.chat.tools import ToolContext, read_node_tool
        state = _make_state_with_l1()
        ctx = ToolContext(state=state)
        result = await read_node_tool.call(
            read_node_tool.input_schema(node_id="c_001"), ctx,
        )
        assert "truncated" not in result.lower()
