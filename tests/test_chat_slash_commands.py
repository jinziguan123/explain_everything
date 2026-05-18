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
        required = {
            "quit", "help", "show", "budget", "compact", "save", "new", "resume"
        }
        assert required.issubset(names)

    def test_command_by_name_finds_each(self):
        for name in ["quit", "help", "show", "budget", "compact", "save", "new", "resume"]:
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
        # Should list all 8 slash commands
        for name in [
            "quit", "help", "show", "budget", "compact", "save", "new", "resume"
        ]:
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
    async def test_budget_display_only_when_no_provider(self):
        """Phase 11 Wave 2.5: input_provider None → display-only event."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_51a55004")
        chat = ChatSession("s_51a55004")  # input_provider 默认 None
        events = await dispatch_slash(chat, "/budget")
        assert len(events) == 1
        assert events[0].type == "slash_budget"
        assert "display-only" in events[0].content

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


class TestSlashResume:
    @pytest.mark.asyncio
    async def test_no_sessions_returns_info(self, monkeypatch):
        from explain_engine.chat.session import ChatSession
        from explain_engine.persistence.session import SessionStore
        _make_done_session("s_5e500001")
        chat = ChatSession("s_5e500001")
        # Monkey patch SessionStore.list 返空 (handler 用 SessionStore.list()
        # 自动 sort + log warning 跳过坏 session, 替代了之前手写 metas loading)
        monkeypatch.setattr(SessionStore, "list", lambda self: [])
        events = await dispatch_slash(chat, "/resume")
        types = [e.type for e in events]
        assert "slash_resume" in types
        assert "slash_switch_session" not in types
        info = next(e for e in events if e.type == "slash_resume")
        assert "无" in info.content or "no session" in info.content.lower()

    @pytest.mark.asyncio
    async def test_args_rejected(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e500002")
        chat = ChatSession("s_5e500002")
        events = await dispatch_slash(chat, "/resume extra")
        assert events[0].type == "slash_error"

    @pytest.mark.asyncio
    async def test_picks_session_yields_switch(self, monkeypatch):
        """2 个 session: 当前 + 另一个. 输入 1 → switch 到 latest (按 created_at desc)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e500003")
        # 加第二个 session, 显式 newer created_at
        from explain_engine.persistence.session import (
            Session,
            SessionMeta,
            SessionStore,
        )
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.state import CognitiveState
        meta_b = SessionMeta.new(question="qb")
        meta_b.session_id = "s_5e500099"
        meta_b.created_at = 9999999999.0  # newer than s_5e500003
        state_b = CognitiveState(
            graph=ExplanationGraph(root_question="qb"),
            budget_remaining=10, root_question="qb",
        )
        SessionStore().save(Session(meta=meta_b, state=state_b))

        chat = ChatSession("s_5e500003")

        # Mock input 返 "1" (选 latest = s_5e500099)
        monkeypatch.setattr(
            "builtins.input", lambda *a, **kw: "1"
        )

        events = await dispatch_slash(chat, "/resume")
        types = [e.type for e in events]
        assert "slash_switch_session" in types
        switch_ev = next(e for e in events if e.type == "slash_switch_session")
        assert switch_ev.content["sid"] == "s_5e500099"

    @pytest.mark.asyncio
    async def test_invalid_number_cancels(self, monkeypatch):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e500004")
        chat = ChatSession("s_5e500004")
        monkeypatch.setattr(
            "builtins.input", lambda *a, **kw: "abc"
        )
        events = await dispatch_slash(chat, "/resume")
        types = [e.type for e in events]
        assert "slash_error" in types
        assert "slash_switch_session" not in types

    @pytest.mark.asyncio
    async def test_out_of_range_cancels(self, monkeypatch):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e500005")
        chat = ChatSession("s_5e500005")
        monkeypatch.setattr(
            "builtins.input", lambda *a, **kw: "99"
        )
        events = await dispatch_slash(chat, "/resume")
        types = [e.type for e in events]
        assert "slash_error" in types
        assert "slash_switch_session" not in types

    @pytest.mark.asyncio
    async def test_q_cancels(self, monkeypatch):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e500006")
        chat = ChatSession("s_5e500006")
        monkeypatch.setattr(
            "builtins.input", lambda *a, **kw: "q"
        )
        events = await dispatch_slash(chat, "/resume")
        types = [e.type for e in events]
        assert "slash_resume" in types
        assert "slash_switch_session" not in types
        info = next(e for e in events if e.type == "slash_resume")
        assert "取消" in info.content or "cancel" in info.content.lower()

    @pytest.mark.asyncio
    async def test_picking_current_session_noop(self, monkeypatch):
        """只 1 session (当前). 输 1 选自己 → 不 yield switch, 只 info."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e500007")
        chat = ChatSession("s_5e500007")
        monkeypatch.setattr(
            "builtins.input", lambda *a, **kw: "1"
        )
        events = await dispatch_slash(chat, "/resume")
        types = [e.type for e in events]
        assert "slash_switch_session" not in types
        info = next(
            e for e in events if e.type == "slash_resume"
        )
        assert "已在" in info.content or "current" in info.content.lower()


class TestSlashResumeProvider:
    """F-1 (2026-05-18 review): /resume 通过 chat.input_provider 拿 input.

    若 input_provider set (e.g. cli REPL 启动时挂上), handler 用它而非
    bare input() 走 fallback. 保证 chat 模式内 picker 也走 prompt_toolkit.
    """

    @pytest.mark.asyncio
    async def test_picks_session_uses_input_provider(self, monkeypatch):
        """Set chat.input_provider 后, handler 用 provider 不走 to_thread(input)."""
        from explain_engine.chat.session import ChatSession
        from explain_engine.persistence.session import (
            Session,
            SessionMeta,
            SessionStore,
        )
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.state import CognitiveState

        _make_done_session("s_f1f10001")
        # 加第二 session, created_at 更晚让它排第 1
        meta_b = SessionMeta.new(question="qb")
        meta_b.session_id = "s_f1f10099"
        meta_b.created_at = 9999999999.0
        state_b = CognitiveState(
            graph=ExplanationGraph(root_question="qb"),
            budget_remaining=10, root_question="qb",
        )
        SessionStore().save(Session(meta=meta_b, state=state_b))

        chat = ChatSession("s_f1f10001")

        provider_calls = []

        async def fake_provider(prompt_text):
            provider_calls.append(prompt_text)
            return "1"

        chat.input_provider = fake_provider

        # 同时 monkeypatch builtins.input 抛 — 验证 handler 没 fallback
        def _input_should_not_be_called(*a, **kw):
            raise AssertionError(
                "handler 应该用 input_provider, 不该走 input() fallback"
            )

        monkeypatch.setattr("builtins.input", _input_should_not_be_called)

        events = await dispatch_slash(chat, "/resume")
        types = [e.type for e in events]
        assert "slash_switch_session" in types
        # provider 被调一次
        assert len(provider_calls) == 1
        assert "选" in provider_calls[0]
        # switch 到 latest session
        switch_ev = next(e for e in events if e.type == "slash_switch_session")
        assert switch_ev.content["sid"] == "s_f1f10099"

    @pytest.mark.asyncio
    async def test_no_provider_falls_back_to_input(self, monkeypatch):
        """chat.input_provider is None (default), handler fallback to_thread(input)."""
        from explain_engine.chat.session import ChatSession

        _make_done_session("s_f1f20002")
        chat = ChatSession("s_f1f20002")  # 不 set input_provider

        assert chat.input_provider is None  # baseline

        monkeypatch.setattr("builtins.input", lambda *a, **kw: "q")
        events = await dispatch_slash(chat, "/resume")
        # q 取消 → slash_resume info, 无 switch
        types = [e.type for e in events]
        assert "slash_resume" in types
        assert "slash_switch_session" not in types


class TestSlashBudgetConfig:
    """Phase 11 Wave 2.5: /budget interactive config (取代 cli flag).

    Sequential prompt (per_turn → per_session). 通过 chat.input_provider
    收输入, 用 chat.chat_state 直接读写 (兼容 EphemeralChatSession).
    """

    @pytest.mark.asyncio
    async def test_display_only_when_no_provider(self):
        """input_provider None → 仅 display, 不改 chat_state."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_bbb00001")
        chat = ChatSession("s_bbb00001")
        before = (
            chat.chat_state.budget_per_turn_limit,
            chat.chat_state.budget_per_session_limit,
        )
        events = await dispatch_slash(chat, "/budget")
        assert events[0].type == "slash_budget"
        assert "display-only" in events[0].content
        # state 不变
        assert (
            chat.chat_state.budget_per_turn_limit,
            chat.chat_state.budget_per_session_limit,
        ) == before

    @pytest.mark.asyncio
    async def test_change_per_turn_only(self):
        """Provider 返 '20', '' → per_turn=20, per_session 保持."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_bbb00002")
        chat = ChatSession("s_bbb00002")
        original_session_limit = chat.chat_state.budget_per_session_limit

        calls = []
        async def fake_provider(prompt):
            calls.append(prompt)
            return "20" if len(calls) == 1 else ""
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/budget")
        assert events[0].type == "slash_budget"
        assert "已更新" in events[0].content
        assert chat.chat_state.budget_per_turn_limit == 20
        assert chat.chat_state.budget_per_session_limit == original_session_limit
        assert len(calls) == 2  # 两次 prompt

    @pytest.mark.asyncio
    async def test_change_both_limits(self):
        """Provider 返 '20', '100' → 两个 limit 都改."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_bbb00003")
        chat = ChatSession("s_bbb00003")

        calls = []
        async def fake_provider(prompt):
            calls.append(prompt)
            return "20" if len(calls) == 1 else "100"
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/budget")
        assert events[0].type == "slash_budget"
        assert "已更新" in events[0].content
        assert chat.chat_state.budget_per_turn_limit == 20
        assert chat.chat_state.budget_per_session_limit == 100

    @pytest.mark.asyncio
    async def test_empty_input_keeps_both(self):
        """Provider 全返 '' → 两 limit 都保持 (但仍 yield slash_budget '已更新')."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_bbb00004")
        chat = ChatSession("s_bbb00004")
        before_turn = chat.chat_state.budget_per_turn_limit
        before_session = chat.chat_state.budget_per_session_limit

        async def fake_provider(prompt):
            return ""
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/budget")
        assert events[0].type == "slash_budget"
        # limit 无变化
        assert chat.chat_state.budget_per_turn_limit == before_turn
        assert chat.chat_state.budget_per_session_limit == before_session

    @pytest.mark.asyncio
    async def test_cancel_with_q(self):
        """Provider 第 1 prompt 返 'q' → 整个流程 abort."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_bbb00005")
        chat = ChatSession("s_bbb00005")
        before_turn = chat.chat_state.budget_per_turn_limit

        calls = []
        async def fake_provider(prompt):
            calls.append(prompt)
            return "q"
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/budget")
        assert events[0].type == "slash_budget"
        assert "取消" in events[0].content
        # state 不变, provider 只调一次 (per-turn 取消 → 不问 per-session)
        assert chat.chat_state.budget_per_turn_limit == before_turn
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_invalid_number_rejects(self):
        """Provider 返 'abc' → slash_error, 不改 state."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_bbb00006")
        chat = ChatSession("s_bbb00006")
        before_turn = chat.chat_state.budget_per_turn_limit

        async def fake_provider(prompt):
            return "abc"
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/budget")
        assert events[0].type == "slash_error"
        assert "abc" in events[0].content
        assert chat.chat_state.budget_per_turn_limit == before_turn

    @pytest.mark.asyncio
    async def test_negative_or_zero_rejects(self):
        """Provider 返 '-1' / '0' → slash_error."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_bbb00007")
        chat = ChatSession("s_bbb00007")
        before_turn = chat.chat_state.budget_per_turn_limit

        async def fake_provider(prompt):
            return "-1"
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/budget")
        assert events[0].type == "slash_error"
        assert ">= 1" in events[0].content
        assert chat.chat_state.budget_per_turn_limit == before_turn

    @pytest.mark.asyncio
    async def test_remaining_capped_to_new_limit(self):
        """new_limit < 当前 remaining → remaining 被 cap 到 new_limit."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_bbb00008")
        chat = ChatSession("s_bbb00008")
        # 模拟 chat 已跑过几轮, remaining 还是 default 10/50
        chat.chat_state.budget_per_turn_remaining = 10  # default
        chat.chat_state.budget_per_session_remaining = 50

        calls = []
        async def fake_provider(prompt):
            calls.append(prompt)
            return "3" if len(calls) == 1 else "20"
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/budget")
        assert events[0].type == "slash_budget"
        assert "已更新" in events[0].content
        # remaining 被 cap (从 10 → 3, 从 50 → 20)
        assert chat.chat_state.budget_per_turn_remaining == 3
        assert chat.chat_state.budget_per_session_remaining == 20

    @pytest.mark.asyncio
    async def test_ephemeral_session_supported(self):
        """Wave 1 review I-1 fold: EphemeralChatSession 也支持 /budget.

        旧 _handle_budget 用 chat.budget (BudgetCounter property), 在 ephemeral
        时 AttributeError. 新实现读 chat.chat_state 直接 work.
        """
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.persistence.storage_v2 import StorageV2

        eph = EphemeralChatSession(storage=StorageV2())

        calls = []
        async def fake_provider(prompt):
            calls.append(prompt)
            return "30" if len(calls) == 1 else "200"
        eph.input_provider = fake_provider

        # dispatch_slash 签名是 ChatSession 但 duck-typed (用 .chat_state +
        # .input_provider); ephemeral 满足契约.
        events = await dispatch_slash(eph, "/budget")
        types = [e.type for e in events]
        assert "slash_budget" in types
        # ephemeral.chat_state 被改 (promote 时拷给 real chat)
        assert eph.chat_state.budget_per_turn_limit == 30
        assert eph.chat_state.budget_per_session_limit == 200
