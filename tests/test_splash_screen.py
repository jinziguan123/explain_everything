"""Phase 19 Wave 6 Task 29+30: SplashScreen 单元测试.

测 SplashScreen:
- compose yield logo Static (pyfiglet ASCII) + version Static + 4 step Static.
- INIT_STEPS 4 项, 第 0 项 label 含 "lexicon".
- on_mount 串行 await 4 init step, 每步先标 "运行中" 再标 "完成".
- step fn 抛异常 → 标 "失败" 不阻塞下一 step.
- _ready_signal 为 async noop sleep.
- _init_lexicon 调 init_lexicon_backend (await).
- _ping_pg 调 verify_connection (await), 失败也吞.
- _load_theory_cache 调 get_active_theories (sync) wrapped in to_thread? — 直接 sync 调.

设计: docs/plans/2026-05-27-phase-19-tui-design.md §3.5 +
docs/plans/2026-05-27-phase-19-tui-plan.md Wave 6.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_splash_screen_class_exists() -> None:
    """SplashScreen 类导入成功 + 是 textual Screen."""
    from textual.screen import Screen

    from explain_engine.chat.splash_screen import SplashScreen

    assert issubclass(SplashScreen, Screen)


def test_splash_screen_init_steps_shape() -> None:
    """INIT_STEPS 4 项, label 含 lexicon/PG/theory/REPL 文案, 每项有 fn."""
    from explain_engine.chat.splash_screen import SplashScreen

    steps = SplashScreen.INIT_STEPS
    assert len(steps) == 4
    labels = " ".join(s["label"] for s in steps).lower()
    assert "lexicon" in labels
    # 第 0 项 label 含 lexicon
    assert "lexicon" in steps[0]["label"].lower()
    # 每 step 有 fn (method name str)
    for s in steps:
        assert "fn" in s
        assert isinstance(s["fn"], str)


@pytest.mark.asyncio
async def test_splash_screen_compose_yields_logo_and_steps(
    tmp_path, monkeypatch
) -> None:
    """run_test pilot 跑起来后 #logo / #step-0..3 widget mount.

    用 wrapper App 把 SplashScreen 作 default screen.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.app import App
    from textual.widgets import Static

    from explain_engine.chat.splash_screen import SplashScreen

    class _Wrapper(App):
        def on_mount(self):
            self.push_screen(SplashScreen())

    wrapper = _Wrapper()
    # patch step fn 让 on_mount 跑空 — 不调真实 init.
    with patch.object(
        SplashScreen, "_init_lexicon", new=AsyncMock(return_value=None)
    ), patch.object(
        SplashScreen, "_ping_pg", new=AsyncMock(return_value=None)
    ), patch.object(
        SplashScreen, "_load_theory_cache", new=AsyncMock(return_value=None)
    ), patch.object(
        SplashScreen, "_ready_signal", new=AsyncMock(return_value=None)
    ):
        async with wrapper.run_test() as pilot:
            await pilot.pause()
            # SplashScreen 已 push
            screen = wrapper.screen
            assert isinstance(screen, SplashScreen)
            # logo Static + 4 step Static mount
            logo = screen.query_one("#splash-logo", Static)
            assert logo is not None
            for i in range(4):
                step_widget = screen.query_one(f"#step-{i}", Static)
                assert step_widget is not None


