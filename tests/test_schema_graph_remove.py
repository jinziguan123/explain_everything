"""ExplanationGraph.remove_node / remove_edge 测试。"""

import pytest

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid: str, level: int = 0) -> VariableNode:
    return VariableNode(
        id=nid,
        name=nid,
        description="",
        abstraction_level=level,  # type: ignore[arg-type]
        confidence=0.7,
        epistemic="observation",
    )


def _edge(eid: str, src: str, tgt: str) -> RelationEdge:
    return RelationEdge(
        id=eid,
        source_node=src,
        target_node=tgt,
        relation_type="manifests_as",
        confidence=0.7,
        mechanism_description="m",
    )


class TestRemoveNode:
    def test_remove_node_basic(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("a"))
        g.remove_node("a")
        assert "a" not in g.nodes

    def test_remove_node_cascade_outgoing_edges(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("a", level=1))
        g.add_node(_node("b"))
        g.add_edge(_edge("e1", "a", "b"))
        g.remove_node("a")
        assert "a" not in g.nodes
        assert "e1" not in g.edges
        assert "b" in g.nodes

    def test_remove_node_cascade_incoming_edges(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("a", level=1))
        g.add_node(_node("b"))
        g.add_edge(_edge("e1", "a", "b"))
        g.remove_node("b")
        assert "b" not in g.nodes
        assert "e1" not in g.edges
        assert "a" in g.nodes

    def test_remove_node_missing_raises(self) -> None:
        g = ExplanationGraph(root_question="q")
        with pytest.raises(ValueError, match="not found"):
            g.remove_node("nonexistent")


class TestRemoveEdge:
    def test_remove_edge_basic(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("a", level=1))
        g.add_node(_node("b"))
        g.add_edge(_edge("e1", "a", "b"))
        g.remove_edge("e1")
        assert "e1" not in g.edges
        assert "a" in g.nodes
        assert "b" in g.nodes

    def test_remove_edge_missing_raises(self) -> None:
        g = ExplanationGraph(root_question="q")
        with pytest.raises(ValueError, match="not found"):
            g.remove_edge("nonexistent")
