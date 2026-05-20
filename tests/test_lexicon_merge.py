"""Phase 13 Wave 2 Task 1: lexicon_merge.find_duplicate cosine logic."""

import numpy as np


class TestFindDuplicate:
    def test_empty_matrix_returns_none(self):
        from explain_engine.engines.lexicon_merge import find_duplicate
        empty = np.zeros((0, 1024), dtype=np.float32)
        new = np.random.randn(1024).astype(np.float32)
        assert find_duplicate(new, empty) is None

    def test_identical_vector_returns_index(self):
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.random.randn(3, 1024).astype(np.float32)
        # New = exact copy of row 1
        idx = find_duplicate(existing[1].copy(), existing)
        assert idx == 1

    def test_orthogonal_vector_returns_none(self):
        """Orthogonal (cos=0) → below 0.85 threshold → None."""
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.array([[1.0, 0.0] + [0.0] * 1022], dtype=np.float32)
        new = np.array([0.0, 1.0] + [0.0] * 1022, dtype=np.float32)
        assert find_duplicate(new, existing) is None

    def test_high_similarity_returns_index(self):
        """cos sim ~0.999 (well above 0.85) → return index."""
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.array([[1.0, 0.0] + [0.0] * 1022], dtype=np.float32)
        # New = small perturbation of existing
        new = np.array([0.999, 0.045] + [0.0] * 1022, dtype=np.float32)
        new = new / np.linalg.norm(new)
        assert find_duplicate(new, existing) == 0

    def test_threshold_boundary_below(self):
        """cos sim slightly below 0.85 → None."""
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.array([[1.0, 0.0] + [0.0] * 1022], dtype=np.float32)
        # construct vec at angle ~32° (cos ≈ 0.848)
        theta = np.arccos(0.84)
        new = np.array(
            [np.cos(theta), np.sin(theta)] + [0.0] * 1022,
            dtype=np.float32,
        )
        assert find_duplicate(new, existing) is None

    def test_threshold_boundary_above(self):
        """cos sim slightly above 0.85 → return index."""
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.array([[1.0, 0.0] + [0.0] * 1022], dtype=np.float32)
        theta = np.arccos(0.86)
        new = np.array(
            [np.cos(theta), np.sin(theta)] + [0.0] * 1022,
            dtype=np.float32,
        )
        assert find_duplicate(new, existing) == 0

    def test_multiple_above_picks_max(self):
        """Multiple entries above threshold → return argmax index."""
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.array([
            [1.0, 0.0] + [0.0] * 1022,
            [0.9, 0.1] + [0.0] * 1022,
        ], dtype=np.float32)
        existing = existing / np.linalg.norm(existing, axis=1, keepdims=True)
        new = existing[0].copy()  # closest to row 0
        assert find_duplicate(new, existing) == 0

    def test_custom_threshold(self):
        """Pass custom threshold (e.g., 0.95) for stricter merge."""
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.array([[1.0, 0.0] + [0.0] * 1022], dtype=np.float32)
        theta = np.arccos(0.86)
        new = np.array(
            [np.cos(theta), np.sin(theta)] + [0.0] * 1022,
            dtype=np.float32,
        )
        # Default 0.85: returns 0; custom 0.95: returns None
        assert find_duplicate(new, existing, threshold=0.95) is None
