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
        # Phase 15: 中文 farewell ("再见, session 已存盘.")
        assert "再见" in events[0].content
        assert "存盘" in events[0].content

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
        # Phase 15: /show 输出全中文化 (问题: / === 因果图 / 现象). 验 3 关键串都在.
        assert "问题:" in content
        assert "=== 因果图 (" in content
        assert "现象" in content

    @pytest.mark.asyncio
    async def test_budget_display_only_when_no_provider(self):
        """Phase 11 Wave 2.5: input_provider None → display-only event."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_51a55004")
        chat = ChatSession("s_51a55004")  # input_provider 默认 None
        events = await dispatch_slash(chat, "/budget")
        assert len(events) == 1
        assert events[0].type == "slash_budget"
        # Phase 15: "(无输入通道, 仅展示 — test/非交互模式)"
        assert "仅展示" in events[0].content or "无输入通道" in events[0].content

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
    """2026-05-20 重构: /new 不再 bootstrap 新 session, 而是 yield
    slash_reset_to_ephemeral 让 REPL 清屏+回 ephemeral. 老 bootstrap+HITL+
    switch_session 路径作废."""

    @pytest.mark.asyncio
    async def test_no_args_yields_reset_event(self):
        """/new (无参) → 单 ChatEvent(type='slash_reset_to_ephemeral')."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e000001")
        chat = ChatSession("s_5e000001")
        events = await dispatch_slash(chat, "/new")
        assert len(events) == 1
        assert events[0].type == "slash_reset_to_ephemeral"
        assert events[0].content is None

    @pytest.mark.asyncio
    async def test_extra_args_silently_ignored(self):
        """`/new 为什么 X` (老 contract) → 不报错, args 静默忽略, 同样 reset.

        向前兼容老用户输 /new <question> 不抛 Usage error — 直接 reset 即可,
        用户接着输 question 走 promote_to_persistent.
        """
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e000002")
        chat = ChatSession("s_5e000002")
        events = await dispatch_slash(chat, "/new 为什么 X 现象")
        assert len(events) == 1
        assert events[0].type == "slash_reset_to_ephemeral"

    @pytest.mark.asyncio
    async def test_works_without_llm(self):
        """/new 不调 LLM → llm=None 也 OK (跟老 contract 不同, 之前要求 llm)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e000003")
        chat = ChatSession("s_5e000003")  # llm 默认 None
        events = await dispatch_slash(chat, "/new")
        assert events[0].type == "slash_reset_to_ephemeral"

    @pytest.mark.asyncio
    async def test_works_in_ephemeral(self):
        """ephemeral chat 也能 /new — no-op-ish 但仍 yield reset event
        (REPL consumer 收到后会建一个新 EphemeralChatSession, 等于刷新启动态)."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.persistence.storage_v2 import StorageV2
        eph = EphemeralChatSession(storage=StorageV2())
        events = await dispatch_slash(eph, "/new")  # type: ignore[arg-type]
        assert events[0].type == "slash_reset_to_ephemeral"

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
        # Phase 15: 中文化 ("仅展示" / "无输入通道")
        assert "仅展示" in events[0].content or "无输入通道" in events[0].content
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
    async def test_negative_rejects(self):
        """2026-05-20 hotfix: 0 现在 valid (=unlimited), 只 -1 才 reject."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_bbb00007")
        chat = ChatSession("s_bbb00007")
        before_turn = chat.chat_state.budget_per_turn_limit

        async def fake_provider(prompt):
            return "-1"
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/budget")
        assert events[0].type == "slash_error"
        assert ">= 0" in events[0].content
        assert chat.chat_state.budget_per_turn_limit == before_turn

    @pytest.mark.asyncio
    async def test_zero_means_unlimited(self):
        """2026-05-20 hotfix: 输 0 → 设 unlimited (有效输入, 不再 reject)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_bbbb0007")
        chat = ChatSession("s_bbbb0007")

        async def fake_provider(prompt):
            return "0"  # both prompts answer 0
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/budget")
        # success 路径 (slash_budget), 不是 error
        assert events[0].type == "slash_budget"
        assert "已更新" in events[0].content
        assert chat.chat_state.budget_per_turn_limit == 0
        assert chat.chat_state.budget_per_session_limit == 0

    @pytest.mark.asyncio
    async def test_commit_refills_remaining_to_limit(self):
        """2026-05-20 hotfix bug 2: /budget commit 把 remaining 拉满 (refill)
        到新 limit, 不再 min cap. 用户用尽后重设 = 重新授权 budget."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_bbb00008")
        chat = ChatSession("s_bbb00008")
        # 模拟 budget 已用尽 (用户场景: per_session 100000 跑到 0)
        chat.chat_state.budget_per_turn_remaining = 0
        chat.chat_state.budget_per_turn_limit = 100000
        chat.chat_state.budget_per_session_remaining = 0
        chat.chat_state.budget_per_session_limit = 100000

        calls = []
        async def fake_provider(prompt):
            calls.append(prompt)
            # 用户重设同样 100000 想 refill
            return "100000"
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/budget")
        assert events[0].type == "slash_budget"
        assert "已更新" in events[0].content
        # remaining refilled to limit (老逻辑 min(0,100000)=0 还是 0; bug 修)
        assert chat.chat_state.budget_per_turn_remaining == 100000
        assert chat.chat_state.budget_per_session_remaining == 100000

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


# ─────────────────────────────────────────────────────────────────────────
# Phase 11 Wave 3: 6 single-session slash + /cf alias.
# /compress /run /check /predict /counterfactual /rescore + /cf.
# 共同模式: ephemeral reject + LLM-None reject + happy-path mock + cancel.
# ─────────────────────────────────────────────────────────────────────────


def _new_ephemeral():
    """Helper: 建 EphemeralChatSession (Wave 3 reject test 共用)."""
    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.persistence.storage_v2 import StorageV2
    return EphemeralChatSession(storage=StorageV2())


class TestSlashCompress:
    """Phase 11 Wave 3: /compress ephemeral reject + happy-path mock."""

    @pytest.mark.asyncio
    async def test_ephemeral_rejects(self):
        eph = _new_ephemeral()
        events = await dispatch_slash(eph, "/compress")
        assert events[0].type == "slash_error"
        assert "compress" in events[0].content
        # Phase 15: 新文案统一引 err_ephemeral_reject — 含"建 session"/"resume"
        assert "建 session" in events[0].content or "/resume" in events[0].content

    @pytest.mark.asyncio
    async def test_no_llm_rejects(self):
        from explain_engine.chat.session import ChatSession
        # Phase 14: stage=bp 让 gate 通过, 才能撞 handler 的 llm=None check.
        _make_done_session("s_c0000001", stage="bootstrap_pending")
        chat = ChatSession("s_c0000001")  # llm=None
        events = await dispatch_slash(chat, "/compress")
        assert events[0].type == "slash_error"
        assert "llm" in events[0].content.lower()

    @pytest.mark.asyncio
    async def test_happy_path_mock(self, monkeypatch):
        """Mock propose + review + flush → slash_compress event."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_c0000002", stage="bootstrap_pending")
        chat = ChatSession("s_c0000002", llm=object())  # type: ignore[arg-type]

        called = {"propose": 0, "review": 0, "flush": 0}

        async def fake_propose(state, llm, min_count=3, max_count=5, **kwargs):
            called["propose"] += 1
            state.insight_candidates = ["c_001"]  # 留 1 个

        async def fake_score(state, llm):
            # Fix 1 (2026-05-19): /compress 现调 score_all (跟 cli 一致)
            pass

        async def fake_review(state, input_provider, console=None):
            called["review"] += 1
            # accept all 不动 candidates

        async def fake_flush(session, storage, llm=None):
            called["flush"] += 1
            return 2  # 2 var written

        monkeypatch.setattr(
            "explain_engine.engines.compression.propose_candidates", fake_propose
        )
        monkeypatch.setattr(
            "explain_engine.engines.evaluation.score_all", fake_score
        )
        monkeypatch.setattr(
            "explain_engine.hitl.cli_interactive.review_insights_async", fake_review
        )
        monkeypatch.setattr(
            "explain_engine.engines.lexicon.flush_to_lexicon", fake_flush
        )

        events = await dispatch_slash(chat, "/compress")
        assert events[0].type == "slash_compress"
        assert "完成" in events[0].content
        # Phase 15: msg_compress_done — "归纳完成: 加了 N 个模式, 其中 2 个写入概念库"
        assert "2" in events[0].content
        assert "概念库" in events[0].content
        assert called == {"propose": 1, "review": 1, "flush": 1}

    @pytest.mark.asyncio
    async def test_compress_runs_score_all_to_populate_gains(self, monkeypatch):
        """Fix 1 (2026-05-19 smoke bug): /compress 应在 propose 后 score_all,
        让 state.last_gains 非空, 否则 review_insights 显 gain 全 0.00.

        Root cause: Phase 11 Wave 3 实施 /compress 时漏 score_all step.
        cli `_run_compress` 有, chat `/compress` 漏. spec gap.
        """
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_c0000099", stage="bootstrap_pending")
        chat = ChatSession("s_c0000099", llm=object())  # type: ignore[arg-type]

        called = {"propose": 0, "score": 0, "review": 0, "flush": 0}

        async def fake_propose(state, llm, min_count=3, max_count=5, **kwargs):
            called["propose"] += 1
            state.insight_candidates = ["c_001"]

        async def fake_score(state, llm):
            called["score"] += 1
            # 真 score_all 写 state.last_gains; fake 模拟
            state.last_gains = {"c_001": 0.75}

        async def fake_review(state, input_provider, console=None):
            called["review"] += 1
            # 验 review 时 last_gains 已 populated (= score_all 在 review 前跑过)
            assert state.last_gains.get("c_001", 0.0) == 0.75, (
                "score_all 未在 review_insights 前跑 — gain 会全 0"
            )

        async def fake_flush(session, storage, llm=None):
            called["flush"] += 1
            return 0

        monkeypatch.setattr(
            "explain_engine.engines.compression.propose_candidates", fake_propose
        )
        monkeypatch.setattr(
            "explain_engine.engines.evaluation.score_all", fake_score
        )
        monkeypatch.setattr(
            "explain_engine.hitl.cli_interactive.review_insights_async", fake_review
        )
        monkeypatch.setattr(
            "explain_engine.engines.lexicon.flush_to_lexicon", fake_flush
        )

        events = await dispatch_slash(chat, "/compress")
        assert events[0].type == "slash_compress"
        # Fix 1 invariant: 调用顺序 propose → score → review → flush
        assert called["propose"] == 1
        assert called["score"] == 1, "score_all 未调用 — gain 会全 0 bug"
        assert called["review"] == 1
        assert called["flush"] == 1

    @pytest.mark.asyncio
    async def test_propose_failure_returns_error(self, monkeypatch):
        """propose_candidates 抛 → slash_error, 不调 review."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_c0000003", stage="bootstrap_pending")
        chat = ChatSession("s_c0000003", llm=object())  # type: ignore[arg-type]

        called = {"review": 0}

        async def fake_propose_fails(state, llm, min_count=3, max_count=5, **kwargs):
            raise RuntimeError("mock LLM down")

        async def fake_review(state, input_provider, console=None):
            called["review"] += 1

        monkeypatch.setattr(
            "explain_engine.engines.compression.propose_candidates", fake_propose_fails
        )
        monkeypatch.setattr(
            "explain_engine.hitl.cli_interactive.review_insights_async", fake_review
        )

        events = await dispatch_slash(chat, "/compress")
        assert events[0].type == "slash_error"
        assert "mock LLM down" in events[0].content or "RuntimeError" in events[0].content
        # review 没被调
        assert called["review"] == 0

    @pytest.mark.asyncio
    async def test_compress_output_shows_dedup_stats(self, monkeypatch):
        """Phase 13 W3.4: /compress output content includes 'near-dup' and 'new' dedup stats line.

        After propose_candidates 加 L1 候选, /compress 应 call
        compute_compress_dedup_stats(display_threshold=0.75) 计 embedding-based
        reuse stats, 在 slash_compress event content 末尾加一行
        'compress dedup: X candidates → Y near-dup (cos≥0.75) / Z new ...'.
        """
        from explain_engine.chat.session import ChatSession
        from explain_engine.schema.nodes import VariableNode
        _make_done_session("s_aa00b001", stage="bootstrap_pending")
        chat = ChatSession("s_aa00b001", llm=object())  # type: ignore[arg-type]

        async def fake_propose(state, llm, min_count=3, max_count=5, **kwargs):
            # Add 2 fake L1 candidates
            for i in range(2):
                nid = f"c_{100 + i:03d}"
                state.graph.add_node(VariableNode(
                    id=nid, name=f"n{i}", description=f"d{i}",
                    abstraction_level=1, confidence=0.7, epistemic="insight",
                ))
                state.insight_candidates.append(nid)

        async def fake_score(state, llm):
            pass

        async def fake_review(state, input_provider, console=None):
            pass

        async def fake_flush(session, storage, llm=None):
            return 0

        monkeypatch.setattr(
            "explain_engine.engines.compression.propose_candidates", fake_propose
        )
        monkeypatch.setattr(
            "explain_engine.engines.evaluation.score_all", fake_score
        )
        monkeypatch.setattr(
            "explain_engine.hitl.cli_interactive.review_insights_async", fake_review
        )
        monkeypatch.setattr(
            "explain_engine.engines.lexicon.flush_to_lexicon", fake_flush
        )

        events = await dispatch_slash(chat, "/compress")
        compress_events = [e for e in events if e.type == "slash_compress"]
        assert len(compress_events) == 1
        content = compress_events[0].content
        # Phase 15: msg_compress_done(..., dedup_reused, dedup_new) 输 dedup 行:
        # "其中 0 个与已有模式相似 (跨 session 复用), 2 个全新."
        assert "相似" in content or "复用" in content
        assert "全新" in content
        # 显示 reused / new 数字 (EXPLAIN_EMBEDDING_DISABLED=1 default → 全 new)
        assert "0 个" in content  # reused=0
        # new 数字 == 当前 candidates 总数 (包含 fake_propose 加的 2 个 + 既有)
        # _make_done_session graph 已含 1 c_001, fake_propose 加 c_100/c_101 →
        # insight_candidates 总 3
        assert "3 个全新" in content


class TestSlashRun:
    """Phase 11 Wave 3: /run reasoning loop."""

    @pytest.mark.asyncio
    async def test_ephemeral_rejects(self):
        eph = _new_ephemeral()
        events = await dispatch_slash(eph, "/run")
        assert events[0].type == "slash_error"

    @pytest.mark.asyncio
    async def test_no_llm_rejects(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_a0000001")
        chat = ChatSession("s_a0000001")
        events = await dispatch_slash(chat, "/run")
        assert events[0].type == "slash_error"
        assert "llm" in events[0].content.lower()

    @pytest.mark.asyncio
    async def test_happy_path_mock(self, monkeypatch):
        """Mock runtime_run 返 stop_reason 'converged' → slash_run event."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_a0000002")
        chat = ChatSession("s_a0000002", llm=object())  # type: ignore[arg-type]

        async def fake_run(state, llm, budget, on_tick=None, scheduler=None):
            state.tick = 5
            return "no_gain_for_3_ticks"

        # 路径: slash 内部 from explain_engine.runtime.runtime import run as runtime_run.
        # monkeypatch 必须打 runtime module 的 run, 而非 slash_commands.
        monkeypatch.setattr(
            "explain_engine.runtime.runtime.run", fake_run
        )

        events = await dispatch_slash(chat, "/run")
        assert events[0].type == "slash_run"
        # Phase 15: msg_run_done 把 stop_reason 翻成中文; tick 表 "在第 N 步停止"
        assert "推理完成" in events[0].content
        assert "5" in events[0].content
        # no_gain_for_3_ticks → "已停 3 步无新发现 (已收敛)"
        assert "无新发现" in events[0].content or "收敛" in events[0].content
        assert "no_gain_for_3_ticks" not in events[0].content


