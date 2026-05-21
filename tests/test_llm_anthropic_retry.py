"""Phase 11 Wave 0: anthropic_protocol retry on malformed LLM response.

deepseek-v4-pro reject forced tool_choice → fallback auto → 偶尔返 free text
(parsed=None). chat() 内 retry 2 次, append "respond JSON only" reminder
重 LLM call. 3 次都失败 → SchemaValidationError 含 raw text preview.

Phase 13 hotfix #4: 现走 messages.stream(). mock 改 stream API.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient
from explain_engine.llm.client import Message
from explain_engine.llm.errors import SchemaValidationError


class _DemoSchema(BaseModel):
    answer: str
    confidence: float


def _free_text_resp(text: str = "I don't know how to answer this in JSON.") -> MagicMock:
    """LLM 返 free text (无 tool_use block) — auto fallback 后常见."""
    resp = MagicMock()
    resp.content = [MagicMock(type="text", text=text)]
    resp.model = "deepseek-v4-pro"
    resp.usage = MagicMock(input_tokens=10, output_tokens=20)
    return resp


def _tool_use_resp(tool_input: dict) -> MagicMock:
    resp = MagicMock()
    block = MagicMock()
    block.type = "tool_use"
    block.input = tool_input
    resp.content = [block]
    resp.model = "deepseek-v4-pro"
    resp.usage = MagicMock(input_tokens=10, output_tokens=20)
    return resp


def _stream_manager(message_resp: MagicMock) -> MagicMock:
    """Wrap message_resp in an async-context-manager mock matching anthropic
    SDK's messages.stream() → AsyncMessageStreamManager → get_final_message().
    """
    stream = AsyncMock()
    stream.get_final_message = AsyncMock(return_value=message_resp)
    mgr = MagicMock()
    mgr.__aenter__ = AsyncMock(return_value=stream)
    mgr.__aexit__ = AsyncMock(return_value=None)
    return mgr


@pytest.fixture
def mock_anthropic(mocker):
    mock_client = AsyncMock()
    mocker.patch(
        "explain_engine.llm.anthropic_protocol.AsyncAnthropic",
        return_value=mock_client,
    )
    return mock_client


class TestAnthropicProtocolRetry:
    async def test_retry_on_malformed_response(self, mock_anthropic):
        """LLM 第一次返 free text (no parsed), 第二次 valid tool_use → 成功返 parsed."""
        mock_anthropic.messages.stream = MagicMock(
            side_effect=[
                _stream_manager(_free_text_resp("not json here")),
                _stream_manager(_tool_use_resp({"answer": "yes", "confidence": 0.9})),
            ]
        )
        client = AnthropicProtocolClient(
            api_key="sk-test", default_model="deepseek-v4-pro"
        )
        r = await client.chat(
            [Message(role="user", content="hi")],
            schema=_DemoSchema,
        )
        assert r.parsed == {"answer": "yes", "confidence": 0.9}
        assert mock_anthropic.messages.stream.call_count == 2

        # 第二次调用应 append 了 reminder message
        second_call_kwargs = mock_anthropic.messages.stream.call_args_list[1].kwargs
        msgs = second_call_kwargs["messages"]
        assert len(msgs) >= 2  # 原 + reminder
        last_msg = msgs[-1]
        assert last_msg["role"] == "user"
        assert "JSON" in last_msg["content"] or "json" in last_msg["content"]

    async def test_max_retries_then_raise(self, mock_anthropic):
        """LLM 3 次都 malformed → SchemaValidationError 含 raw text preview."""
        bad_text = "Sorry, I cannot produce structured output for this query."
        mock_anthropic.messages.stream = MagicMock(
            side_effect=lambda **_kw: _stream_manager(_free_text_resp(bad_text))
        )
        client = AnthropicProtocolClient(
            api_key="sk-test", default_model="deepseek-v4-pro"
        )
        with pytest.raises(SchemaValidationError) as exc_info:
            await client.chat(
                [Message(role="user", content="hi")],
                schema=_DemoSchema,
            )
        # 总调用 3 次 (initial + 2 retry)
        assert mock_anthropic.messages.stream.call_count == 3
        # raw text preview 在 error message 里 (诊断用)
        assert "Sorry, I cannot" in str(exc_info.value)

    async def test_retry_on_empty_dict_parsed(self, mock_anthropic):
        """Fix 5 (2026-05-19 smoke): LLM 返 valid tool_use 但 input={} 空 dict
        (字段缺失) → 应 retry, 不直接返让 caller raise.

        Root cause: Wave 0 retry condition `parsed is not None` 漏 cover
        empty dict (truthy=False 但 `is not None`=True 仍走 return path).
        production 撞 deepseek 返 `{"input":{}}` → CompressionOutput.model_validate({})
        ValidationError → SchemaValidationError.
        """
        mock_anthropic.messages.stream = MagicMock(
            side_effect=[
                _stream_manager(_tool_use_resp({})),  # 空 dict 不符 schema
                _stream_manager(_tool_use_resp({"answer": "yes", "confidence": 0.7})),
            ]
        )
        client = AnthropicProtocolClient(
            api_key="sk-test", default_model="deepseek-v4-pro"
        )
        r = await client.chat(
            [Message(role="user", content="hi")],
            schema=_DemoSchema,
        )
        assert r.parsed == {"answer": "yes", "confidence": 0.7}
        # 应 retry: 2 次调用
        assert mock_anthropic.messages.stream.call_count == 2

    async def test_valid_first_time_no_retry(self, mock_anthropic):
        """LLM 第一次就 valid → mock call 1 次, 无 retry."""
        mock_anthropic.messages.stream = MagicMock(
            side_effect=lambda **_kw: _stream_manager(
                _tool_use_resp({"answer": "yes", "confidence": 0.5})
            )
        )
        client = AnthropicProtocolClient(
            api_key="sk-test", default_model="deepseek-v4-pro"
        )
        r = await client.chat(
            [Message(role="user", content="hi")],
            schema=_DemoSchema,
        )
        assert r.parsed == {"answer": "yes", "confidence": 0.5}
        assert mock_anthropic.messages.stream.call_count == 1
