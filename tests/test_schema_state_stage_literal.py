"""Stage Literal Phase 4 拓展测试。

Phase 4 Stage Literal: bootstrap_pending / insight_pending / done。
"""

import pytest

from explain_engine.persistence.session import SessionMeta


class TestStageLiteral:
    def test_bootstrap_pending_valid(self) -> None:
        meta = SessionMeta(
            session_id="s_abcd1234",
            question="why",
            stage="bootstrap_pending",
            created_at=1.0,
            updated_at=1.0,
        )
        assert meta.stage == "bootstrap_pending"

    def test_insight_pending_valid(self) -> None:
        meta = SessionMeta(
            session_id="s_abcd1234",
            question="why",
            stage="insight_pending",
            created_at=1.0,
            updated_at=1.0,
        )
        assert meta.stage == "insight_pending"

    def test_done_valid(self) -> None:
        meta = SessionMeta(
            session_id="s_abcd1234",
            question="why",
            stage="done",
            created_at=1.0,
            updated_at=1.0,
        )
        assert meta.stage == "done"

    def test_running_no_longer_valid(self) -> None:
        """Phase 4 砍掉 running / finalize_pending。"""
        with pytest.raises(ValueError, match="invalid stage"):
            SessionMeta(
                session_id="s_abcd1234",
                question="why",
                stage="running",
                created_at=1.0,
                updated_at=1.0,
            )

    def test_finalize_pending_no_longer_valid(self) -> None:
        with pytest.raises(ValueError, match="invalid stage"):
            SessionMeta(
                session_id="s_abcd1234",
                question="why",
                stage="finalize_pending",
                created_at=1.0,
                updated_at=1.0,
            )
