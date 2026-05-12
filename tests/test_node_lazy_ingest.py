from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from explain_agent.graph.state import new_attribution_state


def _make_state():
    s = new_attribution_state("test")
    s["target"] = "半导体"
    s["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    return s


@pytest.mark.asyncio
async def test_lazy_ingest_skips_when_corpus_fresh():
    """MAX(fetched_at) 距 now < 3h, skip。"""
    from explain_agent.graph.nodes.lazy_ingest import lazy_ingest_node

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    # MAX(fetched_at) = 1 小时前
    mock_conn.exec_driver_sql.return_value.fetchone.return_value = (datetime.now() - timedelta(hours=1),)

    mock_crawler = MagicMock()
    mock_crawler.crawl_symbol = MagicMock(return_value=[])
    mock_indexer = MagicMock()
    mock_indexer.engine = mock_engine
    mock_indexer.index = MagicMock(return_value=0)

    out = await lazy_ingest_node(_make_state(), news_crawler=mock_crawler, news_indexer=mock_indexer)
    assert out.get("lazy_ingest_skipped") is True
    mock_crawler.crawl_symbol.assert_not_called()


@pytest.mark.asyncio
async def test_lazy_ingest_pulls_when_corpus_stale():
    """MAX(fetched_at) 距 now > 3h, 触发拉。"""
    from explain_agent.graph.nodes.lazy_ingest import lazy_ingest_node
    from explain_agent.ingest.news_crawler import NewsItem

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    # MAX(fetched_at) = 5 小时前
    mock_conn.exec_driver_sql.return_value.fetchone.return_value = (datetime.now() - timedelta(hours=5),)

    fake_item = NewsItem(
        news_id="n1", url_hash="h1", source="test",
        url="http://a.com", title="x", content="y",
        published_at=datetime(2026, 5, 10),
    )

    mock_crawler = MagicMock()
    mock_crawler.crawl_symbol = MagicMock(return_value=[fake_item])
    mock_indexer = MagicMock()
    mock_indexer.engine = mock_engine
    mock_indexer.index = MagicMock(return_value=1)

    out = await lazy_ingest_node(_make_state(), news_crawler=mock_crawler, news_indexer=mock_indexer)
    assert out.get("lazy_ingest_count") == 1
    assert out.get("lazy_ingest_skipped") is None or "lazy_ingest_skipped" not in out
    mock_crawler.crawl_symbol.assert_called_once_with("半导体")


@pytest.mark.asyncio
async def test_lazy_ingest_handles_crawler_exception():
    """crawler 抛异常 -> 静默 fallback, return {}。"""
    from explain_agent.graph.nodes.lazy_ingest import lazy_ingest_node

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchone.return_value = (datetime.now() - timedelta(hours=5),)

    mock_crawler = MagicMock()
    mock_crawler.crawl_symbol = MagicMock(side_effect=RuntimeError("network down"))
    mock_indexer = MagicMock()
    mock_indexer.engine = mock_engine

    out = await lazy_ingest_node(_make_state(), news_crawler=mock_crawler, news_indexer=mock_indexer)
    assert out == {}


@pytest.mark.asyncio
async def test_lazy_ingest_handles_indexer_exception():
    """indexer.index 抛异常 -> 静默 fallback。"""
    from explain_agent.graph.nodes.lazy_ingest import lazy_ingest_node
    from explain_agent.ingest.news_crawler import NewsItem

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchone.return_value = (datetime.now() - timedelta(hours=5),)

    fake_item = NewsItem(
        news_id="n1", url_hash="h1", source="test",
        url="http://a.com", title="x", content="y",
        published_at=datetime(2026, 5, 10),
    )

    mock_crawler = MagicMock()
    mock_crawler.crawl_symbol = MagicMock(return_value=[fake_item])
    mock_indexer = MagicMock()
    mock_indexer.engine = mock_engine
    mock_indexer.index = MagicMock(side_effect=RuntimeError("DB down"))

    out = await lazy_ingest_node(_make_state(), news_crawler=mock_crawler, news_indexer=mock_indexer)
    assert out == {}


@pytest.mark.asyncio
async def test_lazy_ingest_noop_when_crawler_or_indexer_none():
    """crawler 或 indexer 是 None 时直接 return {}, 不查 DB。"""
    from explain_agent.graph.nodes.lazy_ingest import lazy_ingest_node

    out = await lazy_ingest_node(_make_state(), news_crawler=None, news_indexer=None)
    assert out == {}

    out = await lazy_ingest_node(_make_state(), news_crawler=MagicMock(), news_indexer=None)
    assert out == {}
