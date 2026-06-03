"""Phase 19 Wave 3 Task 14-15: _render_event dispatch.

Task 14: assistant_text → log.write / slash_quit → exit / 其他 → dim fallback.
Task 15: slash_deepen_promoted (建 ChatSession) / slash_reset_to_ephemeral (重建
  ephemeral) handler.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_render_assistant_text_writes_to_log(tmp_path, monkeypatch) -> None:
    """assistant_text event → mount Static 到 #output (Wave 4)."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.containers import VerticalScroll
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
        await app._render_event(ChatEvent(type="assistant_text", content="hello world"))
        await pilot.pause()
        container = app.query_one("#output", VerticalScroll)
        statics = list(container.query(Static))
        assert len(statics) >= 1


@pytest.mark.asyncio
async def test_render_slash_quit_exits(tmp_path, monkeypatch) -> None:
    """slash_quit event → app.exit() 标记退出."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

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
        await app._render_event(ChatEvent(type="slash_quit", content="bye"))
        await pilot.pause()
        # exit() 调用后 _exit 标记应 True 或 app.return_value 有变化.
        # textual: app._exit_renderables 不 stable; 用 _exit attr 检查
        assert app._exit is True


@pytest.mark.asyncio
async def test_render_unknown_event_dim_fallback(tmp_path, monkeypatch) -> None:
    """未知 event type → dim fallback mount Static (不崩)."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.containers import VerticalScroll
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
        # 一个 Wave 3 未支持的 type
        await app._render_event(ChatEvent(type="some_unknown_type", content="x"))
        await pilot.pause()
        container = app.query_one("#output", VerticalScroll)
        statics = list(container.query(Static))
        assert len(statics) >= 1


