"""PhaseScheduler — Phase 7 Wave C.2: 1 round = K expand + 1 reflect。

Phase 5 原版: K expand + 1 evaluate。
Phase 7 Wave C 替换 evaluate 为 reflect (调 reflection.reflect 决策
re-expand / prune / stop / continue)。

budget=15, K=4 → 3 round = 12 expand + 3 reflect。
"""

from dataclasses import dataclass
from typing import Literal

from explain_engine.schema.state import CognitiveState

Action = Literal["expand", "reflect"]


@dataclass
class PhaseScheduler:
    K: int = 4

    def pick(self, state: CognitiveState) -> Action:
        """按 (K+1)-modulo 决定下一个 action。

        round 内前 K tick = expand, 第 K+1 tick = reflect。
        compress 在当前 wave 不入 round (新 candidate 推后续 + batch scoring)。
        """
        if state.tick % (self.K + 1) < self.K:
            return "expand"
        return "reflect"
