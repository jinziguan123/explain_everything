"""OpenAIClient 单测（mock openai SDK）。"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from explain_engine.llm.client import Message
from explain_engine.llm.openai_client import OpenAIClient


class _DemoSchema(BaseModel):
    answer: str
    confidence: float


@pytest.fixture
def mock_openai(mocker):
    mock_client = AsyncMock()
    mocker.patch(
        "explain_engine.llm.openai_client.AsyncOpenAI",
        return_value=mock_client,
    )
    return mock_client


def _mock_choice(content: str, model: str = "gpt-4o"):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.model = model
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
    return resp


class TestOpenAIClient:
    async def test_chat_text_response(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice("hello world")
        )
        client = OpenAIClient(api_key="sk-test", default_model="gpt-4o")
        r = await client.chat([Message(role="user", content="hi")])
        assert r.text == "hello world"
        assert r.parsed is None
        assert r.usage == {"input_tokens": 10, "output_tokens": 20}

    async def test_chat_with_schema_uses_json_schema(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice(
                json.dumps({"answer": "yes", "confidence": 0.9})
            )
        )
        client = OpenAIClient(api_key="sk-test", default_model="gpt-4o")
        r = await client.chat(
            [Message(role="user", content="hi")],
            schema=_DemoSchema,
        )
        assert r.parsed == {"answer": "yes", "confidence": 0.9}
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert kwargs["response_format"]["type"] == "json_schema"
        assert kwargs["response_format"]["json_schema"]["name"] == "_DemoSchema"

    async def test_messages_passed_through(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice("ok")
        )
        client = OpenAIClient(api_key="sk-test", default_model="gpt-4o")
        await client.chat(
            [
                Message(role="system", content="you are helpful"),
                Message(role="user", content="hi"),
            ]
        )
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert kwargs["messages"] == [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hi"},
        ]

    async def test_model_override(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice("ok")
        )
        client = OpenAIClient(api_key="sk-test", default_model="gpt-4o")
        await client.chat([Message(role="user", content="hi")], model="gpt-4-turbo")
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "gpt-4-turbo"
