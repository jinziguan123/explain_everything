from datetime import date
from unittest.mock import MagicMock

import pytest

from explain_agent.adapters.web_search import WebSearchAdapter
from explain_agent.core.types import AdapterQuery


@pytest.mark.asyncio
async def test_query_calls_tavily_and_returns_evidence():
    fake_tavily = MagicMock()
    fake_tavily.search.return_value = {
        "results": [
            {
                "url": "https://a.com/news/1",
                "title": "标题 1",
                "content": "正文内容 1",
                "score": 0.9,
            },
            {
                "url": "https://b.com/news/2",
                "title": "标题 2",
                "content": "正文内容 2",
                "score": 0.85,
            },
        ],
    }
    fake_store = MagicMock()
    fake_store.save.side_effect = ["snap_001", "snap_002"]

    adapter = WebSearchAdapter(tavily_client=fake_tavily, snapshot_store=fake_store)
    q = AdapterQuery(
        keywords=["半导体", "制裁"],
        time_window=(date(2026, 5, 5), date(2026, 5, 12)),
        target="半导体",
        limit=5,
    )
    out = await adapter.query(q)

    assert len(out) == 2
    assert out[0].source == "web_search"
    assert out[0].source_type == "news"
    assert out[0].url == "https://a.com/news/1"
    assert out[0].snippet == "正文内容 1"
    assert out[0].snapshot_id == "snap_001"
    fake_tavily.search.assert_called_once()
    assert fake_store.save.call_count == 2


@pytest.mark.asyncio
async def test_query_handles_tavily_failure():
    fake_tavily = MagicMock()
    fake_tavily.search.side_effect = RuntimeError("api error")
    fake_store = MagicMock()

    adapter = WebSearchAdapter(tavily_client=fake_tavily, snapshot_store=fake_store)
    q = AdapterQuery(
        keywords=["x"],
        time_window=(date(2026, 5, 5), date(2026, 5, 12)),
        target="x",
    )
    out = await adapter.query(q)
    assert out == []


@pytest.mark.asyncio
async def test_query_returns_empty_when_tavily_empty():
    fake_tavily = MagicMock()
    fake_tavily.search.return_value = {"results": []}
    fake_store = MagicMock()

    adapter = WebSearchAdapter(tavily_client=fake_tavily, snapshot_store=fake_store)
    q = AdapterQuery(
        keywords=["x"],
        time_window=(date(2026, 5, 5), date(2026, 5, 12)),
        target="x",
    )
    out = await adapter.query(q)
    assert out == []
