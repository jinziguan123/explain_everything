"""Phase 16: /theory <id> [reject] chat slash."""

import pytest

from explain_engine.chat.slash_commands import dispatch_slash


class TestSlashTheory:
    @pytest.mark.asyncio
    async def test_no_args_returns_usage_error(self):
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        _make_done_session("s_20000001")
        chat = ChatSession("s_20000001")
        events = await dispatch_slash(chat, "/theory")
        assert events[0].type == "slash_error"
        assert "用法" in events[0].content

    @pytest.mark.asyncio
    async def test_not_found_returns_error(self):
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        _make_done_session("s_20000002")
        chat = ChatSession("s_20000002")
        events = await dispatch_slash(chat, "/theory t_nonexistent")
        # cache 为空 (cold start) → theory 找不到 → err_theory_not_found
        assert events[0].type == "slash_error"
        assert "t_nonexistent" in events[0].content

    @pytest.mark.asyncio
    async def test_reject_nonexistent_returns_error(self):
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        _make_done_session("s_20000003")
        chat = ChatSession("s_20000003")
        events = await dispatch_slash(chat, "/theory t_nonexistent reject")
        assert events[0].type == "slash_error"  # cache 内无 theory, reject 失败

    @pytest.mark.asyncio
    async def test_default_commands_includes_theory(self):
        from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
        names = {c.name for c in DEFAULT_COMMANDS}
        assert "theory" in names
