"""Phase 11 Wave 1 Task 1.B: cli main entry (REPL default + subcommand backward compat)."""

from typer.testing import CliRunner

from explain_engine.cli import app


class TestCliMainEntry:
    def test_no_subcommand_enters_repl(self, monkeypatch):
        """无参数 `explain` → 走 @app.callback() → enter_repl_async called.

        Phase 19 Wave 6 Task 32: callback 现传 show_splash kw — fake 接 **kwargs
        防 signature 不兼容.
        """
        called: list[bool] = []

        async def fake_enter(**kwargs):
            called.append(True)

        # cli.py callback 用 local import; monkeypatch source 模块
        monkeypatch.setattr(
            "explain_engine.chat.repl_entry.enter_repl_async", fake_enter
        )
        runner = CliRunner()
        result = runner.invoke(app, [])
        # enter_repl_async fake 跑完后 callback raise typer.Exit() → exit_code 0
        assert result.exit_code == 0, (
            f"expected exit 0, got {result.exit_code}; output={result.output!r}"
        )
        assert called == [True], (
            f"enter_repl_async 未被调用; output={result.output!r}"
        )

    def test_subcommand_still_typer(self, monkeypatch):
        """`explain list` 仍走 typer subcommand, 不进 REPL."""
        # 防 enter_repl_async 误调 (subcommand 路径 callback 应跳过)
        called: list[bool] = []

        async def fake_enter(**kwargs):
            called.append(True)

        monkeypatch.setattr(
            "explain_engine.chat.repl_entry.enter_repl_async", fake_enter
        )
        runner = CliRunner()
        result = runner.invoke(app, ["list"])
        # list 命令应该正常输 (即使 empty project)
        assert result.exit_code == 0, (
            f"expected exit 0, got {result.exit_code}; output={result.output!r}"
        )
        # 关键: REPL 不应启动
        assert called == [], "subcommand 路径不应触发 enter_repl_async"

    def test_help_works(self):
        """`explain --help` 仍 typer 默认 + 显 help text."""
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Cognitive Engine" in result.output

    def test_no_splash_flag_passes_through(self, monkeypatch):
        """`explain --no-splash` → enter_repl_async(show_splash=False)."""
        captured: dict = {}

        async def fake_enter(show_splash: bool = True):
            captured["show_splash"] = show_splash

        monkeypatch.setattr(
            "explain_engine.chat.repl_entry.enter_repl_async", fake_enter
        )
        runner = CliRunner()
        result = runner.invoke(app, ["--no-splash"])
        assert result.exit_code == 0, (
            f"expected exit 0, got {result.exit_code}; output={result.output!r}"
        )
        assert captured.get("show_splash") is False, (
            f"--no-splash 应传 show_splash=False, got {captured!r}"
        )

    def test_default_invocation_passes_show_splash_true(self, monkeypatch):
        """`explain` (无 flag) → enter_repl_async(show_splash=True)."""
        captured: dict = {}

        async def fake_enter(show_splash: bool = True):
            captured["show_splash"] = show_splash

        monkeypatch.setattr(
            "explain_engine.chat.repl_entry.enter_repl_async", fake_enter
        )
        runner = CliRunner()
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert captured.get("show_splash") is True
