"""Phase 5 stop signals + GAIN_THRESHOLD。

3 个 signal,按优先级检查:
  1. budget_exhausted: budget_remaining <= 0
  2. no_gain_for_3_ticks: tick - last_gain_tick >= 3
  3. no_frontier_remaining: graph.frontier_nodes() == []
"""

from explain_engine.schema.state import CognitiveState

GAIN_THRESHOLD: float = 0.1
"""Phase 5 阈值: expansion_gain >= 0.1 (plausibility >= 0.5/5) 算"有 gain"。

Phase 5 跑 ≥1 真实 session 后 tune。
"""


def should_stop(state: CognitiveState) -> tuple[bool, str | None]:
    if state.budget_remaining <= 0:
        return True, "budget_exhausted"
    if state.tick - state.last_gain_tick >= 3:
        return True, "no_gain_for_3_ticks"
    if not state.graph.frontier_nodes():
        return True, "no_frontier_remaining"
    return False, None
