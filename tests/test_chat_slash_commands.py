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
    """Phase 19 Wave 7 hotfix (2026-05-27): /resume 重设为 /resume <sid> 显式 arg.

    老版无参 picker (asyncio.to_thread(input, ...)) 在 textual app 内死锁
    (textual hold stdin, builtin input 永远读不到). 修法: 删 picker, 改
    显式 sid arg. 用户流: /list 看 sid → /resume <sid>.

    所有 input_provider / builtins.input mock 已废除 — 新设计完全不依赖 stdin.
    """

    @pytest.mark.asyncio
    async def test_no_args_opens_session_picker(self):
        """Wave 7 follow-up: /resume 无参 + 有 session → slash_open_session_picker.

        (老 hotfix spec: slash_error 用法提示. 改 spec: textual ModalScreen
        picker. 详 tests/test_chat_slash_resume_picker.py)
        """
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e500001")
        chat = ChatSession("s_5e500001")
        events = await dispatch_slash(chat, "/resume")
        # 有 _make_done_session 的 session → picker event
        assert events[0].type == "slash_open_session_picker"
        assert events[0].metadata is not None
        assert "sessions" in events[0].metadata

    @pytest.mark.asyncio
    async def test_too_many_args_rejected(self):
        """/resume <sid> <extra> → slash_error (只接 1 个 sid)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e500002")
        chat = ChatSession("s_5e500002")
        events = await dispatch_slash(chat, "/resume sidA sidB")
        assert events[0].type == "slash_error"

    @pytest.mark.asyncio
    async def test_valid_sid_yields_switch(self):
        """/resume <existing_sid> 非当前 → slash_resume + slash_switch_session."""
        from explain_engine.chat.session import ChatSession
        from explain_engine.persistence.session import (
            Session,
            SessionMeta,
            SessionStore,
        )
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.state import CognitiveState

        _make_done_session("s_5e500003")
        # 加第二 session, 当 switch 目标
        meta_b = SessionMeta.new(question="qb")
        meta_b.session_id = "s_5e500099"
        state_b = CognitiveState(
            graph=ExplanationGraph(root_question="qb"),
            budget_remaining=10, root_question="qb",
        )
        SessionStore().save(Session(meta=meta_b, state=state_b))

        chat = ChatSession("s_5e500003")
        events = await dispatch_slash(chat, "/resume s_5e500099")
        types = [e.type for e in events]
        assert "slash_switch_session" in types
        switch_ev = next(e for e in events if e.type == "slash_switch_session")
        assert switch_ev.content["sid"] == "s_5e500099"

    @pytest.mark.asyncio
    async def test_nonexistent_sid_returns_error(self):
        """/resume <nonexistent_sid> → slash_error, 不 yield switch."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e500004")
        chat = ChatSession("s_5e500004")
        events = await dispatch_slash(chat, "/resume s_nonexist")
        types = [e.type for e in events]
        assert "slash_error" in types
        assert "slash_switch_session" not in types
        # 错误信息含 sid
        err = next(e for e in events if e.type == "slash_error")
        assert "s_nonexist" in err.content

    @pytest.mark.asyncio
    async def test_current_sid_noop(self):
        """/resume <current_sid> → slash_resume info, 不 yield switch."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e500007")
        chat = ChatSession("s_5e500007")
        events = await dispatch_slash(chat, "/resume s_5e500007")
        types = [e.type for e in events]
        assert "slash_switch_session" not in types
        info = next(e for e in events if e.type == "slash_resume")
        assert "已在" in info.content or "current" in info.content.lower()

    @pytest.mark.asyncio
    async def test_empty_sid_returns_error(self):
        """/resume "" (空 sid, e.g. /resume +空白) → slash_error."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_5e500008")
        chat = ChatSession("s_5e500008")
        # dispatch_slash 拆 args 用 split, 多空格会被吃掉; 这里直接调底层
        # 验空字符串 arg 被拒
        from explain_engine.chat.slash_commands import _handle_resume
        events = await _handle_resume(chat, [""])
        assert events[0].type == "slash_error"


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

        async def fake_flush(session, storage, llm=None, light_llm=None):
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

        async def fake_flush(session, storage, llm=None, light_llm=None):
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

        async def fake_flush(session, storage, llm=None, light_llm=None):
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

    @pytest.mark.asyncio
    async def test_run_with_unlimited_session_budget_passes_large_budget(self, monkeypatch):
        """Phase 15.1 hotfix: chat_state.budget_per_session_limit==0 (用户 /budget
        设无限) → /run 传大 budget 给 runtime, 不再受 state.budget_remaining 限制.

        Root cause: chat 有两套 budget (chat_state LLM-call vs CognitiveState tick).
        /budget 命令只改 chat_state; /run 老代码读 state.budget_remaining 计 tick budget.
        用户设无限 mental model: 所有耗预算的都不限. 这条 honor 它.
        """
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_b1a55001")
        chat = ChatSession("s_b1a55001", llm=object())  # type: ignore[arg-type]
        # 用户 /budget 设 per_session=0 (无限) + state.budget_remaining=5 (历史用了 15)
        chat.chat_state.budget_per_session_limit = 0
        chat.state.budget_remaining = 5

        captured = {}

        async def fake_run(state, llm, budget, on_tick=None, scheduler=None):
            captured["budget"] = budget
            state.tick = 0
            return "no_gain_for_3_ticks"

        monkeypatch.setattr("explain_engine.runtime.runtime.run", fake_run)
        await dispatch_slash(chat, "/run")
        # honor "无限": 传给 runtime 的 budget 应远超 5 (state.budget_remaining)
        assert captured["budget"] >= 10**6, (
            f"chat_state 设无限 (per_session_limit=0) 时 /run 应传大 budget, "
            f"实际 {captured.get('budget')}"
        )

    @pytest.mark.asyncio
    async def test_run_with_finite_session_budget_uses_state_remaining(self, monkeypatch):
        """Phase 15.1: chat_state.budget_per_session_limit>0 (有限预算) → 保留老行为
        max(state.budget_remaining, 1) 不变."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_b1a55002")
        chat = ChatSession("s_b1a55002", llm=object())  # type: ignore[arg-type]
        chat.chat_state.budget_per_session_limit = 100  # 有限
        chat.state.budget_remaining = 5

        captured = {}

        async def fake_run(state, llm, budget, on_tick=None, scheduler=None):
            captured["budget"] = budget
            state.tick = 0
            return "no_gain_for_3_ticks"

        monkeypatch.setattr("explain_engine.runtime.runtime.run", fake_run)
        await dispatch_slash(chat, "/run")
        assert captured["budget"] == 5  # 老行为: max(state.budget_remaining, 1)


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

    @pytest.mark.asyncio
    async def test_handle_predict_cancel_no_intervention_metadata(self):
        """Phase 16.2 Task 4.5: 用户 input 'q' 取消, event 不带 intervention metadata.

        取消分支返 "已取消." 文案, metadata is None (而非 {"intervention": "q"}).
        Wrapper 反解后跳过 intervention 字段写入 history.
        """
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_cafe0045")
        chat = ChatSession("s_cafe0045", llm=object())  # type: ignore[arg-type]

        async def fake_provider(prompt):
            return "q"
        chat.input_provider = fake_provider

        events = await dispatch_slash(chat, "/predict")
        predict_evt = next(e for e in events if e.type == "slash_predict")
        assert "已取消" in predict_evt.content
        assert predict_evt.metadata is None

    @pytest.mark.asyncio
    async def test_handle_predict_event_carries_intervention_metadata(self, monkeypatch):
        """Phase 16.2 Task 4.3: /predict 的 ChatEvent.metadata 含 intervention text.

        wrapper (Wave 3) 反解 metadata.intervention 写到 repl_history entry, 让
        resume banner / /history 命令显示完整假设文本.
        """
        from dataclasses import dataclass

        from explain_engine.chat.session import ChatSession
        _make_done_session("s_dead0004")
        chat = ChatSession("s_dead0004", llm=object())  # type: ignore[arg-type]

        async def fake_provider(prompt):
            return "假设 JEPA 解决 c_001 + c_004"
        chat.input_provider = fake_provider

        @dataclass
        class FakeReport:
            new_node_ids: list
            predicted_L0_ids: list
            activated_existing_L0: list
            propagation_acts: dict

        async def fake_predict(state, intervention_text, llm):
            return FakeReport([], [], [], {})

        monkeypatch.setattr(
            "explain_engine.engines.prediction.predict", fake_predict
        )

        events = await dispatch_slash(chat, "/predict")
        predict_evt = next(e for e in events if e.type == "slash_predict")
        assert predict_evt.metadata == {"intervention": "假设 JEPA 解决 c_001 + c_004"}


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
        """/cf 注册了, 且底层 handler 跟 /counterfactual 同 (alias 契约).

        Phase 16.2 Wave 3.9 后两 SlashCommand 各自经 _wrap_handler 单独包装
        (cf entry.cmd="cf", counterfactual entry.cmd="counterfactual"), wrapped
        是不同 instance, 但 wrapper 暴露 __wrapped__ 指向原 handler 验等价.
        """
        cf_cmd = _command_by_name("cf")
        counterfactual_cmd = _command_by_name("counterfactual")
        assert cf_cmd is not None
        assert counterfactual_cmd is not None
        assert cf_cmd.handler.__wrapped__ is counterfactual_cmd.handler.__wrapped__

    @pytest.mark.asyncio
    async def test_handle_counterfactual_event_carries_intervention_metadata(self, monkeypatch):
        """Phase 16.2 Task 4.4: /counterfactual ChatEvent.metadata 含 intervention."""
        from dataclasses import dataclass

        from explain_engine.chat.session import ChatSession
        _make_done_session("s_beef0044")
        chat = ChatSession("s_beef0044", llm=object())  # type: ignore[arg-type]

        async def fake_provider(prompt):
            return "若用 X 替代 Y"
        chat.input_provider = fake_provider

        @dataclass
        class FakeCFReport:
            removed_node_ids: list
            added_node_ids: list
            activation_diff: dict
            alt_narrative: str = ""

        async def fake_substitute(state, intervention_text, llm):
            return FakeCFReport([], [], {})

        monkeypatch.setattr(
            "explain_engine.engines.counterfactual.substitute", fake_substitute
        )

        events = await dispatch_slash(chat, "/counterfactual")
        cf_evt = next(e for e in events if e.type == "slash_counterfactual")
        assert cf_evt.metadata == {"intervention": "若用 X 替代 Y"}


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

    def test_total_count_is_26(self):
        """8 base + 6 Wave 3 + 1 alias (cf) + 3 Wave 4 + 1 Phase 12 (graph) +
        2 Phase 16 (theories + theory) + 1 Phase 16.2 (history) +
        1 Phase 17.2 (delete) + 1 Phase 18 (deepen) +
        1 Phase 19 Wave 4 (thinking) + 1 Phase 20.4 (llm) = 26."""
        assert len(DEFAULT_COMMANDS) == 26
        assert "llm" in {c.name for c in DEFAULT_COMMANDS}

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
    """Phase 14: /compress stage gate.
    Phase 17.1 Wave 8 修正: allowed 加 done/converged 让重入支持 — 用户 /predict
    加新 L0 后可再 /compress 把新 L0 归 L1, 重复 L1 由 lexicon dedup 兜底.
    """

    @pytest.mark.asyncio
    async def test_compress_allowed_at_done(self, monkeypatch):
        """Phase 17.1 Wave 8 Task 8.2: 重跑 /compress on done session → 允许通过, stage 保 done."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e000000c", stage="done")
        chat = ChatSession("s_e000000c", llm=object())  # type: ignore[arg-type]

        # Mock 4 engine 跑通整 handler 流程 (跟 bp 路径同 fake)
        async def fake_propose(state, llm, min_count=3, max_count=5, **kwargs):
            state.insight_candidates = ["c_002"]

        async def fake_score(state, llm):
            pass

        async def fake_review(state, input_provider, console=None):
            pass

        async def fake_flush(session, storage, llm=None, light_llm=None):
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
        assert "slash_error" not in types  # 不被 stage gate 拒
        # stage 保 done — decorator success_stage='done' 改 in-memory meta.
        # 注: handler 内 mid-stage persist 落盘 stage="insight_pending", decorator
        # 之后改 in-memory 但不再 persist, 所以验 in-memory 而非 reload from disk.
        assert chat._session.meta.stage == "done"

    @pytest.mark.asyncio
    async def test_compress_allowed_at_converged_falls_back_to_done(self, monkeypatch):
        """Phase 17.1 Wave 8 Task 8.3: /compress 在 converged 也允许, stage 回退到 done.

        语义: converged = '已推理' → re-compress 加 L1 后需重新 /run 推理, 所以
        stage 应回到 done (= '已归纳, 待推理'). decorator success_stage='done'
        覆盖任何入口 stage.
        """
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_e000000d", stage="converged")
        chat = ChatSession("s_e000000d", llm=object())  # type: ignore[arg-type]

        async def fake_propose(state, llm, min_count=3, max_count=5, **kwargs):
            state.insight_candidates = ["c_003"]

        async def fake_score(state, llm):
            pass

        async def fake_review(state, input_provider, console=None):
            pass

        async def fake_flush(session, storage, llm=None, light_llm=None):
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
        assert "slash_error" not in types
        # converged → done 回退 (in-memory check, 同上理由)
        assert chat._session.meta.stage == "done"

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

        async def fake_flush(session, storage, llm=None, light_llm=None):
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

        async def fake_flush(session, storage, llm=None, light_llm=None):
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

        async def fake_flush(session, storage, llm=None, light_llm=None):
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
            "history",  # Phase 16.2 Wave 5: /history 注册到 session 管理组
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


