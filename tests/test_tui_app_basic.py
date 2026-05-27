"""Phase 19 Wave 3 Task 13: ExplainChatApp scaffolding.

验 textual App 基本壳: compose Header + RichLog#output + Input#prompt + Footer,
BINDINGS Ctrl+O/Ctrl+C/Ctrl+L, CSS_PATH 指 tui_app.tcss.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_explain_chat_app_constructs(tmp_path, monkeypatch) -> None:
    """ExplainChatApp 接 llm + light_llm + ephemeral_chat 构造."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    assert app.chat is ephemeral
    assert app._thinking_visible is True


@pytest.mark.asyncio
async def test_explain_chat_app_composes_widgets(tmp_path, monkeypatch) -> None:
    """run_test pilot 启 app, 验 Input#prompt + RichLog#output mount."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import Input, RichLog

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
        output = app.query_one("#output", RichLog)
        assert prompt is not None
        assert output is not None


@pytest.mark.asyncio
async def test_explain_chat_app_bindings(tmp_path, monkeypatch) -> None:
    """BINDINGS 含 Ctrl+O / Ctrl+C / Ctrl+L."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.tui_app import ExplainChatApp

    keys = {b.key for b in ExplainChatApp.BINDINGS}
    assert "ctrl+o" in keys
    assert "ctrl+c" in keys
    assert "ctrl+l" in keys


@pytest.mark.asyncio
async def test_explain_chat_app_css_path(tmp_path, monkeypatch) -> None:
    """CSS_PATH 指 tui_app.tcss 同目录."""
    from explain_engine.chat.tui_app import ExplainChatApp

    assert ExplainChatApp.CSS_PATH == "tui_app.tcss"


@pytest.mark.asyncio
async def test_action_clear_log(tmp_path, monkeypatch) -> None:
    """action_clear_log 调 RichLog.clear."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import RichLog

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
        log = app.query_one("#output", RichLog)
        log.write("hello")
        await pilot.pause()
        # action_clear_log 调用后 lines 应为 0
        app.action_clear_log()
        await pilot.pause()
        assert len(log.lines) == 0
