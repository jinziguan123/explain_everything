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


def _mock_choice_with_reasoning(
    content: str,
    reasoning_content: str | None,
    model: str = "deepseek-reasoner",
):
    """Phase 19 Task 4: mock OpenAI-compat message with reasoning_content field.

    DeepSeek-R1 / OpenAI-compat vendor 返 reasoning_content alongside content.
    """
    msg = MagicMock(spec=["content", "reasoning_content"])
    msg.content = content
    msg.reasoning_content = reasoning_content
    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]
    resp.model = model
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
    return resp


def _mock_choice_no_reasoning_attr(content: str, model: str = "gpt-4o"):
    """gpt-4o etc. — message 对象不带 reasoning_content 字段 (spec 限定)."""
    msg = MagicMock(spec=["content"])
    msg.content = content
    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]
    resp.model = model
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
    return resp


class TestOpenAIChatReasoningContent:
    """Phase 19 Task 4: openai chat() 解析 vendor 返的 reasoning_content.

    DeepSeek-R1 / openai-compat endpoint 返 message.reasoning_content alongside
    message.content. gpt-4o 不返此字段 → Response.reasoning is None 自然.
    用 getattr(msg, "reasoning_content", None) 兼容两种情况.
    """

    async def test_chat_extracts_reasoning_content(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice_with_reasoning(
                content="最终回答",
                reasoning_content="让我先想一下用户的问题",
            )
        )
        client = OpenAIProtocolClient(
            api_key="sk-test", default_model="deepseek-reasoner"
        )
        r = await client.chat([Message(role="user", content="hi")])
        assert r.text == "最终回答"
        assert r.reasoning == "让我先想一下用户的问题"

    async def test_chat_no_reasoning_content_field_is_none(self, mock_openai):
        """gpt-4o 等不返 reasoning_content → resp.reasoning is None."""
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice_no_reasoning_attr("just answer"),
        )
        client = OpenAIProtocolClient(
            api_key="sk-test", default_model="gpt-4o"
        )
        r = await client.chat([Message(role="user", content="hi")])
        assert r.text == "just answer"
        assert r.reasoning is None

    async def test_chat_reasoning_content_explicit_none(self, mock_openai):
        """vendor 返 reasoning_content=None 字段 → resp.reasoning is None."""
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice_with_reasoning(
                content="answer", reasoning_content=None
            )
        )
        client = OpenAIProtocolClient(
            api_key="sk-test", default_model="deepseek-reasoner"
        )
        r = await client.chat([Message(role="user", content="hi")])
        assert r.reasoning is None

    async def test_chat_reasoning_content_non_str_falls_back_to_none(
        self, mock_openai
    ):
        """Phase 19 Wave 1 review fix (I-4): vendor 返 reasoning_content 是 list/dict
        (违反 contract) → defensive narrow (isinstance str check) → resp.reasoning is None.

        防 vendor implementation drift 把意外类型直接灌入 Response.reasoning, 下游
        消费 str 时炸. 显式断 None 防 silent regression.
        """
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice_with_reasoning(
                content="answer",
                reasoning_content=[],  # type: ignore[arg-type]  # 非 str, 触发 isinstance 防御
            )
        )
        client = OpenAIProtocolClient(
            api_key="sk-test", default_model="deepseek-reasoner"
        )
        r = await client.chat([Message(role="user", content="hi")])
        assert r.text == "answer"
        assert r.reasoning is None  # 防御 narrow 工作
