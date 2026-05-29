"""Phase 20.2 UX: 重新开启 textual mouse tracking — 让鼠标滚轮翻历史.

背景 (决策反转):
Phase 19 真终端 Bug A 当初设 `mouse=False` 关 SGR mouse tracking, 为的是让真
终端能 click+drag 原生选文字复制 (textual mouse=True 会把 click/drag 当 input
抢走). 代价: 鼠标滚轮失效 → 用户只能用 PgUp/PgDn 翻历史.

Phase 20.2: 用户实际用下来要鼠标滚轮翻历史 (auto-scroll 智能锚定已修好"输出
到一半"主症状, 但回看历史仍想用滚轮). 终端 mouse tracking 是 all-or-nothing —
滚轮事件 (button 64/65) 必须开 click tracking 才收得到, 没有"只收滚轮"模式.
故重开 `mouse=True`. 代价: click-drag 原生选字被 app 抢走, 但多数终端
(iTerm2 / macOS Terminal.app) 按住 Shift / Option 拖拽仍可原生选字复制.

regression test 策略 (跟 test_repl_entry_textual.py 同款 monkeypatch):
mock `ExplainChatApp.run_async`, capture kwargs, 验 `mouse=True` 一定传 (显式
传而非靠 textual 默认, 让意图可测 + 防未来 textual 改默认).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_enter_repl_async_passes_mouse_true(tmp_path, monkeypatch) -> None:
    """enter_repl_async 调 app.run_async(mouse=True) — 开 mouse tracking 让滚轮翻历史.

    Phase 20.2 反转 Phase 19 的 mouse=False: 用户要鼠标滚轮上下滚动历史. 终端
    mouse tracking all-or-nothing, 开滚轮就得开 click tracking → mouse=True.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_mouse_enabled")

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

    assert captured.get("mouse") is True, (
        f"期望 app.run_async(mouse=True) 开启 textual mouse tracking, "
        f"让真终端鼠标滚轮能上下翻历史. 实际 captured kwargs={captured!r}."
    )


@pytest.mark.asyncio
async def test_enter_repl_async_no_splash_mouse_true(tmp_path, monkeypatch) -> None:
    """show_splash=False 路径 (cli --no-splash) 也应 mouse=True.

    防御性: 两条路径 (splash / no-splash) 都开 mouse, 不能有 mouse=False 残留.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_mouse_enabled_no_splash")

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

    assert captured.get("mouse") is True, (
        f"show_splash=False 路径也应 mouse=True, captured={captured!r}"
    )
