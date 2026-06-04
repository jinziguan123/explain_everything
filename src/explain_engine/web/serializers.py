"""把引擎数据结构转成前端 (Cytoscape) 友好的 JSON."""
from __future__ import annotations

from typing import Any

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
