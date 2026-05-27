"""Phase 19 Wave 7 follow-up: /resume 不带 sid → textual ModalScreen 交互式 picker.

上一轮 hotfix (9838507) 把 /resume 无 args 简单返"用法提示"避死锁. user 反馈
期望真 picker — 输 `/resume` 弹 session 列表, ↑↓ 选, Enter 确认.

textual ModalScreen + OptionList 是 native widget, 走 textual message pump,
跟之前 asyncio.to_thread(input) 完全不同路径 — 不会跟 textual stdin 抢. 不会
死锁.

新 spec:
- /resume (无 args) + 有 session list → slash_open_session_picker event,
  REPL (tui_app) 接到 push SessionPickerScreen.
- /resume (无 args) + 空 session list → slash_error "无 session 可 resume".
- /resume <sid> 行为不变 (老 spec).

ChatEvent 协议:
- type: "slash_open_session_picker"
- content: str (zh hint, e.g. "选择 session...")
- metadata: {
    "sessions": [
      {"sid": str, "question": str, "stage": str, "created_at": float},
      ...
    ],
    "current_sid": str,  # 当前 chat.sid (picker 可标 "当前")
  }
"""

from __future__ import annotations

import asyncio

import pytest

from explain_engine.chat.slash_commands import dispatch_slash
from explain_engine.persistence.session import (
    Session,
    SessionMeta,
    SessionStore,
)
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState
from tests.test_chat_session import _make_done_session


def _make_extra_session(sid: str, question: str) -> None:
    """Helper: 加一个额外的 session (not via _make_done_session) 当 picker 备选."""
    meta = SessionMeta.new(question=question)
    meta.session_id = sid
    state = CognitiveState(
        graph=ExplanationGraph(root_question=question),
        budget_remaining=10,
        root_question=question,
    )
    SessionStore().save(Session(meta=meta, state=state))


# ─── 1. /resume 无 args + 有 session → yield slash_open_session_picker ───


class TestResumePickerOpensModal:
    """无 args + 有 session list → slash_open_session_picker event."""

    @pytest.mark.asyncio
    async def test_no_args_with_sessions_yields_open_picker(
        self, tmp_path, monkeypatch
    ) -> None:
        """/resume 无 args, 当前 project 有 2 个 session → slash_open_session_picker."""
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_resume_picker_open")

        from explain_engine.chat.session import ChatSession

        _make_done_session("s_aaaa001a")
        _make_extra_session("s_aaaa001b", "另一个 session")

        chat = ChatSession("s_aaaa001a")

        events = await asyncio.wait_for(
            dispatch_slash(chat, "/resume"), timeout=5.0
        )

        # 应单 event: slash_open_session_picker
        assert len(events) == 1
        ev = events[0]
        assert ev.type == "slash_open_session_picker", (
            f"无 args + 有 session 应 yield slash_open_session_picker, got {ev.type}"
        )
        # metadata 含 sessions list + current_sid
        assert ev.metadata is not None
        assert "sessions" in ev.metadata
        sessions = ev.metadata["sessions"]
        assert isinstance(sessions, list)
        assert len(sessions) >= 2
        # 每个 entry 含 sid + question
        for s in sessions:
            assert "sid" in s
            assert "question" in s
        sids = {s["sid"] for s in sessions}
        assert "s_aaaa001a" in sids
        assert "s_aaaa001b" in sids
        # current_sid 标当前
        assert ev.metadata.get("current_sid") == "s_aaaa001a"

    @pytest.mark.asyncio
    async def test_no_args_empty_project_yields_error(
        self, tmp_path, monkeypatch
    ) -> None:
        """/resume 无 args + 空 project (无 session list) → slash_error 提示无可 resume.

        Edge case: 用户刚装 project, 还没 /deepen 过 → 没 session. 弹 picker
        是浪费, 直接 slash_error 提示先 /deepen 或 explain new.
        """
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_resume_picker_empty")

        # 只有当前 chat 自己 (ChatSession 必须存在的 sid)
        from explain_engine.chat.session import ChatSession

        _make_done_session("s_aaaa002a")
        chat = ChatSession("s_aaaa002a")

        # 删 self 的 session 让 list 返空 — 不行 ChatSession 还 reference. 用
        # 别的 project_id 让 SessionStore.list() 返空.
        # 直接: chat 已 load, 但 SessionStore.list 重 mock 返 []
        monkeypatch.setattr(
            "explain_engine.persistence.session.SessionStore.list",
            lambda self: [],
        )

        events = await dispatch_slash(chat, "/resume")
        # 空 list 应是 slash_error, 不是 picker
        assert len(events) == 1
        assert events[0].type == "slash_error"
        assert (
            "无" in events[0].content
            or "empty" in events[0].content.lower()
            or "暂无" in events[0].content
            or "没有" in events[0].content
        ), f"应提示无 session, got: {events[0].content!r}"


