"""should_stop — Phase 5 3 个 stop signal:

  - budget_exhausted
  - no_gain_for_3_ticks
  - no_frontier_remaining
"""

from explain_engine.runtime.stop import GAIN_THRESHOLD, should_stop
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _state_with_frontier(
    tick: int, budget: int, last_gain_tick: int,
    last_refl: int | None = None,
) -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(id="c_001", name="x", description="d", abstraction_level=1,
                            confidence=0.7, epistemic="insight"))
    s = CognitiveState(graph=g, budget_remaining=budget, root_question="why")
    s.tick = tick
    s.last_gain_tick = last_gain_tick
    # Wave C.3 后: last_reflection_change_tick 默认对齐 tick 以避免误触发
    # reflection_signaled_stop (CONSISTENCY_STALE_TICKS+1=4 阈值)。
    s.last_reflection_change_tick = tick if last_refl is None else last_refl
    return s


def _state_no_frontier(
    tick: int, budget: int, last_gain_tick: int,
    last_refl: int | None = None,
) -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    # 无 level==1 节点 → 无 frontier
    s = CognitiveState(graph=g, budget_remaining=budget, root_question="why")
    s.tick = tick
    s.last_gain_tick = last_gain_tick
    s.last_reflection_change_tick = tick if last_refl is None else last_refl
    return s


class TestShouldStop:
    def test_budget_exhausted(self) -> None:
        s = _state_with_frontier(tick=5, budget=0, last_gain_tick=4)
        stop, reason = should_stop(s)
        assert stop is True
        assert reason == "budget_exhausted"

    def test_no_gain_for_3_ticks(self) -> None:
        s = _state_with_frontier(tick=5, budget=10, last_gain_tick=2)
        # tick - last_gain_tick = 3 → trigger
        stop, reason = should_stop(s)
        assert stop is True
        assert reason == "no_gain_for_3_ticks"

    def test_no_gain_2_ticks_no_stop(self) -> None:
        s = _state_with_frontier(tick=4, budget=10, last_gain_tick=2)
        # diff = 2 → no stop
        stop, reason = should_stop(s)
        assert stop is False
        assert reason is None

    def test_no_frontier_remaining(self) -> None:
        s = _state_no_frontier(tick=3, budget=10, last_gain_tick=3)
        stop, reason = should_stop(s)
        assert stop is True
        assert reason == "no_frontier_remaining"

    def test_budget_first_priority(self) -> None:
        """多 signal 同时触发,budget_exhausted 优先返回。"""
        s = _state_no_frontier(tick=10, budget=0, last_gain_tick=2)
        _stop, reason = should_stop(s)
        assert reason == "budget_exhausted"

    def test_no_gain_before_no_frontier(self) -> None:
        s = _state_no_frontier(tick=5, budget=10, last_gain_tick=2)
        _stop, reason = should_stop(s)
        # budget 没空, no_gain 先于 no_frontier 检查
        assert reason == "no_gain_for_3_ticks"

    def test_running_no_stop(self) -> None:
        s = _state_with_frontier(tick=1, budget=10, last_gain_tick=0)
        stop, reason = should_stop(s)
        assert stop is False
        assert reason is None

    def test_gain_threshold_exists(self) -> None:
        assert isinstance(GAIN_THRESHOLD, float)
        assert 0.0 < GAIN_THRESHOLD < 1.0
