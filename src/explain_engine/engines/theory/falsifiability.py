"""Phase 16 JEPA (a): theory 的 predictive_power 评估 (leave-one-session-out).

哲学 §9.4 可证伪性 — theory 必须可失败. 客观判据:
若 motif nodes 在 held-out session L0 phenomena 中能 cosine ≥ 0.85 match
至少 1 个, 算 predict 成功. predictive_power = 命中 / supporting_session 数.

设计抉择 (MVP):
- 用 motif.nodes 当 query (lexicon global_id 但 encode 时用 id 字面 — 实际 lexicon
  side 应给 name. 当前 simplified, 后续 Task 12 集成时改 lookup canonical name).
- 用 held session L0 .name 当 corpus.
- cosine ≥ 0.85 跟 Phase 13 merge threshold 一致.
- 任一 motif node 跟任一 L0 cosine ≥ threshold → 算 hit (per session).
"""
from __future__ import annotations

import numpy as np

from explain_engine.engines.theory.motif_mining import RawMotif


def evaluate_predictive_power(
    motif: RawMotif,
    all_sessions: dict[str, object],   # {sid: ExplanationGraph-like}
    embedder,                          # has .encode(names) -> np.ndarray
    match_threshold: float = 0.85,
) -> float:
    """leave-one-out: 对每 supporting session, 看 motif nodes 能否在该 session L0 match.

    Returns predictive_power ∈ [0, 1].
    若 supporting_sessions < 2, 返 0 (无 leave-one-out 意义).
    """
    if len(motif.supporting_sessions) < 2:
        return 0.0

    motif_node_names = list(motif.nodes)
    motif_embs = _normalize(embedder.encode(motif_node_names))

    hit_count = 0
    for held_out_sid in motif.supporting_sessions:
        held_graph = all_sessions.get(held_out_sid)
        if held_graph is None:
            continue
        held_l0_phenomena = [
            n for n in held_graph.nodes.values()
            if getattr(n, "abstraction_level", -1) == 0
        ]
        if not held_l0_phenomena:
            continue

        held_names = [n.name for n in held_l0_phenomena]
        held_embs = _normalize(embedder.encode(held_names))

        # 任一 motif node 跟任一 held L0 cosine ≥ threshold → 命中
        sim_matrix = motif_embs @ held_embs.T  # (motif_n, held_n)
        if (sim_matrix >= match_threshold).any():
            hit_count += 1

    return hit_count / len(motif.supporting_sessions)


def _normalize(embs: np.ndarray) -> np.ndarray:
    """L2 归一化 (cosine 通过 dot product 算)."""
    if embs.size == 0:
        return embs
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    return embs / np.where(norms > 0, norms, 1)
