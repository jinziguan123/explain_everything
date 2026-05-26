"""_classify_question — Phase 17.2 Task 12+13."""

from unittest.mock import AsyncMock

from explain_engine.engines.bootstrap import _classify_question
from explain_engine.llm.client import Response


def _mock_classify_resp(type_str: str | None) -> Response:
    """构造 light_llm.chat 返的 Response (parsed=dict 或 None)."""
    return Response(
        text="",
        parsed={"type": type_str} if type_str is not None else None,
        model="mock-light",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


async def test_classify_returns_causal_modern():
    light = AsyncMock()
    main = AsyncMock()
    light.chat = AsyncMock(return_value=_mock_classify_resp("causal_modern"))

    result = await _classify_question("为什么年轻人不消费", light, main)
    assert result == "causal_modern"
    assert light.chat.call_count == 1
    assert main.chat.call_count == 0


async def test_classify_returns_concept_explanation():
    light = AsyncMock()
    main = AsyncMock()
    light.chat = AsyncMock(return_value=_mock_classify_resp("concept_explanation"))

    result = await _classify_question("为什么易经里有用九用六", light, main)
    assert result == "concept_explanation"


async def test_classify_returns_mechanism():
    light = AsyncMock()
    main = AsyncMock()
    light.chat = AsyncMock(return_value=_mock_classify_resp("mechanism"))

    result = await _classify_question("为什么 Raft 用 majority quorum", light, main)
    assert result == "mechanism"


async def test_classify_returns_phenomenon():
    light = AsyncMock()
    main = AsyncMock()
    light.chat = AsyncMock(return_value=_mock_classify_resp("phenomenon"))

    result = await _classify_question("为什么彩虹有七色", light, main)
    assert result == "phenomenon"


# ----- Task 13: fallback paths -----


async def test_classify_unknown_type_fallback_causal_modern():
    """light 返奇怪 type → fallback causal_modern."""
    light = AsyncMock()
    main = AsyncMock()
    light.chat = AsyncMock(return_value=_mock_classify_resp("weirdcategory"))

    result = await _classify_question("anything", light, main)
    assert result == "causal_modern"


async def test_classify_parsed_none_fallback():
    """light 返 parsed=None → fallback causal_modern."""
    light = AsyncMock()
    main = AsyncMock()
    light.chat = AsyncMock(return_value=_mock_classify_resp(None))

    result = await _classify_question("anything", light, main)
    assert result == "causal_modern"


async def test_classify_total_failure_fallback():
    """light + main 都 raise LLMError → fallback causal_modern (永不挂)."""
    from explain_engine.llm.errors import LLMError

    light = AsyncMock()
    main = AsyncMock()
    light.chat = AsyncMock(side_effect=LLMError("net down"))
    main.chat = AsyncMock(side_effect=LLMError("net down"))

    result = await _classify_question("anything", light, main)
    assert result == "causal_modern"
    assert light.chat.call_count == 1
    assert main.chat.call_count == 1
