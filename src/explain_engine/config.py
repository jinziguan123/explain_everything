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


def _read_thinking_enabled() -> bool:
    """Phase 19 Task 5: LLM_THINKING_DISABLED env → 控 anthropic extended thinking.

    默 enable. 接受 "1" / "true" / "yes" (大小写不敏感) 视作 disable.
    其他值 (空 string / 任意) → enable.

    openai 协议 reasoning_content 是 vendor 返字段, 客户端无 disable 控,
    本 env 仅影响 anthropic provider 构造时是否传 thinking call_kwargs.
    """
    raw = os.environ.get("LLM_THINKING_DISABLED", "").strip().lower()
    return raw not in ("1", "true", "yes")


def _read_read_timeout_env() -> float:
    """Phase 20.0 Task 1 follow-up: LLM_READ_TIMEOUT_S env validation.

    Default 120.0s. Empty / unset → 120.0. Invalid (non-float) / non-positive
    → ValueError with env name + offending value in msg (跟 LLM_MAX_TOKENS
    现有 validation pattern 一致).
    """
    raw = os.environ.get("LLM_READ_TIMEOUT_S")
    if raw is None or raw == "":
        return 120.0
    try:
        val = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"LLM_READ_TIMEOUT_S must be a positive number, got {raw!r}: {exc}"
        ) from exc
    if val <= 0:
        raise ValueError(f"LLM_READ_TIMEOUT_S must be > 0, got {val}")
    return val


def _read_splash_pause_env() -> float:
    """Phase 20.1 #5: LLM_SPLASH_PAUSE_S env validation.

    Default 5.0s (保 Wave 7 决策让 user 真看见 splash). 0 允许 (user 主动跳).
    Negative / non-float → ValueError with env name + offending value
    (跟 _read_read_timeout_env 同 validation pattern).
    """
    raw = os.environ.get("LLM_SPLASH_PAUSE_S")
    if raw is None or raw == "":
        return 5.0
    try:
        val = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"LLM_SPLASH_PAUSE_S must be a non-negative number, got {raw!r}: {exc}"
        ) from exc
    if val < 0:
        raise ValueError(f"LLM_SPLASH_PAUSE_S must be >= 0, got {val}")
    return val


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


def _build_client(
    *,
    protocol: str,
    base_url: str,
    api_key: str,
    model: str,
    max_tokens: int | None,
    structured_output_mode: str,
    enable_thinking: bool,
    read_timeout: float,
) -> LLMClient:
    """从已解析的配置 dict 构造 client (anthropic / openai 路由)。

    Phase 20.4: 抽出来给 env 路径 + llm_config profile 路径共用 (DRY)。
    enable_thinking / read_timeout 仍是 env 级 knob (LLM_THINKING_DISABLED /
    LLM_READ_TIMEOUT_S), 不进 profile — 跟 provider 凭据正交。
    """
    if protocol == "anthropic":
        return AnthropicProtocolClient(
            api_key=api_key,
            default_model=model,
            base_url=base_url,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
            read_timeout=read_timeout,
        )
    if protocol == "openai":
        if structured_output_mode not in ("json_schema", "json_object"):
            raise ValueError(
                f"structured_output_mode must be 'json_schema' or 'json_object', "
                f"got {structured_output_mode!r}"
            )
        mode: Mode = structured_output_mode  # type: ignore[assignment]
        return OpenAIProtocolClient(
            api_key=api_key,
            default_model=model,
            base_url=base_url,
            mode=mode,
            max_tokens=max_tokens,
            read_timeout=read_timeout,
        )
    raise ValueError(
        f"Unknown protocol: {protocol!r}, must be 'anthropic' or 'openai'"
    )


def make_llm_client() -> LLMClient:
    """构造主 LLM client。

    Phase 20.4: 优先用 EXPLAIN_HOME/llm_config.json 的 active profile;
    无 (文件缺 / active=None) → fallback 现有 .env / env 路径 (向后兼容)。

    自动加载 .env (override=False, 已设的 env var 优先 — 让 test monkeypatch 生效)。

    必填 env (无 active profile 时):
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
    enable_thinking = _read_thinking_enabled()
    # Phase 20.0 Layer A: LLM_READ_TIMEOUT_S env knob (default 120.0s) 防
    # streaming chunk gap 永等. 透传给 protocol client → AsyncAnthropic/OpenAI
    # httpx.Timeout(read=...).
    read_timeout = _read_read_timeout_env()

    # Phase 20.4: active profile 优先 (用户在 app 内管理的配置)。
    from explain_engine.llm.llm_config import LLMConfigStore

    profile = LLMConfigStore().active_profile()
    if profile is not None:
        return _build_client(
            **profile.main_config(),
            enable_thinking=enable_thinking,
            read_timeout=read_timeout,
        )

    # 无 active profile → 现有 env 路径 (向后兼容)。
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

    mode_str = os.environ.get("LLM_STRUCTURED_OUTPUT_MODE", "json_schema")
    return _build_client(
        protocol=proto,
        base_url=base_url,
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        structured_output_mode=mode_str,
        enable_thinking=enable_thinking,
        read_timeout=read_timeout,
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

    Phase 20.4: active profile 优先 — profile 的 light_* 缺省 fallback 该 profile
    主字段 (跟 LLM_LIGHT_* fallback LLM_* 语义一致)。无 active profile → env 路径。
    """
    enable_thinking = _read_thinking_enabled()
    read_timeout = _read_read_timeout_env()

    # Phase 20.4: active profile 优先。
    from explain_engine.llm.llm_config import LLMConfigStore

    profile = LLMConfigStore().active_profile()
    if profile is not None:
        return _build_client(
            **profile.light_config(),
            enable_thinking=enable_thinking,
            read_timeout=read_timeout,
        )

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

    mode_str = os.environ.get("LLM_STRUCTURED_OUTPUT_MODE", "json_schema")
    try:
        return _build_client(
            protocol=proto,
            base_url=base_url,
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            structured_output_mode=mode_str,
            enable_thinking=enable_thinking,
            read_timeout=read_timeout,
        )
    except ValueError as exc:
        # 保留原 LIGHT-specific 错误措辞 (proto 来自 LLM_LIGHT_PROTOCOL or LLM_PROTOCOL)
        raise ValueError(
            f"Unknown LLM_LIGHT_PROTOCOL (or LLM_PROTOCOL): {proto!r}, "
            f"must be 'anthropic' or 'openai'"
        ) from exc
