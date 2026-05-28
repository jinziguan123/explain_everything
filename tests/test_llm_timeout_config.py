"""Phase 20.0 Task 1 Layer A: LLM_READ_TIMEOUT_S env knob + AsyncAnthropic/OpenAI timeout kwarg.

防 streaming chunk gap 永等 — DeepSeek API silent drop 后无 timeout 兜底.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import httpx

from explain_engine.config import make_light_llm_client, make_llm_client
from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient
from explain_engine.llm.openai_protocol import OpenAIProtocolClient


def test_anthropic_protocol_client_default_read_timeout_120():
    """AnthropicProtocolClient.__init__ 默 read_timeout=120, AsyncAnthropic 收到 httpx.Timeout."""
    with patch("explain_engine.llm.anthropic_protocol.AsyncAnthropic") as mock_anthropic:
        AnthropicProtocolClient(
            api_key="sk-test",
            default_model="claude-test",
            base_url=None,
        )
        call_kwargs = mock_anthropic.call_args.kwargs
        assert "timeout" in call_kwargs
        timeout = call_kwargs["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read == 120.0


def test_anthropic_protocol_client_custom_read_timeout():
    """AnthropicProtocolClient(read_timeout=30) → AsyncAnthropic 收 timeout.read=30."""
    with patch("explain_engine.llm.anthropic_protocol.AsyncAnthropic") as mock_anthropic:
        AnthropicProtocolClient(
            api_key="sk-test",
            default_model="claude-test",
            base_url=None,
            read_timeout=30.0,
        )
        call_kwargs = mock_anthropic.call_args.kwargs
        timeout = call_kwargs["timeout"]
        assert timeout.read == 30.0


def test_openai_protocol_client_default_read_timeout_120():
    """OpenAIProtocolClient.__init__ 默 read_timeout=120, AsyncOpenAI 收 httpx.Timeout."""
    with patch("explain_engine.llm.openai_protocol.AsyncOpenAI") as mock_openai:
        OpenAIProtocolClient(
            api_key="sk-test",
            default_model="gpt-test",
            base_url=None,
        )
        call_kwargs = mock_openai.call_args.kwargs
        assert "timeout" in call_kwargs
        timeout = call_kwargs["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read == 120.0


def test_openai_protocol_client_custom_read_timeout():
    """OpenAIProtocolClient(read_timeout=60) → AsyncOpenAI 收 timeout.read=60."""
    with patch("explain_engine.llm.openai_protocol.AsyncOpenAI") as mock_openai:
        OpenAIProtocolClient(
            api_key="sk-test",
            default_model="gpt-test",
            base_url=None,
            read_timeout=60.0,
        )
        call_kwargs = mock_openai.call_args.kwargs
        timeout = call_kwargs["timeout"]
        assert timeout.read == 60.0


def test_make_llm_client_reads_env_default_120(monkeypatch):
    """LLM_READ_TIMEOUT_S 未设 → make_llm_client 传 read_timeout=120 给 protocol client."""
    for k in list(os.environ.keys()):
        if k.startswith("LLM_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "claude-test")

    with patch("explain_engine.config.AnthropicProtocolClient") as mock_apc:
        make_llm_client()
        call_kwargs = mock_apc.call_args.kwargs
        assert call_kwargs.get("read_timeout") == 120.0


def test_make_llm_client_env_override(monkeypatch):
    """LLM_READ_TIMEOUT_S=30 → make_llm_client 传 read_timeout=30 给 protocol client."""
    for k in list(os.environ.keys()):
        if k.startswith("LLM_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "claude-test")
    monkeypatch.setenv("LLM_READ_TIMEOUT_S", "30")

    with patch("explain_engine.config.AnthropicProtocolClient") as mock_apc:
        make_llm_client()
        call_kwargs = mock_apc.call_args.kwargs
        assert call_kwargs.get("read_timeout") == 30.0


def test_make_light_llm_client_reads_env_default_120(monkeypatch):
    """make_light_llm_client 同款读 LLM_READ_TIMEOUT_S env, default 120."""
    for k in list(os.environ.keys()):
        if k.startswith("LLM_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_LIGHT_PROTOCOL", "openai")
    monkeypatch.setenv("LLM_LIGHT_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("LLM_LIGHT_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_LIGHT_MODEL", "gpt-test")
    # make_light_llm_client fallback 到主 LLM_*, 必填项也需设
    monkeypatch.setenv("LLM_PROTOCOL", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "gpt-test")

    with patch("explain_engine.config.OpenAIProtocolClient") as mock_opc:
        client = make_light_llm_client()
        if client is not None:
            call_kwargs = mock_opc.call_args.kwargs
            assert call_kwargs.get("read_timeout") == 120.0
