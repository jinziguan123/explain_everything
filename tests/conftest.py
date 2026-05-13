"""pytest fixtures for explain_engine tests.

约定：
- 不写测试 import 应用代码（避免 fixture 与 production code 互依赖）
- LLM provider 测试 mock，集成测放 @pytest.mark.integration
- session 落地用 tmp_path，绝不污染 sessions/
"""

import pytest


@pytest.fixture
def tmp_sessions_dir(tmp_path, monkeypatch):
    """提供临时 sessions 目录，自动 monkeypatch 到 SESSIONS_DIR 环境变量。"""
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
