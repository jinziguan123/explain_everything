"""Wave 4 Task 4.2: propagation / expansion 跳过 decayed 节点."""

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


class TestExpandDownwardSkipDecayed:
    def test_expand_downward_raises_for_decayed_l1(self) -> None:
        """expand_downward on a decayed L1 should raise."""
        import asyncio

        import pytest

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
