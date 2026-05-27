"""Phase 19 Wave 7 production smoke 4-bug fix 验证 test.

4 个 bug 真实端到端验:
- Bug 1: splash pop 后 banner Static (Explain REPL) 出现在 #output 容器.
- Bug 2: Input#prompt height == 3 (CSS `height: 3` 显式 + dock bottom).
- Bug 3: Input#prompt suggester 非 None (SuggestFromList(slash_names)).
- Bug 4: app mount 完后 Input focused (默 focus, 让用户立刻能输).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_input_focused_after_mount_no_splash(tmp_path, monkeypatch) -> None:
    """Bug 4: show_splash=False 路径, app 启动后 Input#prompt 立刻 focused."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_focus_no_splash")

    from textual.widgets import Input

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
        show_splash=False,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        # 验 focused widget == Input#prompt (Bug 4 修后)
        assert app.focused is prompt, (
            f"期望 Input#prompt focused, 实际 focused={app.focused!r}"
        )


@pytest.mark.asyncio
async def test_input_focused_after_splash_pop(tmp_path, monkeypatch) -> None:
    """Bug 4: show_splash=True 路径, splash pop 后 Input#prompt focused."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_focus_splash")

    from textual.widgets import Input

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.splash_screen import SplashScreen
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
        show_splash=True,
    )

    # patch 4 step + asyncio.sleep 避免真等
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
            await pilot.pause()
            # 等 splash 完全 pop
            for _ in range(15):
                await pilot.pause()
            # 此刻 splash pop, Input#prompt focused
            assert not isinstance(app.screen, SplashScreen)
            prompt = app.query_one("#prompt", Input)
            assert app.focused is prompt, (
                f"splash pop 后期望 Input#prompt focused, 实际 focused={app.focused!r}"
            )


@pytest.mark.asyncio
async def test_input_has_slash_suggester(tmp_path, monkeypatch) -> None:
    """Bug 3: Input#prompt 装有 textual Suggester (slash 自动补全)."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_suggester")

    from textual.suggester import Suggester
    from textual.widgets import Input

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
        show_splash=False,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        assert prompt.suggester is not None, "Input#prompt 应装 Suggester (slash 自动补全)"
        assert isinstance(prompt.suggester, Suggester)


@pytest.mark.asyncio
async def test_suggester_returns_slash_help(tmp_path, monkeypatch) -> None:
    """Bug 3: Suggester 对 '/h' 应返补全 '/help' (验真实 slash 名单)."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_suggester_help")

    from textual.widgets import Input

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
        show_splash=False,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        sugg = prompt.suggester
        assert sugg is not None
        # SuggestFromList.get_suggestion 是 async, 返 str | None.
        result = await sugg.get_suggestion("/h")
        assert result is not None
        assert result.startswith("/h")
        assert result in {"/help", "/history"}


@pytest.mark.asyncio
async def test_input_not_overlapping_footer(tmp_path, monkeypatch) -> None:
    """Bug 2: Input#prompt 底 border 不被 Footer 盖.

    真因: Input + Footer 都 dock bottom 时, Footer 占 y=23 这一行, 而 Input
    region 是 y=21..23 (含 y=23 底 border 行) — 两者 y=23 重叠, Footer 视觉
    上盖了 Input 的最底 border. 修复: Input 显式 height: 3 + 不 dock bottom,
    让 Footer dock bottom 后 Input 自然落在 Footer 上方 (无重叠).
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_input_no_overlap")

    from textual.widgets import Footer, Input

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
        show_splash=False,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        footer = app.query_one(Footer)
        input_bottom = prompt.region.y + prompt.region.height
        footer_top = footer.region.y
        assert input_bottom <= footer_top, (
            f"Input 底 (y={input_bottom}) 不该越过 Footer 顶 (y={footer_top}). "
            f"Input region={prompt.region}, Footer region={footer.region}. "
            f"Bug 2: 二者 dock bottom 重叠, Footer 盖 Input 底 border."
        )


@pytest.mark.asyncio
async def test_banner_visible_after_mount_no_splash(tmp_path, monkeypatch) -> None:
    """Bug 1: show_splash=False 路径 on_mount 后 #output 含 banner Static 'Explain REPL'."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_banner_no_splash")

    from textual.containers import VerticalScroll
    from textual.widgets import Static

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
        show_splash=False,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        container = app.query_one("#output", VerticalScroll)
        statics = list(container.query(Static))
        # 至少 1 个 Static, 内容含 "Explain REPL" (textual Static.render() 返 Content).
        joined = "\n".join(str(s.render()) for s in statics)
        assert "Explain REPL" in joined, (
            f"期望 #output 含 'Explain REPL' banner, 实际 statics={joined!r}"
        )


@pytest.mark.asyncio
async def test_banner_visible_after_splash_pop(tmp_path, monkeypatch) -> None:
    """Bug 1: show_splash=True 路径 splash pop 后 #output 含 banner 'Explain REPL'."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_banner_splash")

    from textual.containers import VerticalScroll
    from textual.widgets import Static

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.splash_screen import SplashScreen
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
        show_splash=True,
    )

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
            await pilot.pause()
            for _ in range(15):
                await pilot.pause()
            assert not isinstance(app.screen, SplashScreen)
            container = app.query_one("#output", VerticalScroll)
            statics = list(container.query(Static))
            joined = "\n".join(str(s.render()) for s in statics)
            assert "Explain REPL" in joined, (
                f"splash pop 后期望 #output 含 'Explain REPL' banner, "
                f"实际 statics={joined!r}"
            )
