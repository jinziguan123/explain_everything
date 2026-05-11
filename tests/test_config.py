from explain_agent.config import Settings


def _set_min_env(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "h")
    monkeypatch.setenv("MYSQL_USER", "u")
    monkeypatch.setenv("MYSQL_PASSWORD", "p")
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("QDRANT_HOST", "h")
    monkeypatch.setenv("WEAK_LLM_PROTOCOL", "openai")
    monkeypatch.setenv("WEAK_LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("WEAK_LLM_API_KEY", "sk-weak")
    monkeypatch.setenv("WEAK_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("STRONG_LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("STRONG_LLM_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setenv("STRONG_LLM_API_KEY", "sk-strong")
    monkeypatch.setenv("STRONG_LLM_MODEL", "claude-opus-4-7")


def test_settings_loads_from_env(monkeypatch):
    _set_min_env(monkeypatch)
    monkeypatch.setenv("MYSQL_HOST", "1.2.3.4")
    monkeypatch.setenv("CLICKHOUSE_HOST", "5.6.7.8")
    monkeypatch.setenv("QDRANT_HOST", "9.10.11.12")

    s = Settings()
    assert s.mysql_host == "1.2.3.4"
    assert s.clickhouse_host == "5.6.7.8"
    assert s.qdrant_host == "9.10.11.12"
    assert s.weak_llm.protocol == "openai"
    assert s.weak_llm.model == "deepseek-chat"
    assert s.strong_llm.protocol == "anthropic"
    assert s.strong_llm.model == "claude-opus-4-7"


def test_mysql_url_built_correctly(monkeypatch):
    _set_min_env(monkeypatch)
    s = Settings()
    url = s.mysql_explain_url()
    assert url.startswith("mysql+pymysql://u:p@h:3306/explain_agent")


def test_invalid_llm_protocol_raises(monkeypatch):
    _set_min_env(monkeypatch)
    monkeypatch.setenv("WEAK_LLM_PROTOCOL", "ollama")
    import pytest
    with pytest.raises(Exception):
        Settings()
