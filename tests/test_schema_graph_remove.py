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
        assert "a" not in g._g.nodes

    def test_remove_node_cascade_outgoing_edges(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("a", level=1))
        g.add_node(_node("b"))
        g.add_edge(_edge("e1", "a", "b"))
        g.remove_node("a")
        assert "a" not in g.nodes
        assert "e1" not in g.edges
        assert "b" in g.nodes
        assert "a" not in g._g.nodes
        assert not g._g.has_edge("a", "b")

    def test_remove_node_cascade_incoming_edges(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("a", level=1))
        g.add_node(_node("b"))
        g.add_edge(_edge("e1", "a", "b"))
        g.remove_node("b")
        assert "b" not in g.nodes
        assert "e1" not in g.edges
        assert "a" in g.nodes
        assert "b" not in g._g.nodes
        assert not g._g.has_edge("a", "b")

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
        assert not g._g.has_edge("a", "b")
        assert "a" in g._g.nodes and "b" in g._g.nodes

    def test_remove_edge_missing_raises(self) -> None:
        g = ExplanationGraph(root_question="q")
        with pytest.raises(ValueError, match="not found"):
            g.remove_edge("nonexistent")


class TestReplaceNode:
    def test_replace_node_basic(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("a", level=1))
        g.add_node(_node("b"))
        g.add_edge(_edge("e1", "a", "b"))

        updated = VariableNode(
            id="a",
            name="new name",
            description="new desc",
            abstraction_level=1,
            confidence=0.9,
            epistemic="observation",
            source="user",
        )
        g.replace_node("a", updated)

        assert g.nodes["a"].name == "new name"
        assert g.nodes["a"].description == "new desc"
        assert g.nodes["a"].confidence == 0.9
        assert g.nodes["a"].source == "user"
        # edges 不动
        assert "e1" in g.edges
        assert g._g.has_edge("a", "b")

    def test_replace_node_missing_raises(self) -> None:
        g = ExplanationGraph(root_question="q")
        new = _node("x")
        with pytest.raises(ValueError, match="not found"):
            g.replace_node("x", new)

    def test_replace_node_id_mismatch_raises(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("a"))
        wrong_id = _node("b")
        with pytest.raises(ValueError, match="id mismatch"):
            g.replace_node("a", wrong_id)
