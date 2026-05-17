"""CLI new (整合 Bootstrap + HITL + save) test."""

from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.llm.client import Response
from explain_engine.persistence.session import SessionStore


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _mock_llm_response(phenomena: list[dict]) -> Response:
    return Response(
        text="",
        parsed={"phenomena": phenomena},
        model="mock",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    """配置 sessions_dir + API key + provider = claude."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("LLM_MODEL", "claude-test")
    return sessions_dir


@pytest.fixture
def mock_llm_chat(monkeypatch):
    """Patch make_client → mock LLM."""

    def _setup(phenomena: list[dict]):
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value=_mock_llm_response(phenomena))
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client",
            lambda: mock_llm,
        )
        return mock_llm

    return _setup


@pytest.fixture
def mock_review_phenomena(monkeypatch):
    """Patch review_phenomena → 按策略返回。"""

    def _setup(keep_strategy: str = "all"):
        def _review(phenomena, console=None):
            if keep_strategy == "all":
                return phenomena
            elif keep_strategy == "none":
                return []
            elif keep_strategy == "first":
                return phenomena[:1]
            else:
                raise ValueError(f"unknown strategy: {keep_strategy}")

        monkeypatch.setattr("explain_engine.cli.review_phenomena", _review)

    return _setup


class TestCliNew:
    def test_new_full_flow_keep_all(
        self, runner, setup_env, mock_llm_chat, mock_review_phenomena
    ):
        del setup_env  # storage_v2 走 EXPLAIN_HOME, sessions_dir 不再用
        mock_llm_chat([
            {"name": "房价上涨", "description": "..."},
            {"name": "收入停滞", "description": "..."},
        ])
        mock_review_phenomena("all")

        result = runner.invoke(app, ["new", "为什么年轻人不消费"])

        assert result.exit_code == 0
        assert "Session" in result.output
        assert "已保存" in result.output

        # 加载 session 验证内容 (Phase 9: 计 SessionStore().list() 替老 glob)
        store = SessionStore()
        metas = store.list()
        assert len(metas) == 1
        loaded = store.load(metas[0].session_id)
        assert loaded.state.root_question == "为什么年轻人不消费"
        assert len(loaded.state.graph.nodes) == 2

    def test_new_drop_all(
        self, runner, setup_env, mock_llm_chat, mock_review_phenomena
    ):
        """HITL drop 全部后，session 仍然落地（含 0 phenomena）。"""
        del setup_env
        mock_llm_chat([{"name": "x", "description": "y"}])
        mock_review_phenomena("none")

        result = runner.invoke(app, ["new", "why?"])

        assert result.exit_code == 0
        store = SessionStore()
        metas = store.list()
        assert len(metas) == 1
        loaded = store.load(metas[0].session_id)
        assert len(loaded.state.graph.nodes) == 0

    def test_new_llm_failure_exits_1(
        self, runner, setup_env, mock_review_phenomena, monkeypatch
    ):
        """LLM 抛 LLMError 时 CLI exit 1，不落 session。"""
        from explain_engine.llm.errors import LLMError

        del setup_env

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(side_effect=LLMError("API down"))
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client",
            lambda: mock_llm,
        )
        mock_review_phenomena("all")

        result = runner.invoke(app, ["new", "why?"])

        assert result.exit_code == 1
        # 不应该有 session 落地
        assert SessionStore().list() == []

    def test_new_schema_failure_exits_2(
        self, runner, setup_env, mock_review_phenomena, monkeypatch
    ):
        """LLM 抛 SchemaValidationError 时 CLI exit 2。"""
        from explain_engine.llm.errors import SchemaValidationError

        del setup_env

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(side_effect=SchemaValidationError("missing field"))
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client",
            lambda: mock_llm,
        )
        mock_review_phenomena("all")

        result = runner.invoke(app, ["new", "why?"])

        assert result.exit_code == 2
        assert SessionStore().list() == []
