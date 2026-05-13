"""CLI show + list test."""

import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_session(question: str, sessions_dir: Path) -> str:
    """工具：在 sessions_dir 落一个 session，返回 session_id。"""
    store = SessionStore(directory=sessions_dir)
    state = CognitiveState.bootstrap(question, budget=10)
    state.graph.add_node(VariableNode(
        id="p_001",
        name="房价上涨",
        description="一线城市房价持续高位",
        abstraction_level=0,
        confidence=0.7,
        epistemic="observation",
    ))
    meta = SessionMeta.new(question=question)
    session = Session(meta=meta, state=state)
    store.save(session)
    return meta.session_id


class TestShow:
    def test_show_existing_session(self, runner, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        sid = _make_session("why?", sessions_dir)

        result = runner.invoke(app, ["show", sid])

        assert result.exit_code == 0
        assert "why?" in result.output
        assert "房价上涨" in result.output
        assert sid in result.output

    def test_show_missing_session_exits_1(self, runner, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        result = runner.invoke(app, ["show", "s_deadbeef"])

        assert result.exit_code == 1


class TestList:
    def test_list_empty(self, runner, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        # 空 list 表格至少要有标题
        assert "Sessions" in result.output or "ID" in result.output

    def test_list_with_sessions(self, runner, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        _make_session("question 1", sessions_dir)
        time.sleep(0.01)
        _make_session("question 2", sessions_dir)
        time.sleep(0.01)
        _make_session("question 3", sessions_dir)

        result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        for q in ["question 1", "question 2", "question 3"]:
            assert q in result.output
