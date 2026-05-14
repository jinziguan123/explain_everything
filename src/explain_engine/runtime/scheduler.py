"""PhaseScheduler — Phase 5 phase-based scheduler。

1 round = K expand + 1 evaluate = (K+1) tick。
budget=15, K=4 → 3 round = 12 expand + 3 evaluate。
"""

from dataclasses import dataclass
from typing import Literal

from explain_engine.schema.state import CognitiveState

Action = Literal["expand", "evaluate"]


@dataclass
class PhaseScheduler:
    K: int = 4

    def pick(self, state: CognitiveState) -> Action:
        """按 (K+1)-modulo 决定下一个 action。

        Phase 5 不出新 compression candidate,所以 round 内只有 expand / evaluate。
        compress 在 Phase 5 不入 round (新 candidate 推 Phase 6 + batch scoring)。
        """
        if state.tick % (self.K + 1) < self.K:
            return "expand"
        return "evaluate"
