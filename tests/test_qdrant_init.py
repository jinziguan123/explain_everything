import pytest
from explain_agent.db.qdrant import get_qdrant_client
from explain_agent.db.qdrant_init import ensure_collections, COLLECTIONS


@pytest.mark.integration
def test_ensure_collections_idempotent():
    result1 = ensure_collections()
    result2 = ensure_collections()
    assert all(not v for v in result2.values())
    client = get_qdrant_client()
    names = {c.name for c in client.get_collections().collections}
    for c in COLLECTIONS:
        assert c in names
