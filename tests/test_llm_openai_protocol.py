"""OpenAIProtocolClient 单测 (含 json_schema / json_object 双 mode)。

Phase 5: merge 旧 OpenAIClient + DeepSeekClient。mode='json_schema' 走 OpenAI
官方 strict 路径，mode='json_object' 走 prompt 注入路径 (DeepSeek 等用)。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from explain_engine.llm.client import Message
from explain_engine.llm.openai_protocol import OpenAIProtocolClient


class _DemoSchema(BaseModel):
    answer: str
    confidence: float


@pytest.fixture
def mock_openai(mocker):
    mock_client = AsyncMock()
    mocker.patch(
        "explain_engine.llm.openai_protocol.AsyncOpenAI",
        return_value=mock_client,
    )
    return mock_client


def _mock_choice(content: str, model: str = "gpt-4o"):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.model = model
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
    return resp


class TestJsonSchemaMode:
    async def test_chat_text_response(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice("hello world")
        )
        client = OpenAIProtocolClient(api_key="sk-test", default_model="gpt-4o")
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
        client = OpenAIProtocolClient(api_key="sk-test", default_model="gpt-4o")
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
        client = OpenAIProtocolClient(api_key="sk-test", default_model="gpt-4o")
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
        client = OpenAIProtocolClient(api_key="sk-test", default_model="gpt-4o")
        await client.chat([Message(role="user", content="hi")], model="gpt-4-turbo")
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "gpt-4-turbo"

    async def test_default_mode_is_json_schema(self) -> None:
        c = OpenAIProtocolClient(
            api_key="sk-fake", default_model="gpt-4o",
            base_url="https://api.openai.com/v1",
        )
        assert c._mode == "json_schema"


class TestJsonObjectMode:
    async def test_chat_with_schema_injects_into_system_prompt(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice(
                json.dumps({"answer": "yes", "confidence": 0.9})
            )
        )
        client = OpenAIProtocolClient(
            api_key="sk-test",
            default_model="deepseek-chat",
            base_url="https://api.deepseek.com",
            mode="json_object",
        )
        r = await client.chat(
            [Message(role="user", content="hi")],
            schema=_DemoSchema,
        )
        assert r.parsed == {"answer": "yes", "confidence": 0.9}

        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert kwargs["response_format"] == {"type": "json_object"}
        first_msg = kwargs["messages"][0]
        assert first_msg["role"] == "system"
        assert "_DemoSchema" in first_msg["content"]
        assert "answer" in first_msg["content"]
        assert "confidence" in first_msg["content"]

    async def test_chat_no_schema_no_json_mode(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice("hello")
        )
        client = OpenAIProtocolClient(
            api_key="sk-test",
            default_model="deepseek-chat",
            base_url="https://api.deepseek.com",
            mode="json_object",
        )
        r = await client.chat([Message(role="user", content="hi")])
        assert r.text == "hello"
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert "response_format" not in kwargs

    async def test_existing_system_message_preserved(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice(json.dumps({"answer": "x", "confidence": 0.5}))
        )
        client = OpenAIProtocolClient(
            api_key="sk-test",
            default_model="deepseek-chat",
            base_url="https://api.deepseek.com",
            mode="json_object",
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
        assert "you are helpful" in first_msg["content"]
        assert "_DemoSchema" in first_msg["content"]
