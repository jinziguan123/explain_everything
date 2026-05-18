"""BootstrapEngine test."""

from unittest.mock import AsyncMock

import pytest

from explain_engine.engines.bootstrap import BootstrapOutput, bootstrap_phenomena
from explain_engine.llm.client import Response
from explain_engine.llm.errors import SchemaValidationError


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

        with pytest.raises(SchemaValidationError, match="未返回 structured output"):
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


def _make_lex_var(
    global_id: str = "v_abc12345",
    name: str = "长期不确定性",
    description: str = "宏观持续低预期",
    abstraction_level: int = 2,
    reuse_count: int = 3,
    avg_essentialness: float = 0.8,
    canonical_mechanism: str = "通常 cause 风险规避; 由历史路径 cause",
) -> dict:
    """Wave 3: lexicon var entry, 同 knowledge/variables.json 行存."""
    return {
        "global_id": global_id,
        "name": name,
        "description": description,
        "abstraction_level": abstraction_level,
        "epistemic": "insight",
        "fitness": {
            "reuse_count": reuse_count,
            "avg_essentialness": avg_essentialness,
            "avg_consistency": 0.7,
            "first_seen_at": "2026-05-01T00:00:00Z",
            "last_seen_at": "2026-05-18T00:00:00Z",
        },
        "canonical_mechanism": canonical_mechanism,
        "source_sessions": ["s_001"],
    }


class TestBootstrapWithLexicon:
    """Phase 10 Wave 3: bootstrap_phenomena 接 lexicon prior."""

    async def test_lexicon_none_backward_compat(self):
        """lexicon=None → prompt 不含 prior section (backward compat)."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=_mock_resp([
            {"name": "x", "description": "y"}
        ]))

        await bootstrap_phenomena("why?", llm, lexicon=None)

        messages = llm.chat.call_args.args[0]
        prompt_text = "\n".join(m.content for m in messages)
        # Wave 3 prior section 含此字符串作 disclaimer
        assert "不强制" not in prompt_text
        assert "仅供参考" not in prompt_text

    async def test_lexicon_empty_list_same_as_none(self):
        """lexicon=[] 行为同 None — 不加 prior section."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=_mock_resp([
            {"name": "x", "description": "y"}
        ]))

        await bootstrap_phenomena("why?", llm, lexicon=[])

        messages = llm.chat.call_args.args[0]
        prompt_text = "\n".join(m.content for m in messages)
        assert "不强制" not in prompt_text
        assert "仅供参考" not in prompt_text

    async def test_lexicon_attached_to_prompt(self):
        """非空 lexicon → prompt 末尾含 prior section, var name + global_id 可见."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=_mock_resp([
            {"name": "x", "description": "y"}
        ]))

        lex = [
            _make_lex_var(
                global_id="v_abc12345",
                name="长期不确定性",
                reuse_count=5,
                avg_essentialness=0.9,
            ),
            _make_lex_var(
                global_id="v_def67890",
                name="风险规避",
                reuse_count=3,
                avg_essentialness=0.7,
            ),
        ]
        await bootstrap_phenomena("why?", llm, lexicon=lex)

        messages = llm.chat.call_args.args[0]
        prompt_text = "\n".join(m.content for m in messages)
        # var name + global_id 应在 prompt 中
        assert "长期不确定性" in prompt_text
        assert "v_abc12345" in prompt_text
        assert "风险规避" in prompt_text
        assert "v_def67890" in prompt_text
        # disclaimer 应在
        assert "不强制" in prompt_text or "仅供参考" in prompt_text
