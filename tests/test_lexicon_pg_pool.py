"""Phase 17.1: lexicon_pg.py + lexicon_pg_schema.py + conftest pg_container/reset_pg tests."""
from __future__ import annotations

import shutil
import subprocess

import pytest


def _docker_daemon_running() -> bool:
    """检 docker binary 在 + daemon 真在跑 (`docker info` exit 0)."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=3, check=False
        )
    except Exception:
        return False
    return result.returncode == 0


_skip_no_docker = pytest.mark.skipif(
    not _docker_daemon_running(),
    reason="Docker daemon not running (testcontainers requires it)",
)


class TestSchemaConstants:
    def test_lexicon_pg_schema_constants_match_init_sql(self):
        from explain_engine.persistence.lexicon_pg_schema import DDL_INIT_SQL
        assert isinstance(DDL_INIT_SQL, str)
        assert len(DDL_INIT_SQL) > 100
        # pgvector extension
        assert "CREATE EXTENSION" in DDL_INIT_SQL
        assert "vector" in DDL_INIT_SQL
        # 3 tables
        assert "CREATE TABLE" in DDL_INIT_SQL
        assert "variables" in DDL_INIT_SQL
        assert "lexicon_merge_audit" in DDL_INIT_SQL
        assert "lexicon_meta" in DDL_INIT_SQL
        # embedding 1024 dim
        assert "vector(1024)" in DDL_INIT_SQL
        # 4 indexes
        assert "hnsw" in DDL_INIT_SQL.lower()
        assert "gin" in DDL_INIT_SQL.lower()
        # updated_at trigger
        assert "trigger_set_updated_at" in DDL_INIT_SQL
        # meta seed
        assert "INSERT INTO lexicon_meta" in DDL_INIT_SQL


@_skip_no_docker
class TestPgContainerFixture:
    """Phase 17.1: testcontainers pgvector fixture sanity. Skip 没 Docker 机器."""

    def test_pg_container_fixture_provides_dsn(self, pg_container):
        import psycopg

        with psycopg.connect(pg_container) as conn:
            cur = conn.execute("SELECT 1")
            assert cur.fetchone()[0] == 1

    def test_pg_container_has_vector_extension(self, pg_container):
        import psycopg

        with psycopg.connect(pg_container) as conn:
            cur = conn.execute(
                "SELECT extname FROM pg_extension WHERE extname = 'vector'"
            )
            assert cur.fetchone() is not None

    def test_pg_container_has_variables_table(self, pg_container):
        import psycopg

        with psycopg.connect(pg_container) as conn:
            cur = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('variables', 'lexicon_merge_audit', 'lexicon_meta')"
            )
            tables = {row[0] for row in cur.fetchall()}
        assert tables == {"variables", "lexicon_merge_audit", "lexicon_meta"}
