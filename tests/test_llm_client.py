from unittest.mock import MagicMock, patch

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
