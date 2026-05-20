"""Phase 13 (2026-05-20): BGE-M3 dense embedding via FlagEmbedding + MPS.

Singleton (process-level) lazy-loaded model. Apple Silicon MPS backend
with fp16 (sweet spot: ~3-5x CPU, cosine precision loss < 0.001).

Disable via env var `EXPLAIN_EMBEDDING_DISABLED=1` for CI / no-GPU env;
callers should catch RuntimeError and fall back to string match.

Note: BGEM3FlagModel with use_fp16=True returns float16 dense_vecs.
We cast to float32 in embed() for downstream numerical stability
(cosine similarity, NaN-resistance, lexicon JSON storage uniformity).
"""

from __future__ import annotations

import os

import numpy as np


class BGE_M3_Embedder:
    """BGE-M3 dense embedding singleton."""

    _instance: BGE_M3_Embedder | None = None

    def __init__(self) -> None:
        if os.environ.get("EXPLAIN_EMBEDDING_DISABLED") == "1":
            raise RuntimeError(
                "EXPLAIN_EMBEDDING_DISABLED=1 — embedding disabled, "
                "caller should fall back to string match path."
            )

        import torch
        from FlagEmbedding import BGEM3FlagModel

        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model = BGEM3FlagModel(
            "BAAI/bge-m3",
            use_fp16=True,
            device=self.device,
        )

    def embed(self, texts: list[str]) -> np.ndarray:
        """Batch encode → (N, 1024) float32 dense embeddings.

        Empty list → (0, 1024) empty array (avoid downstream NaN).
        Model returns float16 (use_fp16=True); we cast to float32 here
        for numerical stability in cosine ops + lexicon JSON storage.
        """
        if not texts:
            return np.zeros((0, 1024), dtype=np.float32)
        out = self.model.encode(texts, return_dense=True)
        vecs = out["dense_vecs"]
        return vecs.astype(np.float32, copy=False)


def get_embedder() -> BGE_M3_Embedder:
    """Lazy singleton accessor. First call loads BGE-M3 ~3-5s (cached).

    Raises RuntimeError if EXPLAIN_EMBEDDING_DISABLED=1 — caller falls back.
    """
    if BGE_M3_Embedder._instance is None:
        BGE_M3_Embedder._instance = BGE_M3_Embedder()
    return BGE_M3_Embedder._instance
