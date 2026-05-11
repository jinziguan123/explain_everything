from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from explain_agent.core.types import Evidence
from explain_agent.graph.state import new_attribution_state
from explain_agent.graph.nodes.fetch_market_facts import fetch_market_facts_node


@pytest.mark.asyncio
async def test_fetch_market_facts_calls_clickhouse_market_adapter():
    mock_adapter = MagicMock()
    mock_adapter.query = AsyncMock(return_value=[
        Evidence(
            id="e1",
            source="clickhouse_market",
            source_type="market_data",
            snippet="半导体板块龙头股: symbol_id=2332 涨跌=92.52%",
            raw_payload={"rows": [(2332, 100.0, 92.52, 1e9), (1001, 50.0, 60.20, 5e8)]},
            timestamp=datetime.now(),
            metadata={"target": "半导体", "kind": "industry_leaders"},
        )
    ])
    state = new_attribution_state("为什么半导体涨")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))

    out = await fetch_market_facts_node(state, market_adapter=mock_adapter)
    assert "raw_payload" in out["market_facts"]
    assert out["market_facts"]["target"] == "半导体"
    assert out["market_facts"]["snippet"].startswith("半导体板块龙头股")


@pytest.mark.asyncio
async def test_fetch_market_facts_handles_empty():
    mock_adapter = MagicMock()
    mock_adapter.query = AsyncMock(return_value=[])
    state = new_attribution_state("为什么 X 涨")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))

    out = await fetch_market_facts_node(state, market_adapter=mock_adapter)
    assert out["market_facts"]["raw_payload"] is None
    assert out["market_facts"]["snippet"] == ""
