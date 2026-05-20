"""LLM client factory: 按 LLM_PROTOCOL 路由 anthropic / openai。

注: autouse fixture mock load_dotenv 为 no-op, 让 test 完全控制 env
(不被本地 .env 污染)。
"""

import pytest


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """所有 factory test 不加载 .env, 让 monkeypatch.setenv/delenv 完全决定 env。"""
    from explain_engine import config as config_mod
    monkeypatch.setattr(config_mod, "load_dotenv", lambda *_a, **_kw: False)


def _set_full_env(monkeypatch, *, protocol: str, base_url: str, api_key: str, model: str) -> None:
    monkeypatch.setenv("LLM_PROTOCOL", protocol)
    monkeypatch.setenv("LLM_BASE_URL", base_url)
    monkeypatch.setenv("LLM_API_KEY", api_key)
    monkeypatch.setenv("LLM_MODEL", model)


def test_factory_anthropic(monkeypatch) -> None:
    from explain_engine.config import make_llm_client
    from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient

    _set_full_env(
        monkeypatch,
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        api_key="sk-ant-fake",
        model="claude-opus-4-7",
    )
    client = make_llm_client()
    assert isinstance(client, AnthropicProtocolClient)


def test_factory_openai_json_schema(monkeypatch) -> None:
    from explain_engine.config import make_llm_client
    from explain_engine.llm.openai_protocol import OpenAIProtocolClient

    _set_full_env(
        monkeypatch,
        protocol="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-fake",
        model="gpt-4o",
    )
    monkeypatch.delenv("LLM_STRUCTURED_OUTPUT_MODE", raising=False)
    client = make_llm_client()
    assert isinstance(client, OpenAIProtocolClient)
    assert client._mode == "json_schema"


def test_factory_openai_json_object(monkeypatch) -> None:
    """DeepSeek 等用 json_object mode。"""
    from explain_engine.config import make_llm_client
    from explain_engine.llm.openai_protocol import OpenAIProtocolClient

    _set_full_env(
        monkeypatch,
        protocol="openai",
        base_url="https://api.deepseek.com/v1",
        api_key="sk-fake",
        model="deepseek-chat",
    )
    monkeypatch.setenv("LLM_STRUCTURED_OUTPUT_MODE", "json_object")
    client = make_llm_client()
    assert isinstance(client, OpenAIProtocolClient)
    assert client._mode == "json_object"


def test_factory_unknown_protocol_rejected(monkeypatch) -> None:
    from explain_engine.config import make_llm_client

    _set_full_env(
        monkeypatch, protocol="bogus", base_url="x", api_key="x", model="x"
    )
    with pytest.raises(ValueError, match="LLM_PROTOCOL"):
        make_llm_client()


def test_factory_missing_env_var_rejected(monkeypatch) -> None:
    """删 env var 后 make_llm_client 抛 KeyError。"""
    from explain_engine.config import make_llm_client

    monkeypatch.delenv("LLM_PROTOCOL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(KeyError, match="LLM_PROTOCOL"):
        make_llm_client()


def test_factory_default_max_tokens(monkeypatch) -> None:
    """Phase 13 hotfix #3: 默认 LLM_MAX_TOKENS = 16384 (DEFAULT_MAX_TOKENS)."""
    from explain_engine.config import make_llm_client
    from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient

    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)
    _set_full_env(
        monkeypatch,
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        api_key="sk-ant-fake",
        model="claude-opus-4-7",
    )
    client = make_llm_client()
    assert client._max_tokens == AnthropicProtocolClient.DEFAULT_MAX_TOKENS == 16384


def test_factory_env_overrides_max_tokens_anthropic(monkeypatch) -> None:
    """LLM_MAX_TOKENS=32768 → anthropic client picks it up."""
    from explain_engine.config import make_llm_client

    monkeypatch.setenv("LLM_MAX_TOKENS", "32768")
    _set_full_env(
        monkeypatch,
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        api_key="sk-ant-fake",
        model="claude-opus-4-7",
    )
    client = make_llm_client()
    assert client._max_tokens == 32768


def test_factory_env_overrides_max_tokens_openai(monkeypatch) -> None:
    """LLM_MAX_TOKENS env applies to openai protocol too."""
    from explain_engine.config import make_llm_client

    monkeypatch.setenv("LLM_MAX_TOKENS", "65536")
    _set_full_env(
        monkeypatch,
        protocol="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-fake",
        model="gpt-4o",
    )
    monkeypatch.delenv("LLM_STRUCTURED_OUTPUT_MODE", raising=False)
    client = make_llm_client()
    assert client._max_tokens == 65536


def test_factory_invalid_max_tokens_non_int(monkeypatch) -> None:
    from explain_engine.config import make_llm_client

    monkeypatch.setenv("LLM_MAX_TOKENS", "not-a-number")
    _set_full_env(
        monkeypatch,
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        api_key="sk-ant-fake",
        model="claude-opus-4-7",
    )
    with pytest.raises(ValueError, match="LLM_MAX_TOKENS"):
        make_llm_client()


def test_factory_invalid_max_tokens_zero(monkeypatch) -> None:
    from explain_engine.config import make_llm_client

    monkeypatch.setenv("LLM_MAX_TOKENS", "0")
    _set_full_env(
        monkeypatch,
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        api_key="sk-ant-fake",
        model="claude-opus-4-7",
    )
    with pytest.raises(ValueError, match="LLM_MAX_TOKENS"):
        make_llm_client()
