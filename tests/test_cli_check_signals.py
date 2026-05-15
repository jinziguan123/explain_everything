"""Wave 2 Task 2.3: explain check 显示 Multi-signal acceptance section."""

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


def _build_session_with_l1_l0(tmp_path, sid: str = "s_aabbccdd") -> str:
    """Create session w/ 1 L1 + 1 L0 + manifests_as edge → check 可跑."""
    g = ExplanationGraph(root_question="q")
    g.add_node(VariableNode(
        id="c_001", name="abstract", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="phenom", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m",
    ))
    state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
    state.insight_candidates = ["c_001"]
    meta = SessionMeta(
        session_id=sid, question="q", stage="converged",
        created_at=1.0, updated_at=1.0,
    )
    SessionStore(directory=tmp_path).save(Session(meta=meta, state=state))
    return sid


class TestCheckMultiSignalSection:
    def test_check_renders_multi_signal_section(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        sid = _build_session_with_l1_l0(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["check", sid])
        assert result.exit_code == 0
        # 关键字应出现在输出
        assert "Multi-signal" in result.output or "rollout_coverage" in result.output

    def test_check_shows_rollout_coverage_value(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        sid = _build_session_with_l1_l0(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["check", sid])
        assert "rollout_coverage" in result.output
        # full L1→L0 链, rollout_coverage 应该 = 1.0
        assert "1.00" in result.output or "1.000" in result.output

    def test_check_shows_weak_chain_l1s_when_present(self, tmp_path, monkeypatch) -> None:
        """改 confidence 让 c_001 进 weak_chain_l1s."""
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        sid = _build_session_with_l1_l0(tmp_path)
        # 改低 conf 让其变 weak
        store = SessionStore(directory=tmp_path)
        sess = store.load(sid)
        for e in sess.state.graph.edges.values():
            e.confidence = 0.1
        store.save(sess)

        runner = CliRunner()
        result = runner.invoke(app, ["check", sid])
        assert result.exit_code == 0
        assert "weak_chain_l1s" in result.output or "c_001" in result.output
