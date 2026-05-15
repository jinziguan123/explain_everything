"""Wave 4 Task 4.2: lifecycle.update_lifecycle 状态机."""

from explain_engine.engines.lifecycle import (
    STALE_TO_DECAYED_TICKS,
    update_lifecycle,
)
from explain_engine.engines.simulation import AcceptanceReport
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _state_with_low_fitness_l1() -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    g.add_node(VariableNode(
        id="c_001", name="n", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
        activation=0.0, stability=0.0,
    ))
    state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
    state.last_acceptance_report = AcceptanceReport(
        avg_consistency=0.0, avg_essentialness=0.0,
        per_l1={"c_001": 0.0},
    )
    return state


class TestUpdateLifecycle:
    def test_active_to_stale_on_low_fitness(self) -> None:
        state = _state_with_low_fitness_l1()
        changes = update_lifecycle(state, current_tick=1)
        assert state.graph.nodes["c_001"].lifecycle_state == "stale"
        assert changes.get("c_001") == "stale"

    def test_stale_to_decayed_after_window(self) -> None:
        state = _state_with_low_fitness_l1()
        # 第一次 update → stale
        update_lifecycle(state, current_tick=1)
        # 等 STALE_TO_DECAYED_TICKS 后再 update → decayed
        update_lifecycle(state, current_tick=1 + STALE_TO_DECAYED_TICKS)
        assert state.graph.nodes["c_001"].lifecycle_state == "decayed"

    def test_decayed_does_not_revive(self) -> None:
        state = _state_with_low_fitness_l1()
        state.graph.nodes["c_001"].lifecycle_state = "decayed"
        # 即使 fitness 高也不复活 (Phase 8 决定; Phase 9 memory consolidation 处理)
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=1.0, avg_essentialness=1.0,
            per_l1={"c_001": 1.0},
        )
        update_lifecycle(state, current_tick=10)
        assert state.graph.nodes["c_001"].lifecycle_state == "decayed"

    def test_stale_to_active_recovery(self) -> None:
        state = _state_with_low_fitness_l1()
        update_lifecycle(state, current_tick=1)
        assert state.graph.nodes["c_001"].lifecycle_state == "stale"
        # fitness 回高
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=1.0, avg_essentialness=1.0,
            per_l1={"c_001": 1.0},
        )
        state.graph.nodes["c_001"].activation = 1.0
        update_lifecycle(state, current_tick=2)
        assert state.graph.nodes["c_001"].lifecycle_state == "active"

    def test_returns_change_log(self) -> None:
        state = _state_with_low_fitness_l1()
        changes = update_lifecycle(state, current_tick=1)
        assert isinstance(changes, dict)
        assert "c_001" in changes

    def test_no_change_returns_no_entry(self) -> None:
        """高 fitness active 节点 → 不出现在 changes."""
        state = _state_with_low_fitness_l1()
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=1.0, avg_essentialness=1.0,
            per_l1={"c_001": 1.0},
        )
        state.graph.nodes["c_001"].activation = 1.0
        changes = update_lifecycle(state, current_tick=1)
        # active stays active → no entry in changes
        assert "c_001" not in changes

    def test_stale_node_decays_after_save_load(self) -> None:
        """Regression: I1 — stale_since_tick must be persisted on VariableNode,
        not state._stale_since_ticks dict, so save+load preserves stale timing.
        """
        state = _state_with_low_fitness_l1()
        # First update → stale at tick=1
        update_lifecycle(state, current_tick=1)
        assert state.graph.nodes["c_001"].lifecycle_state == "stale"
        assert state.graph.nodes["c_001"].stale_since_tick == 1

        # Simulate save → load by reconstructing node from dict
        node_dict = state.graph.nodes["c_001"].model_dump()
        reloaded_node = VariableNode.model_validate(node_dict)
        assert reloaded_node.lifecycle_state == "stale"
        assert reloaded_node.stale_since_tick == 1

        # Replace node in graph (simulate full session reload — replace_node
        # 保留 id 不变, 仅替 metadata, 等价于 from_dict 重建后挂回 graph).
        state.graph.replace_node("c_001", reloaded_node)

        # Now run update_lifecycle at tick=1+STALE_TO_DECAYED_TICKS
        # Should decay (because stale_since_tick=1 was preserved through save/load)
        update_lifecycle(state, current_tick=1 + STALE_TO_DECAYED_TICKS)
        assert state.graph.nodes["c_001"].lifecycle_state == "decayed"
