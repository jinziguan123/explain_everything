from datetime import date, datetime
from unittest.mock import MagicMock
import pytest
from explain_agent.core.types import AdapterQuery
from explain_agent.adapters.news_corpus import NewsCorpusAdapter


@pytest.mark.asyncio
async def test_news_corpus_returns_evidence_from_qdrant():
    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024]

    mock_qdrant = MagicMock()
    mock_hit = MagicMock()
    mock_hit.id = "news_1"
    mock_hit.score = 0.85
    mock_hit.payload = {
        "news_id": "news_1",
        "title": "半导体涨停",
        "source": "东方财富",
        "published_at": "2026-05-10T09:30:00",
        "url": "http://a.com/1",
        "tags": ["半导体"],
    }
    mock_resp = MagicMock()
    mock_resp.points = [mock_hit]
    mock_qdrant.query_points.return_value = mock_resp

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = [("news_1", "正文片段...")]

    adapter = NewsCorpusAdapter(qdrant=mock_qdrant, embedder=mock_embedder, engine=mock_engine)
    q = AdapterQuery(
        keywords=["半导体", "涨停原因"],
        time_window=(date(2026, 5, 1), date(2026, 5, 11)),
        target="半导体",
    )
    out = await adapter.query(q)
    assert len(out) == 1
    assert out[0].source_type == "news"
    assert out[0].title == "半导体涨停"


@pytest.mark.asyncio
async def test_news_corpus_empty_when_no_hits():
    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024]
    mock_qdrant = MagicMock()
    mock_empty = MagicMock()
    mock_empty.points = []
    mock_qdrant.query_points.return_value = mock_empty
    mock_engine = MagicMock()
    adapter = NewsCorpusAdapter(qdrant=mock_qdrant, embedder=mock_embedder, engine=mock_engine)
    q = AdapterQuery(keywords=["x"], time_window=(date(2026, 5, 1), date(2026, 5, 11)), target="x")
    out = await adapter.query(q)
    assert out == []
