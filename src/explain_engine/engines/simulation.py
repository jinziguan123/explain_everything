"""Phase 6 SimulationEngine — Consistency Check (C₁ + C₂).

参考 docs/plans/2026-05-14-cognitive-engine-phase-6-design.md §4.

API:
  check_consistency(state, target_id) → ConsistencyReport
  check_consistency_batch(state, target_ids?) → list[ConsistencyReport]

Pure rule-based, 0 LLM call. L0 不可 check (是 ground truth).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from explain_engine.engines._propagation import (
    WEAK_CHAIN_THRESHOLD,
    DecayStep,
    get_all_L0,
    get_all_L1_L2,
    propagate,
    rollout_from_roots,  # NEW Wave 2.1
)
from explain_engine.schema.state import CognitiveState


@dataclass(frozen=True)
class ConsistencyReport:
    """单个 target 的 consistency check 结果。

    Note: frozen=True 是浅冻结 — 只防字段 rebind, 不防 reachable_L0.append()
    或 contribution_breakdown[k]=v 这种 inner mutable container mutate.
    依赖 downstream (CLI render 等) 自律 read-only. Phase 7+ 真要严格 immutable
    再换 tuple + MappingProxyType.

    单 target 报告; 全 graph 聚合见 AcceptanceReport.
    """

    target_id: str
    consistency_score: float
    reachable_L0: list[str]
    weak_chains: list[str]
    essentialness_score: float
    contribution_breakdown: dict[str, float]
    decay_trace: list[DecayStep]


def _validate_target(state: CognitiveState, target_id: str) -> None:
    if target_id not in state.graph.nodes:
        raise ValueError(f"target {target_id!r} not found in graph")
    level = state.graph.nodes[target_id].abstraction_level
    if level == 0:
        raise ValueError(
            f"target {target_id!r} has level=0 (concrete), "
            f"only L1/L2 can be consistency-checked "
            f"(L0 is ground truth, not subject to verification)"
        )


def _check_with_baseline(
    state: CognitiveState,
    target_id: str,
    baseline_acts: dict[str, float] | None,
) -> ConsistencyReport:
    """单 target 的 C₁ + C₂. baseline 可传入 (batch 共用)."""
    graph = state.graph
    L0_nodes = get_all_L0(graph)
    all_L1_L2 = get_all_L1_L2(graph)

    # ─── C₁: single-source propagation ─────────────
    c1_acts, c1_trace = propagate(graph, {target_id})
    reachable_L0 = sorted(nid for nid in L0_nodes if c1_acts.get(nid, 0.0) > 0)
    if reachable_L0:
        consistency_score = sum(c1_acts[nid] for nid in reachable_L0) / len(reachable_L0)
    else:
        consistency_score = 0.0
    weak_chains = sorted(
        nid for nid in reachable_L0 if c1_acts[nid] < WEAK_CHAIN_THRESHOLD
    )

    # ─── C₂: counterfactual ────────────────────────
    if baseline_acts is None:
        # 极简 case: all_L1_L2 == {target_id}, baseline == c1_acts (避免重算)
        if all_L1_L2 == {target_id}:
            baseline_acts = c1_acts
        else:
            baseline_acts, _ = propagate(graph, all_L1_L2)
    without_acts, _ = propagate(graph, all_L1_L2 - {target_id})
    # Clip to [0, +∞): MAX_ACTIVE_VARIABLES top-k pruning 在 baseline 触发但
    # without_target 不触发时, 可能让无关 L0 在 without 中"复活", 产生
    # contribution < 0 (反向贡献). 语义上 essentialness 只衡量正向 marginal
    # contribution — 删 target 引起的 top-k reshuffle 是 algorithm artifact,
    # 不是 target 的真实"贡献", 应 clip.
    contribution = {
        nid: max(0.0, baseline_acts.get(nid, 0.0) - without_acts.get(nid, 0.0))
        for nid in L0_nodes
    }
    essentialness_score = (
        sum(contribution.values()) / len(L0_nodes) if L0_nodes else 0.0
    )

    return ConsistencyReport(
        target_id=target_id,
        consistency_score=consistency_score,
        reachable_L0=reachable_L0,
        weak_chains=weak_chains,
        essentialness_score=essentialness_score,
        contribution_breakdown=contribution,
        decay_trace=c1_trace,
    )


def check_consistency(state: CognitiveState, target_id: str) -> ConsistencyReport:
    """对单个 target (L1 abstract 或 L2 driver) 跑 C₁ + C₂.

    Raises:
        ValueError: target_id 不在 graph / level=0
    """
    _validate_target(state, target_id)
    return _check_with_baseline(state, target_id, baseline_acts=None)


def check_consistency_batch(
    state: CognitiveState,
    target_ids: Iterable[str] | None = None,
) -> list[ConsistencyReport]:
    """Batch check, baseline propagation 共用.

    Args:
        target_ids: None = 全 graph 所有 L1+L2 (按 id 升序); list = 指定.

    Raises:
        ValueError: 任一 target 不在 graph / level=0 (fail-fast).
    """
    if target_ids is None:
        target_id_list = sorted(get_all_L1_L2(state.graph))
    else:
        target_id_list = list(target_ids)

    if not target_id_list:
        return []

    for tid in target_id_list:
        _validate_target(state, tid)

    all_L1_L2 = get_all_L1_L2(state.graph)
    baseline_acts, _ = propagate(state.graph, all_L1_L2)

    return [
        _check_with_baseline(state, tid, baseline_acts)
        for tid in target_id_list
    ]


@dataclass(frozen=True)
class AcceptanceReport:
    """Wave 2 Task 2.2: aggregate multi-signal acceptance report.

    与 per-target ConsistencyReport 不同, 这是全 graph 的聚合报告.
    给 reflect / CLI / acceptance verdict 用.

    哲学锚点:
      - §11.3 "最低 entropy 下的最大解释力" → consistency_spread / essentialness_spread
      - §8.1 "Explanation 必须能 rollout" → rollout_coverage
      - §9.4 可证伪性 → input_alignment / falsifiable_reason (Wave 3 注入)

    Note: weak_chain_l1s 与 ConsistencyReport.weak_chains 含义不同:
      - ConsistencyReport.weak_chains: 单 target 内 reachable_L0 中 activation 弱的 L0
      - AcceptanceReport.weak_chain_l1s: 全 graph 中 consistency_score 低的 L1
    """

    # 主信号 (聚合)
    avg_consistency: float
    avg_essentialness: float

    # per-node 明细
    per_l1: dict[str, float] = field(default_factory=dict)
    per_l2: dict[str, float] = field(default_factory=dict)

    # ── Wave 2 multi-signal (6 字段) ──
    weak_chain_l1s: list[str] = field(default_factory=list)
    """consistency < LOW_CONSISTENCY_THRESHOLD 的 L1 id 列表 (按 score 升序)."""

    lowest_l1: tuple[str, float] | None = None
    """argmin(per_l1) 的 (id, score). 空 graph 返 None."""

    consistency_spread: float = 0.0
    """max(per_l1) - min(per_l1)."""

    essentialness_spread: float = 0.0
    """max(per_l2) - min(per_l2)."""

    rollout_coverage: float = 1.0
    """从 L2 root rollout 触达的 L0 比例. 无 L0 时 trivially 1.0."""

    missing_l0: list[str] = field(default_factory=list)
    """rollout 没触达的 L0 id 列表 (升序)."""

    # ── Wave 3 alignment 字段 (留位, Wave 3 注入) ──
    input_alignment: float | None = None
    """Wave 3 input_validation overlap_score / 5.0. None = 没跑过校验."""

    falsifiable_reason: str | None = None
    """Wave 3 LLM 给的对齐失败理由."""


def aggregate_acceptance(state: CognitiveState) -> AcceptanceReport:
    """Wave 2 Task 2.2: 全 graph 聚合 multi-signal report.

    流程:
      1. rollout_from_roots → rollout_coverage / missing_l0 (无论 L1+L2 是否存在)
      2. check_consistency_batch (现有 Phase 6) → list[ConsistencyReport] per-target
      3. 拆 per_l1 (consistency_score) / per_l2 (essentialness_score) dict
      4. 算 avg / spread / weak_chains_l1s / lowest_l1

    不调 LLM. 只读, 不改 state.
    """
    # Import 在函数体内避免 circular: reflection.py imports from simulation.py.
    # TODO(Phase 8 housekeeping): extract LOW_CONSISTENCY_THRESHOLD to
    # engines/_thresholds.py, 让 simulation 和 reflection 都 top-level import,
    # 移除此 workaround.
    from explain_engine.engines.reflection import LOW_CONSISTENCY_THRESHOLD

    # ── Wave 2 rollout coverage (无论是否有 L1+L2 都算, 防 only-L0 graph 报错默认) ──
    reachable, missing = rollout_from_roots(state.graph)
    all_l0 = {nid for nid, n in state.graph.nodes.items() if n.abstraction_level == 0}
    rollout_coverage = (len(reachable) / len(all_l0)) if all_l0 else 1.0

    # ── Wave 3 Task 3.2: 注入 alignment 字段 (如果 input_validation 跑过) ──
    # state.last_input_alignment_report 由 CLI explain run 入口 tick=0 时写入.
    # 没跑过 (旧 session / --no-input-check / tick > 0 resume) → None, 不影响 report.
    input_alignment: float | None = None
    falsifiable_reason: str | None = None
    align_report = getattr(state, "last_input_alignment_report", None)
    if align_report is not None:
        input_alignment = align_report.overlap_score / 5.0
        falsifiable_reason = align_report.falsifiable_reason

    L1_L2 = sorted(get_all_L1_L2(state.graph))
    if not L1_L2:
        # 无 L1+L2: 没有 consistency 可算, 但 rollout 仍可信. only-L0 graph
        # 此时 rollout_coverage = 0 (L0 都触达不到), missing_l0 = all_l0.
        return AcceptanceReport(
            avg_consistency=0.0,
            avg_essentialness=0.0,
            rollout_coverage=rollout_coverage,
            missing_l0=sorted(missing),
            input_alignment=input_alignment,
            falsifiable_reason=falsifiable_reason,
        )

    reports = check_consistency_batch(state)
    per_l1: dict[str, float] = {}
    per_l2: dict[str, float] = {}
    for r in reports:
        node = state.graph.nodes.get(r.target_id)
        if node is None:
            continue
        if node.abstraction_level == 1:
            per_l1[r.target_id] = r.consistency_score
        elif node.abstraction_level == 2:
            per_l2[r.target_id] = r.essentialness_score

    avg_consistency = sum(per_l1.values()) / len(per_l1) if per_l1 else 0.0
    avg_essentialness = sum(per_l2.values()) / len(per_l2) if per_l2 else 0.0

    # Wave 2 multi-signal aggregations
    weak_chain_l1s = sorted(
        [l1 for l1, s in per_l1.items() if s < LOW_CONSISTENCY_THRESHOLD],
        key=lambda l1: per_l1[l1],
    )
    lowest_l1 = min(per_l1.items(), key=lambda kv: kv[1]) if per_l1 else None
    consistency_spread = (
        (max(per_l1.values()) - min(per_l1.values())) if per_l1 else 0.0
    )
    essentialness_spread = (
        (max(per_l2.values()) - min(per_l2.values())) if per_l2 else 0.0
    )

    return AcceptanceReport(
        avg_consistency=avg_consistency,
        avg_essentialness=avg_essentialness,
        per_l1=per_l1,
        per_l2=per_l2,
        weak_chain_l1s=weak_chain_l1s,
        lowest_l1=lowest_l1,
        consistency_spread=consistency_spread,
        essentialness_spread=essentialness_spread,
        rollout_coverage=rollout_coverage,
        missing_l0=sorted(missing),
        input_alignment=input_alignment,
        falsifiable_reason=falsifiable_reason,
    )
