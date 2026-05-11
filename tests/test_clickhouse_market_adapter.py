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


@pytest.mark.asyncio
async def test_resolver_falls_back_to_like(monkeypatch):
    from explain_agent.adapters.clickhouse_market import IndustryResolver

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    call_count = {"n": 0}

    def fake_exec(sql, params):
        call_count["n"] += 1
        result = MagicMock()
        if "LIKE" in sql:
            result.fetchall.return_value = [(99, "300001.SZ")]
        else:
            result.fetchall.return_value = []
        return result

    mock_conn.exec_driver_sql.side_effect = fake_exec

    resolver = IndustryResolver(mock_engine)
    out = resolver.resolve_industry_symbols_with_codes("光伏")
    assert out == [(99, "300001.SZ")]
    assert call_count["n"] >= 4
