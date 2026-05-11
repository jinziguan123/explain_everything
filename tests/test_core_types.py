from datetime import datetime, date
from explain_agent.core.types import Evidence, AdapterQuery


def test_evidence_serialization():
    e = Evidence(
        id="e1",
        source="news_corpus",
        source_type="news",
        url="https://example.com",
        title="测试标题",
        snippet="正文片段",
        raw_payload=None,
        snapshot_id=None,
        timestamp=datetime(2026, 5, 1, 9, 30),
        metadata={"tag": "半导体"},
    )
    d = e.model_dump()
    assert d["id"] == "e1"
    assert d["source_type"] == "news"
    assert d["metadata"]["tag"] == "半导体"


def test_adapter_query_defaults():
    q = AdapterQuery(
        keywords=["半导体", "国产替代"],
        time_window=(date(2026, 4, 1), date(2026, 5, 1)),
        target="半导体",
    )
    assert q.filters == {}
    assert q.limit == 50
