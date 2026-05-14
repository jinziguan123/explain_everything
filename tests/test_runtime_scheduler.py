"""PhaseScheduler — Phase 5 phase-based scheduler。

1 round = K expand + 1 evaluate (K+1 tick)。
"""

from explain_engine.runtime.scheduler import PhaseScheduler
from explain_engine.schema.state import CognitiveState


def _state(tick: int) -> CognitiveState:
    s = CognitiveState.bootstrap("why", budget=10)
    s.tick = tick
    return s


class TestPhaseScheduler:
    def test_k4_round_layout(self) -> None:
        """K=4: tick 0-3 = expand, tick 4 = evaluate, tick 5-8 = expand, tick 9 = evaluate, ..."""
        sched = PhaseScheduler(K=4)
        assert sched.pick(_state(0)) == "expand"
        assert sched.pick(_state(1)) == "expand"
        assert sched.pick(_state(2)) == "expand"
        assert sched.pick(_state(3)) == "expand"
        assert sched.pick(_state(4)) == "evaluate"
        assert sched.pick(_state(5)) == "expand"
        assert sched.pick(_state(9)) == "evaluate"
        assert sched.pick(_state(10)) == "expand"

    def test_k3_round_layout(self) -> None:
        sched = PhaseScheduler(K=3)
        assert sched.pick(_state(0)) == "expand"
        assert sched.pick(_state(2)) == "expand"
        assert sched.pick(_state(3)) == "evaluate"
        assert sched.pick(_state(4)) == "expand"

    def test_k5_round_layout(self) -> None:
        sched = PhaseScheduler(K=5)
        assert sched.pick(_state(4)) == "expand"
        assert sched.pick(_state(5)) == "evaluate"

    def test_default_k_is_4(self) -> None:
        sched = PhaseScheduler()
        assert sched.K == 4