class TestSlashCheck:
    """Phase 11 Wave 3: /check multi-signal acceptance (read-only)."""

    @pytest.mark.asyncio
    async def test_ephemeral_rejects(self):
        eph = _new_ephemeral()
        events = await dispatch_slash(eph, "/check")
        assert events[0].type == "slash_error"

    @pytest.mark.asyncio
    async def test_happy_path(self):
        """真跑 aggregate_acceptance 在 _make_done_session 的 graph 上.

        _make_done_session graph: 1 L1 (c_001) + 1 L0 (p_001) + 1 manifests_as edge.
        aggregate_acceptance 不抛即 OK; content 含字段名.
        """
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000001")
        chat = ChatSession("s_e0000001")
        events = await dispatch_slash(chat, "/check")
        assert events[0].type == "slash_check"
        c = events[0].content
        # Phase 15: 中文化字段名
        assert "一致性" in c
        assert "本质重要性" in c
        assert "覆盖率" in c
        assert "薄弱因果链" in c
        assert "缺失现象" in c


class TestSlashPredict:
    """Phase 11 Wave 3: /predict interactive intervention."""

    @pytest.mark.asyncio
    async def test_ephemeral_rejects(self):
        eph = _new_ephemeral()
        events = await dispatch_slash(eph, "/predict")
        assert events[0].type == "slash_error"

    @pytest.mark.asyncio
    async def test_no_llm_rejects(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_b0000001")
        chat = ChatSession("s_b0000001")  # llm=None
        events = await dispatch_slash(chat, "/predict")
        assert events[0].type == "slash_error"
        assert "llm" in events[0].content.lower()

    @pytest.mark.asyncio
    async def test_no_provider_rejects(self):
        """No input_provider → slash_error (REPL 模式必备)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_b0000002")
        chat = ChatSession("s_b0000002", llm=object())  # type: ignore[arg-type]
        # input_provider 默认 None
        events = await dispatch_slash(chat, "/predict")
        assert events[0].type == "slash_error"
        # Phase 15: "需要在交互模式下运行 (当前无 input 通道)"
        assert "交互模式" in events[0].content or "input" in events[0].content

    @pytest.mark.asyncio
    async def test_q_cancels(self):
        """Provider 返 'q' → slash_predict 取消, prediction.predict 不调."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_b0000003")
        chat = ChatSession("s_b0000003", llm=object())  # type: ignore[arg-type]

        async def fake_provider(prompt):
            return "q"
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/predict")
        assert events[0].type == "slash_predict"
        assert "取消" in events[0].content

    @pytest.mark.asyncio
    async def test_empty_cancels(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_b0000004")
        chat = ChatSession("s_b0000004", llm=object())  # type: ignore[arg-type]

        async def fake_provider(prompt):
            return ""
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/predict")
        assert events[0].type == "slash_predict"
        assert "取消" in events[0].content

    @pytest.mark.asyncio
    async def test_happy_path_mock(self, monkeypatch):
        """Mock predict 返 fake report → slash_predict event 含 ids."""
        from dataclasses import dataclass

        from explain_engine.chat.session import ChatSession
        _make_done_session("s_b0000005")
        chat = ChatSession("s_b0000005", llm=object())  # type: ignore[arg-type]

        async def fake_provider(prompt):
            return "如果 X 增加"
        chat.input_provider = fake_provider

        @dataclass
        class FakeReport:
            new_node_ids: list
            predicted_L0_ids: list
            activated_existing_L0: list

        async def fake_predict(state, intervention_text, llm):
            assert intervention_text == "如果 X 增加"
            return FakeReport(
                new_node_ids=["c_999"],
                predicted_L0_ids=["p_999"],
                activated_existing_L0=["p_001"],
            )

        monkeypatch.setattr(
            "explain_engine.engines.prediction.predict", fake_predict
        )

        events = await dispatch_slash(chat, "/predict")
        assert events[0].type == "slash_predict"
        c = events[0].content
        assert "c_999" in c
        assert "p_999" in c
        assert "p_001" in c
        assert "如果 X 增加" in c

    @pytest.mark.asyncio
    async def test_predict_displays_node_name_and_description(self, monkeypatch):
        """Fix 3 (2026-05-19 smoke bug 2): /predict 应显 node.name + description,
        而非裸 ID (c_005). User 看到 c_005 不知是啥, 需 name + 短 desc.

        覆盖 new_nodes / predicted_L0 / activated_existing_L0 / top propagation
        全 4 处 ID display.
        """
        from dataclasses import dataclass

        from explain_engine.chat.session import ChatSession
        from explain_engine.schema.nodes import VariableNode
        _make_done_session("s_b0bf0003")
        chat = ChatSession("s_b0bf0003", llm=object())  # type: ignore[arg-type]

        # 加 c_005 + p_016 + 现有 c_001 到 graph 模拟 prediction 后状态
        chat.state.graph.add_node(VariableNode(
            id="c_005", name="银发经济", description="老龄人口消费结构",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
        chat.state.graph.add_node(VariableNode(
            id="p_016", name="老年 wellness 消费上升",
            description="50+ 群体健康养生支出占比上升",
            abstraction_level=0, confidence=0.7, epistemic="observation",
        ))

        async def fake_provider(prompt):
            return "对于银发经济会有什么影响"
        chat.input_provider = fake_provider

        @dataclass
        class FakeReport:
            new_node_ids: list
            predicted_L0_ids: list
            activated_existing_L0: list
            propagation_acts: dict

        async def fake_predict(state, intervention_text, llm):
            return FakeReport(
                new_node_ids=["c_005"],
                predicted_L0_ids=["p_016"],
                activated_existing_L0=["p_001"],  # 现有 L0 from _make_done_session
                propagation_acts={"c_001": 0.62},  # 现有 c_001 from _make_done_session
            )

        monkeypatch.setattr(
            "explain_engine.engines.prediction.predict", fake_predict
        )

        events = await dispatch_slash(chat, "/predict")
        c = events[0].content
        # name 应 surface (非裸 ID)
        assert "银发经济" in c, "c_005 应显 name 「银发经济」"
        assert "老年 wellness" in c, "p_016 应显 name"
        # propagation_acts top entry 也应显 name
        assert "c_001" in c
        # ID 也保留 (cross-reference 用)
        assert "c_005" in c

    @pytest.mark.asyncio
    async def test_predict_displays_propagation_acts(self, monkeypatch):
        """Fix 2 (2026-05-19 smoke bug): /predict 输出应含 top-K propagation_acts.

        Root cause: PredictionReport.propagation_acts 是核心信息 (新 concept
        propagation 到现 graph 的 activation map), Wave 3 _handle_predict 漏显.
        用户看 activated_existing_L0=none 觉得 engine 没干事, 实际 propagation_acts
        可能含 mid-level 变化但漏 surface.
        """
        from dataclasses import dataclass

        from explain_engine.chat.session import ChatSession
        _make_done_session("s_b0000099")
        chat = ChatSession("s_b0000099", llm=object())  # type: ignore[arg-type]

        async def fake_provider(prompt):
            return "对于银发经济会有什么影响"
        chat.input_provider = fake_provider

        @dataclass
        class FakeReport:
            new_node_ids: list
            predicted_L0_ids: list
            activated_existing_L0: list
            propagation_acts: dict

        async def fake_predict(state, intervention_text, llm):
            return FakeReport(
                new_node_ids=["c_005"],
                predicted_L0_ids=["p_016", "p_017"],
                activated_existing_L0=[],
                propagation_acts={
                    "c_001": 0.62,
                    "c_002": 0.45,
                    "c_003": 0.30,
                    "c_004": 0.12,
                    "p_001": 0.08,
                },
            )

        monkeypatch.setattr(
            "explain_engine.engines.prediction.predict", fake_predict
        )

        events = await dispatch_slash(chat, "/predict")
        assert events[0].type == "slash_predict"
        c = events[0].content
        # Top-3 (by act desc) 应显
        assert "c_001" in c
        assert "c_002" in c
        assert "c_003" in c
        # Format: 0.62 浮点显示
        assert "0.62" in c


class TestSlashCounterfactual:
    """Phase 11 Wave 3: /counterfactual + /cf alias.

    /cf 跟 /counterfactual 共享 handler 实例, 验注册到 DEFAULT_COMMANDS.
    """

    @pytest.mark.asyncio
    async def test_ephemeral_rejects(self):
        eph = _new_ephemeral()
        events = await dispatch_slash(eph, "/counterfactual")
        assert events[0].type == "slash_error"

    @pytest.mark.asyncio
    async def test_cf_alias_ephemeral_rejects(self):
        eph = _new_ephemeral()
        events = await dispatch_slash(eph, "/cf")
        assert events[0].type == "slash_error"

    @pytest.mark.asyncio
    async def test_q_cancels(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_f0000001")
        chat = ChatSession("s_f0000001", llm=object())  # type: ignore[arg-type]

        async def fake_provider(prompt):
            return "q"
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/counterfactual")
        assert events[0].type == "slash_counterfactual"
        assert "取消" in events[0].content

    @pytest.mark.asyncio
    async def test_happy_path_via_cf_alias(self, monkeypatch):
        """走 /cf alias, 验路由到同 handler (output content 同 /counterfactual)."""
        from dataclasses import dataclass, field

        from explain_engine.chat.session import ChatSession
        _make_done_session("s_f0000002")
        chat = ChatSession("s_f0000002", llm=object())  # type: ignore[arg-type]

        async def fake_provider(prompt):
            return "若 X 替代 Y"
        chat.input_provider = fake_provider

        @dataclass
        class FakeCFReport:
            removed_node_ids: list = field(default_factory=lambda: ["c_001"])
            added_node_ids: list = field(default_factory=lambda: ["c_002"])
            activation_diff: dict = field(default_factory=lambda: {"p_001": 0.3})
            alt_narrative: str | None = "test narrative"

        async def fake_substitute(state, intervention_text, llm):
            assert intervention_text == "若 X 替代 Y"
            return FakeCFReport()

        monkeypatch.setattr(
            "explain_engine.engines.counterfactual.substitute", fake_substitute
        )

        events = await dispatch_slash(chat, "/cf")
        assert events[0].type == "slash_counterfactual"
        c = events[0].content
        assert "若 X 替代 Y" in c
        assert "c_001" in c
        assert "c_002" in c
        assert "test narrative" in c

    @pytest.mark.asyncio
    async def test_cf_alias_in_default_commands(self):
        """/cf 注册了, 且 handler 跟 /counterfactual 同 (alias 契约)."""
        cf_cmd = _command_by_name("cf")
        counterfactual_cmd = _command_by_name("counterfactual")
        assert cf_cmd is not None
        assert counterfactual_cmd is not None
        assert cf_cmd.handler is counterfactual_cmd.handler


class TestSlashRescore:
    """Phase 11 Wave 3: /rescore."""

    @pytest.mark.asyncio
    async def test_ephemeral_rejects(self):
        eph = _new_ephemeral()
        events = await dispatch_slash(eph, "/rescore")
        assert events[0].type == "slash_error"

    @pytest.mark.asyncio
    async def test_no_llm_rejects(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_d0000001")
        chat = ChatSession("s_d0000001")
        events = await dispatch_slash(chat, "/rescore")
        assert events[0].type == "slash_error"
        assert "llm" in events[0].content.lower()

    @pytest.mark.asyncio
    async def test_happy_path_mock(self, monkeypatch):
        """Mock rescore_session 返 {edge_id: conf} → slash_rescore event."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_d0000002")
        chat = ChatSession("s_d0000002", llm=object())  # type: ignore[arg-type]

        async def fake_rescore(state, llm):
            return {"e_001": 0.8, "e_002": 0.6}

        monkeypatch.setattr(
            "explain_engine.engines.rescore.rescore_session", fake_rescore
        )

        events = await dispatch_slash(chat, "/rescore")
        assert events[0].type == "slash_rescore"
        c = events[0].content
        # Phase 15: msg_rescore_done — "重评完成: 2 条因果关系, 平均可信度 0.70."
        assert "2" in c
        assert "0.70" in c  # avg of 0.8, 0.6
        assert "因果关系" in c
        assert "可信度" in c

    @pytest.mark.asyncio
    async def test_empty_result_message(self, monkeypatch):
        """rescore_session 返空 dict → 友好提示无 edges."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_d0000003")
        chat = ChatSession("s_d0000003", llm=object())  # type: ignore[arg-type]

        async def fake_rescore(state, llm):
            return {}

        monkeypatch.setattr(
            "explain_engine.engines.rescore.rescore_session", fake_rescore
        )

        events = await dispatch_slash(chat, "/rescore")
        assert events[0].type == "slash_rescore"
        # Phase 15: "重评完成: 无可 rescore 的因果关系 (体现为 / 导致 类型)."
        assert "无可" in events[0].content or "重评完成" in events[0].content
        assert "因果关系" in events[0].content


class TestWave3Registry:
    """Phase 11 Wave 3: DEFAULT_COMMANDS 注册验证."""

    def test_six_new_slash_registered(self):
        names = {c.name for c in DEFAULT_COMMANDS}
        for name in ["compress", "run", "check", "predict", "counterfactual", "rescore", "cf"]:
            assert name in names, f"/{name} not registered"

    def test_total_count_is_19(self):
        """8 base + 6 Wave 3 + 1 alias (cf) + 3 Wave 4 + 1 Phase 12 (graph) = 19."""
        assert len(DEFAULT_COMMANDS) == 19

    def test_help_lists_all_wave3_commands(self):
        """/help 自动遍历 DEFAULT_COMMANDS — 验 Wave 3 6+1 都列出."""
        import asyncio

        from explain_engine.chat.session import ChatSession
        _make_done_session("s_aa000001")
        chat = ChatSession("s_aa000001")
        events = asyncio.run(dispatch_slash(chat, "/help"))
        content = events[0].content
        for name in ["compress", "run", "check", "predict", "counterfactual", "cf", "rescore"]:
            assert f"/{name}" in content, f"/help missing /{name}"


# ─────────────────────────────────────────────────────────────────────────
# Phase 11 Wave 4: 3 cross-session slash — /list /lexicon /migrate.
# Cross-session 不依赖 single session graph, ephemeral 也 work (不 reject).
# ─────────────────────────────────────────────────────────────────────────


class TestSlashList:
    """Phase 11 Wave 4: /list cross-session inspect."""

    @pytest.mark.asyncio
    async def test_empty_project(self):
        """No session → '当前项目无任何 session.' info."""
        eph = _new_ephemeral()
        events = await dispatch_slash(eph, "/list")
        assert len(events) == 1
        assert events[0].type == "slash_list"
        # Phase 15: "当前项目无任何 session."
        assert "无任何 session" in events[0].content or "无 session" in events[0].content

    @pytest.mark.asyncio
    async def test_with_sessions(self):
        """3 session 存在 → table 含 3 个 sid."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_11ee0001")
        _make_done_session("s_11ee0002")
        _make_done_session("s_11ee0003")
        chat = ChatSession("s_11ee0001")
        events = await dispatch_slash(chat, "/list")
        assert events[0].type == "slash_list"
        content = events[0].content
        for sid in ["s_11ee0001", "s_11ee0002", "s_11ee0003"]:
            assert sid in content

    @pytest.mark.asyncio
    async def test_ephemeral_works(self):
        """Wave 4 cross-session: ephemeral 不 reject."""
        _make_done_session("s_11ee0004")
        eph = _new_ephemeral()
        events = await dispatch_slash(eph, "/list")
        assert events[0].type == "slash_list"
        # 必须不是 slash_error (ephemeral reject 才会 slash_error)
        assert "ephemeral" not in events[0].content.lower()
        assert "s_11ee0004" in events[0].content


class TestSlashLexicon:
    """Phase 11 Wave 4: /lexicon cross-session inspect."""

    def _seed_lexicon(self, vars_payload):
        """Write knowledge/variables.json with given var entries."""
        import json

        from explain_engine.persistence.storage_v2 import StorageV2

        storage = StorageV2()
        knowledge_dir = storage.knowledge_dir()
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        path = knowledge_dir / "variables.json"
        lexicon = {
            "version": "1.0",
            "updated_at": "2026-05-18T00:00:00",
            "variables": vars_payload,
        }
        path.write_text(json.dumps(lexicon), encoding="utf-8")

    @pytest.mark.asyncio
    async def test_empty_lexicon(self):
        """No variables.json → 'lexicon 暂无变量' hint."""
        eph = _new_ephemeral()
        events = await dispatch_slash(eph, "/lexicon")
        assert len(events) == 1
        assert events[0].type == "slash_lexicon"
        assert "暂无变量" in events[0].content

    @pytest.mark.asyncio
    async def test_with_vars(self):
        """Seed 2 var → table 含 global_id + name."""
        self._seed_lexicon([
            {
                "global_id": "v_aaaaaaaa",
                "name": "Alpha",
                "canonical_mechanism": "mech-a",
                "abstraction_level": 1,
                "fitness": {
                    "reuse_count": 3,
                    "avg_essentialness": 0.85,
                    "last_seen_at": "2026-05-15T12:00:00",
                },
            },
            {
                "global_id": "v_bbbbbbbb",
                "name": "Beta",
                "canonical_mechanism": "mech-b",
                "abstraction_level": 2,
                "fitness": {
                    "reuse_count": 7,
                    "avg_essentialness": 0.92,
                    "last_seen_at": "2026-05-17T12:00:00",
                },
            },
        ])
        eph = _new_ephemeral()
        events = await dispatch_slash(eph, "/lexicon")
        assert events[0].type == "slash_lexicon"
        content = events[0].content
        assert "Alpha" in content
        assert "Beta" in content
        assert "v_aaaaaaaa" in content
        assert "v_bbbbbbbb" in content
        # Phase 15: abstraction_level 翻成中文 (1→模式, 2→深层原因)
        assert "模式" in content
        assert "深层原因" in content

    @pytest.mark.asyncio
    async def test_ephemeral_works(self):
        """Wave 4 cross-session: ephemeral 不 reject (即使 empty)."""
        eph = _new_ephemeral()
        events = await dispatch_slash(eph, "/lexicon")
        assert events[0].type == "slash_lexicon"
        # 不该是 slash_error
        assert events[0].type != "slash_error"


class TestSlashMigrate:
    """Phase 11 Wave 4: /migrate cross-session admin."""

    @pytest.mark.asyncio
    async def test_no_legacy_session(self, monkeypatch):
        """detect 返 [] → 'no legacy' info, 不调 migrate_all."""
        called = {"migrate_all": 0}

        def fake_detect():
            return []

        def fake_migrate_all(dry_run=False):
            called["migrate_all"] += 1
            return []

        monkeypatch.setattr(
            "explain_engine.persistence.migration.detect_legacy_sessions",
            fake_detect,
        )
        monkeypatch.setattr(
            "explain_engine.persistence.migration.migrate_all",
            fake_migrate_all,
        )

        eph = _new_ephemeral()
        events = await dispatch_slash(eph, "/migrate")
        assert len(events) == 1
        assert events[0].type == "slash_migrate"
        assert "无老" in events[0].content or "无 legacy" in events[0].content
        assert called["migrate_all"] == 0

    @pytest.mark.asyncio
    async def test_confirm_n_cancels(self, monkeypatch):
        """legacy 存在 + provider 返 'n' → 取消, migrate_all 不调用."""
        called = {"migrate_all": 0}

        def fake_detect():
            return ["s_l1abcdef", "s_l2abcdef"]

        def fake_migrate_all(dry_run=False):
            called["migrate_all"] += 1
            return []

        monkeypatch.setattr(
            "explain_engine.persistence.migration.detect_legacy_sessions",
            fake_detect,
        )
        monkeypatch.setattr(
            "explain_engine.persistence.migration.migrate_all",
            fake_migrate_all,
        )

        eph = _new_ephemeral()

        async def provider(prompt):
            return "n"

        eph.input_provider = provider
        events = await dispatch_slash(eph, "/migrate")
        assert events[0].type == "slash_migrate"
        assert "已取消" in events[0].content
        assert called["migrate_all"] == 0

    @pytest.mark.asyncio
    async def test_confirm_y_migrates(self, monkeypatch):
        """legacy 存在 + provider 返 'y' → 调 migrate_all(dry_run=False) +
        slash_migrate 含 '成功迁'."""
        called = {"migrate_all_dry": None}

        def fake_detect():
            return ["s_l1abcdef", "s_l2abcdef"]

        def fake_migrate_all(dry_run=False):
            called["migrate_all_dry"] = dry_run
            return [
                {"sid": "s_l1abcdef", "migrated": True},
                {"sid": "s_l2abcdef", "migrated": True},
            ]

        monkeypatch.setattr(
            "explain_engine.persistence.migration.detect_legacy_sessions",
            fake_detect,
        )
        monkeypatch.setattr(
            "explain_engine.persistence.migration.migrate_all",
            fake_migrate_all,
        )

        eph = _new_ephemeral()

        async def provider(prompt):
            return "y"

        eph.input_provider = provider
        events = await dispatch_slash(eph, "/migrate")
        assert events[0].type == "slash_migrate"
        # Phase 15: "成功迁移 2/2 个 session."
        assert "成功迁移 2" in events[0].content
        assert called["migrate_all_dry"] is False

    @pytest.mark.asyncio
    async def test_no_provider_display_only(self, monkeypatch):
        """legacy 存在 + chat.input_provider=None → display-only, 不跑 migrate."""
        called = {"migrate_all": 0}

        def fake_detect():
            return ["s_l1abcdef"]

        def fake_migrate_all(dry_run=False):
            called["migrate_all"] += 1
            return []

        monkeypatch.setattr(
            "explain_engine.persistence.migration.detect_legacy_sessions",
            fake_detect,
        )
        monkeypatch.setattr(
            "explain_engine.persistence.migration.migrate_all",
            fake_migrate_all,
        )

        eph = _new_ephemeral()
        # input_provider 默认 None (EphemeralChatSession default)
        events = await dispatch_slash(eph, "/migrate")
        assert events[0].type == "slash_migrate"
        # display info should mention count + skip
        assert "1" in events[0].content
        assert called["migrate_all"] == 0


class TestWave4Registry:
    """Phase 11 Wave 4: DEFAULT_COMMANDS 注册验证."""

    def test_three_new_slash_registered(self):
        names = {c.name for c in DEFAULT_COMMANDS}
        for name in ["list", "lexicon", "migrate"]:
            assert name in names, f"/{name} not registered"

    def test_help_lists_all_wave4_commands(self):
        """/help 自动遍历 — 验 Wave 4 3 个都列出."""
        import asyncio

        from explain_engine.chat.session import ChatSession
        _make_done_session("s_aa000004")
        chat = ChatSession("s_aa000004")
        events = asyncio.run(dispatch_slash(chat, "/help"))
        content = events[0].content
        for name in ["list", "lexicon", "migrate"]:
            assert f"/{name}" in content, f"/help missing /{name}"


class TestPhase12Registry:
    """Phase 12 (2026-05-19): /graph slash 注册验证."""

    def test_graph_registered(self):
        names = {c.name for c in DEFAULT_COMMANDS}
        assert "graph" in names, "/graph not registered"

    def test_help_lists_graph(self):
        """/help 自动遍历 DEFAULT_COMMANDS — 验 /graph 列出."""
        import asyncio

        from explain_engine.chat.session import ChatSession
        _make_done_session("s_aa000005")
        chat = ChatSession("s_aa000005")
        events = asyncio.run(dispatch_slash(chat, "/help"))
        content = events[0].content
        assert "/graph" in content, "/help missing /graph"


class TestSlashStageGateRun:
    """Phase 14: /run stage gate (allowed=[done], success_stage=converged)."""

    @pytest.mark.asyncio
    async def test_run_blocked_at_bootstrap_pending(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000001", stage="bootstrap_pending")
        chat = ChatSession("s_e0000001", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/run")
        types = [e.type for e in events]
        assert "slash_error" in types
        assert "slash_next_step_hint" in types
        hint = next(e for e in events if e.type == "slash_next_step_hint")
        assert "/compress" in hint.content

    @pytest.mark.asyncio
    async def test_run_blocked_at_converged(self):
        """已经 converged 重跑 /run 也拒 (需 stage=done 精确)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000002", stage="converged")
        chat = ChatSession("s_e0000002", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/run")
        assert any(e.type == "slash_error" for e in events)


class TestSlashStageGatePredict:
    """Phase 14: /predict stage gate (allowed=[done, converged], success_stage=None)."""

    @pytest.mark.asyncio
    async def test_predict_blocked_at_bootstrap_pending(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000003", stage="bootstrap_pending")
        chat = ChatSession("s_e0000003", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/predict 测试")
        assert any(e.type == "slash_error" for e in events)
        assert any(e.type == "slash_next_step_hint" for e in events)

    @pytest.mark.asyncio
    async def test_predict_blocked_at_insight_pending(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000004", stage="insight_pending")
        chat = ChatSession("s_e0000004", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/predict 测试")
        assert any(e.type == "slash_error" for e in events)

    @pytest.mark.asyncio
    async def test_predict_allowed_at_done(self):
        """stage=done → gate 通过, handler 跑 (走 user-cancel 路径避真 LLM)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000005", stage="done")
        chat = ChatSession("s_e0000005", llm=object())  # type: ignore[arg-type]

        async def fake_provider(prompt):
            return "q"  # 取消
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/predict")
        types = [e.type for e in events]
        # gate 通过 → 不是 slash_error gate 那条
        assert "slash_predict" in types
        assert not any(
            "不允许" in (e.content if isinstance(e.content, str) else "")
            for e in events
        )

    @pytest.mark.asyncio
    async def test_predict_at_converged_also_allowed(self):
        """stage=converged → gate 通过."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000006", stage="converged")
        chat = ChatSession("s_e0000006", llm=object())  # type: ignore[arg-type]

        async def fake_provider(prompt):
            return "q"
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/predict")
        types = [e.type for e in events]
        assert "slash_predict" in types


class TestSlashStageGateCounterfactual:
    """Phase 14: /counterfactual + /cf alias stage gate."""

    @pytest.mark.asyncio
    async def test_counterfactual_blocked_at_bootstrap_pending(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000007", stage="bootstrap_pending")
        chat = ChatSession("s_e0000007", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/counterfactual 测试")
        assert any(e.type == "slash_error" for e in events)
        assert any(e.type == "slash_next_step_hint" for e in events)

    @pytest.mark.asyncio
    async def test_cf_alias_blocked_at_bootstrap_pending(self):
        """/cf alias 跟 /counterfactual 一样走 gate (handler ref 同一个)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000008", stage="bootstrap_pending")
        chat = ChatSession("s_e0000008", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/cf 测试")
        assert any(e.type == "slash_error" for e in events)
        assert any(e.type == "slash_next_step_hint" for e in events)

    @pytest.mark.asyncio
    async def test_counterfactual_allowed_at_done(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000009", stage="done")
        chat = ChatSession("s_e0000009", llm=object())  # type: ignore[arg-type]

        async def fake_provider(prompt):
            return "q"
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/counterfactual")
        types = [e.type for e in events]
        assert "slash_counterfactual" in types
        assert not any(
            "不允许" in (e.content if isinstance(e.content, str) else "")
            for e in events
        )


class TestSlashStageGateRescore:
    """Phase 14: /rescore allowed=None (任意 stage), success_hint=after_rescore."""

    @pytest.mark.asyncio
    async def test_rescore_allowed_at_any_stage(self, monkeypatch):
        """allowed=None → bootstrap_pending 也允许 (不 gate)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e000000a", stage="bootstrap_pending")
        chat = ChatSession("s_e000000a", llm=object())  # type: ignore[arg-type]

        async def fake_rescore(state, llm):
            return {}
        monkeypatch.setattr(
            "explain_engine.engines.rescore.rescore_session", fake_rescore,
        )

        events = await dispatch_slash(chat, "/rescore")
        assert not any(
            "不允许" in (e.content if isinstance(e.content, str) else "")
            for e in events
        )

    @pytest.mark.asyncio
    async def test_rescore_yields_after_rescore_hint(self, monkeypatch):
        """success path → 末加 after_rescore hint event."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e000000b", stage="done")
        chat = ChatSession("s_e000000b", llm=object())  # type: ignore[arg-type]

        async def fake_rescore(state, llm):
            return {"e_001": 0.8}
        monkeypatch.setattr(
            "explain_engine.engines.rescore.rescore_session", fake_rescore,
        )

        events = await dispatch_slash(chat, "/rescore")
        types = [e.type for e in events]
        assert "slash_rescore" in types
        assert "slash_next_step_hint" in types
        hint = next(e for e in events if e.type == "slash_next_step_hint")
        assert "/show" in hint.content


class TestSlashStageGateCompress:
    """Phase 14: /compress stage gate (allowed=[bp,ip], success_stage=done)."""

    @pytest.mark.asyncio
    async def test_compress_blocked_at_done(self):
        """重跑 /compress on done session → gate 拒."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e000000c", stage="done")
        chat = ChatSession("s_e000000c", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/compress")
        assert any(e.type == "slash_error" for e in events)
        assert any(e.type == "slash_next_step_hint" for e in events)

    @pytest.mark.asyncio
    async def test_compress_blocked_at_converged(self):
        """重跑 /compress on converged session → gate 拒."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e000000d", stage="converged")
        chat = ChatSession("s_e000000d", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/compress")
        assert any(e.type == "slash_error" for e in events)

    @pytest.mark.asyncio
    async def test_compress_allowed_at_bootstrap_pending_pushes_done(self, monkeypatch):
        """bp 允许 — handler 跑完, decorator 推 stage→done + persist."""
        from explain_engine.chat.session import ChatSession
        from explain_engine.persistence.session import SessionStore
        _make_done_session("s_e000000e", stage="bootstrap_pending")
        chat = ChatSession("s_e000000e", llm=object())  # type: ignore[arg-type]

        async def fake_propose(state, llm, min_count=3, max_count=5, **kwargs):
            state.insight_candidates = ["c_001"]

        async def fake_score(state, llm):
            pass

        async def fake_review(state, input_provider, console=None):
            pass

        async def fake_flush(session, storage, llm=None):
            return 0

        monkeypatch.setattr(
            "explain_engine.engines.compression.propose_candidates", fake_propose
        )
        monkeypatch.setattr(
            "explain_engine.engines.evaluation.score_all", fake_score
        )
        monkeypatch.setattr(
            "explain_engine.hitl.cli_interactive.review_insights_async", fake_review
        )
        monkeypatch.setattr(
            "explain_engine.engines.lexicon.flush_to_lexicon", fake_flush
        )

        events = await dispatch_slash(chat, "/compress")
        types = [e.type for e in events]
        assert "slash_compress" in types
        assert "slash_next_step_hint" in types
        # decorator 已推 stage 到 done + persist
        meta = SessionStore().load("s_e000000e").meta
        assert meta.stage == "done"


class TestCompressMidStageResilience:
    """Phase 14: /compress 中断恢复 — propose+score 完后 set stage=ip + persist."""

    @pytest.mark.asyncio
    async def test_mid_stage_set_after_score(self, monkeypatch):
        """propose+score 完 → review 之前 stage 应已是 insight_pending 落盘.

        模拟: user 在 HITL review 时取消 (KeyboardInterrupt) — 重入 ip 短路 LLM
        (Task 15 覆盖).
        """
        from explain_engine.chat.session import ChatSession
        from explain_engine.persistence.session import SessionStore
        _make_done_session("s_e000000f", stage="bootstrap_pending")
        chat = ChatSession("s_e000000f", llm=object())  # type: ignore[arg-type]

        async def fake_propose(state, llm, min_count=3, max_count=5, **kwargs):
            state.insight_candidates = ["c_001"]

        async def fake_score(state, llm):
            pass

        async def fake_review_cancel(state, input_provider, console=None):
            raise KeyboardInterrupt()

        monkeypatch.setattr(
            "explain_engine.engines.compression.propose_candidates", fake_propose,
        )
        monkeypatch.setattr(
            "explain_engine.engines.evaluation.score_all", fake_score,
        )
        monkeypatch.setattr(
            "explain_engine.hitl.cli_interactive.review_insights_async",
            fake_review_cancel,
        )

        with pytest.raises(KeyboardInterrupt):
            await dispatch_slash(chat, "/compress")

        meta = SessionStore().load("s_e000000f").meta
        assert meta.stage == "insight_pending"


class TestCompressInsightPendingShortCircuit:
    """Phase 14: stage=ip 入口 /compress → 跳 LLM (propose+score), 直接进 review."""

    @pytest.mark.asyncio
    async def test_ip_entry_skips_propose_and_score(self, monkeypatch):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000010", stage="insight_pending")
        chat = ChatSession("s_e0000010", llm=object())  # type: ignore[arg-type]

        calls = {"propose": 0, "score": 0, "review": 0, "flush": 0}

        async def track_propose(state, llm, min_count=3, max_count=5, **kwargs):
            calls["propose"] += 1

        async def track_score(state, llm):
            calls["score"] += 1

        async def fake_review(state, input_provider, console=None):
            calls["review"] += 1

        async def fake_flush(session, storage, llm=None):
            calls["flush"] += 1
            return 0

        monkeypatch.setattr(
            "explain_engine.engines.compression.propose_candidates", track_propose,
        )
        monkeypatch.setattr(
            "explain_engine.engines.evaluation.score_all", track_score,
        )
        monkeypatch.setattr(
            "explain_engine.hitl.cli_interactive.review_insights_async", fake_review,
        )
        monkeypatch.setattr(
            "explain_engine.engines.lexicon.flush_to_lexicon", fake_flush,
        )

        events = await dispatch_slash(chat, "/compress")
        # ip 入口: propose / score 不调
        assert calls["propose"] == 0, "ip 入口不该重跑 propose"
        assert calls["score"] == 0, "ip 入口不该重跑 score"
        # review / flush 仍走
        assert calls["review"] == 1
        assert calls["flush"] == 1
        # 跑完 stage 推到 done (decorator)
        assert any(e.type == "slash_compress" for e in events)

    @pytest.mark.asyncio
    async def test_bp_entry_still_runs_propose_and_score(self, monkeypatch):
        """对照: bp 入口仍跑 propose + score (Task 15 不破 Task 13/14)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000011", stage="bootstrap_pending")
        chat = ChatSession("s_e0000011", llm=object())  # type: ignore[arg-type]

        calls = {"propose": 0, "score": 0}

        async def track_propose(state, llm, min_count=3, max_count=5, **kwargs):
            calls["propose"] += 1
            state.insight_candidates = ["c_001"]

        async def track_score(state, llm):
            calls["score"] += 1

        async def fake_review(state, input_provider, console=None):
            pass

        async def fake_flush(session, storage, llm=None):
            return 0

        monkeypatch.setattr(
            "explain_engine.engines.compression.propose_candidates", track_propose,
        )
        monkeypatch.setattr(
            "explain_engine.engines.evaluation.score_all", track_score,
        )
        monkeypatch.setattr(
            "explain_engine.hitl.cli_interactive.review_insights_async", fake_review,
        )
        monkeypatch.setattr(
            "explain_engine.engines.lexicon.flush_to_lexicon", fake_flush,
        )

        await dispatch_slash(chat, "/compress")
        assert calls["propose"] == 1
        assert calls["score"] == 1


class TestHelpGrouping:
    """Phase 14 Task 16: /help 6 分组渲染 + 19 命令全在 + /cf alias 行."""

    @pytest.mark.asyncio
    async def test_help_includes_all_six_group_headers(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000012")
        chat = ChatSession("s_e0000012")
        events = await dispatch_slash(chat, "/help")
        content = events[0].content
        # Phase 15 起 6 中文 group (chat_copy.HELP_GROUPS_ZH)
        for header in (
            "推进 session",
            "干预分析",
            "查看状态",
            "管理 session",
            "其他",
            "帮助 / 退出",
        ):
            assert header in content, f"missing group header: {header}"

    @pytest.mark.asyncio
    async def test_help_includes_all_commands(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000013")
        chat = ChatSession("s_e0000013")
        events = await dispatch_slash(chat, "/help")
        content = events[0].content
        for cmd in (
            "compress", "run", "rescore", "predict", "counterfactual",
            "show", "graph", "check", "new", "resume", "list", "lexicon",
            "budget", "compact", "save", "migrate", "help", "quit",
        ):
            assert f"/{cmd}" in content, f"missing /{cmd}"

    @pytest.mark.asyncio
    async def test_help_includes_cf_alias_line(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e0000014")
        chat = ChatSession("s_e0000014")
        events = await dispatch_slash(chat, "/help")
        content = events[0].content
        assert "/cf" in content
        assert "alias" in content.lower() or "counterfactual" in content


class TestSlashRegistryUsesChineseDescriptions:
    """Phase 15 Task 10: DEFAULT_COMMANDS desc 全中文 + 无 jargon."""

    def test_all_commands_have_chinese_description(self):
        import re

        from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
        chinese_pattern = re.compile(r'[一-鿿]')
        for c in DEFAULT_COMMANDS:
            assert chinese_pattern.search(c.description), (
                f"/{c.name} 无中文 description: {c.description!r}"
            )

    def test_no_english_jargon_in_descriptions(self):
        from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
        forbidden = [
            "propose_candidates", "HITL", "reasoning loop",
            "multi-signal", "manifests_as", "storage_v2",
            "Multi-signal", "abstraction",
        ]
        for c in DEFAULT_COMMANDS:
            for f in forbidden:
                assert f not in c.description, (
                    f"/{c.name} desc 仍含 jargon: '{f}' in '{c.description}'"
                )


class TestHelpGroupingChinese:
    """Phase 15 Task 11: /help 中文 group header + 不含 Phase 14 英文 jargon."""

    @pytest.mark.asyncio
    async def test_help_shows_chinese_group_headers(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_a0000001")
        chat = ChatSession("s_a0000001")
        events = await dispatch_slash(chat, "/help")
        content = events[0].content
        for header in (
            "推进 session",
            "干预分析",
            "查看状态",
            "管理 session",
            "其他",
            "帮助 / 退出",
        ):
            assert header in content, f"missing chinese group header: {header}"

    @pytest.mark.asyncio
    async def test_help_no_english_group_names(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_a0000002")
        chat = ChatSession("s_a0000002")
        events = await dispatch_slash(chat, "/help")
        content = events[0].content
        # Phase 14 老 group name 应被替换
        assert "Session 推进" not in content
        assert "Inspection" not in content
