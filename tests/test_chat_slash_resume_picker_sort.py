"""Phase 20.1 #8: /resume 无 args picker sessions sorted by created_at desc.

设计 doc 3c8edb8 §#8:
- _handle_resume 无 args 路径, SessionStore().list() 返 metas, 现 raw 顺序
  传给 picker — user 期望最近用的在顶 (跟 macOS Finder Recent / Chrome 最近
  标签同语义).
- 修法 (本 commit): _handle_resume 内 sorted(metas, key=lambda m: m.created_at,
  reverse=True), sessions_payload 用 sorted 版本. 即使 SessionStore.list() 现
  也 sort, 本 handler defensive 自己再 sort 一遍 — 解耦 handler 跟 caller 的
  排序契约, 测试 pin 住 picker 的 output contract.

不动:
- 带 sid arg 路径 (line ~1019 第二 SessionStore().list()): 仅 sid 存在验证,
  不排序.
- question >40 char 截断已 done (tui_app.py:135 SessionPickerScreen).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from explain_engine.chat.slash_commands import _handle_resume


@pytest.mark.asyncio
async def test_resume_no_args_picker_metas_sorted_desc():
    """SessionStore.list 返 3 metas 乱序 → handler 排序 by created_at desc."""
    # mock 3 metas (created_at 顺序: oldest, newest, middle)
    mock_meta_1 = MagicMock(session_id="s_old", question="Q1", stage="done",
                            created_at=100.0)
    mock_meta_2 = MagicMock(session_id="s_new", question="Q2", stage="done",
                            created_at=300.0)
    mock_meta_3 = MagicMock(session_id="s_mid", question="Q3", stage="done",
                            created_at=200.0)

    mock_store = MagicMock()
    # 故意乱序传, 即使真实 SessionStore.list() 已 sort, handler 也得 defensive
    # 自己 sort — 解耦 handler 跟 caller sort 契约.
    mock_store.list = MagicMock(
        return_value=[mock_meta_1, mock_meta_2, mock_meta_3]
    )

    mock_chat = MagicMock()
    mock_chat.sid = None

    with patch(
        "explain_engine.persistence.session.SessionStore",
        return_value=mock_store,
    ):
        events = await _handle_resume(mock_chat, [])

    # 应有 1 个 slash_open_session_picker event
    picker_events = [e for e in events if e.type == "slash_open_session_picker"]
    assert len(picker_events) == 1
    ev = picker_events[0]
    sessions = ev.metadata["sessions"]
    # sort by created_at desc → s_new(300) / s_mid(200) / s_old(100)
    assert [s["sid"] for s in sessions] == ["s_new", "s_mid", "s_old"]
