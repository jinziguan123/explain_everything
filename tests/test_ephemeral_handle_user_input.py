"""EphemeralChatSession.handle_user_input — Phase 18 Task 2 + Phase 19 Task 8."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from explain_engine.chat.chat_copy import STATUS_THINKING
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


class _StreamingFakeLLM:
    """Phase 20.3: 真流式 fake — chat() 逐 chunk 调 on_delta(kind, text).

    模拟 anthropic_protocol 行为: 先 emit thinking deltas, 再 emit text deltas,
    最后返回完整 Response. on_delta=None 时退化为整段 (不 emit).
    """

    def __init__(self, text_chunks, thinking_chunks=None):
        self.text_chunks = text_chunks
        self.thinking_chunks = thinking_chunks or []

    async def chat(self, messages, schema=None, model=None, on_delta=None):
        full_text = "".join(self.text_chunks)
        full_think = "".join(self.thinking_chunks)
        if on_delta is not None:
            for t in self.thinking_chunks:
                await on_delta("thinking", t)
            for t in self.text_chunks:
                await on_delta("text", t)
        return MagicMock(
            text=full_text,
            reasoning=full_think or None,
        )


@pytest.mark.asyncio
async def test_handle_user_input_streams_text_deltas(tmp_path, monkeypatch):
    """流式 provider → yield 多个 assistant_text_delta (逐 chunk), 不发整段 assistant_text."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = _StreamingFakeLLM(
        text_chunks=["水", "沸腾", "是因为", "蒸汽压"],
        thinking_chunks=["让我", "想想"],
    )

    events = []
    async for ev in ephemeral.handle_user_input("为什么烧水能沸", llm):
        events.append(ev)

    types = [e.type for e in events]
    text_deltas = [e for e in events if e.type == "assistant_text_delta"]
    think_deltas = [e for e in events if e.type == "thinking_delta"]

    # 逐 chunk 流式: 4 个 text delta + 2 个 thinking delta
    assert [e.content for e in text_deltas] == ["水", "沸腾", "是因为", "蒸汽压"]
    assert [e.content for e in think_deltas] == ["让我", "想想"]
    # 流式时不再发整段 assistant_text (避免重复渲染)
    assert "assistant_text" not in types
    # spinner 在首 delta 前关 (status_end 早于第一个 delta)
    assert types.index("status_end") < types.index("thinking_delta")
    # turn_complete 收尾
    assert types[-1] == "turn_complete"
    # transcript 落完整文本 (delta 拼回)
    assert ephemeral.transcript[-1] == {
        "role": "assistant",
        "content": "水沸腾是因为蒸汽压",
    }


@pytest.mark.asyncio
async def test_handle_user_input_non_streaming_provider_fallback(tmp_path, monkeypatch):
    """非流式 provider (on_delta 不被调) → fallback 整段 assistant_text, 无 delta."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    # AsyncMock.chat 不会调 on_delta → any_text_delta=False → 整段 fallback
    llm = AsyncMock()
    llm.chat.return_value = MagicMock(text="整段回答", reasoning=None)

    events = []
    async for ev in ephemeral.handle_user_input("hi", llm):
        events.append(ev)

    types = [e.type for e in events]
    assert "assistant_text_delta" not in types
    assistant = [e for e in events if e.type == "assistant_text"]
    assert len(assistant) == 1
    assert assistant[0].content == "整段回答"
    assert types[-1] == "turn_complete"


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


# ---- Phase 19 Task 8: status_start/end + thinking_text yield ----


@pytest.mark.asyncio
async def test_handle_user_input_yields_status_start_at_head(tmp_path, monkeypatch):
    """ephemeral handle_user_input 调 LLM 前 yield status_start("思考中...")."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    resp = MagicMock(text="answer", reasoning=None)
    llm.chat.return_value = resp

    events = [ev async for ev in ephemeral.handle_user_input("hi", llm)]

    assert events[0].type == "status_start"
    assert events[0].content == STATUS_THINKING


@pytest.mark.asyncio
async def test_handle_user_input_yields_status_end_after_llm(tmp_path, monkeypatch):
    """LLM 成功后 yield status_end 清 spinner. status_end 在 status_start 之后,
    在 assistant_text 之前 (LLM 完成 → 清 spinner → 出答)."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    llm.chat.return_value = MagicMock(text="answer", reasoning=None)

    events = [ev async for ev in ephemeral.handle_user_input("hi", llm)]
    types = [e.type for e in events]

    assert "status_end" in types
    start_idx = types.index("status_start")
    end_idx = types.index("status_end")
    asst_idx = types.index("assistant_text")
    assert start_idx < end_idx < asst_idx


@pytest.mark.asyncio
async def test_handle_user_input_yields_status_end_on_llm_error(tmp_path, monkeypatch):
    """LLM 抛 LLMError 也 yield status_end (try/except early return 路径清 spinner).
    error path: status_start → status_end → slash_error."""
    from explain_engine.llm.errors import LLMError

    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    llm.chat.side_effect = LLMError("boom")

    events = [ev async for ev in ephemeral.handle_user_input("hi", llm)]
    types = [e.type for e in events]

    assert "status_start" in types
    assert "status_end" in types
    assert "slash_error" in types
    # 顺序: status_start → status_end → slash_error
    assert types.index("status_start") < types.index("status_end") < types.index("slash_error")


@pytest.mark.asyncio
async def test_handle_user_input_yields_thinking_text_when_reasoning(tmp_path, monkeypatch):
    """resp.reasoning 非 None (真 LLM 提取的 thinking content) → yield thinking_text
    event 在 status_end 之后, assistant_text 之前."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    llm.chat.return_value = MagicMock(text="answer", reasoning="先想下:饱和蒸汽压 > 大气压")

    events = [ev async for ev in ephemeral.handle_user_input("hi", llm)]
    types = [e.type for e in events]

    thinking_events = [e for e in events if e.type == "thinking_text"]
    assert len(thinking_events) == 1
    assert thinking_events[0].content == "先想下:饱和蒸汽压 > 大气压"
    # 顺序: status_start → status_end → thinking_text → assistant_text
    end_idx = types.index("status_end")
    thinking_idx = types.index("thinking_text")
    asst_idx = types.index("assistant_text")
    assert end_idx < thinking_idx < asst_idx


@pytest.mark.asyncio
async def test_handle_user_input_no_thinking_text_when_reasoning_none(tmp_path, monkeypatch):
    """resp.reasoning is None (gpt-4o / anthropic no-thinking) → 不 yield thinking_text."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    llm.chat.return_value = MagicMock(text="answer", reasoning=None)

    events = [ev async for ev in ephemeral.handle_user_input("hi", llm)]
    thinking_events = [e for e in events if e.type == "thinking_text"]
    assert thinking_events == []
