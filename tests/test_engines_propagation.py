"""propagate() 算法 unit tests — table-driven, 0 mock, 0 LLM。"""

import pytest

from explain_engine.engines._propagation import propagate
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid: str, level: int = 1) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7, epistemic="insight",
    )


def _edge(
    eid: str, src: str, dst: str,
    rel: str = "manifests_as", conf: float = 0.7,
) -> RelationEdge:
    return RelationEdge(
        id=eid, source_node=src, target_node=dst,
        relation_type=rel, confidence=conf,
        mechanism_description="m",
    )


class TestPropagationBasics:
    def test_empty_sources_returns_empty(self) -> None:
        g = ExplanationGraph(root_question="why")
        acts, trace = propagate(g, set())
        assert acts == {}
        assert trace == []

    def test_missing_source_raises_value_error(self) -> None:
        g = ExplanationGraph(root_question="why")
        with pytest.raises(ValueError, match=r"sources not in graph"):
            propagate(g, {"nonexistent"})

    def test_source_with_no_outgoing_returns_source_only(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        acts, trace = propagate(g, {"c_001"})
        assert acts == {"c_001": 1.0}
        assert trace == []


class TestSingleEdgePropagation:
    def test_multiplicative_decay(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.7))

        acts, trace = propagate(g, {"c_001"})
        assert acts["c_001"] == 1.0
        assert abs(acts["p_001"] - 0.7) < 1e-9
        assert len(trace) == 1
        step = trace[0]
        assert step.src == "c_001"
        assert step.dst == "p_001"
        assert step.edge_id == "e_001"
        assert step.activation_before == 1.0
        assert step.edge_confidence == 0.7
        assert abs(step.activation_after - 0.7) < 1e-9
        assert step.depth == 0


class TestNoisyOR:
    def test_two_parents_combine_via_noisy_or(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("c_002"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.7))
        g.add_edge(_edge("e_002", "c_002", "p_001", conf=0.5))
        acts, _ = propagate(g, {"c_001", "c_002"})
        # noisy-OR: 1 - (1-0.7)(1-0.5) = 0.85
        assert abs(acts["p_001"] - 0.85) < 1e-9

    def test_three_parents_combine(self) -> None:
        g = ExplanationGraph(root_question="why")
        for cid in ("c_001", "c_002", "c_003"):
            g.add_node(_node(cid))
        g.add_node(_node("p_001", level=0))
        for src, eid, conf in (
            ("c_001", "e_001", 0.5),
            ("c_002", "e_002", 0.5),
            ("c_003", "e_003", 0.5),
        ):
            g.add_edge(_edge(eid, src, "p_001", conf=conf))
        acts, _ = propagate(g, {"c_001", "c_002", "c_003"})
        # noisy-OR: 1 - (1-0.5)^3 = 0.875
        assert abs(acts["p_001"] - 0.875) < 1e-9


class TestMultiHopChain:
    def test_two_hop_decay_d_to_c_to_p(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("d_001", level=2))
        g.add_node(_node("c_001", level=1))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "d_001", "c_001", rel="causes", conf=0.6))
        g.add_edge(_edge("e_002", "c_001", "p_001", rel="manifests_as", conf=0.7))

        acts, trace = propagate(g, {"d_001"})
        assert acts["d_001"] == 1.0
        assert abs(acts["c_001"] - 0.6) < 1e-9
        assert abs(acts["p_001"] - 0.42) < 1e-9
        assert len(trace) == 2
        assert [s.depth for s in trace] == [0, 1]

    def test_three_hop_decay(self) -> None:
        g = ExplanationGraph(root_question="why")
        for nid, lvl in (("a", 2), ("b", 2), ("c", 1), ("d", 0)):
            g.add_node(_node(nid, level=lvl))
        g.add_edge(_edge("e1", "a", "b", rel="causes", conf=0.9))
        g.add_edge(_edge("e2", "b", "c", rel="causes", conf=0.9))
        g.add_edge(_edge("e3", "c", "d", rel="manifests_as", conf=0.9))
        acts, _ = propagate(g, {"a"})
        assert abs(acts["d"] - 0.729) < 1e-9

    def test_cross_layer_merge_combines_direct_and_indirect(self) -> None:
        """同 dst 既被直接 reach 又被 indirect reach, 跨层 merge 用 noisy-OR.

        d → p (direct, conf=0.6) + d → c → p (indirect, 0.5×0.4=0.2)
        合并: 1 - (1-0.6)(1-0.2) = 0.68
        """
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("d", level=2))
        g.add_node(_node("c", level=1))
        g.add_node(_node("p", level=0))
        g.add_edge(_edge("e1", "d", "c", rel="causes", conf=0.5))
        g.add_edge(_edge("e2", "d", "p", rel="manifests_as", conf=0.6))
        g.add_edge(_edge("e3", "c", "p", rel="manifests_as", conf=0.4))

        acts, _ = propagate(g, {"d"})
        # depth 0: c = 0.5 (via e1), p = 0.6 (via e2 direct)
        # depth 1: p gets indirect 0.5 × 0.4 = 0.2, cross-layer noisy-OR:
        #   p = 1 - (1-0.6)(1-0.2) = 0.68
        assert abs(acts["p"] - 0.68) < 1e-9
        assert abs(acts["c"] - 0.5) < 1e-9


