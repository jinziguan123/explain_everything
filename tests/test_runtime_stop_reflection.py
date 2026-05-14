"""Wave C.3: stop.py 加 reflection_signaled_stop。

design §6.6: 优先级 budget > no_gain > reflection_stop > no_frontier。
gating 通过 state.last_reflection_change_tick (runtime.py reflect "stop"
分支会把它设为 max(0, tick - CONSISTENCY_STALE_TICKS - 1) 反推触发)。

NOTE: stop.py 不调 check_consistency_batch — 纯 state 检查, 无需 mock。
"""

from explain_engine.runtime.stop import should_stop
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _state(tick: int, last_refl: int = 0,
           budget: int = 10) -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    g.add_node(VariableNode(
        id="c_001", name="c", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    s = CognitiveState(
        graph=g, budget_remaining=budget, root_question="q",
    )
    s.tick = tick
    s.last_gain_tick = tick  # 避免 no_gain_for_3_ticks 干扰
    s.last_reflection_change_tick = last_refl
    return s


class TestReflectionSignaledStop:
    def test_stale_reflection_triggers_stop(self) -> None:
        """tick - last_reflection_change_tick >= CONSISTENCY_STALE_TICKS+1 触发."""
        # diff = 5, CONSISTENCY_STALE_TICKS=3, threshold = 4 → trigger
        state = _state(tick=5, last_refl=0)
        stop, reason = should_stop(state)
        assert stop is True
        assert reason == "reflection_signaled_stop"

    def test_exact_boundary_does_not_trigger(self) -> None:
        """diff == CONSISTENCY_STALE_TICKS (=3) 不触发, 需要 >= +1."""
        state = _state(tick=3, last_refl=0)
        stop, reason = should_stop(state)
        assert reason != "reflection_signaled_stop"
        # diff=3 既不到 reflection 阈值, 也不到 no_gain (last_gain_tick=tick),
        # graph 有 L1 节点不触发 no_frontier → 应继续运行
        assert stop is False

    def test_fresh_reflection_does_not_trigger(self) -> None:
        """新刚发生过 reflection change, 不该 stop."""
        state = _state(tick=2, last_refl=2, budget=5)
        stop, reason = should_stop(state)
        assert reason != "reflection_signaled_stop"
        assert stop is False


class TestStopSignalPriority:
    def test_budget_before_reflection_stop(self) -> None:
        """优先级 budget > reflection_stop."""
        state = _state(tick=10, last_refl=0, budget=0)
        stop, reason = should_stop(state)
        assert stop is True
        assert reason == "budget_exhausted"

    def test_no_gain_before_reflection_stop(self) -> None:
        """优先级 no_gain > reflection_stop."""
        state = _state(tick=10, last_refl=0, budget=10)
        # 强制 no_gain trigger: last_gain_tick=2 → diff=8, 同时 reflection diff=10
        state.last_gain_tick = 2
        stop, reason = should_stop(state)
        assert stop is True
        assert reason == "no_gain_for_3_ticks"

    def test_reflection_stop_before_no_frontier(self) -> None:
        """优先级 reflection_stop > no_frontier。"""
        # graph 全无 L1+ → no_frontier 也会触发, 但 reflection 阈值更高优先。
        g = ExplanationGraph(root_question="q")
        s = CognitiveState(graph=g, budget_remaining=10, root_question="q")
        s.tick = 5
        s.last_gain_tick = 5
        s.last_reflection_change_tick = 0  # diff=5 ≥ 4
        stop, reason = should_stop(s)
        assert stop is True
        assert reason == "reflection_signaled_stop"
