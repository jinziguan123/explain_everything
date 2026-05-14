"""Settings + make_llm_client factory test。

Phase 5: LLM 配置走 LLM_PROTOCOL / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 四元组。
Settings 类只剩 runtime + sessions_dir 字段（LLM 配置直接读 env 由 make_llm_client
解析）。make_llm_client 的测试覆盖在 test_llm_client_factory.py。
"""

from pathlib import Path

from explain_engine.config import Settings


class TestSettings:
    def test_defaults(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_BUDGET", "30")
        s = Settings()
        assert s.default_budget == 30

    def test_sessions_dir_default(self, monkeypatch) -> None:
        monkeypatch.delenv("SESSIONS_DIR", raising=False)
        s = Settings()
        # 默认相对路径 ./sessions
        assert s.sessions_dir == Path("./sessions")

    def test_sessions_dir_env_override(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        s = Settings()
        assert s.sessions_dir == tmp_path
