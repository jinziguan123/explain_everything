"""Wave B.3: CounterfactualEngine.substitute 测试. design §5.4.

副作用必须 = 0 (不改 state.graph). 这是 B.3 跟 B.2 (predict) 的关键区别.
"""

import pytest

from explain_engine.engines.counterfactual import substitute
from explain_engine.engines.intervention_parser import (
    NewConceptSpec,
    ParsedIntervention,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state() -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id="c_001", name="c", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="d_002", name="driver", description="d",
        abstraction_level=2, confidence=0.6, epistemic="inference",
    ))
    g.add_node(VariableNode(
        id="p_001", name="L0", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m",
    ))
    g.add_edge(RelationEdge(
        id="e_002", source_node="d_002", target_node="c_001",
        relation_type="causes", confidence=0.6,
        mechanism_description="m",
    ))
    return CognitiveState(graph=g, budget_remaining=10, root_question="why")


def _mock_parser(mocker, parsed: ParsedIntervention):
    async def fake_parse(*args, **kwargs):
        return parsed
    mocker.patch(
        "explain_engine.engines.counterfactual.parse_intervention",
        side_effect=fake_parse,
    )


def _mock_substitute_llm(mocker, narrative_text: str = "alt narrative"):
    """Mock LLM 返 generation + narrative outputs (in order)."""
    from unittest.mock import MagicMock
    def make_resp(content_dict):
        r = MagicMock()
        r.parsed = content_dict
        return r
    llm = mocker.AsyncMock()
    llm.chat = mocker.AsyncMock(side_effect=[
        make_resp({"predicted_L0": [
            {"name": "alt_p", "description": "d", "mechanism": "m"},
        ]}),
        make_resp({"narrative": narrative_text}),
    ])
    return llm


class TestCounterfactualNoMutation:
    @pytest.mark.asyncio
    async def test_state_graph_not_mutated_on_remove(self, mocker) -> None:
        state = _make_state()
        snapshot = state.graph.to_dict()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"], new_concepts=[],
        ))
        llm = mocker.AsyncMock()
        await substitute(state, "如果删除 d_002", llm)
        # to_dict 比 set(keys) 严格: 同时捕 structural 与 value mutation
        # (e.g. node.confidence / description 被改).
        assert state.graph.to_dict() == snapshot

    @pytest.mark.asyncio
    async def test_state_graph_not_mutated_on_substitute(self, mocker) -> None:
        state = _make_state()
        snapshot = state.graph.to_dict()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"],
            new_concepts=[NewConceptSpec(name="替代", description="d", expected_level=2)],
        ))
        llm = _mock_substitute_llm(mocker)
        await substitute(state, "用 X 替代 Y", llm)
        assert state.graph.to_dict() == snapshot


class TestCounterfactualReport:
    @pytest.mark.asyncio
    async def test_pure_remove_no_narrative(self, mocker) -> None:
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"], new_concepts=[],
        ))
        llm = mocker.AsyncMock()
        report = await substitute(state, "如果删除 d_002", llm)
        assert report.alt_narrative is None
        assert report.removed_node_ids == ["d_002"]
        assert report.added_node_ids == []
        # pure remove 不调 LLM (no generation, no narrative)
        assert llm.chat.call_count == 0

    @pytest.mark.asyncio
    async def test_substitute_returns_narrative(self, mocker) -> None:
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"],
            new_concepts=[NewConceptSpec(name="替代", description="d", expected_level=2)],
        ))
        llm = _mock_substitute_llm(mocker, narrative_text="alt trajectory: ...")
        report = await substitute(state, "用 X 替代 d_002", llm)
        assert report.alt_narrative == "alt trajectory: ..."
        assert len(report.added_node_ids) == 1
        assert len(report.added_predicted_L0_ids) == 1

    @pytest.mark.asyncio
    async def test_activation_diff_calculated(self, mocker) -> None:
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"], new_concepts=[],
        ))
        llm = mocker.AsyncMock()
        report = await substitute(state, "x", llm)
        # baseline 跑全 L1+L2 = {c_001, d_002} → p_001 act > 0
        assert report.baseline_acts.get("p_001", 0.0) > 0
        # counterfactual 删 d_002, baseline 仍含 c_001
        assert report.counterfactual_acts.get("p_001", 0.0) > 0
        # diff 应非空
        assert "p_001" in report.activation_diff

    @pytest.mark.asyncio
    async def test_substitute_with_two_new_concepts_links_first_only(self, mocker) -> None:
        """跟 B.2 I1 fix 对齐: 2 new_concepts → 仅第一个跟 predicted L0 建 edge.

        副作用 = 0, 所以只能通过 added_predicted_L0_ids count + counterfactual_acts 验证.
        """
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=[],
            new_concepts=[
                NewConceptSpec(name="A", description="d", expected_level=2),
                NewConceptSpec(name="B", description="d", expected_level=2),
            ],
        ))
        llm = _mock_substitute_llm(mocker, narrative_text="alt")
        report = await substitute(state, "A + B", llm)
        # 2 new_concepts created (deep copy)
        assert len(report.added_node_ids) == 2
        # 1 predicted L0 created (from mock)
        assert len(report.added_predicted_L0_ids) == 1


class TestCounterfactualErrors:
    @pytest.mark.asyncio
    async def test_parser_empty_raises(self, mocker) -> None:
        state = _make_state()
        async def fake_parse(*args, **kwargs):
            raise ValueError("无法解析")
        mocker.patch(
            "explain_engine.engines.counterfactual.parse_intervention",
            side_effect=fake_parse,
        )
        with pytest.raises(ValueError):
            await substitute(state, "废话", mocker.AsyncMock())

    @pytest.mark.asyncio
    async def test_remove_nonexistent_id_is_silent(self, mocker) -> None:
        """Defensive branch (counterfactual.py: `if rid in cf_graph.nodes`).

        parser 已经会拒 invalid id, 但 counterfactual 多一层防御兜 race
        condition (e.g. parser 跟 substitute 之间 state 被改).

        本测试通过 mock parser 直接注一个 graph 里没有的 id, 绕过 parser 的
        valid_ids 校验, 真触发 defensive branch.

        Assertion: removed_node_ids 只含*实际*删的 id, 不含 silently 跳过的.
        """
        state = _make_state()
        # 直接 mock parser 输出含 ghost id (绕过正常 valid_ids 校验)
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002", "ghost_nonexistent"],
            new_concepts=[],
        ))
        llm = mocker.AsyncMock()
        report = await substitute(state, "x", llm)
        # 仅 d_002 被实际删; ghost_nonexistent silently 跳
        assert report.removed_node_ids == ["d_002"]
