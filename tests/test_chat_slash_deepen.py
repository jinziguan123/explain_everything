"""/deepen handler — Phase 18 Task 9+."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
from explain_engine.persistence.storage_v2 import StorageV2


def test_deepen_registered_in_default_commands():
    names = [c.name for c in DEFAULT_COMMANDS]
    assert "deepen" in names


@pytest.mark.asyncio
async def test_deepen_with_explicit_question(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    # 注入 fake llm + mock promote_to_persistent 避真 LLM 调用
    fake_real_chat = MagicMock(sid="s_test1234")
    ephemeral.promote_to_persistent = AsyncMock(return_value=fake_real_chat)
    ephemeral._llm_for_test = MagicMock()  # Task 9 placeholder, Task 12 wire chat.llm

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, ["为什么", "烧水", "能沸"])

    ephemeral.promote_to_persistent.assert_called_once()
    call_args = ephemeral.promote_to_persistent.call_args
    passed_q = call_args[0][0] if call_args[0] else call_args.kwargs["question"]
    assert passed_q == "为什么 烧水 能沸"
    assert any(e.type == "slash_deepen_promoted" for e in events)


@pytest.mark.asyncio
async def test_deepen_without_args_uses_last_user_msg(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)
    ephemeral.transcript = [
        {"role": "user", "content": "为什么烧水能沸"},
        {"role": "assistant", "content": "因为蒸汽压..."},
        {"role": "user", "content": "能再说说吗"},
    ]
    fake_real_chat = MagicMock(sid="s_test1234")
    ephemeral.promote_to_persistent = AsyncMock(return_value=fake_real_chat)
    ephemeral._llm_for_test = MagicMock()

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    await cmd.handler(ephemeral, [])

    call_args = ephemeral.promote_to_persistent.call_args
    question = call_args[0][0] if call_args[0] else call_args.kwargs["question"]
    # 最近 user msg (倒序找), 不识别 "为什么" 模式
    assert question == "能再说说吗"
