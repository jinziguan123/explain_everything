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


@pytest.mark.asyncio
async def test_deepen_empty_transcript_no_args(tmp_path, monkeypatch):
    """ephemeral 刚启动 (transcript 空) 立即 /deepen 不带参 → 用法提示 slash_error."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)
    # transcript 空 + 不带 args

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, [])

    error_events = [e for e in events if e.type == "slash_error"]
    assert len(error_events) == 1
    assert "用法" in error_events[0].content
    # 不该 触发 promote — ephemeral 没 mock promote_to_persistent, 触发会 LLMError
    # 直接 ' slash_deepen_promoted' 不应在 events 中
    assert not any(e.type == "slash_deepen_promoted" for e in events)


@pytest.mark.asyncio
async def test_deepen_uses_chat_llm_field(tmp_path, monkeypatch):
    """Task 12: EphemeralChatSession.llm field (REPL 注入) 优先于 _llm_for_test.

    验证: 构造时传 llm=...; handler 取 chat.llm 调 promote;
    metadata.sid 含 promote 返 ChatSession 的 sid (REPL outer loop Wave 3 会接).
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    injected_llm = MagicMock(name="injected_llm")
    ephemeral = EphemeralChatSession(storage=storage, llm=injected_llm)

    fake_real_chat = MagicMock(sid="s_promoted")
    ephemeral.promote_to_persistent = AsyncMock(return_value=fake_real_chat)
    # 故意不设 _llm_for_test, 验 chat.llm 走通

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, ["为什么 X"])

    # promote 被调, llm 参数 = 注入的 chat.llm
    ephemeral.promote_to_persistent.assert_called_once()
    call = ephemeral.promote_to_persistent.call_args
    passed_llm = call[0][1] if len(call[0]) >= 2 else call.kwargs.get("llm")
    assert passed_llm is injected_llm

    # slash_deepen_promoted event 带 metadata.sid
    promoted = [e for e in events if e.type == "slash_deepen_promoted"]
    assert len(promoted) == 1
    assert promoted[0].metadata["sid"] == "s_promoted"


@pytest.mark.asyncio
async def test_deepen_no_llm_at_all(tmp_path, monkeypatch):
    """没 chat.llm 也没 _llm_for_test → slash_error 提示 LLM 未配置."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)  # 不传 llm

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, ["为什么"])

    error_events = [e for e in events if e.type == "slash_error"]
    assert len(error_events) == 1
    assert "LLM" in error_events[0].content


@pytest.mark.asyncio
async def test_deepen_in_persistent_session_rejected(tmp_path, monkeypatch):
    """已 promote 的 ChatSession 内 /deepen → err_deepen_already_promoted + 提示 /new."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.session import ChatSession
    from explain_engine.persistence.session import Session, SessionMeta, SessionStore
    from explain_engine.schema.state import CognitiveState

    # 预存一个 persistent session
    state = CognitiveState.bootstrap("已建模问题", budget=20)
    meta = SessionMeta.new("已建模问题")
    SessionStore().save(Session(meta=meta, state=state))

    chat = ChatSession(meta.session_id, llm=MagicMock())

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(chat, ["别的问题"])

    error_events = [e for e in events if e.type == "slash_error"]
    assert len(error_events) == 1
    content = error_events[0].content
    # 拒绝文案 — 包含 "已建模" 或 "已 /deepen" 任一, 必含 /new + 当前 question
    assert "已 /deepen" in content or "已建模" in content
    assert "/new" in content
    assert "已建模问题" in content

    # 不该触发 promote
    assert not any(e.type == "slash_deepen_promoted" for e in events)


@pytest.mark.asyncio
async def test_deepen_promote_failure_keeps_ephemeral(tmp_path, monkeypatch):
    """promote_to_persistent raise → slash_error, ephemeral 状态保留 (无 slash_deepen_promoted)."""
    from explain_engine.llm.errors import LLMError

    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage, llm=MagicMock())
    ephemeral.promote_to_persistent = AsyncMock(
        side_effect=LLMError("classify failed")
    )

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, ["为什么"])

    error_events = [e for e in events if e.type == "slash_error"]
    assert len(error_events) == 1
    assert "建模失败" in error_events[0].content or "LLMError" in error_events[0].content
    # 不应 yield slash_deepen_promoted — REPL outer loop 不切 chat var
    promoted_events = [e for e in events if e.type == "slash_deepen_promoted"]
    assert len(promoted_events) == 0
