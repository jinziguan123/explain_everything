"""Wave C.1 + Phase 8 Wave 1: Reflection Engine.

design §6.2 / Phase 8 design §4. 0 LLM call.
决策优先级 (Wave 1 改): expand-downward > prune > stop > continue.

Wave 1 (Phase 8) 用 expand-downward 替换 re-expand 修死循环根因:
re_expand 加 incoming causes (driver → L1) 但 consistency 测 outgoing
manifests_as (L1 → L0). 加 driver 不影响 L1 outgoing edges, 永远改善不了
consistency 分数 → 死循环. expand-downward 直接给 L1 加 manifests_as 子节点,
检验 L1 是不是真机制 (哲学 §8.1 rollout).
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

RE_EXPAND_LOOKBACK_WINDOW: int = 5
"""Wave C 补丁2 v2: 反 thrash 时 reasoning_trace 倒序扫的 reflect-entries 窗口大小."""

RE_EXPAND_THRASH_LIMIT: int = 2
"""Wave C 补丁2: 同 target 在最近 RE_EXPAND_LOOKBACK_WINDOW 个 reflect entries 里
出现次数 >= LIMIT 视为 exhausted, 从 re-expand 候选排除.

v2 改 occurrence-in-window 语义 (v1 用 consecutive, prune 打断 streak 漏 c_003 → re-expand → c_003).
v2 的 forgetting horizon: 5 ticks 不 pick 后, target 重新可选."""


def _exhausted_expansion_targets(state: CognitiveState) -> set[str]:
    """找出最近 RE_EXPAND_LOOKBACK_WINDOW 个 reflect entries 里 expansion 同 target
    出现次数 >= RE_EXPAND_THRASH_LIMIT 的 target id.

    Wave 1 Phase 8 改: 同时数 expand-downward (新) + re-expand (老 trace backward
    compat). Occurrence-in-window 语义不变.

    Occurrence-in-window 语义 (v2):
      - 倒序扫 reasoning_trace, expand entries 跳过 (不算 lookback)
      - 其他 reflect entries (continue/prune/stop/expand-downward/re-expand) 都算 window 大小
      - 但只有 reflection_action ∈ {expand-downward, re-expand} 且 target_node_id 非空才加 count

    v1 的 consecutive 语义 leak: prune 打断 streak 让 count reset, 实跑 LLM 看到
    "re-expand X → re-expand X → prune Y → re-expand X..." 仍 thrash.

    Returns: 要从 expansion 候选中排除的 target_id 集合.
    """
    counts: dict[str, int] = {}
    seen_reflects = 0

    for entry in reversed(state.reasoning_trace):
        if entry.action != "reflect":
            continue   # expand entries 跳过, 不算 window
        seen_reflects += 1
        if seen_reflects > RE_EXPAND_LOOKBACK_WINDOW:
            break
        if (
            entry.reflection_action in ("expand-downward", "re-expand")
            and entry.target_node_id
        ):
            counts[entry.target_node_id] = counts.get(entry.target_node_id, 0) + 1

    return {t for t, c in counts.items() if c >= RE_EXPAND_THRASH_LIMIT}


# 保留旧名作 alias (没人调外部, 但保险)
_exhausted_re_expand_targets = _exhausted_expansion_targets


def reflect(state: CognitiveState) -> tuple[ReflectionAction, str | None]:
    """Reflection decision. 0 LLM call.

    Returns: (action, target_id)
      - expand-downward → target_id = lowest-consistency L1 id (Wave 1 Phase 8: 替原 re-expand)
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

    # 1. expand-downward 低 consistency L1
    exhausted = _exhausted_expansion_targets(state)
    low_c = sorted(
        [r for r in reports
         if state.graph.nodes[r.target_id].abstraction_level == 1
         and r.consistency_score < LOW_CONSISTENCY_THRESHOLD
         and r.target_id not in exhausted],
        key=lambda r: r.consistency_score,
    )
    # Wave 1 Phase 8 fix: 用 expand-downward 替原 re-expand. 见 module docstring.
    if low_c:
        return ("expand-downward", low_c[0].target_id)

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
