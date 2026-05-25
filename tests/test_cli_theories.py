"""Phase 16: cli explain theories subcommand."""

from typer.testing import CliRunner

from explain_engine.cli import app


class TestCliTheories:
    def test_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(app, ["theories", "--help"])
        assert result.exit_code == 0
        # 含中文 desc
        out = result.output
        assert "theories" in out.lower() or "因果模式" in out

    def test_no_sessions_shows_cold_start(self, tmp_path, monkeypatch):
        """0 session → cold start msg."""
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        runner = CliRunner()
        result = runner.invoke(app, ["theories"])
        assert result.exit_code == 0
        # cold start: 0 session, 应显 需累积 or 类似
        out = result.output
        assert "session" in out.lower() or "需累积" in out

    def test_force_recompute_flag_accepted(self):
        runner = CliRunner()
        result = runner.invoke(app, ["theories", "--force", "--help"])
        assert result.exit_code == 0