# ─── Phase 16.2 Wave 2: snapshot/delta helpers (inline state builder) ───

def _h_node(nid: str, level: int):
    from explain_engine.schema.nodes import VariableNode
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7,
        epistemic="insight" if level >= 1 else "observation",
    )


def _h_edge(eid: str, src: str, tgt: str, rtype: str = "manifests_as"):
    from explain_engine.schema.edges import RelationEdge
    return RelationEdge(
        id=eid, source_node=src, target_node=tgt,
        relation_type=rtype, confidence=0.7,
        mechanism_description="m",
    )


def _h_make_state(
    nodes: list[tuple[str, int]],
    edges: list[tuple[str, str, str]] | None = None,
):
    from explain_engine.schema.graph import ExplanationGraph
    from explain_engine.schema.state import CognitiveState
    g = ExplanationGraph(root_question="q")
    for nid, lvl in nodes:
        g.add_node(_h_node(nid, lvl))
    for eid, src, tgt in (edges or []):
        g.add_edge(_h_edge(eid, src, tgt))
    return CognitiveState(graph=g, budget_remaining=10, root_question="q")


class TestSnapshotAndDelta:
    def test_snapshot_graph_counts_by_level(self):
        from explain_engine.chat.slash_commands import _snapshot_graph
        state = _h_make_state(
            nodes=[("p_1", 0), ("p_2", 0), ("c_1", 1)],
            edges=[("e_1", "p_1", "c_1"), ("e_2", "p_2", "c_1"), ("e_3", "c_1", "p_1")],
        )
        assert _snapshot_graph(state) == {"l0": 2, "l1": 1, "l2": 0, "edges": 3}

    def test_snapshot_graph_empty_graph_returns_zeros(self):
        from explain_engine.chat.slash_commands import _snapshot_graph
        state = _h_make_state(nodes=[])
        assert _snapshot_graph(state) == {"l0": 0, "l1": 0, "l2": 0, "edges": 0}

    def test_compute_delta_positive_l1(self):
        from explain_engine.chat.slash_commands import _compute_delta
        before = {"l0": 0, "l1": 5, "l2": 0, "edges": 0}
        after = {"l0": 0, "l1": 6, "l2": 0, "edges": 0}
        assert _compute_delta(before, after) == "+1 L1"

    def test_compute_delta_negative(self):
        from explain_engine.chat.slash_commands import _compute_delta
        before = {"l0": 0, "l1": 5, "l2": 0, "edges": 0}
        after = {"l0": 0, "l1": 3, "l2": 0, "edges": 0}
        assert _compute_delta(before, after) == "-2 L1"

    def test_compute_delta_zero_omitted(self):
        from explain_engine.chat.slash_commands import _compute_delta
        before = {"l0": 0, "l1": 5, "l2": 0, "edges": 0}
        after = {"l0": 5, "l1": 5, "l2": 0, "edges": 0}
        # l1/l2/edges 不变 → 仅 "+5 现象" 单字段, l1/l2/edges 项不出现
        result = _compute_delta(before, after)
        assert result == "+5 现象"
        assert "L1" not in result and "L2" not in result and "边" not in result

    def test_compute_delta_no_change(self):
        from explain_engine.chat.slash_commands import _compute_delta
        before = {"l0": 21, "l1": 5, "l2": 12, "edges": 89}
        after = {"l0": 21, "l1": 5, "l2": 12, "edges": 89}
        assert _compute_delta(before, after) == "无变化"

    def test_compute_delta_multi_field_order(self):
        from explain_engine.chat.slash_commands import _compute_delta
        before = {"l0": 0, "l1": 0, "l2": 0, "edges": 0}
        after = {"l0": 5, "l1": 1, "l2": 12, "edges": 37}
        # 顺序: L1 → 现象 → L2 → 边 (跟 impl 排列一致)
        assert _compute_delta(before, after) == "+1 L1 / +5 现象 / +12 L2 / +37 边"

    def test_snapshot_safe_returns_none_on_exception(self, caplog):
        import logging

        from explain_engine.chat.slash_commands import _snapshot_graph_safe

        class _BrokenState:
            @property
            def graph(self):
                raise RuntimeError("boom")

        with caplog.at_level(logging.DEBUG, logger="explain_engine.chat.slash_commands"):
            result = _snapshot_graph_safe(_BrokenState())
        assert result is None
        assert any("snapshot failed" in r.message for r in caplog.records)

    def test_compute_delta_handles_none_inputs(self):
        from explain_engine.chat.slash_commands import _compute_delta
        valid = {"l0": 0, "l1": 5, "l2": 0, "edges": 0}
        # before=None
        assert _compute_delta(None, valid) == "(变化未知)"
        # after=None
        assert _compute_delta(valid, None) == "(变化未知)"
        # 两个都 None
        assert _compute_delta(None, None) == "(变化未知)"


