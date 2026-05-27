"""Phase 19 Wave 3 Task 17: repl_entry.enter_repl_async 用 textual App.

老 Phase 11/18 Rich Console + prompt_toolkit 整段删, 改 5 行调用 textual App:
    ExplainChatApp(llm=..., light_llm=..., ephemeral_chat=...).run_async()

Test 不真启 textual (run_async 会挂 non-TTY pytest), 而是 monkeypatch
ExplainChatApp.run_async 收集调用参数.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_enter_repl_async_runs_textual_app(tmp_path, monkeypatch) -> None:
    """enter_repl_async 内部 build ExplainChatApp + await app.run_async().

    Phase 19 Wave 6: 默 show_splash=True; init_lexicon_backend 不在 repl_entry
    自跑 (splash 搬家). 故无需 monkeypatch init.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_repl_textual")

    # mock make_llm_client / make_light_llm_client (避真 LLM env 要求)
    fake_llm = AsyncMock(name="main_llm")
    fake_light = AsyncMock(name="light_llm")
    monkeypatch.setattr(
        "explain_engine.config.make_llm_client", lambda: fake_llm
    )
    monkeypatch.setattr(
        "explain_engine.config.make_light_llm_client", lambda: fake_light
    )

    # mock init_lexicon_backend 防真 PG 连 — 默路径 show_splash=True 不应调
    # 它 (splash 搬家). 若意外调到, 这个 mock 也吞.
    init_called: list[bool] = []

    async def fake_init():
        init_called.append(True)
        return False

    monkeypatch.setattr(
        "explain_engine.engines.lexicon.init_lexicon_backend", fake_init
    )

    # mock ExplainChatApp.run_async 收集调用
    captured: dict = {}

    async def fake_run_async(self):
        captured["chat"] = self.chat
        captured["llm"] = self.llm
        captured["light_llm"] = self.light_llm
        captured["show_splash"] = self.show_splash

    monkeypatch.setattr(
        "explain_engine.chat.tui_app.ExplainChatApp.run_async", fake_run_async
    )

    from explain_engine.chat.repl_entry import enter_repl_async

    await enter_repl_async()

    assert captured["llm"] is fake_llm
    assert captured["light_llm"] is fake_light
    # chat 是 EphemeralChatSession (启始态)
    from explain_engine.chat.ephemeral import EphemeralChatSession
    assert isinstance(captured["chat"], EphemeralChatSession)
    # default show_splash=True
    assert captured["show_splash"] is True
    # 默路径 show_splash=True → repl_entry 不调 init_lexicon (搬家到 splash)
    assert init_called == [], (
        f"默 show_splash=True 时 repl_entry 应不调 init_lexicon_backend (搬家), "
        f"got init_called={init_called}"
    )


@pytest.mark.asyncio
async def test_enter_repl_async_no_splash_runs_init_lexicon(
    tmp_path, monkeypatch
) -> None:
    """show_splash=False (cli --no-splash) → repl_entry 自跑 init_lexicon_backend."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_repl_no_splash")

    fake_llm = AsyncMock(name="main_llm")
    fake_light = AsyncMock(name="light_llm")
    monkeypatch.setattr(
        "explain_engine.config.make_llm_client", lambda: fake_llm
    )
    monkeypatch.setattr(
        "explain_engine.config.make_light_llm_client", lambda: fake_light
    )

    init_called: list[bool] = []

    async def fake_init():
        init_called.append(True)
        return False

    monkeypatch.setattr(
        "explain_engine.engines.lexicon.init_lexicon_backend", fake_init
    )

    captured: dict = {}

    async def fake_run_async(self):
        captured["show_splash"] = self.show_splash

    monkeypatch.setattr(
        "explain_engine.chat.tui_app.ExplainChatApp.run_async", fake_run_async
    )

    from explain_engine.chat.repl_entry import enter_repl_async

    await enter_repl_async(show_splash=False)

    assert captured["show_splash"] is False
    assert init_called == [True], (
        f"show_splash=False 时 repl_entry 应自跑 init_lexicon_backend, "
        f"got init_called={init_called}"
    )


@pytest.mark.asyncio
async def test_enter_repl_async_llm_not_configured(tmp_path, monkeypatch) -> None:
    """LLM env 未配 (make_llm_client 抛 KeyError) → enter_repl_async 不崩, llm=None.

    跟 Phase 11/18 行为对齐 — slash 仍可 work, 自然语言会 fail at handler level.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_repl_textual_noenv")

    def fake_make_llm():
        raise KeyError("LLM_API_KEY")

    monkeypatch.setattr("explain_engine.config.make_llm_client", fake_make_llm)
    monkeypatch.setattr(
        "explain_engine.config.make_light_llm_client", fake_make_llm
    )

    async def fake_init():
        return False

    monkeypatch.setattr(
        "explain_engine.engines.lexicon.init_lexicon_backend", fake_init
    )

    captured: dict = {}

    async def fake_run_async(self):
        captured["llm"] = self.llm

    monkeypatch.setattr(
        "explain_engine.chat.tui_app.ExplainChatApp.run_async", fake_run_async
    )

    from explain_engine.chat.repl_entry import enter_repl_async

    await enter_repl_async()

    assert captured["llm"] is None


