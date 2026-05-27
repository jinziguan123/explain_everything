"""Phase 19 Wave 5 Task 24: status_start/end → LoadingIndicator mount/unmount.

ExplainChatApp._render_event 接 status_start (mount LoadingIndicator + Static
label) / status_end (unmount). 设计参考 docs/plans/2026-05-27-phase-19-tui-design.md
§4.1.

单元层:
- status_start event → mount LoadingIndicator (id=status-indicator) +
  Static (id=status-label, class=status-label, 显示 ev.content).
- status_end event → 移走两 widget (idempotent).
- 多次 status_start (无 end) — Phase 19 不支持 nested, 但 helper 应 robust
  (用 query 找 + 移走所有同 id widget; 防 silent leak).

端到端集成在 Task 25 (test_phase19_status_flow.py).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_status_start_mounts_loading_indicator(
    tmp_path, monkeypatch
) -> None:
    """Task 24: status_start event → mount LoadingIndicator 在 #output 容器内.

    LoadingIndicator id="status-indicator", Static label id="status-label"
    + class="status-label" 含 ev.content.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import LoadingIndicator, Static

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._render_event(
            ChatEvent(type="status_start", content="思考中...")
        )
        await pilot.pause()
        # 1 LoadingIndicator mount
        indicators = list(app.query(LoadingIndicator))
        assert len(indicators) == 1, (
            f"期望 1 个 LoadingIndicator, 实际 {len(indicators)}"
        )
        # 1 Static label, 含 "思考中..."
        label = app.query_one("#status-label", Static)
        rendered = label.render()
        plain = rendered.plain if hasattr(rendered, "plain") else str(rendered)
        assert "思考中..." in plain


@pytest.mark.asyncio
async def test_status_end_unmounts_loading_indicator(
    tmp_path, monkeypatch
) -> None:
    """Task 24: status_end → 移走前一个 status_start mount 的 LoadingIndicator + label.

    完整 start → end 循环后, query LoadingIndicator + Static#status-label
    应都返 0.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import LoadingIndicator

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # mount
        await app._render_event(
            ChatEvent(type="status_start", content="思考中...")
        )
        await pilot.pause()
        assert len(list(app.query(LoadingIndicator))) == 1
        # unmount
        await app._render_event(ChatEvent(type="status_end"))
        await pilot.pause()
        assert len(list(app.query(LoadingIndicator))) == 0
        # Static#status-label 也应被移走
        assert len(list(app.query("#status-label"))) == 0


@pytest.mark.asyncio
async def test_status_end_idempotent_no_active_status(
    tmp_path, monkeypatch
) -> None:
    """Task 24: status_end 在无 active status_start 时不报错 (idempotent).

    场景: handle_user_input 异常 LLM call 失败 → 直接 yield status_end (跳
    status_start), 或重复 status_end. _unmount_status 应静默 no-op.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import LoadingIndicator

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # 直接 status_end (无 prior start) — 不该 raise
        await app._render_event(ChatEvent(type="status_end"))
        await pilot.pause()
        assert len(list(app.query(LoadingIndicator))) == 0


@pytest.mark.asyncio
async def test_status_label_uses_safe_plain_append(
    tmp_path, monkeypatch
) -> None:
    """Task 24 review-fix preempt: status content 含 markup-looking 字符
    ([INST] / x[0] 等) 不被 textual Content markup parser 吃掉.

    跟 Wave 4 review C-1 同款 — _mount_status 走 markup=False 或 Content
    plain append, 让 LLM-provided status text 100% 安全.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_c1_status")

    from textual.widgets import Static

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        # status content 含 markup-looking text
        content = "处理 [INST] 段 x[0] 字符"
        await app._render_event(
            ChatEvent(type="status_start", content=content)
        )
        await pilot.pause()
        label = app.query_one("#status-label", Static)
        r = label.render()
        plain = r.plain if hasattr(r, "plain") else str(r)
        # 各 [...] 字符段都应原样保留 (markup 不吃)
        assert "[INST]" in plain, (
            f"[INST] 被 markup 吃了: {plain!r}"
        )
        assert "x[0]" in plain, (
            f"x[0] 被 markup 吃了: {plain!r}"
        )


@pytest.mark.asyncio
async def test_status_start_replaces_previous_active(
    tmp_path, monkeypatch
) -> None:
    """Task 24 边界: 连续 status_start (无 end) 时, 第 2 个不应让现 mount
    LoadingIndicator + label 倍增 — 应替换 (一时刻 1 个 active status pair,
    design §5 决策).

    Phase 19 plan 假设无 nested. 这 test 验证 helper 不 leak widget.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import LoadingIndicator, Static

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(),
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._render_event(
            ChatEvent(type="status_start", content="第一个...")
        )
        await pilot.pause()
        await app._render_event(
            ChatEvent(type="status_start", content="第二个...")
        )
        await pilot.pause()
        # 仅 1 active pair
        assert len(list(app.query(LoadingIndicator))) == 1, (
            "2nd status_start 应替换 1st, 不该并存"
        )
        labels = list(app.query("#status-label"))
        assert len(labels) == 1
        # 显示的是新 label
        r = labels[0].render() if hasattr(labels[0], "render") else None
        if r is not None:
            plain = r.plain if hasattr(r, "plain") else str(r)
            assert "第二个..." in plain
        _ = Static  # 占位用 import 防 ruff
