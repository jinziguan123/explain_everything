"""LLMClient Protocol + 基础类型。

三个 provider (Claude / OpenAI / DeepSeek) 都实现这个 Protocol。
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str


class Response(BaseModel):
    text: str
    reasoning: str | None = None  # Phase 19: extended thinking / reasoning_content
    parsed: dict[str, Any] | None
    model: str
    usage: dict[str, int]


@dataclass
class ToolsResponse:
    """Phase 9 Wave F.3/F.4: facade for chat_with_tools across providers.

    解耦 SDK 升级风险 — query_loop 只认 (text, tool_uses, stop_reason) 三字段,
    不直接吃 raw SDK Message 对象.

    Fields:
        text: concat 所有 TextBlock.text in Anthropic Message.content
              (OpenAI 等同 choices[0].message.content)
        tool_uses: list of {id, name, input(dict)}
                   (对应 Anthropic ToolUseBlock / OpenAI ToolCall)
        stop_reason: forward 原值 ("end_turn", "tool_use", "max_tokens" /
                     OpenAI "stop", "tool_calls", "length")
        raw_content_blocks: F.4 — preserve raw SDK content blocks for round-trip
                            to reasoning models (deepseek-reasoner / Claude
                            extended thinking require their thinking blocks be
                            echoed in subsequent API calls).
    """

    text: str = ""
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    raw_content_blocks: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class LLMClient(Protocol):
    """统一的 LLM 调用接口。

    每个 provider 实现 `chat` (structured output) + `chat_with_tools`
    (Phase 9 chat agent loop). `schema` 不为 None 时启用 structured
    output (provider 内部选择 tools / response_format / JSON mode)。
    """

    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str | None = None,
    ) -> Response: ...

    async def chat_with_tools(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str | None = None,
    ) -> ToolsResponse: ...
