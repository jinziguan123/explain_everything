from datetime import datetime
from unittest.mock import MagicMock

from explain_agent.ingest.research_crawler import ResearchItem


def make_item(i: int, qt: int = 1) -> ResearchItem:
    return ResearchItem(
        research_id=f"id_{i}", url_hash=f"h_{i}",
        source="东方财富研报",
        url=f"https://x.com/{i}.pdf",
        title=f"研报标题 {i}",
        institution="测试证券",
        industry="银行Ⅱ" if qt == 1 else "宏观策略",
        published_at=datetime(2026, 5, 10, 9, 0),
        q_type=qt, researcher="测试分析师",
        info_code=f"AP_TEST_{i}",
    )


def test_indexer_writes_mysql_and_qdrant():
    from explain_agent.ingest.research_indexer import ResearchIndexer

    items = [make_item(i) for i in range(3)]
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = []

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024 for _ in items]
    mock_qdrant = MagicMock()

    indexer = ResearchIndexer(
        engine=mock_engine, embedder=mock_embedder, qdrant=mock_qdrant,
    )
    n = indexer.index(items)
    assert n == 3

    # embedding 用的是 "title | 机构 | 行业" 短文本
    texts = mock_embedder.embed.call_args.args[0]
    assert "研报标题 0" in texts[0]
    assert "测试证券" in texts[0]
    assert "银行Ⅱ" in texts[0]

    # INSERT explain_research_corpus 至少 3 次
    insert_calls = [c for c in mock_conn.exec_driver_sql.call_args_list
                    if "INSERT INTO explain_agent.explain_research_corpus" in c.args[0]]
    assert len(insert_calls) == 3
    # 第一条 INSERT 参数检查
    first_params = insert_calls[0].args[1]
    assert "id_0" in first_params      # research_id
    assert "h_0" in first_params       # url_hash

    # Qdrant upsert 调用
    assert mock_qdrant.upsert.called
    points = mock_qdrant.upsert.call_args.kwargs.get("points") or mock_qdrant.upsert.call_args.args[1]
    assert len(points) == 3


def test_indexer_dedupes_by_url_hash():
    """已存在的 url_hash 自动跳过。"""
    from explain_agent.ingest.research_indexer import ResearchIndexer

    items = [make_item(0), make_item(1)]
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    # h_0 已存在, h_1 是新的
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = [("h_0",)]

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024]
    mock_qdrant = MagicMock()

    indexer = ResearchIndexer(engine=mock_engine, embedder=mock_embedder, qdrant=mock_qdrant)
    n = indexer.index(items)
    assert n == 1
