import asyncio
from datetime import datetime, timedelta

from explain_agent.graph.state import AttributionState


async def lazy_ingest_node(
    state: AttributionState,
    news_crawler=None,
    news_indexer=None,
    threshold_hours: int = 3,
    timeout_seconds: float = 60.0,
) -> dict:
    """parse 完后检查 corpus 时效, MAX(fetched_at) 距 now 落后 > threshold_hours
    触发 akshare 增量拉。任何步骤异常都不阻塞 main graph, 失败 return {}
    让 fan_out 用 stale 数据继续。
    """
    if news_crawler is None or news_indexer is None:
        return {}

    target = state.get("target")
    time_window = state.get("time_window")
    if not target or not time_window:
        return {}

    try:
        max_fetched = await asyncio.to_thread(_get_max_fetched_at, news_indexer.engine)

        threshold = datetime.now() - timedelta(hours=threshold_hours)
        if max_fetched is not None and max_fetched >= threshold:
            return {"lazy_ingest_count": 0, "lazy_ingest_skipped": True}

        items = await asyncio.wait_for(
            asyncio.to_thread(news_crawler.crawl_symbol, target),
            timeout=timeout_seconds,
        )
        if not items:
            return {"lazy_ingest_count": 0}

        n = await asyncio.wait_for(
            asyncio.to_thread(news_indexer.index, items),
            timeout=timeout_seconds * 2,
        )
        return {"lazy_ingest_count": n}
    except (asyncio.TimeoutError, Exception):
        return {}


def _get_max_fetched_at(engine):
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT MAX(fetched_at) FROM explain_agent.explain_news_corpus"
        ).fetchone()
    return row[0] if row else None
