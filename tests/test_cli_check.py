"""explain check <sid> [<var_id>] CLI 测试."""

from pathlib import Path

from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.persistence.session import (
    Session,
    SessionMeta,
    SessionStore,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _prepare_session(tmp_path: Path, sid: str = "s_aaaabbbb") -> str:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id="p_001", name="p1", description="d", abstraction_level=0,
        confidence=0.8, epistemic="observation",
    ))
    g.add_node(VariableNode(
        id="c_001", name="c1", description="d", abstraction_level=1,
        confidence=0.7, epistemic="insight",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m",
    ))
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    meta = SessionMeta(
        session_id=sid, question="why", stage="converged",
        created_at=1.0, updated_at=1.0,
    )
    SessionStore(directory=tmp_path).save(Session(meta=meta, state=state))
    return sid


def test_check_batch_happy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["check", sid])
    assert result.exit_code == 0, result.output
    assert "c_001" in result.output
    assert "Consistency" in result.output


def test_check_single_target_happy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["check", sid, "c_001"])
    assert result.exit_code == 0, result.output
    assert "c_001" in result.output
    # 详细模式应该含 decay trace 或 contribution
    assert "consistency_score" in result.output.lower() or "decay" in result.output.lower()


def test_check_session_not_found_exits_1(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["check", "s_99999999"])
    assert result.exit_code == 1


def test_check_target_not_in_graph_exits_2(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["check", sid, "c_999"])
    assert result.exit_code == 2


def test_check_target_level_0_exits_2(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["check", sid, "p_001"])
    assert result.exit_code == 2


def test_check_empty_graph_no_L1_L2(tmp_path, monkeypatch) -> None:
    """graph 只有 L0 → batch 输出空提示, exit 0.
    session_id 必须 ^s_[0-9a-f]{8}$, 用 s_eeee0000 (8 hex)."""
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id="p_001", name="p1", description="d", abstraction_level=0,
        confidence=0.8, epistemic="observation",
    ))
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    meta = SessionMeta(
        session_id="s_eeee0000", question="why", stage="converged",
        created_at=1.0, updated_at=1.0,
    )
    SessionStore(directory=tmp_path).save(Session(meta=meta, state=state))

    runner = CliRunner()
    result = runner.invoke(app, ["check", "s_eeee0000"])
    assert result.exit_code == 0
    assert "无 L1/L2" in result.output or "无 L1" in result.output


def test_check_renders_color_threshold_in_batch(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["check", sid])
    assert result.exit_code == 0
    assert "Consistency" in result.output
    assert "Essentialness" in result.output


def test_check_single_with_trace_all_renders_full_trace(tmp_path, monkeypatch) -> None:
    """--trace-all flag 渲染完整 decay_trace (区别于默认 top 8)."""
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["check", sid, "c_001", "--trace-all"])
    assert result.exit_code == 0
    # 详细模式渲染 "(full)" 标题, 区别于默认 "(top 8 by activation_after, ...)"
    assert "(full)" in result.output
