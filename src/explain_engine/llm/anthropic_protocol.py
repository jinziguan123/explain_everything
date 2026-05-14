"""Anthropic 协议 client (跨 vendor: Anthropic 官方 / DeepSeek anthropic / Bedrock / Vertex)。

Phase 5 起取代 ClaudeClient，通过 base_url 解耦协议与供应商。
Structured output 走 tools API。
"""

from typing import Any

from anthropic import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncAnthropic,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from explain_engine.llm.client import Message, Response
from explain_engine.llm.errors import LLMError, SchemaValidationError


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

            api_resp = await self._client.messages.create(**call_kwargs)

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
