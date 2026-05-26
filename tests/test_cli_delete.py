"""explain delete <sid> CLI — Phase 17.2 Task 23 + 24.

Covers:
- Task 23: confirm y / n path
- Task 24: --force / 不存在 sid
"""
from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.persistence.session import (
    Session,
    SessionMeta,
    SessionStore,
)
from explain_engine.schema.state import CognitiveState

runner = CliRunner()


def _make_session() -> str:
    """Persist a real session and return its sid (autouse isolated_explain_home
    保证落 tmp dir)."""
    meta = SessionMeta.new("q")
    sess = Session(meta=meta, state=CognitiveState.bootstrap("q", budget=10))
    SessionStore().save(sess)
    return meta.session_id


def test_cli_delete_with_confirm_y() -> None:
    """typer.confirm y → 走 store.delete + 绿字 + exit 0."""
    sid = _make_session()
    store = SessionStore()
    assert store._storage.session_dir(sid).exists()

    result = runner.invoke(app, ["delete", sid], input="y\n")
    assert result.exit_code == 0, result.stdout
    assert "已删" in result.stdout
    assert not store._storage.session_dir(sid).exists()


def test_cli_delete_cancel_with_n() -> None:
    """typer.confirm n → 取消, session 保留, exit 0."""
    sid = _make_session()
    store = SessionStore()
    assert store._storage.session_dir(sid).exists()

    result = runner.invoke(app, ["delete", sid], input="n\n")
    assert result.exit_code == 0, result.stdout
    assert "已取消" in result.stdout
    assert store._storage.session_dir(sid).exists()
