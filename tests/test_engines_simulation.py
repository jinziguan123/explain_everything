"""SimulationEngine API tests."""

import pytest

from explain_engine.engines.simulation import (
    ConsistencyReport,
    check_consistency,
    check_consistency_batch,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


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


def _make_state() -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(_node("p_001", level=0))
    g.add_node(_node("p_002", level=0))
    g.add_node(_node("c_001", level=1))
    g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.7))
    g.add_edge(_edge("e_002", "c_001", "p_002", conf=0.7))
    return CognitiveState(graph=g, budget_remaining=0, root_question="why")


class TestCheckConsistencyValidation:
    def test_target_not_in_graph_raises(self) -> None:
        state = _make_state()
        with pytest.raises(ValueError, match=r"not found in graph"):
            check_consistency(state, "nonexistent")

    def test_target_level_0_raises(self) -> None:
        state = _make_state()
        with pytest.raises(ValueError, match=r"level=0"):
            check_consistency(state, "p_001")


class TestC1ConsistencyScore:
    def test_score_is_mean_over_reachable_L0(self) -> None:
        state = _make_state()
        report = check_consistency(state, "c_001")
        # p_001/p_002 各 0.7, mean = 0.7
        assert sorted(report.reachable_L0) == ["p_001", "p_002"]
        assert abs(report.consistency_score - 0.7) < 1e-9

    def test_score_zero_when_no_reachable_L0(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
        report = check_consistency(state, "c_001")
        assert report.consistency_score == 0.0
        assert report.reachable_L0 == []
        assert report.weak_chains == []

    def test_weak_chains_below_threshold(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("c_002"))
        g.add_node(_node("p_strong", level=0))
        g.add_node(_node("p_weak", level=0))
        g.add_edge(_edge("e_001", "c_001", "c_002", conf=0.3))   # depth 0
        g.add_edge(_edge("e_002", "c_002", "p_weak", conf=0.3))  # depth 1: 0.09 < 0.15
        g.add_edge(_edge("e_003", "c_001", "p_strong", conf=0.8))  # 0.8

        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
        report = check_consistency(state, "c_001")
        assert "p_weak" in report.weak_chains
        assert "p_strong" not in report.weak_chains

    def test_score_one_when_all_paths_perfect(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=1.0))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
        report = check_consistency(state, "c_001")
        assert abs(report.consistency_score - 1.0) < 1e-9


class TestC2Essentialness:
    def test_high_when_target_unique_explainer(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.9))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
        report = check_consistency(state, "c_001")
        # essentialness = 0.9 / 1 = 0.9
        assert abs(report.essentialness_score - 0.9) < 1e-9
        assert abs(report.contribution_breakdown["p_001"] - 0.9) < 1e-9

    def test_zero_when_target_fully_redundant(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("c_002"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.7))
        g.add_edge(_edge("e_002", "c_002", "p_001", conf=0.7))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
        # baseline p_001 = 0.91, without c_001 → 0.7, contribution = 0.21
        report = check_consistency(state, "c_001")
        assert abs(report.essentialness_score - 0.21) < 1e-2

    def test_contribution_per_concrete(self) -> None:
        state = _make_state()
        report = check_consistency(state, "c_001")
        assert set(report.contribution_breakdown.keys()) == {"p_001", "p_002"}
        # Invariant: contribution clipped to [0, +∞) — see _check_with_baseline
        # docstring for rationale (MAX_ACTIVE pruning artifact handling)
        for v in report.contribution_breakdown.values():
            assert v >= 0

    def test_essentialness_non_negative_when_max_active_pruning_engages(
        self, monkeypatch,
    ) -> None:
        """MAX_ACTIVE=3 时 baseline 剪掉某 L0, without_target 保留它, 不应
        produce 负 contribution (clip 到 0)."""
        monkeypatch.setattr(
            "explain_engine.engines._propagation.MAX_ACTIVE_VARIABLES",
            3,
        )
        g = ExplanationGraph(root_question="why")
        # 5 个 L1 各自指向唯一 L0, 不同 confidence
        # baseline propagate(5 sources) 时 5 candidates → 剪到 top 3 (按 confidence)
        # without target=c_001 时 4 candidates → 剪到 top 3, 但保留的子集不同
        for i, conf in enumerate([0.9, 0.8, 0.7, 0.6, 0.5]):
            g.add_node(_node(f"c_{i:03d}"))
            g.add_node(_node(f"p_{i:03d}", level=0))
            g.add_edge(_edge(f"e_{i:03d}", f"c_{i:03d}", f"p_{i:03d}", conf=conf))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")

        report = check_consistency(state, "c_000")
        # 关键 assertion: 所有 contribution 都 >= 0 (clip), essentialness >= 0
        for nid, v in report.contribution_breakdown.items():
            assert v >= 0, (
                f"contribution[{nid}]={v} < 0 — MAX_ACTIVE pruning "
                f"reshuffle 应被 clip"
            )
        assert report.essentialness_score >= 0


