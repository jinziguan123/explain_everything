"""LLMClient Protocol + 基础类型。

三个 provider (Claude / OpenAI / DeepSeek) 都实现这个 Protocol。
"""

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str


class Response(BaseModel):
    text: str
    parsed: dict[str, Any] | None
    model: str
    usage: dict[str, int]


@runtime_checkable
class LLMClient(Protocol):
    """统一的 LLM 调用接口。

    每个 provider 实现 `chat`。`schema` 不为 None 时启用 structured
    output (provider 内部选择 tools / response_format / JSON mode)。
    """

    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str | None = None,
    ) -> Response: ...
