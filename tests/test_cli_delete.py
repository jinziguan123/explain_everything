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


def test_cli_delete_force_skips_confirm() -> None:
    """--force 跳 typer.confirm 直删, 不需 stdin input."""
    sid = _make_session()
    store = SessionStore()

    result = runner.invoke(app, ["delete", sid, "--force"])
    assert result.exit_code == 0, result.stdout
    assert "已删" in result.stdout
    assert not store._storage.session_dir(sid).exists()


def test_cli_delete_short_force_flag() -> None:
    """-f 等价 --force."""
    sid = _make_session()
    store = SessionStore()

    result = runner.invoke(app, ["delete", sid, "-f"])
    assert result.exit_code == 0
    assert not store._storage.session_dir(sid).exists()


def test_cli_delete_nonexistent_returns_1() -> None:
    """不存在 sid (但格式合规 — Wave 3 review C-1 后必须合规才进 FileNotFoundError 分支)
    → red error + exit 1."""
    result = runner.invoke(app, ["delete", "s_deadbeef", "--force"])
    assert result.exit_code == 1, result.stdout
    assert "不存在" in result.stdout


# ── Phase 17.2 Wave 3 review fix ──


def test_cli_delete_oserror_returns_1(monkeypatch) -> None:
    """I-2: SessionStore.delete raise PermissionError → 红字 '删除失败 (IO/权限)' +
    exit 1, 不暴 raw stack trace.

    保护 OSError 抓 branch: 若 future 重构漏抓, 此 test 静默 regress.
    """
    sid = _make_session()

    def boom(self, s):
        raise PermissionError("simulated no-write")
    monkeypatch.setattr(SessionStore, "delete", boom)

    result = runner.invoke(app, ["delete", sid, "--force"])
    assert result.exit_code == 1, result.stdout
    assert "删除失败" in result.stdout or "IO/权限" in result.stdout


def test_cli_delete_path_traversal_returns_1() -> None:
    """C-1: cli delete 传 '.' (path traversal PoC) → 拒绝 + exit 1, 不删 sessions/."""
    # 预创 1 个 real session 看是否被误删
    sid_keep = _make_session()
    store = SessionStore()
    assert store._storage.session_dir(sid_keep).exists()

    result = runner.invoke(app, ["delete", ".", "--force"])
    assert result.exit_code == 1, result.stdout
    assert "非法 session id" in result.stdout or "invalid sid format" in result.stdout

    # 真正的 session 必须仍在
    assert store._storage.session_dir(sid_keep).exists(), (
        "path traversal 修复后, 真正的 session 不能被误删"
    )
