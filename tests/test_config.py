"""Settings + make_client factory test。"""

import pytest
from pydantic import ValidationError

from explain_engine.config import Settings, make_client
from explain_engine.llm.claude import ClaudeClient
from explain_engine.llm.deepseek import DeepSeekClient
from explain_engine.llm.openai_client import OpenAIClient


class TestSettings:
    def test_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "claude")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-7")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("DEFAULT_BUDGET", "30")

        s = Settings()
        assert s.llm_provider == "claude"
        assert s.llm_model == "claude-opus-4-7"
        assert s.default_budget == 30

    def test_invalid_provider_rejected(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "invalid")
        with pytest.raises(ValidationError):
            Settings()


class TestMakeClient:
    def test_make_claude(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "claude")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-7")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        client = make_client(Settings())
        assert isinstance(client, ClaudeClient)

    def test_make_openai(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        client = make_client(Settings())
        assert isinstance(client, OpenAIClient)

    def test_make_deepseek(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        client = make_client(Settings())
        assert isinstance(client, DeepSeekClient)

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "claude")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-7")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            make_client(Settings())
