"""把引擎数据结构转成前端 (Cytoscape) 友好的 JSON."""
from __future__ import annotations

from typing import Any

from explain_engine.engines.theory.cache import TheoriesCache
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState


def graph_to_cytoscape(
    graph: ExplanationGraph,
    state: CognitiveState | None = None,
) -> dict[str, Any]:
    """ExplanationGraph → Cytoscape elements (nodes/edges).

    Phase G: 传入 state 时附带证据字段 — tier (fact/inference/hypothesis/
    contested, 由 grounding.compute_tiers 按图结构计算) + evidence 来源列表
    (节点抽屉显示)。不传 state 保持旧行为 (backward compat)。
    """
    tiers: dict[str, str] = {}
    if state is not None:
        from explain_engine.engines.grounding import compute_tiers
        tiers = compute_tiers(state)

    def _evidence_of(obj) -> list[dict[str, str]]:
        if state is None or not obj.evidence_ids:
            return []
        return [
            {"url": ev.url, "title": ev.title, "stance": ev.stance}
            for ev_id in obj.evidence_ids
            if (ev := state.evidence.get(ev_id)) is not None
        ]

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
            "evidence_state": n.evidence_state,
            "tier": tiers.get(n.id, ""),
            "evidence": _evidence_of(n),
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
            "evidence_state": e.evidence_state,
            "tier": tiers.get(e.id, ""),
            "evidence": _evidence_of(e),
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
    edges = theory motif 结构 (stable + tentative 并集, 去 rejected), 连接变量。
    in_theory 标记参与任意 (非 rejected) theory 的变量。

    rejected theory (cache.rejected_theory_ids, 只 mark 不删) 不出现在边/in_theory。
    theme_index: theme 的数值序号 (前端 Cytoscape mapData 需数值才能上色; theme
    本身是字符串 id)。无 theme → -1。
    """
    rejected = cache.rejected_theory_ids
    # gid -> (theme_id, theme_name, theme_index)
    theme_of: dict[str, tuple[str, str, int]] = {}
    for idx, theme in enumerate(cache.themes):
        for gid in theme.member_global_ids:
            theme_of[gid] = (theme.id, theme.name, idx)

    all_theories = [
        t
        for t in list(cache.stable_theories) + list(cache.tentative_theories)
        if t.id not in rejected
    ]
    in_theory_ids: set[str] = set()
    for theory in all_theories:
        in_theory_ids.update(theory.node_ids)

    node_ids = {v["global_id"] for v in variables}
    nodes = []
    for v in variables:
        gid = v["global_id"]
        theme_id, theme_name, theme_index = theme_of.get(gid, ("", "", -1))
        nodes.append({"data": {
            "id": gid,
            "label": v["name"],
            "reuse": v["reuse_count"],
            "level": v["abstraction_level"],
            "theme": theme_id,
            "theme_name": theme_name,
            "theme_index": theme_index,
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
