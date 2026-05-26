"""EphemeralChatSession.send_user_message — Phase 18 Task 2."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.persistence.storage_v2 import StorageV2


@pytest.mark.asyncio
async def test_send_user_message_yields_assistant_text(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    resp = MagicMock()
    resp.text = "烧水沸腾是因为水的饱和蒸汽压等于大气压."
    llm.chat.return_value = resp

    events = []
    async for ev in ephemeral.send_user_message("为什么烧水能沸", llm):
        events.append(ev)

    assistant_events = [e for e in events if e.type == "assistant_text"]
    turn_complete_events = [e for e in events if e.type == "turn_complete"]

    assert len(assistant_events) >= 1
    assert "饱和蒸汽压" in assistant_events[0].content
    assert len(turn_complete_events) == 1
