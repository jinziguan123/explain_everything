"""Settings + make_llm_client factory test。

Phase 5: LLM 配置走 LLM_PROTOCOL / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 四元组。
Settings 类只剩 runtime 字段（LLM 配置直接读 env 由 make_llm_client 解析）。
Phase 9 Wave A.1 fix · I2: 移除 sessions_dir 字段 (storage_v2 走 EXPLAIN_HOME).
make_llm_client 的测试覆盖在 test_llm_client_factory.py。
"""

from explain_engine.config import Settings


class TestSettings:
    def test_defaults(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_BUDGET", "30")
        s = Settings()
        assert s.default_budget == 30

    def test_sessions_dir_removed(self, monkeypatch) -> None:
        """Phase 9 Wave A.1 fix · I2: sessions_dir 字段已删."""
        monkeypatch.delenv("SESSIONS_DIR", raising=False)
        s = Settings()
        assert not hasattr(s, "sessions_dir")

    def test_legacy_sessions_dir_env_silently_ignored(
        self, monkeypatch, tmp_path
    ) -> None:
        """Phase 9 Wave A.1 fix · I2: 老 SESSIONS_DIR env 被 extra='ignore' 容忍,
        不抛 error (storage_v2 走 EXPLAIN_HOME)."""
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        s = Settings()  # 不抛 ValidationError
        assert not hasattr(s, "sessions_dir")
