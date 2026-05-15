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

from explain_engine.engines.lifecycle import (
    DECAY_THRESHOLD,
    compute_fitness,
)
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


def pick_decay_target(state: CognitiveState) -> str | None:
    """Wave 4 Task 4.2: 选 fitness 最低且 < DECAY_THRESHOLD 的非 decayed L1+L2 节点.

    L0 nodes 跳过 (Task 4.1 M4 contract: L0 是 ground truth).
    """
    if not state.graph.nodes:
        return None

    candidates: list[tuple[str, float]] = []
    for nid, node in state.graph.nodes.items():
        if node.abstraction_level == 0:
            continue  # L0 跳过
        if node.lifecycle_state == "decayed":
            continue
        f = compute_fitness(node, state)
        if f < DECAY_THRESHOLD:
            candidates.append((nid, f))

    if not candidates:
        return None
    return min(candidates, key=lambda kv: kv[1])[0]


def reflect(state: CognitiveState) -> tuple[ReflectionAction, str | None]:
    """Reflection decision (Wave 1 + Wave 2 + Wave 4 重写). 0 LLM call.

    Wave 2 Task 2.3 改: 优先用 cached state.last_acceptance_report 的
    weak_chain_l1s + per_l2 (production runtime 在 reflect tick 前必刷新,
    见 runtime.run). Fallback 当场调 aggregate_acceptance — 仅供 unit test
    直接调 reflect() 跳过 runtime orchestration; production 路径不会触发.

    Wave 4 Task 4.2 加 decay 分支:
      决策优先级: expand-downward > decay > prune > stop > continue.
      expand-downward + prune 都跳过 lifecycle_state == "decayed" 的节点.

    Returns: (action, target_id)
      - expand-downward → target_id = lowest-consistency L1 id
      - decay           → target_id = lowest-fitness L1/L2 id (新 Wave 4)
      - prune           → target_id = lowest-essentialness L2 id
      - stop            → target_id = None
      - continue        → target_id = None
    """
    if not state.graph.nodes:
        return ("continue", None)

    L1_L2 = [
        nid for nid, n in state.graph.nodes.items() if n.abstraction_level >= 1
    ]
    if not L1_L2:
        return ("continue", None)

    # Wave 2 Task 2.3: 优先 cached report; fallback 当场聚合 (老 caller / test).
    # Local import 避免 reflection ↔ simulation 循环 (simulation.aggregate_acceptance
    # 已 import LOW_CONSISTENCY_THRESHOLD).
    report = state.last_acceptance_report
    if report is None:
        from explain_engine.engines.simulation import aggregate_acceptance
        report = aggregate_acceptance(state)

    exhausted = _exhausted_expansion_targets(state)

    # 1. expand-downward 弱 L1 (用 cached weak_chain_l1s, 已按 score 升序).
    for l1_id in report.weak_chain_l1s:
        if l1_id in exhausted:
            continue
        if l1_id not in state.graph.nodes:
            continue   # defensive: 上一 tick prune 后 cached report 可能含陈旧 id
        node = state.graph.nodes[l1_id]
        if node.lifecycle_state == "decayed":
            continue   # Wave 4: 跳过 decayed
        # Wave 1 Phase 8 fix: expand-downward 替原 re-expand. 见 module docstring.
        return ("expand-downward", l1_id)

    # 2. decay 极低 fitness L1/L2 (Wave 4 NEW).
    decay_target = pick_decay_target(state)
    if decay_target is not None:
        return ("decay", decay_target)

    # 3. prune 低 essentialness L2 (用 report.per_l2; Wave 4: 跳过 decayed).
    low_l2 = sorted(
        [(l2, score) for l2, score in report.per_l2.items()
         if score < LOW_ESSENTIALNESS_THRESHOLD
         and l2 in state.graph.nodes
         and state.graph.nodes[l2].lifecycle_state != "decayed"],
        key=lambda kv: kv[1],
    )
    if low_l2:
        return ("prune", low_l2[0][0])

    # 4. stale 检测 (不变).
    if state.tick - state.last_reflection_change_tick >= CONSISTENCY_STALE_TICKS:
        return ("stop", None)

    return ("continue", None)
