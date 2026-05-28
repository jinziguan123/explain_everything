"""Phase 19 Wave 7 follow-up Bug 3: splash 真终端调试日志 + 持续时间延长.

user 报告: SVG snapshot smoke 显示 splash 正常, 但真终端 (macOS Terminal.app)
看不见. 矛盾说明:
1. splash 真在跑, 只是太快 (~3.7s 总: 4×0.3s + 2.5s) user 还没看清
2. textual alt-screen pop 把整屏覆盖, splash 历史不可见
3. 真终端渲染 vs SVG snapshot 有差异 (字号/行高/字体)

修法 (本 commit, Bug 3):
1. ~/.explain/phase19_debug.log 写 splash lifecycle (app start / splash push /
   step 完成 / sleep done / pop / banner / focus) 带时间戳. 让 user 跑后给我
   看 log 确诊真 path.
2. sleep 2.5 → 5s 让 user 真看见 (4 ✓ 点亮后留 5s 视觉停顿).
3. splash pop 之后写一行 "✓ 已加载, 启动 chat" 显示在主 layer — user 即使
   splash 没看到也知道它跑过 (logo 不可见 ≠ 没跑).

测试 (TDD):
- 写 debug log: 启 app + 跑 on_mount → log 文件存在 + 含 5 个关键事件
- pop banner 加 "✓ 已加载" 行
- sleep 时间 5s (不真等, monkeypatch sleep)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

# ─── 1. Debug log 路径 + 写入 ───


class TestSplashDebugLogPath:
    """debug log 写到 ~/.explain/phase19_debug.log (EXPLAIN_HOME-aware)."""

    def test_phase19_debug_log_constant_exists(self) -> None:
        """tui_app.PHASE19_DEBUG_LOG 是 callable / property 返路径."""
        from explain_engine.chat import tui_app

        # 函数: 取动态 EXPLAIN_HOME 解析
        assert hasattr(tui_app, "_phase19_debug_log_path"), (
            "tui_app 应导出 _phase19_debug_log_path() helper, 返 Path"
        )

    def test_phase19_debug_log_path_uses_explain_home(
        self, tmp_path, monkeypatch
    ) -> None:
        """log path = $EXPLAIN_HOME/phase19_debug.log."""
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        from explain_engine.chat.tui_app import _phase19_debug_log_path

        p = _phase19_debug_log_path()
        assert p.parent == tmp_path
        assert p.name == "phase19_debug.log"


# ─── 2. on_mount 写 lifecycle 事件 ───


class TestSplashLifecycleLogged:
    """on_mount push splash → 关键事件写入 debug log."""

    @pytest.mark.asyncio
    async def test_on_mount_writes_lifecycle_events_to_log(
        self, tmp_path, monkeypatch
    ) -> None:
        """splash 完整 lifecycle: app start / splash push / init done /
        sleep done / splash pop / banner mount / focus 全 log.

        Wave 7 follow-up Bug 3 P-0 hotfix: splash 流程现在跑在 worker 内
        (避免 batch_update 阻 paint). test patch:
        - 4 step fn → AsyncMock(return None) 避免真 PG 10s connect timeout
        - asyncio.sleep → 0 让 worker 不真 sleep 5s
        多 pilot.pause() 让 worker fully drain.
        """
        from textual.widgets import Input
        from unittest.mock import patch

        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.chat.splash_screen import SplashScreen
        from explain_engine.chat.tui_app import (
            ExplainChatApp,
            _phase19_debug_log_path,
        )
        from explain_engine.persistence.storage_v2 import StorageV2

        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_splash_debug_log")

        # monkey patch asyncio.sleep 缩短 5s → 0s
        import asyncio as _asyncio
        original_sleep = _asyncio.sleep

        async def fast_sleep(sec):
            # 不真等 — 让 test 不卡 5s
            return await original_sleep(0)

        monkeypatch.setattr("asyncio.sleep", fast_sleep)

        # P-0 hotfix: 现 splash 流程在 worker 内. patch 4 step fn 避免真跑
        # init_lexicon (10s PG timeout) — 之前 module-level _PG_BACKEND_ACTIVE
        # cache 让旧 test 隐性 fast-path, fix 后 worker 内重新 await init,
        # 测试需显式 patch.
        with patch.object(
            SplashScreen, "_init_lexicon", new=AsyncMock(return_value=None)
        ), patch.object(
            SplashScreen, "_ping_pg", new=AsyncMock(return_value=None)
        ), patch.object(
            SplashScreen, "_load_theory_cache", new=AsyncMock(return_value=None)
        ), patch.object(
            SplashScreen, "_ready_signal", new=AsyncMock(return_value=None)
        ):
            ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
            app = ExplainChatApp(
                llm=AsyncMock(),
                light_llm=AsyncMock(),
                ephemeral_chat=ephemeral,
                show_splash=True,
            )

            async with app.run_test() as pilot:
                # P-0 fix: worker 内跑 splash 流程 — 多 pause 让 worker drain
                # (init 4 step + 0 sleep + pop + banner + focus). 比旧 inline
                # await 路径需更多 yield 让 textual message pump 跑 worker.
                for _ in range(15):
                    await pilot.pause()
                # 最终验 input focused (lifecycle 末尾)
                prompt = app.query_one("#prompt", Input)
                assert prompt.has_focus, (
                    "splash lifecycle 末尾应 focus Input — 当前 focused: "
                    f"{app.focused!r}"
                )

        # 验 log 文件存在
        log_path = _phase19_debug_log_path()
        assert log_path.exists(), (
            f"on_mount 后应有 {log_path}, 但不存在. 可能 _log_phase19 没写入."
        )

        log_content = log_path.read_text(encoding="utf-8")
        # 关键 lifecycle 事件应都出现
        expected_keywords = [
            "splash_push",          # push_screen(splash)
            "init_task_done",       # _init_task await 完
            "splash_pop",           # pop_screen()
            "banner_mounted",       # _write_banner 完
            "input_focused",        # Input.focus 完
        ]
        for kw in expected_keywords:
            assert kw in log_content, (
                f"lifecycle event '{kw}' 未写入 debug log. log:\n{log_content!r}"
            )


# ─── 3. sleep 时间延长到 5s ───


class TestSplashHoldDurationFiveSeconds:
    """splash on_mount 内 asyncio.sleep 用 5.0 不是 2.5."""

    @pytest.mark.asyncio
    async def test_splash_sleep_is_5_seconds(
        self, tmp_path, monkeypatch
    ) -> None:
        """worker 内 await asyncio.sleep(5.0) 让 user 真看见 splash.

        Wave 7 follow-up Bug 3 P-0 hotfix: splash 流程现跑 worker 内, sleep(5.0)
        从 on_mount 搬到 _run_splash_sequence (worker 协程). 检 sleep 调用
        仍含 5.0 即可 (不依赖调用位置).
        """
        from unittest.mock import patch

        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.chat.splash_screen import SplashScreen
        from explain_engine.chat.tui_app import ExplainChatApp
        from explain_engine.persistence.storage_v2 import StorageV2

        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_splash_5s")

        # 收集所有 sleep 调用
        captured_sleeps: list[float] = []
        import asyncio as _asyncio
        original_sleep = _asyncio.sleep

        async def capture_sleep(sec):
            captured_sleeps.append(sec)
            return await original_sleep(0)  # 不真等

        monkeypatch.setattr("asyncio.sleep", capture_sleep)

        # P-0 hotfix: patch 4 step fn 避免真 PG 10s connect timeout
        with patch.object(
            SplashScreen, "_init_lexicon", new=AsyncMock(return_value=None)
        ), patch.object(
            SplashScreen, "_ping_pg", new=AsyncMock(return_value=None)
        ), patch.object(
            SplashScreen, "_load_theory_cache", new=AsyncMock(return_value=None)
        ), patch.object(
            SplashScreen, "_ready_signal", new=AsyncMock(return_value=None)
        ):
            ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
            app = ExplainChatApp(
                llm=AsyncMock(),
                light_llm=AsyncMock(),
                ephemeral_chat=ephemeral,
                show_splash=True,
            )

            async with app.run_test() as pilot:
                # 多 pause 让 worker drain (含 sleep(5.0) 调用)
                for _ in range(15):
                    await pilot.pause()

        # 验: 有一个 sleep(5.0) 调用 (worker 内 hold)
        # 注: splash _run_init_steps 内也有 sleep(0.3) × 4. 我们只关心 hold 那次.
        assert 5.0 in captured_sleeps, (
            f"worker 应 await asyncio.sleep(5.0) 让 user 真看见 splash, "
            f"got captured: {captured_sleeps}"
        )


# ─── 4. splash pop 后 banner 含 "已加载" / ready 标志 ───


class TestSplashReadyBannerAfterPop:
    """splash pop 后写一行 "✓ 已加载, 启动 chat" 主 layer banner — user 即使
    splash 没看到也可见 = splash 真跑过."""

    @pytest.mark.asyncio
    async def test_after_splash_pop_writes_ready_banner(
        self, tmp_path, monkeypatch
    ) -> None:
        """splash pop → banner with "已加载" or "ready" 字样.

        Wave 7 follow-up Bug 3 P-0 hotfix: worker 内 pop + 写 ready banner.
        """
        from unittest.mock import patch

        from textual.containers import VerticalScroll
        from textual.widgets import Static

        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.chat.splash_screen import SplashScreen
        from explain_engine.chat.tui_app import ExplainChatApp
        from explain_engine.persistence.storage_v2 import StorageV2

        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_splash_ready_banner")

        # 加速 sleep
        import asyncio as _asyncio
        original_sleep = _asyncio.sleep
        async def fast_sleep(sec):
            return await original_sleep(0)
        monkeypatch.setattr("asyncio.sleep", fast_sleep)

        # P-0 hotfix: patch 4 step fn 避免真 PG 10s connect timeout
        with patch.object(
            SplashScreen, "_init_lexicon", new=AsyncMock(return_value=None)
        ), patch.object(
            SplashScreen, "_ping_pg", new=AsyncMock(return_value=None)
        ), patch.object(
            SplashScreen, "_load_theory_cache", new=AsyncMock(return_value=None)
        ), patch.object(
            SplashScreen, "_ready_signal", new=AsyncMock(return_value=None)
        ):
            ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
            app = ExplainChatApp(
                llm=AsyncMock(),
                light_llm=AsyncMock(),
                ephemeral_chat=ephemeral,
                show_splash=True,
            )

            async with app.run_test() as pilot:
                # 多 pause 让 worker drain
                for _ in range(15):
                    await pilot.pause()

                container = app.query_one("#output", VerticalScroll)
                statics = list(container.query(Static))
                # textual Static.render() 返 Content / RenderableType; str() 拿 plain
                all_text = "\n".join(str(s.render()) for s in statics)
                # 应含 "已加载" / "ready" 字样让 user 知道 splash 真跑过
                assert "已加载" in all_text or "ready" in all_text.lower(), (
                    "splash pop 后应写一行 '✓ 已加载' / ready banner, "
                    f"got mount text:\n{all_text!r}"
                )
