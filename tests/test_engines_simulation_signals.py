"""Wave 2 Task 2.2: simulation.aggregate_acceptance + AcceptanceReport.

design §5.2: 聚合 ConsistencyReport (per-target) + rollout_from_roots
形成 multi-signal AcceptanceReport.
"""

from explain_engine.engines.simulation import (
    AcceptanceReport,
    aggregate_acceptance,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _node(nid, level, conf=0.7):
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=conf,
        epistemic="observation" if level == 0 else "insight",
    )


def _edge(eid, src, tgt, rel="manifests_as", conf=0.7):
    return RelationEdge(
        id=eid, source_node=src, target_node=tgt,
        relation_type=rel, confidence=conf, mechanism_description="m",
    )


def _make_state(nodes_levels: list[tuple[str, int]],
                edges: list[tuple[str, str, str, str, float]]) -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    for nid, lv in nodes_levels:
        g.add_node(_node(nid, lv))
    for eid, src, tgt, rel, conf in edges:
        g.add_edge(_edge(eid, src, tgt, rel, conf))
    return CognitiveState(graph=g, budget_remaining=10, root_question="q")


class TestAcceptanceReport:
    def test_dataclass_default_fields(self) -> None:
        r = AcceptanceReport(
            avg_consistency=0.5, avg_essentialness=0.3,
            per_l1={"c_001": 0.5}, per_l2={"d_001": 0.3},
        )
        assert r.weak_chain_l1s == []
        assert r.lowest_l1 is None
        assert r.consistency_spread == 0.0
        assert r.essentialness_spread == 0.0
        assert r.rollout_coverage == 1.0
        assert r.missing_l0 == []
        assert r.input_alignment is None
        assert r.falsifiable_reason is None


class TestAggregateAcceptance:
    def test_weak_chain_l1s_lists_below_threshold(self) -> None:
        # 一条强链 + 一条弱链 (低 conf edge)
        state = _make_state(
            nodes_levels=[("c_001", 1), ("c_002", 1), ("p_001", 0), ("p_002", 0)],
            edges=[
                ("e_001", "c_001", "p_001", "manifests_as", 0.9),
                ("e_002", "c_002", "p_002", "manifests_as", 0.1),  # 弱
            ],
        )
        report = aggregate_acceptance(state)
        # c_002 consistency 应该 < 0.5 (LOW_CONSISTENCY_THRESHOLD)
        assert "c_002" in report.weak_chain_l1s
        assert "c_001" not in report.weak_chain_l1s

    def test_lowest_l1_returns_argmin(self) -> None:
        state = _make_state(
            nodes_levels=[("c_001", 1), ("c_002", 1), ("p_001", 0), ("p_002", 0)],
            edges=[
                ("e_001", "c_001", "p_001", "manifests_as", 0.9),
                ("e_002", "c_002", "p_002", "manifests_as", 0.2),
            ],
        )
        report = aggregate_acceptance(state)
        assert report.lowest_l1 is not None
        assert report.lowest_l1[0] == "c_002"

    def test_lowest_l1_empty_l1_returns_none(self) -> None:
        state = _make_state(nodes_levels=[("p_001", 0)], edges=[])
        report = aggregate_acceptance(state)
        assert report.lowest_l1 is None

    def test_consistency_spread_max_minus_min(self) -> None:
        state = _make_state(
            nodes_levels=[("c_001", 1), ("c_002", 1), ("p_001", 0), ("p_002", 0)],
            edges=[
                ("e_001", "c_001", "p_001", "manifests_as", 0.9),
                ("e_002", "c_002", "p_002", "manifests_as", 0.1),
            ],
        )
        report = aggregate_acceptance(state)
        assert report.consistency_spread > 0.0

    def test_essentialness_spread_single_l2_zero(self) -> None:
        # 单 L2 → spread = 0 (max == min)
        state = _make_state(
            nodes_levels=[("d_001", 2), ("c_001", 1), ("p_001", 0)],
            edges=[
                ("e_001", "d_001", "c_001", "causes", 0.7),
                ("e_002", "c_001", "p_001", "manifests_as", 0.7),
            ],
        )
        report = aggregate_acceptance(state)
        assert report.essentialness_spread == 0.0

    def test_rollout_coverage_full_chain(self) -> None:
        state = _make_state(
            nodes_levels=[("d_001", 2), ("c_001", 1), ("p_001", 0)],
            edges=[
                ("e_001", "d_001", "c_001", "causes", 0.7),
                ("e_002", "c_001", "p_001", "manifests_as", 0.7),
            ],
        )
        report = aggregate_acceptance(state)
        assert report.rollout_coverage == 1.0
        assert report.missing_l0 == []

    def test_rollout_coverage_partial(self) -> None:
        state = _make_state(
            nodes_levels=[("c_001", 1), ("p_001", 0), ("p_002", 0)],
            edges=[("e_001", "c_001", "p_001", "manifests_as", 0.7)],
        )
        report = aggregate_acceptance(state)
        assert report.rollout_coverage == 0.5
        assert report.missing_l0 == ["p_002"]

    def test_empty_graph_returns_safe_defaults(self) -> None:
        state = _make_state(nodes_levels=[], edges=[])
        report = aggregate_acceptance(state)
        assert report.avg_consistency == 0.0
        assert report.weak_chain_l1s == []
        assert report.lowest_l1 is None
        assert report.rollout_coverage == 1.0  # 无 L0 → trivially 1.0