# ─── 2. picker event 不阻塞 / 不调 input_provider ───


class TestResumePickerNoBlocking:
    """picker event yield 是同步立返, 不调 input_provider / builtin input."""

    @pytest.mark.asyncio
    async def test_no_args_does_not_call_input_provider(
        self, tmp_path, monkeypatch
    ) -> None:
        """/resume 无 args → 不调 input_provider (textual modal 是 dispatch, 不是 sync)."""
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_resume_picker_noblock")

        from explain_engine.chat.session import ChatSession

        _make_done_session("s_aaaa003a")
        _make_extra_session("s_aaaa003b", "q3b")

        chat = ChatSession("s_aaaa003a")

        async def _provider_blacklist(prompt):
            raise AssertionError("/resume 无 args 不该调 input_provider — 走 modal event")

        def _input_blacklist(*a, **kw):
            raise AssertionError("/resume 无 args 不该 fallback builtin input")

        chat.input_provider = _provider_blacklist
        monkeypatch.setattr("builtins.input", _input_blacklist)

        events = await asyncio.wait_for(
            dispatch_slash(chat, "/resume"), timeout=2.0
        )
        # 不抛 = 没调过 blacklist 路径
        assert events[0].type == "slash_open_session_picker"


# ─── 3. textual app 内 picker 真显 ─── (集成 smoke)


