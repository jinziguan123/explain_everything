"""Phase X2: TUI 启动横幅在台账有到期未结算预测时提醒一句 (验收标准 #4)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.chat.tui_app import ExplainChatApp
from explain_engine.engines.theory.ledger import add_prediction
from explain_engine.persistence.storage_v2 import StorageV2


def _make_app() -> ExplainChatApp:
    mock_chat = MagicMock(spec=EphemeralChatSession)
    mock_chat.is_slash_command = lambda t: t.startswith("/")
    return ExplainChatApp(
        llm=MagicMock(), light_llm=None, ephemeral_chat=mock_chat,
        show_splash=False,
    )


def _banner_text(app) -> str:
    from textual.containers import VerticalScroll
    from textual.widgets import Static

    container = app.query_one("#output", VerticalScroll)
    return "\n".join(str(s.render()) for s in container.query(Static))


@pytest.mark.asyncio
async def test_banner_warns_when_due_predictions():
    """台账有已到期 pending 预测 → 横幅含提醒."""
    add_prediction(
        StorageV2(), theory_id="t_x", assertion="某断言",
        method="time_window", deadline="2020-01-01",  # 远早于今天 → 到期
    )
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        joined = _banner_text(app)
        assert "已到期" in joined and "结算" in joined, (
            f"期望横幅含到期预测提醒, 实际={joined!r}"
        )


@pytest.mark.asyncio
async def test_banner_no_warning_when_ledger_empty():
    """无到期预测 → 横幅无提醒行 (仅常规 banner)."""
    app = _make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        joined = _banner_text(app)
        assert "已到期" not in joined
