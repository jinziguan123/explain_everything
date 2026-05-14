"""ExplanationGraph.frontier_nodes() — Phase 5 expansion 起点识别。

返 abstraction_level == 1 且没有 incoming causes edge 的节点 id list。
排序: 按 node id 字符串升序。
"""

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid: str, level: int) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7, epistemic="insight",
    )


class TestFrontierNodes:
    def test_empty_graph(self) -> None:
        g = ExplanationGraph(root_question="why")
        assert g.frontier_nodes() == []

    def test_only_concrete(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("p_001", 0))
        g.add_node(_node("p_002", 0))
        # level 0 不是 frontier
        assert g.frontier_nodes() == []

    def test_abstract_no_incoming(self) -> None:
        """c_001 是 abstract 且没有 incoming causes → 是 frontier。"""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("p_001", 0))
        g.add_node(_node("c_001", 1))
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.7, mechanism_description="m",
        ))
        # c_001 有 outgoing manifests_as 但没 incoming causes
        assert g.frontier_nodes() == ["c_001"]

    def test_abstract_with_incoming_causes_excluded(self) -> None:
        """c_001 已经被 d_001 通过 causes 解释 → 不是 frontier。"""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001", 1))
        g.add_node(_node("d_001", 2))
        g.add_edge(RelationEdge(
            id="e_001", source_node="d_001", target_node="c_001",
            relation_type="causes", confidence=0.7, mechanism_description="m",
        ))
        # c_001 有 incoming causes，d_001 是 level=2（Phase 5 cap，不算 frontier）
        assert g.frontier_nodes() == []

    def test_only_level_1_returned(self) -> None:
        """Phase 5 cap: 即使 d_NNN (level=2) 没 incoming causes，也不算 frontier。"""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001", 1))
        g.add_node(_node("d_001", 2))
        # d_001 没有 incoming causes 但 level=2 → 不算 frontier
        assert g.frontier_nodes() == ["c_001"]

    def test_multiple_frontiers_sorted(self) -> None:
        g = ExplanationGraph(root_question="why")
        for cid in ("c_003", "c_001", "c_002"):
            g.add_node(_node(cid, 1))
        # 3 个 abstract 都没 incoming causes，按 id 升序
        assert g.frontier_nodes() == ["c_001", "c_002", "c_003"]

    def test_incoming_manifests_as_not_excluding(self) -> None:
        """abstract 有 incoming manifests_as 不影响 frontier 判定（manifests_as 不算 cause）。"""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001", 1))
        g.add_node(_node("c_002", 1))
        # 极端 case: 两个 abstract 之间有 manifests_as 边
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="c_002",
            relation_type="manifests_as", confidence=0.7, mechanism_description="m",
        ))
        # c_002 有 incoming manifests_as 但不是 causes → 仍 frontier
        assert g.frontier_nodes() == ["c_001", "c_002"]
