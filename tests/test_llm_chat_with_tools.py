"""Phase 9 Wave F.3: chat_with_tools tests for both protocols.

测试 AnthropicProtocolClient + OpenAIProtocolClient 的 chat_with_tools 实装.
覆盖:
- 仅文本响应 → ToolsResponse(text, [], stop_reason)
- 含 tool_use → ToolsResponse(text, [{id,name,input}], "tool_use")
- API 错误包成 LLMError
- OpenAI tools schema 翻译 (Anthropic-style → OpenAI function tools)
- ToolsResponse dataclass 默认值 / 构造

Mock 走 module-level (patch AsyncAnthropic / AsyncOpenAI 整个类), 与既有
test_llm_anthropic_protocol.py / test_llm_openai_protocol.py 一致.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from explain_engine.llm.client import ToolsResponse


@pytest.fixture
def mock_anthropic(mocker):
    mock_client = AsyncMock()
    mocker.patch(
        "explain_engine.llm.anthropic_protocol.AsyncAnthropic",
        return_value=mock_client,
    )
    return mock_client


@pytest.fixture
def mock_openai(mocker):
    mock_client = AsyncMock()
    mocker.patch(
        "explain_engine.llm.openai_protocol.AsyncOpenAI",
        return_value=mock_client,
    )
    return mock_client


class TestAnthropicChatWithTools:
    async def test_response_with_text_only(self, mock_anthropic):
        from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient

        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type="text", text="hello")]
        mock_resp.stop_reason = "end_turn"
        mock_anthropic.messages.create = AsyncMock(return_value=mock_resp)

        client = AnthropicProtocolClient(api_key="k", default_model="claude-test")
        result = await client.chat_with_tools(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
        assert isinstance(result, ToolsResponse)
        assert result.text == "hello"
        assert result.tool_uses == []
        assert result.stop_reason == "end_turn"

    async def test_response_with_tool_use(self, mock_anthropic):
        from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient

        text_block = MagicMock(type="text", text="let me check")
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "tu_001"
        tool_block.name = "check"
        tool_block.input = {"target_id": "c_001"}
        mock_resp = MagicMock()
        mock_resp.content = [text_block, tool_block]
        mock_resp.stop_reason = "tool_use"
        mock_anthropic.messages.create = AsyncMock(return_value=mock_resp)

        client = AnthropicProtocolClient(api_key="k", default_model="claude-test")
        result = await client.chat_with_tools(
            system="sys",
            messages=[{"role": "user", "content": "go"}],
            tools=[{"name": "check", "description": "d", "input_schema": {}}],
        )
        assert result.text == "let me check"
        assert len(result.tool_uses) == 1
        assert result.tool_uses[0]["id"] == "tu_001"
        assert result.tool_uses[0]["name"] == "check"
        assert result.tool_uses[0]["input"] == {"target_id": "c_001"}
        assert result.stop_reason == "tool_use"

    async def test_passes_system_and_tools_to_api(self, mock_anthropic):
        from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient

        mock_resp = MagicMock()
        mock_resp.content = []
        mock_resp.stop_reason = "end_turn"
        mock_anthropic.messages.create = AsyncMock(return_value=mock_resp)

        client = AnthropicProtocolClient(api_key="k", default_model="claude-test")
        tools = [{"name": "t1", "description": "d", "input_schema": {"type": "object"}}]
        await client.chat_with_tools(
            system="sys-prompt-here",
            messages=[{"role": "user", "content": "hi"}],
            tools=tools,
        )
        kwargs = mock_anthropic.messages.create.call_args.kwargs
        assert kwargs["system"] == "sys-prompt-here"
        assert kwargs["tools"] == tools
        assert kwargs["tool_choice"] == {"type": "auto"}
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]

    async def test_api_error_wrapped(self, mock_anthropic):
        from anthropic import APIConnectionError

        from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient
        from explain_engine.llm.errors import LLMError

        mock_request = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            side_effect=APIConnectionError(request=mock_request),
        )

        client = AnthropicProtocolClient(api_key="k", default_model="claude-test")
        with pytest.raises(LLMError):
            await client.chat_with_tools(
                system="s",
                messages=[{"role": "user", "content": "x"}],
                tools=[],
            )


class TestOpenAIChatWithTools:
    async def test_response_with_text_only(self, mock_openai):
        from explain_engine.llm.openai_protocol import OpenAIProtocolClient

        choice = MagicMock()
        choice.message.content = "hello"
        choice.message.tool_calls = None
        choice.finish_reason = "stop"
        mock_resp = MagicMock()
        mock_resp.choices = [choice]
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_resp)

        client = OpenAIProtocolClient(api_key="k", default_model="gpt-test")
        result = await client.chat_with_tools(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
        assert result.text == "hello"
        assert result.tool_uses == []
        assert result.stop_reason == "stop"

    async def test_response_with_tool_use(self, mock_openai):
        from explain_engine.llm.openai_protocol import OpenAIProtocolClient

        tc = MagicMock()
        tc.id = "call_001"
        tc.function.name = "check"
        tc.function.arguments = '{"target_id": "c_001"}'
        choice = MagicMock()
        choice.message.content = "let me check"
        choice.message.tool_calls = [tc]
        choice.finish_reason = "tool_calls"
        mock_resp = MagicMock()
        mock_resp.choices = [choice]
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_resp)

        client = OpenAIProtocolClient(api_key="k", default_model="gpt-test")
        result = await client.chat_with_tools(
            system="sys",
            messages=[{"role": "user", "content": "go"}],
            tools=[
                {"name": "check", "description": "d", "input_schema": {"type": "object"}}
            ],
        )
        assert result.text == "let me check"
        assert len(result.tool_uses) == 1
        assert result.tool_uses[0]["id"] == "call_001"
        assert result.tool_uses[0]["name"] == "check"
        assert result.tool_uses[0]["input"] == {"target_id": "c_001"}
        assert result.stop_reason == "tool_calls"

    async def test_translates_tools_schema_format(self, mock_openai):
        """Anthropic-style tool input_schema → OpenAI function parameters."""
        from explain_engine.llm.openai_protocol import OpenAIProtocolClient

        choice = MagicMock()
        choice.message.content = ""
        choice.message.tool_calls = None
        choice.finish_reason = "stop"
        mock_resp = MagicMock()
        mock_resp.choices = [choice]
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_resp)

        client = OpenAIProtocolClient(api_key="k", default_model="gpt-test")
        await client.chat_with_tools(
            system="sys",
            messages=[{"role": "user", "content": "x"}],
            tools=[
                {"name": "check", "description": "d", "input_schema": {"type": "object"}}
            ],
        )
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert kwargs["tools"][0]["type"] == "function"
        assert kwargs["tools"][0]["function"]["name"] == "check"
        assert kwargs["tools"][0]["function"]["description"] == "d"
        assert kwargs["tools"][0]["function"]["parameters"] == {"type": "object"}
        assert kwargs["tool_choice"] == "auto"
        # system 走 messages[0] (OpenAI 无独立 system 参数)
        assert kwargs["messages"][0] == {"role": "system", "content": "sys"}
        assert kwargs["messages"][1] == {"role": "user", "content": "x"}

    async def test_tool_call_invalid_json_falls_back_empty(self, mock_openai):
        """vendor 偶尔返非法 JSON args; 不抛, 走空 dict."""
        from explain_engine.llm.openai_protocol import OpenAIProtocolClient

        tc = MagicMock()
        tc.id = "call_x"
        tc.function.name = "check"
        tc.function.arguments = "not-valid-json"
        choice = MagicMock()
        choice.message.content = ""
        choice.message.tool_calls = [tc]
        choice.finish_reason = "tool_calls"
        mock_resp = MagicMock()
        mock_resp.choices = [choice]
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_resp)

        client = OpenAIProtocolClient(api_key="k", default_model="gpt-test")
        result = await client.chat_with_tools(
            system="s",
            messages=[{"role": "user", "content": "x"}],
            tools=[
                {"name": "check", "description": "d", "input_schema": {"type": "object"}}
            ],
        )
        assert result.tool_uses[0]["input"] == {}

    async def test_api_error_wrapped(self, mock_openai):
        from openai import APIConnectionError

        from explain_engine.llm.errors import LLMError
        from explain_engine.llm.openai_protocol import OpenAIProtocolClient

        mock_request = MagicMock()
        mock_openai.chat.completions.create = AsyncMock(
            side_effect=APIConnectionError(request=mock_request),
        )

        client = OpenAIProtocolClient(api_key="k", default_model="gpt-test")
        with pytest.raises(LLMError):
            await client.chat_with_tools(
                system="s",
                messages=[{"role": "user", "content": "x"}],
                tools=[],
            )


class TestToolsResponseDataclass:
    def test_defaults(self):
        r = ToolsResponse()
        assert r.text == ""
        assert r.tool_uses == []
        assert r.stop_reason == ""

    def test_construction(self):
        r = ToolsResponse(
            text="hi",
            tool_uses=[{"id": "1", "name": "x", "input": {}}],
            stop_reason="end_turn",
        )
        assert r.text == "hi"
        assert len(r.tool_uses) == 1
        assert r.stop_reason == "end_turn"
