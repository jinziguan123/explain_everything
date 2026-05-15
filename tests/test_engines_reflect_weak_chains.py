"""Wave 2 Task 2.3: reflect 改用 AcceptanceReport.weak_chain_l1s.

design §5.4: 用 weak_chain_l1s 列表替临时 sorted+filter 构造.
"""

from datetime import UTC, datetime

from explain_engine.engines import reflection
from explain_engine.engines.simulation import AcceptanceReport
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState, TraceEntry


def _make_state_with_report(per_l1: dict[str, float],
                             weak_chain_l1s: list[str]) -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    for l1_id in per_l1:
        g.add_node(VariableNode(
            id=l1_id, name=l1_id, description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
    g.add_node(VariableNode(
        id="p_001", name="p", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    for i, l1_id in enumerate(per_l1, 1):
        g.add_edge(RelationEdge(
            id=f"e_{i:03d}", source_node=l1_id, target_node="p_001",
            relation_type="manifests_as", confidence=0.5,
            mechanism_description="m",
        ))

    state = CognitiveState(
        graph=g, budget_remaining=10, root_question="q",
    )
    avg = sum(per_l1.values()) / len(per_l1) if per_l1 else 0.0
    state.last_acceptance_report = AcceptanceReport(
        avg_consistency=avg,
        avg_essentialness=0.5,
        per_l1=per_l1,
        weak_chain_l1s=weak_chain_l1s,
    )
    return state


class TestReflectWeakChainsList:
    def test_picks_first_unexhausted_from_weak_chain_l1s(self) -> None:
        state = _make_state_with_report(
            per_l1={"c_001": 0.2, "c_002": 0.3, "c_003": 0.7},
            weak_chain_l1s=["c_001", "c_002"],  # 升序
        )
        action, target = reflection.reflect(state)
        assert action == "expand-downward"
        assert target == "c_001"

    def test_skips_exhausted_l1(self) -> None:
        state = _make_state_with_report(
            per_l1={"c_001": 0.2, "c_002": 0.3},
            weak_chain_l1s=["c_001", "c_002"],
        )
        ts = datetime.now(UTC).isoformat()
        # c_001 exhausted (出现 2 次)
        state.reasoning_trace = [
            TraceEntry(tick=0, action="reflect", target_node_id="c_001",
                       gain_delta=0.0, llm_calls=1, timestamp=ts,
                       reflection_action="expand-downward"),
            TraceEntry(tick=1, action="reflect", target_node_id="c_001",
                       gain_delta=0.0, llm_calls=1, timestamp=ts,
                       reflection_action="expand-downward"),
        ]
        action, target = reflection.reflect(state)
        assert action == "expand-downward"
        assert target == "c_002"  # 跳过 c_001

    def test_no_weak_chains_falls_through(self) -> None:
        state = _make_state_with_report(
            per_l1={"c_001": 0.8},
            weak_chain_l1s=[],
        )
        action, _ = reflection.reflect(state)
        assert action != "expand-downward"

    def test_uses_cached_report_when_present(self) -> None:
        """有 last_acceptance_report → 直接用 weak_chain_l1s, 不重算 simulation."""
        state = _make_state_with_report(
            per_l1={"c_001": 0.2}, weak_chain_l1s=["c_001"],
        )
        action, target = reflection.reflect(state)
        assert (action, target) == ("expand-downward", "c_001")

    def test_falls_back_to_fresh_aggregate_when_no_cached_report(self) -> None:
        """state.last_acceptance_report = None → reflect 当场算."""
        g = ExplanationGraph(root_question="q")
        g.add_node(VariableNode(
            id="c_001", name="weak", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
        g.add_node(VariableNode(
            id="p_001", name="p", description="d",
            abstraction_level=0, confidence=0.7, epistemic="observation",
        ))
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.1,  # 弱
            mechanism_description="m",
        ))
        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
        # state.last_acceptance_report 默认 None
        action, target = reflection.reflect(state)
        # 当场聚合应仍发现 weak L1
        assert action == "expand-downward"
        assert target == "c_001"