# ─── Bug 2 (Phase 19 hotfix): kitty CSI-u keyboard protocol disable for macOS Terminal.app ───
# 真因: textual 默开 kitty 增强键盘协议 (CSI-u escape), macOS Terminal.app 不识别 →
# 用户输汉字时显 raw `[49;;20026:20160:20040u` 而不是 IME 提交的 "为什么". textual
# upstream 已经知道 Apple_Terminal 有 sync mode 问题 (linux_driver.py L322 特判),
# 但 kitty key protocol 没特判 → 这里在 repl_entry 顶部 detect TERM_PROGRAM 后
# os.environ.setdefault("TEXTUAL_DISABLE_KITTY_KEY", "1"), 让 textual.constants
# module-level Final 读到. setdefault 而非 [] 赋值: user 显式设了别覆盖.


@pytest.mark.asyncio
async def test_enter_repl_async_apple_terminal_sets_disable_kitty_key(
    tmp_path, monkeypatch
) -> None:
    """macOS Terminal.app (TERM_PROGRAM=Apple_Terminal) → enter_repl_async 自动设
    TEXTUAL_DISABLE_KITTY_KEY=1, 让 textual driver 跳过 kitty CSI-u 协议.

    没这个 fix, 真终端用户输汉字会显 raw `[49;;{codepoint}u` escape (production
    smoke bug 2).
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_repl_apple_term")
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    monkeypatch.delenv("TEXTUAL_DISABLE_KITTY_KEY", raising=False)

    fake_llm = AsyncMock(name="main_llm")
    fake_light = AsyncMock(name="light_llm")
    monkeypatch.setattr(
        "explain_engine.config.make_llm_client", lambda: fake_llm
    )
    monkeypatch.setattr(
        "explain_engine.config.make_light_llm_client", lambda: fake_light
    )

    async def fake_run_async(self):
        pass

    monkeypatch.setattr(
        "explain_engine.chat.tui_app.ExplainChatApp.run_async", fake_run_async
    )

    import os

    from explain_engine.chat.repl_entry import enter_repl_async

    await enter_repl_async(show_splash=False)

    assert os.environ.get("TEXTUAL_DISABLE_KITTY_KEY") == "1", (
        "Apple_Terminal 检测应设 TEXTUAL_DISABLE_KITTY_KEY=1, "
        f"got {os.environ.get('TEXTUAL_DISABLE_KITTY_KEY')!r}"
    )


@pytest.mark.asyncio
async def test_enter_repl_async_iterm_does_not_set_disable_kitty(
    tmp_path, monkeypatch
) -> None:
    """非 Apple_Terminal (iTerm2 / kitty / WezTerm 等) → 不动 TEXTUAL_DISABLE_KITTY_KEY.

    这些终端支持 kitty CSI-u, 应保持 textual 默 enhanced 模式 (更准 key event).
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_repl_iterm")
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.delenv("TEXTUAL_DISABLE_KITTY_KEY", raising=False)

    fake_llm = AsyncMock(name="main_llm")
    fake_light = AsyncMock(name="light_llm")
    monkeypatch.setattr(
        "explain_engine.config.make_llm_client", lambda: fake_llm
    )
    monkeypatch.setattr(
        "explain_engine.config.make_light_llm_client", lambda: fake_light
    )

    async def fake_run_async(self):
        pass

    monkeypatch.setattr(
        "explain_engine.chat.tui_app.ExplainChatApp.run_async", fake_run_async
    )

    import os

    from explain_engine.chat.repl_entry import enter_repl_async

    await enter_repl_async(show_splash=False)

    assert os.environ.get("TEXTUAL_DISABLE_KITTY_KEY") is None, (
        "非 Apple_Terminal 不应 set TEXTUAL_DISABLE_KITTY_KEY, "
        f"got {os.environ.get('TEXTUAL_DISABLE_KITTY_KEY')!r}"
    )


@pytest.mark.asyncio
async def test_enter_repl_async_user_explicit_setting_preserved(
    tmp_path, monkeypatch
) -> None:
    """User 显式设 TEXTUAL_DISABLE_KITTY_KEY=0 (e.g. 强 enable) 时 setdefault 不覆盖.

    setdefault 语义保留: detect 仅给出"建议值", user 显式 set 优先.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_repl_user_explicit")
    monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
    monkeypatch.setenv("TEXTUAL_DISABLE_KITTY_KEY", "0")

    fake_llm = AsyncMock(name="main_llm")
    fake_light = AsyncMock(name="light_llm")
    monkeypatch.setattr(
        "explain_engine.config.make_llm_client", lambda: fake_llm
    )
    monkeypatch.setattr(
        "explain_engine.config.make_light_llm_client", lambda: fake_light
    )

    async def fake_run_async(self):
        pass

    monkeypatch.setattr(
        "explain_engine.chat.tui_app.ExplainChatApp.run_async", fake_run_async
    )

    import os

    from explain_engine.chat.repl_entry import enter_repl_async

    await enter_repl_async(show_splash=False)

    assert os.environ.get("TEXTUAL_DISABLE_KITTY_KEY") == "0", (
        f"User 显式 set '0' 应保留, got {os.environ.get('TEXTUAL_DISABLE_KITTY_KEY')!r}"
    )
