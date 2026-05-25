"""Phase 16 JEPA (a): leave-one-session-out predictive_power."""

import numpy as np

from explain_engine.engines.theory.motif_mining import RawMotif


class FakeEmbedder:
    """Mock embedder, encode() 返预设 vector."""
    def __init__(self, name_to_vec: dict[str, np.ndarray]):
        self._map = name_to_vec

    def encode(self, names):
        return np.stack([self._map.get(n, np.zeros(3)) for n in names])


def _fake_l0_node(name: str):
    """Mock L0 node (有 .name + .abstraction_level=0)."""
    class N:
        pass
    n = N()
    n.name = name
    n.abstraction_level = 0
    return n


def _fake_session_with_l0(l0_names):
    """Mock graph with given L0 phenomena."""
    class G:
        def __init__(self, nodes):
            self.nodes = {f"l0_{i}": _fake_l0_node(name) for i, name in enumerate(nodes)}
    return G(l0_names)


class TestEvaluatePredictivePower:
    def test_supporting_less_than_2_returns_zero(self):
        from explain_engine.engines.theory.falsifiability import evaluate_predictive_power
        motif = RawMotif("chain", ("v_a",), (("v_a", "v_b", "causes"),), ("s_1",))
        result = evaluate_predictive_power(motif, {}, embedder=FakeEmbedder({}))
        assert result == 0.0

    def test_perfect_predict_all_supporting_hit(self):
        """3 supporting session, motif node 全 cosine match L0 → 1.0."""
        from explain_engine.engines.theory.falsifiability import evaluate_predictive_power
        v = np.array([1.0, 0.0, 0.0])  # 完全一致 vec
        embedder = FakeEmbedder({
            "v_a": v, "v_b": v,
            "obs_a": v, "obs_b": v, "obs_c": v,
        })
        motif = RawMotif(
            "chain", ("v_a", "v_b"), (("v_a", "v_b", "causes"),),
            ("s_1", "s_2", "s_3"),
        )
        sessions = {
            "s_1": _fake_session_with_l0(["obs_a"]),
            "s_2": _fake_session_with_l0(["obs_b"]),
            "s_3": _fake_session_with_l0(["obs_c"]),
        }
        result = evaluate_predictive_power(motif, sessions, embedder)
        assert result == 1.0  # 全 3 session match (cosine 1.0 ≥ 0.85)

    def test_no_match_returns_zero(self):
        """motif node 跟 held L0 cosine 远小于 threshold → 0.0."""
        from explain_engine.engines.theory.falsifiability import evaluate_predictive_power
        v1 = np.array([1.0, 0.0, 0.0])
        v2 = np.array([0.0, 1.0, 0.0])  # cos=0
        embedder = FakeEmbedder({
            "v_a": v1, "v_b": v1,
            "obs_a": v2, "obs_b": v2,
        })
        motif = RawMotif(
            "chain", ("v_a", "v_b"), (("v_a", "v_b", "causes"),),
            ("s_1", "s_2"),
        )
        sessions = {
            "s_1": _fake_session_with_l0(["obs_a"]),
            "s_2": _fake_session_with_l0(["obs_b"]),
        }
        result = evaluate_predictive_power(motif, sessions, embedder)
        assert result == 0.0

    def test_partial_match_returns_ratio(self):
        """2 supporting, 1 hit 1 miss → 0.5."""
        from explain_engine.engines.theory.falsifiability import evaluate_predictive_power
        v_match = np.array([1.0, 0.0, 0.0])
        v_nomatch = np.array([0.0, 1.0, 0.0])
        embedder = FakeEmbedder({
            "v_a": v_match,
            "obs_match": v_match,    # cos 1.0
            "obs_nomatch": v_nomatch,  # cos 0
        })
        motif = RawMotif(
            "chain", ("v_a",), (("v_a", "v_b", "causes"),),
            ("s_1", "s_2"),
        )
        sessions = {
            "s_1": _fake_session_with_l0(["obs_match"]),  # hit
            "s_2": _fake_session_with_l0(["obs_nomatch"]),  # miss
        }
        result = evaluate_predictive_power(motif, sessions, embedder)
        assert result == 0.5
