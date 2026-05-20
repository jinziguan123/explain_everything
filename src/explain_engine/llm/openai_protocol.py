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

from explain_engine.llm.client import Message, Response, ToolsResponse
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

    async def chat_with_tools(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str | None = None,
    ) -> ToolsResponse:
        """Phase 9 Wave F.3: OpenAI function-calling for chat agent loop.

        Args:
            system: system prompt (OpenAI 走 messages[0] role=system, 无独立参数)
            messages: list of {role, content} (Anthropic-style; 当前仅支持
                      content=str. Anthropic-style list content 多轮 tool 对话
                      暂不支持, 见已知 limitations)
            tools: Anthropic-style tool schemas [{name, description, input_schema}]
                   - 内部翻译成 OpenAI function tools
                     [{type: "function", function: {name, description, parameters}}]
            model: optional override

        Returns:
            ToolsResponse(text, tool_uses, stop_reason)
        """
        try:
            # 翻译 Anthropic-style tools → OpenAI function tools
            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    },
                }
                for t in tools
            ]
            # OpenAI 把 system 放 messages[0], 无独立 system 参数
            openai_messages = [{"role": "system", "content": system}, *messages]

            call_kwargs: dict[str, Any] = {
                "model": model or self._default_model,
                "messages": openai_messages,
                "max_tokens": 8192,
            }
            if openai_tools:
                call_kwargs["tools"] = openai_tools
                call_kwargs["tool_choice"] = "auto"

            api_resp = await self._client.chat.completions.create(**call_kwargs)

            choice = api_resp.choices[0]
            text = choice.message.content or ""
            tool_uses: list[dict[str, Any]] = []
            for tc in choice.message.tool_calls or []:
                try:
                    args_dict = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    # vendor 偶尔返非法 JSON; 不抛, 走空 dict (caller 容错)
                    args_dict = {}
                tool_uses.append(
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "input": args_dict,
                    }
                )
            return ToolsResponse(
                text=text,
                tool_uses=tool_uses,
                stop_reason=choice.finish_reason or "",
            )
        except (APIConnectionError, APITimeoutError, RateLimitError, APIError) as exc:
            raise LLMError(str(exc)) from exc
