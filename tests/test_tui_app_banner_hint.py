"""Phase 20.2: banner 含复制用法提示.

mouse=True (滚轮翻历史) 后, 终端原生 click-drag 选字被 app 抢走 → 复制需按住
Option 拖选 + Cmd+C. banner 补一行提示让用户可发现这个用法 (用户选了
"Option+拖拽就够了" 而非加切换键).
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
        llm=MagicMock(), light_llm=None, ephemeral_chat=mock_chat, show_splash=False
    )


@pytest.mark.asyncio
async def test_banner_includes_copy_hint():
    """banner 提示复制要 Option+拖拽 (mouse=True 后原生选字被 app 抢, 修饰键绕过)."""
    app = _make_app()
    async with app.run_test() as pilot:
        from textual.containers import VerticalScroll
        from textual.widgets import Static

        await pilot.pause()
        container = app.query_one("#output", VerticalScroll)
        joined = "\n".join(str(s.render()) for s in container.query(Static))
        assert "Option" in joined, (
            f"期望 banner 含复制提示 (Option+拖拽), 实际 #output={joined!r}"
        )
