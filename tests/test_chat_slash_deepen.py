"""/deepen handler — Phase 18 Task 9+."""

from unittest.mock import AsyncMock, MagicMock, patch

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
    # Phase 18 Wave 2 review M-1: 直接 EphemeralChatSession(llm=...) 显式注入,
    # 不再走 _llm_for_test fallback (handler 已删 fallback path).
    ephemeral = EphemeralChatSession(storage=storage, llm=MagicMock())

    # mock promote_to_persistent 避真 LLM 调用 (test 只验 handler 派发, 非 promote 内部)
    fake_real_chat = MagicMock(sid="s_test1234")
    ephemeral.promote_to_persistent = AsyncMock(return_value=fake_real_chat)

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
    # Phase 18 Wave 2 review M-1: 显式 llm 注入, 不走 _llm_for_test fallback.
    ephemeral = EphemeralChatSession(storage=storage, llm=MagicMock())
    ephemeral.transcript = [
        {"role": "user", "content": "为什么烧水能沸"},
        {"role": "assistant", "content": "因为蒸汽压..."},
        {"role": "user", "content": "能再说说吗"},
    ]
    fake_real_chat = MagicMock(sid="s_test1234")
    ephemeral.promote_to_persistent = AsyncMock(return_value=fake_real_chat)

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


@pytest.mark.asyncio
async def test_deepen_preserves_ephemeral_chat_state_budget(
    tmp_path, monkeypatch
):
    """Phase 18 Wave 3 hotfix C-1 regression: ephemeral 设 /budget → /deepen 后,
    outer loop 用 metadata.sid 重 build ChatSession 时仍能 load 回 budget 限制.

    Bug 复现: promote_to_persistent 内 build real_chat + 拷 chat_state 后未 persist,
    outer loop ChatSession(sid) → load_chat_state 返 None → 默 ChatStateDict() 全 0
    → 用户设的 /budget 5 50 silent 丢失.

    Fix: real_chat.persist() 落盘 chat_state.json.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_c1_regression")

    from explain_engine.chat.session import ChatSession, ChatStateDict
    from explain_engine.schema.nodes import VariableNode

    storage = StorageV2()
    llm = MagicMock(name="injected_llm")

    # ephemeral: 用户设 /budget 5 50 → 写到 chat_state.budget_per_turn_limit=5
    # / budget_per_session_limit=50 (按 ChatStateDict 真字段名)
    ephemeral = EphemeralChatSession(storage=storage, llm=llm)
    ephemeral.chat_state = ChatStateDict(
        budget_per_turn_limit=5,
        budget_per_session_limit=50,
    )

    # mock bootstrap_phenomena 避真 LLM 调 (8 个 fake VariableNode 满足 min_count)
    fake_phenomena = [
        VariableNode(
            id=f"p_{i + 1:03d}",
            name=f"p{i}",
            description="d",
            abstraction_level=0,
            confidence=0.7,
            epistemic="observation",
        )
        for i in range(8)
    ]

    # mock make_light_llm_client 避读 env (test 无 LLM_LIGHT_* / LLM_*).
    # 注: promote 内 make_light_llm_client 只是建 client, 实际不 call (bootstrap
    # 已被 patch). 返 MagicMock 即可.
    with (
        patch(
            "explain_engine.chat.ephemeral.bootstrap_phenomena",
            return_value=fake_phenomena,
        ),
        patch(
            "explain_engine.chat.ephemeral.make_light_llm_client",
            return_value=MagicMock(name="light_llm"),
        ),
    ):
        real_chat = await ephemeral.promote_to_persistent("question", llm)

    # 断 1: real_chat 自身 chat_state OK (in-memory 拷)
    assert real_chat.chat_state.budget_per_turn_limit == 5
    assert real_chat.chat_state.budget_per_session_limit == 50

    # 断 2 (关键修复点): outer loop 模拟 — 用 sid 重 build ChatSession 后仍保留.
    # Bug 时这里两个 assert 都是 0 (silent loss).
    rebuilt = ChatSession(real_chat.sid, llm=llm)
    assert rebuilt.chat_state.budget_per_turn_limit == 5
    assert rebuilt.chat_state.budget_per_session_limit == 50


# ---- Phase 19 Task 10: _handle_deepen yield status_start/end ----


@pytest.mark.asyncio
async def test_deepen_yields_status_start_before_promote(tmp_path, monkeypatch):
    """promote_to_persistent 调用前 yield status_start("启动深度建模 — classify 中...").

    textual TUI 在 5-10s bootstrap pipeline 期间 visible 显示 LoadingIndicator.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage, llm=MagicMock())
    fake_real_chat = MagicMock(sid="s_test1234")
    ephemeral.promote_to_persistent = AsyncMock(return_value=fake_real_chat)

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, ["为什么", "X"])

    types = [e.type for e in events]
    assert "status_start" in types
    # 找 status_start event 验内容
    status_start = next(e for e in events if e.type == "status_start")
    assert "启动深度建模" in status_start.content or "classify" in status_start.content


@pytest.mark.asyncio
async def test_deepen_yields_status_end_after_promote_success(tmp_path, monkeypatch):
    """promote 成功后 yield status_end. 顺序: status_start → status_end → slash_deepen_promoted."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage, llm=MagicMock())
    fake_real_chat = MagicMock(sid="s_test1234")
    ephemeral.promote_to_persistent = AsyncMock(return_value=fake_real_chat)

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, ["为什么"])

    types = [e.type for e in events]
    assert types.index("status_start") < types.index("status_end") < types.index("slash_deepen_promoted")


@pytest.mark.asyncio
async def test_deepen_yields_status_end_on_promote_failure(tmp_path, monkeypatch):
    """promote 失败也 yield status_end (清 spinner) + slash_error.
    顺序: status_start → status_end → slash_error.
    """
    from explain_engine.llm.errors import LLMError

    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage, llm=MagicMock())
    ephemeral.promote_to_persistent = AsyncMock(side_effect=LLMError("classify failed"))

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, ["为什么"])

    types = [e.type for e in events]
    assert "status_start" in types
    assert "status_end" in types
    assert "slash_error" in types
    assert types.index("status_start") < types.index("status_end") < types.index("slash_error")


@pytest.mark.asyncio
async def test_deepen_no_status_when_already_promoted(tmp_path, monkeypatch):
    """ChatSession 内 /deepen 直接拒绝 (无 promote 调用), 不 yield status (无 spinner 必要)."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    # 构造一个 fake "已 promote" 的 chat (is_ephemeral=False)
    chat = MagicMock(is_ephemeral=False)
    chat._session = MagicMock()
    chat._session.meta = MagicMock(question="已建模问题")

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(chat, ["别的问题"])

    types = [e.type for e in events]
    assert "status_start" not in types
    assert "status_end" not in types
    # 只有 slash_error 拒绝
    assert "slash_error" in types


@pytest.mark.asyncio
async def test_deepen_no_status_when_empty_transcript_no_args(tmp_path, monkeypatch):
    """transcript 空 + 不带 args → 用法 slash_error, 不 yield status (没真调 LLM)."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)
    # transcript 空 + 不带 args

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, [])

    types = [e.type for e in events]
    assert "status_start" not in types
    assert "status_end" not in types
    assert "slash_error" in types


@pytest.mark.asyncio
async def test_deepen_no_status_when_llm_not_configured(tmp_path, monkeypatch):
    """ephemeral.llm = None → 早 return slash_error, 不 yield status."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage, llm=None)

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, ["为什么"])

    types = [e.type for e in events]
    assert "status_start" not in types
    assert "status_end" not in types
    assert "slash_error" in types
