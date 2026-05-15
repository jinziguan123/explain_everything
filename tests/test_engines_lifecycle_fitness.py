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
        """同 graph 内, 同 outgoing-degree 节点, 但 redundancy 不同 → fitness 不同.

        Isolation: equalize centrality so the only differentiator is redundancy.
        """
        # c_redundant + c_dup1 + c_dup2 都指向 p_shared (3 redundant siblings)
        # c_unique 指向 p_other (no siblings)
        # 4 个 L1 + 2 个 L0, 所有 L1 都有 1 outgoing edge → centrality 相同
        c_red = _node("c_red", 1, activation=1.0, stability=0.5)
        c_dup1 = _node("c_dup1", 1, activation=1.0, stability=0.5)
        c_dup2 = _node("c_dup2", 1, activation=1.0, stability=0.5)
        c_unique = _node("c_unique", 1, activation=1.0, stability=0.5)
        p_shared = _node("p_shared", 0)
        p_other = _node("p_other", 0)
        state = _state_with(
            [c_red, c_dup1, c_dup2, c_unique, p_shared, p_other],
            edges=[
                ("e_001", "c_red", "p_shared", "manifests_as", 0.7),
                ("e_002", "c_dup1", "p_shared", "manifests_as", 0.7),
                ("e_003", "c_dup2", "p_shared", "manifests_as", 0.7),
                ("e_004", "c_unique", "p_other", "manifests_as", 0.7),
            ],
        )

        # Same acceptance report for all → equal explanatory
        from explain_engine.engines.simulation import AcceptanceReport
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=0.5, avg_essentialness=0.5,
            per_l1={"c_red": 0.5, "c_dup1": 0.5, "c_dup2": 0.5, "c_unique": 0.5},
        )

        f_red = compute_fitness(state.graph.nodes["c_red"], state)
        f_unique = compute_fitness(state.graph.nodes["c_unique"], state)

        # Both have 1 outgoing edge, but:
        # - c_red has 2 siblings with same target set {p_shared} → redundancy 0.4
        # - c_unique has 0 siblings with same target set {p_other} → redundancy 0.0
        # Centrality may differ slightly (p_shared has 3 incoming vs p_other 1),
        # but L1 nodes themselves all have degree=1 outgoing.
        # The redundancy term must dominate.
        assert f_red < f_unique
        # Stronger: gap should be at least the redundancy delta (0.4)
        # minus any centrality compensation. Verify gap is meaningful.
        assert (f_unique - f_red) >= 0.2  # roughly redundancy 0.4 - centrality compensation
