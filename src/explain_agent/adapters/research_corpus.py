from datetime import datetime, time

from qdrant_client.http.models import Filter, FieldCondition, DatetimeRange

from explain_agent.core.types import AdapterQuery, Evidence


class ResearchCorpusAdapter:
    name = "research_corpus"

    def __init__(self, qdrant, embedder, engine, collection: str = "research_v1"):
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
        try:
            resp = self.qdrant.query_points(
                collection_name=self.collection,
                query=vec,
                query_filter=flt,
                limit=q.limit,
                with_payload=True,
            )
        except Exception:
            return []
        hits = resp.points if hasattr(resp, "points") else resp
        if not hits:
            return []

        # 从 MySQL 补全 title / institution / industry / abstract (snippet 用)
        ids = [h.id for h in hits]
        placeholders = ",".join(["%s"] * len(ids))
        try:
            with self.engine.begin() as conn:
                rows = conn.exec_driver_sql(
                    f"""SELECT research_id, title, institution, industry
                        FROM explain_agent.explain_research_corpus
                        WHERE research_id IN ({placeholders})""",
                    tuple(ids),
                ).fetchall()
            meta_map = {r[0]: (r[1], r[2], r[3]) for r in rows}
        except Exception:
            meta_map = {}

        out: list[Evidence] = []
        for h in hits:
            title, inst, industry = meta_map.get(h.id, (
                h.payload.get("title", ""),
                h.payload.get("institution", ""),
                h.payload.get("industry", ""),
            ))
            snippet = f"{title} | {inst} | {industry}"
            out.append(Evidence(
                id=h.id,
                source=self.name,
                source_type="research",
                url=h.payload.get("url"),
                title=title,
                snippet=snippet,
                timestamp=datetime.fromisoformat(h.payload["published_at"]),
                metadata={
                    "institution": inst,
                    "industry": industry,
                    "q_type": h.payload.get("q_type"),
                    "score": h.score,
                },
            ))
        return out
