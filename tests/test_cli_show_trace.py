"""explain show <session_id> --trace 渲染 reasoning_trace 表。"""

from pathlib import Path

from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState, TraceEntry


def _prepare_session_with_trace(tmp_path: Path) -> str:
    g = ExplanationGraph(root_question="why")
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    state.reasoning_trace.extend([
        TraceEntry(tick=0, action="expand", target_node_id="c_001",
                   gain_delta=0.8, llm_calls=1, timestamp="2026-05-13T10:00:00"),
        TraceEntry(tick=1, action="expand", target_node_id="c_002",
                   gain_delta=0.4, llm_calls=1, timestamp="2026-05-13T10:00:01"),
        TraceEntry(tick=2, action="evaluate", target_node_id=None,
                   gain_delta=0.0, llm_calls=0, timestamp="2026-05-13T10:00:02"),
    ])
    meta = SessionMeta(session_id="s_aaaabbbb", question="why", stage="converged",
                       created_at=1.0, updated_at=1.0)
    SessionStore(directory=tmp_path).save(Session(meta=meta, state=state))
    return "s_aaaabbbb"


def test_show_without_trace_no_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session_with_trace(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["show", sid])
    assert result.exit_code == 0
    # 不带 --trace 不渲染 reasoning trace 标题
    assert "Reasoning Trace" not in result.output


def test_show_with_trace_renders_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session_with_trace(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["show", sid, "--trace"])
    assert result.exit_code == 0
    # 渲染表头 + 3 tick 数据
    assert "Reasoning Trace" in result.output
    assert "expand" in result.output
    assert "evaluate" in result.output
    assert "c_001" in result.output


def test_show_with_trace_empty(tmp_path, monkeypatch) -> None:
    """reasoning_trace 为空时 --trace 不爆,提示 empty。"""
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    g = ExplanationGraph(root_question="why")
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    meta = SessionMeta(session_id="s_ccccdddd", question="why", stage="done",
                       created_at=1.0, updated_at=1.0)
    SessionStore(directory=tmp_path).save(Session(meta=meta, state=state))

    runner = CliRunner()
    result = runner.invoke(app, ["show", "s_ccccdddd", "--trace"])
    assert result.exit_code == 0
    assert "reasoning_trace" in result.output.lower() or "empty" in result.output.lower()
