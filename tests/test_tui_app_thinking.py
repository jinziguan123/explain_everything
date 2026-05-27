"""Phase 19 Wave 4: thinking_text → Collapsible widget mount + Ctrl+O toggle.

Task 19 (mount): thinking_text event → mount Collapsible(Static(content))
  - title 含 "thinking (N 字)" — N 是 content 字符数
  - 默 collapsed=not _thinking_visible (启动 visible=True → 默 expand)
  - 容器是 VerticalScroll#output (RichLog → VerticalScroll 激进迁移)

Task 20 (Ctrl+O): action_toggle_thinking 切 _thinking_visible + 现 mount
  Collapsible.collapsed.

Task 22 (端到端): handle_user_input 自动 yield thinking_text → mount + Ctrl+O 切.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_thinking_text_mounts_collapsible(tmp_path, monkeypatch) -> None:
    """Task 19: thinking_text event → mount Collapsible 在 #output 容器内.

    默 _thinking_visible=True → Collapsible.collapsed=False (展开).
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import Collapsible

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
            ChatEvent(type="thinking_text", content="思考过程内容")
        )
        await pilot.pause()
        collapsibles = list(app.query(Collapsible))
        assert len(collapsibles) == 1, (
            f"期望 1 个 Collapsible mount, 实际 {len(collapsibles)}"
        )
        # 默 _thinking_visible=True → collapsed=False
        assert collapsibles[0].collapsed is False


@pytest.mark.asyncio
async def test_thinking_title_shows_char_count(tmp_path, monkeypatch) -> None:
    """Task 19 (D2): Collapsible title 含 'thinking (N 字)' — N 是字符数."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import Collapsible

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
        content = "12345中文测试"  # 9 字符
        await app._render_event(
            ChatEvent(type="thinking_text", content=content)
        )
        await pilot.pause()
        col = app.query_one(Collapsible)
        assert "thinking" in col.title
        assert "9" in col.title
        assert "字" in col.title


@pytest.mark.asyncio
async def test_thinking_mount_when_thinking_invisible_collapsed(
    tmp_path, monkeypatch
) -> None:
    """Task 19 (D3): _thinking_visible=False 时 mount 新 Collapsible 应默 collapsed=True.

    切 visible=False 后, 后续 mount 的 thinking 默 collapse.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import Collapsible

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
        # 手动切到 hidden
        app._thinking_visible = False
        await app._render_event(
            ChatEvent(type="thinking_text", content="hidden")
        )
        await pilot.pause()
        col = app.query_one(Collapsible)
        assert col.collapsed is True


@pytest.mark.asyncio
async def test_output_container_is_vertical_scroll(tmp_path, monkeypatch) -> None:
    """Task 19 (D1, 激进路线): #output 容器是 VerticalScroll (替代 RichLog)."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.containers import VerticalScroll

    from explain_engine.chat.ephemeral import EphemeralChatSession
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
        container = app.query_one("#output", VerticalScroll)
        assert container is not None


@pytest.mark.asyncio
async def test_ctrl_o_toggles_all_collapsibles(tmp_path, monkeypatch) -> None:
    """Task 20: Ctrl+O 切所有现 mount Collapsible.collapsed 同 _thinking_visible 反向."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from textual.widgets import Collapsible

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
            ChatEvent(type="thinking_text", content="思考 1")
        )
        await app._render_event(
            ChatEvent(type="thinking_text", content="思考 2")
        )
        await pilot.pause()
        cols = list(app.query(Collapsible))
        assert len(cols) == 2
        assert all(c.collapsed is False for c in cols)
        # Ctrl+O → 折叠
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert app._thinking_visible is False
        assert all(c.collapsed is True for c in app.query(Collapsible))
        # 再 Ctrl+O → 展开
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert app._thinking_visible is True
        assert all(c.collapsed is False for c in app.query(Collapsible))


