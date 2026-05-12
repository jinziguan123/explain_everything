import json
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from explain_agent.core.types import Evidence
from explain_agent.graph.state import new_attribution_state, DimensionResult
from explain_agent.graph.nodes.synthesizer import synthesizer_node


def make_ev(id: str, snippet: str) -> Evidence:
    return Evidence(
        id=id, source="x", source_type="news",
        snippet=snippet, timestamp=datetime.now(),
    )


@pytest.mark.asyncio
async def test_synthesizer_decides_subbranches():
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "needs_subbranch": True,
        "subbranches": [{"name": "HBM 制裁影响", "query_hints": ["BIS", "HBM"]}],
    }))
    state = new_attribution_state("test")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", "美国 BIS 制裁 HBM")],
            mini_summary="...", retry_count=1, no_data=False, confidence="high",
        ),
    }

    out = await synthesizer_node(state, llm=fake_llm)
    assert out["needs_subbranch"] is True
    assert len(out["subbranches"]) == 1
    assert out["subbranches"][0]["name"] == "HBM 制裁影响"


@pytest.mark.asyncio
async def test_synthesizer_caps_subbranches_at_2():
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "needs_subbranch": True,
        "subbranches": [
            {"name": "a", "query_hints": []},
            {"name": "b", "query_hints": []},
            {"name": "c", "query_hints": []},
            {"name": "d", "query_hints": []},
        ],
    }))
    state = new_attribution_state("test")
    state["target"] = "X"
    state["dimension_results"] = {}
    out = await synthesizer_node(state, llm=fake_llm)
    assert len(out["subbranches"]) == 2


@pytest.mark.asyncio
async def test_synthesizer_no_subbranches_when_llm_says_no():
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value=json.dumps({"needs_subbranch": False, "subbranches": []}))
    state = new_attribution_state("test")
    state["target"] = "X"
    state["dimension_results"] = {}
    out = await synthesizer_node(state, llm=fake_llm)
    assert out["needs_subbranch"] is False
    assert out["subbranches"] == []
