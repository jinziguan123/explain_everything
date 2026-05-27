"""Anthropic 协议 client (跨 vendor: Anthropic 官方 / DeepSeek anthropic / Bedrock / Vertex)。

Phase 5 起取代 ClaudeClient，通过 base_url 解耦协议与供应商。
Structured output 走 tools API。

Phase 13 hotfix #4 (2026-05-20): 所有 LLM 调用走 messages.stream() 而非
messages.create(). 原因: anthropic SDK 在 non-streaming 模式下对 max_tokens
有硬限制 (general formula expected_time = 3600 * max_tokens / 128000 > 600s
即 max_tokens > 21333 reject; 部分 model_cap e.g. claude-opus-4 = 8192).
deepseek-v4-pro via anthropic-compatible endpoint 实际能输出 384K, 但被
SDK 限到 21333. 即使 SDK 不 reject (16384 < 21333), LLM 输出本身仍被
max_tokens 截断 (mid-JSON 断在 Phase 13 propose_candidates 实测撞过).
stream API 没有这个 SDK-side cap, 解锁 model 真实输出上限.

下游解析逻辑不变 — get_final_message() 返回的 Message 对象跟 create() 同形.
"""

import json
import logging
from typing import Any

from anthropic import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncAnthropic,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from explain_engine.llm.client import Message, Response, ToolsResponse
from explain_engine.llm.errors import LLMError, SchemaValidationError

logger = logging.getLogger(__name__)

# Phase 11 Wave 0: deepseek-v4-pro forced→auto fallback 后偶尔返 free text
# (parsed=None). retry 2 次, append reminder. 总 3 次调用.
#
# Fix 5 (2026-05-19 smoke 扩 scope): 本 retry 现 cover 两种 malformed:
# 1. parsed is None — LLM auto fallback 后返 free text (Wave 0 原 case)
# 2. parsed is empty dict `{}` — LLM 返 valid tool_use 但 input 空 (production
#    deepseek 撞到, 之前 yaml prompt 给了 "return empty object" escape hatch 制造)
# 仍由 caller (engines/_llm_retry) outer 处理 schema-shape mismatch
# (字段类型错等). 两层 layered defense.
#
# Phase 13 hotfix #5 (2026-05-20): 加 free-text JSON fallback (见
# _try_parse_free_text_json_dict). LLM 拒 tool_use 直接返 JSON 在 text block
# 时, 接受为 parsed 等价输出. 不再触发上面 retry 链路 (因为 deepseek-v4-pro
# 实测 reminder 不管用, 它就是想用 text 写 JSON).
MAX_RETRIES_ON_MALFORMED = 2
_REMINDER_MSG = (
    "Previous response was not valid JSON matching the requested schema. "
    "Please respond with ONLY valid JSON, no markdown code fences, "
    "no explanation, no preamble."
)


