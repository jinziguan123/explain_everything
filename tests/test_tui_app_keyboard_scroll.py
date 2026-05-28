"""Phase 20.0 Task 3 Layer C: PgUp/PgDn keyboard scroll for VerticalScroll#output.

mouse=False (Phase 19 Bug A 副作用) 下 wheel 失效, keyboard 兜底.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.chat.tui_app import ExplainChatApp


def _make_app() -> ExplainChatApp:
    mock_chat = MagicMock(spec=EphemeralChatSession)
    mock_chat.is_slash_command = lambda t: t.startswith("/")
    return ExplainChatApp(
        llm=MagicMock(),
        light_llm=None,
        ephemeral_chat=mock_chat,
        show_splash=False,
    )


@pytest.mark.asyncio
async def test_pageup_scrolls_output_up():
    """PgUp 按下 → VerticalScroll#output.scroll_page_up 被调."""
    app = _make_app()
    async with app.run_test() as pilot:
        from textual.containers import VerticalScroll
        from textual.widgets import Static

        container = app.query_one("#output", VerticalScroll)
        for i in range(30):
            await container.mount(Static(f"line {i}"))
        await pilot.pause(0.05)
        container.scroll_end(animate=False)
        await pilot.pause(0.05)
        y_before = container.scroll_offset.y

        await pilot.press("pageup")
        await pilot.pause(0.1)

        y_after = container.scroll_offset.y
        assert y_after < y_before, (
            f"Expected scroll_offset.y to decrease, before={y_before}, after={y_after}"
        )


@pytest.mark.asyncio
async def test_pagedown_scrolls_output_down():
    """PgDn 按下 → VerticalScroll#output.scroll_page_down 被调."""
    app = _make_app()
    async with app.run_test() as pilot:
        from textual.containers import VerticalScroll
        from textual.widgets import Static

        container = app.query_one("#output", VerticalScroll)
        for i in range(30):
            await container.mount(Static(f"line {i}"))
        await pilot.pause(0.05)
        container.scroll_home(animate=False)
        await pilot.pause(0.05)
        y_before = container.scroll_offset.y

        await pilot.press("pagedown")
        await pilot.pause(0.1)

        y_after = container.scroll_offset.y
        assert y_after > y_before, (
            f"Expected scroll_offset.y to increase, before={y_before}, after={y_after}"
        )