@pytest.mark.asyncio
async def test_splash_screen_on_mount_calls_all_4_steps_in_order(
    tmp_path, monkeypatch
) -> None:
    """on_mount 串行 await 4 init step fn 按 INIT_STEPS 顺序."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.app import App

    from explain_engine.chat.splash_screen import SplashScreen

    class _Wrapper(App):
        def on_mount(self):
            self.push_screen(SplashScreen())

    wrapper = _Wrapper()
    call_order: list[str] = []

    # AsyncMock side_effect 跑 sync fn 后, AsyncMock wraps 返回 coroutine.
    # 用 sync fn append 到 list 即可 (AsyncMock 会自己包成 async).
    def _make_recorder(name: str):
        def _r() -> None:
            call_order.append(name)
        return _r

    with patch.object(
        SplashScreen, "_init_lexicon",
        new=AsyncMock(side_effect=_make_recorder("lexicon"))
    ), patch.object(
        SplashScreen, "_ping_pg",
        new=AsyncMock(side_effect=_make_recorder("pg"))
    ), patch.object(
        SplashScreen, "_load_theory_cache",
        new=AsyncMock(side_effect=_make_recorder("theory"))
    ), patch.object(
        SplashScreen, "_ready_signal",
        new=AsyncMock(side_effect=_make_recorder("ready"))
    ):
        async with wrapper.run_test() as pilot:
            await pilot.pause()
            # 等 on_mount 4 step 跑完
            screen = wrapper.screen
            await screen._init_task
    assert call_order == ["lexicon", "pg", "theory", "ready"]


@pytest.mark.asyncio
async def test_splash_screen_step_failure_does_not_block_next(
    tmp_path, monkeypatch
) -> None:
    """某 step fn 抛异常 → 标失败, 下一 step 仍跑."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.app import App
    from textual.widgets import Static

    from explain_engine.chat.splash_screen import SplashScreen

    class _Wrapper(App):
        def on_mount(self):
            self.push_screen(SplashScreen())

    wrapper = _Wrapper()
    later_called: list[str] = []

    def _rec(name: str):
        def _r() -> None:
            later_called.append(name)
        return _r

    with patch.object(
        SplashScreen, "_init_lexicon",
        new=AsyncMock(side_effect=RuntimeError("boom"))
    ), patch.object(
        SplashScreen, "_ping_pg",
        new=AsyncMock(side_effect=_rec("pg"))
    ), patch.object(
        SplashScreen, "_load_theory_cache",
        new=AsyncMock(side_effect=_rec("theory"))
    ), patch.object(
        SplashScreen, "_ready_signal",
        new=AsyncMock(side_effect=_rec("ready"))
    ):
        async with wrapper.run_test() as pilot:
            await pilot.pause()
            screen = wrapper.screen
            await screen._init_task
            # step 0 失败标记: content 或 CSS class 应反映失败
            step0 = screen.query_one("#step-0", Static)
            content = str(step0.content)
            classes = step0.classes
            assert (
                "失败" in content or "✗" in content
                or "splash-step-failed" in classes
            )
    # 后续 3 step 仍调用了
    assert later_called == ["pg", "theory", "ready"]


@pytest.mark.asyncio
async def test_splash_screen_init_lexicon_calls_backend(
    tmp_path, monkeypatch
) -> None:
    """_init_lexicon 实际 await init_lexicon_backend."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.splash_screen import SplashScreen

    screen = SplashScreen()
    backend_mock = AsyncMock(return_value=True)
    with patch(
        "explain_engine.engines.lexicon.init_lexicon_backend",
        new=backend_mock,
    ):
        await screen._init_lexicon()
    backend_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_splash_screen_ping_pg_calls_verify_connection(
    tmp_path, monkeypatch
) -> None:
    """_ping_pg await verify_connection — 失败时仍吞 (不抛)."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.splash_screen import SplashScreen

    screen = SplashScreen()
    # 成功 path
    vc_ok = AsyncMock(return_value=None)
    with patch(
        "explain_engine.persistence.lexicon_pg.verify_connection", new=vc_ok
    ):
        await screen._ping_pg()
    vc_ok.assert_awaited_once()

    # 失败 path: 不抛, swallow
    vc_fail = AsyncMock(side_effect=RuntimeError("db down"))
    with patch(
        "explain_engine.persistence.lexicon_pg.verify_connection", new=vc_fail
    ):
        # 不应抛 — splash 失败 step 单独标记, 不阻塞 app start
        await screen._ping_pg()


@pytest.mark.asyncio
async def test_splash_screen_load_theory_cache_calls_sync(
    tmp_path, monkeypatch
) -> None:
    """_load_theory_cache 直接 sync 调 get_active_theories — 短 IO 不到 asyncio.to_thread."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.splash_screen import SplashScreen

    screen = SplashScreen()
    cache_mock = MagicMock(return_value=MagicMock())
    with patch(
        "explain_engine.engines.theory.cache.get_active_theories",
        new=cache_mock,
    ):
        await screen._load_theory_cache()
    cache_mock.assert_called_once()


@pytest.mark.asyncio
async def test_splash_screen_ready_signal_is_async_noop(
    tmp_path, monkeypatch
) -> None:
    """_ready_signal 为 async noop sleep — 不抛."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.splash_screen import SplashScreen

    screen = SplashScreen()
    await screen._ready_signal()
