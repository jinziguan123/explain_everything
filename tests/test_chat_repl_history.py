"""Phase 16.2 Wave 7 + Wave 9: render_recent_history banner + e2e 集成测试.

design: docs/plans/2026-05-25-repl-history-persistence-design.md §6.1
plan: docs/plans/2026-05-25-repl-history-persistence-plan.md Wave 7 + Wave 9

Wave 7 — 纯函数 render_recent_history(entries, max_n) -> str:
- 旧 → 新排序 (按 ts 升序)
- intervention 截 80 字, llm_turn user/assistant 各截 60 字
- 空 list → BANNER_HISTORY_EMPTY
- error entry summary 透传 (展示失败标记)

Wave 9 — e2e (TestE2EReplHistory): 真 StorageV2 + tmp EXPLAIN_HOME (conftest
autouse isolated_explain_home) + mock 单 engine 函数, 走真 wrapped handler
经 dispatch_slash → 验 repl_history.jsonl 真有 entry + banner 真拼.
"""

from __future__ import annotations

import pytest


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

    def test_render_recent_history_empty_friendly_msg(self) -> None:
        """空 list, 输出含 BANNER_HISTORY_EMPTY 文本."""
        from explain_engine.chat.chat_copy import BANNER_HISTORY_EMPTY
        from explain_engine.chat.history_render import render_recent_history

        out = render_recent_history([], max_n=10)

        assert BANNER_HISTORY_EMPTY in out
        # 不应有 header / footer (空 path 走的是友好提示分支)
        assert "最近" not in out  # header 不出现
        assert "/history" not in out  # footer 不出现

    def test_render_recent_history_unknown_delta(self) -> None:
        """entry summary='(变化未知)' (snapshot 失败 fallback), banner 原样显."""
        from explain_engine.chat.history_render import render_recent_history

        entries = [
            {
                "type": "slash",
                "ts": "2026-05-25T14:11:00",
                "cmd": "rescore",
                "args": [],
                "summary": "(变化未知)",
            }
        ]
        out = render_recent_history(entries, max_n=10)

        # summary 原样透传 (Wave 3.7 snapshot 失败 fallback 文案)
        assert "(变化未知)" in out
        assert "rescore" in out


class TestResumeBannerIntegration:
    """Wave 7 Task 7.9: string-level — render_recent_history 拼出 banner section."""

    def test_resume_banner_includes_recent_history_section(self) -> None:
        """模拟 resume 入口拼 banner: 已恢复 session line + render_recent_history.

        验输出整段含 banner '已恢复 session' line + '最近 N 条操作' section.
        不调真 cli.py (cli 是 typer + click runtime), 只验拼装等价行为.
        """
        from explain_engine.chat.history_render import render_recent_history

        # 模拟 cli._run_chat_repl_async 内的 banner 拼装 (1 行 Loaded + history 段)
        sid = "s_df84e637"
        existing_banner = (
            f"Loaded session {sid}. "
            f"Type /help for commands. /quit to exit. ctrl+o toggle log."
        )
        history = [
            {
                "type": "slash",
                "ts": "2026-05-25T14:08:00",
                "cmd": "compress",
                "args": [],
                "summary": "+4 L1 / +12 边",
            },
            {
                "type": "llm_turn",
                "ts": "2026-05-25T14:14:00",
                "user_input": "JEPA 论文的关键思想?",
                "assistant_text": "JEPA 论文核心是 abstract causal structure...",
            },
        ]
        history_section = render_recent_history(history, max_n=10)
        full_banner = existing_banner + history_section

        # 既有 banner 段保留
        assert sid in full_banner
        assert "Loaded session" in full_banner
        # 新加 history section 内容: header + 2 个 entry + footer
        assert "最近 2 条操作" in full_banner
        assert "compress" in full_banner
        assert "+4 L1" in full_banner
        assert "JEPA 论文的关键思想?" in full_banner
        assert "Claude:" in full_banner
        assert "/history" in full_banner


