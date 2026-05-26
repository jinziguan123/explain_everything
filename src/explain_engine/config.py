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

    可选 env:
      - LLM_MAX_TOKENS: 整数, 每次 LLM call 的 max_tokens (default 16384).
        Phase 13 hotfix #4 (2026-05-20): anthropic 协议已切到 messages.stream(),
        解锁了 SDK 之前的 non-streaming cap (general formula > 21333 reject,
        部分 model_cap e.g. claude-opus-4 = 8192). 上限现在取决于 model 本身:
        deepseek-v4-pro 支持到 384K, Claude 4.x 实际 model 上限 64K (Sonnet)
        / 32K (Opus). 中文 dense JSON 输出场景 (bootstrap_phenomena /
        propose_candidates) 建议 ≥16384, 避免 mid-JSON truncation 导致
        SchemaValidationError. 推荐 32768 以上更稳.
        Note: openai 协议未走 streaming, 受 server-side max_tokens 限制
        (vendor 而非 SDK).
      - LLM_STRUCTURED_OUTPUT_MODE: 仅 openai 协议. 'json_schema' (默认)
        或 'json_object' (DeepSeek 等不支持 json_schema strict 的 vendor 用).
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

    # LLM_MAX_TOKENS: optional, validate int + positive
    max_tokens_env = os.environ.get("LLM_MAX_TOKENS")
    max_tokens: int | None = None
    if max_tokens_env is not None:
        try:
            max_tokens = int(max_tokens_env)
            if max_tokens < 1:
                raise ValueError(f"LLM_MAX_TOKENS must be >= 1, got {max_tokens}")
        except ValueError as exc:
            raise ValueError(
                f"LLM_MAX_TOKENS must be a positive integer, got {max_tokens_env!r}: {exc}"
            ) from exc

    if proto == "anthropic":
        return AnthropicProtocolClient(
            api_key=api_key,
            default_model=model,
            base_url=base_url,
            max_tokens=max_tokens,
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
            max_tokens=max_tokens,
        )
    raise ValueError(
        f"Unknown LLM_PROTOCOL: {proto!r}, must be 'anthropic' or 'openai'"
    )


def make_light_llm_client() -> LLMClient:
    """light-model factory. 读 LLM_LIGHT_* env, 缺则 fallback 主 LLM_*.

    Phase 17.2 Feature B: cheap LLM (haiku / gpt-4o-mini) 给 classify /
    canonical signature 这种简单 task 用. 失败由 with_light_fallback 自动
    回退到主 LLM.

    env precedence (per field): LLM_LIGHT_<X> 设了用它, 否则 fallback LLM_<X>.
    LIGHT_* 全空时该 client 跟主 LLM 配置完全一致 (零行为差).

    必填 (主 fallback): LLM_PROTOCOL / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL.

    可选: LLM_LIGHT_PROTOCOL / LLM_LIGHT_BASE_URL / LLM_LIGHT_API_KEY /
    LLM_LIGHT_MODEL / LLM_LIGHT_MAX_TOKENS.
    """
    load_dotenv(override=False)
    try:
        proto = os.environ.get("LLM_LIGHT_PROTOCOL") or os.environ["LLM_PROTOCOL"]
        base_url = (
            os.environ.get("LLM_LIGHT_BASE_URL") or os.environ["LLM_BASE_URL"]
        )
        api_key = os.environ.get("LLM_LIGHT_API_KEY") or os.environ["LLM_API_KEY"]
        model = os.environ.get("LLM_LIGHT_MODEL") or os.environ["LLM_MODEL"]
    except KeyError as exc:
        raise KeyError(
            f"missing required env var: {exc.args[0]} "
            f"(LIGHT 可缺, 但主 LLM_PROTOCOL / LLM_BASE_URL / LLM_API_KEY / "
            f"LLM_MODEL 必填)"
        ) from exc

    max_tokens_env = os.environ.get("LLM_LIGHT_MAX_TOKENS") or os.environ.get(
        "LLM_MAX_TOKENS"
    )
    max_tokens: int | None = None
    if max_tokens_env is not None:
        try:
            max_tokens = int(max_tokens_env)
            if max_tokens < 1:
                raise ValueError(
                    f"LLM_LIGHT_MAX_TOKENS must be >= 1, got {max_tokens}"
                )
        except ValueError as exc:
            raise ValueError(
                f"LLM_LIGHT_MAX_TOKENS must be a positive integer, "
                f"got {max_tokens_env!r}: {exc}"
            ) from exc

    if proto == "anthropic":
        return AnthropicProtocolClient(
            api_key=api_key,
            default_model=model,
            base_url=base_url,
            max_tokens=max_tokens,
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
            max_tokens=max_tokens,
        )
    raise ValueError(
        f"Unknown LLM_LIGHT_PROTOCOL (or LLM_PROTOCOL): {proto!r}, "
        f"must be 'anthropic' or 'openai'"
    )
