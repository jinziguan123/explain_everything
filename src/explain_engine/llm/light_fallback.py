"""light_llm + 主 LLM auto-fallback helper — Phase 17.2 Feature B.

light 失败 (LLMError / SchemaValidationError) → 自动 fallback 主 LLM,
log warning. Caller 零感知.

Use case: classify / canonical_mechanism 这类小 LLM call 走 cheap model
(haiku / gpt-4o-mini), 失败时 (网络 / model 输出不 fit schema) 自动用主 LLM
重跑一次保证 caller 收到合法 result.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from explain_engine.llm.client import LLMClient
from explain_engine.llm.errors import LLMError, SchemaValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def with_light_fallback(
    light_llm: LLMClient,
    main_llm: LLMClient,
    fn: Callable[[LLMClient], Awaitable[T]],
) -> T:
    """跑 fn(light_llm), 失败 (LLM / Schema 错) 自动 fallback fn(main_llm).

    若 light is main (same client instance), 直接 fn(main_llm) — 无 fallback,
    避免重复打同一 client.
    """
    if light_llm is main_llm:
        return await fn(main_llm)

    try:
        return await fn(light_llm)
    except (LLMError, SchemaValidationError) as exc:
        logger.warning(
            "light_llm 失败 (model=%s, %s: %s), fallback 主 LLM",
            getattr(light_llm, "_default_model", "?"),
            type(exc).__name__,
            exc,
        )
        return await fn(main_llm)
