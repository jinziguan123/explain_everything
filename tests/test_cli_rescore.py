"""Wave D.1: explain rescore CLI 测试."""

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


def _save_session_with_default_conf(sessions_dir: Path, sid: str) -> None:
    """Phase 9: 拆 legacy payload → storage_v2 (sessions_dir 仅作 fixture 签名)."""
    del sessions_dir  # 不再使用 (storage_v2 走 EXPLAIN_HOME)
    from explain_engine.persistence.storage_v2 import StorageV2
    meta = {
        "session_id": sid, "question": "q", "stage": "converged",
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
                "c_001": {
                    "id": "c_001", "name": "c", "description": "d",
                    "abstraction_level": 1, "confidence": 0.7,
                    "epistemic": "insight",
                },
                "d_001": {
                    "id": "d_001", "name": "d", "description": "d",
                    "abstraction_level": 2, "confidence": 0.6,
                    "epistemic": "inference",
                },
            },
            "edges": {
                "e_001": {
                    "id": "e_001", "source_node": "c_001", "target_node": "p_001",
                    "relation_type": "manifests_as",
                    "confidence": 0.7, "mechanism_description": "m1",
                },
                "e_002": {
                    "id": "e_002", "source_node": "d_001", "target_node": "c_001",
                    "relation_type": "causes",
                    "confidence": 0.6, "mechanism_description": "m2",
                },
            },
        },
        "budget_remaining": 0, "root_question": "q",
        "active_frontier": [], "insight_candidates": ["c_001"],
        "tick": 0, "last_gain_tick": 0,
        "last_gains": {}, "reasoning_trace": [],
    }
    sv2 = StorageV2()
    sv2.save_metadata(sid, meta)
    sv2.save_graph(sid, state)


class TestCLIRescore:
    def test_session_not_found(self, cli_env, runner) -> None:
        result = runner.invoke(app, ["rescore", "s_00000000"])
        assert result.exit_code == 1

    def test_rescore_overwrites_default_confidence(
        self, cli_env, runner, monkeypatch
    ) -> None:
        """rescore 应改 edge.confidence 并 save."""
        _save_session_with_default_conf(cli_env, "s_a1b2c3d4")

        async def fake_rescore(state, llm):
            for e in state.graph.edges.values():
                e.confidence = 1.0   # all 5/5
            return {eid: 1.0 for eid in state.graph.edges}

        monkeypatch.setattr(
            "explain_engine.engines.rescore.rescore_session", fake_rescore,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["rescore", "s_a1b2c3d4"])
        assert result.exit_code == 0, result.output
        # 重 load session 看 edge.confidence 真 persist
        from explain_engine.persistence.session import SessionStore
        store = SessionStore(directory=cli_env)
        session = store.load("s_a1b2c3d4")
        for e in session.state.graph.edges.values():
            assert e.confidence == pytest.approx(1.0)

    def test_rescore_handles_both_edge_types(
        self, cli_env, runner, monkeypatch
    ) -> None:
        """rescore 应处理 manifests_as + causes edges (都用 scoring.yaml)."""
        _save_session_with_default_conf(cli_env, "s_e5f6a7b8")
        edge_types_seen = []

        async def fake_rescore(state, llm):
            for e in state.graph.edges.values():
                edge_types_seen.append(e.relation_type)
                e.confidence = 0.8
            return {eid: 0.8 for eid in state.graph.edges}

        monkeypatch.setattr(
            "explain_engine.engines.rescore.rescore_session", fake_rescore,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["rescore", "s_e5f6a7b8"])
        assert result.exit_code == 0, result.output
        assert "manifests_as" in edge_types_seen
        assert "causes" in edge_types_seen

    def test_rescore_no_edges_returns_message(
        self, cli_env, runner, monkeypatch
    ) -> None:
        """空 graph (无 edges) → yellow message, exit 0."""
        del cli_env  # storage_v2 走 EXPLAIN_HOME
        from explain_engine.persistence.storage_v2 import StorageV2
        # 灌一个 graph 无 edges
        meta = {
            "session_id": "s_c9d8e7f6", "question": "q",
            "stage": "converged", "created_at": 1234567890.0,
            "updated_at": 1234567890.0,
        }
        state = {
            "graph": {
                "root_question": "q",
                "nodes": {},
                "edges": {},
            },
            "budget_remaining": 0, "root_question": "q",
            "active_frontier": [], "insight_candidates": [],
            "tick": 0, "last_gain_tick": 0,
            "last_gains": {}, "reasoning_trace": [],
        }
        sv2 = StorageV2()
        sv2.save_metadata("s_c9d8e7f6", meta)
        sv2.save_graph("s_c9d8e7f6", state)

        async def fake_rescore(state, llm):
            return {}

        monkeypatch.setattr(
            "explain_engine.engines.rescore.rescore_session", fake_rescore,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["rescore", "s_c9d8e7f6"])
        assert result.exit_code == 0, result.output
        assert "无" in result.output or "no" in result.output.lower()

    def test_rescore_llm_failure_exits_1(
        self, cli_env, runner, monkeypatch
    ) -> None:
        _save_session_with_default_conf(cli_env, "s_fa110001")
        from explain_engine.llm.errors import LLMError

        async def fake_rescore(state, llm):
            raise LLMError("API 挂了")

        monkeypatch.setattr(
            "explain_engine.engines.rescore.rescore_session", fake_rescore,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["rescore", "s_fa110001"])
        assert result.exit_code == 1

    def test_rescore_save_failure_exits_3(
        self, cli_env, runner, monkeypatch
    ) -> None:
        """OSError on save → exit 3, error message 含 'LLM cost 已消耗'."""
        _save_session_with_default_conf(cli_env, "s_99887766")

        async def fake_rescore(state, llm):
            return {"e_001": 0.8}

        monkeypatch.setattr(
            "explain_engine.engines.rescore.rescore_session", fake_rescore,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        # Patch SessionStore.save 抛 OSError
        def fake_save(self, session):
            raise OSError("disk full")
        monkeypatch.setattr(
            "explain_engine.persistence.session.SessionStore.save",
            fake_save,
        )
        result = runner.invoke(app, ["rescore", "s_99887766"])
        assert result.exit_code == 3
        assert "LLM cost" in result.output or "save" in result.output.lower()
