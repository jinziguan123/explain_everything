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

    def test_invalid_stage_rejected(self):
        with pytest.raises(ValueError, match="invalid stage"):
            SessionMeta(
                session_id="s_00000000",
                question="why?",
                stage="bogus",  # type: ignore[arg-type]
                created_at=0.0,
                updated_at=0.0,
            )

    def test_invalid_session_id_format_rejected(self):
        with pytest.raises(ValueError, match="invalid session_id"):
            SessionMeta(
                session_id="bad-id",
                question="why?",
                stage="bootstrap_pending",
                created_at=0.0,
                updated_at=0.0,
            )


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
        session.meta.stage = "insight_pending"
        store.save(session)

        loaded = store.load(session.meta.session_id)
        assert loaded.state.tick == 1
        assert loaded.meta.stage == "insight_pending"

    def test_load_invalid_json_raises(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        # 写一个非法 session 文件
        bad_path = tmp_sessions_dir / "s_00000000.json"
        bad_path.write_text('{"meta": {"session_id": "s_00000000"}, "state": "not a dict"}')
        with pytest.raises(ValueError, match="invalid session dict"):
            store.load("s_00000000")

    def test_save_is_atomic_no_tmp_left(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        session = Session(
            meta=SessionMeta.new(question="why?"),
            state=CognitiveState.bootstrap("why?", budget=10),
        )
        store.save(session)
        # 不应该有 .tmp 文件残留
        tmps = list(tmp_sessions_dir.glob("*.tmp"))
        assert tmps == [], f"residual tmp files: {tmps}"

    def test_list_skips_corrupted_files(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        # 写一个好 session
        good = Session(
            meta=SessionMeta.new(question="good"),
            state=CognitiveState.bootstrap("good", budget=5),
        )
        store.save(good)
        # 写一个 corrupted 文件 (合法文件名但 JSON 损坏)
        bad = tmp_sessions_dir / "s_deadbeef.json"
        bad.write_text("{ corrupted")
        # list 不应该崩，只返回 good
        metas = store.list()
        assert len(metas) == 1
        assert metas[0].session_id == good.meta.session_id
