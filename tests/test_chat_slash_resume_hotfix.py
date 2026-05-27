"""Phase 19 Wave 7 hotfix: /resume textual 模式死锁 regression.

User report: 在 textual ExplainChatApp 内输 /resume 后 app 卡死, 输任何东西
都无效, Ctrl+C 也无法退出.

根因: 老 _handle_resume 无参数时弹 picker, 用 chat.input_provider 收选号.
textual app 不设 input_provider (= None), 老 fallback path 走
`asyncio.to_thread(input, ...)`. 但 textual 已 hold stdin (raw mode),
builtin input() 阻塞读 stdin 永远读不到 → 整个 app 死锁. Ctrl+C 被
textual 自己 capture 但 input() 子线程不响应 signal.

修法: /resume 必须带 sid arg.

- /resume (无 args) → slash_error 用法提示 (引导 /list 看 sid)
- /resume <sid> 不存在 → slash_error sid 不存在
- /resume <sid> 等于 chat.sid → slash_resume info "已在该 session"
- /resume <sid> 合法且不等当前 → slash_resume + slash_switch_session

彻底删 input_provider 依赖, 从根上消除 textual 死锁可能.

注意: 用 cli prompt_toolkit REPL (非 textual) 也走新逻辑 — 一致性比向后
兼 picker 重要. 用户改用 /list 看 sid → /resume <sid>.
"""

from __future__ import annotations

import asyncio

import pytest

from explain_engine.chat.slash_commands import dispatch_slash
from tests.test_chat_session import _make_done_session

# ─── 1. 死锁不复发 — 无参 /resume 5s 内必返 ───


class TestResumeNoArgsDoesNotHang:
    """无 args 必须立即返 (slash_open_session_picker 或 slash_error), 不阻塞.

    Wave 7 hotfix (9838507) 原 spec: 无 args → slash_error 用法提示.
    Wave 7 follow-up (本 commit) 新 spec: 无 args + 有 session → 弹 textual
    modal picker (slash_open_session_picker), 不再要求 user /list 复制 sid.
    无 args + 空 session list → slash_error (没什么可选).

    本 test 核心断言: 不阻塞 + 不调 builtin input / input_provider. event type
    可以是 slash_open_session_picker (有 session) 或 slash_error (空 list).
    详细 picker 行为见 tests/test_chat_slash_resume_picker.py.
    """

    @pytest.mark.asyncio
    async def test_no_args_returns_within_timeout(self):
        """/resume 无 args → 5s 内必返 (有 session 弹 picker / 空 slash_error)."""
        from explain_engine.chat.session import ChatSession

        _make_done_session("s_de4d10c4")
        chat = ChatSession("s_de4d10c4")
        assert chat.input_provider is None  # baseline: textual 模式不设

        # asyncio.wait_for 5s timeout — 老 bug 会 TimeoutError, fix 后必须立即返
        events = await asyncio.wait_for(dispatch_slash(chat, "/resume"), timeout=5.0)

        assert len(events) == 1
        # 当前 project 至少有 _make_done_session 建的那个 sid → picker event
        assert events[0].type == "slash_open_session_picker", (
            f"无 args + 有 session 应弹 picker, got {events[0].type}"
        )

    @pytest.mark.asyncio
    async def test_no_args_does_not_call_input_or_provider(self, monkeypatch):
        """无 args path 完全不调 input() / input_provider — 走 modal event 路径."""
        from explain_engine.chat.session import ChatSession

        _make_done_session("s_de4d10c5")
        chat = ChatSession("s_de4d10c5")

        def _input_blacklist(*a, **kw):
            raise AssertionError("/resume 无 args 不该 fallback builtin input")

        async def _provider_blacklist(prompt):
            raise AssertionError("/resume 无 args 不该调 input_provider")

        monkeypatch.setattr("builtins.input", _input_blacklist)
        chat.input_provider = _provider_blacklist

        events = await dispatch_slash(chat, "/resume")
        # 不抛 = blacklist 路径没被调到. event type 视 session list 而定.
        assert events[0].type in ("slash_open_session_picker", "slash_error")


# ─── 2. /resume <sid> 正常切换 ───


