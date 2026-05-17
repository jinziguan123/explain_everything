"""CLI explain compress 集成测试。"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.llm.client import Response
from explain_engine.llm.errors import LLMError, SchemaValidationError
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState

runner = CliRunner()


def _setup_bootstrap_session(tmp_dir: Path) -> str:
    state = CognitiveState.bootstrap("为什么年轻人不消费", budget=20)
    for i in range(1, 6):
        state.graph.add_node(
            VariableNode(
                id=f"p_{i:03d}", name=f"p{i}", description=f"d{i}",
                abstraction_level=0, confidence=0.7, epistemic="observation",
            )
        )
    meta = SessionMeta.new(question="为什么年轻人不消费")
    sid = meta.session_id
    store = SessionStore(directory=tmp_dir)
    store.save(Session(meta=meta, state=state))
    return sid


def _mock_settings_to_tmp(monkeypatch: pytest.MonkeyPatch, tmp: Path) -> None:
    # Phase 9: storage_v2 reads EXPLAIN_HOME (autouse fixture sets it).
    # `tmp` 仅作 fixture 签名兼容, 实际写入由 storage_v2 决定 path.
    del tmp
    from explain_engine import cli as cli_mod
    from explain_engine.config import Settings

    def fake_get_store() -> SessionStore:
        return SessionStore()

    monkeypatch.setattr(cli_mod, "_get_store", fake_get_store)
    monkeypatch.setattr(
        cli_mod, "Settings", lambda: Settings(default_budget=20)
    )


def _comp_response() -> Response:
    return Response(
        text="",
        parsed={"candidates": [
            {"name": "abs_1", "description": "d1",
             "coverage": [{"concrete_id": "p_001", "mechanism": "m1"},
                          {"concrete_id": "p_002", "mechanism": "m2"}]},
            {"name": "abs_2", "description": "d2",
             "coverage": [{"concrete_id": "p_003", "mechanism": "m3"},
                          {"concrete_id": "p_004", "mechanism": "m4"}]},
        ]},
        model="t", usage={"input_tokens": 0, "output_tokens": 0},
    )


def _score_response(s: int = 4) -> Response:
    return Response(
        text="", parsed={"score": s, "rationale": "ok"},
        model="t", usage={"input_tokens": 0, "output_tokens": 0},
    )


class TestCompress:
    def test_full_flow_keep_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = _setup_bootstrap_session(tmp_path)
        _mock_settings_to_tmp(monkeypatch, tmp_path)

        fake_llm = AsyncMock()
        # 1 compression call + 4 scoring calls (2 candidates × 2 edges each)
        fake_llm.chat.side_effect = [_comp_response()] + [_score_response()] * 4

        with patch("explain_engine.cli.make_llm_client", return_value=fake_llm), \
             patch("rich.prompt.Prompt.ask", side_effect=["k", "k"]):
            result = runner.invoke(app, ["compress", sid])
        assert result.exit_code == 0, result.output

        store = SessionStore(directory=tmp_path)
        s = store.load(sid)
        assert s.meta.stage == "done"
        assert sum(1 for n in s.state.graph.nodes.values()
                   if n.abstraction_level == 1) == 2

    def test_session_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_settings_to_tmp(monkeypatch, tmp_path)
        result = runner.invoke(app, ["compress", "s_00000000"])
        assert result.exit_code == 1

    def test_stage_done_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = _setup_bootstrap_session(tmp_path)
        _mock_settings_to_tmp(monkeypatch, tmp_path)
        store = SessionStore(directory=tmp_path)
        s = store.load(sid)
        s.meta.stage = "done"
        store.save(s)
        result = runner.invoke(app, ["compress", sid])
        assert result.exit_code == 4

    def test_resume_insight_pending_skips_llm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = _setup_bootstrap_session(tmp_path)
        _mock_settings_to_tmp(monkeypatch, tmp_path)
        # 模拟已经 compress + score 跑完，stage=insight_pending
        store = SessionStore(directory=tmp_path)
        s = store.load(sid)
        s.state.graph.add_node(VariableNode(
            id="c_001", name="abs_1", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
        s.state.insight_candidates = ["c_001"]
        s.meta.stage = "insight_pending"
        store.save(s)

        fake_llm = AsyncMock()
        # LLM 不该被调用
        with patch("explain_engine.cli.make_llm_client", return_value=fake_llm), \
             patch("rich.prompt.Prompt.ask", side_effect=["k"]):
            result = runner.invoke(app, ["compress", sid])
        assert result.exit_code == 0
        assert fake_llm.chat.await_count == 0
        s2 = store.load(sid)
        assert s2.meta.stage == "done"

    def test_llm_error_exit_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = _setup_bootstrap_session(tmp_path)
        _mock_settings_to_tmp(monkeypatch, tmp_path)
        fake_llm = AsyncMock()
        fake_llm.chat.side_effect = LLMError("network down")
        with patch("explain_engine.cli.make_llm_client", return_value=fake_llm):
            result = runner.invoke(app, ["compress", sid])
        assert result.exit_code == 1

    def test_schema_error_exit_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = _setup_bootstrap_session(tmp_path)
        _mock_settings_to_tmp(monkeypatch, tmp_path)
        fake_llm = AsyncMock()
        fake_llm.chat.side_effect = SchemaValidationError("bad output")
        with patch("explain_engine.cli.make_llm_client", return_value=fake_llm):
            result = runner.invoke(app, ["compress", sid])
        assert result.exit_code == 2
