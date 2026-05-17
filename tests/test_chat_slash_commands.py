"""Wave F.1: 6 slash commands tests."""

import pytest

from explain_engine.chat.slash_commands import (
    DEFAULT_COMMANDS,
    _command_by_name,
    dispatch_slash,
)

# Reuse _make_done_session from test_chat_session.py
from tests.test_chat_session import _make_done_session


class TestSlashRegistry:
    def test_has_6_default_commands(self):
        names = {c.name for c in DEFAULT_COMMANDS}
        assert names == {"quit", "help", "show", "budget", "compact", "save"}

    def test_command_by_name_finds_each(self):
        for name in ["quit", "help", "show", "budget", "compact", "save"]:
            assert _command_by_name(name) is not None
        assert _command_by_name("nonexistent") is None


class TestDispatchSlash:
    @pytest.mark.asyncio
    async def test_quit_yields_slash_quit_event(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_51a55001")
        chat = ChatSession("s_51a55001")
        events = await dispatch_slash(chat, "/quit")
        assert len(events) == 1
        assert events[0].type == "slash_quit"
        assert "Goodbye" in events[0].content

    @pytest.mark.asyncio
    async def test_help_lists_commands_and_tools(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_51a55002")
        chat = ChatSession("s_51a55002")
        events = await dispatch_slash(chat, "/help")
        content = events[0].content
        # Should list all 6 slash commands
        for name in ["quit", "help", "show", "budget", "compact", "save"]:
            assert f"/{name}" in content
        # Should list at least the expand tool
        assert "expand" in content

    @pytest.mark.asyncio
    async def test_show_includes_question_and_graph_counts(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_51a55003")
        chat = ChatSession("s_51a55003")
        events = await dispatch_slash(chat, "/show")
        content = events[0].content
        assert "Question:" in content
        assert "Graph:" in content
        assert "L0" in content

    @pytest.mark.asyncio
    async def test_budget_shows_remaining(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_51a55004")
        chat = ChatSession("s_51a55004")
        events = await dispatch_slash(chat, "/budget")
        content = events[0].content
        assert "per-turn remaining:" in content
        assert "per-session remaining:" in content

    @pytest.mark.asyncio
    async def test_compact_yields_compact_event(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_51a55005")
        chat = ChatSession("s_51a55005")
        events = await dispatch_slash(chat, "/compact")
        assert events[0].type == "slash_compact"

    @pytest.mark.asyncio
    async def test_save_persists_to_disk(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_51a55006")
        chat = ChatSession("s_51a55006")
        # Mutate something so persist has something to flush
        chat.chat_state.turn_count = 42
        events = await dispatch_slash(chat, "/save")
        assert events[0].type == "slash_save"
        # Re-load and verify
        chat2 = ChatSession("s_51a55006")
        assert chat2.chat_state.turn_count == 42

    @pytest.mark.asyncio
    async def test_unknown_command(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_51a55007")
        chat = ChatSession("s_51a55007")
        events = await dispatch_slash(chat, "/foobar")
        assert events[0].type == "slash_unknown"

    @pytest.mark.asyncio
    async def test_empty_slash(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_51a55008")
        chat = ChatSession("s_51a55008")
        events = await dispatch_slash(chat, "/")
        assert events[0].type == "slash_error"


class TestSessionIntegration:
    @pytest.mark.asyncio
    async def test_handle_user_input_dispatches_slash(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_51a55009")
        chat = ChatSession("s_51a55009")
        events = []
        async for ev in chat.handle_user_input("/help"):
            events.append(ev)
        # Should get a slash_help event (not slash_unimplemented)
        assert any(ev.type == "slash_help" for ev in events)
        # Turn count NOT bumped for slash commands
        assert chat.chat_state.turn_count == 0
