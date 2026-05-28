"""Phase 19 真终端 Bug C 验证: slash long-task handler 在 textual TUI 模式下 spinner 真显.

真因 (第一性原理):
- `_handle_compress` / `_handle_run` / `_handle_predict` / `_handle_counterfactual` /
  `_handle_rescore` / `_handle_theories` 等 long-running handler 用
  `Console().status(...)` 包 LLM call.
- Rich Console.status 是 Rich library 的 spinner, 走 stderr/stdout 写 ANSI.
- 但 textual TUI 模式下, textual hold stdin/stdout (alt screen), Console.status
  spinner ANSI 输出**被 textual capture** + 不显示 — user 全程看不到 LLM 在跑.
- 之前 `_handle_deepen` 已经用 `ChatEvent(status_start, ...)` event 协议解决,
  ephemeral.handle_user_input 同款. Bug C 是把同样修法扩展到其他 6 个 slash handler.

修法:
- Chat session (ChatSession + EphemeralChatSession) 加 `tui_app: Any = None` field.
- `ExplainChatApp.__init__` set `self.chat.tui_app = self`. chat var 切换时同步.
- slash handler 长 LLM task 前后调 `chat.tui_app._mount_status(label)` /
  `chat.tui_app._unmount_status()`. fallback (tui_app=None, e.g. test / batch mode)
  仍走老 `Console().status(...)` Rich path 保持 backward compat.
- ephemeral.promote_to_persistent 内 `调 LLM 生现象` Console.status 同款改.

约束 (设计 review preempt):
- 双向引用 (chat.tui_app + tui_app.chat) **不构成 GC 问题** — Python 标 GC
  cycle collector 自动处理. tui_app 生命周期 = ExplainChatApp 整个 run, chat 切换
  时 update tui_app.chat 但 old chat 仍指 tui_app, 没事 (old chat 不被外部 hold).
- helper `_with_status` 用 async context manager: `async with _with_status(chat, label): ...`
  内部 try-finally 保证 mount/unmount 配对, LLM 异常也清 spinner.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ─── Test 1: ChatSession.tui_app field exists ───────────────────────────────


@pytest.mark.asyncio
async def test_chat_session_has_tui_app_field(tmp_path, monkeypatch):
    """ChatSession 加 tui_app field, 默 None — backward compat 测试."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_tui_app_field")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.persistence.storage_v2 import StorageV2

    # ephemeral 先建 + promote → real ChatSession
    eph = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())

    # ephemeral 也应有 tui_app field 默 None
    assert hasattr(eph, "tui_app"), "EphemeralChatSession 需有 tui_app field"
    assert eph.tui_app is None, "EphemeralChatSession.tui_app 默 None"


@pytest.mark.asyncio
async def test_chat_session_real_has_tui_app_field(tmp_path, monkeypatch):
    """ChatSession (promoted) 也有 tui_app field 默 None."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_tui_app_real")

    from explain_engine.chat.session import ChatSession
    from explain_engine.persistence.session import Session, SessionMeta, SessionStore
    from explain_engine.schema.state import CognitiveState

    # 手 build 一个 session
    meta = SessionMeta.new(question="test")
    state = CognitiveState.bootstrap("test", budget=10)
    sess = Session(meta=meta, state=state)
    SessionStore().save(sess)

    real_chat = ChatSession(meta.session_id, llm=AsyncMock())
    assert hasattr(real_chat, "tui_app"), "ChatSession 需有 tui_app field"
    assert real_chat.tui_app is None, "ChatSession.tui_app 默 None"


# ─── Test 2: ExplainChatApp wire tui_app reference ──────────────────────────


@pytest.mark.asyncio
async def test_explain_chat_app_sets_chat_tui_app_reference(tmp_path, monkeypatch):
    """ExplainChatApp.__init__ 后 self.chat.tui_app is self (双向引用)."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_wire")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    eph = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(), light_llm=AsyncMock(), ephemeral_chat=eph,
    )
    # 双向引用建立
    assert app.chat is eph, "app.chat should be the ephemeral we passed"
    assert eph.tui_app is app, (
        "ExplainChatApp.__init__ should set self.chat.tui_app = self"
    )


