from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from explain_agent.config import LLMConfig
from explain_agent.llm.client import AnthropicClient, OpenAIClient, make_llm_client


def test_make_client_anthropic():
    cfg = LLMConfig(protocol="anthropic", base_url="https://api.anthropic.com",
                    api_key="sk", model="claude-opus-4-7")
    with patch("explain_agent.llm.client.Anthropic") as mock_cls:
        c = make_llm_client(cfg)
    assert isinstance(c, AnthropicClient)
    mock_cls.assert_called_once_with(api_key="sk", base_url="https://api.anthropic.com")


def test_make_client_openai():
    cfg = LLMConfig(protocol="openai", base_url="https://api.deepseek.com/v1",
                    api_key="sk", model="deepseek-chat")
    with patch("explain_agent.llm.client.OpenAI") as mock_cls:
        c = make_llm_client(cfg)
    assert isinstance(c, OpenAIClient)
    mock_cls.assert_called_once_with(api_key="sk", base_url="https://api.deepseek.com/v1")


def test_anthropic_chat_returns_text():
    cfg = LLMConfig(protocol="anthropic", base_url="x", api_key="x", model="m")
    fake_sdk = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(text="hello")]
    fake_sdk.messages.create.return_value = fake_resp
    client = AnthropicClient(cfg, sdk_client=fake_sdk)
    out = client.chat(system="sys", user="hi", max_tokens=100)
    assert out == "hello"
    args, kwargs = fake_sdk.messages.create.call_args
    assert kwargs["model"] == "m"
    assert kwargs["system"] == "sys"
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_openai_chat_returns_text():
    cfg = LLMConfig(protocol="openai", base_url="x", api_key="x", model="m")
    fake_sdk = MagicMock()
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="hi-back"))]
    fake_sdk.chat.completions.create.return_value = fake_resp
    client = OpenAIClient(cfg, sdk_client=fake_sdk)
    out = client.chat(system="sys", user="hi", max_tokens=100)
    assert out == "hi-back"
    args, kwargs = fake_sdk.chat.completions.create.call_args
    assert kwargs["model"] == "m"
    assert kwargs["messages"][0] == {"role": "system", "content": "sys"}
    assert kwargs["messages"][1] == {"role": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_anthropic_achat_lazy_inits_async_sdk():
    """achat 首次调用时才创建 AsyncAnthropic。"""
    from explain_agent.llm.client import AnthropicClient
    from explain_agent.config import LLMConfig

    cfg = LLMConfig(protocol="anthropic", base_url="https://x", api_key="k", model="claude-x")
    client = AnthropicClient(cfg, sdk_client=MagicMock())
    assert client._async_sdk is None

    fake_async = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(text="hello")]
    fake_async.messages.create = AsyncMock(return_value=fake_resp)
    client._async_sdk = fake_async

    out = await client.achat(system="s", user="u")
    assert out == "hello"
    fake_async.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_openai_achat_returns_content():
    from explain_agent.llm.client import OpenAIClient
    from explain_agent.config import LLMConfig

    cfg = LLMConfig(protocol="openai", base_url="https://x/v1", api_key="k", model="deepseek-x")
    client = OpenAIClient(cfg, sdk_client=MagicMock())
    fake_async = MagicMock()
    fake_choice = MagicMock()
    fake_choice.message.content = "world"
    fake_async.chat.completions.create = AsyncMock(return_value=MagicMock(choices=[fake_choice]))
    client._async_sdk = fake_async

    out = await client.achat(system="s", user="u")
    assert out == "world"
