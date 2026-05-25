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
