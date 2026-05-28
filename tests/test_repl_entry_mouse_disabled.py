"""Phase 19 真终端 Bug A: textual mouse tracking 关闭路径.

真因:
textual `App.run_async()` 默 `mouse=True` → Linux/macOS driver
`_enable_mouse_support` 写 `\x1b[?1000h \x1b[?1003h \x1b[?1006h` SGR mouse
escape 让 terminal 把 mouse click/drag 当 input event 报给 app, 不走
terminal 自己的 text selection. 结果: user 在真终端 (Terminal.app /
iTerm2 等) **无法 click+drag 选文字复制** — mouse 事件被 textual 抢走.

Phase 19 BINDINGS 全用 Ctrl 键盘组合 (Ctrl+O / Ctrl+L / Ctrl+C),
SessionPickerScreen 用 ↑↓ + Enter, Input 用 Tab/Shift-Tab 切 focus +
Enter 提交, 完全不依赖 mouse click. 完全可以 disable mouse 换回 terminal
text selection.

修法 (textual 公开 API):
`App.run_async(mouse=False)` (跟 `App.run(mouse=False)`). 该 flag 透传到
`Driver(mouse=False)`, `_enable_mouse_support` 早返 (不写 SGR escape).
textual 跨平台都支持, 跟 headless / inline mode 正交.

regression test 策略 (跟 test_repl_entry_textual.py 同款 monkeypatch):
mock `ExplainChatApp.run_async`, capture kwargs, 验 `mouse=False` 一定传.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_enter_repl_async_passes_mouse_false(tmp_path, monkeypatch) -> None:
    """enter_repl_async 调 app.run_async(mouse=False) — 关 SGR mouse tracking.

    真终端 user 用 click + drag 选文字 (macOS Cmd+C 复制). 老版默 mouse=True
    textual 抢 mouse → user 无法选文字. 修后 mouse=False, 鼠标事件不被 textual
    捕获, terminal 自然能用 selection.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_mouse_disabled")

    # mock LLM clients 避真 LLM env 要求
    fake_llm = AsyncMock(name="main_llm")
    fake_light = AsyncMock(name="light_llm")
    monkeypatch.setattr(
        "explain_engine.config.make_llm_client", lambda: fake_llm
    )
    monkeypatch.setattr(
        "explain_engine.config.make_light_llm_client", lambda: fake_light
    )

    async def fake_init():
        return False

    monkeypatch.setattr(
        "explain_engine.engines.lexicon.init_lexicon_backend", fake_init
    )

    # capture run_async kwargs
    captured: dict = {}

    async def fake_run_async(self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        "explain_engine.chat.tui_app.ExplainChatApp.run_async", fake_run_async
    )

    from explain_engine.chat.repl_entry import enter_repl_async

    await enter_repl_async()

    assert captured.get("mouse") is False, (
        f"期望 app.run_async(mouse=False) 关闭 textual SGR mouse tracking, "
        f"让真终端能 click+drag 选文字. 实际 captured kwargs={captured!r}."
    )


@pytest.mark.asyncio
async def test_enter_repl_async_no_splash_mouse_false(tmp_path, monkeypatch) -> None:
    """show_splash=False 路径 (cli --no-splash) 也应 mouse=False.

    防御性: 两条路径 (splash / no-splash) 都不能有 mouse=True 残留.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_mouse_disabled_no_splash")

    fake_llm = AsyncMock(name="main_llm")
    fake_light = AsyncMock(name="light_llm")
    monkeypatch.setattr(
        "explain_engine.config.make_llm_client", lambda: fake_llm
    )
    monkeypatch.setattr(
        "explain_engine.config.make_light_llm_client", lambda: fake_light
    )

    async def fake_init():
        return False

    monkeypatch.setattr(
        "explain_engine.engines.lexicon.init_lexicon_backend", fake_init
    )

    captured: dict = {}

    async def fake_run_async(self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        "explain_engine.chat.tui_app.ExplainChatApp.run_async", fake_run_async
    )

    from explain_engine.chat.repl_entry import enter_repl_async

    await enter_repl_async(show_splash=False)

    assert captured.get("mouse") is False, (
        f"show_splash=False 路径也应 mouse=False, captured={captured!r}"
    )
