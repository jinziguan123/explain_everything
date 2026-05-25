"""Phase 16: bootstrap_phenomena 加 theories 参数, prompt inject theory section."""

import pytest

from explain_engine.engines.theory.theory import Theory


def _make_theory(id="t1", summary="不确定性 → 风险规避 → 行为退缩",
                 supporting=("s_1", "s_2", "s_3"), predictive=0.75):
    return Theory(
        id=id, motif_type="chain",
        theme_ids=("th_001",), node_ids=("v_a", "v_b"),
        edges=(("v_a", "v_b", "causes"),),
        supporting_sessions=supporting,
        natural_language_summary=summary,
        structure_complexity=3,
        first_seen_session=supporting[0], last_seen_session=supporting[-1],
        predictive_power=predictive,
        stability_status="stable",
    )


class FakeLLM:
    """Mock LLM, 记录 messages 让 test 验 prompt."""
    def __init__(self):
        self.captured_messages = None

    async def chat(self, messages, schema=None):
        self.captured_messages = messages
        # 返 minimal valid BootstrapOutput
        class FakeResp:
            def __init__(self):
                self.parsed = {"phenomena": [
                    {"name": "fake_a", "description": "desc a"},
                ]}
        return FakeResp()


class TestBootstrapPhenomenaTheoryInject:
    @pytest.mark.asyncio
    async def test_theories_none_keeps_old_prompt(self):
        """theories=None → prompt 不含 'theory' 段 (backward compat)."""
        from explain_engine.engines.bootstrap import bootstrap_phenomena
        llm = FakeLLM()
        await bootstrap_phenomena(
            question="为什么 X", llm=llm,
            theories=None,  # ★ 新参数
        )
        user_msg = llm.captured_messages[1].content
        # 不应含 "已发现" / theory 相关引导
        assert "已发现" not in user_msg
        assert "theory" not in user_msg.lower() or "因果模式" not in user_msg

    @pytest.mark.asyncio
    async def test_theories_non_empty_adds_segment(self):
        """theories 非空 → user prompt 加 theory section."""
        from explain_engine.engines.bootstrap import bootstrap_phenomena
        llm = FakeLLM()
        theories = [
            _make_theory(id="t1", summary="不确定性 → 风险规避 → 行为退缩"),
            _make_theory(id="t2", summary="不确定性 → 生活退缩"),
        ]
        await bootstrap_phenomena(
            question="为什么年轻人不结婚", llm=llm,
            theories=theories,
        )
        user_msg = llm.captured_messages[1].content
        # 应含 theory section + 至少 1 个 summary
        assert "已发现" in user_msg or "因果模式" in user_msg
        assert "不确定性 → 风险规避 → 行为退缩" in user_msg
        # 应含 "仅供参考" 软引导防过拟合
        assert "参考" in user_msg or "不必" in user_msg

    @pytest.mark.asyncio
    async def test_top_5_theories_cap(self):
        """超过 5 theory 只取 top-5 防 prompt 爆."""
        from explain_engine.engines.bootstrap import bootstrap_phenomena
        llm = FakeLLM()
        theories = [_make_theory(id=f"t{i}", summary=f"theory {i}") for i in range(10)]
        await bootstrap_phenomena(
            question="X", llm=llm, theories=theories,
        )
        user_msg = llm.captured_messages[1].content
        # 前 5 个 summary 在, 第 6+ 不在
        assert "theory 0" in user_msg
        assert "theory 4" in user_msg
        # theory 5+ 不应在 (top-5 cap)
        assert "theory 9" not in user_msg