class TestCheckConsistencyBatch:
    def test_batch_default_includes_all_L1_L2(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("p_001", level=0))
        for cid in ("c_001", "c_002", "c_003"):
            g.add_node(_node(cid, level=1))
            g.add_edge(_edge(f"e_{cid}", cid, "p_001", conf=0.7))
        g.add_node(_node("d_001", level=2))
        g.add_edge(_edge("e_d_c", "d_001", "c_001", rel="causes", conf=0.6))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")

        reports = check_consistency_batch(state)
        target_ids = [r.target_id for r in reports]
        assert target_ids == ["c_001", "c_002", "c_003", "d_001"]

    def test_batch_explicit_target_ids(self) -> None:
        state = _make_state()
        reports = check_consistency_batch(state, ["c_001"])
        assert len(reports) == 1
        assert reports[0].target_id == "c_001"

    def test_batch_empty_target_ids_returns_empty(self) -> None:
        state = _make_state()
        assert check_consistency_batch(state, []) == []

    def test_batch_no_L1_L2_in_graph_returns_empty(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("p_001", level=0))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
        assert check_consistency_batch(state) == []

    def test_batch_fail_fast_on_invalid_target(self) -> None:
        state = _make_state()
        with pytest.raises(ValueError, match=r"not found"):
            check_consistency_batch(state, ["c_001", "nonexistent"])

    def test_batch_baseline_shared_optimization(self, mocker) -> None:
        """Batch N target 应该只算 1 次 baseline, 总 1+2N propagation."""
        from explain_engine.engines import simulation as sim_mod
        spy = mocker.spy(sim_mod, "propagate")

        g = ExplanationGraph(root_question="why")
        g.add_node(_node("p_001", level=0))
        for cid in ("c_001", "c_002", "c_003"):
            g.add_node(_node(cid, level=1))
            g.add_edge(_edge(f"e_{cid}", cid, "p_001", conf=0.7))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")

        check_consistency_batch(state)
        # N=3: 1 baseline + 3 C₁ + 3 without = 7 propagations
        assert spy.call_count == 7


class TestDecayTraceContent:
    def test_returns_c1_trace_only_not_c2(self) -> None:
        state = _make_state()
        report = check_consistency(state, "c_001")
        # C₁ propagate {c_001}: 2 step (c_001 → p_001, c_001 → p_002)
        assert len(report.decay_trace) == 2
        for step in report.decay_trace:
            assert step.src == "c_001"


def test_consistency_report_is_dataclass_with_expected_fields() -> None:
    """Smoke test: ConsistencyReport dataclass shape."""
    state = _make_state()
    report = check_consistency(state, "c_001")
    assert isinstance(report, ConsistencyReport)
    assert isinstance(report.target_id, str)
    assert isinstance(report.consistency_score, float)
    assert isinstance(report.reachable_L0, list)
    assert isinstance(report.weak_chains, list)
    assert isinstance(report.essentialness_score, float)
    assert isinstance(report.contribution_breakdown, dict)
    assert isinstance(report.decay_trace, list)
