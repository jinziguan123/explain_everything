"""Wave A.1: migration tests — sessions/*.json → ~/.explain/projects/<proj>/sessions/<sid>/"""

import json

from explain_engine.persistence.migration import (
    detect_legacy_sessions,
    migrate_all,
    migrate_session,
)


class TestDetectLegacy:
    def test_finds_legacy_files(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "sessions").mkdir()
        (tmp_path / "sessions" / "s_001abcde.json").write_text(
            '{"meta":{"session_id":"s_001abcde"}}'
        )
        (tmp_path / "sessions" / "s_002fedcb.json").write_text(
            '{"meta":{"session_id":"s_002fedcb"}}'
        )
        # backup files should NOT be picked up
        (tmp_path / "sessions" / "s_001abcde.before-rescore.json").write_text("{}")
        found = detect_legacy_sessions()
        assert sorted(found) == ["s_001abcde", "s_002fedcb"]

    def test_no_sessions_dir_returns_empty(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert detect_legacy_sessions() == []


class TestMigrateSession:
    def test_migrates_single(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
        # Setup legacy file
        (tmp_path / "sessions").mkdir()
        legacy = {
            "meta": {
                "session_id": "s_001abcde",
                "question": "why?",
                "stage": "done",
                "created_at": 1234567890.0,
                "updated_at": 1234567890.0,
            },
            "state": {
                "graph": {"nodes": {}, "edges": {}, "root_question": "why?"},
                "tick": 0,
                "budget_remaining": 10,
                "root_question": "why?",
                "insight_candidates": [],
                "reasoning_trace": [],
                "last_gains": {},
                "last_gain_tick": 0,
                "last_reflection_change_tick": 0,
            },
        }
        (tmp_path / "sessions" / "s_001abcde.json").write_text(json.dumps(legacy))

        migrate_session("s_001abcde")

        # New layout exists
        from explain_engine.persistence.storage_v2 import StorageV2
        s = StorageV2()
        meta = s.load_metadata("s_001abcde")
        assert meta["question"] == "why?"
        graph_data = s.load_graph("s_001abcde")
        assert graph_data["root_question"] == "why?"
        # Legacy file moved to .legacy/
        legacy_archive = tmp_path / "sessions" / ".legacy" / "s_001abcde.json"
        assert legacy_archive.exists()
        # Original removed
        assert not (tmp_path / "sessions" / "s_001abcde.json").exists()

    def test_dry_run_no_change(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
        (tmp_path / "sessions").mkdir()
        (tmp_path / "sessions" / "s_001abcde.json").write_text(
            '{"meta":{"session_id":"s_001abcde","question":"q","stage":"done",'
            '"created_at":1.0,"updated_at":1.0},'
            '"state":{"graph":{"nodes":{},"edges":{},"root_question":"q"},'
            '"tick":0,"budget_remaining":1,"root_question":"q","insight_candidates":[],'
            '"reasoning_trace":[],"last_gains":{},"last_gain_tick":0,'
            '"last_reflection_change_tick":0}}'
        )
        migrate_session("s_001abcde", dry_run=True)
        # No new directory created
        assert not (tmp_path / ".explain").exists()
        # Legacy untouched
        assert (tmp_path / "sessions" / "s_001abcde.json").exists()


class TestMigrateAll:
    def test_migrates_multiple(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
        (tmp_path / "sessions").mkdir()
        for sid in ["s_001abcde", "s_002fedcb", "s_00345678"]:
            data = {
                "meta": {
                    "session_id": sid, "question": "q", "stage": "done",
                    "created_at": 1.0, "updated_at": 1.0,
                },
                "state": {
                    "graph": {"nodes": {}, "edges": {}, "root_question": "q"},
                    "tick": 0, "budget_remaining": 1, "root_question": "q",
                    "insight_candidates": [], "reasoning_trace": [],
                    "last_gains": {}, "last_gain_tick": 0,
                    "last_reflection_change_tick": 0,
                },
            }
            (tmp_path / "sessions" / f"{sid}.json").write_text(json.dumps(data))

        results = migrate_all()
        assert len(results) == 3
        from explain_engine.persistence.storage_v2 import StorageV2
        s = StorageV2()
        assert sorted(s.list_sessions()) == ["s_001abcde", "s_002fedcb", "s_00345678"]