@pytest.mark.asyncio
async def test_render_slash_help_renders_text(tmp_path, monkeypatch) -> None:
    """slash_help / slash_show 等普通 text event → mount Static."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.containers import VerticalScroll
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
        await app._render_event(ChatEvent(type="slash_help", content="HELP TEXT"))
        await pilot.pause()
        container = app.query_one("#output", VerticalScroll)
        statics = list(container.query(Static))
        assert len(statics) >= 1


@pytest.mark.asyncio
async def test_render_slash_deepen_promoted_switches_chat(tmp_path, monkeypatch) -> None:
    """Task 15: slash_deepen_promoted event → self.chat 替换为 ChatSession (用 metadata.sid).

    建 session 用 promote_to_persistent + fake bootstrap (跟 test_phase18_full_flow 同 pattern).
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_tui_render_deepen")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent, ChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2
    from explain_engine.schema.nodes import VariableNode

    storage = StorageV2()
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=__import__(
        "unittest.mock", fromlist=["MagicMock"]
    ).MagicMock(text="ok", reasoning=None))

    async def fake_bootstrap(question, llm, **kwargs):
        return [
            VariableNode(
                id="p_001",
                name="x",
                description="d",
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
        ]

    async def fake_review(phenomena, input_provider, console=None):
        return phenomena

    monkeypatch.setattr(
        "explain_engine.chat.ephemeral.bootstrap_phenomena", fake_bootstrap
    )
    monkeypatch.setattr(
        "explain_engine.chat.ephemeral.review_phenomena_async", fake_review
    )

    ephemeral_pre = EphemeralChatSession(storage=storage, llm=llm)
    real_chat = await ephemeral_pre.promote_to_persistent("test Q", llm)
    sid = real_chat.sid
    await real_chat.aclose()

    # 现在 app 用 fresh ephemeral 启动, 模拟 slash_deepen_promoted event → 切到 sid
    ephemeral = EphemeralChatSession(storage=storage, llm=llm)
    app = ExplainChatApp(
        llm=llm,
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        ev = ChatEvent(
            type="slash_deepen_promoted",
            content="深度建模完成",
            metadata={"sid": sid},
        )
        await app._render_event(ev)
        await pilot.pause()
        assert isinstance(app.chat, ChatSession)
        assert app.chat.sid == sid


@pytest.mark.asyncio
async def test_render_slash_deepen_promoted_missing_metadata(tmp_path, monkeypatch) -> None:
    """metadata 缺 sid → slash_error 红色 + 保留 ephemeral."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

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
        # metadata=None
        ev = ChatEvent(type="slash_deepen_promoted", content="x", metadata=None)
        await app._render_event(ev)
        await pilot.pause()
        # 仍是 ephemeral
        assert app.chat is ephemeral


@pytest.mark.asyncio
async def test_render_slash_reset_to_ephemeral(tmp_path, monkeypatch) -> None:
    """Task 15: slash_reset_to_ephemeral → 重建 EphemeralChatSession.

    新建的 chat 实例跟原 ephemeral 不是同一个 (重建).
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

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
        await app._render_event(ChatEvent(type="slash_reset_to_ephemeral", content=None))
        await pilot.pause()
        # 重建 → 新 EphemeralChatSession 实例 (不是同一对象)
        assert isinstance(app.chat, EphemeralChatSession)
        assert app.chat is not ephemeral


@pytest.mark.asyncio
async def test_render_slash_error_renders_text(tmp_path, monkeypatch) -> None:
    """slash_error → mount Static (红色 markup)."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.containers import VerticalScroll
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
        await app._render_event(ChatEvent(type="slash_error", content="some error"))
        await pilot.pause()
        container = app.query_one("#output", VerticalScroll)
        statics = list(container.query(Static))
        assert len(statics) >= 1


@pytest.mark.asyncio
async def test_render_slash_switch_session_switches_chat(tmp_path, monkeypatch) -> None:
    """Phase 19 Wave 3 review I-4: /resume slash 触发 slash_switch_session event
    (content={"sid": str}) → self.chat 替换为 ChatSession.

    注意: 跟 slash_deepen_promoted 不同, sid 在 content (非 metadata).
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_tui_render_switch")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent, ChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2
    from explain_engine.schema.nodes import VariableNode

    storage = StorageV2()
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=__import__(
        "unittest.mock", fromlist=["MagicMock"]
    ).MagicMock(text="ok", reasoning=None))

    async def fake_bootstrap(question, llm, **kwargs):
        return [
            VariableNode(
                id="p_001",
                name="x",
                description="d",
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
        ]

    async def fake_review(phenomena, input_provider, console=None):
        return phenomena

    monkeypatch.setattr(
        "explain_engine.chat.ephemeral.bootstrap_phenomena", fake_bootstrap
    )
    monkeypatch.setattr(
        "explain_engine.chat.ephemeral.review_phenomena_async", fake_review
    )

    # 先 promote 出一个 sid 可切到
    ephemeral_pre = EphemeralChatSession(storage=storage, llm=llm)
    real_chat = await ephemeral_pre.promote_to_persistent("switch target Q", llm)
    sid = real_chat.sid
    await real_chat.aclose()

    # app 用 fresh ephemeral 启动, 模拟 /resume 触发 slash_switch_session
    ephemeral = EphemeralChatSession(storage=storage, llm=llm)
    app = ExplainChatApp(
        llm=llm,
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        ev = ChatEvent(
            type="slash_switch_session",
            content={"sid": sid},
        )
        await app._render_event(ev)
        await pilot.pause()
        assert isinstance(app.chat, ChatSession)
        assert app.chat.sid == sid


@pytest.mark.asyncio
async def test_render_slash_switch_session_missing_sid_keeps_chat(
    tmp_path, monkeypatch
) -> None:
    """Phase 19 Wave 3 review I-4: slash_switch_session content 缺 sid (None / 非 dict /
    没 sid key) → 保留当前 chat + 红字提示, 不切到坏对象.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_tui_render_switch_missing")

    from textual.containers import VerticalScroll
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
        # content=None: 防御应触发, chat 仍是 ephemeral
        await app._render_event(
            ChatEvent(type="slash_switch_session", content=None)
        )
        await pilot.pause()
        assert app.chat is ephemeral

        # content={} (无 sid key): 防御应触发
        await app._render_event(
            ChatEvent(type="slash_switch_session", content={})
        )
        await pilot.pause()
        assert app.chat is ephemeral

        # 验有红字提示 mount 到 #output (至少 1 个 Static)
        container = app.query_one("#output", VerticalScroll)
        statics = list(container.query(Static))
        assert len(statics) >= 1


# ─── Phase 20.3: 流式 delta 增量渲染 + agent 工具调用可见反馈 ───

@pytest.mark.asyncio
async def test_render_assistant_text_delta_accumulates(tmp_path, monkeypatch) -> None:
    """多个 assistant_text_delta → 累积到同一个 Static (增量 update), 非每 delta 一个."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(), light_llm=AsyncMock(), ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        for chunk in ["水", "沸腾", "是因为", "蒸汽压"]:
            await app._render_event(
                ChatEvent(type="assistant_text_delta", content=chunk)
            )
        await pilot.pause()
        # 单一流式 widget 累积全文
        assert app._stream_answer is not None
        assert app._stream_answer_buf == "水沸腾是因为蒸汽压"
        # turn_complete → reset 引用
        await app._render_event(ChatEvent(type="turn_complete", content=None))
        await pilot.pause()
        assert app._stream_answer is None


@pytest.mark.asyncio
async def test_render_thinking_delta_accumulates_in_collapsible(tmp_path, monkeypatch) -> None:
    """thinking_delta → 累积到 Collapsible 内 Static, title 显字数."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import Collapsible

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(), light_llm=AsyncMock(), ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        for chunk in ["让我", "想想"]:
            await app._render_event(ChatEvent(type="thinking_delta", content=chunk))
        await pilot.pause()
        assert app._stream_thinking_buf == "让我想想"
        cols = list(app.query(Collapsible))
        assert len(cols) == 1
        assert "4 字" in cols[0].title


@pytest.mark.asyncio
async def test_render_tool_use_shows_trace_and_spinner(tmp_path, monkeypatch) -> None:
    """tool_use → 持久 trace 行 (含中文 label) + 动画 spinner; tool_result → 撤 spinner."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.containers import VerticalScroll
    from textual.widgets import LoadingIndicator, Static

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.loop import ToolResultEvent, ToolUseEvent
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(
        llm=AsyncMock(), light_llm=AsyncMock(), ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._render_event(
            ToolUseEvent(
                tool_name="counterfactual",
                tool_input={"intervention": "如果温度升高"},
            )
        )
        await pilot.pause()
        container = app.query_one("#output", VerticalScroll)
        texts = [s.renderable for s in container.query(Static)]
        joined = " ".join(str(t) for t in texts)
        # trace 行含中文 label + input 预览
        assert "做反事实分析" in joined
        assert "如果温度升高" in joined
        # spinner mount 中
        assert len(list(app.query(LoadingIndicator))) == 1
        # tool_result → 撤 spinner
        await app._render_event(
            ToolResultEvent(tool_name="counterfactual", result="done")
        )
        await pilot.pause()
        assert len(list(app.query(LoadingIndicator))) == 0
