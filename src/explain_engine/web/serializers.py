"""把引擎数据结构转成前端 (Cytoscape) 友好的 JSON."""
from __future__ import annotations

from typing import Any

from explain_engine.engines.theory.cache import TheoriesCache
from explain_engine.schema.graph import ExplanationGraph


def graph_to_cytoscape(graph: ExplanationGraph) -> dict[str, Any]:
    """ExplanationGraph → Cytoscape elements (nodes/edges)."""
    nodes = [
        {"data": {
            "id": n.id,
            "label": n.name,
            "level": n.abstraction_level,
            "epistemic": n.epistemic,
            "confidence": n.confidence,
            "description": n.description,
            "lifecycle": n.lifecycle_state,
            "activation": n.activation,
            "stability": n.stability,
        }}
        for n in graph.nodes.values()
    ]
    edges = [
        {"data": {
            "id": e.id,
            "source": e.source_node,
            "target": e.target_node,
            "relation": e.relation_type,
            "confidence": e.confidence,
            "mechanism": e.mechanism_description,
        }}
        for e in graph.edges.values()
    ]
    return {
        "root_question": graph.root_question,
        "elements": {"nodes": nodes, "edges": edges},
    }


def lexicon_to_cytoscape(
    variables: list[dict[str, Any]],
    cache: TheoriesCache,
) -> dict[str, Any]:
    """跨 session 知识图 → Cytoscape elements.

    nodes = lexicon 变量 (按 reuse 调大小, 按 theme 上色),
    edges = theory motif 结构 (stable + tentative 并集), 连接变量。
    in_theory 标记参与任意 theory 的变量。
    """
    # gid -> (theme_id, theme_name)
    theme_of: dict[str, tuple[str, str]] = {}
    for theme in cache.themes:
        for gid in theme.member_global_ids:
            theme_of[gid] = (theme.id, theme.name)

    all_theories = list(cache.stable_theories) + list(cache.tentative_theories)
    in_theory_ids: set[str] = set()
    for theory in all_theories:
        in_theory_ids.update(theory.node_ids)

    node_ids = {v["global_id"] for v in variables}
    nodes = []
    for v in variables:
        gid = v["global_id"]
        theme_id, theme_name = theme_of.get(gid, ("", ""))
        nodes.append({"data": {
            "id": gid,
            "label": v["name"],
            "reuse": v["reuse_count"],
            "level": v["abstraction_level"],
            "theme": theme_id,
            "theme_name": theme_name,
            "in_theory": gid in in_theory_ids,
        }})

    edges = []
    seen_edge_ids: set[str] = set()
    for theory in all_theories:
        for src, tgt, rel in theory.edges:
            # 跳过悬空边: 两端都得在节点集里 (防 Cytoscape 报 nonexistent source/target)
            if src not in node_ids or tgt not in node_ids:
                continue
            edge_id = f"{src}__{tgt}__{rel}"
            if edge_id in seen_edge_ids:
                continue
            seen_edge_ids.add(edge_id)
            edges.append({"data": {
                "id": edge_id,
                "source": src,
                "target": tgt,
                "relation": rel,
                "theory_id": theory.id,
            }})

    return {"elements": {"nodes": nodes, "edges": edges}}
