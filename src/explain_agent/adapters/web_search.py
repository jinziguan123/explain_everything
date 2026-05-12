from datetime import datetime
from uuid import uuid4

from explain_agent.core.types import AdapterQuery, Evidence


class WebSearchAdapter:
    name = "web_search"

    def __init__(self, tavily_client, snapshot_store, max_results: int = 5):
        self.tavily = tavily_client
        self.snapshot_store = snapshot_store
        self.max_results = max_results

    async def query(self, q: AdapterQuery) -> list[Evidence]:
        query_text = " ".join([q.target] + q.keywords)
        max_n = min(q.limit, self.max_results) if q.limit else self.max_results
        try:
            resp = self.tavily.search(
                query=query_text,
                max_results=max_n,
                search_depth="basic",
            )
        except Exception:
            return []
        results = (resp or {}).get("results") or []
        out: list[Evidence] = []
        now = datetime.now()
        for item in results:
            content = item.get("content") or ""
            url = item.get("url")
            title = item.get("title")
            snap_id = None
            try:
                snap_id = self.snapshot_store.save(content, content_type="web_search")
            except Exception:
                snap_id = None
            out.append(
                Evidence(
                    id=str(uuid4()),
                    source=self.name,
                    source_type="news",
                    url=url,
                    title=title,
                    snippet=content,
                    snapshot_id=snap_id,
                    timestamp=now,
                    metadata={
                        "target": q.target,
                        "score": item.get("score"),
                        "query": query_text,
                    },
                )
            )
        return out
