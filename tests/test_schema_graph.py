"""ExplanationGraph (networkx 包装) test."""

import pytest

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(id_: str, level: int = 0, epi: str = "observation") -> VariableNode:
    return VariableNode(
        id=id_,
        name=id_,
        description=id_,
        abstraction_level=level,  # type: ignore[arg-type]
        confidence=0.5,
        epistemic=epi,  # type: ignore[arg-type]
    )


def _edge(eid: str, src: str, tgt: str, rt: str = "manifests_as") -> RelationEdge:
    return RelationEdge(
        id=eid,
        source_node=src,
        target_node=tgt,
        relation_type=rt,  # type: ignore[arg-type]
        confidence=0.5,
        mechanism_description="x",
    )


class TestExplanationGraph:
    def test_empty_graph(self):
        g = ExplanationGraph(root_question="why?")
        assert g.nodes == {}
        assert g.edges == {}
        assert g.compression_score() == 0.0
        assert g.coverage_score() == 0.0

    def test_add_node(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_001"))
        assert "n_001" in g.nodes

    def test_add_duplicate_node_rejected(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_001"))
        with pytest.raises(ValueError, match="already exists"):
            g.add_node(_node("n_001"))

    def test_add_edge(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_abs", level=2))
        g.add_node(_node("n_con", level=0))
        g.add_edge(_edge("e_001", "n_abs", "n_con"))
        assert "e_001" in g.edges

    def test_add_edge_unknown_node_rejected(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_001"))
        with pytest.raises(ValueError, match="unknown node"):
            g.add_edge(_edge("e_001", "n_001", "n_missing"))

    def test_compression_score_one_abstract_covers_three(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_abs", level=2))
        for i in range(3):
            cid = f"n_con_{i}"
            g.add_node(_node(cid))
            g.add_edge(_edge(f"e_{i}", "n_abs", cid))
        # 1 个 abstract 覆盖 3 个 concrete = 压缩 3
        assert g.compression_score() == 3.0

    def test_coverage_score_partial(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_abs", level=2))
        for i in range(4):
            g.add_node(_node(f"n_con_{i}"))
        # 只有 2 条边
        g.add_edge(_edge("e_0", "n_abs", "n_con_0"))
        g.add_edge(_edge("e_1", "n_abs", "n_con_1"))
        # 4 个 concrete 中 2 个被覆盖
        assert g.coverage_score() == 0.5

    def test_frontier_returns_abstract_no_outgoing(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_abs_isolated", level=2))
        g.add_node(_node("n_abs_used", level=2))
        g.add_node(_node("n_con"))
        g.add_edge(_edge("e", "n_abs_used", "n_con"))
        # n_abs_used 已经有 outgoing，不是 frontier
        # n_abs_isolated 没有 outgoing，是 frontier
        assert g.frontier() == ["n_abs_isolated"]

    def test_serialize_round_trip(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_abs", level=2))
        g.add_node(_node("n_con"))
        g.add_edge(_edge("e", "n_abs", "n_con"))
        d = g.to_dict()
        restored = ExplanationGraph.from_dict(d)
        assert restored.nodes == g.nodes
        assert restored.edges == g.edges
        assert restored.root_question == g.root_question

    def test_compression_score_counts_mid_level_too(self):
        """abstraction_level=1 (mid) 节点也参与 compression."""
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_mid", level=1))
        g.add_node(_node("n_con_a"))
        g.add_node(_node("n_con_b"))
        g.add_edge(_edge("e_1", "n_mid", "n_con_a"))
        g.add_edge(_edge("e_2", "n_mid", "n_con_b"))
        # mid 节点应该被 compression 计入
        assert g.compression_score() == 2.0
