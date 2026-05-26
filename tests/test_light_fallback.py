"""with_light_fallback helper — Phase 17.2 Task 3+."""

import logging

import pytest

from explain_engine.llm.errors import LLMError, SchemaValidationError
from explain_engine.llm.light_fallback import with_light_fallback


class _StubClient:
    def __init__(self, name: str, raises: Exception | None = None):
        self.name = name
        self.raises = raises
        self.call_count = 0

    async def chat(self, *args, **kwargs):
        self.call_count += 1
        if self.raises:
            raise self.raises
        return f"response-from-{self.name}"


@pytest.mark.asyncio
async def test_with_light_fallback_light_success_skips_main():
    """light 成功 → 不调主 LLM."""
    light = _StubClient("light")
    main = _StubClient("main")

    result = await with_light_fallback(
        light, main, lambda llm: llm.chat("hi")
    )

    assert result == "response-from-light"
    assert light.call_count == 1
    assert main.call_count == 0


@pytest.mark.asyncio
async def test_with_light_fallback_light_llm_error_falls_back(caplog):
    """light raise LLMError → 调主 + log warning."""
    caplog.set_level(logging.WARNING, logger="explain_engine.llm.light_fallback")
    light = _StubClient("light", raises=LLMError("connection refused"))
    main = _StubClient("main")

    result = await with_light_fallback(
        light, main, lambda llm: llm.chat("hi")
    )

    assert result == "response-from-main"
    assert light.call_count == 1
    assert main.call_count == 1
    assert any("light_llm 失败" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_with_light_fallback_schema_error_falls_back():
    """light raise SchemaValidationError (返垃圾 JSON) → 调主 重试."""
    light = _StubClient("light", raises=SchemaValidationError("missing field"))
    main = _StubClient("main")

    result = await with_light_fallback(
        light, main, lambda llm: llm.chat("hi")
    )

    assert result == "response-from-main"
    assert light.call_count == 1
    assert main.call_count == 1


@pytest.mark.asyncio
async def test_with_light_fallback_skip_when_same_client():
    """light is main (同 client 实例) → 跳 fallback 逻辑, 仅调 1 次."""
    main = _StubClient("main")

    result = await with_light_fallback(
        main, main, lambda llm: llm.chat("hi")
    )

    assert result == "response-from-main"
    assert main.call_count == 1
