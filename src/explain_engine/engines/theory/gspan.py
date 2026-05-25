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
