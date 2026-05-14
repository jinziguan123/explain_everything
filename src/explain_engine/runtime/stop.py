"""Phase 5/7 stop signals + GAIN_THRESHOLD。

stop signal,按优先级检查:
  1. budget_exhausted: budget_remaining <= 0
  2. no_gain_for_3_ticks: tick - last_gain_tick >= 3
  3. no_frontier_remaining: graph 无任何 L1+ 节点 (Phase 7 Wave C.2 改:
     原来是 frontier_nodes() 空就停, 现在因为 reflection 可 re-expand 已 covered
     L1, 只有当 graph 完全没 L1+ 节点时才停。)
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
    # Phase 7 Wave C.2: reflection 可对已 covered L1 做 re_expand,
    # 所以仅在 graph 完全没 L1+ 节点时才视为彻底无 frontier。
    has_L1_or_higher = any(
        n.abstraction_level >= 1 for n in state.graph.nodes.values()
    )
    if not has_L1_or_higher:
        return True, "no_frontier_remaining"
    return False, None
