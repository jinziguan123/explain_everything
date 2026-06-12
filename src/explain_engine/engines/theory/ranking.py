"""Phase 16 JEPA (b)(c): scoring + MMR diversity + promote stable."""
from __future__ import annotations

from explain_engine.engines.theory.theory import Theory


def compute_score(theory: Theory, n_sessions_total: int) -> float:
    """Composite score:
      0.35 · frequency  + 0.20 · complexity  + 0.45 · predictive_power
    weights 偏向 falsifiability (JEPA a) 最重.
    """
    freq = len(theory.supporting_sessions) / max(n_sessions_total, 1)
    complexity = min(theory.structure_complexity, 5) / 5.0
    return (
        0.35 * freq
        + 0.20 * complexity
        + 0.45 * theory.predictive_power
    )


def theme_overlap(t1: Theory, t2: Theory) -> float:
    """Jaccard 相似度 of theme_ids."""
    s1, s2 = set(t1.theme_ids), set(t2.theme_ids)
    return len(s1 & s2) / max(len(s1 | s2), 1)


def rank_topk_with_mmr(
    theories: list[Theory],
    k: int = 20,
    lambda_: float = 0.7,
    n_sessions_total: int = 1,
) -> list[Theory]:
    """JEPA (c): MMR diversity ranking.

    lambda_ 偏 relevance, (1-lambda_) 偏 diversity.
    经典 MMR: λ·sim(query, doc) - (1-λ)·max_overlap(doc, selected).
    """
    if not theories:
        return []
    selected: list[Theory] = []
    pool = sorted(theories, key=lambda t: -compute_score(t, n_sessions_total))
    while len(selected) < k and pool:
        if not selected:
            selected.append(pool.pop(0))
            continue
        best = max(pool, key=lambda t: (
            lambda_ * compute_score(t, n_sessions_total)
            - (1 - lambda_) * max(theme_overlap(t, s) for s in selected)
        ))
        selected.append(best)
        pool.remove(best)
    return selected


def overlap_ratio(t1: Theory, t2: Theory) -> float:
    """node_ids Jaccard — §七.3 竞争对判定 (>0.5 即同一现象域上的竞争理论)。"""
    s1, s2 = set(t1.node_ids), set(t2.node_ids)
    return len(s1 & s2) / max(len(s1 | s2), 1)


def competition_rank(
    theories: list[Theory], n_sessions_total: int,
) -> list[Theory]:
    """Phase T (设计预期-修正版 §七.3) 竞争排序: 按字典序
    predictive_power → 综合分 (压缩值代理) → 复现 session 数。

    仅影响展示顺序与推荐, 不删除败者 (多解释共存原则)。
    覆盖重叠 >0.5 的竞争对在此序下自然分出先后; 不重叠的理论
    也用同一全序, 保持列表稳定可比。
    """
    return sorted(theories, key=lambda t: (
        -t.predictive_power,
        -compute_score(t, n_sessions_total),
        -len(t.supporting_sessions),
        t.id,
    ))


def maybe_promote_to_stable(
    theory: Theory,
    all_sessions: list[str],
    window_size: int,
) -> bool:
    """JEPA (b): 最近 window_size session 内 ≥ ⌈window/2⌉+1 个有 theory → stable.

    简化版 (真 EMA 在 NN 才有, symbolic 用 sliding window 即可).
    若 all_sessions < window_size, 返 False (不够窗口判 stability).
    """
    if len(all_sessions) < window_size:
        return False
    recent_window = set(all_sessions[-window_size:])
    overlap = recent_window & set(theory.supporting_sessions)
    return len(overlap) >= (window_size // 2 + 1)
