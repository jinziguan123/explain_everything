"""Phase 16: lexicon variables 按 cosine 距离 cluster, 形成 theme groups.

复用 Phase 13 已 lazy-migrate 的 BGE-M3 embedding (lexicon 内 var 的 embedding 字段).
若 var 缺 embedding 字段, 当前实现跳 (不调 embedder); 完整 fallback embedder.encode()
留给后续 polish.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from explain_engine.engines.theory.theory import Theme


def cluster_lexicon_themes(
    lexicon: dict,
    embedder=None,  # 兼容性占位; 当前 MVP 假设 var 已含 embedding
    cosine_threshold: float = 0.85,
) -> list[Theme]:
    """Union-find agglomerative clustering. O(N²) for N ≤ 100 var.

    Args:
        lexicon: {"variables": [{"global_id", "name", "embedding": list[float]}]}
        embedder: 占位, 当前不用 (var 已有 embedding); future fallback.
        cosine_threshold: ≥ 阈值视为同 theme (跟 Phase 13 0.85 一致).

    Returns:
        Theme list (size ≥ 2 的 cluster). cluster name = 中心最近 member.name.
    """
    variables = [v for v in lexicon.get("variables", []) if v.get("embedding")]
    if len(variables) < 2:
        return []

    embs = np.stack([np.array(v["embedding"]) for v in variables])
    # 归一化 (cosine 比 dot-product 用)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    embs = embs / np.where(norms > 0, norms, 1)
    var_ids = [v["global_id"] for v in variables]
    var_names = {v["global_id"]: v["name"] for v in variables}
    n = len(var_ids)

    # Union-find
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    sim_matrix = embs @ embs.T
    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i, j] >= cosine_threshold:
                union(i, j)

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    themes: list[Theme] = []
    cluster_idx = 0
    for indices in clusters.values():
        if len(indices) < 2:
            continue  # single member 无意义
        member_ids = tuple(var_ids[i] for i in indices)
        # centroid 最近 var 取 name
        centroid = embs[indices].mean(axis=0)
        distances = [(i, np.linalg.norm(embs[i] - centroid)) for i in indices]
        nearest_idx = min(distances, key=lambda x: x[1])[0]
        rep_name = var_names[var_ids[nearest_idx]]
        themes.append(Theme(
            id=f"th_{cluster_idx:03d}",
            name=rep_name,
            member_global_ids=member_ids,
            centroid_summary=f"{rep_name} (cluster of {len(member_ids)})",
        ))
        cluster_idx += 1
    return themes
