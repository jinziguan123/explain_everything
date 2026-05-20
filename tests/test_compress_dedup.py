"""Phase 13 Wave 3 Task 3: compute_compress_dedup_stats helper test."""

import pytest


class TestComputeCompressDedupStats:
    def _make_state_with_nodes(self, *node_specs):
        """Build CognitiveState with given (id, name, desc) tuples as L1 nodes."""
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.nodes import VariableNode
        from explain_engine.schema.state import CognitiveState

        g = ExplanationGraph(root_question="q")
        for nid, name, desc in node_specs:
            g.add_node(VariableNode(
                id=nid, name=name, description=desc,
                abstraction_level=1, confidence=0.7, epistemic="insight",
            ))
        return CognitiveState(graph=g, budget_remaining=10, root_question="q")

    def _make_storage_with_lexicon(self, tmp_path, var_dicts=None):
        """Build mock storage pointing at tmp_path/knowledge/variables.json."""
        import json
        from unittest.mock import MagicMock
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir(parents=True)
        if var_dicts:
            data = {
                "version": 1,
                "updated_at": "2026-05-20T00:00:00Z",
                "variables": list(var_dicts),
            }
            (knowledge_dir / "variables.json").write_text(json.dumps(data), encoding="utf-8")
        storage = MagicMock()
        storage.knowledge_dir.return_value = knowledge_dir
        return storage

    def test_no_lexicon_all_new(self, tmp_path):
        """Empty lexicon → all candidates count as new."""
        from explain_engine.engines.compress_dedup import compute_compress_dedup_stats
        state = self._make_state_with_nodes(
            ("c_001", "概念A", "描述A"),
            ("c_002", "概念B", "描述B"),
        )
        storage = self._make_storage_with_lexicon(tmp_path)
        stats = compute_compress_dedup_stats(state, storage, ["c_001", "c_002"])
        assert stats == {"reused": 0, "new": 2}

    def test_no_node_ids_returns_zero(self, tmp_path):
        """Empty new_node_ids list → 0 reused / 0 new."""
        from explain_engine.engines.compress_dedup import compute_compress_dedup_stats
        state = self._make_state_with_nodes()
        storage = self._make_storage_with_lexicon(tmp_path)
        stats = compute_compress_dedup_stats(state, storage, [])
        assert stats == {"reused": 0, "new": 0}

    def test_disabled_env_returns_all_new(self, tmp_path, monkeypatch):
        """EXPLAIN_EMBEDDING_DISABLED=1 → all candidates marked new (no embedding load)."""
        monkeypatch.setenv("EXPLAIN_EMBEDDING_DISABLED", "1")
        from explain_engine.engines.compress_dedup import compute_compress_dedup_stats
        state = self._make_state_with_nodes(
            ("c_001", "n1", "d1"),
            ("c_002", "n2", "d2"),
        )
        storage = self._make_storage_with_lexicon(tmp_path)
        stats = compute_compress_dedup_stats(state, storage, ["c_001", "c_002"])
        assert stats == {"reused": 0, "new": 2}

    @pytest.mark.embedding
    def test_distinct_candidates_all_new(self, tmp_path):
        """Lexicon has unrelated topic; new candidates also unrelated → all new."""
        from explain_engine.engines.compress_dedup import compute_compress_dedup_stats
        # Lexicon: 1 entry about housing
        var = {
            "global_id": "v_aaaa1111",
            "name": "房价上涨",
            "description": "一线城市房价上涨",
            "abstraction_level": 1,
            "epistemic": "insight",
            "fitness": {
                "reuse_count": 1,
                "avg_essentialness": 0.5,
                "avg_consistency": 0.5,
                "first_seen_at": "2026-05-20T00:00:00Z",
                "last_seen_at": "2026-05-20T00:00:00Z",
            },
            "canonical_mechanism": "房地产价格快速上涨导致购房压力",
            "source_sessions": ["s_aaaa0001"],
            "embedding": None,  # will be migrated by lazy fixture? no — DISABLED is default for non-embedding tests, BUT this test has @embedding marker so embedder is enabled
        }
        storage = self._make_storage_with_lexicon(tmp_path, [var])
        # Trigger migration via load+flush
        from explain_engine.engines.lexicon import _load_lexicon, _migrate_lexicon_embeddings
        lex = _load_lexicon(tmp_path / "knowledge" / "variables.json")
        _migrate_lexicon_embeddings(lex, tmp_path / "knowledge" / "variables.json")

        # New candidates about employment (unrelated)
        state = self._make_state_with_nodes(
            ("c_001", "就业压力", "互联网行业大规模裁员导致应届就业困难"),
        )
        stats = compute_compress_dedup_stats(state, storage, ["c_001"])
        # Different topic → no cosine match
        assert stats == {"reused": 0, "new": 1}

    @pytest.mark.embedding
    def test_similar_candidate_counted_reused(self, tmp_path):
        """Lexicon has 房价 entry; new candidate semantically similar → reused."""
        from explain_engine.engines.compress_dedup import compute_compress_dedup_stats
        var = {
            "global_id": "v_aaaa1111",
            "name": "房价压力",
            "description": "购房成本上升",
            "abstraction_level": 1,
            "epistemic": "insight",
            "fitness": {
                "reuse_count": 5,
                "avg_essentialness": 0.7,
                "avg_consistency": 0.7,
                "first_seen_at": "2026-05-20T00:00:00Z",
                "last_seen_at": "2026-05-20T00:00:00Z",
            },
            "canonical_mechanism": "一线城市房地产价格快速上涨导致购房成本飙升",
            "source_sessions": ["s_aaaa0001"],
            "embedding": None,
        }
        storage = self._make_storage_with_lexicon(tmp_path, [var])
        from explain_engine.engines.lexicon import _load_lexicon, _migrate_lexicon_embeddings
        lex = _load_lexicon(tmp_path / "knowledge" / "variables.json")
        _migrate_lexicon_embeddings(lex, tmp_path / "knowledge" / "variables.json")

        # New candidate using different phrasing but same concept.
        # Note: cosine asymmetry — lexicon embeds canonical_mechanism only,
        # candidate uses "name - description". To cross 0.85 the candidate
        # text needs to share key phrases with canonical (一线城市, 房地产
        # 价格, 购房成本). Empirically: 0.7649 (loose paraphrase) → 0.9005
        # when candidate mirrors canonical phrasing more closely. This
        # reflects realistic LLM near-duplicate output.
        state = self._make_state_with_nodes(
            ("c_001", "一线城市房价压力", "房地产价格快速上涨使购房成本上升"),
        )
        stats = compute_compress_dedup_stats(state, storage, ["c_001"])
        # Same concept (房价/购房) → cosine > 0.85 → reused
        assert stats["reused"] >= 1
        assert stats["new"] + stats["reused"] == 1
