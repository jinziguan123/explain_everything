"""Phase 19 Wave 4 Task 21: /thinking on|off slash command.

/thinking on|off → yield slash_thinking_toggle event with metadata={"visible": bool}
  + 中文 msg. tui_app._render_event 接 → 同 action_toggle_thinking 等价 + 显 msg.

ephemeral / ChatSession 都接 (跟 chat session lifecycle 解耦).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


def test_thinking_registered_in_default_commands():
    """/thinking 注册到 DEFAULT_COMMANDS."""
    from explain_engine.chat.slash_commands import DEFAULT_COMMANDS

    names = {c.name for c in DEFAULT_COMMANDS}
    assert "thinking" in names


def test_thinking_description_in_command_descriptions():
    """COMMAND_DESCRIPTIONS 含 'thinking' key."""
    from explain_engine.chat.chat_copy import COMMAND_DESCRIPTIONS

    assert "thinking" in COMMAND_DESCRIPTIONS
    desc = COMMAND_DESCRIPTIONS["thinking"]
    assert isinstance(desc, str)
    assert len(desc) > 0


def test_thinking_in_help_groups():
    """HELP_GROUPS_ZH 某分组含 'thinking'."""
    from explain_engine.chat.chat_copy import HELP_GROUPS_ZH

    all_names = [name for _, names in HELP_GROUPS_ZH for name in names]
    assert "thinking" in all_names


@pytest.mark.asyncio
async def test_thinking_on(tmp_path, monkeypatch) -> None:
    """/thinking on → yield slash_thinking_toggle metadata.visible=True + 中文 msg."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_slash_thinking_on")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.slash_commands import dispatch_slash
    from explain_engine.persistence.storage_v2 import StorageV2

    chat = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    events = await dispatch_slash(chat, "/thinking on")
    toggle = [e for e in events if e.type == "slash_thinking_toggle"]
    assert len(toggle) == 1
    assert toggle[0].metadata == {"visible": True}
    # 含中文 "开启" / "已开启" 等 + content 是 str
    msgs = [e for e in events if isinstance(e.content, str) and "开启" in e.content]
    assert len(msgs) >= 1


@pytest.mark.asyncio
async def test_thinking_off(tmp_path, monkeypatch) -> None:
    """/thinking off → yield slash_thinking_toggle metadata.visible=False + 中文 msg."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_slash_thinking_off")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.slash_commands import dispatch_slash
    from explain_engine.persistence.storage_v2 import StorageV2

    chat = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    events = await dispatch_slash(chat, "/thinking off")
    toggle = [e for e in events if e.type == "slash_thinking_toggle"]
    assert len(toggle) == 1
    assert toggle[0].metadata == {"visible": False}
    # 含中文 "关闭"
    msgs = [e for e in events if isinstance(e.content, str) and "关闭" in e.content]
    assert len(msgs) >= 1


@pytest.mark.asyncio
async def test_thinking_no_args_or_bad_args(tmp_path, monkeypatch) -> None:
    """/thinking 无参 / 错参 → slash_error 用法提示."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_slash_thinking_bad")

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.slash_commands import dispatch_slash
    from explain_engine.persistence.storage_v2 import StorageV2

    chat = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    # 无参
    events = await dispatch_slash(chat, "/thinking")
    errs = [e for e in events if e.type == "slash_error"]
    assert len(errs) >= 1
    # 错参 "bad"
    events_bad = await dispatch_slash(chat, "/thinking bad")
    errs_bad = [e for e in events_bad if e.type == "slash_error"]
    assert len(errs_bad) >= 1


@pytest.mark.asyncio
async def test_tui_app_handles_slash_thinking_toggle_event(
    tmp_path, monkeypatch
) -> None:
    """Task 21: tui_app._render_event 接 slash_thinking_toggle → 切 _thinking_visible
    + 同步现 mount Collapsible.collapsed.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_tui_slash_thinking")

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
        # 先 mount 2 个 Collapsible (默 expand)
        await app._render_event(ChatEvent(type="thinking_text", content="思考 1"))
        await app._render_event(ChatEvent(type="thinking_text", content="思考 2"))
        await pilot.pause()
        assert all(c.collapsed is False for c in app.query(Collapsible))

        # 模拟 /thinking off → slash_thinking_toggle visible=False
        await app._render_event(
            ChatEvent(
                type="slash_thinking_toggle",
                content="thinking 折叠已开启",
                metadata={"visible": False},
            )
        )
        await pilot.pause()
        assert app._thinking_visible is False
        assert all(c.collapsed is True for c in app.query(Collapsible))

        # /thinking on → 切回
        await app._render_event(
            ChatEvent(
                type="slash_thinking_toggle",
                content="thinking 已开启",
                metadata={"visible": True},
            )
        )
        await pilot.pause()
        assert app._thinking_visible is True
        assert all(c.collapsed is False for c in app.query(Collapsible))


@pytest.mark.asyncio
async def test_tui_app_slash_thinking_toggle_missing_metadata(
    tmp_path, monkeypatch
) -> None:
    """Task 21 防御: slash_thinking_toggle 缺 metadata.visible (bool) → 保留
    当前 _thinking_visible 状态 + 红色错误 echo.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_tui_thinking_missing_meta")

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
        prev = app._thinking_visible
        # metadata 为 None
        await app._render_event(
            ChatEvent(type="slash_thinking_toggle", content="", metadata=None)
        )
        await pilot.pause()
        assert app._thinking_visible is prev, "缺 metadata 时应保留状态"

        # metadata 有但 visible 缺
        await app._render_event(
            ChatEvent(
                type="slash_thinking_toggle",
                content="",
                metadata={"other": True},
            )
        )
        await pilot.pause()
        assert app._thinking_visible is prev

        # metadata.visible 类型错 (str 而非 bool)
        await app._render_event(
            ChatEvent(
                type="slash_thinking_toggle",
                content="",
                metadata={"visible": "on"},
            )
        )
        await pilot.pause()
        assert app._thinking_visible is prev
