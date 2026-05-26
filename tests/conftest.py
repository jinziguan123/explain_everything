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


@pytest.fixture(autouse=True)
def _disable_embedding_unless_marked(request, monkeypatch):
    """Phase 13 default: EXPLAIN_EMBEDDING_DISABLED=1 unless @pytest.mark.embedding.

    Prevents tests that touch lexicon flush from accidentally loading the
    4.3 GB BGE-M3 model. Tests that need real embedding must declare
    @pytest.mark.embedding marker, which opts them out of this disable.
    """
    if request.node.get_closest_marker("embedding") is None:
        monkeypatch.setenv("EXPLAIN_EMBEDDING_DISABLED", "1")
    else:
        # Explicit unset — tests marked @embedding should NOT inherit env from outer shell
        monkeypatch.delenv("EXPLAIN_EMBEDDING_DISABLED", raising=False)


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


# ── Phase 17.1 Wave 1: testcontainers pgvector container per test session ──
#
# Lazy: 只在 test 显式引用 pg_container fixture (或经 reset_pg 间接引用) 才 spin.
# 既有 1135 test 不触本 fixture, 启动速度不受影响.
#
# 首次跑: docker pull pgvector/pgvector:pg16 (~150MB), 30s-2min 取决于网络.
# 后续: container 复用整 test session, 各 test 经 reset_pg TRUNCATE 隔离.


@pytest.fixture(scope="session")
def pg_container():
    """Phase 17.1: pgvector container per session, 跑 DDL_INIT_SQL 起 schema."""
    pytest.importorskip("testcontainers.postgres")
    import psycopg
    from testcontainers.postgres import PostgresContainer

    from explain_engine.persistence.lexicon_pg_schema import DDL_INIT_SQL

    pg = PostgresContainer("pgvector/pgvector:pg16")
    pg.start()
    try:
        # testcontainers 默认返 SQLAlchemy 风格 dsn, 剥前缀给 psycopg3
        url = pg.get_connection_url()
        dsn = url.replace("postgresql+psycopg2://", "postgresql://")
        # Apply schema (CREATE EXTENSION + 3 tables + 4 indexes + trigger + meta seed)
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(DDL_INIT_SQL)
        yield dsn
    finally:
        pg.stop()
