"""Phase 8 housekeeping: shared generic LLM retry helper.

Extracted from expansion.py + input_validation.py (Wave 1.1 + 3.1 reviewed).
Engines with extra post-validation hooks (compression / intervention_parser) keep
their own versions; this is for pure-Pydantic-validate-then-return path.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError

T = TypeVar("T", bound=BaseModel)


async def call_with_retry(
    llm: LLMClient,
    messages: list[Message],
    output_model: type[T],
    error_prefix: str = "LLM 输出 schema 不合规",
) -> T:
    """Generic 1-retry LLM call (= 2 attempts total) with Pydantic validation.

    Args:
        llm: LLM client.
        messages: chat messages.
        output_model: Pydantic class for structured output validation.
        error_prefix: prefix for SchemaValidationError message (let callers add context).

    Returns:
        Validated model instance.

    Raises:
        SchemaValidationError: 2 attempts both fail (LLM returned None parsed
        OR Pydantic validation error on parsed dict).
    """
    last_exc: Exception | None = None
    for _attempt in range(2):
        resp = await llm.chat(messages, schema=output_model)
        if resp.parsed is None:
            last_exc = SchemaValidationError("LLM 未返回 structured output")
            continue
        try:
            return output_model.model_validate(resp.parsed)
        except ValidationError as exc:
            last_exc = SchemaValidationError(f"{error_prefix}: {exc}")
            continue
    assert last_exc is not None
    raise last_exc
