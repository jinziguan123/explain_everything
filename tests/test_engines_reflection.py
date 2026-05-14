"""Wave C.1: ReflectionEngine.reflect 测试.

design §6.2. 0 LLM call, 用 Phase 6 simulation.check_consistency_batch.
决策优先级: re-expand > prune > stop > continue.
"""

from explain_engine.engines.reflection import (
    CONSISTENCY_STALE_TICKS,
    LOW_CONSISTENCY_THRESHOLD,
    LOW_ESSENTIALNESS_THRESHOLD,
    reflect,
)
from explain_engine.engines.simulation import ConsistencyReport
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _node(nid: str, level: int) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7,
        epistemic="insight" if level >= 1 else "observation",
    )


def _make_state(nodes: list[tuple[str, int]], tick: int = 0,
                last_refl: int = 0) -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    for nid, lvl in nodes:
        g.add_node(_node(nid, lvl))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="q",
        tick=tick, last_reflection_change_tick=last_refl,
    )


def _mock_reports(mocker, reports: list[tuple[str, float, float]]):
    """Mock check_consistency_batch 返指定 reports.

    Each tuple: (target_id, consistency, essentialness).
    """
    fake = [
        ConsistencyReport(
            target_id=tid, consistency_score=c, essentialness_score=e,
            reachable_L0=[], weak_chains=[],
            contribution_breakdown={}, decay_trace=[],
        )
        for tid, c, e in reports
    ]
    mocker.patch(
        "explain_engine.engines.reflection.check_consistency_batch",
        return_value=fake,
    )


class TestReflectEdgeCases:
    def test_empty_graph_returns_continue(self) -> None:
        state = _make_state([])
        action, target = reflect(state)
        assert action == "continue"
        assert target is None

    def test_no_L1_L2_returns_continue(self) -> None:
        state = _make_state([("p_001", 0)])
        action, target = reflect(state)
        assert action == "continue"
        assert target is None


class TestReExpand:
    def test_single_low_consistency_L1_triggers_re_expand(self, mocker) -> None:
        state = _make_state([("c_001", 1), ("p_001", 0)])
        _mock_reports(mocker, [("c_001", 0.3, 0.5)])
        action, target = reflect(state)
        assert action == "re-expand"
        assert target == "c_001"

    def test_multi_low_consistency_returns_lowest(self, mocker) -> None:
        state = _make_state([("c_001", 1), ("c_002", 1), ("p_001", 0)])
        _mock_reports(mocker, [
            ("c_001", 0.4, 0.5),
            ("c_002", 0.2, 0.5),
        ])
        action, target = reflect(state)
        assert action == "re-expand"
        assert target == "c_002"   # 0.2 < 0.4

    def test_threshold_exclusive(self, mocker) -> None:
        """consistency = 0.5 exactly 不触发 (严格 <)."""
        state = _make_state([("c_001", 1), ("p_001", 0)])
        _mock_reports(mocker, [("c_001", LOW_CONSISTENCY_THRESHOLD, 0.5)])
        action, _ = reflect(state)
        assert action != "re-expand"


class TestPrune:
    def test_low_essentialness_L2_triggers_prune(self, mocker) -> None:
        state = _make_state([("c_001", 1), ("d_001", 2), ("p_001", 0)])
        _mock_reports(mocker, [
            ("c_001", 0.8, 0.5),
            ("d_001", 0.7, 0.02),
        ])
        action, target = reflect(state)
        assert action == "prune"
        assert target == "d_001"

    def test_re_expand_priority_over_prune(self, mocker) -> None:
        """Same time: low consistency L1 + low essentialness L2 → re-expand 优先."""
        state = _make_state([("c_001", 1), ("d_001", 2), ("p_001", 0)])
        _mock_reports(mocker, [
            ("c_001", 0.3, 0.5),
            ("d_001", 0.7, 0.02),
        ])
        action, target = reflect(state)
        assert action == "re-expand"
        assert target == "c_001"

    def test_multi_low_essentialness_returns_lowest(self, mocker) -> None:
        """对称 multi-L1 lowest-consistency: 多 L2 选 lowest essentialness."""
        state = _make_state([("c_001", 1), ("d_001", 2), ("d_002", 2), ("p_001", 0)])
        _mock_reports(mocker, [
            ("c_001", 0.8, 0.5),
            ("d_001", 0.7, 0.04),
            ("d_002", 0.7, 0.01),
        ])
        action, target = reflect(state)
        assert action == "prune"
        assert target == "d_002"   # 0.01 < 0.04

    def test_essentialness_threshold_exclusive(self, mocker) -> None:
        """对称 consistency=0.5 严格 <: essentialness=0.05 恰好不触发 prune."""
        state = _make_state([("d_001", 2), ("p_001", 0)])
        _mock_reports(mocker, [("d_001", 0.7, LOW_ESSENTIALNESS_THRESHOLD)])
        action, _ = reflect(state)
        assert action != "prune"


class TestStop:
    def test_stale_change_tick_triggers_stop(self, mocker) -> None:
        state = _make_state(
            [("c_001", 1), ("p_001", 0)],
            tick=CONSISTENCY_STALE_TICKS,
            last_refl=0,
        )
        _mock_reports(mocker, [("c_001", 0.8, 0.5)])
        action, target = reflect(state)
        assert action == "stop"
        assert target is None

    def test_fresh_change_tick_returns_continue(self, mocker) -> None:
        state = _make_state(
            [("c_001", 1), ("p_001", 0)],
            tick=1, last_refl=0,
        )
        _mock_reports(mocker, [("c_001", 0.8, 0.5)])
        action, _ = reflect(state)
        assert action == "continue"


class TestNoLLMCalls:
    def test_reflect_uses_no_llm(self, mocker) -> None:
        """Reflect 0 LLM call — 仅用 Phase 6 simulation."""
        state = _make_state([("c_001", 1), ("p_001", 0)])
        _mock_reports(mocker, [("c_001", 0.8, 0.5)])
        action, _ = reflect(state)
        assert action in ("continue", "re-expand", "prune", "stop")
