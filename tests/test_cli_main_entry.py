"""Phase 11 Wave 1 Task 1.B: cli main entry (REPL default + subcommand backward compat)."""

from typer.testing import CliRunner

from explain_engine.cli import app


class TestCliMainEntry:
    def test_no_subcommand_enters_repl(self, monkeypatch):
        """无参数 `explain` → 走 @app.callback() → enter_repl_async called."""
        called: list[bool] = []

        async def fake_enter():
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

        async def fake_enter():
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
