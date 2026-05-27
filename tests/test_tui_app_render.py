"""Phase 19 Wave 3 Task 14-15: _render_event dispatch.

Task 14: assistant_text → log.write / slash_quit → exit / 其他 → dim fallback.
Task 15: slash_deepen_promoted (建 ChatSession) / slash_reset_to_ephemeral (重建
  ephemeral) handler.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_render_assistant_text_writes_to_log(tmp_path, monkeypatch) -> None:
    """assistant_text event → log.write content."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import RichLog

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent
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
        await app._render_event(ChatEvent(type="assistant_text", content="hello world"))
        await pilot.pause()
        log = app.query_one("#output", RichLog)
        # RichLog.lines 是 list[Strip]; 检查 1 行被 write 即可.
        assert len(log.lines) >= 1


@pytest.mark.asyncio
async def test_render_slash_quit_exits(tmp_path, monkeypatch) -> None:
    """slash_quit event → app.exit() 标记退出."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent
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
        await app._render_event(ChatEvent(type="slash_quit", content="bye"))
        await pilot.pause()
        # exit() 调用后 _exit 标记应 True 或 app.return_value 有变化.
        # textual: app._exit_renderables 不 stable; 用 _exit attr 检查
        assert app._exit is True


@pytest.mark.asyncio
async def test_render_unknown_event_dim_fallback(tmp_path, monkeypatch) -> None:
    """未知 event type → dim fallback 写到 log (不崩)."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import RichLog

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent
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
        # 一个 Wave 3 未支持的 type
        await app._render_event(ChatEvent(type="some_unknown_type", content="x"))
        await pilot.pause()
        log = app.query_one("#output", RichLog)
        # 至少有一行 (dim fallback)
        assert len(log.lines) >= 1


@pytest.mark.asyncio
async def test_render_slash_help_renders_text(tmp_path, monkeypatch) -> None:
    """slash_help / slash_show 等普通 text event → 写到 log."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import RichLog

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent
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
        await app._render_event(ChatEvent(type="slash_help", content="HELP TEXT"))
        await pilot.pause()
        log = app.query_one("#output", RichLog)
        assert len(log.lines) >= 1


@pytest.mark.asyncio
async def test_render_slash_error_renders_text(tmp_path, monkeypatch) -> None:
    """slash_error → 红色 (markup) 写到 log."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import RichLog

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent
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
        await app._render_event(ChatEvent(type="slash_error", content="some error"))
        await pilot.pause()
        log = app.query_one("#output", RichLog)
        assert len(log.lines) >= 1
