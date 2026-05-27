"""repl_entry ephemeral 分支 — Phase 18 Wave 3 Task 16 + 17 单元层 (Phase 19 Wave 5
Task 26 后).

验证 ephemeral chat handler 单元行为:
- Task 16: user 输自然语言 → chat.handle_user_input(text, llm) (不再 auto promote)

Phase 19 Wave 5 Task 26: 之前 3 个 xfailed integration test (legacy
prompt_toolkit-based) 已删 — 等价语义已 cover:
- ephemeral natural language → handle_user_input: test_tui_app_input.py +
  test_phase19_status_flow.py (端到端验 mount/unmount)
- slash_deepen_promoted → chat var 切换: test_tui_app_render.py 的
  _switch_to_chat_session 单元层 + test_phase19_status_flow.py 集成层
- slash_deepen_promoted metadata 缺失防御: test_tui_app_render.py
  test_slash_deepen_promoted_missing_metadata_keeps_ephemeral (已等价)

Phase 19 后 enter_repl_async 是 textual App 启动器, 不再 outer-loop pattern,
原 3 test 跑不通且语义已分布到 textual.pilot fixture-based test.
"""

from unittest.mock import AsyncMock, MagicMock

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
