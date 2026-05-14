"""Wave B.2: ForwardPredictionEngine.predict 测试. design §5.3."""

import pytest

from explain_engine.engines.intervention_parser import (
    NewConceptSpec,
    ParsedIntervention,
)
from explain_engine.engines.prediction import (
    predict,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state() -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id="c_001", name="abstract", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="d_002", name="driver", description="d",
        abstraction_level=2, confidence=0.6, epistemic="inference",
    ))
    g.add_node(VariableNode(
        id="p_001", name="existing_L0", description="d",
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
        "explain_engine.engines.prediction.parse_intervention",
        side_effect=fake_parse,
    )


def _mock_generation(mocker, predicted_L0: list[dict]):
    """Mock LLM 返 PredictionOutput."""
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.parsed = {"predicted_L0": predicted_L0}
    return resp


class TestPredictMainPath:
    @pytest.mark.asyncio
    async def test_new_concept_creates_node_and_predicted_L0(self, mocker) -> None:
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=[],
            new_concepts=[NewConceptSpec(
                name="新概念", description="d", expected_level=2,
            )],
        ))
        gen_resp = _mock_generation(mocker, [
            {"name": "predicted_p1", "description": "d", "mechanism": "m1"},
            {"name": "predicted_p2", "description": "d", "mechanism": "m2"},
        ])
        llm = mocker.AsyncMock()
        llm.chat = mocker.AsyncMock(return_value=gen_resp)
        report = await predict(state, "新概念", llm)
        new_drivers = [
            nid for nid, n in state.graph.nodes.items()
            if n.name == "新概念" and n.abstraction_level == 2
        ]
        assert len(new_drivers) == 1
        predicted = [
            nid for nid, n in state.graph.nodes.items()
            if n.epistemic == "speculation" and n.abstraction_level == 0
        ]
        assert len(predicted) == 2
        assert len(report.new_node_ids) == 1
        assert len(report.predicted_L0_ids) == 2

    @pytest.mark.asyncio
    async def test_existing_only_skips_generation(self, mocker) -> None:
        """B1 退化 case: existing_refs only, 不调 generation LLM."""
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"], new_concepts=[],
        ))
        llm = mocker.AsyncMock()
        report = await predict(state, "已有 driver", llm)
        assert llm.chat.call_count == 0
        assert report.new_node_ids == []
        assert report.predicted_L0_ids == []
        assert "d_002" in report.propagation_acts

    @pytest.mark.asyncio
    async def test_predicted_L0_uses_speculation_epistemic(self, mocker) -> None:
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=[],
            new_concepts=[NewConceptSpec(name="新", description="d", expected_level=2)],
        ))
        gen_resp = _mock_generation(mocker, [
            {"name": "predicted", "description": "d", "mechanism": "m"},
        ])
        llm = mocker.AsyncMock()
        llm.chat = mocker.AsyncMock(return_value=gen_resp)
        report = await predict(state, "新", llm)
        predicted_id = report.predicted_L0_ids[0]
        assert state.graph.nodes[predicted_id].epistemic == "speculation"
        assert state.graph.nodes[predicted_id].abstraction_level == 0

    @pytest.mark.asyncio
    async def test_predicted_L0_edge_uses_default_0_7(self, mocker) -> None:
        """新加的 manifests_as edge 应该 conf=0.7 (LLM 没自评 plausibility 用 default)."""
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=[],
            new_concepts=[NewConceptSpec(name="新", description="d", expected_level=2)],
        ))
        gen_resp = _mock_generation(mocker, [
            {"name": "p", "description": "d", "mechanism": "m"},
        ])
        llm = mocker.AsyncMock()
        llm.chat = mocker.AsyncMock(return_value=gen_resp)
        report = await predict(state, "新", llm)
        new_edges = [
            e for e in state.graph.edges.values()
            if e.relation_type == "manifests_as"
            and e.target_node == report.predicted_L0_ids[0]
        ]
        assert len(new_edges) == 1
        assert new_edges[0].confidence == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_predict_returns_propagation_acts(self, mocker) -> None:
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"], new_concepts=[],
        ))
        llm = mocker.AsyncMock()
        report = await predict(state, "x", llm)
        assert report.propagation_acts.get("d_002") == 1.0
        assert "p_001" in report.propagation_acts

    @pytest.mark.asyncio
    async def test_new_concept_level_1_uses_c_prefix(self, mocker) -> None:
        """expected_level=1 → c_NNN id; level=2 → d_NNN id."""
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=[],
            new_concepts=[NewConceptSpec(name="abstract_concept", description="d", expected_level=1)],
        ))
        gen_resp = _mock_generation(mocker, [
            {"name": "p", "description": "d", "mechanism": "m"},
        ])
        llm = mocker.AsyncMock()
        llm.chat = mocker.AsyncMock(return_value=gen_resp)
        report = await predict(state, "x", llm)
        assert report.new_node_ids[0].startswith("c_")

    @pytest.mark.asyncio
    async def test_activated_existing_L0_excludes_predicted(self, mocker) -> None:
        """activated_existing_L0 不应该含 predicted (speculation) L0."""
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"],
            new_concepts=[NewConceptSpec(name="X", description="d", expected_level=2)],
        ))
        gen_resp = _mock_generation(mocker, [
            {"name": "predicted", "description": "d", "mechanism": "m"},
        ])
        llm = mocker.AsyncMock()
        llm.chat = mocker.AsyncMock(return_value=gen_resp)
        report = await predict(state, "x", llm)
        # activated_existing_L0 should only contain p_001 (original L0), not the predicted L0
        for pid in report.activated_existing_L0:
            assert state.graph.nodes[pid].epistemic != "speculation"

    @pytest.mark.asyncio
    async def test_generation_failure_raises(self, mocker) -> None:
        """LLM 生 predicted L0 失败 → SchemaValidationError after retry."""
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=[],
            new_concepts=[NewConceptSpec(name="X", description="d", expected_level=2)],
        ))
        from unittest.mock import MagicMock
        bad_resp = MagicMock()
        bad_resp.parsed = None
        llm = mocker.AsyncMock()
        llm.chat = mocker.AsyncMock(return_value=bad_resp)
        from explain_engine.llm.errors import SchemaValidationError
        with pytest.raises(SchemaValidationError):
            await predict(state, "X", llm)

    @pytest.mark.asyncio
    async def test_parser_valueerror_propagates(self, mocker) -> None:
        state = _make_state()
        async def fake_parse(*args, **kwargs):
            raise ValueError("无法解析")
        mocker.patch(
            "explain_engine.engines.prediction.parse_intervention",
            side_effect=fake_parse,
        )
        with pytest.raises(ValueError, match="无法解析"):
            await predict(state, "废话", mocker.AsyncMock())

    @pytest.mark.asyncio
    async def test_no_predicted_L0_for_existing_only_case(self, mocker) -> None:
        """existing_refs only → 不生 predicted L0 (跳过 generation)."""
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"], new_concepts=[],
        ))
        llm = mocker.AsyncMock()
        await predict(state, "x", llm)
        speculation_nodes = [
            nid for nid, n in state.graph.nodes.items()
            if n.epistemic == "speculation"
        ]
        assert speculation_nodes == []
