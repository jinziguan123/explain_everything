"""Phase 19 Wave 5 Task 25: status_start/end 端到端集成 — yield → tui_app mount/unmount.

ephemeral.handle_user_input / _handle_deepen yield status_start/end →
ExplainChatApp._render_event 接到 → mount/unmount LoadingIndicator + label.

3 个集成验:
- ephemeral success path: chat 1 轮 → status mount 短暂出现 (LLM 调期间) → unmount
- ephemeral LLMError path: 异常 path 也 unmount (防 spinner leak)
- slash /deepen path: status_start(STATUS_DEEPEN_CLASSIFY) → promote 完 → unmount

策略: 用 LLM mock 让 chat 同步完成 (无真延时), 走完后验 final state 没 leak;
中间状态用 stepwise iteration (async for 内打断点检查) 验真 mount 过.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_ephemeral_status_start_end_full_cycle_mount_unmount(
    tmp_path, monkeypatch
) -> None:
    """Phase 19 Wave 5 Task 25: ephemeral chat 1 轮 end-to-end.

    状态序列 (yield 顺序):
        status_start("思考中...") → [LLM ok] → status_end → assistant_text → turn_complete

    分段 iter 内验:
    - status_start render 后: LoadingIndicator + label mount.
    - status_end render 后: 2 个 widget 都 unmount.
    - 整 cycle 结束: 0 LoadingIndicator (干净).
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_phase19_status_e2e")

    from textual.widgets import LoadingIndicator, Static

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    # LLM 返简单 chat response, 不含 reasoning (避免 Collapsible 干扰)
    llm = AsyncMock()
    llm.chat = AsyncMock(
        return_value=MagicMock(text="答案", reasoning=None)
    )

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=llm)
    app = ExplainChatApp(
        llm=llm,
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()

        # iter ephemeral.handle_user_input event, 分段验中间状态
        events_seen = []
        async for ev in ephemeral.handle_user_input("你好", llm):
            await app._render_event(ev)
            await pilot.pause()
            events_seen.append(ev.type)
            indicators = list(app.query(LoadingIndicator))
            if ev.type == "status_start":
                # status_start render 后, 应有 1 个 LoadingIndicator + 1 个 label
                assert len(indicators) == 1, (
                    f"status_start 后期望 1 LoadingIndicator, 实际 {len(indicators)}"
                )
                labels = list(app.query("#status-label"))
                assert len(labels) == 1
                # label 应含 "思考中"
                r = labels[0].render()
                plain = r.plain if hasattr(r, "plain") else str(r)
                assert "思考中" in plain
            elif ev.type == "status_end":
                # status_end render 后, 0 LoadingIndicator
                assert len(indicators) == 0, (
                    f"status_end 后期望 0 LoadingIndicator, 实际 {len(indicators)}"
                )
                assert len(list(app.query("#status-label"))) == 0

        # cycle 完, 终态干净
        assert len(list(app.query(LoadingIndicator))) == 0
        assert len(list(app.query("#status-label"))) == 0

        # 顺序断言: status_start 在 status_end 前
        ss_idx = events_seen.index("status_start")
        se_idx = events_seen.index("status_end")
        assert ss_idx < se_idx
        # event 序列含 assistant_text
        assert "assistant_text" in events_seen

        _ = Static  # 占 import (防 ruff)


@pytest.mark.asyncio
async def test_ephemeral_llm_error_still_unmounts_status(
    tmp_path, monkeypatch
) -> None:
    """Phase 19 Wave 5 Task 25: ephemeral LLMError path 也清 spinner.

    yield 流 (LLMError):
        status_start → [LLM raise LLMError] → status_end → slash_error → return

    关键: status_end yield 在 try/except early-return 路径上, 保证错误时不
    leak LoadingIndicator (没 unmount call).
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_phase19_status_err")

    from textual.widgets import LoadingIndicator

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.llm.errors import LLMError
    from explain_engine.persistence.storage_v2 import StorageV2

    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=LLMError("network down"))

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=llm)
    app = ExplainChatApp(
        llm=llm,
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()

        events_seen = []
        async for ev in ephemeral.handle_user_input("你好", llm):
            await app._render_event(ev)
            await pilot.pause()
            events_seen.append(ev.type)

        # 终态: 0 LoadingIndicator (LLMError path 也清干净)
        assert len(list(app.query(LoadingIndicator))) == 0, (
            "LLMError path 没清 spinner — status_end yield 丢"
        )

        # 验序列: status_start → status_end → slash_error
        assert events_seen[0] == "status_start"
        assert "status_end" in events_seen
        assert "slash_error" in events_seen
        # slash_error 在 status_end 之后
        se_idx = events_seen.index("status_end")
        err_idx = events_seen.index("slash_error")
        assert se_idx < err_idx, "status_end 应在 slash_error 之前 (先清 spinner)"


@pytest.mark.asyncio
async def test_handle_deepen_status_classify_mount_unmount(
    tmp_path, monkeypatch
) -> None:
    """Phase 19 Wave 5 Task 25: /deepen 路径 status_start(STATUS_DEEPEN_CLASSIFY)
    → [promote] → status_end → slash_deepen_promoted.

    mock promote_to_persistent 直接返 fake ChatSession (避真 bootstrap/HITL).
    验 _render_event 接 deepen status_start label 显示 "启动深度建模 — classify 中..."
    且 cycle 完毕清干净.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_phase19_deepen_status")

    from textual.widgets import LoadingIndicator

    from explain_engine.chat.chat_copy import STATUS_DEEPEN_CLASSIFY
    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.slash_commands import dispatch_slash
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    llm = AsyncMock()
    llm.chat = AsyncMock(
        return_value=MagicMock(text="答案", reasoning=None)
    )
    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=llm)

    # mock promote_to_persistent: 不真跑 bootstrap, 返 fake ChatSession 占位
    fake_persistent = MagicMock()
    fake_persistent.sid = "s_fake_promoted_xxx"

    async def fake_promote(question, llm_arg):
        return fake_persistent

    ephemeral.promote_to_persistent = fake_promote

    app = ExplainChatApp(
        llm=llm,
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )

    # mock _switch_to_chat_session 防真建 ChatSession (会读 disk 文件)
    async def stub_switch(metadata):
        app.chat = fake_persistent

    app._switch_to_chat_session = stub_switch

    async with app.run_test() as pilot:
        await pilot.pause()

        # 在 transcript 加 1 条 user msg 让 /deepen 不带参也能取问题
        ephemeral.transcript.append({"role": "user", "content": "为什么烧水"})

        # 跑 /deepen — 返 list[ChatEvent]
        events = await dispatch_slash(ephemeral, "/deepen")
        assert len(events) >= 3, f"events 太少: {[e.type for e in events]}"
        types_in_order = [e.type for e in events]
        assert types_in_order[0] == "status_start"
        assert types_in_order[0] == "status_start"
        # 第一个 event content 应是 STATUS_DEEPEN_CLASSIFY
        assert events[0].content == STATUS_DEEPEN_CLASSIFY
        # 序列含 status_end + slash_deepen_promoted
        assert "status_end" in types_in_order
        assert "slash_deepen_promoted" in types_in_order
        # status_end 在 slash_deepen_promoted 之前
        se_idx = types_in_order.index("status_end")
        prom_idx = types_in_order.index("slash_deepen_promoted")
        assert se_idx < prom_idx

        # render 每个 event, 分段验中间 LoadingIndicator
        for ev in events:
            await app._render_event(ev)
            await pilot.pause()
            indicators = list(app.query(LoadingIndicator))
            if ev.type == "status_start":
                assert len(indicators) == 1
                labels = list(app.query("#status-label"))
                assert len(labels) == 1
                r = labels[0].render()
                plain = r.plain if hasattr(r, "plain") else str(r)
                assert "深度建模" in plain or "classify" in plain
            elif ev.type == "status_end":
                assert len(indicators) == 0

        # 终态干净
        assert len(list(app.query(LoadingIndicator))) == 0
