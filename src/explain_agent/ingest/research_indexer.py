import json

from qdrant_client.http.models import PointStruct
from sqlalchemy.engine import Engine

from explain_agent.embedding.bge_m3 import BGEM3Embedder
from explain_agent.ingest.research_crawler import ResearchItem


class ResearchIndexer:
    """与 NewsIndexer 同形, 复用 BGE-M3 + Qdrant research_v1 collection。"""

    def __init__(
        self,
        engine: Engine,
        embedder: BGEM3Embedder,
        qdrant,
        collection: str = "research_v1",
    ):
        self.engine = engine
        self.embedder = embedder
        self.qdrant = qdrant
        self.collection = collection

    def _filter_existing(self, items: list[ResearchItem]) -> list[ResearchItem]:
        if not items:
            return []
        hashes = [i.url_hash for i in items]
        placeholders = ",".join(["%s"] * len(hashes))
        with self.engine.begin() as conn:
            rows = conn.exec_driver_sql(
                f"SELECT url_hash FROM explain_agent.explain_research_corpus WHERE url_hash IN ({placeholders})",
                tuple(hashes),
            ).fetchall()
        existing = {r[0] for r in rows}
        return [i for i in items if i.url_hash not in existing]

    def _to_embedding_text(self, item: ResearchItem) -> str:
        return f"{item.title} | {item.institution} | {item.industry}"

    def index(self, items: list[ResearchItem]) -> int:
        items = self._filter_existing(items)
        if not items:
            return 0

        texts = [self._to_embedding_text(i) for i in items]
        vectors = self.embedder.embed(texts)

        with self.engine.begin() as conn:
            for item in items:
                tags = {
                    "q_type": item.q_type,
                    "researcher": item.researcher,
                    "info_code": item.info_code,
                }
                conn.exec_driver_sql(
                    """
                    INSERT INTO explain_agent.explain_research_corpus
                      (research_id, url_hash, source, url, title, abstract,
                       institution, industry, published_at, tags, is_indexed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        item.research_id, item.url_hash, item.source, item.url,
                        item.title, item.title,  # abstract = title (没有正文)
                        item.institution, item.industry, item.published_at,
                        json.dumps(tags, ensure_ascii=False),
                    ),
                )

        points = [
            PointStruct(
                id=item.research_id,
                vector=vec,
                payload={
                    "research_id": item.research_id,
                    "title": item.title,
                    "institution": item.institution,
                    "industry": item.industry,
                    "published_at": item.published_at.isoformat(),
                    "q_type": item.q_type,
                    "url": item.url,
                },
            )
            for item, vec in zip(items, vectors)
        ]
        self.qdrant.upsert(collection_name=self.collection, points=points)
        return len(items)
