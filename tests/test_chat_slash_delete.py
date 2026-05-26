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


class TestSlashDeleteCurrentSession:
    """Phase 17.2 Task 26: 拒绝删自身 (chat.sid == sid)."""

    @pytest.mark.asyncio
    async def test_delete_current_session_rejected_even_with_force(self):
        """chat 在 s_current, /delete s_current --force → err_delete_active,
        session 仍在 (没被删)."""
        from explain_engine.persistence.storage_v2 import StorageV2

        _make_done_session("s_de200001")
        chat = ChatSession("s_de200001")
        cmd = _command_by_name("delete")

        # 跟 chat.sid 相同 → 拒绝
        events = await cmd.handler(chat, ["s_de200001", "--force"])
        assert events[0].type == "slash_error"
        content = events[0].content
        assert "s_de200001" in content
        assert "当前" in content

        # 确认 session_dir 未被删
        assert StorageV2().session_dir("s_de200001").exists()

    @pytest.mark.asyncio
    async def test_delete_current_session_rejected_without_force_too(self):
        """没 --force 时仍优先拒绝自身 (err_delete_active), 不漏 --force hint."""
        _make_done_session("s_de200002")
        chat = ChatSession("s_de200002")
        cmd = _command_by_name("delete")

        events = await cmd.handler(chat, ["s_de200002"])
        # 拒绝逻辑应先于 --force 检查, content 应包含 "当前"
        assert events[0].type == "slash_error"
        assert "当前" in events[0].content


class TestSlashDeleteForceSuccess:
    """Phase 17.2 Task 27: 成功 path — /delete <非当前 sid> --force."""

    @pytest.mark.asyncio
    async def test_delete_other_session_with_force_success(self):
        """chat 在 s_a, /delete s_b --force → slash_delete event + s_b dir 没了."""
        from explain_engine.persistence.storage_v2 import StorageV2

        # 预创 2 个 session
        _make_done_session("s_de300a01")  # current
        _make_done_session("s_de300b02")  # 要删的
        chat = ChatSession("s_de300a01")
        cmd = _command_by_name("delete")

        storage = StorageV2()
        assert storage.session_dir("s_de300a01").exists()
        assert storage.session_dir("s_de300b02").exists()

        events = await cmd.handler(chat, ["s_de300b02", "--force"])
        # 应是 slash_delete event
        assert events[0].type == "slash_delete"
        assert "s_de300b02" in events[0].content
        assert "已删" in events[0].content

        # s_de300b02 dir 被删, s_de300a01 (当前) 保留
        assert not storage.session_dir("s_de300b02").exists()
        assert storage.session_dir("s_de300a01").exists()

    @pytest.mark.asyncio
    async def test_delete_via_dispatch_slash(self):
        """走完整 dispatch_slash 链路 ("/delete s_x --force" 字符串) 应同结果."""
        from explain_engine.chat.slash_commands import dispatch_slash
        from explain_engine.persistence.storage_v2 import StorageV2

        _make_done_session("s_de300a03")  # current
        _make_done_session("s_de300b04")  # target

        chat = ChatSession("s_de300a03")
        events = await dispatch_slash(chat, "/delete s_de300b04 --force")
        assert events[0].type == "slash_delete"
        assert not StorageV2().session_dir("s_de300b04").exists()
