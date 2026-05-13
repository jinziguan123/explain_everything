"""OpenAI provider 实现。

Structured output 走 response_format={"type": "json_schema", ...}。
"""

import json
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel

from explain_engine.llm.client import Message, Response


class OpenAIClient:
    def __init__(
        self,
        api_key: str,
        default_model: str,
        base_url: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)
        self._default_model = default_model

    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str | None = None,
    ) -> Response:
        call_kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }

        if schema is not None:
            call_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            }

        api_resp = await self._client.chat.completions.create(**call_kwargs)

        text = api_resp.choices[0].message.content or ""
        parsed: dict[str, Any] | None = None
        if schema is not None and text:
            parsed = json.loads(text)

        return Response(
            text=text,
            parsed=parsed,
            model=api_resp.model,
            usage={
                "input_tokens": api_resp.usage.prompt_tokens,
                "output_tokens": api_resp.usage.completion_tokens,
            },
        )
