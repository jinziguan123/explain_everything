from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from explain_agent.core.types import AdapterQuery


@pytest.mark.asyncio
async def test_query_returns_evidence_with_research_source_type():
    from explain_agent.adapters.research_corpus import ResearchCorpusAdapter

    fake_qdrant = MagicMock()
    fake_hit = MagicMock()
    fake_hit.id = "research_1"
    fake_hit.score = 0.85
    fake_hit.payload = {
        "research_id": "research_1",
        "title": "半导体行业策略 2026 春季",
        "institution": "中信证券",
        "industry": "电子",
        "published_at": "2026-05-10T09:00:00",
        "q_type": 1,
        "url": "https://x.com/r1.pdf",
    }
    fake_qdrant.query_points.return_value = MagicMock(points=[fake_hit])

    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [[0.1] * 1024]

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = [
        ("research_1", "半导体行业策略 2026 春季", "中信证券", "电子"),
    ]

    adapter = ResearchCorpusAdapter(qdrant=fake_qdrant, embedder=fake_embedder, engine=mock_engine)
    q = AdapterQuery(
        keywords=["半导体", "产业链"],
        time_window=(date(2026, 5, 1), date(2026, 5, 12)),
        target="半导体",
        limit=5,
    )
    out = await adapter.query(q)
    assert len(out) == 1
    ev = out[0]
    assert ev.source == "research_corpus"
    assert ev.source_type == "research"
    assert "半导体行业策略" in ev.snippet
    assert "中信证券" in ev.snippet
    assert ev.url == "https://x.com/r1.pdf"


@pytest.mark.asyncio
async def test_query_returns_empty_when_no_hits():
    from explain_agent.adapters.research_corpus import ResearchCorpusAdapter

    fake_qdrant = MagicMock()
    fake_qdrant.query_points.return_value = MagicMock(points=[])
    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [[0.1] * 1024]

    adapter = ResearchCorpusAdapter(qdrant=fake_qdrant, embedder=fake_embedder, engine=MagicMock())
    q = AdapterQuery(
        keywords=["x"], time_window=(date(2026, 5, 1), date(2026, 5, 12)),
        target="x", limit=5,
    )
    out = await adapter.query(q)
    assert out == []