# ─── Phase 16.2 Wave 3: dispatcher _wrap_handler test fixtures ───
#
# 用 _FakeEvent 替代真 ChatEvent — ChatEvent.metadata 字段 Wave 4 才加,
# Wave 3 wrapper 用 getattr(evt, "metadata", None) 兼容, 此处 fake 提供
# .metadata 属性让 Task 3.3/3.4 测 metadata 反解.


class _FakeEvent:
    """Minimal ChatEvent-like 双子, 让 Wave 3 test 不依赖 Wave 4 ChatEvent.metadata."""

    def __init__(self, type, content=None, metadata=None):
        self.type = type
        self.content = content
        self.metadata = metadata


def _h_make_chat_with_storage(tmp_path, monkeypatch, state=None, sid="s_test"):
    """造一个 minimal 'chat-like' object 让 _wrap_handler 能跑.

    不构 ChatSession (重) — wrapper 只用 chat.sid / chat.storage / chat.state /
    chat.is_ephemeral, fake object 提供这 4 属性即可.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    from explain_engine.persistence.storage_v2 import StorageV2
    storage = StorageV2(project_id="test_proj")
    if state is None:
        state = _h_make_state(nodes=[])

    class _FakeChat:
        pass

    chat = _FakeChat()
    chat.sid = sid
    chat.storage = storage
    chat.state = state
    chat.is_ephemeral = False
    return chat


class TestWrapHandler:
    @pytest.mark.asyncio
    async def test_wrap_handler_writes_entry_on_success(self, tmp_path, monkeypatch):
        """Task 3.1: handler 改 graph +1 现象 → wrapper 写 entry, summary 含 '+1 现象'."""
        from explain_engine.chat.slash_commands import _wrap_handler

        chat = _h_make_chat_with_storage(tmp_path, monkeypatch)

        async def fake_handler(c, args):
            # 改 graph 加 1 L0 (现象)
            c.state.graph.add_node(_h_node("p_new", 0))
            return [_FakeEvent(type="slash_fake", content="ok")]

        wrapped = _wrap_handler("fakecmd", fake_handler)
        result = await wrapped(chat, [])
        assert len(result) == 1
        assert result[0].type == "slash_fake"

        entries = chat.storage.load_repl_history(chat.sid)
        assert len(entries) == 1
        e = entries[0]
        assert e["type"] == "slash"
        assert e["cmd"] == "fakecmd"
        assert e["args"] == []
        assert "+1 现象" in e["summary"]
        assert "intervention" not in e
        assert "error" not in e
        assert "ts" in e

    @pytest.mark.asyncio
    async def test_wrap_handler_passes_through_args_and_result(
        self, tmp_path, monkeypatch
    ):
        """Task 3.2: wrapped(chat, args) 跟原 handler 完全等价 (events + 收 args 透传)."""
        from explain_engine.chat.slash_commands import _wrap_handler

        chat = _h_make_chat_with_storage(tmp_path, monkeypatch)
        received_args: list = []
        sentinel_events = [
            _FakeEvent(type="slash_a", content="x"),
            _FakeEvent(type="slash_b", content={"k": 1}),
        ]

        async def fake_handler(c, args):
            received_args.append(list(args))
            return sentinel_events

        wrapped = _wrap_handler("passthrough", fake_handler)
        result = await wrapped(chat, ["a", "b"])

        # 1. args 完整透传到 handler
        assert received_args == [["a", "b"]]
        # 2. 返回值 (events list 对象) 透传不变
        assert result is sentinel_events
        assert len(result) == 2
        assert result[0].type == "slash_a"
        assert result[1].type == "slash_b"

        # entry args 字段也应是原 args
        entries = chat.storage.load_repl_history(chat.sid)
        assert len(entries) == 1
        assert entries[0]["args"] == ["a", "b"]
        assert entries[0]["cmd"] == "passthrough"

    @pytest.mark.asyncio
    async def test_wrap_handler_reads_intervention_from_metadata(
        self, tmp_path, monkeypatch
    ):
        """Task 3.3: handler 返 event 含 metadata={'intervention':...} → entry 含 intervention 字段."""
        from explain_engine.chat.slash_commands import _wrap_handler

        chat = _h_make_chat_with_storage(tmp_path, monkeypatch)

        async def fake_handler(c, args):
            return [
                _FakeEvent(
                    type="slash_predict",
                    content="预测结果...",
                    metadata={"intervention": "假设 X 增加"},
                )
            ]

        wrapped = _wrap_handler("predict", fake_handler)
        await wrapped(chat, [])

        entries = chat.storage.load_repl_history(chat.sid)
        assert len(entries) == 1
        assert entries[0]["intervention"] == "假设 X 增加"
        assert entries[0]["cmd"] == "predict"

    @pytest.mark.asyncio
    async def test_wrap_handler_no_metadata_no_intervention_key(
        self, tmp_path, monkeypatch
    ):
        """Task 3.4: handler 返 event 无 metadata → entry dict 不含 'intervention' key.

        关键: 验 key absent (而非 None) — schema 设计要求字段省略, 不写 null.
        """
        from explain_engine.chat.slash_commands import _wrap_handler

        chat = _h_make_chat_with_storage(tmp_path, monkeypatch)

        async def fake_handler(c, args):
            # 无 metadata 参数 → 默 None
            return [_FakeEvent(type="slash_show", content="ok")]

        wrapped = _wrap_handler("show", fake_handler)
        await wrapped(chat, [])

        entries = chat.storage.load_repl_history(chat.sid)
        assert len(entries) == 1
        e = entries[0]
        assert "intervention" not in e
        # 反 KeyError 验; 也 sanity check 不等于 None
        with pytest.raises(KeyError):
            _ = e["intervention"]

    @pytest.mark.asyncio
    async def test_wrap_handler_handler_exception_writes_error_entry_then_raises(
        self, tmp_path, monkeypatch
    ):
        """Task 3.5: handler 抛 ValueError → wrapper 先写 entry (含 error 字段), 再 raise."""
        from explain_engine.chat.slash_commands import _wrap_handler

        chat = _h_make_chat_with_storage(tmp_path, monkeypatch)

        async def fake_handler(c, args):
            raise ValueError("boom")

        wrapped = _wrap_handler("brokencmd", fake_handler)
        with pytest.raises(ValueError, match="boom"):
            await wrapped(chat, [])

        entries = chat.storage.load_repl_history(chat.sid)
        assert len(entries) == 1
        e = entries[0]
        assert e["cmd"] == "brokencmd"
        assert e["error"] == "ValueError: boom"
        assert e["summary"] == "(执行失败: ValueError)"

    @pytest.mark.asyncio
    async def test_wrap_handler_append_failure_logs_warn_not_raise(
        self, tmp_path, monkeypatch, caplog
    ):
        """Task 3.6: storage.append_repl_history 抛 IOError → wrapper 吞 + log warn, 上层不见."""
        import logging

        from explain_engine.chat.slash_commands import _wrap_handler

        chat = _h_make_chat_with_storage(tmp_path, monkeypatch)

        # Monkeypatch storage.append_repl_history 抛 IOError
        def broken_append(sid, entry):
            raise OSError("disk full")

        chat.storage.append_repl_history = broken_append

        async def fake_handler(c, args):
            return [_FakeEvent(type="slash_ok", content="x")]

        wrapped = _wrap_handler("safecmd", fake_handler)
        # 调用不应 raise IOError
        with caplog.at_level(logging.WARNING, logger="explain_engine.chat.slash_commands"):
            result = await wrapped(chat, [])

        # handler 结果仍透传给上层
        assert len(result) == 1
        assert result[0].type == "slash_ok"

        # caplog 含 warn
        warn_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("append_repl_history failed" in m for m in warn_msgs)
        assert any("safecmd" in m for m in warn_msgs)

    @pytest.mark.asyncio
    async def test_wrap_handler_snapshot_failure_summary_is_unknown(
        self, tmp_path, monkeypatch
    ):
        """Task 3.7: monkeypatch _snapshot_graph 抛 → summary 退化为 '(变化未知)'."""
        from explain_engine.chat import slash_commands as sc
        from explain_engine.chat.slash_commands import _wrap_handler

        chat = _h_make_chat_with_storage(tmp_path, monkeypatch)

        def broken_snapshot(state):
            raise RuntimeError("graph corrupted")

        monkeypatch.setattr(sc, "_snapshot_graph", broken_snapshot)

        async def fake_handler(c, args):
            return [_FakeEvent(type="slash_ok", content="x")]

        wrapped = _wrap_handler("snapfail", fake_handler)
        await wrapped(chat, [])

        entries = chat.storage.load_repl_history(chat.sid)
        assert len(entries) == 1
        assert entries[0]["summary"] == "(变化未知)"

    @pytest.mark.asyncio
    async def test_wrap_handler_keyboard_interrupt_propagates_no_write(
        self, tmp_path, monkeypatch
    ):
        """Task 3.8: handler 抛 KeyboardInterrupt → wrapper 不写 history, 直 propagate."""
        from explain_engine.chat.slash_commands import _wrap_handler

        chat = _h_make_chat_with_storage(tmp_path, monkeypatch)

        async def fake_handler(c, args):
            raise KeyboardInterrupt()

        wrapped = _wrap_handler("intcmd", fake_handler)
        with pytest.raises(KeyboardInterrupt):
            await wrapped(chat, [])

        # jsonl 文件不存在 — 没写任何 entry
        path = chat.storage.session_dir(chat.sid) / "repl_history.jsonl"
        assert not path.exists()


# ─── Phase 16.2 Wave 5: /history slash 命令 test fixtures ───
#
# 注意 Wave 5 严格调 raw `_handle_history(chat, args)` (而非 dispatch_slash),
# 避开 _wrap_handler 副作用 (wrapper 会写 cmd=history entry, 干扰断言 entry 数量).
# Wave 9 e2e 才走真 wrapped /history.


def _h_make_history_entry_slash(
    cmd: str = "compress",
    summary: str = "+1 L1",
    intervention: str | None = None,
    ts: str = "2026-05-25T14:00:00+08:00",
    args: list[str] | None = None,
) -> dict:
    """造一条 type=slash entry, schema 跟 Wave 3 _build_history_entry 一致."""
    entry: dict = {
        "ts": ts,
        "type": "slash",
        "cmd": cmd,
        "args": args or [],
        "summary": summary,
    }
    if intervention is not None:
        entry["intervention"] = intervention
    return entry


def _h_make_history_entry_llm(
    user_input: str = "为什么 X?",
    assistant_text: str = "因为 Y.",
    ts: str = "2026-05-25T14:05:00+08:00",
) -> dict:
    """造一条 type=llm_turn entry (Wave 6 写入路径的样本)."""
    return {
        "ts": ts,
        "type": "llm_turn",
        "user_input": user_input,
        "assistant_text": assistant_text,
    }


def _h_make_chat_with_history(
    tmp_path, monkeypatch, entries: list[dict], sid: str = "s_test"
):
    """造 minimal chat object + 预填好 history entry (用真 StorageV2)."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    from explain_engine.persistence.storage_v2 import StorageV2
    storage = StorageV2(project_id="test_proj")
    for e in entries:
        storage.append_repl_history(sid, e)

    class _FakeChat:
        pass

    chat = _FakeChat()
    chat.sid = sid
    chat.storage = storage
    chat.state = _h_make_state(nodes=[])
    chat.is_ephemeral = False
    return chat


