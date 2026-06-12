"""Phase 16: Theory + Theme dataclass + 稳定 hash."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    member_global_ids: tuple[str, ...]
    centroid_summary: str


@dataclass(frozen=True)
class Theory:
    id: str
    motif_type: Literal["chain", "star", "cycle"]
    theme_ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    edges: tuple[tuple[str, str, str], ...]  # (src_gid, tgt_gid, relation_type)
    supporting_sessions: tuple[str, ...]
    natural_language_summary: str
    structure_complexity: int
    first_seen_session: str
    last_seen_session: str
    # JEPA (a) — falsifiability
    predictive_power: float = 0.0
    # Phase T (设计预期-修正版 §七.2): predictive_power 来源 —
    # "retrodiction" = falsifiability.py leave-one-out 回溯估计 (默认);
    # "ledger" = 预测台账命中率 (有已结算预测时覆盖回溯估计)。
    predictive_power_source: Literal["retrodiction", "ledger"] = "retrodiction"
    # JEPA (b) — slow-fast; Phase T 加 "weakened" (台账连败 ≥2 → 已削弱)
    stability_status: Literal["tentative", "stable", "weakened"] = "tentative"
    stable_promoted_at_session: str | None = None


def _compute_theory_id(motif_type: str, edges: tuple) -> str:
    """edges 按 (src, tgt, rel) 排序保 deterministic. 跨 recompute 稳定."""
    canonical = f"{motif_type}:{tuple(sorted(edges))}"
    return "t_" + hashlib.sha256(canonical.encode()).hexdigest()[:10]
