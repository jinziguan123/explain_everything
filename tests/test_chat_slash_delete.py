"""slash /delete handler — Phase 17.2 Task 25-27.

设计参考 docs/plans/2026-05-26-phase-17.2-design.md Feature C.

Note: slash 默认要求 --force (plan Task 25 简化 — 真二次 confirm 在 chat REPL
有 I/O 复杂度, 此 phase 让 user 显式 --force 表示确认).
"""
import pytest

from explain_engine.chat.session import ChatSession
from explain_engine.chat.slash_commands import DEFAULT_COMMANDS, _command_by_name

# Reuse fixture maker
from tests.test_chat_session import _make_done_session


class TestSlashDeleteRegistry:
    def test_delete_command_registered(self):
        """/delete 必须在 DEFAULT_COMMANDS, 且 description 非空中文."""
        names = [c.name for c in DEFAULT_COMMANDS]
        assert "delete" in names
        cmd = _command_by_name("delete")
        assert cmd is not None
        assert cmd.description
        # 含中文
        import re
        assert re.search(r"[一-鿿]", cmd.description)


class TestSlashDeleteHandler:
    @pytest.mark.asyncio
    async def test_no_args_returns_usage_error(self):
        """/delete 不带参数 → slash_error 提示用法."""
        _make_done_session("s_de100001")
        chat = ChatSession("s_de100001")
        cmd = _command_by_name("delete")
        events = await cmd.handler(chat, [])
        assert len(events) >= 1
        assert events[0].type == "slash_error"
        assert "/delete" in events[0].content

    @pytest.mark.asyncio
    async def test_only_force_flag_returns_usage_error(self):
        """/delete --force (没 sid) → slash_error."""
        _make_done_session("s_de100002")
        chat = ChatSession("s_de100002")
        cmd = _command_by_name("delete")
        events = await cmd.handler(chat, ["--force"])
        assert events[0].type == "slash_error"
        assert "/delete" in events[0].content

    @pytest.mark.asyncio
    async def test_without_force_returns_error_with_hint(self):
        """简化版: slash 不带 --force → 拒绝, 提示加 --force.

        (real two-step confirm 需 chat REPL I/O 复杂, plan 简化.)
        """
        _make_done_session("s_de100003")  # current sid
        _make_done_session("s_de100099")  # 目标 sid
        chat = ChatSession("s_de100003")
        cmd = _command_by_name("delete")
        events = await cmd.handler(chat, ["s_de100099"])
        # 应是 slash_error 且 hint 含 --force
        assert events[0].type == "slash_error"
        assert "--force" in events[0].content

    @pytest.mark.asyncio
    async def test_nonexistent_sid_with_force(self):
        """/delete <不存在> --force → err_delete_not_found event."""
        _make_done_session("s_de100004")
        chat = ChatSession("s_de100004")
        cmd = _command_by_name("delete")
        events = await cmd.handler(chat, ["s_notexist", "--force"])
        assert events[0].type == "slash_error"
        assert "不存在" in events[0].content
