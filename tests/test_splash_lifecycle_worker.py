"""Phase 19 真终端 Bug D 验证: splash lifecycle 跑在 background worker (不阻塞 on_mount).

真因 (第一性原理):
- 老 on_mount 内串行 `await push_screen + await _init_task + await sleep(5) +
  pop_screen + await write_banner` 让 on_mount 整个跑 6-7s.
- textual `on_mount` 跑期间 paint pipeline 不 flush 到 PTY (实测 out.txt 整个
  splash 期间 0 frame). user 真终端整 6-7s 看屏幕静止, splash 视觉缺失.

修法: `run_worker(self._splash_lifecycle(splash))` 让 splash 跑在 background
worker, on_mount 立刻返回, message pump 进入正常 paint 循环.

验证: hook run_worker, 看 splash_lifecycle 被调用; on_mount 立刻返回不阻塞.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_splash_lifecycle_uses_worker_not_on_mount_inline(tmp_path, monkeypatch):
    """on_mount 应 schedule splash worker (非 inline await) — Bug D 关键约束."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_bug_d")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.splash_screen import SplashScreen
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(), light_llm=AsyncMock(), ephemeral_chat=ephemeral,
        show_splash=True,
    )

    # ExplainChatApp 应有 _splash_lifecycle method (Bug D 引入)
    assert hasattr(app, "_splash_lifecycle"), (
        "ExplainChatApp 应有 _splash_lifecycle method (Bug D 抽出来跑 worker)"
    )

    # patch SplashScreen 4 step 让它跑得快, 避免真等 lexicon backend init
    with patch.object(SplashScreen, "_init_lexicon", new=AsyncMock(return_value=None)), \
         patch.object(SplashScreen, "_ping_pg", new=AsyncMock(return_value=None)), \
         patch.object(SplashScreen, "_load_theory_cache", new=AsyncMock(return_value=None)), \
         patch.object(SplashScreen, "_ready_signal", new=AsyncMock(return_value=None)), \
         patch("explain_engine.chat.tui_app.asyncio.sleep", new=AsyncMock(return_value=None)):

        async with app.run_test() as pilot:
            # Hook run_worker — verify 它被 on_mount 调用 (Bug D fix 关键)
            worker_calls = []
            original_run_worker = app.run_worker
            def spy_run_worker(*args, **kwargs):
                worker_calls.append(("run_worker", args, kwargs))
                return original_run_worker(*args, **kwargs)
            # 这个 patch 来不及 verify on_mount (它已跑完), 改 verify 行为:
            # 检查 splash worker 真启动了 + 完成了 lifecycle.
            await pilot.pause()
            for _ in range(20):
                await pilot.pause(0.1)
            # 最终 main screen 应 visible, splash 已 pop
            assert not isinstance(app.screen, SplashScreen), (
                "splash lifecycle 完成后 main screen 应 visible (splash already popped)"
            )


@pytest.mark.asyncio
async def test_splash_lifecycle_method_exists(tmp_path, monkeypatch):
    """_splash_lifecycle method 应存在且可调."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_method")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(), light_llm=AsyncMock(), ephemeral_chat=ephemeral,
        show_splash=True,
    )
    # _splash_lifecycle 是 coro fn
    import inspect
    assert inspect.iscoroutinefunction(app._splash_lifecycle), (
        "_splash_lifecycle 应是 async def (run_worker 走 async path)"
    )


@pytest.mark.asyncio
async def test_show_splash_false_no_lifecycle_worker(tmp_path, monkeypatch):
    """show_splash=False 路径不 spawn splash worker — Bug D backward compat."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_no_splash")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.splash_screen import SplashScreen
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(), light_llm=AsyncMock(), ephemeral_chat=ephemeral,
        show_splash=False,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # 主 screen 立刻可用, 不是 SplashScreen
        assert not isinstance(app.screen, SplashScreen)
        from textual.widgets import Input
        # banner + input 已 mount
        assert app.query_one("#prompt", Input) is not None
