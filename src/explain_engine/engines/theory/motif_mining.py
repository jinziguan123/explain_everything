"""Phase 16: 跨 session theme subgraph 抽取 → gspan_mine → RawMotif."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import networkx as nx

from explain_engine.engines.theory.gspan import gspan_mine
from explain_engine.engines.theory.theory import Theme


@dataclass(frozen=True)
class RawMotif:
    motif_type: Literal["chain", "star", "cycle"]
    nodes: tuple[str, ...]  # lexicon global_ids (从 first embedding 取)
    edges: tuple[tuple[str, str, str], ...]
    supporting_sessions: tuple[str, ...]


def find_motifs_per_theme(
    sessions: dict[str, object],   # {sid: ExplanationGraph-like}
    theme: Theme,
    min_freq: int,
) -> list[RawMotif]:
    """对每 session 抽 theme subgraph (含一跳邻居), 跑 gspan_mine, 返 RawMotif list."""
    theme_node_set = set(theme.member_global_ids)
    per_session_subgraph: list[tuple[str, nx.DiGraph]] = []

    for sid, graph in sessions.items():
        sub = _extract_theme_subgraph(graph, theme_node_set)
        if len(sub.nodes) >= 2:
            per_session_subgraph.append((sid, sub))

    if len(per_session_subgraph) < min_freq:
        return []

    frequent = gspan_mine(
        graphs=per_session_subgraph, min_support=min_freq,
        min_size=2, max_size=5, is_directed=True,
    )

    motifs: list[RawMotif] = []
    for fs in frequent:
        motif_type = _classify_motif_type(fs)
        if not fs.embeddings_in_graphs:
            continue
        _first_gid, first_emb = fs.embeddings_in_graphs[0]
        # nodes: 用 first graph 的 mapping 反查 lexicon global_id
        nodes_gids = tuple(first_emb.get(n, n) for n in fs.nodes)
        # edges: 把 motif-local n0/n1 转 graph node id
        edges_gids = tuple(
            (first_emb.get(src, src), first_emb.get(tgt, tgt), rel)
            for src, tgt, rel in fs.edges
        )
        motifs.append(RawMotif(
            motif_type=motif_type,
            nodes=nodes_gids,
            edges=edges_gids,
            supporting_sessions=tuple(gid for gid, _ in fs.embeddings_in_graphs),
        ))
    return motifs


def _extract_theme_subgraph(graph, theme_node_set: set[str]) -> nx.DiGraph:
    """从 session graph 抽 theme nodes 涉及的 subgraph (含一跳邻居).

    label 取 node.name (用于 gspan 子图同构 match).
    """
    sub = nx.DiGraph()
    for node in graph.nodes.values():
        if node.id in theme_node_set:
            sub.add_node(node.id, label=node.name)
    for edge in graph.edges.values():
        if edge.source_node in theme_node_set or edge.target_node in theme_node_set:
            for nid in (edge.source_node, edge.target_node):
                if nid not in sub.nodes and nid in graph.nodes:
                    sub.add_node(nid, label=graph.nodes[nid].name)
            sub.add_edge(edge.source_node, edge.target_node, label=edge.relation_type)
    return sub


def _classify_motif_type(fs) -> Literal["chain", "star", "cycle"]:
    """根据 motif 拓扑分类."""
    g = nx.DiGraph()
    for src, tgt, _ in fs.edges:
        g.add_edge(src, tgt)
    if g.number_of_nodes() == 0:
        return "chain"
    if any(g.in_degree(n) > 1 or g.out_degree(n) > 1 for n in g.nodes):
        return "star"
    if not nx.is_directed_acyclic_graph(g):
        return "cycle"
    return "chain"
