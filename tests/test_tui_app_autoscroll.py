"""Phase 20.2 P0 真因修: #output 智能锚定 (auto-scroll 跟随新输出到底).

根因 (前两次误诊 timeout / markup 都错): tui_app 从不 scroll, textual
VerticalScroll 默认也不跟随新内容 → 长 LLM 回答 mount 后视口钉在顶部
(scroll_y=0), 大半内容在 fold 下看不见, 叠加 mouse=False 滚轮死 →
用户感知"输出到一半卡死, 无法上下滚动, slash 还能输出但只能从滚动条看出".

修法: on_mount 调 #output.anchor() — textual 8.2.7 原生智能锚定:
新内容跟随到底; 用户上翻则脱锚不被拽下; 翻回底部重新跟随.
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


def _long(n: int, tag: str = "行"):
    """造一个会溢出 run_test 默认视口 (80x24) 的高 Static."""
    from textual.widgets import Static

    return Static("\n".join(f"{tag} {i}" for i in range(n)))


@pytest.mark.asyncio
async def test_long_output_follows_to_bottom():
    """P0 回归: 长 assistant_text mount 后视口跟随到底 (不再钉在 scroll_y=0).

    修前: scroll_y 恒 0, 内容在 fold 下 → "输出到一半卡死".
    修后: #output 锚定 → scroll_y == max_scroll_y.
    """
    app = _make_app()
    async with app.run_test() as pilot:
        from textual.containers import VerticalScroll

        c = app.query_one("#output", VerticalScroll)
        await pilot.pause()
        await c.mount(_long(60))
        await pilot.pause()
        assert c.max_scroll_y > 0, "前置: 内容应溢出视口"
        assert c.scroll_offset.y == c.max_scroll_y, (
            f"长输出后视口应跟随到底, 实际 scroll_y={c.scroll_offset.y} "
            f"max_scroll_y={c.max_scroll_y} (修前钉在 0 = '输出到一半卡死')"
        )


@pytest.mark.asyncio
async def test_output_is_anchored_after_mount():
    """on_mount 后 #output 处于 anchored 状态 (智能锚定开关)."""
    app = _make_app()
    async with app.run_test() as pilot:
        from textual.containers import VerticalScroll

        c = app.query_one("#output", VerticalScroll)
        await pilot.pause()
        assert c.is_anchored, "on_mount 应调 #output.anchor() 开启智能锚定"


@pytest.mark.asyncio
async def test_scrolled_up_user_not_yanked_by_new_output():
    """智能锚定 (非 naive scroll_end): 用户上翻后脱锚, 新输出不把他拽回底部.

    naive '每次 mount 都 scroll_end' 会让此 test 失败 (被拽到底).
    """
    app = _make_app()
    async with app.run_test() as pilot:
        from textual.containers import VerticalScroll

        c = app.query_one("#output", VerticalScroll)
        await pilot.pause()
        await c.mount(_long(60, "A"))
        await pilot.pause()
        # 用户上翻 → 脱锚
        await pilot.press("pageup")
        await pilot.press("pageup")
        await pilot.pause()
        y_up = c.scroll_offset.y
        assert y_up < c.max_scroll_y, "前置: 上翻后应离开底部"
        # 新输出来 → 不被拽下 (scroll_y 不动)
        await c.mount(_long(20, "B"))
        await pilot.pause()
        assert c.scroll_offset.y == y_up, (
            f"用户上翻后不应被新输出拽下, scroll_y 从 {y_up} 变成 {c.scroll_offset.y}"
        )


@pytest.mark.asyncio
async def test_return_to_bottom_reengages_follow():
    """脱锚后翻回底部 → 重新跟随新输出 (智能锚定的'回锚')."""
    app = _make_app()
    async with app.run_test() as pilot:
        from textual.containers import VerticalScroll

        c = app.query_one("#output", VerticalScroll)
        await pilot.pause()
        await c.mount(_long(60, "A"))
        await pilot.pause()
        await pilot.press("pageup")
        await pilot.pause()
        assert c.scroll_offset.y < c.max_scroll_y, "前置: 上翻后离开底部"
        # 翻回底部 → 重新锚定
        c.scroll_end(animate=False)
        await pilot.pause()
        await c.mount(_long(20, "C"))
        await pilot.pause()
        assert c.scroll_offset.y == c.max_scroll_y, (
            "回到底部后应重新跟随新输出, "
            f"scroll_y={c.scroll_offset.y} max={c.max_scroll_y}"
        )
