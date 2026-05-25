"""Phase 16 JEPA (b)(c): ranking + promote stable."""

from explain_engine.engines.theory.theory import Theory


def _make_theory(id="t1", supporting=("s_1",), predictive=0.5,
                 theme_ids=("th_001",), complexity=3):
    return Theory(
        id=id, motif_type="chain",
        theme_ids=theme_ids, node_ids=("v_a", "v_b"),
        edges=(("v_a", "v_b", "causes"),),
        supporting_sessions=supporting,
        natural_language_summary="...",
        structure_complexity=complexity,
        first_seen_session=supporting[0], last_seen_session=supporting[-1],
        predictive_power=predictive,
    )


class TestComputeScore:
    def test_predictive_power_weighted_most(self):
        from explain_engine.engines.theory.ranking import compute_score
        t1 = _make_theory(supporting=("s_1", "s_2"), predictive=1.0)
        t2 = _make_theory(supporting=("s_1", "s_2"), predictive=0.0)
        assert compute_score(t1, 10) > compute_score(t2, 10)


class TestMmrRanking:
    def test_diversity_penalty(self):
        from explain_engine.engines.theory.ranking import rank_topk_with_mmr
        # 2 same-theme theory, 1 different-theme
        t1 = _make_theory(id="t1", theme_ids=("th_001",),
                          predictive=0.9, supporting=("s_1",))
        t2 = _make_theory(id="t2", theme_ids=("th_001",),
                          predictive=0.85, supporting=("s_1",))
        t3 = _make_theory(id="t3", theme_ids=("th_002",),
                          predictive=0.8, supporting=("s_1",))
        ranked = rank_topk_with_mmr([t1, t2, t3], k=2, lambda_=0.5,
                                     n_sessions_total=10)
        # 高 score 的 t1 一定在; 第二个应是 t3 (不同 theme, 防 paraphrase)
        ids = [t.id for t in ranked]
        assert ids[0] == "t1"
        assert ids[1] == "t3"


class TestPromoteStable:
    def test_promote_if_in_recent_window(self):
        from explain_engine.engines.theory.ranking import maybe_promote_to_stable
        # window=5, theory 在最近 5 session 中 3 个有出现 → stable (5//2+1 = 3)
        sessions = ["s_1", "s_2", "s_3", "s_4", "s_5"]
        t = _make_theory(supporting=("s_2", "s_3", "s_5"))
        assert maybe_promote_to_stable(t, sessions, window_size=5) is True

    def test_not_promote_if_too_few(self):
        from explain_engine.engines.theory.ranking import maybe_promote_to_stable
        sessions = ["s_1", "s_2", "s_3", "s_4", "s_5"]
        t = _make_theory(supporting=("s_2",))  # only 1 in window
        assert maybe_promote_to_stable(t, sessions, window_size=5) is False
