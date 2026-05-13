"""Session JSON I/O test."""

import json

import pytest

from explain_engine.persistence.session import (
    Session,
    SessionMeta,
    SessionStore,
)
from explain_engine.schema.state import CognitiveState


class TestSessionMeta:
    def test_fresh_session_meta(self):
        meta = SessionMeta.new(question="why?")
        assert meta.session_id.startswith("s_")
        assert len(meta.session_id) == 2 + 8  # s_ + 8 hex
        assert meta.question == "why?"
        assert meta.stage == "bootstrap_pending"


class TestSessionStore:
    def test_save_and_load(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        state = CognitiveState.bootstrap("why?", budget=20)
        meta = SessionMeta.new(question="why?")
        session = Session(meta=meta, state=state)

        store.save(session)
        loaded = store.load(meta.session_id)

        assert loaded.meta.session_id == meta.session_id
        assert loaded.state.root_question == "why?"
        assert loaded.state.budget_remaining == 20

    def test_load_missing_raises(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        with pytest.raises(FileNotFoundError):
            store.load("s_nonexistent")

    def test_list_returns_metas_sorted(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        for q in ["q1", "q2", "q3"]:
            store.save(
                Session(
                    meta=SessionMeta.new(question=q),
                    state=CognitiveState.bootstrap(q, budget=10),
                )
            )
        metas = store.list()
        assert len(metas) == 3
        # 按 created_at 降序
        assert metas[0].created_at >= metas[1].created_at >= metas[2].created_at

    def test_save_writes_json_file(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        session = Session(
            meta=SessionMeta.new(question="why?"),
            state=CognitiveState.bootstrap("why?", budget=10),
        )
        store.save(session)
        path = tmp_sessions_dir / f"{session.meta.session_id}.json"
        assert path.exists()
        # 文件是合法 JSON
        json.loads(path.read_text())

    def test_update_session_overwrites(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        session = Session(
            meta=SessionMeta.new(question="why?"),
            state=CognitiveState.bootstrap("why?", budget=10),
        )
        store.save(session)

        # 修改 state 再保存
        session.state.advance_tick()
        session.meta.stage = "in_progress"
        store.save(session)

        loaded = store.load(session.meta.session_id)
        assert loaded.state.tick == 1
        assert loaded.meta.stage == "in_progress"
