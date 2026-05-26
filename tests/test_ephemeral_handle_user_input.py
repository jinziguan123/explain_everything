"""EphemeralChatSession.handle_user_input — Phase 18 Task 2."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.persistence.storage_v2 import StorageV2


@pytest.mark.asyncio
async def test_handle_user_input_yields_assistant_text(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    resp = MagicMock()
    resp.text = "烧水沸腾是因为水的饱和蒸汽压等于大气压."
    llm.chat.return_value = resp

    events = []
    async for ev in ephemeral.handle_user_input("为什么烧水能沸", llm):
        events.append(ev)

    assistant_events = [e for e in events if e.type == "assistant_text"]
    turn_complete_events = [e for e in events if e.type == "turn_complete"]

    assert len(assistant_events) >= 1
    assert "饱和蒸汽压" in assistant_events[0].content
    assert len(turn_complete_events) == 1


@pytest.mark.asyncio
async def test_handle_user_input_llm_error_no_transcript_pollution(tmp_path, monkeypatch):
    from explain_engine.llm.errors import LLMError

    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    llm.chat.side_effect = LLMError("network down")

    events = []
    async for ev in ephemeral.handle_user_input("hi", llm):
        events.append(ev)

    error_events = [e for e in events if e.type == "slash_error"]
    assert len(error_events) == 1
    assert "LLMError" in error_events[0].content
    # transcript 不变 (retry 友好)
    assert ephemeral.transcript == []


@pytest.mark.asyncio
async def test_handle_user_input_multi_turn_transcript_accumulates(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    # 2 轮 chat
    resp1 = MagicMock(text="第一轮回答")
    resp2 = MagicMock(text="第二轮回答")
    llm.chat.side_effect = [resp1, resp2]

    async for _ in ephemeral.handle_user_input("问题1", llm):
        pass
    async for _ in ephemeral.handle_user_input("问题2", llm):
        pass

    assert len(ephemeral.transcript) == 4
    assert ephemeral.transcript[0] == {"role": "user", "content": "问题1"}
    assert ephemeral.transcript[1] == {"role": "assistant", "content": "第一轮回答"}
    assert ephemeral.transcript[2] == {"role": "user", "content": "问题2"}
    assert ephemeral.transcript[3] == {"role": "assistant", "content": "第二轮回答"}


@pytest.mark.asyncio
async def test_handle_user_input_second_turn_passes_history_to_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)
    ephemeral.transcript = [
        {"role": "user", "content": "之前问题"},
        {"role": "assistant", "content": "之前回答"},
    ]

    llm = AsyncMock()
    resp = MagicMock(text="新回答")
    llm.chat.return_value = resp

    async for _ in ephemeral.handle_user_input("新问题", llm):
        pass

    # 验 LLM messages 含 history
    call_args = llm.chat.call_args
    messages = call_args[0][0]
    roles = [m.role for m in messages]
    contents = [m.content for m in messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert "之前问题" in contents
    assert "之前回答" in contents
    assert "新问题" in contents


@pytest.mark.asyncio
async def test_ephemeral_chat_does_not_persist_transcript(tmp_path, monkeypatch):
    """ephemeral 下 handle_user_input 后, storage_v2 不写 transcript.jsonl."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    llm.chat.return_value = MagicMock(text="answer")

    async for _ in ephemeral.handle_user_input("问题", llm):
        pass

    # 验 storage_v2 没有任何 session_dir (ephemeral.sid is None)
    assert ephemeral.sid is None
    # 项目 dir 应该为空 / 无 sessions
    sessions_root = storage.project_dir() / "sessions"
    if sessions_root.exists():
        assert list(sessions_root.iterdir()) == []  # 没 session dir
