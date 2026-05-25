"""Phase 16.2 Wave 7: render_recent_history banner 渲染单元 + 集成测试.

design: docs/plans/2026-05-25-repl-history-persistence-design.md §6.1
plan: docs/plans/2026-05-25-repl-history-persistence-plan.md Wave 7

测的是纯函数 render_recent_history(entries, max_n) -> str —
- 旧 → 新排序 (按 ts 升序)
- intervention 截 80 字, llm_turn user/assistant 各截 60 字
- 空 list → BANNER_HISTORY_EMPTY
- error entry summary 透传 (展示失败标记)

Wave 9 e2e 在另文件 (类 TestE2EReplHistoryFlow), 本文件仅 string-level.
"""

from __future__ import annotations


class TestRenderRecentHistory:
    """纯函数: list[dict] -> banner string. 不需 ChatSession."""

    def test_render_recent_history_basic_5_entries(self) -> None:
        """5 slash entry: 输出含 '最近 5 条' header + 5 行 + footer."""
        from explain_engine.chat.history_render import render_recent_history

        entries = [
            {
                "type": "slash",
                "ts": f"2026-05-25T14:0{i}:00",
                "cmd": "show",
                "args": [],
                "summary": f"summary-{i}",
            }
            for i in range(5)
        ]
        out = render_recent_history(entries, max_n=10)

        # header 显实际数 (5 不是 max_n)
        assert "最近 5 条操作" in out
        # 5 个 cmd 都出现
        for i in range(5):
            assert f"summary-{i}" in out
        # footer 提示
        assert "/history" in out
        assert "/help" in out

    def test_render_recent_history_max_10_caps_long_list(self) -> None:
        """30 entry, max_n=10: 仅最后 10 (按 ts 排序后取末 10)."""
        from explain_engine.chat.history_render import render_recent_history

        # 30 entry, ts 升序构造 (HH:MM 00-29)
        entries = [
            {
                "type": "slash",
                "ts": f"2026-05-25T14:{i:02d}:00",
                "cmd": "show",
                "args": [],
                "summary": f"entry-{i:02d}",
            }
            for i in range(30)
        ]
        out = render_recent_history(entries, max_n=10)

        # header 显 10 (max_n cap), 非 30
        assert "最近 10 条操作" in out
        # 仅末 10 (entry-20 ~ entry-29) 应在, 前 20 (entry-00 ~ entry-19) 不在
        for i in range(20, 30):
            assert f"entry-{i:02d}" in out
        for i in range(0, 20):
            assert f"entry-{i:02d}" not in out
