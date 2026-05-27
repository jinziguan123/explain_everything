"""Phase 19 Wave 6 Task 31: ExplainChatApp on_mount push SplashScreen.

ExplainChatApp.__init__ 加 show_splash: bool = True 参数.
on_mount 内:
- show_splash=True → push SplashScreen + await splash._init_task + asyncio.sleep(1) +
  pop_screen, 露出主 chat layer.
- show_splash=False → 跳过, 直接是主 chat layer.

测试:
1. 构造默 show_splash=True.
2. 显式 show_splash=False 跳过 splash.
3. show_splash=True 启 app, 首屏 SplashScreen, 等 init + sleep + pop 后 主 screen.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_explain_chat_app_default_show_splash_true(
    tmp_path, monkeypatch
) -> None:
    """ExplainChatApp() 默 show_splash=True."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(), light_llm=AsyncMock(), ephemeral_chat=ephemeral,
    )
    assert app.show_splash is True


@pytest.mark.asyncio
async def test_explain_chat_app_show_splash_false_explicit(
    tmp_path, monkeypatch
) -> None:
    """ExplainChatApp(show_splash=False) — 不 push splash."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.splash_screen import SplashScreen
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(), light_llm=AsyncMock(), ephemeral_chat=ephemeral,
        show_splash=False,
    )
    assert app.show_splash is False
    async with app.run_test() as pilot:
        await pilot.pause()
        # 主屏不应 SplashScreen — 是 default screen 含 #prompt
        assert not isinstance(app.screen, SplashScreen)
        # #prompt 立刻可用
        from textual.widgets import Input
        assert app.query_one("#prompt", Input) is not None


@pytest.mark.asyncio
async def test_explain_chat_app_show_splash_true_pushes_and_pops(
    tmp_path, monkeypatch
) -> None:
    """show_splash=True: 启 app 后 SplashScreen 出现, await _init + 1s pause 后 pop."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.splash_screen import SplashScreen
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(), light_llm=AsyncMock(), ephemeral_chat=ephemeral,
        show_splash=True,
    )

    # patch 4 step fn 让 _run_init_steps 跑空, 跑得快.
    # 也 patch asyncio.sleep 避免真等 1s.
    with patch.object(
        SplashScreen, "_init_lexicon", new=AsyncMock(return_value=None)
    ), patch.object(
        SplashScreen, "_ping_pg", new=AsyncMock(return_value=None)
    ), patch.object(
        SplashScreen, "_load_theory_cache", new=AsyncMock(return_value=None)
    ), patch.object(
        SplashScreen, "_ready_signal", new=AsyncMock(return_value=None)
    ), patch(
        "explain_engine.chat.tui_app.asyncio.sleep",
        new=AsyncMock(return_value=None),
    ):
        async with app.run_test() as pilot:
            # on_mount 启 splash worker — 等它跑完
            await pilot.pause()
            # 等所有 pending task 完成 (splash 4 step + pop)
            for _ in range(10):
                await pilot.pause()
            # 最终 splash 已 pop, 当前 screen 不是 SplashScreen
            assert not isinstance(app.screen, SplashScreen)
            from textual.widgets import Input
            assert app.query_one("#prompt", Input) is not None
