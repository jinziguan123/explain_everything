"""Wave 2 Task 2.1: _propagation.rollout_from_roots 单元测试.

design §5.3.1: 从 L2 root 沿 causes ↓ manifests_as ↓ BFS, 收集 reachable L0.
"""

from explain_engine.engines._propagation import rollout_from_roots
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid: str, level: int) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7,
        epistemic="observation" if level == 0 else "insight",
    )


def _edge(eid: str, src: str, tgt: str, rel: str = "manifests_as", conf: float = 0.7) -> RelationEdge:
    return RelationEdge(
        id=eid, source_node=src, target_node=tgt,
        relation_type=rel, confidence=conf, mechanism_description="m",
    )


class TestRolloutFromRoots:
    def test_full_chain_l2_to_l1_to_l0_all_reachable(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("d_001", 2))
        g.add_node(_node("c_001", 1))
        g.add_node(_node("p_001", 0))
        g.add_edge(_edge("e_001", "d_001", "c_001", "causes"))
        g.add_edge(_edge("e_002", "c_001", "p_001", "manifests_as"))

        reachable, missing = rollout_from_roots(g)
        assert reachable == {"p_001"}
        assert missing == set()

    def test_disconnected_l0_in_missing(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("d_001", 2))
        g.add_node(_node("c_001", 1))
        g.add_node(_node("p_001", 0))
        g.add_node(_node("p_002", 0))   # 孤立 L0
        g.add_edge(_edge("e_001", "d_001", "c_001", "causes"))
        g.add_edge(_edge("e_002", "c_001", "p_001", "manifests_as"))

        reachable, missing = rollout_from_roots(g)
        assert reachable == {"p_001"}
        assert missing == {"p_002"}

    def test_no_l2_falls_back_to_l1_as_roots(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("c_001", 1))
        g.add_node(_node("p_001", 0))
        g.add_edge(_edge("e_001", "c_001", "p_001", "manifests_as"))

        reachable, missing = rollout_from_roots(g)
        assert reachable == {"p_001"}
        assert missing == set()

    def test_empty_graph_returns_empty(self) -> None:
        g = ExplanationGraph(root_question="q")
        reachable, missing = rollout_from_roots(g)
        assert reachable == set()
        assert missing == set()

    def test_handles_cycle_without_infinite_loop(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("d_001", 2))
        g.add_node(_node("c_001", 1))
        g.add_node(_node("c_002", 1))
        g.add_node(_node("p_001", 0))
        g.add_edge(_edge("e_001", "d_001", "c_001", "causes"))
        # 人造小循环 c_001 → c_002 → c_001
        g.add_edge(_edge("e_002", "c_001", "c_002", "manifests_as"))
        g.add_edge(_edge("e_003", "c_002", "c_001", "causes"))
        g.add_edge(_edge("e_004", "c_001", "p_001", "manifests_as"))

        reachable, missing = rollout_from_roots(g)
        assert reachable == {"p_001"}
        assert missing == set()

    def test_skips_decayed_nodes_when_present(self) -> None:
        """Wave 4 Task 4.2 启用: lifecycle_state == decayed 不参与 rollout."""
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("d_001", 2))
        g.add_node(VariableNode(
            id="c_001", name="c_001", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
            lifecycle_state="decayed",
        ))
        g.add_node(_node("p_001", 0))
        g.add_edge(_edge("e_001", "d_001", "c_001", "causes"))
        g.add_edge(_edge("e_002", "c_001", "p_001", "manifests_as"))

        reachable, missing = rollout_from_roots(g)
        # c_001 decayed → p_001 不可达 (rollout 路径断)
        assert "p_001" in missing
        assert "p_001" not in reachable
