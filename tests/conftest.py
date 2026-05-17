"""pytest fixtures for explain_engine tests.

约定：
- 不写测试 import 应用代码（避免 fixture 与 production code 互依赖）
- LLM provider 测试 mock，集成测放 @pytest.mark.integration
- session 落地用 tmp_path，绝不污染 sessions/ 或 ~/.explain/

Phase 9: autouse `isolated_explain_home` fixture 替老 SESSIONS_DIR fixture
pattern. 所有 test 透明 isolation 到 tmp_path/.explain (project_id=test_proj).
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_explain_home(tmp_path, monkeypatch):
    """Phase 9: All tests auto-isolate EXPLAIN_HOME to tmp_path/.explain.

    Replaces Phase 0-8 SESSIONS_DIR fixture pattern (now obsolete).
    Tests 不再需要手 set SESSIONS_DIR 或 EXPLAIN_HOME.
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_proj")
    yield


@pytest.fixture
def tmp_sessions_dir(tmp_path, monkeypatch):
    """向后兼容老测试 fixture: 返回 tmp_path/sessions (mkdir).

    Phase 9 起 SessionStore 不再读 SESSIONS_DIR (走 EXPLAIN_HOME instead),
    但部分老测试依然显式传 directory 参数 / 用此 fixture 作 path container.
    保留方便老测试 minimal 改动 (SESSIONS_DIR setenv 也保留, 但已无 production
    effect).
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
    return sessions_dir


@pytest.fixture
def mock_llm_response():
    """返回一个工具函数：给定 JSON dict，生成 mock LLM response。"""

    def _make(payload: dict, raw_text: str | None = None):
        from explain_engine.llm.client import Response

        return Response(
            text=raw_text if raw_text is not None else "",
            parsed=payload,
            model="mock",
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    return _make
