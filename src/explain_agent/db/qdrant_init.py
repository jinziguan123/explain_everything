from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    VectorParams,
    PayloadSchemaType,
)
from explain_agent.db.qdrant import get_qdrant_client


COLLECTIONS = {
    "news_v1": 1024,
    "policy_v1": 1024,
    "research_v1": 1024,
}


def ensure_collections(client: QdrantClient | None = None) -> dict[str, bool]:
    client = client or get_qdrant_client()
    existing = {c.name for c in client.get_collections().collections}
    created: dict[str, bool] = {}
    for name, dim in COLLECTIONS.items():
        if name in existing:
            created[name] = False
            continue
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        client.create_payload_index(name, "published_at", PayloadSchemaType.DATETIME)
        client.create_payload_index(name, "source", PayloadSchemaType.KEYWORD)
        client.create_payload_index(name, "tags", PayloadSchemaType.KEYWORD)
        created[name] = True
    return created
