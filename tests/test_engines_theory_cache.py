"""Phase 16: TheoriesCache lazy invalidation + atomic write + reject."""

import json


def _empty_cache_dict():
    return {
        "version": "1.0", "computed_at": "2026-05-21T00:00:00Z",
        "session_ids_snapshot": [], "cold_start_threshold": 3,
        "stability_window_size": 5,
        "themes": [], "tentative_theories": [], "stable_theories": [],
        "rejected_theory_ids": [],
    }


class TestGetActiveTheoriesCache:
    def test_no_cache_file_returns_empty(self, tmp_path, monkeypatch):
        from explain_engine.engines.theory.cache import get_active_theories
        from explain_engine.persistence.storage_v2 import StorageV2
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        storage = StorageV2()
        # 无 session, cold-start path 返 empty
        result = get_active_theories(storage, embedder=None)
        assert result.session_ids_snapshot == []

    def test_cache_hit_returns_cached(self, tmp_path, monkeypatch):
        from explain_engine.engines.theory.cache import get_active_theories
        from explain_engine.persistence.storage_v2 import StorageV2
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        storage = StorageV2()
        knowledge_dir = storage.knowledge_dir()
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        cache_path = knowledge_dir / "theories.json"
        cache_path.write_text(json.dumps(_empty_cache_dict()))
        # 无 session 时 snapshot=[] 跟 cache 一致 → cache hit
        result = get_active_theories(storage, embedder=None)
        assert result.session_ids_snapshot == []


class TestRejectTheory:
    def test_reject_idempotent(self, tmp_path, monkeypatch):
        from explain_engine.engines.theory.cache import reject_theory
        from explain_engine.persistence.storage_v2 import StorageV2
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        storage = StorageV2()
        knowledge_dir = storage.knowledge_dir()
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        cache_dict = _empty_cache_dict()
        cache_dict["tentative_theories"] = [{
            "id": "t_abc", "motif_type": "chain",
            "theme_ids": [], "node_ids": [], "edges": [],
            "supporting_sessions": [], "natural_language_summary": "",
            "structure_complexity": 2,
            "first_seen_session": "", "last_seen_session": "",
            "predictive_power": 0.5, "stability_status": "tentative",
            "stable_promoted_at_session": None,
        }]
        cache_path = knowledge_dir / "theories.json"
        cache_path.write_text(json.dumps(cache_dict))

        assert reject_theory(storage, "t_abc") is True
        assert reject_theory(storage, "t_abc") is True  # idempotent
        reloaded = json.loads(cache_path.read_text())
        assert "t_abc" in reloaded["rejected_theory_ids"]

    def test_reject_nonexistent_returns_false(self, tmp_path, monkeypatch):
        from explain_engine.engines.theory.cache import reject_theory
        from explain_engine.persistence.storage_v2 import StorageV2
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        storage = StorageV2()
        knowledge_dir = storage.knowledge_dir()
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        cache_path = knowledge_dir / "theories.json"
        cache_path.write_text(json.dumps(_empty_cache_dict()))
        assert reject_theory(storage, "t_does_not_exist") is False
