"""Phase 13 Wave 2: cosine similarity merge logic for lexicon dedup."""

from __future__ import annotations

import numpy as np

LEXICON_MERGE_THRESHOLD = 0.85
"""Cosine similarity threshold for merging lexicon entries.

Hard-coded MVP. BGE-M3 中文同义句典型 cosine 0.85+. False merge 风险
低于 0.05 在 manual smoke; 边界 case 由 audit log 事后修正.
"""


def find_duplicate(
    new_emb: np.ndarray,
    existing_matrix: np.ndarray,
    threshold: float = LEXICON_MERGE_THRESHOLD,
) -> int | None:
    """Return existing_matrix row index of max cosine sim if > threshold, else None.

    Args:
        new_emb: shape (1024,), candidate vector
        existing_matrix: shape (N, 1024), N = lexicon entries with embedding
        threshold: cosine cutoff (default 0.85)

    Returns:
        int idx if max(cos) > threshold, else None
    """
    if existing_matrix.shape[0] == 0:
        return None

    # Cosine
    new_norm = np.linalg.norm(new_emb)
    existing_norms = np.linalg.norm(existing_matrix, axis=1)
    denoms = existing_norms * new_norm
    denoms = np.maximum(denoms, 1e-9)  # avoid div0

    sims = (existing_matrix @ new_emb) / denoms
    max_idx = int(np.argmax(sims))
    return max_idx if sims[max_idx] > threshold else None
