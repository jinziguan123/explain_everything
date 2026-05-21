"""LLMError / SchemaValidationError + provider wrap 测试。

Phase 5: 旧 3 client → 2 (AnthropicProtocol / OpenAIProtocol)。
DeepSeek 是 OpenAIProtocolClient(mode='json_object') 的 vendor，wrap 行为
跟 OpenAI mode 一致，所以这里覆盖 anthropic + openai 两个协议即可。
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from explain_engine.llm.client import Message
from explain_engine.llm.errors import LLMError, SchemaValidationError


class TestErrorTypes:
    def test_llm_error_is_exception(self) -> None:
        assert issubclass(LLMError, Exception)

    def test_schema_validation_error_is_exception(self) -> None:
        assert issubclass(SchemaValidationError, Exception)

    def test_llm_error_message(self) -> None:
        err = LLMError("network timeout")
        assert str(err) == "network timeout"

    def test_schema_validation_error_message(self) -> None:
        err = SchemaValidationError("missing field 'name'")
        assert "missing field" in str(err)


def _make_request() -> httpx.Request:
    return httpx.Request("POST", "https://example.test/v1/messages")


class TestAnthropicProtocolWrap:
    """AnthropicProtocolClient 把 anthropic SDK 异常 wrap 成 LLMError。"""

    async def test_wraps_api_error(self, mocker) -> None:
        from anthropic import APIError

        from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.anthropic_protocol.AsyncAnthropic",
            return_value=mock_client,
        )

        # Phase 13 hotfix #4: stream() raises sync if vendor rejects; mock as MagicMock.
        mock_client.messages.stream = MagicMock(
            side_effect=APIError(
                message="network down",
                request=_make_request(),
                body=None,
            )
        )

        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-test")
        with pytest.raises(LLMError, match="network down"):
            await client.chat([Message(role="user", content="hi")])

    async def test_wraps_connection_error(self, mocker) -> None:
        from anthropic import APIConnectionError

        from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.anthropic_protocol.AsyncAnthropic",
            return_value=mock_client,
        )

        mock_client.messages.stream = MagicMock(
            side_effect=APIConnectionError(request=_make_request())
        )

        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-test")
        with pytest.raises(LLMError):
            await client.chat([Message(role="user", content="hi")])


class TestOpenAIProtocolWrap:
    async def test_wraps_api_error(self, mocker) -> None:
        import openai

        from explain_engine.llm.openai_protocol import OpenAIProtocolClient

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.openai_protocol.AsyncOpenAI",
            return_value=mock_client,
        )

        mock_client.chat.completions.create = AsyncMock(
            side_effect=openai.APIError(
                message="quota exceeded",
                request=_make_request(),
                body=None,
            )
        )

        client = OpenAIProtocolClient(api_key="sk-test", default_model="gpt-test")
        with pytest.raises(LLMError, match="quota exceeded"):
            await client.chat([Message(role="user", content="hi")])

    async def test_wraps_json_decode_error_json_schema(self, mocker) -> None:
        """structured output 返回非法 JSON → LLMError (parse-layer)。"""
        from pydantic import BaseModel

        from explain_engine.llm.openai_protocol import OpenAIProtocolClient

        class _Schema(BaseModel):
            answer: str

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.openai_protocol.AsyncOpenAI",
            return_value=mock_client,
        )

        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "not a json"
        resp.model = "gpt-test"
        resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)

        mock_client.chat.completions.create = AsyncMock(return_value=resp)

        client = OpenAIProtocolClient(api_key="sk-test", default_model="gpt-test")
        with pytest.raises(LLMError):
            await client.chat(
                [Message(role="user", content="hi")],
                schema=_Schema,
            )

    async def test_wraps_json_decode_error_json_object_mode(self, mocker) -> None:
        """json_object mode (DeepSeek 等) 同样 wrap JSONDecodeError。"""
        from pydantic import BaseModel

        from explain_engine.llm.openai_protocol import OpenAIProtocolClient

        class _Schema(BaseModel):
            answer: str

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.openai_protocol.AsyncOpenAI",
            return_value=mock_client,
        )

        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "not json {{{"
        resp.model = "deepseek-test"
        resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)

        mock_client.chat.completions.create = AsyncMock(return_value=resp)

        client = OpenAIProtocolClient(
            api_key="sk-test",
            default_model="deepseek-test",
            base_url="https://api.deepseek.test",
            mode="json_object",
        )
        with pytest.raises(LLMError):
            await client.chat(
                [Message(role="user", content="hi")],
                schema=_Schema,
            )

    async def test_deepseek_connection_error_wrapped(self, mocker) -> None:
        """openai SDK + base_url=deepseek 的 connection error wrap。"""
        import openai

        from explain_engine.llm.openai_protocol import OpenAIProtocolClient

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.openai_protocol.AsyncOpenAI",
            return_value=mock_client,
        )

        mock_client.chat.completions.create = AsyncMock(
            side_effect=openai.APIConnectionError(request=_make_request())
        )

        client = OpenAIProtocolClient(
            api_key="sk-test",
            default_model="deepseek-test",
            base_url="https://api.deepseek.test",
            mode="json_object",
        )
        with pytest.raises(LLMError):
            await client.chat([Message(role="user", content="hi")])


class TestExceptionChainPreserved:
    """raise X from exc 保留 __cause__。"""

    async def test_anthropic_chain_preserved(self, mocker) -> None:
        from anthropic import APIError

        from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.anthropic_protocol.AsyncAnthropic",
            return_value=mock_client,
        )

        original = APIError(
            message="boom",
            request=_make_request(),
            body=None,
        )
        mock_client.messages.stream = MagicMock(side_effect=original)

        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-test")
        try:
            await client.chat([Message(role="user", content="hi")])
        except LLMError as wrapped:
            assert wrapped.__cause__ is original
        else:
            pytest.fail("expected LLMError")
