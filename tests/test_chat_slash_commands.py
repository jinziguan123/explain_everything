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
    def test_has_required_default_commands(self):
        names = {c.name for c in DEFAULT_COMMANDS}
        # 至少包含这些 (Wave 4 会再加 resume)
        required = {"quit", "help", "show", "budget", "compact", "save", "new"}
        assert required.issubset(names)

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


class TestSlashNew:
    @pytest.mark.asyncio
    async def test_empty_args_rejects(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e000001")
        chat = ChatSession("s_5e000001")
        events = await dispatch_slash(chat, "/new")
        assert len(events) == 1
        assert events[0].type == "slash_error"
        assert "Usage" in events[0].content

    @pytest.mark.asyncio
    async def test_no_llm_rejects(self):
        """Chat 没绑 llm 时 /new 明确报错 (而非裸 AttributeError)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e000002")
        chat = ChatSession("s_5e000002")  # llm 默认 None
        events = await dispatch_slash(chat, "/new 为什么 X")
        assert events[0].type == "slash_error"
        assert "llm" in events[0].content.lower()

    @pytest.mark.asyncio
    async def test_success_creates_session_and_yields_switch(
        self, monkeypatch
    ):
        """Mock bootstrap + review → 验创建 session + yield slash_switch_session."""
        from explain_engine.chat.session import ChatSession
        from explain_engine.schema.nodes import VariableNode

        _make_done_session("s_5e000003")

        # Mock bootstrap_phenomena: 返 2 个固定 phenomena
        async def fake_bootstrap(question, llm, min_count=8, max_count=15):
            assert question == "为什么 中文 测试"
            return [
                VariableNode(
                    id="p_001", name="A", description="da",
                    abstraction_level=0, confidence=0.7, epistemic="observation",
                ),
                VariableNode(
                    id="p_002", name="B", description="db",
                    abstraction_level=0, confidence=0.7, epistemic="observation",
                ),
            ]
        monkeypatch.setattr(
            "explain_engine.chat.slash_commands.bootstrap_phenomena",
            fake_bootstrap,
        )

        # Mock review_phenomena: pass-through (keep all)
        def fake_review(phenomena, console=None):
            return list(phenomena)
        monkeypatch.setattr(
            "explain_engine.chat.slash_commands.review_phenomena",
            fake_review,
        )

        # ChatSession 必须带 llm 才能 /new — sentinel 即可 (fake_bootstrap 不用真 llm)
        chat = ChatSession("s_5e000003", llm=object())  # type: ignore[arg-type]

        events = await dispatch_slash(chat, "/new 为什么 中文 测试")

        # 应 yield slash_new (info) + slash_switch_session (signal)
        types = [e.type for e in events]
        assert "slash_new" in types
        assert "slash_switch_session" in types

        switch_ev = next(e for e in events if e.type == "slash_switch_session")
        new_sid = switch_ev.content["sid"]
        assert new_sid.startswith("s_")
        assert new_sid != "s_5e000003"

        # 真存盘了
        from explain_engine.persistence.session import SessionStore
        store = SessionStore()
        loaded = store.load(new_sid)
        assert loaded.meta.question == "为什么 中文 测试"
        assert len(loaded.state.graph.nodes) == 2  # 2 phenomena

    @pytest.mark.asyncio
    async def test_bootstrap_error_returns_error_no_switch(self, monkeypatch):
        """Mock bootstrap raise → slash_error, 不 yield switch."""
        from explain_engine.chat.session import ChatSession
        from explain_engine.llm.errors import LLMError

        _make_done_session("s_5e000004")

        async def fake_bootstrap_fails(question, llm, min_count=8, max_count=15):
            raise LLMError("mock LLM down")
        monkeypatch.setattr(
            "explain_engine.chat.slash_commands.bootstrap_phenomena",
            fake_bootstrap_fails,
        )

        chat = ChatSession("s_5e000004", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/new question")

        types = [e.type for e in events]
        assert "slash_error" in types
        assert "slash_switch_session" not in types
        err = next(e for e in events if e.type == "slash_error")
        assert "LLMError" in err.content or "mock LLM down" in err.content

    @pytest.mark.asyncio
    async def test_registered_in_default_commands(self):
        names = {c.name for c in DEFAULT_COMMANDS}
        assert "new" in names