def _try_parse_free_text_json_dict(text: str) -> dict[str, Any] | None:
    """Phase 13 hotfix #5: free-text JSON fallback.

    部分 model (e.g. deepseek-v4-pro via anthropic-compatible endpoint) 在
    forced tool_choice 被 vendor reject 后 fallback auto, 选择直接把 JSON
    写在 text block 而非 tool_use block. reminder retry 无效 — 它就是不用
    tool_use. 本 helper 把 text 当 free-text JSON parse:
    - strip 周围空白 / 常见 markdown fence (```json / ``` / 任意 lang)
    - json.loads → 必须 dict (array / scalar JSON 也不行, 我们语义需要 dict)
    - 失败 (parse error / 非 dict) → 返 None, caller 走原 retry 路径

    schema 校验留给 chat() 外层 retry check (`parsed is not None and parsed`
    truthy 即接受, caller 下游 pydantic 做严格 shape 验证). 跟 tool_use
    block.input 的处理一致, 保持两条路径语义对称.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # 跳第一行 (```json / ```yaml / 裸 ```)
        first_nl = cleaned.find("\n")
        if first_nl > 0:
            cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


class AnthropicProtocolClient:
    DEFAULT_MAX_TOKENS = 16384
    """Phase 13 hotfix #3 (2026-05-20): 默认 16384 (deepseek-v4-pro 支持
    1M context + 384K output, 16K 安全且能容详细中文 JSON ~10K char).
    可通过 LLM_MAX_TOKENS env / 构造 max_tokens kwarg 覆盖.
    """

    THINKING_BUDGET_TOKENS = 4096
    """Phase 19 Task 3: extended thinking budget (claude-opus / deepseek-reasoner
    via anthropic-compat). default 4096 — 折中 cost / 推理深度."""

    def __init__(
        self,
        api_key: str,
        default_model: str,
        base_url: str | None = None,
        max_tokens: int | None = None,
        enable_thinking: bool = True,
    ) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**kwargs)
        self._default_model = default_model
        self._max_tokens = max_tokens if max_tokens is not None else self.DEFAULT_MAX_TOKENS
        # Phase 19 Task 3: enable extended thinking by default.
        # True → call_kwargs["thinking"] = {type:enabled, budget_tokens:4096}.
        # False → omit (省 token, vendor 不出 thinking blocks).
        # config.py make_llm_client / make_light_llm_client 读 LLM_THINKING_DISABLED
        # env 决定传 False (默 True).
        self._enable_thinking = enable_thinking

    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str | None = None,
    ) -> Response:
        try:
            # Phase 11 Wave 0: retry loop on malformed structured output.
            # schema=None 时只跑 1 次 (无 retry). schema 非 None 且 parsed=None
            # (forced→auto fallback 后 LLM 返 free text) → append reminder retry.
            current_messages = list(messages)
            last_response: Response | None = None
            for attempt in range(MAX_RETRIES_ON_MALFORMED + 1):
                last_response = await self._single_chat_call(
                    current_messages, schema=schema, model=model
                )
                # Fix 5 (2026-05-19): cover empty dict / list 也算 malformed.
                # `parsed is not None and parsed` 同时排 None + falsy (e.g. {}, [], "")
                if schema is None or (
                    last_response.parsed is not None and last_response.parsed
                ):
                    return last_response
                # malformed: parsed=None 或 empty 而 schema 要求 structured output
                if attempt < MAX_RETRIES_ON_MALFORMED:
                    logger.warning(
                        "LLM response malformed (attempt %d/%d, model=%s). "
                        "Raw preview: %s. Retrying with JSON-only reminder.",
                        attempt + 1,
                        MAX_RETRIES_ON_MALFORMED + 1,
                        model or self._default_model,
                        (last_response.text or "")[:200],
                    )
                    current_messages = [
                        *current_messages,
                        Message(role="user", content=_REMINDER_MSG),
                    ]
                    continue
            # max retries exhausted — raise with raw text preview for diagnostics
            assert last_response is not None
            raw_preview = (last_response.text or "")[:500]
            raise SchemaValidationError(
                f"LLM returned malformed (no structured output) after "
                f"{MAX_RETRIES_ON_MALFORMED + 1} attempts. "
                f"Raw text preview: {raw_preview}"
            )
        except (APIConnectionError, APITimeoutError, RateLimitError, APIError) as exc:
            raise LLMError(str(exc)) from exc
        except ValidationError as exc:
            raise SchemaValidationError(str(exc)) from exc

    async def _single_chat_call(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None,
        model: str | None,
    ) -> Response:
        # 拆 system message（Anthropic API 单独传 system）
        system_text: str | None = None
        chat_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_text = (
                    (system_text + "\n\n" if system_text else "") + m.content
                )
            else:
                chat_messages.append({"role": m.role, "content": m.content})

        call_kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "max_tokens": self._max_tokens,
            "messages": chat_messages,
        }
        if system_text:
            call_kwargs["system"] = system_text

        # Phase 19 Task 3: extended thinking — vendor 返 thinking block 由
        # _single_chat_call 解析填 Response.reasoning (见 Task 2 实装).
        if self._enable_thinking:
            call_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.THINKING_BUDGET_TOKENS,
            }

        if schema is not None:
            tool_name = schema.__name__
            call_kwargs["tools"] = [
                {
                    "name": tool_name,
                    "description": schema.__doc__ or f"Structured output: {tool_name}",
                    "input_schema": schema.model_json_schema(),
                }
            ]
            call_kwargs["tool_choice"] = {"type": "tool", "name": tool_name}

        try:
            async with self._client.messages.stream(**call_kwargs) as stream:
                api_resp = await stream.get_final_message()
        except APIError as exc:
            # Vendor-specific: some reasoning models (deepseek-reasoner, o1, etc.)
            # reject forced tool_choice. Retry with "auto" and let LLM decide.
            err_msg = str(exc).lower()
            forced = (call_kwargs.get("tool_choice", {}).get("type") == "tool")
            if forced and "tool_choice" in err_msg:
                logger.warning(
                    "Forced tool_choice rejected by model (%s); retrying with auto. "
                    "Note: LLM may return free text instead of structured output; "
                    "Pydantic validation downstream will catch malformed responses.",
                    model or self._default_model,
                )
                call_kwargs["tool_choice"] = {"type": "auto"}
                async with self._client.messages.stream(**call_kwargs) as stream:
                    api_resp = await stream.get_final_message()
            else:
                raise

        text = ""
        parsed: dict[str, Any] | None = None
        thinking_parts: list[str] = []
        for block in api_resp.content:
            if block.type == "tool_use":
                parsed = dict(block.input)
            elif block.type == "text":
                text += block.text
            elif block.type == "thinking":
                # Phase 19 Task 2: chat() path 对称收 thinking block →
                # Response.reasoning. 现 chat_with_tools 已收到
                # ToolsResponse.raw_content_blocks (Phase 9 Wave F.4),
                # 普通 chat() 走 structured output 也需暴露.
                thinking_parts.append(getattr(block, "thinking", ""))
            elif block.type == "redacted_thinking":
                # Phase 19 Wave 1 review fix (C-1): chat_with_tools 已 cover, chat() 补对称.
                # vendor content policy 触发遮蔽 thinking 时返此 block (内容不可见).
                # 留 marker 字符串让 caller 知道"有但被遮", 不静默丢.
                thinking_parts.append("[redacted thinking]")

        reasoning: str | None = "".join(thinking_parts) if thinking_parts else None

        # Phase 13 hotfix #5: free-text JSON fallback. 仅当 schema 期望 structured
        # output (schema 非 None) 且 LLM 没用 tool_use (parsed=None) 但 text 非空时
        # 试图把 text 当 free-text JSON parse. 成功 → 当 parsed 等价输出, 避免老
        # retry 链路在 deepseek-v4-pro 等不听话 model 上空转 3 次后 fail.
        if parsed is None and schema is not None and text:
            fallback = _try_parse_free_text_json_dict(text)
            if fallback is not None:
                parsed = fallback
                logger.debug(
                    "Free-text JSON fallback accepted (%s): LLM returned "
                    "valid JSON in text block instead of tool_use.",
                    model or self._default_model,
                )

        return Response(
            text=text,
            reasoning=reasoning,
            parsed=parsed,
            model=api_resp.model,
            usage={
                "input_tokens": api_resp.usage.input_tokens,
                "output_tokens": api_resp.usage.output_tokens,
            },
        )

    async def chat_with_tools(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str | None = None,
    ) -> ToolsResponse:
        """Phase 9 Wave F.3: Anthropic native tool_use API for chat agent loop.

        Args:
            system: system prompt string (Anthropic API 走独立 system 参数)
            messages: list of {role, content} dicts (content 可为 str OR
                      list of blocks; query_loop 当前送 str content)
            tools: list of Anthropic tool schemas {name, description, input_schema}
            model: optional override

        Returns:
            ToolsResponse(text, tool_uses, stop_reason)
        """
        try:
            call_kwargs: dict[str, Any] = {
                "model": model or self._default_model,
                "max_tokens": self._max_tokens,
                "system": system,
                "messages": messages,
            }
            if tools:
                call_kwargs["tools"] = tools
                call_kwargs["tool_choice"] = {"type": "auto"}

            # Phase 19 Task 3: extended thinking 统一 wire (chat / chat_with_tools).
            if self._enable_thinking:
                call_kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self.THINKING_BUDGET_TOKENS,
                }

            async with self._client.messages.stream(**call_kwargs) as stream:
                api_resp = await stream.get_final_message()

            text = ""
            tool_uses: list[dict[str, Any]] = []
            raw_blocks: list[dict[str, Any]] = []
            for block in api_resp.content:
                if block.type == "text":
                    text += block.text
                    raw_blocks.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    tu = {
                        "id": block.id,
                        "name": block.name,
                        "input": dict(block.input),
                    }
                    tool_uses.append(tu)
                    raw_blocks.append({"type": "tool_use", **tu})
                elif block.type == "thinking":
                    # F.4: preserve for round-trip (deepseek-reasoner /
                    # Claude extended thinking 要求 thinking block 必须 echo
                    # 回下一轮 API 调用, 否则 vendor 报 400)
                    thinking_block: dict[str, Any] = {
                        "type": "thinking",
                        "thinking": getattr(block, "thinking", ""),
                    }
                    sig = getattr(block, "signature", None)
                    if sig:
                        thinking_block["signature"] = sig
                    raw_blocks.append(thinking_block)
                elif block.type == "redacted_thinking":
                    raw_blocks.append({
                        "type": "redacted_thinking",
                        "data": getattr(block, "data", ""),
                    })
                # Other future block types: skip (don't crash)
            return ToolsResponse(
                text=text,
                tool_uses=tool_uses,
                stop_reason=api_resp.stop_reason or "",
                raw_content_blocks=raw_blocks,
            )
        except (APIConnectionError, APITimeoutError, RateLimitError, APIError) as exc:
            raise LLMError(str(exc)) from exc
