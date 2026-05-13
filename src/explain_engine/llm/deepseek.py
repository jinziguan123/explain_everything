"""DeepSeek provider 实现。

DeepSeek 用 OpenAI 兼容 API。structured output 走 JSON mode +
prompt 注入 schema 描述 —— 不支持 json_schema strict 模式。
"""

import json
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel

from explain_engine.llm.client import Message, Response


def _schema_instructions(schema: type[BaseModel]) -> str:
    json_schema = schema.model_json_schema()
    return (
        f"You MUST respond with a single JSON object matching schema "
        f"{schema.__name__}:\n```json\n{json.dumps(json_schema, indent=2)}\n```\n"
        f"Do not include any explanation outside the JSON."
    )


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        default_model: str,
        base_url: str,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._default_model = default_model

    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str | None = None,
    ) -> Response:
        api_messages: list[dict[str, str]] = [
            {"role": m.role, "content": m.content} for m in messages
        ]

        call_kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": api_messages,
        }

        if schema is not None:
            schema_text = _schema_instructions(schema)
            if api_messages and api_messages[0]["role"] == "system":
                api_messages[0] = {
                    "role": "system",
                    "content": schema_text + "\n\n" + api_messages[0]["content"],
                }
            else:
                api_messages.insert(0, {"role": "system", "content": schema_text})
            call_kwargs["messages"] = api_messages
            call_kwargs["response_format"] = {"type": "json_object"}

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
