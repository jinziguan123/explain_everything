"""Phase 16: /theories chat slash."""

import pytest

from explain_engine.chat.slash_commands import dispatch_slash


class TestSlashTheories:
    @pytest.mark.asyncio
    async def test_cold_start_shows_threshold_message(self):
        """1 session < cold_start_threshold=3 → cold start msg."""
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        _make_done_session("s_10000001")
        chat = ChatSession("s_10000001")
        events = await dispatch_slash(chat, "/theories")
        assert events[0].type == "slash_theories"
        # 1 session < cold_start_threshold=3 → 应显 "需累积" cold start msg
        assert "session" in events[0].content
        # 应含 cold start 中文 (需累积 / current / needed format)
        assert "累积" in events[0].content or "需" in events[0].content

    @pytest.mark.asyncio
    async def test_no_session_at_all(self):
        """0 session — chat session 是 ephemeral 或 cold start. 不该 crash."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.persistence.storage_v2 import StorageV2
        eph = EphemeralChatSession(storage=StorageV2())
        events = await dispatch_slash(eph, "/theories")
        # cross-session 命令, ephemeral 也 work (跟 /list /lexicon 一致)
        assert events[0].type == "slash_theories"
        # 0 session → cold start msg
        assert "session" in events[0].content

    @pytest.mark.asyncio
    async def test_default_commands_includes_theories(self):
        from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
        names = {c.name for c in DEFAULT_COMMANDS}
        assert "theories" in names
