"""Wave 4 Task 4.2: reflect 加 decay action."""

from explain_engine.engines import reflection
from explain_engine.engines.simulation import AcceptanceReport
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _state_with_low_fitness_no_weak() -> CognitiveState:
    """构造一个无 weak chain 但有 low-fitness 节点的 state."""
    g = ExplanationGraph(root_question="q")
    # 高 conf chain → no weak L1
    g.add_node(VariableNode(
        id="c_001", name="strong", description="d",
        abstraction_level=1, confidence=0.9, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="p", description="d",
        abstraction_level=0, confidence=0.9, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.9,
        mechanism_description="m",
    ))
    # 加一个孤立低 fitness L2
    g.add_node(VariableNode(
        id="d_999", name="useless", description="d",
        abstraction_level=2, confidence=0.7, epistemic="inference",
        activation=0.0, stability=0.0,
    ))
    state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
    state.last_acceptance_report = AcceptanceReport(
        avg_consistency=0.9, avg_essentialness=0.0,
        per_l1={"c_001": 0.9},
        per_l2={"d_999": 0.0},
        weak_chain_l1s=[],
    )
    return state


class TestReflectDecay:
    def test_picks_lowest_fitness_below_threshold(self) -> None:
        state = _state_with_low_fitness_no_weak()
        target = reflection.pick_decay_target(state)
        assert target == "d_999"

    def test_returns_none_when_all_above_threshold(self) -> None:
        state = _state_with_low_fitness_no_weak()
        # 拉高 d_999 activation
        state.graph.nodes["d_999"].activation = 1.0
        state.graph.nodes["d_999"].stability = 1.0
        target = reflection.pick_decay_target(state)
        assert target is None

    def test_skips_already_decayed(self) -> None:
        state = _state_with_low_fitness_no_weak()
        state.graph.nodes["d_999"].lifecycle_state = "decayed"
        target = reflection.pick_decay_target(state)
        assert target is None

    def test_skips_l0_nodes(self) -> None:
        """Task 4.1 M4 contract: L0 nodes shouldn't be decay candidates."""
        state = _state_with_low_fitness_no_weak()
        # Make p_001 also have low activation/stability
        state.graph.nodes["p_001"].activation = 0.0
        state.graph.nodes["p_001"].stability = 0.0
        # Even with low fitness, L0 should not be picked
        target = reflection.pick_decay_target(state)
        assert target != "p_001"
        # Should still pick d_999 (the L2 with low fitness)
        assert target == "d_999"

    def test_reflect_returns_decay_action(self) -> None:
        state = _state_with_low_fitness_no_weak()
        action, target = reflection.reflect(state)
        # 无 weak L1 + 有 low fitness L2 → 应该 decay
        assert action == "decay"
        assert target == "d_999"

    def test_decay_priority_after_expand_downward(self) -> None:
        """priority: expand-downward > decay > prune > stop."""
        state = _state_with_low_fitness_no_weak()
        # 加一个 weak L1 → 应该返 expand-downward 而非 decay
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=0.5, avg_essentialness=0.0,
            per_l1={"c_001": 0.2},
            per_l2={"d_999": 0.0},
            weak_chain_l1s=["c_001"],
        )
        action, _target = reflection.reflect(state)
        assert action == "expand-downward"  # 优先级高于 decay