class TestConstraints:
    def test_threshold_prunes_weak_propagation(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "explain_engine.engines._propagation.PROPAGATION_THRESHOLD",
            0.5,
        )
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.3))
        acts, trace = propagate(g, {"c_001"})
        assert acts == {"c_001": 1.0}
        assert trace == []

    def test_max_depth_caps_chain(self, monkeypatch) -> None:
        monkeypatch.setattr("explain_engine.engines._propagation.MAX_DEPTH", 2)
        g = ExplanationGraph(root_question="why")
        for i in range(5):
            g.add_node(_node(f"n_{i}"))
        for i in range(4):
            g.add_edge(_edge(f"e_{i}", f"n_{i}", f"n_{i+1}", conf=0.9))
        acts, _ = propagate(g, {"n_0"})
        assert "n_1" in acts
        assert "n_2" in acts
        assert "n_3" not in acts

    def test_max_active_top_k_pruning(self, monkeypatch) -> None:
        monkeypatch.setattr("explain_engine.engines._propagation.MAX_ACTIVE_VARIABLES", 2)
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        confs = [0.9, 0.8, 0.7, 0.6, 0.5]
        for i, conf in enumerate(confs):
            g.add_node(_node(f"p_{i}", level=0))
            g.add_edge(_edge(f"e_{i}", "c_001", f"p_{i}", conf=conf))
        acts, _ = propagate(g, {"c_001"})
        assert "p_0" in acts
        assert "p_1" in acts
        assert "p_2" not in acts
        assert "p_3" not in acts
        assert "p_4" not in acts


class TestEdgeTypeFilter:
    def test_only_causes_and_manifests_as_propagate(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        # amplifies 是 schema 合法 RelationType, 但不在 FORWARD_RELATIONS,
        # 所以不应 propagate (plan 原写 "influences", 但当前 schema 不支持)。
        g.add_edge(_edge("e_001", "c_001", "p_001", rel="amplifies", conf=0.9))
        acts, trace = propagate(g, {"c_001"})
        assert acts == {"c_001": 1.0}
        assert trace == []


class TestCycleHandling:
    def test_cycle_terminates_at_max_depth(self) -> None:
        g = ExplanationGraph(root_question="why")
        for nid in ("a", "b", "c"):
            g.add_node(_node(nid))
        g.add_edge(_edge("e_ab", "a", "b", conf=0.9))
        g.add_edge(_edge("e_bc", "b", "c", conf=0.9))
        g.add_edge(_edge("e_ca", "c", "a", conf=0.9))
        # 完成即 PASS (无死循环)
        acts, _ = propagate(g, {"a"})
        assert "a" in acts
        assert "b" in acts
        assert "c" in acts


class TestMultiSourcePropagation:
    def test_two_sources_simultaneous_starts(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("c_002"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.7))
        g.add_edge(_edge("e_002", "c_002", "p_001", conf=0.5))
        acts, _ = propagate(g, {"c_001", "c_002"})
        assert acts["c_001"] == 1.0
        assert acts["c_002"] == 1.0
        assert abs(acts["p_001"] - 0.85) < 1e-9
