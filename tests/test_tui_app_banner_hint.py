"""Phase 20.2: banner 含复制用法提示.

mouse=True (滚轮翻历史) 后, textual 自带"选区感知"复制: 鼠标拖选高亮文字,
Ctrl+C (Screen.copy_text) 把选区复制到系统剪贴板 (OSC52); 无选区时 copy_text
raise SkipAction 放行, 落到 app 的 ctrl+c → 退出. banner 提示这个用法
(比 Option+拖拽更顺, 不需修饰键).
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
    """banner 提示复制用法: 鼠标拖选 + Ctrl+C (textual 自带选区复制)."""
    app = _make_app()
    async with app.run_test() as pilot:
        from textual.containers import VerticalScroll
        from textual.widgets import Static

        await pilot.pause()
        container = app.query_one("#output", VerticalScroll)
        joined = "\n".join(str(s.render()) for s in container.query(Static))
        assert "复制" in joined and "Ctrl+C" in joined, (
            f"期望 banner 含复制提示 (拖选 + Ctrl+C), 实际 #output={joined!r}"
        )
