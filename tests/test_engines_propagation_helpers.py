"""Wave B.4: _propagation.py 加 public helpers (get_all_L0, get_all_L1_L2, _next_id, _next_edge_id)."""

from explain_engine.engines._propagation import (
    get_all_L0,
    get_all_L1_L2,
    next_edge_id,
    next_id,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid: str, level: int) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7,
        epistemic="insight" if level >= 1 else "observation",
    )


class TestGetAllHelpers:
    def test_get_all_L0_returns_only_level_0(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("p_001", 0))
        g.add_node(_node("p_002", 0))
        g.add_node(_node("c_001", 1))
        g.add_node(_node("d_001", 2))
        assert get_all_L0(g) == {"p_001", "p_002"}

    def test_get_all_L1_L2_returns_non_L0(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("p_001", 0))
        g.add_node(_node("c_001", 1))
        g.add_node(_node("d_001", 2))
        assert get_all_L1_L2(g) == {"c_001", "d_001"}

    def test_empty_graph(self) -> None:
        g = ExplanationGraph(root_question="q")
        assert get_all_L0(g) == set()
        assert get_all_L1_L2(g) == set()


class TestNextIdHelpers:
    def test_next_id_empty_graph_returns_001(self) -> None:
        g = ExplanationGraph(root_question="q")
        assert next_id(g, "p") == "p_001"
        assert next_id(g, "c") == "c_001"
        assert next_id(g, "d") == "d_001"

    def test_next_id_increments_from_max(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("p_001", 0))
        g.add_node(_node("p_005", 0))
        assert next_id(g, "p") == "p_006"

    def test_next_id_ignores_other_prefixes(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("c_001", 1))
        g.add_node(_node("c_002", 1))
        assert next_id(g, "p") == "p_001"
        assert next_id(g, "c") == "c_003"

    def test_next_edge_id_empty(self) -> None:
        g = ExplanationGraph(root_question="q")
        assert next_edge_id(g) == "e_001"

    def test_next_edge_id_increments(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("c_001", 1))
        g.add_node(_node("p_001", 0))
        g.add_edge(RelationEdge(
            id="e_005", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.7,
            mechanism_description="m",
        ))
        assert next_edge_id(g) == "e_006"
