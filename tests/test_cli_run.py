"""explain run <session_id> CLI 测试 (mock LLM)。

注: plan 用 EXPLAIN_SESSIONS_DIR env var,但项目 Settings 字段 sessions_dir 对应
默认 SESSIONS_DIR env var (跟 conftest 一致),所以这里用 SESSIONS_DIR。
"""

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.llm.client import Response
from explain_engine.persistence.session import (
    Session,
    SessionMeta,
    SessionStore,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


class FakeLLM:
    async def chat(self, messages, schema=None, model=None):
        return Response(
            text="x",
            parsed={"drivers": [
                {"name": "drv", "description": "d", "mechanism": "m", "plausibility": 4}
            ]},
            model="fake",
            usage={"input_tokens": 0, "output_tokens": 0},
        )


def _prepare_done_session(tmp_path: Path) -> str:
    """构造一个 stage=done 的 session 落盘,返 session_id。"""
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(id="p_001", name="p", description="d", abstraction_level=0,
                            confidence=0.8, epistemic="observation"))
    g.add_node(VariableNode(id="c_001", name="c", description="d", abstraction_level=1,
                            confidence=0.7, epistemic="insight"))
    g.add_edge(RelationEdge(id="e_001", source_node="c_001", target_node="p_001",
                            relation_type="manifests_as", confidence=0.7,
                            mechanism_description="m"))

    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    meta = SessionMeta(
        session_id="s_abcd1234", question="why", stage="done",
        created_at=1.0, updated_at=1.0,
    )
    store = SessionStore(directory=tmp_path)
    store.save(Session(meta=meta, state=state))
    return "s_abcd1234"


def test_run_happy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_done_session(tmp_path)
    with patch("explain_engine.cli.make_llm_client", return_value=FakeLLM()):
        runner = CliRunner()
        result = runner.invoke(app, ["run", sid, "--budget", "3"])
    assert result.exit_code == 0, result.output
    # 重新 load 看 stage
    store = SessionStore(directory=tmp_path)
    session = store.load(sid)
    assert session.meta.stage == "converged"
    assert session.state.tick >= 1
    assert len(session.state.reasoning_trace) >= 1


def test_run_session_not_found(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["run", "s_nonexis"])  # 8 hex chars (regex)
    assert result.exit_code == 1


def test_run_stage_not_done(tmp_path, monkeypatch) -> None:
    """stage=bootstrap_pending 重跑 run → exit 4。"""
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    g = ExplanationGraph(root_question="why")
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    meta = SessionMeta(
        session_id="s_aaaabbbb", question="why", stage="bootstrap_pending",
        created_at=1.0, updated_at=1.0,
    )
    store = SessionStore(directory=tmp_path)
    store.save(Session(meta=meta, state=state))

    runner = CliRunner()
    result = runner.invoke(app, ["run", "s_aaaabbbb"])
    assert result.exit_code == 4


def test_run_already_converged(tmp_path, monkeypatch) -> None:
    """stage=converged 重跑 → exit 4。"""
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    g = ExplanationGraph(root_question="why")
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    meta = SessionMeta(
        session_id="s_aaaabbbb", question="why", stage="converged",
        created_at=1.0, updated_at=1.0,
    )
    store = SessionStore(directory=tmp_path)
    store.save(Session(meta=meta, state=state))

    runner = CliRunner()
    result = runner.invoke(app, ["run", "s_aaaabbbb"])
    assert result.exit_code == 4