@pytest.mark.asyncio
async def test_thinking_content_with_brackets_preserved(
    tmp_path, monkeypatch
) -> None:
    """Wave 4 review C-1 regression: thinking_text content 含 [INST] /
    [0,1] / [Smith, 2020] / 代码 x[0] 等 Rich markup-looking 字符
    不被 markup 解析吃 (LLM reasoning 几乎必碰).

    经 rich.markup.escape 后, content 应原样保留在 Static.renderable
    plain text 内.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_c1_brackets")

    from textual.widgets import Collapsible, Static

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
        content = "see [a, b] and [INST] x[0] cite [Smith, 2020]"
        await app._render_event(
            ChatEvent(type="thinking_text", content=content)
        )
        await pilot.pause()
        col = app.query_one(Collapsible)
        # Collapsible 内含 CollapsibleTitle (是 Static 子类) +
        # Contents container 包 mount 进去的 child Static.
        # query(Static) 拿所有 Static, concat 各 render plain text 一起验
        # — 避免误中 title (title 只含 "thinking (N 字)" 不含 brackets).
        statics = list(col.query(Static))
        assert len(statics) >= 2, (
            f"Collapsible 至少应有 title + content Static, "
            f"实际 {len(statics)}"
        )
        rendered_plains: list[str] = []
        for s in statics:
            r = s.render()
            rendered_plains.append(
                r.plain if hasattr(r, "plain") else str(r)
            )
        combined = "\n".join(rendered_plains)
        # 关键: 各 [...] 字符段都应原样保留 (经 escape, Rich markup 不吃)
        assert "[a, b]" in combined, (
            f"[a, b] 被 markup 吃了: {combined!r}"
        )
        assert "[INST]" in combined, (
            f"[INST] 被 markup 吃了: {combined!r}"
        )
        assert "x[0]" in combined, (
            f"x[0] 被 markup 吃了: {combined!r}"
        )
        assert "[Smith, 2020]" in combined, (
            f"[Smith, 2020] 被 markup 吃了: {combined!r}"
        )


@pytest.mark.asyncio
async def test_mount_new_thinking_after_thinking_off_is_collapsed(
    tmp_path, monkeypatch
) -> None:
    """Wave 4 review I-2: D3 invariant — /thinking off 后, 下次 mount 新
    thinking_text 默 collapse.

    覆 slash 路径 (区别于 test_thinking_mount_when_thinking_invisible_collapsed
    手动 set app._thinking_visible 的私 attr 路径).
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_i2_mount_after_off")

    from textual.widgets import Collapsible

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
        # 模 /thinking off slash 事件
        await app._render_event(
            ChatEvent(
                type="slash_thinking_toggle",
                content="thinking 段已关闭",
                metadata={"visible": False},
            )
        )
        await pilot.pause()
        assert app._thinking_visible is False
        # 现 mount 新 thinking → 应默 collapse
        await app._render_event(
            ChatEvent(type="thinking_text", content="新思考")
        )
        await pilot.pause()
        col = app.query_one(Collapsible)
        assert col.collapsed is True, (
            "/thinking off 后新 mount 的 Collapsible 应默 collapse"
        )


@pytest.mark.asyncio
async def test_thinking_end_to_end_via_handle_user_input(
    tmp_path, monkeypatch
) -> None:
    """Task 22 (端到端): mock LLM 返 reasoning → handle_user_input yield
    thinking_text → tui_app mount Collapsible → Ctrl+O 切.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_tui_e2e_thinking")

    from textual.widgets import Collapsible

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.persistence.storage_v2 import StorageV2

    llm = AsyncMock()
    # 模 LLM Response: 含 reasoning 段, 应触发 thinking_text yield
    llm.chat = AsyncMock(
        return_value=MagicMock(
            text="答案", reasoning="reasoning step content"
        )
    )
    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=llm)
    app = ExplainChatApp(
        llm=llm,
        light_llm=AsyncMock(),
        ephemeral_chat=ephemeral,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        async for ev in ephemeral.handle_user_input("你好", llm):
            await app._render_event(ev)
        await pilot.pause()
        cols = list(app.query(Collapsible))
        assert len(cols) == 1
        assert cols[0].collapsed is False
        # Ctrl+O 切到折叠
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert all(c.collapsed is True for c in app.query(Collapsible))
