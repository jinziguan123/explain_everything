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

    def test_render_recent_history_old_to_new_order(self) -> None:
        """打乱时序 list 输入, 输出按 ts 升序 (旧 → 新)."""
        from explain_engine.chat.history_render import render_recent_history

        # 故意逆序 + 中间穿插
        entries = [
            {"type": "slash", "ts": "2026-05-25T14:30:00", "cmd": "save",
             "args": [], "summary": "third"},
            {"type": "slash", "ts": "2026-05-25T14:08:00", "cmd": "compress",
             "args": [], "summary": "first"},
            {"type": "slash", "ts": "2026-05-25T14:20:00", "cmd": "predict",
             "args": [], "summary": "second"},
        ]
        out = render_recent_history(entries, max_n=10)

        # 检查在输出中的相对顺序: first 在 second 前, second 在 third 前
        idx_first = out.find("first")
        idx_second = out.find("second")
        idx_third = out.find("third")
        assert idx_first != -1 and idx_second != -1 and idx_third != -1
        assert idx_first < idx_second < idx_third, (
            f"expected 旧→新, got first={idx_first} second={idx_second} "
            f"third={idx_third}"
        )

    def test_render_recent_history_intervention_truncated_80(self) -> None:
        """intervention 500 字, banner 第二行截 80 字 + '...', 不含原 100~500 字段."""
        from explain_engine.chat.history_render import render_recent_history

        long_iv = "A" * 500
        entries = [
            {
                "type": "slash",
                "ts": "2026-05-25T14:20:00",
                "cmd": "predict",
                "args": [],
                "summary": "+1 L1",
                "intervention": long_iv,
            }
        ]
        out = render_recent_history(entries, max_n=10)

        # 首 80 字应在 (开头连续 A)
        assert "A" * 80 in out
        # 但 500 字全文不应在 (verify 截断)
        assert long_iv not in out
        # 应有 '假设:' prefix + '...' 尾
        assert "假设:" in out
        assert "..." in out

    def test_render_recent_history_llm_turn_truncated_60(self) -> None:
        """llm_turn user_input/assistant_text 各 200 字, banner 各截 60 字 + '...'."""
        from explain_engine.chat.history_render import render_recent_history

        long_user = "U" * 200
        long_asst = "C" * 200
        entries = [
            {
                "type": "llm_turn",
                "ts": "2026-05-25T14:14:00",
                "user_input": long_user,
                "assistant_text": long_asst,
            }
        ]
        out = render_recent_history(entries, max_n=10)

        # user_input 截 60 字: 60 个 U 应在, 200 个 U 不在
        assert "U" * 60 in out
        assert long_user not in out
        # assistant_text 截 60 字: 60 个 C 应在, 200 个 C 不在
        assert "C" * 60 in out
        assert long_asst not in out
        # 含 '你:' + 'Claude:' prefix
        assert "你:" in out
        assert "Claude:" in out
        # 截断标记
        assert "..." in out

    def test_render_recent_history_error_entry_marker(self) -> None:
        """error entry: summary='(执行失败: LLMError)' + error 字段, banner 显失败 marker."""
        from explain_engine.chat.history_render import render_recent_history

        entries = [
            {
                "type": "slash",
                "ts": "2026-05-25T14:30:00",
                "cmd": "predict",
                "args": [],
                "summary": "(执行失败: LLMError)",
                "error": "LLMError: rate limit",
            }
        ]
        out = render_recent_history(entries, max_n=10)

        # summary 透传 — 用户重启后 banner 能看到失败比缺记录有价值
        assert "(执行失败: LLMError)" in out
        # cmd 仍在
        assert "predict" in out
