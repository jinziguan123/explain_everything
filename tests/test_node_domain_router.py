import pytest
from datetime import date
from explain_agent.graph.state import new_attribution_state
from explain_agent.graph.nodes.domain_router import domain_router_node


@pytest.mark.asyncio
async def test_router_matches_attribution_pattern():
    state = new_attribution_state("为什么半导体板块今天涨停")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["intent"] = "up"

    out = await domain_router_node(state)
    assert out["domain_id"] == "cn_equity_sector_attribution"


@pytest.mark.asyncio
async def test_router_falls_back_when_no_match():
    state = new_attribution_state("今天天气真好")
    state["target"] = "天气"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["intent"] = "general"

    out = await domain_router_node(state)
    assert out["domain_id"] == "cn_equity_sector_attribution"
