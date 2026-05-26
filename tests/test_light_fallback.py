"""with_light_fallback helper — Phase 17.2 Task 3+."""

import pytest

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
