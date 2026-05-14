"""Wave C.2: PhaseScheduler 改 reflect."""

from explain_engine.runtime.scheduler import PhaseScheduler
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState


def _state(tick: int) -> CognitiveState:
    return CognitiveState(
        graph=ExplanationGraph(root_question="q"),
        budget_remaining=100, root_question="q",
        tick=tick,
    )


class TestPhaseSchedulerReflect:
    def test_K4_tick_0_to_3_expand(self) -> None:
        sched = PhaseScheduler(K=4)
        for t in range(4):
            assert sched.pick(_state(t)) == "expand"

    def test_K4_tick_4_reflect(self) -> None:
        sched = PhaseScheduler(K=4)
        assert sched.pick(_state(4)) == "reflect"

    def test_K4_tick_5_to_8_expand_again(self) -> None:
        sched = PhaseScheduler(K=4)
        for t in range(5, 9):
            assert sched.pick(_state(t)) == "expand"

    def test_K4_tick_9_reflect_again(self) -> None:
        sched = PhaseScheduler(K=4)
        assert sched.pick(_state(9)) == "reflect"

    def test_K2_alternation(self) -> None:
        sched = PhaseScheduler(K=2)
        assert sched.pick(_state(0)) == "expand"
        assert sched.pick(_state(1)) == "expand"
        assert sched.pick(_state(2)) == "reflect"
