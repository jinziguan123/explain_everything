"""Phase 6 SimulationEngine — Consistency Check (C₁ + C₂).

参考 docs/plans/2026-05-14-cognitive-engine-phase-6-design.md §4.

API:
  check_consistency(state, target_id) → ConsistencyReport
  check_consistency_batch(state, target_ids?) → list[ConsistencyReport]

Pure rule-based, 0 LLM call. L0 不可 check (是 ground truth).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from explain_engine.engines._propagation import (
    WEAK_CHAIN_THRESHOLD,
    DecayStep,
    propagate,
)
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState


@dataclass(frozen=True)
class ConsistencyReport:
    """单个 target 的 consistency check 结果。

    Note: frozen=True 是浅冻结 — 只防字段 rebind, 不防 reachable_L0.append()
    或 contribution_breakdown[k]=v 这种 inner mutable container mutate.
    依赖 downstream (CLI render 等) 自律 read-only. Phase 7+ 真要严格 immutable
    再换 tuple + MappingProxyType.
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


def _get_all_L0(graph: ExplanationGraph) -> set[str]:
    return {nid for nid, n in graph.nodes.items() if n.abstraction_level == 0}


def _get_all_L1_L2(graph: ExplanationGraph) -> set[str]:
    return {nid for nid, n in graph.nodes.items() if n.abstraction_level >= 1}


def _check_with_baseline(
    state: CognitiveState,
    target_id: str,
    baseline_acts: dict[str, float] | None,
) -> ConsistencyReport:
    """单 target 的 C₁ + C₂. baseline 可传入 (batch 共用)."""
    graph = state.graph
    L0_nodes = _get_all_L0(graph)
    all_L1_L2 = _get_all_L1_L2(graph)

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
        target_id_list = sorted(_get_all_L1_L2(state.graph))
    else:
        target_id_list = list(target_ids)

    if not target_id_list:
        return []

    for tid in target_id_list:
        _validate_target(state, tid)

    all_L1_L2 = _get_all_L1_L2(state.graph)
    baseline_acts, _ = propagate(state.graph, all_L1_L2)

    return [
        _check_with_baseline(state, tid, baseline_acts)
        for tid in target_id_list
    ]
