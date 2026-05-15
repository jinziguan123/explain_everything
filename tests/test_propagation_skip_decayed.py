"""Wave 4 Task 4.2: propagation / expansion 跳过 decayed 节点."""

import pytest

from explain_engine.engines._propagation import propagate, rollout_from_roots
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid, level, **kw):
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7,
        epistemic="observation" if level == 0 else "insight",
        **kw,
    )


def _g_with_decayed():
    g = ExplanationGraph(root_question="q")
    g.add_node(_node("c_001", 1, lifecycle_state="decayed"))   # decayed L1
    g.add_node(_node("c_002", 1))                              # active L1
    g.add_node(_node("p_001", 0))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.9, mechanism_description="m",
    ))
    g.add_edge(RelationEdge(
        id="e_002", source_node="c_002", target_node="p_001",
        relation_type="manifests_as", confidence=0.9, mechanism_description="m",
    ))
    return g


class TestRolloutSkipDecayed:
    def test_rollout_skips_decayed_root(self) -> None:
        g = _g_with_decayed()
        reachable, _missing = rollout_from_roots(g)
        assert "p_001" in reachable  # via active c_002


class TestPropagateSkipDecayed:
    def test_propagate_from_active_unaffected(self) -> None:
        """propagate from active source should reach p_001 normally."""
        g = _g_with_decayed()
        acts, _ = propagate(g, {"c_002"})
        assert acts.get("p_001", 0) > 0

    def test_propagate_skips_decayed_in_path(self) -> None:
        """If we propagate from c_001 (decayed), it shouldn't activate downstream."""
        # Note: propagate accepts {sources} — sources themselves get activation 1.0 by design.
        # But if c_001 is decayed, it still gets initial activation but shouldn't propagate further.
        # Behavior we want: decayed nodes don't transmit (out-edges blocked).
        g = _g_with_decayed()
        acts, _ = propagate(g, {"c_001"})
        # p_001 should NOT be activated via decayed c_001 (the only path)
        assert acts.get("p_001", 0) == 0
        # Verify source itself still gets activation 1.0 (only the propagation is blocked)
        assert acts.get("c_001", 0) == 1.0


class TestExpandDownwardSkipDecayed:
    def test_expand_downward_raises_for_decayed_l1(self) -> None:
        """expand_downward on a decayed L1 should raise."""
        import asyncio

        from explain_engine.engines.expansion import expand_downward
        from explain_engine.schema.state import CognitiveState

        g = _g_with_decayed()
        # c_001 is decayed
        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")

        class _FakeLLM:
            async def chat(self, messages, schema):
                return type("R", (), {"parsed": None})()

        with pytest.raises(ValueError, match=r"decayed|cannot expand"):
            asyncio.run(expand_downward(state, "c_001", _FakeLLM()))


class TestPredictSkipDecayed:
    """M3: predict() 内部用 propagate, 跳过 decayed 节点 contract 在此锁定.

    核心 contract: 即使把 decayed c_001 列入 sources, propagate 不让它向 p_001
    传 → activated_existing_L0 不会含 p_001 (没有非 decayed 路径触达 p_001 时).
    """

    @pytest.mark.asyncio
    async def test_predict_propagation_respects_decayed(self, mocker) -> None:
        """If only path to p_001 is via decayed c_001, p_001 stays inactive."""
        from explain_engine.engines.intervention_parser import ParsedIntervention
        from explain_engine.engines.prediction import predict
        from explain_engine.schema.state import CognitiveState

        g = ExplanationGraph(root_question="q")
        # decayed L1 → p_001 (唯一路径)
        g.add_node(_node("c_001", 1, lifecycle_state="decayed"))
        g.add_node(_node("p_001", 0))
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.9,
            mechanism_description="m",
        ))
        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")

        # Mock parser → existing_refs=[c_001] (decayed), no new_concepts
        async def _fake_parse(*args, **kwargs):
            return ParsedIntervention(existing_refs=["c_001"], new_concepts=[])

        mocker.patch(
            "explain_engine.engines.prediction.parse_intervention",
            side_effect=_fake_parse,
        )

        report = await predict(state, "test", mocker.AsyncMock())
        # decayed c_001 不向下传 → p_001 不激活 → 不出现在 activated_existing_L0
        assert "p_001" not in report.activated_existing_L0


class TestCounterfactualSkipDecayed:
    """M3: substitute() 内部用 propagate (baseline + cf), decayed 节点不影响 baseline.

    核心 contract: baseline_acts 由 propagate 算; decayed L1 不向下传, p_001 在
    baseline 也不激活 (跟 cf 一致). diff 不会因 decayed 而失真.
    """

    @pytest.mark.asyncio
    async def test_substitute_baseline_respects_decayed(self, mocker) -> None:
        """baseline propagation 跳过 decayed source → 与 cf 一致, 不失真."""
        from explain_engine.engines.counterfactual import substitute
        from explain_engine.engines.intervention_parser import ParsedIntervention
        from explain_engine.schema.state import CognitiveState

        g = ExplanationGraph(root_question="q")
        # decayed L1 → p_001 (唯一路径). 加 active L2 driver 给一个非 trivial graph.
        g.add_node(_node("c_001", 1, lifecycle_state="decayed"))
        g.add_node(_node("d_001", 2))
        g.add_node(_node("p_001", 0))
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.9,
            mechanism_description="m",
        ))
        g.add_edge(RelationEdge(
            id="e_002", source_node="d_001", target_node="c_001",
            relation_type="causes", confidence=0.6,
            mechanism_description="m",
        ))
        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")

        # Mock parser → pure remove case (no new_concepts → 0 LLM call after parser)
        async def _fake_parse(*args, **kwargs):
            return ParsedIntervention(existing_refs=["d_001"], new_concepts=[])

        mocker.patch(
            "explain_engine.engines.counterfactual.parse_intervention",
            side_effect=_fake_parse,
        )

        report = await substitute(state, "remove d_001", mocker.AsyncMock())
        # baseline: 即使 d_001 active, c_001 是 decayed → p_001 不激活
        assert report.baseline_acts.get("p_001", 0) == 0
