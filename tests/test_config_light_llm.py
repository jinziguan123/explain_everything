"""make_light_llm_client factory — Phase 17.2 Task 1+."""

from explain_engine.config import make_light_llm_client
from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient


def test_make_light_llm_client_fallback_all_empty(monkeypatch):
    """LIGHT_* 全空 → 用主 LLM_* 配置."""
    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setenv("LLM_API_KEY", "main-key")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-7")
    monkeypatch.delenv("LLM_LIGHT_PROTOCOL", raising=False)
    monkeypatch.delenv("LLM_LIGHT_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_LIGHT_API_KEY", raising=False)
    monkeypatch.delenv("LLM_LIGHT_MODEL", raising=False)
    monkeypatch.delenv("LLM_LIGHT_MAX_TOKENS", raising=False)

    light = make_light_llm_client()
    assert isinstance(light, AnthropicProtocolClient)
    assert light._default_model == "claude-opus-4-7"


def test_make_light_llm_client_independent(monkeypatch):
    """LIGHT_* 全配 → 独立 client, 不复用主 LLM."""
    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setenv("LLM_API_KEY", "main-key")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("LLM_LIGHT_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_LIGHT_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setenv("LLM_LIGHT_API_KEY", "light-key")
    monkeypatch.setenv("LLM_LIGHT_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("LLM_LIGHT_MAX_TOKENS", "4096")

    light = make_light_llm_client()
    assert isinstance(light, AnthropicProtocolClient)
    assert light._default_model == "claude-haiku-4-5"
