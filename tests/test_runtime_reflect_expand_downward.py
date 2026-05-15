"""Wave 1 Task 1.2: reflect 改用 expand-downward + dispatch 集成测试.

design §4.3: reflect() 在 weak L1 时返 expand-downward.
runtime.run dispatch 加 expand-downward → engines.expand_downward.
anti-thrash 同时数 expand-downward + re-expand.
"""

from datetime import UTC, datetime

from explain_engine.engines import reflection
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState, TraceEntry


def _make_weak_l1_state() -> CognitiveState:
    """1 L1 + 1 L0 with low-conf edge → consistency_score 应该 < 0.5."""
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id="c_001", name="weak_l1", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="phenom", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.1,  # 低 conf → weak chain
        mechanism_description="m",
    ))
    state = CognitiveState(
        graph=g, budget_remaining=10, root_question="why",
        insight_candidates=["c_001"],
    )
    return state


class TestReflectExpandDownward:
    def test_reflect_weak_l1_returns_expand_downward(self) -> None:
        """Wave 1: 改前返 ('re-expand', c_001), 改后返 ('expand-downward', c_001)."""
        state = _make_weak_l1_state()
        action, target = reflection.reflect(state)
        assert action == "expand-downward"
        assert target == "c_001"

    def test_reflect_returns_continue_when_no_weak_l1(self) -> None:
        """高 conf chain → no weak L1 → continue/stop."""
        g = ExplanationGraph(root_question="why")
        g.add_node(VariableNode(
            id="c_001", name="strong_l1", description="d",
            abstraction_level=1, confidence=0.9, epistemic="insight",
        ))
        g.add_node(VariableNode(
            id="p_001", name="phenom", description="d",
            abstraction_level=0, confidence=0.9, epistemic="observation",
        ))
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.9,
            mechanism_description="m",
        ))
        state = CognitiveState(
            graph=g, budget_remaining=10, root_question="why",
            insight_candidates=["c_001"],
        )
        action, _ = reflection.reflect(state)
        assert action in ("continue", "stop")


class TestAntiThrash:
    def test_anti_thrash_counts_expand_downward(self) -> None:
        """LOOKBACK=5 内同 target expand-downward >= 2 次 → exhausted."""
        state = _make_weak_l1_state()
        ts = datetime.now(UTC).isoformat()
        state.reasoning_trace = [
            TraceEntry(tick=0, action="reflect", target_node_id="c_001",
                       gain_delta=0.0, llm_calls=1, timestamp=ts,
                       reflection_action="expand-downward"),
            TraceEntry(tick=1, action="reflect", target_node_id="c_001",
                       gain_delta=0.0, llm_calls=1, timestamp=ts,
                       reflection_action="expand-downward"),
        ]
        exhausted = reflection._exhausted_expansion_targets(state)
        assert "c_001" in exhausted

    def test_anti_thrash_counts_re_expand_too_for_backward_compat(self) -> None:
        """Backward compat: 老 trace 用 re-expand action 也算入 anti-thrash."""
        state = _make_weak_l1_state()
        ts = datetime.now(UTC).isoformat()
        state.reasoning_trace = [
            TraceEntry(tick=0, action="reflect", target_node_id="c_001",
                       gain_delta=0.0, llm_calls=1, timestamp=ts,
                       reflection_action="re-expand"),
            TraceEntry(tick=1, action="reflect", target_node_id="c_001",
                       gain_delta=0.0, llm_calls=1, timestamp=ts,
                       reflection_action="expand-downward"),
        ]
        exhausted = reflection._exhausted_expansion_targets(state)
        assert "c_001" in exhausted
