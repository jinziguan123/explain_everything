from datetime import date
from unittest.mock import MagicMock
import pytest
from explain_agent.core.types import AdapterQuery
from explain_agent.adapters.mysql_fundamentals import MySQLFundamentalsAdapter


@pytest.mark.asyncio
async def test_returns_industry_fundamentals_summary():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = [
        ("000001.SZ", 12.5, 0.18, 0.45, 1.2e8, date(2026, 3, 31)),
        ("000002.SZ", 8.3, 0.10, 0.35, 8.0e7, date(2026, 3, 31)),
    ]

    mock_resolver = MagicMock()
    mock_resolver.resolve_industry_symbols_with_codes.return_value = [
        (1001, "000001.SZ"),
        (1002, "000002.SZ"),
    ]

    adapter = MySQLFundamentalsAdapter(engine=mock_engine, industry_resolver=mock_resolver)
    q = AdapterQuery(
        keywords=["半导体"],
        time_window=(date(2026, 1, 1), date(2026, 5, 1)),
        target="半导体",
    )
    out = await adapter.query(q)
    assert len(out) >= 1
    assert out[0].source_type == "market_data"
    assert "ROE" in out[0].snippet or "净利润" in out[0].snippet


@pytest.mark.asyncio
async def test_returns_empty_when_no_symbols():
    mock_engine = MagicMock()
    mock_resolver = MagicMock()
    mock_resolver.resolve_industry_symbols_with_codes.return_value = []

    adapter = MySQLFundamentalsAdapter(engine=mock_engine, industry_resolver=mock_resolver)
    q = AdapterQuery(
        keywords=[], time_window=(date(2026, 1, 1), date(2026, 5, 1)), target="不存在的行业"
    )
    out = await adapter.query(q)
    assert out == []
