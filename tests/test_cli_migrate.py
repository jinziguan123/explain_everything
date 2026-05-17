"""Phase 9 Wave F.2: CLI `explain migrate` command tests.

Legacy sessions/*.json → ~/.explain/projects/<proj>/sessions/<sid>/ migration.
覆盖: no-legacy / single migrate / dry-run / multi / idempotent.
"""

import json

from typer.testing import CliRunner

from explain_engine.cli import app


def _write_legacy(tmp_path, sid):
    """造一个 Phase 0-8 风格 legacy session JSON 到 tmp_path/sessions/<sid>.json."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    legacy = {
        "meta": {
            "session_id": sid,
            "question": "q",
            "stage": "done",
            "budget": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
        "state": {
            "graph": {"nodes": [], "edges": [], "root_question": "q"},
            "tick": 0,
            "budget_remaining": 1,
            "root_question": "q",
            "insight_candidates": [],
            "reasoning_trace": [],
            "last_gains": {},
            "last_gain_tick": 0,
            "last_reflection_change_tick": 0,
        },
    }
    (sessions_dir / f"{sid}.json").write_text(
        json.dumps(legacy), encoding="utf-8"
    )


class TestMigrateCommand:
    def test_no_legacy_says_so(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["migrate"])
        assert result.exit_code == 0
        assert "no legacy" in result.output.lower()

    def test_migrate_one_session(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # 用 tmp_path/.explain 隔离 storage_v2 目标 (override 全局 autouse fixture)
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
        _write_legacy(tmp_path, "s_aaaa0001")

        runner = CliRunner()
        result = runner.invoke(app, ["migrate"])
        assert result.exit_code == 0
        assert "migrated" in result.output.lower()
        assert "s_aaaa0001" in result.output
        # 验 legacy file 已 archive 到 .legacy/
        assert (tmp_path / "sessions" / ".legacy" / "s_aaaa0001.json").exists()
        # 验 storage_v2 layout 建出来了
        assert (tmp_path / ".explain").exists()

    def test_dry_run_does_not_move(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
        _write_legacy(tmp_path, "s_bbbb0001")

        runner = CliRunner()
        result = runner.invoke(app, ["migrate", "--dry-run"])
        assert result.exit_code == 0
        assert "dry-run" in result.output.lower()
        # File 没动
        assert (tmp_path / "sessions" / "s_bbbb0001.json").exists()
        assert not (
            tmp_path / "sessions" / ".legacy" / "s_bbbb0001.json"
        ).exists()

    def test_migrate_multiple(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
        for sid in ["s_cccc0001", "s_cccc0002", "s_cccc0003"]:
            _write_legacy(tmp_path, sid)

        runner = CliRunner()
        result = runner.invoke(app, ["migrate"])
        assert result.exit_code == 0
        assert "3/3" in result.output or "Migrated 3" in result.output

    def test_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
        _write_legacy(tmp_path, "s_dddd0001")
        runner = CliRunner()
        # 第一次: migrate 成功
        first = runner.invoke(app, ["migrate"])
        assert first.exit_code == 0
        # 第二次: 无 legacy left (因为 first 已 move 到 .legacy/)
        result = runner.invoke(app, ["migrate"])
        assert result.exit_code == 0
        assert "no legacy" in result.output.lower()
