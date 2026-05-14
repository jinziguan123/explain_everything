"""OpenAI 协议 client (跨 vendor: OpenAI / DeepSeek openai / Azure / Together / Groq)。

Phase 5 起取代 OpenAIClient + DeepSeekClient,通过 base_url + mode 解耦。

Structured output mode:
- 'json_schema': 用 response_format={"type":"json_schema", ...} strict (OpenAI 官方等)
- 'json_object': 用 response_format={"type":"json_object"} + prompt 注入 schema
   (DeepSeek 等不支持 json_schema strict 的 vendor 用)
"""

import json
from typing import Any, Literal

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from explain_engine.llm.client import Message, Response
from explain_engine.llm.errors import LLMError, SchemaValidationError

Mode = Literal["json_schema", "json_object"]


def _schema_instructions(schema: type[BaseModel]) -> str:
    json_schema = schema.model_json_schema()
    return (
        f"You MUST respond with a single JSON object matching schema "
        f"{schema.__name__}:\n```json\n{json.dumps(json_schema, indent=2)}\n```\n"
        f"Do not include any explanation outside the JSON."
    )


class OpenAIProtocolClient:
    def __init__(
        self,
        api_key: str,
        default_model: str,
        base_url: str | None = None,
        mode: Mode = "json_schema",
    ) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)
        self._default_model = default_model
        self._mode = mode

    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str | None = None,
    ) -> Response:
        try:
            api_messages: list[dict[str, str]] = [
                {"role": m.role, "content": m.content} for m in messages
            ]
            call_kwargs: dict[str, Any] = {
                "model": model or self._default_model,
                "messages": api_messages,
            }

            if schema is not None:
                if self._mode == "json_schema":
                    call_kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema.__name__,
                            "schema": schema.model_json_schema(),
                            "strict": True,
                        },
                    }
                else:  # json_object
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
        except (APIConnectionError, APITimeoutError, RateLimitError, APIError) as exc:
            raise LLMError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"invalid JSON in response: {exc}") from exc
        except ValidationError as exc:
            raise SchemaValidationError(str(exc)) from exc
