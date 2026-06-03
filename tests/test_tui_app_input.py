"""Phase 19 Wave 3 Task 16: Input.Submitted handler.

handle_input(event):
- text 以 / 开头 → _spawn_chat_task(_consume_slash_events) → 后台 dispatch_slash + _render
- 否则 → _spawn_chat_task(_consume_chat_events) → async for + _render

Phase 20.2 P0 (/compress 卡死修): slash 与自然语言都跑在可 cancel 的后台 task
(self._chat_task) 上 — handler 立刻返让 message pump free, escape 才能 cancel
长 slash (如 /compress). 测试需 `await app._chat_task` 等后台渲染完再断言.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_input_submitted_slash_dispatch(tmp_path, monkeypatch) -> None:
    """输 /help → dispatch_slash 调用 + 渲染 events."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_tui_input")

    from textual.containers import VerticalScroll
    from textual.widgets import Input, Static

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        prompt.focus()
        await pilot.pause()
        prompt.value = "/help"
        await pilot.press("enter")
        # Phase 20.2: slash 现走后台 task — 等它完成再断言渲染.
        if app._chat_task is not None:
            await app._chat_task
        await pilot.pause()

        # /help yield 一个 slash_help event (str content) → mount Static
        container = app.query_one("#output", VerticalScroll)
        statics = list(container.query(Static))
        assert len(statics) >= 1
        # Input value 已清空
        assert prompt.value == ""


@pytest.mark.asyncio
async def test_input_submitted_natural_language(tmp_path, monkeypatch) -> None:
    """输自然语言 → 调用 chat.handle_user_input (async generator) + 渲染 events."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_tui_input_nl")

    from textual.containers import VerticalScroll
    from textual.widgets import Input, Static

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    storage = StorageV2()
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=MagicMock(text="answer hi", reasoning=None))
    ephemeral = EphemeralChatSession(storage=storage, llm=llm)
    app = ExplainChatApp(
        llm=llm,
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        prompt.focus()
        await pilot.pause()
        prompt.value = "你好"
        await pilot.press("enter")
        await pilot.pause()
        # 多 pause 几次确保 async for 走完
        await pilot.pause()
        await pilot.pause()

        # LLM chat 调用过
        llm.chat.assert_called()
        # answer 已 mount Static
        container = app.query_one("#output", VerticalScroll)
        statics = list(container.query(Static))
        assert len(statics) >= 1
        assert prompt.value == ""


@pytest.mark.asyncio
async def test_input_submitted_empty_string_no_op(tmp_path, monkeypatch) -> None:
    """空 input (空格 / 仅 enter) → 不调 dispatch_slash 也不调 handle_user_input."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_tui_input_empty")

    from textual.widgets import Input

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    storage = StorageV2()
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=MagicMock(text="x", reasoning=None))
    ephemeral = EphemeralChatSession(storage=storage, llm=llm)
    app = ExplainChatApp(
        llm=llm,
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        prompt.focus()
        await pilot.pause()
        prompt.value = "   "
        await pilot.press("enter")
        await pilot.pause()
        # llm.chat 不该被调用
        llm.chat.assert_not_called()


@pytest.mark.asyncio
async def test_input_submitted_slash_quit_exits(tmp_path, monkeypatch) -> None:
    """输 /quit → dispatch_slash 返 slash_quit event → render 调用 self.exit()."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_tui_input_quit")

    from textual.widgets import Input

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        prompt.focus()
        await pilot.pause()
        prompt.value = "/quit"
        await pilot.press("enter")
        # Phase 20.2: slash 现走后台 task — 等 _render_event 调 self.exit().
        if app._chat_task is not None:
            await app._chat_task
        await pilot.pause()
        assert app._exit is True
