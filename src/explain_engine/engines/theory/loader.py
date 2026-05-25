"""Phase 16: load_all_session_graphs — IO heavy lazy."""
from __future__ import annotations


def load_all_session_graphs(sids, storage):
    """加载 N session 的 ExplanationGraph. 5 session < 1s, 50 session ~5s."""
    from explain_engine.persistence.session import SessionStore
    store = SessionStore()
    return {sid: store.load(sid).state.graph for sid in sids}
