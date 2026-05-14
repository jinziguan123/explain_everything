"""ExplanationGraph.outgoing_edges(node_id) helper — Phase 6 propagation 用。"""

import pytest

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid: str, level: int = 1) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7, epistemic="insight",
    )


def _edge(eid: str, src: str, dst: str, rel: str = "manifests_as") -> RelationEdge:
    return RelationEdge(
        id=eid, source_node=src, target_node=dst,
        relation_type=rel, confidence=0.7,
        mechanism_description="m",
    )


class TestOutgoingEdges:
    def test_outgoing_edges_of_existing_node(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        g.add_node(_node("p_002", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001"))
        g.add_edge(_edge("e_002", "c_001", "p_002"))
        # 不该返 incoming edges
        g.add_node(_node("d_001", level=2))
        g.add_edge(_edge("e_003", "d_001", "c_001", rel="causes"))

        outs = list(g.outgoing_edges("c_001"))
        assert len(outs) == 2
        out_ids = sorted(e.id for e in outs)
        assert out_ids == ["e_001", "e_002"]

    def test_outgoing_edges_of_isolated_node_returns_empty(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        outs = list(g.outgoing_edges("c_001"))
        assert outs == []

    def test_outgoing_edges_of_nonexistent_node_raises(self) -> None:
        g = ExplanationGraph(root_question="why")
        with pytest.raises(ValueError, match=r"not found|不存在"):
            list(g.outgoing_edges("nonexistent"))
