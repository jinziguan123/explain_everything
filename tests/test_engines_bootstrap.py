"""BootstrapEngine test."""

from unittest.mock import AsyncMock

import pytest

from explain_engine.engines.bootstrap import BootstrapOutput, bootstrap_phenomena
from explain_engine.llm.client import Response


def _mock_resp(phenomena: list[dict]) -> Response:
    return Response(
        text="",
        parsed={"phenomena": phenomena},
        model="mock",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


class TestBootstrapPhenomena:
    async def test_returns_phenomena_with_defaults(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=_mock_resp([
            {"name": "房价上涨", "description": "一线城市房价持续高位"},
            {"name": "收入停滞", "description": "工资5年无明显增长"},
        ]))

        result = await bootstrap_phenomena("why?", llm)

        assert len(result) == 2
        assert result[0].id == "p_001"
        assert result[0].name == "房价上涨"
        assert result[0].abstraction_level == 0
        assert result[0].confidence == 0.7
        assert result[0].epistemic == "observation"
        assert result[0].evidence_ids == []

    async def test_sequential_ids(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=_mock_resp([
            {"name": f"现象 {i}", "description": "x"} for i in range(5)
        ]))

        result = await bootstrap_phenomena("why?", llm)

        assert [n.id for n in result] == ["p_001", "p_002", "p_003", "p_004", "p_005"]

    async def test_truncates_over_max_count(self):
        llm = AsyncMock()
        # LLM 出 20 个，但 max_count=15
        llm.chat = AsyncMock(return_value=_mock_resp([
            {"name": f"现象 {i}", "description": "x"} for i in range(20)
        ]))

        result = await bootstrap_phenomena("why?", llm, max_count=15)

        assert len(result) == 15
        assert result[-1].id == "p_015"

    async def test_raises_when_no_parsed(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=Response(
            text="garbage",
            parsed=None,
            model="mock",
            usage={"input_tokens": 0, "output_tokens": 0},
        ))

        with pytest.raises(ValueError, match="未返回 structured output"):
            await bootstrap_phenomena("why?", llm)

    async def test_passes_schema_to_llm(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=_mock_resp([
            {"name": "x", "description": "y"}
        ]))

        await bootstrap_phenomena("why?", llm)

        kwargs = llm.chat.call_args.kwargs
        # schema 参数应该是 BootstrapOutput
        assert kwargs.get("schema") is BootstrapOutput

    async def test_user_message_contains_question_and_counts(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=_mock_resp([
            {"name": "x", "description": "y"}
        ]))

        await bootstrap_phenomena("为什么年轻人不消费", llm, min_count=10, max_count=12)

        # 第一个 positional arg 是 messages list
        messages = llm.chat.call_args.args[0]
        # 应该有一条 user message 含 question + count
        user_msg = next(m for m in messages if m.role == "user")
        assert "为什么年轻人不消费" in user_msg.content
        assert "10" in user_msg.content
        assert "12" in user_msg.content
