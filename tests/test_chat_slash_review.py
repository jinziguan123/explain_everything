"""Phase X2: /review slash 命令 tests.

- ephemeral → reject
- input_provider=None (TUI/test) → 只读列表 event + 提示
- input_provider 有 → 交互编辑/删除/新增, 持久化, source=user
FakeLLM 模式 — 不触网不触 PG。
"""

from __future__ import annotations

import pytest

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.chat.session import ChatSession
from explain_engine.chat.slash_commands import dispatch_slash
from explain_engine.persistence.storage_v2 import StorageV2
from tests.test_chat_session import _make_done_session


def _make_provider(answers: list[str]):
    it = iter(answers)

    async def _provider(prompt: str) -> str:
        return next(it)

    return _provider


class TestSlashReview:
    @pytest.mark.asyncio
    async def test_ephemeral_rejected(self):
        eph = EphemeralChatSession(storage=StorageV2())
        events = await dispatch_slash(eph, "/review")
        assert events[0].type == "slash_error"
        assert "review" in events[0].content

    @pytest.mark.asyncio
    async def test_readonly_when_no_input_provider(self):
        """input_provider None → slash_review event 含列表 + 只读提示."""
        _make_done_session("s_aabb0001")
        chat = ChatSession("s_aabb0001")  # input_provider 默认 None
        events = await dispatch_slash(chat, "/review")
        assert events[0].type == "slash_review"
        content = events[0].content
        assert "现象 (L0):" in content
        assert "无输入通道" in content  # 只读提示

    @pytest.mark.asyncio
    async def test_interactive_edit_persists_source_user(self):
        """编号编辑 → source=user, 改动持久化 (reload 仍在)."""
        _make_done_session("s_aabb0002")
        chat = ChatSession("s_aabb0002")
        # 列表: 1=p_001 (L0), 2=c_001 (L1). 编辑编号 1 → 新名/新描述 → q
        chat.input_provider = _make_provider(["1", "改后名称", "改后描述", "q"])
        events = await dispatch_slash(chat, "/review")
        assert events[0].type == "slash_review"

        node = chat.state.graph.nodes["p_001"]
        assert node.name == "改后名称"
        assert node.source == "user"

        # 持久化校验 — reload
        reloaded = ChatSession("s_aabb0002")
        assert reloaded.state.graph.nodes["p_001"].name == "改后名称"
        assert reloaded.state.graph.nodes["p_001"].source == "user"

    @pytest.mark.asyncio
    async def test_interactive_add_phenomenon(self):
        _make_done_session("s_aabb0003")
        chat = ChatSession("s_aabb0003")
        chat.input_provider = _make_provider(["a", "新现象X", "新现象描述", "q"])
        events = await dispatch_slash(chat, "/review")
        assert events[0].type == "slash_review"
        # 新增 L0, source=user, 持久化
        reloaded = ChatSession("s_aabb0003")
        new_l0 = [
            n for n in reloaded.state.graph.nodes.values()
            if n.abstraction_level == 0 and n.name == "新现象X"
        ]
        assert len(new_l0) == 1
        assert new_l0[0].source == "user"

    @pytest.mark.asyncio
    async def test_no_change_when_quit(self):
        _make_done_session("s_aabb0004")
        chat = ChatSession("s_aabb0004")
        chat.input_provider = _make_provider(["q"])
        events = await dispatch_slash(chat, "/review")
        assert events[0].type == "slash_review"
        assert "未做改动" in events[0].content
