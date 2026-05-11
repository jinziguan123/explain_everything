from datetime import datetime, time
from qdrant_client.http.models import Filter, FieldCondition, DatetimeRange
from explain_agent.core.types import AdapterQuery, Evidence


class NewsCorpusAdapter:
    name = "news_corpus"

    def __init__(self, qdrant, embedder, engine, collection: str = "news_v1"):
        self.qdrant = qdrant
        self.embedder = embedder
        self.engine = engine
        self.collection = collection

    async def query(self, q: AdapterQuery) -> list[Evidence]:
        query_text = " ".join([q.target] + q.keywords)
        vec = self.embedder.embed([query_text])[0]
        start_iso = datetime.combine(q.time_window[0], time.min).isoformat()
        end_iso = datetime.combine(q.time_window[1], time.max).isoformat()
        flt = Filter(
            must=[
                FieldCondition(
                    key="published_at",
                    range=DatetimeRange(gte=start_iso, lte=end_iso),
                ),
            ]
        )
        resp = self.qdrant.query_points(
            collection_name=self.collection,
            query=vec,
            query_filter=flt,
            limit=q.limit,
            with_payload=True,
        )
        hits = resp.points if hasattr(resp, "points") else resp
        if not hits:
            return []

        ids = [h.id for h in hits]
        placeholders = ",".join(["%s"] * len(ids))
        with self.engine.begin() as conn:
            rows = conn.exec_driver_sql(
                f"SELECT news_id, content FROM explain_agent.explain_news_corpus WHERE news_id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
        content_map = {r[0]: r[1] for r in rows}

        return [
            Evidence(
                id=h.id,
                source=self.name,
                source_type="news",
                url=h.payload.get("url"),
                title=h.payload.get("title"),
                snippet=(content_map.get(h.id, "") or h.payload.get("title", ""))[:600],
                timestamp=datetime.fromisoformat(h.payload["published_at"]),
                metadata={
                    "tags": h.payload.get("tags", []),
                    "score": h.score,
                    "source_name": h.payload.get("source"),
                },
            )
            for h in hits
        ]