class TestE2EReplHistory:
    """Wave 9: 端到端 smoke — 真 StorageV2 + tmp EXPLAIN_HOME (autouse fixture)
    + 走真 wrapped handler 经 dispatch_slash, 验整链路 history 持久化 + 回读 + 渲染.

    不 mock storage 也不 mock _wrap_handler — 只 mock 真正贵 (LLM 调) 的 engine
    函数 (prediction_predict). /show /check 走真路径不需 mock.
    """

    @pytest.mark.asyncio
    async def test_e2e_slash_predict_writes_history_visible_on_resume(
        self, monkeypatch
    ) -> None:
        """走真 wrapped /predict (经 _wrap_handler) → repl_history 有 1 entry,
        intervention 完整保留 + summary 含 '+1 L1' / '+5 现象' 等 delta 文案.

        关键链路: dispatch_slash → DEFAULT_COMMANDS[predict] → _wrap_handler
          → snapshot before → _handle_predict (mock predict) → snapshot after
          → _build_history_entry → storage.append_repl_history → load 回 1 entry.
        """
        from dataclasses import dataclass, field

        from explain_engine.chat.session import ChatSession
        from explain_engine.chat.slash_commands import dispatch_slash
        from explain_engine.persistence.storage_v2 import StorageV2
        from explain_engine.schema.nodes import VariableNode
        from tests.test_chat_session import _make_done_session

        _make_done_session("s_e2e90001")
        # ChatSession.__init__ 不允许 llm 为 None 时调 /predict, 传 object() 占位
        # — mock 的 prediction_predict 不会 touch llm 内部.
        chat = ChatSession("s_e2e90001", llm=object())  # type: ignore[arg-type]

        long_intervention = (
            "假设 LeCun JEPA 真正解决了 c_001 结构先验内化深度问题 + "
            "c_004 组合生成边界, 那么世界模型可以从纯像素预测转向抽象 latent 因果 "
            "预测, 从根本避免生成式模型在高维像素空间的瓶颈, 这是一种 fundamentally "
            "不同的归纳偏置策略"
        )

        async def fake_provider(prompt: str) -> str:
            return long_intervention

        chat.input_provider = fake_provider

        @dataclass
        class FakeReport:
            new_node_ids: list = field(default_factory=list)
            predicted_L0_ids: list = field(default_factory=list)
            activated_existing_L0: list = field(default_factory=list)
            propagation_acts: dict = field(default_factory=dict)

        async def fake_predict(state, intervention_text, llm):
            state.graph.add_node(VariableNode(
                id="c_e1", name="t", description="d",
                abstraction_level=1, confidence=0.7, epistemic="insight",
            ))
            for i in range(5):
                state.graph.add_node(VariableNode(
                    id=f"p_e{i}", name=f"p{i}", description="d",
                    abstraction_level=0, confidence=0.7, epistemic="observation",
                ))
            return FakeReport(
                new_node_ids=["c_e1"],
                predicted_L0_ids=[f"p_e{i}" for i in range(5)],
                activated_existing_L0=[],
                propagation_acts={},
            )

        monkeypatch.setattr(
            "explain_engine.engines.prediction.predict", fake_predict
        )

        events = await dispatch_slash(chat, "/predict")
        # handler 应正常返回 (无 stage gate 失败 / 无 _ephemeral_reject)
        assert any(e.type == "slash_predict" for e in events), (
            f"expected slash_predict event, got types: {[e.type for e in events]}"
        )

        storage = StorageV2()  # env-based, 同 chat.storage 看一致 project_id
        history = storage.load_repl_history("s_e2e90001")
        slash_entries = [
            e for e in history
            if e.get("type") == "slash" and e.get("cmd") == "predict"
        ]
        assert len(slash_entries) == 1, (
            f"expected 1 predict entry, got {len(slash_entries)}: {history}"
        )
        entry = slash_entries[0]
        # intervention 应完整保存 (jsonl 不截)
        assert entry["intervention"] == long_intervention
        # summary delta: 加 1 L1 + 5 L0 (无 edge 加, _handle_predict mock 不加)
        assert "+1 L1" in entry["summary"]
        assert "+5 现象" in entry["summary"]
