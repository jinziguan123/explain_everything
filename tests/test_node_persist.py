from datetime import date
from unittest.mock import MagicMock
import pytest
from explain_agent.graph.state import new_attribution_state
from explain_agent.graph.nodes.persist import persist_node


@pytest.mark.asyncio
async def test_persist_writes_session_and_tree():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    state = new_attribution_state("为什么半导体涨", session_id="s_test_123")
    state["domain_id"] = "cn_equity_sector_attribution"
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["narrative"] = "半导体涨停因 ..."
    state["dimension_reports"] = {"policy": "...", "industry_chain": "..."}
    state["citations"] = []
    state["confidence"] = "high"
    state["total_cost"] = 1.5

    out = await persist_node(state, engine=mock_engine)
    assert out["session_id"] == "s_test_123"
    assert mock_conn.exec_driver_sql.call_count >= 2
