"""Phase 9 Wave F.2: CLI `explain chat` command tests.

Note: 完整 REPL 交互测试很难做 (涉及 stdin / asyncio.to_thread / live LLM).
本文件只覆盖 command surface: 命令存在 / 接受 flag / session 不存在时退出非 0.

真实 REPL 测试留给 Task G.1 acceptance (手工跑).
"""

import pytest
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
        # Phase 11 Wave 2.5: --tool-budget-per-* flag 删除, 改 /budget slash
        # interactive config. verify they're gone (回归 guard).
        assert "--tool-budget-per-turn" not in result.output
        assert "--tool-budget-per-session" not in result.output
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


class TestReplSwitchSession:
    """REPL 收到 slash_switch_session event 后切到新 chat_session."""

    @pytest.mark.asyncio
    async def test_switch_session_replaces_chat_session(
        self, monkeypatch
    ) -> None:
        """模拟用户输 magic input → handler yield slash_switch_session → REPL 切.

        用 monkeypatch 把 input 改成返预设序列 ("trigger switch", /quit);
        把 ChatSession.handle_user_input mock 成对第 1 input yield switch event
        (sid=s_target), 第 2 input yield slash_quit.
        验切换后 chat_session.sid == 's_target'.
        """
        from explain_engine.chat.session import ChatEvent
        from explain_engine.cli import (
            _run_chat_repl_async,  # NEW symbol (Step 2.3)
        )
        from tests.test_chat_session import _make_done_session

        # 注意 sid 必须符合 ^s_[0-9a-f]{8}$ regex (Wave 1 偏差 1 已发现)
        _make_done_session("s_22222001")
        _make_done_session("s_22222002")

        # input 序列: 1st 触发切换, 2nd /quit
        # Wave 4 (2026-05-18): cli._run_chat_repl_async 内部 local import
        # `from explain_engine.chat.repl_input import read_input` — patch source.
        inputs = iter(["switch please", "/quit"])

        async def fake_read_input(pt_session, prompt_text="\n> "):
            return next(inputs)

        monkeypatch.setattr(
            "explain_engine.chat.repl_input.read_input", fake_read_input
        )

        # 跟踪每次 handle_user_input 调用时 chat_session.sid
        observed_sids: list[str] = []

        async def fake_handle(self, text, llm=None):
            observed_sids.append(self.sid)
            if text == "switch please":
                yield ChatEvent(
                    type="slash_switch_session",
                    content={"sid": "s_22222002"},
                )
            elif text == "/quit":
                yield ChatEvent(type="slash_quit", content="bye")

        from explain_engine.chat.session import ChatSession
        monkeypatch.setattr(ChatSession, "handle_user_input", fake_handle)

        # 跑 (llm=None 即可, fake_handle 不用)
        await _run_chat_repl_async(
            initial_sid="s_22222001",
            llm=None,
        )

        # 第 1 input 时 chat 仍是 src, 第 2 input 时已切到 dst
        assert observed_sids == ["s_22222001", "s_22222002"]

    @pytest.mark.asyncio
    async def test_switch_failure_recovers_to_previous_not_initial(
        self, monkeypatch
    ) -> None:
        """切失败时回到上次成功 sid, 不是 initial. 防 I-2 (review 2026-05-18) 回归.

        场景: 启动 sid_A → /new 切到 sid_B (成功) → /resume 选 sid_X (sid_X 不存在,
        切失败). 应该回到 sid_B 而不是 sid_A.
        """
        from explain_engine.chat.session import ChatEvent, ChatSession
        from explain_engine.cli import _run_chat_repl_async
        from tests.test_chat_session import _make_done_session

        _make_done_session("s_aaaaaa01")  # initial
        _make_done_session("s_aaaaaa02")  # 第一次切的目标 (成功)
        # s_aaaaaaff 故意不建 → 第二次切会 FileNotFoundError

        inputs = iter([
            "switch to b",   # 切 A → B 成功
            "switch to x",   # 切 B → X 失败
            "/quit",          # 退出
        ])

        async def fake_read_input(pt_session, prompt_text="\n> "):
            return next(inputs)

        monkeypatch.setattr(
            "explain_engine.chat.repl_input.read_input", fake_read_input
        )

        observed_sids: list[str] = []

        async def fake_handle(self, text, llm=None):
            observed_sids.append(self.sid)
            if text == "switch to b":
                yield ChatEvent(
                    type="slash_switch_session",
                    content={"sid": "s_aaaaaa02"},
                )
            elif text == "switch to x":
                yield ChatEvent(
                    type="slash_switch_session",
                    content={"sid": "s_aaaaaaff"},  # 不存在
                )
            elif text == "/quit":
                yield ChatEvent(type="slash_quit", content="bye")

        monkeypatch.setattr(ChatSession, "handle_user_input", fake_handle)

        await _run_chat_repl_async(
            initial_sid="s_aaaaaa01",
            llm=None,
        )

        # 第 1 input: A. 第 2 input: B (切 A→B 成功后). 第 3 input: 应该是 B
        # (切 B→X 失败, recovery 回 B 不是 A).
        assert observed_sids == ["s_aaaaaa01", "s_aaaaaa02", "s_aaaaaa02"]
