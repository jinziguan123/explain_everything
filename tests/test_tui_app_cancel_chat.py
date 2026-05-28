"""Phase 20.0 Task 2 Layer B: esc cancel in-flight chat task.

textual App.run_test() harness, mock chat.handle_user_input async gen 永等
模拟 LLM stream stall, press_keys('escape') 验 task cancelled + spinner 清.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.chat.session import ChatEvent
from explain_engine.chat.tui_app import ExplainChatApp


def _make_app_with_mock_chat(handle_user_input_gen) -> ExplainChatApp:
    """Helper: 造 ExplainChatApp 用 mock chat (避免真 storage/llm)."""
    mock_chat = MagicMock(spec=EphemeralChatSession)
    mock_chat.handle_user_input = handle_user_input_gen
    mock_chat.is_slash_command = lambda t: t.startswith("/")
    mock_chat.tui_app = None  # 让 __init__ set self
    return ExplainChatApp(
        llm=MagicMock(),
        light_llm=None,
        ephemeral_chat=mock_chat,
        show_splash=False,  # 跳过 splash 加速 test
    )


@pytest.mark.asyncio
async def test_escape_cancels_inflight_chat_task():
    """提交 input → mock chat handler 永等 → press escape → task cancelled."""
    async def never_ending_gen(text, llm):
        yield ChatEvent(type="status_start", content="思考中...")
        await asyncio.sleep(99)
        yield ChatEvent(type="assistant_text", content="never")

    app = _make_app_with_mock_chat(never_ending_gen)

    async with app.run_test() as pilot:
        await pilot.press(*"hello")
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert app._chat_task is not None
        assert not app._chat_task.done()

        await pilot.press("escape")
        await pilot.pause(0.1)

        assert app._chat_task is None or app._chat_task.done()


@pytest.mark.asyncio
async def test_escape_no_inflight_task_shows_message():
    """无 in-flight task → escape 仅 mount '(无 in-flight 请求可取消)' 行."""
    async def normal_gen(text, llm):
        yield ChatEvent(type="assistant_text", content="ok")

    app = _make_app_with_mock_chat(normal_gen)

    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause(0.05)

        assert app._chat_task is None
        from textual.widgets import Static
        statics = list(app.query(Static))
        # Static.render() 返 Content / str — 用 plain 文本对比 (str() 拿到
        # Content.plain). 不是所有 Static 子类 (HeaderTitle) 都能 render 同样
        # interface — getattr fallback "" 防 AttributeError.
        rendered = []
        for s in statics:
            try:
                rendered.append(str(s.render()))
            except Exception:
                rendered.append("")
        assert any("无 in-flight" in r for r in rendered), (
            f"Expected '无 in-flight' message, got: {rendered}"
        )


@pytest.mark.asyncio
async def test_chat_task_normal_completion_clears_ref():
    """chat handler 正常完成 → _chat_task 回 None."""
    async def quick_gen(text, llm):
        yield ChatEvent(type="status_start", content="...")
        yield ChatEvent(type="assistant_text", content="hello")
        yield ChatEvent(type="status_end", content=None)

    app = _make_app_with_mock_chat(quick_gen)

    async with app.run_test() as pilot:
        await pilot.press(*"hi")
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert app._chat_task is None
