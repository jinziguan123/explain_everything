from datetime import datetime
from unittest.mock import MagicMock
from explain_agent.ingest.news_crawler import NewsItem
from explain_agent.ingest.news_indexer import NewsIndexer


def make_item(i: int) -> NewsItem:
    return NewsItem(
        news_id=f"id_{i}",
        url_hash=f"h_{i}",
        source="东方财富",
        url=f"http://a.com/{i}",
        title=f"标题 {i}",
        content="内容",
        published_at=datetime(2026, 5, 10, 9, 30),
    )


def test_indexer_writes_mysql_and_qdrant():
    items = [make_item(i) for i in range(3)]

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = []

    mock_tagger = MagicMock()
    mock_tagger.tag.return_value = {"industries": ["半导体"], "concepts": [], "policy_type": None, "event_type": None}

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024 for _ in items]

    mock_qdrant = MagicMock()

    indexer = NewsIndexer(
        engine=mock_engine,
        tagger=mock_tagger,
        embedder=mock_embedder,
        qdrant=mock_qdrant,
    )
    n = indexer.index(items)
    assert n == 3
    assert mock_conn.exec_driver_sql.called
    assert mock_qdrant.upsert.called


def test_indexer_skips_existing_by_url_hash():
    items = [make_item(0), make_item(1)]

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = [("h_0",)]

    mock_tagger = MagicMock()
    mock_tagger.tag.return_value = {"industries": [], "concepts": [], "policy_type": None, "event_type": None}
    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024]
    mock_qdrant = MagicMock()

    indexer = NewsIndexer(
        engine=mock_engine,
        tagger=mock_tagger,
        embedder=mock_embedder,
        qdrant=mock_qdrant,
    )
    n = indexer.index(items)
    assert n == 1


def test_index_writes_snapshot_id():
    """index() 调用时为每条 NewsItem 生成 snapshot_id 并填入 explain_news_corpus。"""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = []

    mock_tagger = MagicMock()
    mock_tagger.tag.return_value = {"industries": ["半导体"], "concepts": []}
    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024]
    mock_qdrant = MagicMock()

    mock_store = MagicMock()
    mock_store.save.return_value = "snap_abc"

    indexer = NewsIndexer(
        engine=mock_engine, tagger=mock_tagger,
        embedder=mock_embedder, qdrant=mock_qdrant,
        snapshot_store=mock_store,
    )
    item = NewsItem(
        news_id="n1", url_hash="h1", source="test",
        url="http://a.com", title="测试标题",
        content="<html><body><p>测试正文</p></body></html>",
        published_at=datetime(2026, 5, 10, 9, 0),
    )
    n = indexer.index([item])
    assert n == 1

    mock_store.save.assert_called_once()
    saved_content = mock_store.save.call_args.args[0]
    assert "测试正文" in saved_content

    insert_calls = [c for c in mock_conn.exec_driver_sql.call_args_list
                    if "INSERT INTO explain_agent.explain_news_corpus" in c.args[0]]
    assert insert_calls, "应该 INSERT explain_news_corpus"
    insert_params = insert_calls[0].args[1]
    assert "snap_abc" in insert_params