class TestResumePickerTextualIntegration:
    """端到端: textual ExplainChatApp 输 /resume → SessionPickerScreen pushed."""

    @pytest.mark.asyncio
    async def test_resume_no_args_pushes_session_picker_screen(
        self, tmp_path, monkeypatch
    ) -> None:
        """/resume 无 args + Enter → app push SessionPickerScreen (5s 内, 不 hang)."""
        from unittest.mock import AsyncMock

        from textual.widgets import Input

        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.chat.tui_app import ExplainChatApp
        from explain_engine.persistence.storage_v2 import StorageV2

        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_resume_picker_textual")

        # 准备 2 个 session 让 picker 有内容
        _make_done_session("s_aaaa004a")
        _make_extra_session("s_aaaa004b", "q4b")

        ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
        app = ExplainChatApp(
            llm=AsyncMock(),
            light_llm=AsyncMock(),
            ephemeral_chat=ephemeral,
            show_splash=False,  # 跳 splash 快
        )

        async def _scenario() -> None:
            async with app.run_test() as pilot:
                await pilot.pause()
                prompt = app.query_one("#prompt", Input)
                prompt.focus()
                await pilot.pause()

                prompt.value = "/resume"
                await pilot.press("enter")
                await pilot.pause()
                await pilot.pause()
                await pilot.pause()

                # 验: 现 screen stack 顶层是 SessionPickerScreen (modal)
                from explain_engine.chat.tui_app import SessionPickerScreen

                top_screen = app.screen_stack[-1]
                assert isinstance(top_screen, SessionPickerScreen), (
                    f"输 /resume 后顶层 screen 应是 SessionPickerScreen, "
                    f"got {type(top_screen).__name__}"
                )

        await asyncio.wait_for(_scenario(), timeout=10.0)

    @pytest.mark.asyncio
    async def test_picker_select_dismisses_with_sid_and_switches(
        self, tmp_path, monkeypatch
    ) -> None:
        """picker 内选一个 entry + Enter → picker dismiss → chat 切到选的 sid."""
        from unittest.mock import AsyncMock

        from textual.widgets import Input

        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.chat.tui_app import ExplainChatApp
        from explain_engine.persistence.storage_v2 import StorageV2

        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_resume_picker_select")

        _make_done_session("s_aaaa005a")
        _make_extra_session("s_aaaa005b", "q5b — pick me")

        ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
        app = ExplainChatApp(
            llm=AsyncMock(),
            light_llm=AsyncMock(),
            ephemeral_chat=ephemeral,
            show_splash=False,
        )

        async def _scenario() -> None:
            async with app.run_test() as pilot:
                await pilot.pause()
                prompt = app.query_one("#prompt", Input)
                prompt.focus()
                await pilot.pause()

                prompt.value = "/resume"
                await pilot.press("enter")
                await pilot.pause()
                await pilot.pause()
                await pilot.pause()

                from explain_engine.chat.tui_app import SessionPickerScreen

                top = app.screen_stack[-1]
                assert isinstance(top, SessionPickerScreen)
                # 直接调 dismiss 选 sid (bypass key binding)
                top.dismiss("s_aaaa005b")
                await pilot.pause()
                await pilot.pause()
                await pilot.pause()

                # 验 chat 已切到新 sid
                from explain_engine.chat.session import ChatSession
                assert isinstance(app.chat, ChatSession), (
                    f"picker dismiss(sid) 后 app.chat 应是 ChatSession, "
                    f"got {type(app.chat).__name__}"
                )
                assert app.chat.sid == "s_aaaa005b", (
                    f"chat.sid 应等于 picker 选的 sid, got {app.chat.sid}"
                )

        await asyncio.wait_for(_scenario(), timeout=10.0)

    @pytest.mark.asyncio
    async def test_picker_cancel_does_not_switch(
        self, tmp_path, monkeypatch
    ) -> None:
        """picker Escape (dismiss None) → 不切 chat, app 回主 layer."""
        from unittest.mock import AsyncMock

        from textual.widgets import Input

        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.chat.tui_app import ExplainChatApp
        from explain_engine.persistence.storage_v2 import StorageV2

        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_resume_picker_cancel")

        _make_done_session("s_aaaa006a")
        _make_extra_session("s_aaaa006b", "q6b")

        ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
        original_chat = ephemeral
        app = ExplainChatApp(
            llm=AsyncMock(),
            light_llm=AsyncMock(),
            ephemeral_chat=ephemeral,
            show_splash=False,
        )

        async def _scenario() -> None:
            async with app.run_test() as pilot:
                await pilot.pause()
                prompt = app.query_one("#prompt", Input)
                prompt.focus()
                await pilot.pause()

                prompt.value = "/resume"
                await pilot.press("enter")
                await pilot.pause()
                await pilot.pause()
                await pilot.pause()

                from explain_engine.chat.tui_app import SessionPickerScreen

                top = app.screen_stack[-1]
                assert isinstance(top, SessionPickerScreen)
                # Escape cancel — dismiss None
                top.dismiss(None)
                await pilot.pause()
                await pilot.pause()
                await pilot.pause()

                # 验 chat 没切, 仍是 original ephemeral
                assert app.chat is original_chat, (
                    "picker Escape (dismiss None) 不该切 chat, "
                    f"got app.chat={app.chat!r}"
                )

        await asyncio.wait_for(_scenario(), timeout=10.0)
