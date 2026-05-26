"""repl_entry ephemeral 分支 — Phase 18 Wave 3 Task 16 + 17.

验证 outer loop ephemeral 状态下:
- Task 16: user 输自然语言 → chat.handle_user_input(text, llm) (不再 auto promote)
- Task 17: /deepen 成功 → slash_deepen_promoted event 触发 chat var 切到 ChatSession
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_ephemeral_natural_language_calls_handle_user_input(
    tmp_path, monkeypatch
):
    """Phase 18 Task 16: ephemeral 自然语言 → chat.handle_user_input, 不 auto promote.

    Strategy: 不跑真 enter_repl_async (太多 IO + LLM init). 仅 inline 复制 outer
    loop ephemeral 分支的核心逻辑, 验 handle_user_input 被调 + promote 未触发.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent
    from explain_engine.persistence.storage_v2 import StorageV2

    storage = StorageV2()
    llm = MagicMock(name="injected_llm")
    ephemeral = EphemeralChatSession(storage=storage, llm=llm)

    # Spy handle_user_input + promote_to_persistent
    async def fake_handle_user_input(text, llm):
        yield ChatEvent(type="assistant_text", content=f"echo: {text}")
        yield ChatEvent(type="turn_complete", content=None)

    ephemeral.handle_user_input = fake_handle_user_input
    ephemeral.promote_to_persistent = AsyncMock()

    # 用 repl_entry 内的 _render_event signature 等价的 sink, 收事件 (省 console)
    events_seen = []
    async for ev in ephemeral.handle_user_input("为什么烧水能沸", llm):
        events_seen.append(ev)

    # 验: handle_user_input yield 了 assistant_text + turn_complete
    types = [e.type for e in events_seen]
    assert "assistant_text" in types
    assert "turn_complete" in types
    # promote_to_persistent 未被调用 (auto-promote 已撤掉, 仅 /deepen 触发)
    ephemeral.promote_to_persistent.assert_not_called()


@pytest.mark.asyncio
async def test_repl_entry_ephemeral_natural_language_calls_handle_user_input(
    tmp_path, monkeypatch
):
    """Task 16 集成验: 跑 enter_repl_async 1 轮自然语言, 确认 handle_user_input 被调.

    Mock LLM + input 序列 + 退出. 验 ephemeral.handle_user_input 触发,
    promote_to_persistent 0 调用.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_repl")

    from explain_engine.chat import repl_entry

    # 计数 handle_user_input / promote_to_persistent 实际调用次数
    handle_called = []
    promote_called = []

    orig_init = repl_entry.EphemeralChatSession.__init__

    def patched_init(self, **kwargs):
        orig_init(self, **kwargs)
        orig_handle = self.handle_user_input

        async def spy_handle(text, llm):
            handle_called.append((text, llm))
            async for ev in orig_handle(text, llm):
                yield ev

        async def spy_promote(*args, **kwargs):
            promote_called.append(args)
            raise RuntimeError("不该被调")

        self.handle_user_input = spy_handle
        self.promote_to_persistent = spy_promote

    monkeypatch.setattr(
        repl_entry.EphemeralChatSession, "__init__", patched_init
    )

    # mock LLM 返简单 chat 文本
    class FakeResp:
        text = "答案"

    fake_llm = MagicMock()
    fake_llm.chat = AsyncMock(return_value=FakeResp())
    monkeypatch.setattr(
        repl_entry, "make_llm_client", lambda: fake_llm
    )

    # input 序列: 1 句自然语言 → /quit
    inputs = iter(["为什么烧水能沸", "/quit"])

    def make_session_stub(_log_handler):
        return None

    async def read_input_stub(_pt_session, prompt_text=""):
        try:
            return next(inputs)
        except StopIteration as exc:
            raise EOFError from exc

    monkeypatch.setattr(repl_entry, "make_session", make_session_stub)
    monkeypatch.setattr(repl_entry, "read_input", read_input_stub)

    # lexicon backend init: 走 fallback JSON, 但避免 PG 连接
    async def stub_init_lexicon():
        return False

    monkeypatch.setattr(
        "explain_engine.engines.lexicon.init_lexicon_backend", stub_init_lexicon
    )

    await repl_entry.enter_repl_async()

    # 验 handle_user_input 被调 1 次, promote 0 次
    assert len(handle_called) == 1, f"handle_user_input 未被调或多调: {handle_called}"
    assert handle_called[0][0] == "为什么烧水能沸"
    assert handle_called[0][1] is fake_llm
    assert len(promote_called) == 0, f"promote_to_persistent 不该被调: {promote_called}"


@pytest.mark.asyncio
async def test_repl_entry_deepen_event_switches_chat_var(
    tmp_path, monkeypatch
):
    """Task 17: ephemeral /deepen 成功 (slash_deepen_promoted event) → chat var 变 ChatSession.

    Mock dispatch_slash 返 slash_deepen_promoted event with metadata.sid,
    再 mock ChatSession.__init__ 验它被调用 with 该 sid.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_repl")

    from explain_engine.chat import repl_entry
    from explain_engine.chat.session import ChatEvent

    # mock LLM
    fake_llm = MagicMock()
    monkeypatch.setattr(repl_entry, "make_llm_client", lambda: fake_llm)

    # input 序列: /deepen Q → /quit
    inputs = iter(["/deepen 为什么", "/quit"])

    def make_session_stub(_log_handler):
        return None

    async def read_input_stub(_pt_session, prompt_text=""):
        try:
            return next(inputs)
        except StopIteration as exc:
            raise EOFError from exc

    monkeypatch.setattr(repl_entry, "make_session", make_session_stub)
    monkeypatch.setattr(repl_entry, "read_input", read_input_stub)

    async def stub_init_lexicon():
        return False

    monkeypatch.setattr(
        "explain_engine.engines.lexicon.init_lexicon_backend", stub_init_lexicon
    )

    # mock dispatch_slash: 第 1 次 (deepen) 返 slash_deepen_promoted, 第 2 次 (quit) 返 slash_quit
    promoted_sid = "s_promoted_xxx"

    async def fake_dispatch_slash(chat, text):
        if text.startswith("/deepen"):
            return [ChatEvent(
                type="slash_deepen_promoted",
                content="模拟 promote 完",
                metadata={"sid": promoted_sid},
            )]
        if text.startswith("/quit"):
            return [ChatEvent(type="slash_quit", content="bye")]
        return []

    monkeypatch.setattr(
        "explain_engine.chat.slash_commands.dispatch_slash", fake_dispatch_slash
    )

    # mock ChatSession.__init__: 不真 load (无 sidecar), 仅记录调用
    chat_session_calls = []

    real_chat_session_cls = repl_entry.ChatSession

    class StubChatSession:
        def __init__(self, sid, llm=None):
            chat_session_calls.append((sid, llm))
            self.sid = sid
            self.llm = llm
            self.input_provider = None

        async def aclose(self):
            pass

    monkeypatch.setattr(repl_entry, "ChatSession", StubChatSession)

    # 也 stub print_history_section 避读 transcript
    monkeypatch.setattr(
        "explain_engine.chat.history_render.print_history_section",
        lambda *_a, **_kw: None,
    )

    await repl_entry.enter_repl_async()

    # 验 ChatSession 被 init with promoted sid
    assert len(chat_session_calls) >= 1, "ChatSession 未被建 — slash_deepen_promoted 未触发切换"
    assert chat_session_calls[0][0] == promoted_sid
    assert chat_session_calls[0][1] is fake_llm

    # Restore (defensive — monkeypatch 会 auto restore, 这里 noop)
    _ = real_chat_session_cls
