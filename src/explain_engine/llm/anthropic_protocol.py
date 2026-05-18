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
        except (APIConnectionError, APITimeoutError, RateLimitError, APIError) as exc:
            raise LLMError(str(exc)) from exc
        except ValidationError as exc:
            raise SchemaValidationError(str(exc)) from exc

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
