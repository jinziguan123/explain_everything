"""AnthropicProtocolClient 单测（mock anthropic SDK）。

Phase 5: 旧 ClaudeClient 改名 + 加 base_url 参数 (跨 vendor: Anthropic 官方 /
DeepSeek anthropic / Bedrock 等)。
Phase 13 hotfix #4 (2026-05-20): Anthropic SDK 在 non-streaming 模式下对
max_tokens 有硬限制 (general formula > 21333 reject, 部分 model_cap 在
8192). 改用 messages.stream() async-context-manager + get_final_message()
解锁真实 model 上限. 下游 Message 对象同形, 解析逻辑不变.
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


def _stream_manager(message_resp):
    """Helper: build async-context-manager mock for messages.stream(...).

    Phase 13 hotfix #4: anthropic SDK messages.stream() returns
    AsyncMessageStreamManager. `async with` yields AsyncMessageStream whose
    get_final_message() resolves to the same Message shape create() returns.
    Helper packages a pre-built mock message_resp into that shape.
    """
    stream = AsyncMock()
    stream.get_final_message = AsyncMock(return_value=message_resp)
    mgr = MagicMock()
    mgr.__aenter__ = AsyncMock(return_value=stream)
    mgr.__aexit__ = AsyncMock(return_value=None)
    return mgr


def _set_stream_returns(mock_anthropic, *message_responses):
    """Configure mock_anthropic.messages.stream to return given responses
    in order. Single resp → all calls return it; multiple → side_effect."""
    if len(message_responses) == 1:
        mock_anthropic.messages.stream = MagicMock(
            return_value=_stream_manager(message_responses[0])
        )
    else:
        mock_anthropic.messages.stream = MagicMock(
            side_effect=[_stream_manager(r) for r in message_responses]
        )


class TestAnthropicProtocolClient:
    async def test_chat_text_response(self, mock_anthropic):
        _set_stream_returns(mock_anthropic, _mock_message_response("hello world"))
        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-opus-4-7")
        r = await client.chat([Message(role="user", content="hi")])
        assert r.text == "hello world"
        assert r.parsed is None
        assert r.usage == {"input_tokens": 10, "output_tokens": 20}

    async def test_chat_with_schema_uses_tools(self, mock_anthropic):
        _set_stream_returns(
            mock_anthropic,
            _mock_tool_use_response({"answer": "yes", "confidence": 0.9}),
        )
        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-opus-4-7")
        r = await client.chat(
            [Message(role="user", content="hi")],
            schema=_DemoSchema,
        )
        assert r.parsed == {"answer": "yes", "confidence": 0.9}
        call_kwargs = mock_anthropic.messages.stream.call_args.kwargs
        assert "tools" in call_kwargs
        assert call_kwargs["tools"][0]["name"] == "_DemoSchema"
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "_DemoSchema"}

    async def test_system_message_extracted(self, mock_anthropic):
        _set_stream_returns(mock_anthropic, _mock_message_response("ok"))
        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-opus-4-7")
        await client.chat(
            [
                Message(role="system", content="you are helpful"),
                Message(role="user", content="hi"),
            ]
        )
        kwargs = mock_anthropic.messages.stream.call_args.kwargs
        assert kwargs["system"] == "you are helpful"
        assert all(m["role"] != "system" for m in kwargs["messages"])

    async def test_default_model_used(self, mock_anthropic):
        _set_stream_returns(mock_anthropic, _mock_message_response("ok"))
        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-haiku")
        await client.chat([Message(role="user", content="hi")])
        kwargs = mock_anthropic.messages.stream.call_args.kwargs
        assert kwargs["model"] == "claude-haiku"

    async def test_model_override(self, mock_anthropic):
        _set_stream_returns(mock_anthropic, _mock_message_response("ok"))
        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-opus-4-7")
        await client.chat([Message(role="user", content="hi")], model="claude-haiku")
        kwargs = mock_anthropic.messages.stream.call_args.kwargs
        assert kwargs["model"] == "claude-haiku"

    async def test_chat_uses_streaming_api_not_create(self, mock_anthropic):
        """Phase 13 hotfix #4 (regression guard): chat() MUST go through
        messages.stream() (async context manager), NOT messages.create().

        Why: non-streaming create() caps max_tokens via SDK-side checks
        (general formula > 21333 reject, model_cap e.g. claude-opus-4 = 8192).
        Streaming unlocks the actual model output limit (deepseek-v4-pro
        claims 384K). 直接 mock messages.create 来断言它没被调用.
        """
        _set_stream_returns(mock_anthropic, _mock_message_response("ok"))
        # 显式 mock create 成 AsyncMock 以便 assert_not_called 可用
        mock_anthropic.messages.create = AsyncMock()
        client = AnthropicProtocolClient(api_key="sk-test", default_model="claude-opus-4-7")
        await client.chat([Message(role="user", content="hi")])
        mock_anthropic.messages.create.assert_not_called()
        assert mock_anthropic.messages.stream.called

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


def _mock_thinking_then_text_response(
    thinking_text: str,
    answer_text: str,
    model: str = "claude-opus-4-7",
):
    """Phase 19 Task 2: mock final message with thinking block + text block.

    chat() path 遍历 api_resp.content 解析 block.type — thinking block 应被
    收集到 reasoning 字段, text block 累积到 text 字段.
    """
    resp = MagicMock()
    thinking_block = MagicMock()
    thinking_block.type = "thinking"
    thinking_block.thinking = thinking_text
    text_block = MagicMock(type="text", text=answer_text)
    resp.content = [thinking_block, text_block]
    resp.model = model
    resp.usage = MagicMock(input_tokens=10, output_tokens=20)
    return resp


class TestAnthropicChatThinkingBlocks:
    """Phase 19 Task 2: chat() 路径暴露 thinking blocks → Response.reasoning.

    现 chat_with_tools 已收 thinking 到 ToolsResponse.raw_content_blocks
    (Phase 9 Wave F.4), 但普通 chat() 路径 (structured output) 之前没解析.
    Wave 1 补对称.
    """

    async def test_chat_extracts_thinking_blocks_to_reasoning(self, mock_anthropic):
        """final message 含 thinking + text block → resp.reasoning 拿 thinking, text 拿正文."""
        _set_stream_returns(
            mock_anthropic,
            _mock_thinking_then_text_response(
                thinking_text="让我先想一下用户的问题",
                answer_text="最终回答",
            ),
        )
        client = AnthropicProtocolClient(
            api_key="sk-test", default_model="claude-opus-4-7"
        )
        r = await client.chat([Message(role="user", content="hi")])
        assert r.text == "最终回答"
        assert r.reasoning == "让我先想一下用户的问题"

    async def test_chat_no_thinking_block_reasoning_is_none(self, mock_anthropic):
        """final message 只有 text block (无 thinking) → resp.reasoning is None."""
        _set_stream_returns(mock_anthropic, _mock_message_response("just text"))
        client = AnthropicProtocolClient(
            api_key="sk-test", default_model="claude-opus-4-7"
        )
        r = await client.chat([Message(role="user", content="hi")])
        assert r.text == "just text"
        assert r.reasoning is None

    async def test_chat_multiple_thinking_blocks_concatenated(self, mock_anthropic):
        """多个 thinking block (理论上少见) → concat 入 reasoning."""
        resp = MagicMock()
        block1 = MagicMock()
        block1.type = "thinking"
        block1.thinking = "step1 "
        block2 = MagicMock()
        block2.type = "thinking"
        block2.thinking = "step2"
        text = MagicMock(type="text", text="answer")
        resp.content = [block1, block2, text]
        resp.model = "claude-opus-4-7"
        resp.usage = MagicMock(input_tokens=10, output_tokens=20)
        _set_stream_returns(mock_anthropic, resp)
        client = AnthropicProtocolClient(
            api_key="sk-test", default_model="claude-opus-4-7"
        )
        r = await client.chat([Message(role="user", content="hi")])
        assert r.reasoning == "step1 step2"
        assert r.text == "answer"

    async def test_chat_handles_redacted_thinking_block(self, mock_anthropic):
        """Phase 19 Wave 1 review fix (C-1): vendor content policy 触发遮蔽 thinking
        → Response.reasoning 含 marker '[redacted thinking]', 不静默丢.

        chat_with_tools 之前已处理 (line 347-351 把 redacted_thinking dict 保
        留到 raw_content_blocks). chat() 没对称处理 → 用户感知 reasoning 截断, debug
        困难. 现 chat() 也 cover.
        """
        resp = MagicMock()
        redacted_block = MagicMock()
        redacted_block.type = "redacted_thinking"
        redacted_block.data = "encrypted-blob-from-vendor"
        text_block = MagicMock(type="text", text="final answer")
        resp.content = [redacted_block, text_block]
        resp.model = "claude-opus-4-7"
        resp.usage = MagicMock(input_tokens=10, output_tokens=5)
        _set_stream_returns(mock_anthropic, resp)
        client = AnthropicProtocolClient(
            api_key="sk-test", default_model="claude-opus-4-7"
        )
        r = await client.chat([Message(role="user", content="hi")])
        assert r.reasoning == "[redacted thinking]"
        assert r.text == "final answer"


class TestAnthropicEnableThinkingParam:
    """Phase 19 Task 3: AnthropicProtocolClient 加 enable_thinking 构造参.

    True (默) → call_kwargs["thinking"] = {type:enabled, budget_tokens:4096}.
    False → omit (省 token, 不出 thinking blocks).
    chat() / chat_with_tools 同款 wire.
    """

    async def test_chat_passes_thinking_param_when_enabled(self, mock_anthropic):
        _set_stream_returns(mock_anthropic, _mock_message_response("ok"))
        client = AnthropicProtocolClient(
            api_key="sk-test",
            default_model="claude-opus-4-7",
            enable_thinking=True,
        )
        await client.chat([Message(role="user", content="hi")])
        kwargs = mock_anthropic.messages.stream.call_args.kwargs
        assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 4096}

    async def test_chat_omits_thinking_param_when_disabled(self, mock_anthropic):
        _set_stream_returns(mock_anthropic, _mock_message_response("ok"))
        client = AnthropicProtocolClient(
            api_key="sk-test",
            default_model="claude-opus-4-7",
            enable_thinking=False,
        )
        await client.chat([Message(role="user", content="hi")])
        kwargs = mock_anthropic.messages.stream.call_args.kwargs
        assert "thinking" not in kwargs

    async def test_default_enable_thinking_is_true(self, mock_anthropic):
        """default constructor → enable_thinking=True (省 token 由 caller / env 控)."""
        _set_stream_returns(mock_anthropic, _mock_message_response("ok"))
        client = AnthropicProtocolClient(
            api_key="sk-test", default_model="claude-opus-4-7"
        )
        await client.chat([Message(role="user", content="hi")])
        kwargs = mock_anthropic.messages.stream.call_args.kwargs
        assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 4096}

    async def test_chat_with_tools_passes_thinking_param_when_enabled(
        self, mock_anthropic
    ):
        """chat_with_tools 也走同款 thinking wire."""
        resp = MagicMock()
        resp.content = [MagicMock(type="text", text="ok")]
        resp.model = "claude-opus-4-7"
        resp.stop_reason = "end_turn"
        resp.usage = MagicMock(input_tokens=10, output_tokens=20)
        _set_stream_returns(mock_anthropic, resp)
        client = AnthropicProtocolClient(
            api_key="sk-test",
            default_model="claude-opus-4-7",
            enable_thinking=True,
        )
        await client.chat_with_tools(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
        kwargs = mock_anthropic.messages.stream.call_args.kwargs
        assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 4096}

    async def test_chat_with_tools_omits_thinking_param_when_disabled(
        self, mock_anthropic
    ):
        resp = MagicMock()
        resp.content = [MagicMock(type="text", text="ok")]
        resp.model = "claude-opus-4-7"
        resp.stop_reason = "end_turn"
        resp.usage = MagicMock(input_tokens=10, output_tokens=20)
        _set_stream_returns(mock_anthropic, resp)
        client = AnthropicProtocolClient(
            api_key="sk-test",
            default_model="claude-opus-4-7",
            enable_thinking=False,
        )
        await client.chat_with_tools(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
        )
        kwargs = mock_anthropic.messages.stream.call_args.kwargs
        assert "thinking" not in kwargs
