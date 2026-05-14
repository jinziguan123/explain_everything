"""Phase 5/7 stop signals + GAIN_THRESHOLD。

stop signal,按优先级检查:
  1. budget_exhausted: budget_remaining <= 0
  2. no_gain_for_3_ticks: tick - last_gain_tick >= 3
  3. reflection_signaled_stop: Phase 7 Wave C.3 加 — reflection 决定 stop 时
     runtime.py 通过 state.last_reflection_change_tick =
     max(0, tick - CONSISTENCY_STALE_TICKS - 1) 反推, 让 diff >= +1 触发。
  4. no_frontier_remaining: graph 无任何 L1+ 节点 (Phase 7 Wave C.2 改:
     原来是 frontier_nodes() 空就停, 现在因为 reflection 可 re-expand 已 covered
     L1, 只有当 graph 完全没 L1+ 节点时才停。)
"""

from explain_engine.engines.reflection import CONSISTENCY_STALE_TICKS
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
    # Phase 7 Wave C.3: reflection 决定 stop 时, runtime.py 把
    # last_reflection_change_tick 设为 max(0, tick - CONSISTENCY_STALE_TICKS - 1),
    # 让 diff = tick - last_reflection_change_tick >= CONSISTENCY_STALE_TICKS + 1
    # 触发。优先级排 budget/no_gain 后, no_frontier 前。
    if (
        state.tick - state.last_reflection_change_tick
        >= CONSISTENCY_STALE_TICKS + 1
    ):
        return True, "reflection_signaled_stop"
    # Phase 7 Wave C.2: reflection 可对已 covered L1 做 re_expand,
    # 所以仅在 graph 完全没 L1+ 节点时才视为彻底无 frontier。
    has_L1_or_higher = any(
        n.abstraction_level >= 1 for n in state.graph.nodes.values()
    )
    if not has_L1_or_higher:
        return True, "no_frontier_remaining"
    return False, None
