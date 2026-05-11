from datetime import date
from unittest.mock import AsyncMock, MagicMock
import pytest
from explain_agent.graph.state import new_attribution_state, DimensionResult
from explain_agent.graph.nodes.dynamic_subbranches import dynamic_subbranches_node


@pytest.mark.asyncio
async def test_dynamic_subbranches_runs_each_spec():
    state = new_attribution_state("test")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["framework"] = {
        "worker_config": {"max_rounds": 1, "soft_terminate_no_gain_rounds": 1, "max_concurrency": 2}
    }
    state["needs_subbranch"] = True
    state["subbranches"] = [
        {"name": "HBM 制裁", "query_hints": ["BIS", "HBM"]},
        {"name": "国产替代", "query_hints": ["晶圆"]},
    ]

    fake_worker = MagicMock()
    fake_worker.run = AsyncMock(return_value=DimensionResult(
        evidence=[], mini_summary="子分支结果", retry_count=1, no_data=False, confidence="medium",
    ))
    fake_factory = MagicMock(return_value=fake_worker)

    out = await dynamic_subbranches_node(state, worker_factory=fake_factory)
    assert set(out["subbranch_results"].keys()) == {"HBM 制裁", "国产替代"}


@pytest.mark.asyncio
async def test_dynamic_subbranches_skips_when_not_needed():
    state = new_attribution_state("test")
    state["needs_subbranch"] = False
    state["subbranches"] = []
    state["framework"] = {"worker_config": {}}

    fake_factory = MagicMock()
    out = await dynamic_subbranches_node(state, worker_factory=fake_factory)
    assert out == {"subbranch_results": {}}
    fake_factory.assert_not_called()
