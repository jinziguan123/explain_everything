"""SessionStore.delete — Phase 17.2 Task 21.

thin wrapper → storage_v2.delete_session; 验证 store.list() 删后不含.
"""
import pytest

from explain_engine.persistence.session import (
    Session,
    SessionMeta,
    SessionStore,
)
from explain_engine.schema.state import CognitiveState


def test_session_store_delete_removes_from_list() -> None:
    """store.delete(sid) 后 store.list() 不再含此 sid."""
    store = SessionStore()
    meta = SessionMeta.new("q")
    sess = Session(meta=meta, state=CognitiveState.bootstrap("q", budget=10))
    store.save(sess)

    metas = store.list()
    assert any(m.session_id == meta.session_id for m in metas)

    store.delete(meta.session_id)
    metas2 = store.list()
    assert not any(m.session_id == meta.session_id for m in metas2)


def test_session_store_delete_nonexistent_raises() -> None:
    """thin wrapper 透传 FileNotFoundError."""
    store = SessionStore()
    with pytest.raises(FileNotFoundError):
        store.delete("s_notexist")
