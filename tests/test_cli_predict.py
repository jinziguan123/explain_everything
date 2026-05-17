"""Wave B.2: explain predict CLI 测试."""

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


def _save_converged_session(sessions_dir: Path, sid: str) -> None:
    """Phase 9: 拆 legacy payload → storage_v2.save_metadata + save_graph."""
    del sessions_dir  # 不再使用 (storage_v2 走 EXPLAIN_HOME)
    from explain_engine.persistence.storage_v2 import StorageV2
    meta = {
        "session_id": sid, "question": "why",
        "stage": "converged", "created_at": 1234567890.0,
        "updated_at": 1234567890.0,
    }
    state = {
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
            },
            "edges": {
                "e_001": {
                    "id": "e_001", "source_node": "c_001",
                    "target_node": "p_001",
                    "relation_type": "manifests_as",
                    "confidence": 0.7, "mechanism_description": "m",
                },
            },
        },
        "budget_remaining": 0, "root_question": "why",
        "active_frontier": [], "insight_candidates": ["c_001"],
        "tick": 0, "last_gain_tick": 0,
        "last_gains": {}, "reasoning_trace": [],
    }
    sv2 = StorageV2()
    sv2.save_metadata(sid, meta)
    sv2.save_graph(sid, state)


class TestCLIPredict:
    def test_session_not_found(self, cli_env, runner) -> None:
        result = runner.invoke(app, ["predict", "s_nonexistent", "x"])
        assert result.exit_code == 1

    def test_predict_invokes_engine(self, cli_env, runner, monkeypatch) -> None:
        _save_converged_session(cli_env, "s_aaaabbbb")
        async def fake_predict(state, text, llm):
            from explain_engine.engines.intervention_parser import ParsedIntervention
            from explain_engine.engines.prediction import PredictionReport
            return PredictionReport(
                intervention_text=text,
                parsed=ParsedIntervention(existing_refs=["c_001"], new_concepts=[]),
                new_node_ids=[], predicted_L0_ids=[],
                activated_existing_L0=["p_001"],
                propagation_acts={"c_001": 1.0, "p_001": 0.7},
                decay_trace=[],
            )
        monkeypatch.setattr(
            "explain_engine.engines.prediction.predict", fake_predict,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["predict", "s_aaaabbbb", "x"])
        assert result.exit_code == 0
        assert "p_001" in result.stdout

    def test_parser_failure_exits_2(self, cli_env, runner, monkeypatch) -> None:
        _save_converged_session(cli_env, "s_bbbbcccc")
        async def fake_predict(state, text, llm):
            raise ValueError("无法解析 intervention")
        monkeypatch.setattr(
            "explain_engine.engines.prediction.predict", fake_predict,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["predict", "s_bbbbcccc", "废话"])
        assert result.exit_code == 2

    def test_llm_failure_exits_1(self, cli_env, runner, monkeypatch) -> None:
        _save_converged_session(cli_env, "s_ccccdddd")
        from explain_engine.llm.errors import LLMError
        async def fake_predict(state, text, llm):
            raise LLMError("API 失败")
        monkeypatch.setattr(
            "explain_engine.engines.prediction.predict", fake_predict,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["predict", "s_ccccdddd", "x"])
        assert result.exit_code == 1

    def test_stage_not_done_or_converged_exits_4(self, cli_env, runner, monkeypatch) -> None:
        """Phase 7 design §5.7: predict 要求 stage ∈ {done, converged}."""
        del cli_env  # 不再使用 (storage_v2 走 EXPLAIN_HOME)
        from explain_engine.persistence.storage_v2 import StorageV2
        sid = "s_eeeeffff"
        meta = {
            "session_id": sid, "question": "q", "stage": "bootstrap_pending",
            "created_at": 1234567890.0, "updated_at": 1234567890.0,
        }
        state = {
            "graph": {
                "root_question": "q",
                "nodes": {
                    "p_001": {
                        "id": "p_001", "name": "p", "description": "d",
                        "abstraction_level": 0, "confidence": 0.7,
                        "epistemic": "observation",
                    },
                },
                "edges": {},
            },
            "budget_remaining": 0, "root_question": "q",
            "active_frontier": [], "insight_candidates": [],
            "tick": 0, "last_gain_tick": 0,
            "last_gains": {}, "reasoning_trace": [],
        }
        sv2 = StorageV2()
        sv2.save_metadata(sid, meta)
        sv2.save_graph(sid, state)

        # Mock LLM (won't be called)
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["predict", sid, "x"])
        assert result.exit_code == 4
        assert "stage" in result.stdout or "stage" in result.stderr

    def test_predict_persists_graph_mutations(self, cli_env, runner, monkeypatch) -> None:
        """predict 副作用应 save 到 session JSON."""
        _save_converged_session(cli_env, "s_ddddeeee")
        async def fake_predict(state, text, llm):
            # 模拟加 driver node 副作用
            from explain_engine.schema.nodes import VariableNode
            state.graph.add_node(VariableNode(
                id="d_999", name="new_driver", description="d",
                abstraction_level=2, confidence=0.7, epistemic="speculation",
            ))
            from explain_engine.engines.intervention_parser import ParsedIntervention
            from explain_engine.engines.prediction import PredictionReport
            return PredictionReport(
                intervention_text=text,
                parsed=ParsedIntervention(existing_refs=[], new_concepts=[]),
                new_node_ids=["d_999"], predicted_L0_ids=[],
                activated_existing_L0=[],
                propagation_acts={"d_999": 1.0},
                decay_trace=[],
            )
        monkeypatch.setattr(
            "explain_engine.engines.prediction.predict", fake_predict,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["predict", "s_ddddeeee", "x"])
        assert result.exit_code == 0
        # 重 load session
        from explain_engine.persistence.session import SessionStore
        store = SessionStore(directory=cli_env)
        session = store.load("s_ddddeeee")
        assert "d_999" in session.state.graph.nodes

    def test_predict_prints_parser_output(self, cli_env, runner, monkeypatch) -> None:
        """Fix A: CLI 显示 parser 解析结果 (existing_refs + new_concepts)."""
        _save_converged_session(cli_env, "s_aaaaaaaa")  # 已有 c_001/p_001
        async def fake_predict(state, text, llm):
            from explain_engine.engines.intervention_parser import (
                NewConceptSpec,
                ParsedIntervention,
            )
            from explain_engine.engines.prediction import PredictionReport
            return PredictionReport(
                intervention_text=text,
                parsed=ParsedIntervention(
                    existing_refs=["c_001"],
                    new_concepts=[
                        NewConceptSpec(name="X", description="d", expected_level=2),
                    ],
                ),
                new_node_ids=["d_999"],
                predicted_L0_ids=[],
                activated_existing_L0=["p_001"],
                propagation_acts={"c_001": 1.0, "p_001": 0.7},
                decay_trace=[],
            )
        monkeypatch.setattr(
            "explain_engine.engines.prediction.predict", fake_predict,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["predict", "s_aaaaaaaa", "x"])
        assert result.exit_code == 0
        # 解析 section 出现
        assert "解析" in result.stdout or "parser" in result.stdout.lower()
        assert "existing_refs" in result.stdout
        assert "new_concepts" in result.stdout
        assert "c_001" in result.stdout

    def test_predict_fallback_message_when_all_empty(
        self, cli_env, runner, monkeypatch,
    ) -> None:
        """Fix C: 当 new_node_ids / predicted_L0_ids / activated_existing_L0 全空, 印 fallback."""
        _save_converged_session(cli_env, "s_bbbbbbbb")
        async def fake_predict(state, text, llm):
            from explain_engine.engines.intervention_parser import ParsedIntervention
            from explain_engine.engines.prediction import PredictionReport
            return PredictionReport(
                intervention_text=text,
                parsed=ParsedIntervention(existing_refs=["c_001"], new_concepts=[]),
                new_node_ids=[], predicted_L0_ids=[],
                activated_existing_L0=[],  # 全空
                propagation_acts={},
                decay_trace=[],
            )
        monkeypatch.setattr(
            "explain_engine.engines.prediction.predict", fake_predict,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["predict", "s_bbbbbbbb", "x"])
        assert result.exit_code == 0
        assert "无明显 effect" in result.stdout or "建议" in result.stdout
