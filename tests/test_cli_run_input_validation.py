"""Wave 3 Task 3.2: CLI explain run 集成 input_validation."""

import pytest
from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.engines.input_validation import InputAlignmentReport
from explain_engine.persistence.session import Session, SessionStore
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _build_done_session(tmp_path, question="why X?"):
    """Build a session at stage='done' (run-eligible) with L0 + L1.

    Phase 5 run requires stage=done (compressed). 直接 manually 构造,
    跳过实际 compress.
    """
    from explain_engine.persistence.session import SessionMeta
    from explain_engine.schema.state import CognitiveState

    g = ExplanationGraph(root_question=question)
    g.add_node(VariableNode(
        id="p_001", name="phenom_a", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_node(VariableNode(
        id="p_002", name="phenom_b", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_node(VariableNode(
        id="c_001", name="abstract", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m",
    ))
    g.add_edge(RelationEdge(
        id="e_002", source_node="c_001", target_node="p_002",
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m",
    ))
    state = CognitiveState(graph=g, budget_remaining=10, root_question=question)
    state.insight_candidates = ["c_001"]
    meta = SessionMeta.new(question=question)
    meta.stage = "done"
    sess = Session(meta=meta, state=state)
    store = SessionStore(tmp_path)
    store.save(sess)
    return store, sess.meta.session_id


async def _stub_validate_high(question, l0_nodes, llm):
    """Async mock returning high alignment.

    Note: 必须是 async def coroutine function (而非 sync 返 coroutine), 因为
    mocker.patch 一个 async 函数会自动用 AsyncMock; AsyncMock 把 side_effect
    coroutine function 用 await 调; 若 side_effect 返回 unawaited coroutine,
    AsyncMock 会原样返回, 导致 asyncio.run 收到嵌套 coroutine.
    """
    return InputAlignmentReport(
        question_subject="X subject",
        observation_subjects=["X-related obs"],
        overlap_score=5,
        falsifiable_reason="aligned",
    )


async def _stub_validate_low(question, l0_nodes, llm):
    """Async mock returning low alignment (fail-fast trigger). 同 _stub_validate_high."""
    return InputAlignmentReport(
        question_subject="X subject",
        observation_subjects=["unrelated Y"],
        overlap_score=0,
        falsifiable_reason="completely unrelated",
    )


class TestRunInputValidation:
    def test_low_overlap_exits_with_code_2(
        self, tmp_path, monkeypatch, mocker
    ) -> None:
        """Mismatch session → exit 2 with friendly error."""
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        _store, sid = _build_done_session(tmp_path)

        # Patch validate to return low overlap
        mocker.patch(
            "explain_engine.cli.validate_input",
            side_effect=_stub_validate_low,
        )
        # Avoid hitting real LLM client construction
        mocker.patch(
            "explain_engine.cli.make_llm_client", return_value=mocker.MagicMock()
        )

        runner = CliRunner()
        result = runner.invoke(app, ["run", sid, "--budget", "2"])
        assert result.exit_code == 2
        # Friendly error markers in stderr/stdout
        assert (
            "Input validation failed" in result.output
            or "0/5" in result.output
        )
        assert "completely unrelated" in result.output

    def test_no_input_check_flag_skips_validation(
        self, tmp_path, monkeypatch, mocker
    ) -> None:
        """--no-input-check → validate 不调用, runtime 直接进."""
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        _store, sid = _build_done_session(tmp_path)

        validate_mock = mocker.patch(
            "explain_engine.cli.validate_input",
            side_effect=_stub_validate_low,
        )
        mocker.patch(
            "explain_engine.cli.make_llm_client", return_value=mocker.MagicMock()
        )
        # Mock runtime_run to short-circuit (avoid real LLM expand)
        async def _fake_run(*a, **k):
            return "budget_exhausted"
        mocker.patch(
            "explain_engine.runtime.runtime.run",
            side_effect=_fake_run,
        )

        runner = CliRunner()
        result = runner.invoke(
            app, ["run", sid, "--budget", "1", "--no-input-check"]
        )
        # validate should NOT be called
        assert validate_mock.call_count == 0
        # Should NOT exit 2 from validation
        assert "Input validation failed" not in result.output

    def test_high_overlap_proceeds_normally(
        self, tmp_path, monkeypatch, mocker
    ) -> None:
        """High overlap → validate called, runtime proceeds (not exit 2 from validation)."""
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        _store, sid = _build_done_session(tmp_path)

        mocker.patch(
            "explain_engine.cli.validate_input",
            side_effect=_stub_validate_high,
        )
        mocker.patch(
            "explain_engine.cli.make_llm_client", return_value=mocker.MagicMock()
        )
        async def _fake_run(*a, **k):
            return "budget_exhausted"
        mocker.patch(
            "explain_engine.runtime.runtime.run",
            side_effect=_fake_run,
        )

        runner = CliRunner()
        result = runner.invoke(app, ["run", sid, "--budget", "1"])
        # validation passed, no fail-fast
        assert "Input validation failed" not in result.output

    def test_validate_only_runs_at_tick_zero(
        self, tmp_path, monkeypatch, mocker
    ) -> None:
        """If state.tick > 0 (resumed session), skip input validation."""
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        store, sid = _build_done_session(tmp_path)
        # Bump tick on saved session
        sess = store.load(sid)
        sess.state.tick = 5
        store.save(sess)

        validate_mock = mocker.patch(
            "explain_engine.cli.validate_input",
            side_effect=_stub_validate_low,
        )
        mocker.patch(
            "explain_engine.cli.make_llm_client", return_value=mocker.MagicMock()
        )
        async def _fake_run(*a, **k):
            return "budget_exhausted"
        mocker.patch(
            "explain_engine.runtime.runtime.run",
            side_effect=_fake_run,
        )

        runner = CliRunner()
        runner.invoke(app, ["run", sid, "--budget", "1"])
        # validate should NOT be called (tick > 0)
        assert validate_mock.call_count == 0


class TestAcceptanceReportInjectsAlignment:
    def test_aggregate_acceptance_injects_alignment_when_report_present(
        self, tmp_path
    ) -> None:
        """state.last_input_alignment_report → aggregate_acceptance pulls into report."""
        from explain_engine.engines.simulation import aggregate_acceptance
        from explain_engine.schema.state import CognitiveState

        g = ExplanationGraph(root_question="q")
        g.add_node(VariableNode(
            id="c_001", name="x", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
        g.add_node(VariableNode(
            id="p_001", name="p", description="d",
            abstraction_level=0, confidence=0.7, epistemic="observation",
        ))
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.7,
            mechanism_description="m",
        ))

        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
        state.last_input_alignment_report = InputAlignmentReport(
            question_subject="X",
            observation_subjects=["Y"],
            overlap_score=4,
            falsifiable_reason="ok",
        )

        report = aggregate_acceptance(state)
        # 4/5 = 0.8
        assert report.input_alignment == pytest.approx(0.8)
        assert report.falsifiable_reason == "ok"

    def test_aggregate_acceptance_alignment_none_when_no_report(self) -> None:
        """No state.last_input_alignment_report → AcceptanceReport.input_alignment is None."""
        from explain_engine.engines.simulation import aggregate_acceptance
        from explain_engine.schema.state import CognitiveState

        g = ExplanationGraph(root_question="q")
        g.add_node(VariableNode(
            id="c_001", name="x", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
        g.add_node(VariableNode(
            id="p_001", name="p", description="d",
            abstraction_level=0, confidence=0.7, epistemic="observation",
        ))
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.7,
            mechanism_description="m",
        ))

        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
        # 不设 last_input_alignment_report → 默认 None

        report = aggregate_acceptance(state)
        assert report.input_alignment is None
        assert report.falsifiable_reason is None
