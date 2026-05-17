"""Settings (pydantic-settings) + make_llm_client factory。

Phase 5: 协议跟供应商解耦 (LLM_PROTOCOL + LLM_BASE_URL + LLM_API_KEY +
LLM_MODEL)。3 个 client → 2 个 (anthropic_protocol / openai_protocol)，
DeepSeek 通过 base_url 切换。
"""

import os
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient
from explain_engine.llm.client import LLMClient
from explain_engine.llm.openai_protocol import Mode, OpenAIProtocolClient

Protocol = Literal["anthropic", "openai"]


class Settings(BaseSettings):
    """Runtime 配置。LLM 配置直接读 env (避免 pydantic-settings 对未配
    LLM_PROTOCOL 等场景报 ValidationError)。

    Phase 9 Wave A.1 fix · I2: 移除 sessions_dir 字段 — storage_v2 走
    EXPLAIN_HOME / EXPLAIN_PROJECT_ID env vars, 老 SESSIONS_DIR env 是
    silent no-op (extra='ignore' 容忍, 不报错).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    default_budget: int = Field(default=20, ge=1)


def make_llm_client() -> LLMClient:
    """按 LLM_PROTOCOL 路由到对应 client。

    自动加载 .env (override=False, 已设的 env var 优先 — 让 test monkeypatch 生效)。

    必填 env:
      - LLM_PROTOCOL: 'anthropic' 或 'openai'
      - LLM_BASE_URL: API 入口 (e.g. https://api.anthropic.com)
      - LLM_API_KEY:  API key
      - LLM_MODEL:    模型名 (e.g. claude-opus-4-7 / gpt-4o / deepseek-chat)

    可选 env (仅 openai 协议):
      - LLM_STRUCTURED_OUTPUT_MODE: 'json_schema' (默认) 或 'json_object'
        (DeepSeek 等不支持 json_schema strict 的 vendor 用 json_object)
    """
    load_dotenv(override=False)
    try:
        proto = os.environ["LLM_PROTOCOL"]
        base_url = os.environ["LLM_BASE_URL"]
        api_key = os.environ["LLM_API_KEY"]
        model = os.environ["LLM_MODEL"]
    except KeyError as exc:
        raise KeyError(
            f"missing required env var: {exc.args[0]} "
            f"(required: LLM_PROTOCOL / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL)"
        ) from exc

    if proto == "anthropic":
        return AnthropicProtocolClient(
            api_key=api_key,
            default_model=model,
            base_url=base_url,
        )
    if proto == "openai":
        mode_str = os.environ.get("LLM_STRUCTURED_OUTPUT_MODE", "json_schema")
        if mode_str not in ("json_schema", "json_object"):
            raise ValueError(
                f"LLM_STRUCTURED_OUTPUT_MODE must be 'json_schema' or 'json_object', "
                f"got {mode_str!r}"
            )
        mode: Mode = mode_str  # type: ignore[assignment]
        return OpenAIProtocolClient(
            api_key=api_key,
            default_model=model,
            base_url=base_url,
            mode=mode,
        )
    raise ValueError(
        f"Unknown LLM_PROTOCOL: {proto!r}, must be 'anthropic' or 'openai'"
    )
