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

RE_EXPAND_THRASH_LIMIT: int = 2
"""Wave C 补丁2: 同 target 连续 re-expand 上限.

防 Wave C 设计 bug 死循环: re_expand 加 incoming causes (driver → L1), 但 L1 的
consistency_score 取决于 outgoing manifests_as (L1 → L0), 改前者不影响后者. 若 reflect
看到 L1 X 低 consistency 触发 re-expand, X 的 consistency 不变, 下次 reflect 又选 X,
死循环.

LIMIT=2: 容许一次重试 (第 1 次 + 第 2 次都跑); 第 3 次 attempt 时 X 视为 exhausted,
fall through to prune/stop/continue.

Phase 8+ 重新设计 reflect/re_expand 语义后可去掉. 当前是 Wave C 落地补丁."""


def _exhausted_re_expand_targets(state: CognitiveState) -> set[str]:
    """找出最近 reasoning_trace 里被连续 re-expand >= RE_EXPAND_THRASH_LIMIT 次的 target.

    Consecutive 语义: 倒序扫 trace, 跳过 expand entries (不属于 reflect 决策),
    遇 reflect 但 action != "re-expand" 或 target != streak_target 则 break.

    Returns: 要从 re-expand 候选中排除的 target_id 集合.
    """
    counts: dict[str, int] = {}
    streak_target: str | None = None

    for entry in reversed(state.reasoning_trace):
        if entry.action != "reflect":
            continue   # expand entries 不影响 streak
        if entry.reflection_action != "re-expand":
            break   # 其他 reflect action (continue/prune/stop) 打断 streak
        target = entry.target_node_id
        if target is None:
            break   # malformed entry
        if streak_target is None:
            streak_target = target
            counts[target] = 1
        elif target == streak_target:
            counts[target] += 1
        else:
            break   # 不同 target, streak 断

    return {t for t, c in counts.items() if c >= RE_EXPAND_THRASH_LIMIT}


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
    # Defensive: 过滤已不在 graph 中的 target (e.g. 上一 tick 刚被 prune 的 node)。
    # 真实 check_consistency_batch 不会返陈旧 target, 但 mock 可能, 避免 KeyError。
    reports = [r for r in reports if r.target_id in state.graph.nodes]

    # 1. re-expand 低 consistency L1
    exhausted = _exhausted_re_expand_targets(state)
    low_c = sorted(
        [r for r in reports
         if state.graph.nodes[r.target_id].abstraction_level == 1
         and r.consistency_score < LOW_CONSISTENCY_THRESHOLD
         and r.target_id not in exhausted],
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