# ─── Test 3: _handle_compress uses tui_app.mount/unmount when present ────────


@pytest.mark.asyncio
async def test_handle_compress_uses_tui_app_when_present(tmp_path, monkeypatch):
    """ChatSession 内有 tui_app 时, _handle_compress 调 tui_app._mount_status /
    _unmount_status, 不走 Console.status."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_compress_tui")

    from explain_engine.chat.session import ChatSession
    from explain_engine.chat.slash_commands import _handle_compress
    from explain_engine.persistence.session import Session, SessionMeta, SessionStore
    from explain_engine.schema.state import CognitiveState

    meta = SessionMeta.new(question="why does it rain?")
    state = CognitiveState.bootstrap("why does it rain?", budget=10)
    sess = Session(meta=meta, state=state)
    SessionStore().save(sess)

    chat = ChatSession(meta.session_id, llm=AsyncMock())

    # mock tui_app: _mount_status / _unmount_status async helpers
    tui_app = MagicMock()
    tui_app._mount_status = AsyncMock(return_value=None)
    tui_app._unmount_status = AsyncMock(return_value=None)
    chat.tui_app = tui_app

    # mock LLM calls to avoid real LLM calls; propose_candidates / score_all stub
    monkeypatch.setattr(
        "explain_engine.engines.compression.propose_candidates",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "explain_engine.engines.evaluation.score_all",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "explain_engine.engines.lexicon.get_lexicon_top_k_for_compress",
        lambda storage, k=20: [],
    )
    monkeypatch.setattr(
        "explain_engine.engines.compress_dedup.compute_compress_dedup_stats",
        lambda *a, **kw: {"reused": 0, "new": 0},
    )
    monkeypatch.setattr(
        "explain_engine.hitl.cli_interactive.review_insights_async",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "explain_engine.engines.lexicon.flush_to_lexicon",
        AsyncMock(return_value=0),
    )
    # 让 stage gate 通过 — 强行设 stage 让 with_stage_gate 不拦
    chat._session.meta.stage = "done"

    # invoke handler — 返 events 不需 verify (本测试只验 spinner mount/unmount)
    await _handle_compress(chat, [])

    # 关键断言: tui_app._mount_status / _unmount_status 真被调
    assert tui_app._mount_status.call_count >= 1, (
        "_handle_compress 应调 chat.tui_app._mount_status (textual spinner mount)"
    )
    assert tui_app._unmount_status.call_count >= 1, (
        "_handle_compress 应调 chat.tui_app._unmount_status (textual spinner unmount)"
    )


# ─── Test 4: tui_app=None fallback 走 Rich Console.status (backward compat) ──


@pytest.mark.asyncio
async def test_handle_compress_fallback_when_no_tui_app(tmp_path, monkeypatch):
    """tui_app=None (test / batch mode) 时 _handle_compress 仍正常工作 — backward compat."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_compress_fallback")

    from explain_engine.chat.session import ChatSession
    from explain_engine.chat.slash_commands import _handle_compress
    from explain_engine.persistence.session import Session, SessionMeta, SessionStore
    from explain_engine.schema.state import CognitiveState

    meta = SessionMeta.new(question="why does it rain?")
    state = CognitiveState.bootstrap("why does it rain?", budget=10)
    sess = Session(meta=meta, state=state)
    SessionStore().save(sess)

    chat = ChatSession(meta.session_id, llm=AsyncMock())
    # 不 set chat.tui_app → 默 None

    monkeypatch.setattr(
        "explain_engine.engines.compression.propose_candidates",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "explain_engine.engines.evaluation.score_all",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "explain_engine.engines.lexicon.get_lexicon_top_k_for_compress",
        lambda storage, k=20: [],
    )
    monkeypatch.setattr(
        "explain_engine.engines.compress_dedup.compute_compress_dedup_stats",
        lambda *a, **kw: {"reused": 0, "new": 0},
    )
    monkeypatch.setattr(
        "explain_engine.hitl.cli_interactive.review_insights_async",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "explain_engine.engines.lexicon.flush_to_lexicon",
        AsyncMock(return_value=0),
    )
    chat._session.meta.stage = "done"

    # 不应 throw — 走 Rich fallback path
    events = await _handle_compress(chat, [])
    assert events, "tui_app=None 时 handler 仍正常返 events"


