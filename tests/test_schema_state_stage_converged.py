"""Phase 5 Stage Literal 加 'converged' 终态。"""

import pytest

from explain_engine.persistence.session import SessionMeta


class TestStageConverged:
    def test_converged_valid(self) -> None:
        meta = SessionMeta(
            session_id="s_abcd1234",
            question="why",
            stage="converged",
            created_at=1.0,
            updated_at=1.0,
        )
        assert meta.stage == "converged"

    def test_phase4_done_still_valid(self) -> None:
        """旧 'done' 状态依然有效（Phase 4 session 反序列化兼容）。"""
        meta = SessionMeta(
            session_id="s_abcd1234",
            question="why",
            stage="done",
            created_at=1.0,
            updated_at=1.0,
        )
        assert meta.stage == "done"

    def test_invalid_stage_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid stage"):
            SessionMeta(
                session_id="s_abcd1234",
                question="why",
                stage="running",
                created_at=1.0,
                updated_at=1.0,
            )
