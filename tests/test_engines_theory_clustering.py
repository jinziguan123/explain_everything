"""Phase 16: cluster_lexicon_themes — Phase 13 embedding cosine clustering."""

import numpy as np


def _fake_lexicon(vars_data):
    return {"version": "1.0", "variables": vars_data}


class TestClusterLexiconThemes:
    def test_empty_lexicon_returns_empty(self):
        from explain_engine.engines.theory.clustering import cluster_lexicon_themes
        themes = cluster_lexicon_themes(_fake_lexicon([]), embedder=None)
        assert themes == []

    def test_single_var_returns_empty(self):
        from explain_engine.engines.theory.clustering import cluster_lexicon_themes
        lex = _fake_lexicon([{"global_id": "v_a", "name": "A",
                              "embedding": [1.0, 0.0, 0.0]}])
        themes = cluster_lexicon_themes(lex, embedder=None)
        assert themes == []  # < 2 var 无法 cluster

    def test_two_similar_vars_form_one_theme(self):
        from explain_engine.engines.theory.clustering import cluster_lexicon_themes
        # 2 highly similar vec (cos > 0.9)
        v1 = np.array([1.0, 0.0, 0.0])
        v2 = np.array([0.95, 0.31, 0.0])
        v2 = (v2 / np.linalg.norm(v2)).tolist()
        lex = _fake_lexicon([
            {"global_id": "v_a", "name": "A", "embedding": v1.tolist()},
            {"global_id": "v_b", "name": "B", "embedding": v2},
        ])
        themes = cluster_lexicon_themes(lex, embedder=None, cosine_threshold=0.85)
        assert len(themes) == 1
        assert set(themes[0].member_global_ids) == {"v_a", "v_b"}

    def test_dissimilar_vars_form_no_theme(self):
        from explain_engine.engines.theory.clustering import cluster_lexicon_themes
        v1 = np.array([1.0, 0.0, 0.0])
        v2 = np.array([0.0, 1.0, 0.0])  # cos=0
        lex = _fake_lexicon([
            {"global_id": "v_a", "name": "A", "embedding": v1.tolist()},
            {"global_id": "v_b", "name": "B", "embedding": v2.tolist()},
        ])
        themes = cluster_lexicon_themes(lex, embedder=None, cosine_threshold=0.85)
        # 每 var 单独 1 cluster, 但 cluster size < 2 应被过滤 → 空
        assert themes == []

    def test_theme_name_taken_from_cluster_member(self):
        from explain_engine.engines.theory.clustering import cluster_lexicon_themes
        v1 = np.array([1.0, 0.0, 0.0])
        v2 = np.array([0.95, 0.31, 0.0])
        v2 = (v2 / np.linalg.norm(v2)).tolist()
        lex = _fake_lexicon([
            {"global_id": "v_a", "name": "中心点", "embedding": v1.tolist()},
            {"global_id": "v_b", "name": "外围点", "embedding": v2},
        ])
        themes = cluster_lexicon_themes(lex, embedder=None, cosine_threshold=0.85)
        # name 是 cluster 内某成员名 (centroid 距离最近者), 必为这 2 个之一
        assert themes[0].name in ("中心点", "外围点")