class TestResumeWithSidArg:
    """带 sid arg → 不弹 picker, 直接验证 + switch / reject."""

    @pytest.mark.asyncio
    async def test_valid_sid_yields_switch(self):
        """/resume <existing_sid> (非当前) → slash_resume + slash_switch_session."""
        from explain_engine.chat.session import ChatSession
        from explain_engine.persistence.session import (
            Session,
            SessionMeta,
            SessionStore,
        )
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.state import CognitiveState

        _make_done_session("s_de4d10c6")
        # 加第二 session, 当 switch target
        meta_b = SessionMeta.new(question="qb")
        meta_b.session_id = "s_de4d10c7"
        state_b = CognitiveState(
            graph=ExplanationGraph(root_question="qb"),
            budget_remaining=10, root_question="qb",
        )
        SessionStore().save(Session(meta=meta_b, state=state_b))

        chat = ChatSession("s_de4d10c6")
        events = await dispatch_slash(chat, "/resume s_de4d10c7")
        types = [e.type for e in events]
        assert "slash_switch_session" in types
        switch_ev = next(e for e in events if e.type == "slash_switch_session")
        assert switch_ev.content["sid"] == "s_de4d10c7"

    @pytest.mark.asyncio
    async def test_current_sid_returns_already_there(self):
        """/resume <current_sid> → slash_resume info "已在该 session", 不 yield switch."""
        from explain_engine.chat.session import ChatSession

        _make_done_session("s_de4d10c8")
        chat = ChatSession("s_de4d10c8")
        events = await dispatch_slash(chat, "/resume s_de4d10c8")
        types = [e.type for e in events]
        assert "slash_switch_session" not in types
        assert "slash_resume" in types
        # 提示已在该 session
        info = next(e for e in events if e.type == "slash_resume")
        assert "已在" in info.content or "current" in info.content.lower()

    @pytest.mark.asyncio
    async def test_nonexistent_sid_returns_error(self):
        """/resume <nonexistent_sid> → slash_error sid 不存在."""
        from explain_engine.chat.session import ChatSession

        _make_done_session("s_de4d10c9")
        chat = ChatSession("s_de4d10c9")
        events = await dispatch_slash(chat, "/resume s_nonexist")
        assert events[0].type == "slash_error"
        # 错误信息提到 sid 不存在
        assert "s_nonexist" in events[0].content


# ─── 3. textual.pilot 真集成: textual app 内输 /resume 不 hang ───


class TestResumeInTextualApp:
    """端到端: textual ExplainChatApp 内输 /resume 不死锁.

    User 真报告 path: 启动 textual app → 输 /resume + Enter → app 卡死.
    fix 后必须: 5s 内 app 仍 responsive + input cleared.

    Wave 7 follow-up: 空 project → slash_error 渲染一行 Static.
    有 session 的 picker push 路径见 tests/test_chat_slash_resume_picker.py.
    """

    @pytest.mark.asyncio
    async def test_resume_no_args_in_textual_does_not_hang(
        self, tmp_path, monkeypatch
    ) -> None:
        """端到端 textual.pilot: /resume + Enter → 5s 内返, app 仍 responsive."""
        from unittest.mock import AsyncMock

        from textual.containers import VerticalScroll
        from textual.widgets import Input, Static

        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.chat.tui_app import ExplainChatApp
        from explain_engine.persistence.storage_v2 import StorageV2

        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_resume_textual_hotfix")

        ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
        app = ExplainChatApp(
            llm=AsyncMock(),
            light_llm=AsyncMock(),
            ephemeral_chat=ephemeral,
        )

        async def _scenario() -> None:
            async with app.run_test() as pilot:
                await pilot.pause()
                prompt = app.query_one("#prompt", Input)
                prompt.focus()
                await pilot.pause()
                # 先记 baseline statics count (banner mount 数)
                container = app.query_one("#output", VerticalScroll)
                baseline_count = len(list(container.query(Static)))

                prompt.value = "/resume"
                await pilot.press("enter")
                # 多 pause 确保 async dispatch_slash + _render_event 完
                await pilot.pause()
                await pilot.pause()
                await pilot.pause()

                # 验 1: 没 hang (test 走到这里就说明 app 没死锁)
                # 验 2: 新增至少 1 个 Static (slash_error 渲染)
                statics = list(container.query(Static))
                assert len(statics) > baseline_count, (
                    f"/resume 后无新增 Static, baseline={baseline_count}, "
                    f"now={len(statics)}. 可能 slash_error 没渲染."
                )
                # 验 3: Input 已清空 (Input.Submitted handler cleared it,
                # 说明 dispatch_slash 跑完没卡)
                assert prompt.value == "", (
                    f"Input value 未清空 ({prompt.value!r}), "
                    "Input.Submitted handler 未完成 → 可能 dispatch_slash 卡住."
                )

        # 10s timeout — 老 bug 会无限 hang. 修后该 within 1s 返
        await asyncio.wait_for(_scenario(), timeout=10.0)
