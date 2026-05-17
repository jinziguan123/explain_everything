"""Phase 9 Wave F.2: CLI `explain chat` command tests.

Note: 完整 REPL 交互测试很难做 (涉及 stdin / asyncio.to_thread / live LLM).
本文件只覆盖 command surface: 命令存在 / 接受 flag / session 不存在时退出非 0.

真实 REPL 测试留给 Task G.1 acceptance (手工跑).
"""

from typer.testing import CliRunner

from explain_engine.cli import app


class TestChatCommand:
    def test_chat_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(app, ["chat", "--help"])
        assert result.exit_code == 0
        assert "chat" in result.output.lower()

    def test_chat_missing_session_exits_with_error(self):
        runner = CliRunner()
        # autouse isolated_explain_home → 全新 tmp EXPLAIN_HOME, session 不存在
        result = runner.invoke(app, ["chat", "s_missing0"])
        assert result.exit_code != 0
        # FileNotFoundError 消息应该提到 sid 或 "not found" 或文件路径
        out = result.output.lower()
        assert (
            "s_missing0" in out
            or "missing0" in out
            or "not found" in out
            or "no such file" in out
        ), f"unexpected output: {result.output!r}"

    def test_chat_flags_present(self):
        runner = CliRunner()
        result = runner.invoke(app, ["chat", "--help"])
        assert result.exit_code == 0
        # --no-input-check is hidden until Wave G+ wires it; verify only visible flags
        assert "--tool-budget-per-turn" in result.output
        assert "--tool-budget-per-session" in result.output
        # 验证 hidden 真生效 (典型回归: 有人移除 hidden=True)
        assert "--no-input-check" not in result.output

    def test_chat_no_input_check_still_accepted_silently(self):
        """Hidden flag still accepted (for any docs/scripts that pass it)."""
        runner = CliRunner()
        # Passing the hidden flag should not error (typer accepts it)
        # Use missing session to verify flag parsing succeeds (then exits on missing)
        result = runner.invoke(app, ["chat", "s_missing0", "--no-input-check"])
        # Exit code != 0 due to missing session, but should NOT be a "unknown option" error
        assert "no such option" not in result.output.lower()
        assert "unknown option" not in result.output.lower()
