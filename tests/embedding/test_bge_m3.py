"""Phase 13 (2026-05-20): BGE_M3_Embedder singleton test.

Tests marked @pytest.mark.embedding (heavy, slow): default skip in CI
via pyproject.toml `addopts = "-m 'not integration and not embedding'"`.
Local dev runs explicitly: `pytest -m embedding`.
"""

import numpy as np
import pytest


class TestBGEM3Embedder:
    pytestmark = pytest.mark.embedding

    def setup_method(self):
        """Reset singleton between tests."""
        from explain_engine.embedding import bge_m3
        bge_m3.BGE_M3_Embedder._instance = None

    def test_get_embedder_returns_singleton(self):
        from explain_engine.embedding.bge_m3 import get_embedder
        e1 = get_embedder()
        e2 = get_embedder()
        assert e1 is e2

    def test_embed_returns_shape(self):
        from explain_engine.embedding.bge_m3 import get_embedder
        e = get_embedder()
        vecs = e.embed(["测试句子 A", "测试句子 B"])
        assert vecs.shape == (2, 1024)
        assert vecs.dtype == np.float32

    def test_embed_single_text(self):
        from explain_engine.embedding.bge_m3 import get_embedder
        e = get_embedder()
        vecs = e.embed(["孤独的一句话"])
        assert vecs.shape == (1, 1024)

    def test_embed_empty_list_returns_empty(self):
        from explain_engine.embedding.bge_m3 import get_embedder
        e = get_embedder()
        vecs = e.embed([])
        assert vecs.shape == (0, 1024)

    def test_device_detection_uses_mps_on_apple(self):
        import torch

        from explain_engine.embedding.bge_m3 import get_embedder
        e = get_embedder()
        expected = "mps" if torch.backends.mps.is_available() else "cpu"
        assert e.device == expected


class TestDisabledEnv:
    """Non-embedding test — runs by default."""

    def setup_method(self):
        from explain_engine.embedding import bge_m3
        bge_m3.BGE_M3_Embedder._instance = None

    def test_disabled_env_short_circuits(self, monkeypatch):
        """EXPLAIN_EMBEDDING_DISABLED=1 → get_embedder raises RuntimeError, callers fall back to string match."""
        monkeypatch.setenv("EXPLAIN_EMBEDDING_DISABLED", "1")
        from explain_engine.embedding.bge_m3 import get_embedder
        with pytest.raises(RuntimeError, match="EXPLAIN_EMBEDDING_DISABLED"):
            get_embedder()
