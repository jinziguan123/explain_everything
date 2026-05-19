"""Anthropic 协议 client (跨 vendor: Anthropic 官方 / DeepSeek anthropic / Bedrock / Vertex)。

Phase 5 起取代 ClaudeClient，通过 base_url 解耦协议与供应商。
Structured output 走 tools API。
"""

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
MAX_RETRIES_ON_MALFORMED = 2
_REMINDER_MSG = (
    "Previous response was not valid JSON matching the requested schema. "
    "Please respond with ONLY valid JSON, no markdown code fences, "
    "no explanation, no preamble."
)


class AnthropicProtocolClient:
    def __init__(
        self,
        api_key: str,
        default_model: str,
        base_url: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**kwargs)
        self._default_model = default_model

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
            "max_tokens": 4096,
            "messages": chat_messages,
        }
        if system_text:
            call_kwargs["system"] = system_text

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
            api_resp = await self._client.messages.create(**call_kwargs)
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
                api_resp = await self._client.messages.create(**call_kwargs)
            else:
                raise

        text = ""
        parsed: dict[str, Any] | None = None
        for block in api_resp.content:
            if block.type == "tool_use":
                parsed = dict(block.input)
            elif block.type == "text":
                text += block.text

        return Response(
            text=text,
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
                "max_tokens": 4096,
                "system": system,
                "messages": messages,
            }
            if tools:
                call_kwargs["tools"] = tools
                call_kwargs["tool_choice"] = {"type": "auto"}

            api_resp = await self._client.messages.create(**call_kwargs)

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
