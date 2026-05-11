from functools import lru_cache

from qdrant_client import QdrantClient

from explain_agent.config import get_settings


@lru_cache
def get_qdrant_client() -> QdrantClient:
    s = get_settings()
    return QdrantClient(
        host=s.qdrant_host,
        port=s.qdrant_port,
        api_key=s.qdrant_api_key,
        https=False,
    )
