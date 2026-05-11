from datetime import date
import pytest
from explain_agent.core.types import AdapterQuery
from explain_agent.adapters.clickhouse_market import ClickHouseMarketAdapter, IndustryResolver
from explain_agent.adapters.mysql_fundamentals import MySQLFundamentalsAdapter
from explain_agent.adapters.akshare_capital_flow import AkshareCapitalFlowAdapter
from explain_agent.adapters.news_corpus import NewsCorpusAdapter
from explain_agent.db.mysql import get_engine
from explain_agent.db.clickhouse import get_client as ch_client
from explain_agent.db.qdrant import get_qdrant_client
from explain_agent.embedding.bge_m3 import get_embedder


@pytest.mark.integration
@pytest.mark.asyncio
async def test_phase1_all_adapters_return_evidence_for_semiconductor():
    q = AdapterQuery(
        keywords=["涨停原因", "国产替代"],
        time_window=(date(2025, 4, 1), date(2026, 5, 31)),
        target="半导体",
    )

    quant_engine = get_engine("quant")
    resolver = IndustryResolver(quant_engine)

    market = ClickHouseMarketAdapter(ch_client(), resolver)
    fund = MySQLFundamentalsAdapter(quant_engine, resolver)
    flow = AkshareCapitalFlowAdapter()
    news = NewsCorpusAdapter(
        qdrant=get_qdrant_client(),
        embedder=get_embedder(),
        engine=get_engine("explain"),
    )

    r_market = await market.query(q)
    r_fund = await fund.query(q)
    r_flow = await flow.query(q)
    r_news = await news.query(q)

    print(f"market: {len(r_market)} | fundamentals: {len(r_fund)} | flow: {len(r_flow)} | news: {len(r_news)}")
    nonempty = sum(bool(r) for r in [r_market, r_fund, r_flow, r_news])
    assert nonempty >= 3, "至少 3 个 Adapter 应返回数据"
