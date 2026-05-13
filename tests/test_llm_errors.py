"""LLMError / SchemaValidationError + provider wrap 测试。"""

from unittest.mock import AsyncMock

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


class TestClaudeWrap:
    """ClaudeClient 把 anthropic SDK 异常 wrap 成 LLMError。"""

    async def test_claude_wraps_api_error(self, mocker) -> None:
        from anthropic import APIError

        from explain_engine.llm.claude import ClaudeClient

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.claude.AsyncAnthropic",
            return_value=mock_client,
        )

        mock_client.messages.create = AsyncMock(
            side_effect=APIError(
                message="network down",
                request=_make_request(),
                body=None,
            )
        )

        client = ClaudeClient(api_key="sk-test", default_model="claude-test")
        with pytest.raises(LLMError, match="network down"):
            await client.chat([Message(role="user", content="hi")])

    async def test_claude_wraps_connection_error(self, mocker) -> None:
        from anthropic import APIConnectionError

        from explain_engine.llm.claude import ClaudeClient

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.claude.AsyncAnthropic",
            return_value=mock_client,
        )

        mock_client.messages.create = AsyncMock(
            side_effect=APIConnectionError(request=_make_request())
        )

        client = ClaudeClient(api_key="sk-test", default_model="claude-test")
        with pytest.raises(LLMError):
            await client.chat([Message(role="user", content="hi")])


class TestOpenAIWrap:
    async def test_openai_wraps_api_error(self, mocker) -> None:
        import openai

        from explain_engine.llm.openai_client import OpenAIClient

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.openai_client.AsyncOpenAI",
            return_value=mock_client,
        )

        mock_client.chat.completions.create = AsyncMock(
            side_effect=openai.APIError(
                message="quota exceeded",
                request=_make_request(),
                body=None,
            )
        )

        client = OpenAIClient(api_key="sk-test", default_model="gpt-test")
        with pytest.raises(LLMError, match="quota exceeded"):
            await client.chat([Message(role="user", content="hi")])

    async def test_openai_wraps_json_decode_error(self, mocker) -> None:
        """structured output 返回非法 JSON → LLMError (parse-layer)。"""
        from pydantic import BaseModel

        from explain_engine.llm.openai_client import OpenAIClient

        class _Schema(BaseModel):
            answer: str

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.openai_client.AsyncOpenAI",
            return_value=mock_client,
        )

        from unittest.mock import MagicMock

        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "not a json"
        resp.model = "gpt-test"
        resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)

        mock_client.chat.completions.create = AsyncMock(return_value=resp)

        client = OpenAIClient(api_key="sk-test", default_model="gpt-test")
        with pytest.raises(LLMError):
            await client.chat(
                [Message(role="user", content="hi")],
                schema=_Schema,
            )


class TestDeepSeekWrap:
    async def test_deepseek_wraps_api_error(self, mocker) -> None:
        import openai

        from explain_engine.llm.deepseek import DeepSeekClient

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.deepseek.AsyncOpenAI",
            return_value=mock_client,
        )

        mock_client.chat.completions.create = AsyncMock(
            side_effect=openai.APIConnectionError(request=_make_request())
        )

        client = DeepSeekClient(
            api_key="sk-test",
            default_model="deepseek-test",
            base_url="https://api.deepseek.test",
        )
        with pytest.raises(LLMError):
            await client.chat([Message(role="user", content="hi")])

    async def test_deepseek_wraps_json_decode_error(self, mocker) -> None:
        from pydantic import BaseModel

        from explain_engine.llm.deepseek import DeepSeekClient

        class _Schema(BaseModel):
            answer: str

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.deepseek.AsyncOpenAI",
            return_value=mock_client,
        )

        from unittest.mock import MagicMock

        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "not json {{{"
        resp.model = "deepseek-test"
        resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)

        mock_client.chat.completions.create = AsyncMock(return_value=resp)

        client = DeepSeekClient(
            api_key="sk-test",
            default_model="deepseek-test",
            base_url="https://api.deepseek.test",
        )
        with pytest.raises(LLMError):
            await client.chat(
                [Message(role="user", content="hi")],
                schema=_Schema,
            )


class TestExceptionChainPreserved:
    """raise X from exc 保留 __cause__。"""

    async def test_claude_chain_preserved(self, mocker) -> None:
        from anthropic import APIError

        from explain_engine.llm.claude import ClaudeClient

        mock_client = AsyncMock()
        mocker.patch(
            "explain_engine.llm.claude.AsyncAnthropic",
            return_value=mock_client,
        )

        original = APIError(
            message="boom",
            request=_make_request(),
            body=None,
        )
        mock_client.messages.create = AsyncMock(side_effect=original)

        client = ClaudeClient(api_key="sk-test", default_model="claude-test")
        try:
            await client.chat([Message(role="user", content="hi")])
        except LLMError as wrapped:
            assert wrapped.__cause__ is original
        else:
            pytest.fail("expected LLMError")
