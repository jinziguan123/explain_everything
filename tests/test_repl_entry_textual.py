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
