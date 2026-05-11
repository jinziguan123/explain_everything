from datetime import date
from unittest.mock import MagicMock
import pytest
from explain_agent.core.types import AdapterQuery
from explain_agent.adapters.clickhouse_market import ClickHouseMarketAdapter


@pytest.mark.asyncio
async def test_query_returns_market_evidence_for_industry():
    mock_ch = MagicMock()
    mock_ch.query.return_value.result_rows = [
        (1001, "中芯国际", 8.32, 1.2e9, 1),
        (1002, "韦尔股份", 6.10, 5.4e8, 0),
    ]
    mock_mysql_resolver = MagicMock()
    mock_mysql_resolver.resolve_industry_symbols.return_value = [1001, 1002]

    adapter = ClickHouseMarketAdapter(
        ch_client=mock_ch,
        industry_resolver=mock_mysql_resolver,
    )
    q = AdapterQuery(
        keywords=["半导体"],
        time_window=(date(2026, 5, 1), date(2026, 5, 11)),
        target="半导体",
    )
    out = await adapter.query(q)
    assert len(out) >= 1
    assert out[0].source_type == "market_data"