# ─── Test 5: _handle_run uses tui_app when present ──────────────────────────


@pytest.mark.asyncio
async def test_handle_run_uses_tui_app_when_present(tmp_path, monkeypatch):
    """_handle_run 同款用 tui_app spinner."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_run_tui")

    from explain_engine.chat.session import ChatSession
    from explain_engine.chat.slash_commands import _handle_run
    from explain_engine.persistence.session import Session, SessionMeta, SessionStore
    from explain_engine.schema.state import CognitiveState

    meta = SessionMeta.new(question="test")
    state = CognitiveState.bootstrap("test", budget=10)
    sess = Session(meta=meta, state=state)
    SessionStore().save(sess)

    chat = ChatSession(meta.session_id, llm=AsyncMock())
    tui_app = MagicMock()
    tui_app._mount_status = AsyncMock(return_value=None)
    tui_app._unmount_status = AsyncMock(return_value=None)
    chat.tui_app = tui_app

    monkeypatch.setattr(
        "explain_engine.runtime.runtime.run",
        AsyncMock(return_value="converged"),
    )
    # stage gate set
    chat._session.meta.stage = "done"

    await _handle_run(chat, [])
    assert tui_app._mount_status.call_count >= 1
    assert tui_app._unmount_status.call_count >= 1


# ─── Test 6: _handle_rescore uses tui_app when present ──────────────────────


@pytest.mark.asyncio
async def test_handle_rescore_uses_tui_app_when_present(tmp_path, monkeypatch):
    """_handle_rescore 同款用 tui_app spinner."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_rescore_tui")

    from explain_engine.chat.session import ChatSession
    from explain_engine.chat.slash_commands import _handle_rescore
    from explain_engine.persistence.session import Session, SessionMeta, SessionStore
    from explain_engine.schema.state import CognitiveState

    meta = SessionMeta.new(question="test")
    state = CognitiveState.bootstrap("test", budget=10)
    sess = Session(meta=meta, state=state)
    SessionStore().save(sess)

    chat = ChatSession(meta.session_id, llm=AsyncMock())
    tui_app = MagicMock()
    tui_app._mount_status = AsyncMock(return_value=None)
    tui_app._unmount_status = AsyncMock(return_value=None)
    chat.tui_app = tui_app

    monkeypatch.setattr(
        "explain_engine.engines.rescore.rescore_session",
        AsyncMock(return_value={}),
    )

    await _handle_rescore(chat, [])
    assert tui_app._mount_status.call_count >= 1
    assert tui_app._unmount_status.call_count >= 1


# ─── Test 7: ephemeral promote_to_persistent uses tui_app when present ──────


@pytest.mark.asyncio
async def test_promote_to_persistent_uses_tui_app_when_present(tmp_path, monkeypatch):
    """ephemeral.promote_to_persistent 内 LLM call 用 tui_app spinner."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_promote_tui")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.persistence.storage_v2 import StorageV2

    eph = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    tui_app = MagicMock()
    tui_app._mount_status = AsyncMock(return_value=None)
    tui_app._unmount_status = AsyncMock(return_value=None)
    eph.tui_app = tui_app

    # mock bootstrap_phenomena (ephemeral.py module-level import → patch by ephemeral path)
    monkeypatch.setattr(
        "explain_engine.chat.ephemeral.bootstrap_phenomena",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "explain_engine.chat.ephemeral.review_phenomena_async",
        AsyncMock(return_value=[]),
    )
    # mock light_llm
    monkeypatch.setattr(
        "explain_engine.config.make_light_llm_client",
        lambda: AsyncMock(),
    )

    real_chat = await eph.promote_to_persistent("test question", AsyncMock())
    assert real_chat is not None
    # tui_app spinner 应被调
    assert tui_app._mount_status.call_count >= 1, (
        "promote_to_persistent 应调 tui_app._mount_status (LLM call 期间)"
    )
    assert tui_app._unmount_status.call_count >= 1
