from datetime import date
from unittest.mock import patch
import pandas as pd
import pytest
from explain_agent.core.types import AdapterQuery
from explain_agent.adapters.akshare_capital_flow import AkshareCapitalFlowAdapter


@pytest.mark.asyncio
async def test_industry_main_flow_evidence():
    mock_df = pd.DataFrame(
        {
            "日期": ["2026-05-09", "2026-05-08"],
            "主力净流入-净额": [1.2e9, -8.5e8],
            "主力净流入-净占比": [4.5, -3.2],
        }
    )
    with patch("akshare.stock_sector_fund_flow_hist", return_value=mock_df):
        adapter = AkshareCapitalFlowAdapter()
        q = AdapterQuery(
            keywords=["半导体"],
            time_window=(date(2026, 5, 1), date(2026, 5, 11)),
            target="半导体",
        )
        out = await adapter.query(q)
    assert len(out) >= 1
    assert "主力" in out[0].snippet
