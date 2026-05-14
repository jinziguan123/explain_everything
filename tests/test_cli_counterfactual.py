"""Wave B.3: explain counterfactual CLI 测试."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from explain_engine.cli import app


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
    return sessions_dir


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _save_converged_session(sessions_dir: Path, sid: str, stage: str = "converged") -> None:
    payload = {
        "meta": {
            "session_id": sid, "question": "why",
            "stage": stage, "created_at": 1234567890.0,
            "updated_at": 1234567890.0,
        },
        "state": {
            "graph": {
                "root_question": "why",
                "nodes": {
                    "p_001": {
                        "id": "p_001", "name": "p", "description": "d",
                        "abstraction_level": 0, "confidence": 0.7,
                        "epistemic": "observation",
                    },
                    "c_001": {
                        "id": "c_001", "name": "c", "description": "d",
                        "abstraction_level": 1, "confidence": 0.7,
                        "epistemic": "insight",
                    },
                    "d_001": {
                        "id": "d_001", "name": "driver", "description": "d",
                        "abstraction_level": 2, "confidence": 0.6,
                        "epistemic": "inference",
                    },
                },
                "edges": {
                    "e_001": {
                        "id": "e_001", "source_node": "c_001",
                        "target_node": "p_001",
                        "relation_type": "manifests_as",
                        "confidence": 0.7, "mechanism_description": "m",
                    },
                    "e_002": {
                        "id": "e_002", "source_node": "d_001",
                        "target_node": "c_001",
                        "relation_type": "causes",
                        "confidence": 0.6, "mechanism_description": "m",
                    },
                },
            },
            "budget_remaining": 0, "root_question": "why",
            "active_frontier": [], "insight_candidates": ["c_001"],
            "tick": 0, "last_gain_tick": 0,
            "last_gains": {}, "reasoning_trace": [],
        },
    }
    (sessions_dir / f"{sid}.json").write_text(json.dumps(payload, ensure_ascii=False))


class TestCLICounterfactual:
    def test_session_not_found(self, cli_env, runner) -> None:
        result = runner.invoke(app, ["counterfactual", "s_nonexis", "x"])
        assert result.exit_code == 1

    def test_counterfactual_invokes_engine(self, cli_env, runner, monkeypatch) -> None:
        _save_converged_session(cli_env, "s_eeeeffff")
        async def fake_substitute(state, text, llm):
            from explain_engine.engines.counterfactual import CounterfactualReport
            from explain_engine.engines.intervention_parser import ParsedIntervention
            return CounterfactualReport(
                intervention_text=text,
                parsed=ParsedIntervention(existing_refs=["d_001"], new_concepts=[]),
                removed_node_ids=["d_001"],
                added_node_ids=[], added_predicted_L0_ids=[],
                baseline_acts={"c_001": 1.0, "p_001": 0.7},
                counterfactual_acts={"c_001": 1.0, "p_001": 0.7},
                activation_diff={"p_001": 0.0},
                alt_narrative=None,
            )
        monkeypatch.setattr(
            "explain_engine.engines.counterfactual.substitute", fake_substitute,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["counterfactual", "s_eeeeffff", "如果删 d_001"])
        assert result.exit_code == 0
        assert "d_001" in result.stdout or "Removed" in result.stdout

    def test_parser_failure_exits_2(self, cli_env, runner, monkeypatch) -> None:
        _save_converged_session(cli_env, "s_aaaaaaaa")
        async def fake_substitute(state, text, llm):
            raise ValueError("无法解析")
        monkeypatch.setattr(
            "explain_engine.engines.counterfactual.substitute", fake_substitute,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["counterfactual", "s_aaaaaaaa", "废话"])
        assert result.exit_code == 2

    def test_llm_failure_exits_1(self, cli_env, runner, monkeypatch) -> None:
        _save_converged_session(cli_env, "s_bbbbbbbb")
        from explain_engine.llm.errors import LLMError
        async def fake_substitute(state, text, llm):
            raise LLMError("API 失败")
        monkeypatch.setattr(
            "explain_engine.engines.counterfactual.substitute", fake_substitute,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["counterfactual", "s_bbbbbbbb", "x"])
        assert result.exit_code == 1

    def test_stage_not_done_or_converged_exits_4(self, cli_env, runner, monkeypatch) -> None:
        """Phase 7 design §5.7: counterfactual 要求 stage ∈ {done, converged}."""
        _save_converged_session(cli_env, "s_cccccccc", stage="bootstrap_pending")
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["counterfactual", "s_cccccccc", "x"])
        assert result.exit_code == 4
