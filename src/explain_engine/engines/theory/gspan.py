"""Phase 16: 自实现 simplified gSpan (Yan & Han 2002), directed in-memory.

简化点 (跟 paper 比):
- 仅 directed (我们 explanation graph 是 directed manifests_as/causes)
- in-memory list[(graph_id, nx.DiGraph)] API, 不读 file
- 不支持 disconnected motif / weighted edge
- Node/edge label 用 nx attribute "label" 字段

参考: Yan & Han 2002 §4 (Algorithm gSpan), §4.1 (canonical DFS code),
§4.2 (rightmost-path extension)
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import networkx as nx


@dataclass(frozen=True)
class DFSEdge:
    """gSpan DFS code 的一条 edge entry.

    (from_idx, to_idx): 在 DFS tree 中的位置. forward edge 若 to_idx > from_idx,
    backward edge 若 to_idx < from_idx (跟 paper §4 一致).
    """
    from_idx: int
    to_idx: int
    from_label: str
    edge_label: str
    to_label: str


@dataclass(frozen=True)
class FrequentSubgraph:
    """gSpan output: frequent subgraph + 在哪些 input graph 出现 + 位置 mapping."""
    nodes: tuple[str, ...]  # canonical DFS 顺序 motif-local id (e.g. "n0", "n1")
    edges: tuple[tuple[str, str, str], ...]  # (src_motif_id, tgt_motif_id, edge_label)
    support_count: int
    embeddings_in_graphs: tuple[tuple[str, dict], ...]
    # (graph_id, {motif_node_id: graph_node_id}) — caller 反查需要


def _count_frequent_edges(
    graphs: list[tuple[str, nx.DiGraph]],
    min_support: int,
) -> list[tuple[tuple[str, str, str], int]]:
    """Phase 1: 数所有 1-edge (from_label, edge_label, to_label) 在多少 graph 出现.

    Returns: [((from_label, edge_label, to_label), graph_count)] 满足 >= min_support.
    """
    edge_to_graphs: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for gid, g in graphs:
        seen_in_this_graph: set[tuple[str, str, str]] = set()
        for src, tgt, edata in g.edges(data=True):
            from_lbl = g.nodes[src].get("label", "")
            to_lbl = g.nodes[tgt].get("label", "")
            edge_lbl = edata.get("label", "")
            template = (from_lbl, edge_lbl, to_lbl)
            if template not in seen_in_this_graph:
                edge_to_graphs[template].add(gid)
                seen_in_this_graph.add(template)
    return [(tpl, len(gids)) for tpl, gids in edge_to_graphs.items()
            if len(gids) >= min_support]


def _is_minimum_dfs_code(code: list[DFSEdge]) -> bool:
    """gSpan §4.1: 判 code 是否为该 subgraph 所有 isomorphic DFS code 中字典序最小者.

    简化策略 (MVP, ≤ 5 node 时正确):
      1. 用 code 重建 subgraph (nodes + edges)
      2. 从每个 node 起始, 按 label 排序确定性 DFS 生成 1 candidate code
      3. 取字典序最小的, 跟 input code 比

    完整 gSpan 用 incremental DFS code generation (Yan & Han §4.1) 加速;
    MVP 暴力即可. 我们 motif ≤ 5 node, 起始点 ≤ 5, 每条 candidate code O(N).
    """
    if not code:
        return True

    # Step 1: 用 code 重建 subgraph
    g = nx.DiGraph()
    node_labels: dict[int, str] = {}
    for e in code:
        if e.from_idx not in node_labels:
            node_labels[e.from_idx] = e.from_label
        if e.to_idx not in node_labels:
            node_labels[e.to_idx] = e.to_label
        g.add_edge(e.from_idx, e.to_idx, label=e.edge_label)
    for nid, lbl in node_labels.items():
        g.nodes[nid]["label"] = lbl

    # Step 2: 从每个 node 起始, 跑 deterministic DFS 生 candidate code
    all_candidates: list[list[DFSEdge]] = []
    for start in g.nodes:
        candidate = _enumerate_dfs_codes_from(g, start)
        if candidate:
            all_candidates.append(candidate)

    # Step 3: 字典序最小
    canonical_keys = sorted(_dfs_code_tuple(c) for c in all_candidates)
    return _dfs_code_tuple(code) == canonical_keys[0]


def _dfs_code_tuple(code: list[DFSEdge]) -> tuple:
    return tuple((e.from_idx, e.to_idx, e.from_label, e.edge_label, e.to_label) for e in code)


def _enumerate_dfs_codes_from(g: nx.DiGraph, start) -> list[DFSEdge]:
    """从 start 跑 deterministic DFS 生 1 candidate DFS code.

    分叉时按 (to_label, edge_label, to_node) 排序选 (deterministic).
    MVP: 每 start 1 个 candidate, 不全枚举 isomorphism (≤ 5 node 时正确).
    """
    visited: dict = {start: 0}
    code: list[DFSEdge] = []

    def visit(node) -> None:
        # 出边按 (to_label, edge_label, to_node) 排序
        edges = sorted(
            g.out_edges(node, data=True),
            key=lambda x: (g.nodes[x[1]].get("label", ""), x[2].get("label", ""), x[1]),
        )
        for _, nbr, edata in edges:
            from_idx = visited[node]
            from_label = g.nodes[node].get("label", "")
            edge_label = edata.get("label", "")
            to_label = g.nodes[nbr].get("label", "")
            if nbr not in visited:
                visited[nbr] = len(visited)
                code.append(DFSEdge(
                    from_idx=from_idx, to_idx=visited[nbr],
                    from_label=from_label, edge_label=edge_label, to_label=to_label,
                ))
                visit(nbr)
            else:
                # backward edge (cycle)
                code.append(DFSEdge(
                    from_idx=from_idx, to_idx=visited[nbr],
                    from_label=from_label, edge_label=edge_label, to_label=to_label,
                ))

    visit(start)
    return code


def _rightmost_path(code: list[DFSEdge]) -> list[int]:
    """gSpan §4.2: rightmost path 是从 root (idx 0) 沿 forward edge 到最新 added 节点的路径.

    forward edge: to_idx > from_idx.
    """
    if not code:
        return [0]
    # 最新 added forward edge 的 to_idx
    forward_edges = [e for e in code if e.to_idx > e.from_idx]
    if not forward_edges:
        return [0]
    rightmost_node = max(e.to_idx for e in forward_edges)
    # 沿 forward edge 回溯到 0
    path = [rightmost_node]
    cur = rightmost_node
    while cur != 0:
        prev = [e for e in code if e.to_idx == cur and e.to_idx > e.from_idx]
        if not prev:
            break
        cur = prev[0].from_idx
        path.insert(0, cur)
    return path


def _enumerate_rightmost_extensions(
    code: list[DFSEdge],
    graphs: list[tuple[str, nx.DiGraph]],
    embeddings: list[dict],  # 每 graph 一个 motif_idx → graph_node 映射
) -> list[DFSEdge]:
    """gSpan §4.2: 只扩展 rightmost path 上的节点 (避免重复枚举).

    MVP: 只支持 forward extension (从 rightmost path 节点新加未访问子节点).
    backward extension (cycle 形成) 由 Task 7 _dfs_extend 内单独处理.
    """
    rmpath = _rightmost_path(code)
    if code:
        next_idx = max(max(e.from_idx, e.to_idx) for e in code) + 1
    else:
        next_idx = 1

    candidates: list[DFSEdge] = []
    seen: set = set()

    for graph_idx, (_gid, g) in enumerate(graphs):
        if graph_idx >= len(embeddings):
            continue
        emb = embeddings[graph_idx]
        for motif_node in rmpath:
            if motif_node not in emb:
                continue
            graph_node = emb[motif_node]
            if graph_node not in g.nodes:
                continue
            for _, nbr, edata in g.out_edges(graph_node, data=True):
                if nbr in emb.values():
                    continue  # 已 mapped, skip (backward 单独处理)
                nbr_label = g.nodes[nbr].get("label", "")
                edge_label = edata.get("label", "")
                from_label = g.nodes[graph_node].get("label", "")
                ext_key = (motif_node, from_label, edge_label, nbr_label)
                if ext_key in seen:
                    continue
                seen.add(ext_key)
                candidates.append(DFSEdge(
                    from_idx=motif_node, to_idx=next_idx,
                    from_label=from_label, edge_label=edge_label, to_label=nbr_label,
                ))
    return candidates


def _count_support(
    motif_code: list[DFSEdge],
    graphs: list[tuple[str, nx.DiGraph]],
) -> tuple[int, list[tuple[str, dict]]]:
    """对 motif_code 跑子图同构, 数它出现在多少 graph + 记录 embedding.

    Returns: (support_count, [(graph_id, {motif_idx: graph_node})])
    """
    # 用 code 重建 motif graph
    motif_g = nx.DiGraph()
    node_labels: dict[int, str] = {}
    for e in motif_code:
        node_labels[e.from_idx] = e.from_label
        node_labels[e.to_idx] = e.to_label
        motif_g.add_edge(e.from_idx, e.to_idx, label=e.edge_label)
    for nid, lbl in node_labels.items():
        motif_g.nodes[nid]["label"] = lbl

    found: list[tuple[str, dict]] = []
    for gid, g in graphs:
        matcher = nx.algorithms.isomorphism.DiGraphMatcher(
            g, motif_g,
            node_match=lambda a, b: a.get("label") == b.get("label"),
            edge_match=lambda a, b: a.get("label") == b.get("label"),
        )
        if matcher.subgraph_is_isomorphic():
            # mapping is graph_node → motif_idx, 反转为 motif_idx → graph_node
            mapping = matcher.mapping  # type: ignore
            inv = {v: k for k, v in mapping.items()}
            found.append((gid, inv))
    return len(found), found
