"""Wave C.1: Reflection Engine.

design §6.2. 0 LLM call (用 Phase 6 simulation).
决策优先级: re-expand > prune > stop > continue.
"""

from __future__ import annotations

import logging

from explain_engine.engines.simulation import check_consistency_batch
from explain_engine.schema.state import CognitiveState, ReflectionAction

logger = logging.getLogger(__name__)

# ─── 常量 (Wave D acceptance 后 tune) ────────────────────────
LOW_CONSISTENCY_THRESHOLD: float = 0.5
"""L1 consistency_score < 阈值 → re-expand."""

LOW_ESSENTIALNESS_THRESHOLD: float = 0.05
"""L2 essentialness_score < 阈值 → prune."""

CONSISTENCY_STALE_TICKS: int = 3
"""state.tick - last_reflection_change_tick >= 此值 → stop."""


def reflect(state: CognitiveState) -> tuple[ReflectionAction, str | None]:
    """Reflection decision. 0 LLM call.

    Returns: (action, target_id)
      - re-expand → target_id = lowest-consistency L1 id
      - prune → target_id = lowest-essentialness L2 id
      - stop → target_id = None
      - continue → target_id = None
    """
    if not state.graph.nodes:
        return ("continue", None)

    L1_L2 = [
        nid for nid, n in state.graph.nodes.items() if n.abstraction_level >= 1
    ]
    if not L1_L2:
        return ("continue", None)

    reports = check_consistency_batch(state)

    # 1. re-expand 低 consistency L1
    low_c = sorted(
        [r for r in reports
         if state.graph.nodes[r.target_id].abstraction_level == 1
         and r.consistency_score < LOW_CONSISTENCY_THRESHOLD],
        key=lambda r: r.consistency_score,
    )
    if low_c:
        return ("re-expand", low_c[0].target_id)

    # 2. prune 低 essentialness L2
    low_e = sorted(
        [r for r in reports
         if state.graph.nodes[r.target_id].abstraction_level == 2
         and r.essentialness_score < LOW_ESSENTIALNESS_THRESHOLD],
        key=lambda r: r.essentialness_score,
    )
    if low_e:
        return ("prune", low_e[0].target_id)

    # 3. stale 检测
    if state.tick - state.last_reflection_change_tick >= CONSISTENCY_STALE_TICKS:
        return ("stop", None)

    return ("continue", None)
