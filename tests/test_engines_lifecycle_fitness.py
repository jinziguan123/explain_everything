"""Wave 4 Task 4.1: lifecycle.compute_fitness 单元测试."""

from explain_engine.engines.lifecycle import compute_fitness
from explain_engine.engines.simulation import AcceptanceReport
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _node(nid, level, **kw):
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7,
        epistemic="observation" if level == 0 else "insight",
        **kw,
    )


def _state_with(nodes, edges=()) -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    for n in nodes:
        g.add_node(n)
    for eid, src, tgt, rel, conf in edges:
        g.add_edge(RelationEdge(
            id=eid, source_node=src, target_node=tgt,
            relation_type=rel, confidence=conf, mechanism_description="m",
        ))
    return CognitiveState(graph=g, budget_remaining=10, root_question="q")


class TestComputeFitness:
    def test_high_consistency_high_fitness(self) -> None:
        n = _node("c_001", 1, activation=1.0, stability=0.5)
        state = _state_with([n])
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=0.8, avg_essentialness=0.5,
            per_l1={"c_001": 0.8},
        )
        f = compute_fitness(n, state)
        assert f > 0.5

    def test_low_activation_lower_fitness(self) -> None:
        n_high = _node("c_001", 1, activation=1.0)
        n_low = _node("c_002", 1, activation=0.1)
        state = _state_with([n_high, n_low])
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=0.5, avg_essentialness=0.5,
            per_l1={"c_001": 0.5, "c_002": 0.5},
        )
        assert compute_fitness(n_high, state) > compute_fitness(n_low, state)

    def test_high_centrality_higher_fitness(self) -> None:
        n_central = _node("c_001", 1)
        n_iso = _node("c_002", 1)
        p = _node("p_001", 0)
        state = _state_with(
            [n_central, n_iso, p],
            edges=[("e_001", "c_001", "p_001", "manifests_as", 0.7)],
        )
        # n_central has 1 outgoing edge, n_iso has none → central fitness higher
        assert compute_fitness(n_central, state) > compute_fitness(n_iso, state)

    def test_no_acceptance_report_uses_default_explanatory(self) -> None:
        n = _node("c_001", 1)
        state = _state_with([n])
        state.last_acceptance_report = None
        f = compute_fitness(n, state)
        # 不抛, 用 0.5 中性默认
        assert isinstance(f, float)
        assert f >= 0.0

    def test_l0_node_uses_default_explanatory(self) -> None:
        n = _node("p_001", 0)
        state = _state_with([n])
        f = compute_fitness(n, state)
        assert f >= 0.0  # L0 不在 per_l1/per_l2, 但仍 compute

    def test_clamps_to_non_negative(self) -> None:
        """高 redundancy 也不会让 fitness 负."""
        n = _node("c_001", 1, activation=0.0, stability=0.0)
        state = _state_with([n])
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=0.0, avg_essentialness=0.0,
            per_l1={"c_001": 0.0},
        )
        f = compute_fitness(n, state)
        assert f >= 0.0

    def test_empty_graph_handles_gracefully(self) -> None:
        """孤立 node + 空 graph (mock case) → 不 crash."""
        n = _node("c_001", 1)
        state = _state_with([n])
        f = compute_fitness(n, state)
        assert isinstance(f, float)

    def test_returns_float(self) -> None:
        n = _node("c_001", 1)
        state = _state_with([n])
        result = compute_fitness(n, state)
        assert isinstance(result, float)

    def test_redundant_siblings_lower_fitness(self) -> None:
        """同 level + 同 outgoing target set → redundancy 加分项扣 fitness."""
        n_unique = _node("c_001", 1, activation=1.0, stability=0.5)
        n_sib1 = _node("c_002", 1, activation=1.0, stability=0.5)
        n_sib2 = _node("c_003", 1, activation=1.0, stability=0.5)
        p = _node("p_001", 0)
        state = _state_with(
            [n_unique, n_sib1, n_sib2, p],
            edges=[
                ("e_001", "c_001", "p_001", "manifests_as", 0.7),
                ("e_002", "c_002", "p_001", "manifests_as", 0.7),  # 与 c_001 同 target
                ("e_003", "c_003", "p_001", "manifests_as", 0.7),  # 与 c_001 同 target
            ],
        )
        # c_002 has 2 siblings with same target ({p_001}) → redundancy 0.4
        # c_001 has 2 siblings with same target ({p_001}) → redundancy 0.4
        # All have same fitness because all are equally redundant.
        # Compare with isolated baseline:
        state2 = _state_with(
            [_node("c_999", 1, activation=1.0, stability=0.5),
             _node("p_999", 0)],
            edges=[("e_001", "c_999", "p_999", "manifests_as", 0.7)],
        )
        f_redundant = compute_fitness(state.graph.nodes["c_001"], state)
        f_unique = compute_fitness(state2.graph.nodes["c_999"], state2)
        # Redundant should be lower (or at least not higher)
        assert f_redundant < f_unique
