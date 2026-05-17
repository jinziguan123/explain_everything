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

    def test_migrate_twice_preserves_first_archive(self, tmp_path, monkeypatch) -> None:
        """I1 regression: re-migration after manual delete shouldn't overwrite prior archive.

        Scenario:
        1. First migration: legacy → .legacy/s_001abcde.json
        2. User manually deletes ~/.explain/projects/<proj>/sessions/s_001abcde/
        3. User restores legacy s_001abcde.json from elsewhere
        4. Second migration: should archive to .legacy/s_001abcde.<timestamp>.json
           (NOT overwrite .legacy/s_001abcde.json from step 1)
        """
        import shutil as sh
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
        (tmp_path / "sessions").mkdir()

        sid = "s_001abcde"
        legacy_data = (
            '{"meta":{"session_id":"' + sid + '","question":"q","stage":"done","budget":1,'
            '"created_at":"2026-01-01T00:00:00+00:00",'
            '"updated_at":"2026-01-01T00:00:00+00:00"},'
            '"state":{"graph":{"nodes":[],"edges":[],"root_question":"q"},'
            '"tick":0,"budget_remaining":1,"root_question":"q","insight_candidates":[],'
            '"reasoning_trace":[],"last_gains":{},"last_gain_tick":0,'
            '"last_reflection_change_tick":0}}'
        )
        legacy_path = tmp_path / "sessions" / f"{sid}.json"
        legacy_path.write_text(legacy_data)

        # Step 1: first migration
        from explain_engine.persistence.migration import migrate_session
        result1 = migrate_session(sid)
        assert result1["migrated"] is True
        archive_path = tmp_path / "sessions" / ".legacy" / f"{sid}.json"
        assert archive_path.exists()
        first_archive_content = archive_path.read_text()

        # Step 2-3: simulate user deletes migrated session + restores legacy
        from explain_engine.persistence.storage_v2 import StorageV2
        sh.rmtree(StorageV2().session_dir(sid))
        # Different content this time to detect overwrite
        legacy_path.write_text(legacy_data.replace('"q"', '"q-v2"'))

        # Step 4: second migration
        result2 = migrate_session(sid)
        assert result2["migrated"] is True

        # First archive must still exist with original content
        assert archive_path.exists()
        assert archive_path.read_text() == first_archive_content

        # Second archive should be at timestamped path
        timestamped = list((tmp_path / "sessions" / ".legacy").glob(f"{sid}.*.json"))
        assert len(timestamped) >= 1
        assert "q-v2" in timestamped[0].read_text()

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
