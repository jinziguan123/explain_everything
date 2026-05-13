"""ExplanationGraph — networkx.DiGraph 包装。"""

from types import MappingProxyType
from typing import Mapping

import networkx as nx

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode


class ExplanationGraph:
    def __init__(self, root_question: str) -> None:
        self.root_question = root_question
        self._g: nx.DiGraph = nx.DiGraph()
        self._nodes: dict[str, VariableNode] = {}
        self._edges: dict[str, RelationEdge] = {}

    @property
    def nodes(self) -> Mapping[str, VariableNode]:
        """只读 view。修改请走 add_node()。"""
        return MappingProxyType(self._nodes)

    @property
    def edges(self) -> Mapping[str, RelationEdge]:
        """只读 view。修改请走 add_edge()。"""
        return MappingProxyType(self._edges)

    def add_node(self, node: VariableNode) -> None:
        if node.id in self._nodes:
            raise ValueError(f"node {node.id} already exists")
        self._nodes[node.id] = node
        self._g.add_node(node.id)

    def add_edge(self, edge: RelationEdge) -> None:
        if edge.source_node not in self._nodes:
            raise ValueError(f"unknown node: {edge.source_node}")
        if edge.target_node not in self._nodes:
            raise ValueError(f"unknown node: {edge.target_node}")
        if edge.id in self._edges:
            raise ValueError(f"edge {edge.id} already exists")
        self._edges[edge.id] = edge
        self._g.add_edge(edge.source_node, edge.target_node, edge_id=edge.id)

    def compression_score(self) -> float:
        return float(
            sum(
                self._g.out_degree(nid)
                for nid, node in self._nodes.items()
                if node.abstraction_level >= 1
            )
        )

    def coverage_score(self) -> float:
        concretes = [nid for nid, n in self._nodes.items() if n.abstraction_level == 0]
        if not concretes:
            return 0.0
        covered = {
            nid
            for nid in concretes
            if any(
                pred for pred in self._g.predecessors(nid)
                if self._nodes[pred].abstraction_level >= 1
            )
        }
        return len(covered) / len(concretes)

    def frontier(self) -> list[str]:
        return sorted(
            nid
            for nid, n in self._nodes.items()
            if n.abstraction_level >= 1 and self._g.out_degree(nid) == 0
        )

    def to_dict(self) -> dict:
        return {
            "root_question": self.root_question,
            "nodes": {nid: n.model_dump() for nid, n in self._nodes.items()},
            "edges": {eid: e.model_dump() for eid, e in self._edges.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExplanationGraph":
        try:
            g = cls(root_question=d["root_question"])
            for nid, n in d["nodes"].items():
                g.add_node(VariableNode.model_validate(n))
            for eid, e in d["edges"].items():
                g.add_edge(RelationEdge.model_validate(e))
            return g
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid graph dict: {exc}") from exc