class TestHandleHistory:
    @pytest.mark.asyncio
    async def test_handle_history_default_shows_last_30(
        self, tmp_path, monkeypatch
    ):
        """Task 5.1: 默认 limit=30. 50 entry 时输出仅最后 30, header 含 'total=50 shown=30'."""
        from explain_engine.chat.slash_commands import _handle_history

        entries = [
            _h_make_history_entry_slash(
                cmd=f"cmd{i}",
                ts=f"2026-05-25T14:{i:02d}:00+08:00",
            )
            for i in range(50)
        ]
        chat = _h_make_chat_with_history(tmp_path, monkeypatch, entries)

        result = await _handle_history(chat, [])
        assert len(result) == 1
        assert result[0].type == "slash_history"
        out = result[0].content
        # header 含 total=50, shown=30
        assert "50" in out
        assert "30" in out
        # 应渲染最后 30 entry (cmd20-cmd49), 前 20 不出现
        assert "/cmd49" in out  # 末尾
        assert "/cmd20" in out  # 末 30 起点
        assert "/cmd19" not in out
        assert "/cmd0" not in out

    @pytest.mark.asyncio
    async def test_handle_history_limit_5(self, tmp_path, monkeypatch):
        """Task 5.2: --limit 5. 输出 5 entry, header shown=5."""
        from explain_engine.chat.slash_commands import _handle_history

        entries = [
            _h_make_history_entry_slash(
                cmd=f"cmd{i}",
                ts=f"2026-05-25T14:{i:02d}:00+08:00",
            )
            for i in range(20)
        ]
        chat = _h_make_chat_with_history(tmp_path, monkeypatch, entries)

        result = await _handle_history(chat, ["--limit", "5"])
        out = result[0].content
        # header: 20 total, 5 shown
        assert "20" in out
        assert "/cmd19" in out
        assert "/cmd15" in out
        assert "/cmd14" not in out
        # 数实际渲染条数
        assert out.count("[2026-05-25") == 5

    @pytest.mark.asyncio
    async def test_handle_history_limit_exceeds_total(
        self, tmp_path, monkeypatch
    ):
        """Task 5.3: --limit 100 但只有 7 entry → 输出 7 + total=7 shown=7."""
        from explain_engine.chat.slash_commands import _handle_history

        entries = [
            _h_make_history_entry_slash(
                cmd=f"cmd{i}",
                ts=f"2026-05-25T14:{i:02d}:00+08:00",
            )
            for i in range(7)
        ]
        chat = _h_make_chat_with_history(tmp_path, monkeypatch, entries)

        result = await _handle_history(chat, ["--limit", "100"])
        out = result[0].content
        # 全 7 entry 应渲染, 不挂掉
        assert out.count("[2026-05-25") == 7
        for i in range(7):
            assert f"/cmd{i}" in out
        # header: total=7 shown=7 (limit > total 时, shown 是 total)
        assert "7" in out

    @pytest.mark.asyncio
    async def test_handle_history_type_slash_filters(
        self, tmp_path, monkeypatch
    ):
        """Task 5.4: --type slash 过滤. 5 slash + 3 llm_turn 混 → 仅 5 slash."""
        from explain_engine.chat.slash_commands import _handle_history

        entries: list[dict] = []
        for i in range(5):
            entries.append(_h_make_history_entry_slash(
                cmd=f"cmdslash{i}",
                ts=f"2026-05-25T14:{i:02d}:00+08:00",
            ))
        for i in range(3):
            entries.append(_h_make_history_entry_llm(
                user_input=f"问题 {i}",
                assistant_text=f"回答 {i}",
                ts=f"2026-05-25T15:{i:02d}:00+08:00",
            ))
        chat = _h_make_chat_with_history(tmp_path, monkeypatch, entries)

        result = await _handle_history(chat, ["--type", "slash"])
        out = result[0].content
        # 5 slash 都在
        for i in range(5):
            assert f"/cmdslash{i}" in out
        # 3 llm_turn 不在 (user_input/assistant_text 文案应缺失)
        for i in range(3):
            assert f"问题 {i}" not in out
            assert f"回答 {i}" not in out

    @pytest.mark.asyncio
    async def test_handle_history_type_multi_equals_no_filter(
        self, tmp_path, monkeypatch
    ):
        """Task 5.5: --type slash llm_turn (多选 = 全集) → 等价无 --type, 全 8 entry 都显."""
        from explain_engine.chat.slash_commands import _handle_history

        entries: list[dict] = []
        for i in range(5):
            entries.append(_h_make_history_entry_slash(
                cmd=f"cmdslash{i}",
                ts=f"2026-05-25T14:{i:02d}:00+08:00",
            ))
        for i in range(3):
            entries.append(_h_make_history_entry_llm(
                user_input=f"问题 {i}",
                assistant_text=f"回答 {i}",
                ts=f"2026-05-25T15:{i:02d}:00+08:00",
            ))
        chat = _h_make_chat_with_history(tmp_path, monkeypatch, entries)

        result = await _handle_history(chat, ["--type", "slash", "llm_turn"])
        out = result[0].content
        # 全 5 slash 都在
        for i in range(5):
            assert f"/cmdslash{i}" in out
        # 全 3 llm_turn 都在
        for i in range(3):
            assert f"问题 {i}" in out
            assert f"回答 {i}" in out

    @pytest.mark.asyncio
    async def test_handle_history_type_dedup(self, tmp_path, monkeypatch):
        """Task 5.6: --type slash slash (重复) → dedup 后等价 --type slash, 仅 slash."""
        from explain_engine.chat.slash_commands import _handle_history

        entries = [
            _h_make_history_entry_slash(
                cmd="cmda",
                ts="2026-05-25T14:00:00+08:00",
            ),
            _h_make_history_entry_llm(
                user_input="问题 X",
                assistant_text="回答 Y",
                ts="2026-05-25T14:05:00+08:00",
            ),
        ]
        chat = _h_make_chat_with_history(tmp_path, monkeypatch, entries)

        result = await _handle_history(chat, ["--type", "slash", "slash"])
        out = result[0].content
        # slash 在, llm_turn 不在 (因 dedup 后仍仅 slash)
        assert "/cmda" in out
        assert "问题 X" not in out
        assert "回答 Y" not in out

    @pytest.mark.asyncio
    async def test_handle_history_limit_invalid_int(
        self, tmp_path, monkeypatch
    ):
        """Task 5.7: --limit abc 非整数 → slash_error, content 含 '需为 1-200 整数'."""
        from explain_engine.chat.slash_commands import _handle_history

        chat = _h_make_chat_with_history(tmp_path, monkeypatch, [])

        result = await _handle_history(chat, ["--limit", "abc"])
        assert len(result) == 1
        assert result[0].type == "slash_error"
        assert "1-200" in result[0].content
        assert "整数" in result[0].content

    @pytest.mark.asyncio
    async def test_handle_history_limit_zero_or_negative(
        self, tmp_path, monkeypatch
    ):
        """Task 5.8: --limit 0 / --limit -1 都 reject (limit < 1)."""
        from explain_engine.chat.slash_commands import _handle_history

        chat = _h_make_chat_with_history(tmp_path, monkeypatch, [])

        # 0
        result0 = await _handle_history(chat, ["--limit", "0"])
        assert result0[0].type == "slash_error"
        assert "1-200" in result0[0].content

        # -1
        result_neg = await _handle_history(chat, ["--limit", "-1"])
        assert result_neg[0].type == "slash_error"
        assert "1-200" in result_neg[0].content

    @pytest.mark.asyncio
    async def test_handle_history_limit_above_200(
        self, tmp_path, monkeypatch
    ):
        """Task 5.9: --limit 201 (超上限) → slash_error, content 含 '上限 200' + 实际值."""
        from explain_engine.chat.slash_commands import _handle_history

        chat = _h_make_chat_with_history(tmp_path, monkeypatch, [])

        result = await _handle_history(chat, ["--limit", "201"])
        assert result[0].type == "slash_error"
        assert "上限 200" in result[0].content
        assert "201" in result[0].content

    @pytest.mark.asyncio
    async def test_handle_history_type_invalid_value(
        self, tmp_path, monkeypatch
    ):
        """Task 5.10: --type foo (非 slash/llm_turn) → slash_error 含 'slash / llm_turn'."""
        from explain_engine.chat.slash_commands import _handle_history

        chat = _h_make_chat_with_history(tmp_path, monkeypatch, [])

        result = await _handle_history(chat, ["--type", "foo"])
        assert result[0].type == "slash_error"
        assert "slash" in result[0].content
        assert "llm_turn" in result[0].content
        # 含 invalid 值, 用 repr 显示
        assert "foo" in result[0].content

    @pytest.mark.asyncio
    async def test_handle_history_positional_arg_rejected(
        self, tmp_path, monkeypatch
    ):
        """Task 5.11: 位置参数 'foo' → slash_error 含 '不接位置参数'."""
        from explain_engine.chat.slash_commands import _handle_history

        chat = _h_make_chat_with_history(tmp_path, monkeypatch, [])

        result = await _handle_history(chat, ["foo"])
        assert result[0].type == "slash_error"
        assert "位置参数" in result[0].content

    @pytest.mark.asyncio
    async def test_handle_history_empty_session(self, tmp_path, monkeypatch):
        """Task 5.12: load 返 [] → slash_history event 含 BANNER_HISTORY_EMPTY 文案."""
        from explain_engine.chat.chat_copy import BANNER_HISTORY_EMPTY
        from explain_engine.chat.slash_commands import _handle_history

        chat = _h_make_chat_with_history(tmp_path, monkeypatch, [])

        result = await _handle_history(chat, [])
        assert len(result) == 1
        assert result[0].type == "slash_history"
        # 友好提示用 chat_copy 常量, 含 "无历史"
        assert result[0].content == BANNER_HISTORY_EMPTY
        assert "无历史" in result[0].content

    @pytest.mark.asyncio
    async def test_handle_history_intervention_full_not_truncated(
        self, tmp_path, monkeypatch
    ):
        """Task 5.13: 500 字 intervention 在 /history 完整显, 不截 80 字 (跟 banner 区分)."""
        from explain_engine.chat.slash_commands import _handle_history

        long_intervention = "假设干预" + "X" * 496  # 500 字
        assert len(long_intervention) == 500
        entries = [
            _h_make_history_entry_slash(
                cmd="predict",
                summary="+1 L1 / +5 现象 / +12 边",
                intervention=long_intervention,
                ts="2026-05-25T14:20:45+08:00",
            )
        ]
        chat = _h_make_chat_with_history(tmp_path, monkeypatch, entries)

        result = await _handle_history(chat, [])
        out = result[0].content
        # 完整 500 字 intervention 都在
        assert long_intervention in out
        # 不应有截断标志 "..."
        assert "..." not in out

    @pytest.mark.asyncio
    async def test_handle_history_llm_turn_full_not_truncated(
        self, tmp_path, monkeypatch
    ):
        """Task 5.14: 200 字 user_input + 200 字 assistant_text 在 /history 完整显, 不截 60 字."""
        from explain_engine.chat.slash_commands import _handle_history

        long_user = "问题前缀" + "A" * 196  # 200 字
        long_assistant = "回答前缀" + "B" * 196  # 200 字
        assert len(long_user) == 200
        assert len(long_assistant) == 200
        entries = [
            _h_make_history_entry_llm(
                user_input=long_user,
                assistant_text=long_assistant,
                ts="2026-05-25T14:27:09+08:00",
            )
        ]
        chat = _h_make_chat_with_history(tmp_path, monkeypatch, entries)

        result = await _handle_history(chat, [])
        out = result[0].content
        # 完整文本都在
        assert long_user in out
        assert long_assistant in out
        # 不应有截断 "..."
        assert "..." not in out

    def test_history_registered_in_default_commands(self):
        """Task 5.15: /history 注册到 DEFAULT_COMMANDS, 走 _wrap_handler 包装.

        验:
        1. 名字存在
        2. description 跟 COMMAND_DESCRIPTIONS["history"] 一致
        3. handler 是 wrapped (有 __wrapped__ 属性指向 _handle_history)
        """
        from explain_engine.chat.chat_copy import COMMAND_DESCRIPTIONS
        from explain_engine.chat.slash_commands import (
            DEFAULT_COMMANDS,
            _handle_history,
        )

        cmd = next((c for c in DEFAULT_COMMANDS if c.name == "history"), None)
        assert cmd is not None, "history 未注册到 DEFAULT_COMMANDS"
        assert cmd.description == COMMAND_DESCRIPTIONS["history"]
        # _wrap_handler 包装后 .__wrapped__ 指原 handler
        assert getattr(cmd.handler, "__wrapped__", None) is _handle_history

    def test_history_in_help_group_session_management(self):
        """Task 5.15: history 加入 HELP_GROUPS_ZH '管理 session' 组."""
        from explain_engine.chat.chat_copy import HELP_GROUPS_ZH

        # 找 'session 管理' / '管理 session' 组
        session_group = next(
            (
                cmds for name, cmds in HELP_GROUPS_ZH
                if "管理" in name and "session" in name
            ),
            None,
        )
        assert session_group is not None, "未找到 '管理 session' 组"
        assert "history" in session_group
