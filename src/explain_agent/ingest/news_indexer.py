import json
import trafilatura
from qdrant_client.http.models import PointStruct
from sqlalchemy.engine import Engine
from explain_agent.ingest.news_crawler import NewsItem
from explain_agent.ingest.tagger import NewsTagger
from explain_agent.embedding.bge_m3 import BGEM3Embedder


class NewsIndexer:
    def __init__(
        self,
        engine: Engine,
        tagger: NewsTagger,
        embedder: BGEM3Embedder,
        qdrant,
        snapshot_store=None,
        collection: str = "news_v1",
    ):
        self.engine = engine
        self.tagger = tagger
        self.embedder = embedder
        self.qdrant = qdrant
        self.snapshot_store = snapshot_store
        self.collection = collection

    def _filter_existing(self, items: list[NewsItem]) -> list[NewsItem]:
        if not items:
            return []
        hashes = [i.url_hash for i in items]
        placeholders = ",".join(["%s"] * len(hashes))
        with self.engine.begin() as conn:
            rows = conn.exec_driver_sql(
                f"SELECT url_hash FROM explain_agent.explain_news_corpus WHERE url_hash IN ({placeholders})",
                tuple(hashes),
            ).fetchall()
        existing = {r[0] for r in rows}
        return [i for i in items if i.url_hash not in existing]

    def _make_snapshot(self, content: str) -> str | None:
        """trafilatura 提取正文 + SnapshotStore 落盘，返回 snapshot_id。"""
        if self.snapshot_store is None or not content:
            return None
        try:
            extracted = trafilatura.extract(content) or content
        except Exception:
            extracted = content
        try:
            return self.snapshot_store.save(extracted, content_type="news")
        except Exception:
            return None

    def index(self, items: list[NewsItem]) -> int:
        items = self._filter_existing(items)
        if not items:
            return 0

        tags = [self.tagger.tag(i.title, i.content) for i in items]
        texts = [f"{i.title}。{i.content[:1500]}" for i in items]
        vectors = self.embedder.embed(texts)
        snap_ids = [self._make_snapshot(i.content) for i in items]

        with self.engine.begin() as conn:
            for item, tag, snap_id in zip(items, tags, snap_ids):
                conn.exec_driver_sql(
                    """
                    INSERT INTO explain_agent.explain_news_corpus
                      (news_id, url_hash, source, url, title, content, published_at, tags, snapshot_id, is_indexed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        item.news_id,
                        item.url_hash,
                        item.source,
                        item.url,
                        item.title,
                        item.content,
                        item.published_at,
                        json.dumps(tag, ensure_ascii=False),
                        snap_id,
                    ),
                )

        points = [
            PointStruct(
                id=item.news_id,
                vector=vec,
                payload={
                    "news_id": item.news_id,
                    "source": item.source,
                    "title": item.title,
                    "published_at": item.published_at.isoformat(),
                    "tags": tag.get("industries", []) + tag.get("concepts", []),
                    "url": item.url,
                    "snapshot_id": snap_id,
                },
            )
            for item, vec, tag, snap_id in zip(items, vectors, tags, snap_ids)
        ]
        self.qdrant.upsert(collection_name=self.collection, points=points)
        return len(items)
