"""AnthropicProtocolClient 单测（mock anthropic SDK）。

Phase 5: 旧 ClaudeClient 改名 + 加 base_url 参数 (跨 vendor: Anthropic 官方 /
DeepSeek anthropic / Bedrock 等)。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient
from explain_engine.llm.client import Message


class _DemoSchema(BaseModel):
    answer: str
    confidence: float


@pytest.fixture
def mock_anthropic(mocker):
    mock_client = AsyncMock()
    mocker.patch(
        "explain_engine.llm.anthropic_protocol.AsyncAnthropic",
        return_value=mock_client,
    )
    return mock_client


def _mock_message_response(text: str, model: str = "claude-opus-4-7"):
    resp = MagicMock()
    resp.content = [MagicMock(type="text", text=text)]
    resp.model = model
    resp.usage = MagicMock(input_tokens=10, output_tokens=20)
    return resp


def _mock_tool_use_response(tool_input: dict, model: str = "claude-opus-4-7"):
    resp = MagicMock()
    block = MagicMock()
    block.type = "tool_use"
    block.input = tool_input
    resp.content = [block]
    resp.model = model
    resp.usage = MagicMock(input_tokens=10, output_tokens=20)
    return resp


class TestAnthropicProtocolClient:
    async def test_chat_text_response(self, mock_anthropic):
        mock_anthropic.messages.create = AsyncMock(
            return_value=_mock_message_response("hello world")
        )
        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-opus-4-7")
        r = await client.chat([Message(role="user", content="hi")])
        assert r.text == "hello world"
        assert r.parsed is None
        assert r.usage == {"input_tokens": 10, "output_tokens": 20}

    async def test_chat_with_schema_uses_tools(self, mock_anthropic):
        mock_anthropic.messages.create = AsyncMock(
            return_value=_mock_tool_use_response({"answer": "yes", "confidence": 0.9})
        )
        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-opus-4-7")
        r = await client.chat(
            [Message(role="user", content="hi")],
            schema=_DemoSchema,
        )
        assert r.parsed == {"answer": "yes", "confidence": 0.9}
        call_kwargs = mock_anthropic.messages.create.call_args.kwargs
        assert "tools" in call_kwargs
        assert call_kwargs["tools"][0]["name"] == "_DemoSchema"
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "_DemoSchema"}

    async def test_system_message_extracted(self, mock_anthropic):
        mock_anthropic.messages.create = AsyncMock(
            return_value=_mock_message_response("ok")
        )
        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-opus-4-7")
        await client.chat(
            [
                Message(role="system", content="you are helpful"),
                Message(role="user", content="hi"),
            ]
        )
        kwargs = mock_anthropic.messages.create.call_args.kwargs
        assert kwargs["system"] == "you are helpful"
        assert all(m["role"] != "system" for m in kwargs["messages"])

    async def test_default_model_used(self, mock_anthropic):
        mock_anthropic.messages.create = AsyncMock(
            return_value=_mock_message_response("ok")
        )
        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-haiku")
        await client.chat([Message(role="user", content="hi")])
        kwargs = mock_anthropic.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-haiku"

    async def test_model_override(self, mock_anthropic):
        mock_anthropic.messages.create = AsyncMock(
            return_value=_mock_message_response("ok")
        )
        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-opus-4-7")
        await client.chat([Message(role="user", content="hi")], model="claude-haiku")
        kwargs = mock_anthropic.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-haiku"

    async def test_construct_with_default_base_url(self, mock_anthropic) -> None:
        """没传 base_url 时用 anthropic SDK 默认 base_url（不报错）。"""
        c = AnthropicProtocolClient(
            api_key="sk-ant-fake",
            default_model="claude-opus-4-7",
        )
        assert c._default_model == "claude-opus-4-7"

    async def test_construct_with_explicit_base_url(self, mocker) -> None:
        """传 base_url 时透传给 anthropic SDK。"""
        spy = mocker.patch(
            "explain_engine.llm.anthropic_protocol.AsyncAnthropic",
            return_value=AsyncMock(),
        )
        AnthropicProtocolClient(
            api_key="sk-fake",
            default_model="deepseek-chat",
            base_url="https://api.deepseek.com/anthropic",
        )
        spy.assert_called_once_with(
            api_key="sk-fake",
            base_url="https://api.deepseek.com/anthropic",
        )
