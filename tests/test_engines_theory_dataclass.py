"""Phase 16: Theory + Theme dataclass + _compute_theory_id 稳定 hash."""

import dataclasses

import pytest


class TestTheme:
    def test_construct(self):
        from explain_engine.engines.theory.theory import Theme
        t = Theme(id="th_001", name="不确定性",
                  member_global_ids=("v_aaaa", "v_bbbb"),
                  centroid_summary="不确定性 (cluster of 2)")
        assert t.id == "th_001"
        assert len(t.member_global_ids) == 2

    def test_theme_is_frozen(self):
        from explain_engine.engines.theory.theory import Theme
        t = Theme(id="th_001", name="x", member_global_ids=(), centroid_summary="")
        with pytest.raises(dataclasses.FrozenInstanceError):
            t.id = "th_002"  # type: ignore


class TestTheory:
    def test_construct_with_defaults(self):
        from explain_engine.engines.theory.theory import Theory
        t = Theory(
            id="t_aaa", motif_type="chain",
            theme_ids=("th_001", "th_002"), node_ids=("v_a", "v_b"),
            edges=(("v_a", "v_b", "causes"),),
            supporting_sessions=("s_1",),
            natural_language_summary="A → B",
            structure_complexity=2,
            first_seen_session="s_1", last_seen_session="s_1",
        )
        assert t.predictive_power == 0.0          # default
        assert t.stability_status == "tentative"  # default
        assert t.stable_promoted_at_session is None  # default

    def test_compute_theory_id_stable_across_edge_order(self):
        from explain_engine.engines.theory.theory import _compute_theory_id
        edges1 = (("v_a", "v_b", "causes"), ("v_b", "v_c", "manifests_as"))
        edges2 = (("v_b", "v_c", "manifests_as"), ("v_a", "v_b", "causes"))
        assert _compute_theory_id("chain", edges1) == _compute_theory_id("chain", edges2)

    def test_compute_theory_id_differs_motif_type(self):
        from explain_engine.engines.theory.theory import _compute_theory_id
        edges = (("v_a", "v_b", "causes"),)
        assert _compute_theory_id("chain", edges) != _compute_theory_id("star", edges)
