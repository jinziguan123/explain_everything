"""ExplanationGraph — networkx.DiGraph 包装。

把 VariableNode / RelationEdge 暴露成 dict-like，并提供 compression /
coverage / frontier 计算。

参考 docs/plans/2026-05-13-cognitive-engine-mvp-design.md §3.2。
"""

import networkx as nx

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode


class ExplanationGraph:
    def __init__(self, root_question: str) -> None:
        self.root_question = root_question
        self._g: nx.DiGraph = nx.DiGraph()
        self.nodes: dict[str, VariableNode] = {}
        self.edges: dict[str, RelationEdge] = {}

    def add_node(self, node: VariableNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"node {node.id} already exists")
        self.nodes[node.id] = node
        self._g.add_node(node.id)

    def add_edge(self, edge: RelationEdge) -> None:
        if edge.source_node not in self.nodes:
            raise ValueError(f"unknown node: {edge.source_node}")
        if edge.target_node not in self.nodes:
            raise ValueError(f"unknown node: {edge.target_node}")
        if edge.id in self.edges:
            raise ValueError(f"edge {edge.id} already exists")
        self.edges[edge.id] = edge
        self._g.add_edge(edge.source_node, edge.target_node, edge_id=edge.id)

    def compression_score(self) -> float:
        """abstract 节点覆盖了多少 concrete。

        v0.1 简化：返回所有 abstraction_level >= 1 节点的 out-degree 之和。
        """
        return float(
            sum(
                self._g.out_degree(nid)
                for nid, node in self.nodes.items()
                if node.abstraction_level >= 1
            )
        )

    def coverage_score(self) -> float:
        """concrete 节点中被任意 high-abstraction 节点覆盖的比例。"""
        concretes = [nid for nid, n in self.nodes.items() if n.abstraction_level == 0]
        if not concretes:
            return 0.0

        covered = {
            nid
            for nid in concretes
            if any(
                pred for pred in self._g.predecessors(nid)
                if self.nodes[pred].abstraction_level >= 1
            )
        }
        return len(covered) / len(concretes)

    def frontier(self) -> list[str]:
        """没有 outgoing edge 的 high-abstraction 节点。"""
        return sorted(
            nid
            for nid, n in self.nodes.items()
            if n.abstraction_level >= 1 and self._g.out_degree(nid) == 0
        )

    def to_dict(self) -> dict:
        return {
            "root_question": self.root_question,
            "nodes": {nid: n.model_dump() for nid, n in self.nodes.items()},
            "edges": {eid: e.model_dump() for eid, e in self.edges.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExplanationGraph":
        g = cls(root_question=d["root_question"])
        for nid, n in d["nodes"].items():
            g.add_node(VariableNode.model_validate(n))
        for eid, e in d["edges"].items():
            g.add_edge(RelationEdge.model_validate(e))
        return g
