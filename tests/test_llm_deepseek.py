"""DeepSeekClient 单测。

DeepSeek 用 OpenAI 兼容 API，但 structured output 是 JSON mode 而不是
json_schema，所以 schema 通过 prompt 描述注入。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from explain_engine.llm.client import Message
from explain_engine.llm.deepseek import DeepSeekClient


class _DemoSchema(BaseModel):
    answer: str
    confidence: float


@pytest.fixture
def mock_openai(mocker):
    mock_client = AsyncMock()
    mocker.patch(
        "explain_engine.llm.deepseek.AsyncOpenAI",
        return_value=mock_client,
    )
    return mock_client


def _mock_choice(content: str, model: str = "deepseek-chat"):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.model = model
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
    return resp


class TestDeepSeekClient:
    async def test_chat_with_schema_injects_into_system_prompt(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice(
                json.dumps({"answer": "yes", "confidence": 0.9})
            )
        )
        client = DeepSeekClient(
            api_key="sk-test",
            default_model="deepseek-chat",
            base_url="https://api.deepseek.com",
        )
        r = await client.chat(
            [Message(role="user", content="hi")],
            schema=_DemoSchema,
        )
        assert r.parsed == {"answer": "yes", "confidence": 0.9}

        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        # JSON mode
        assert kwargs["response_format"] == {"type": "json_object"}
        # schema 应该被注入第一条 system message
        first_msg = kwargs["messages"][0]
        assert first_msg["role"] == "system"
        assert "_DemoSchema" in first_msg["content"]
        assert "answer" in first_msg["content"]
        assert "confidence" in first_msg["content"]

    async def test_chat_no_schema_no_json_mode(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice("hello")
        )
        client = DeepSeekClient(
            api_key="sk-test",
            default_model="deepseek-chat",
            base_url="https://api.deepseek.com",
        )
        r = await client.chat([Message(role="user", content="hi")])
        assert r.text == "hello"
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert "response_format" not in kwargs

    async def test_existing_system_message_preserved(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice(json.dumps({"answer": "x", "confidence": 0.5}))
        )
        client = DeepSeekClient(
            api_key="sk-test",
            default_model="deepseek-chat",
            base_url="https://api.deepseek.com",
        )
        await client.chat(
            [
                Message(role="system", content="you are helpful"),
                Message(role="user", content="hi"),
            ],
            schema=_DemoSchema,
        )
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        first_msg = kwargs["messages"][0]
        # schema 注入应该 prepend 到原 system message
        assert "you are helpful" in first_msg["content"]
        assert "_DemoSchema" in first_msg["content"]
