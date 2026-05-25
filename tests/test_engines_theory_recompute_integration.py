"""Phase 16: _recompute_all 完整 7-step pipeline integration test.

Mock embedder + 3 fake session sidecar, 验跑通 cluster → motif → predict →
promote → rank → TheoriesCache.
"""

import numpy as np


class FakeEmbedder:
    """Mock embedder, encode() 返预设 vector."""

    def __init__(self, name_to_vec):
        self._map = name_to_vec

    def encode(self, names):
        if not names:
            return np.zeros((0, 3))
        return np.stack([self._map.get(n, np.zeros(3)) for n in names])


class TestRecomputeColdStart:
    def test_under_threshold_returns_empty(self, tmp_path, monkeypatch):
        """sessions < cold_start_threshold 直接返 empty cache."""
        from explain_engine.engines.theory.recompute import _recompute_all
        from explain_engine.persistence.storage_v2 import StorageV2

        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        storage = StorageV2()
        embedder = FakeEmbedder({})
        result = _recompute_all(
            sessions=["s_1", "s_2"],  # 2 < max(3, 2//3)=3
            storage=storage,
            embedder=embedder,
            preserve_rejected=set(),
        )
        assert result.cold_start_threshold == 3
        assert result.tentative_theories == []
        assert result.stable_theories == []
        assert result.session_ids_snapshot == ["s_1", "s_2"]


class TestRecomputePreservesRejected:
    def test_rejected_passed_through(self, tmp_path, monkeypatch):
        from explain_engine.engines.theory.recompute import _recompute_all
        from explain_engine.persistence.storage_v2 import StorageV2

        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        storage = StorageV2()
        embedder = FakeEmbedder({})
        preserved = {"t_old_reject"}
        result = _recompute_all(
            sessions=["s_1"],
            storage=storage,
            embedder=embedder,
            preserve_rejected=preserved,
        )
        assert result.rejected_theory_ids == {"t_old_reject"}
