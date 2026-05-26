"""Phase 17.1: lexicon_pg.py + lexicon_pg_schema.py + conftest fixture tests."""
from __future__ import annotations

import os

import pytest

# 没设 EXPLAIN_TEST_DB_URL 时, lexicon_pg test 自动 SKIP (user 没建 explain_test 库).
_skip_no_test_db = pytest.mark.skipif(
    os.environ.get("EXPLAIN_TEST_DB_URL") is None,
    reason="EXPLAIN_TEST_DB_URL not set (see deploy/postgres/README.md '建 test db' 一节)",
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


@_skip_no_test_db
class TestPgTestDsnFixture:
    """Phase 17.1: pg_test_dsn fixture (远程 explain_test db) sanity."""

    def test_pg_test_dsn_fixture_provides_dsn(self, pg_test_dsn):
        import psycopg

        assert pg_test_dsn is not None
        with psycopg.connect(pg_test_dsn) as conn:
            cur = conn.execute("SELECT 1")
            assert cur.fetchone()[0] == 1

    def test_pg_test_dsn_has_vector_extension(self, pg_test_dsn):
        import psycopg

        with psycopg.connect(pg_test_dsn) as conn:
            cur = conn.execute(
                "SELECT extname FROM pg_extension WHERE extname = 'vector'"
            )
            assert cur.fetchone() is not None

    def test_pg_test_dsn_has_required_tables(self, pg_test_dsn):
        import psycopg

        with psycopg.connect(pg_test_dsn) as conn:
            cur = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('variables', 'lexicon_merge_audit', 'lexicon_meta')"
            )
            tables = {row[0] for row in cur.fetchall()}
        assert tables == {"variables", "lexicon_merge_audit", "lexicon_meta"}


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestResetPgFixture:
    """Phase 17.1: reset_pg TRUNCATE 跨 test 隔离验证 (含 3 层 safety guard)."""

    def test_reset_pg_truncates_variables_on_entry(self, pg_test_dsn):
        """每 test 进入时 variables 表空 (reset_pg fixture 已 TRUNCATE)."""
        import psycopg

        with psycopg.connect(pg_test_dsn) as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM variables").fetchone()[0]
        assert cnt == 0

    def test_reset_pg_sets_explain_db_url_env(self, pg_test_dsn):
        """reset_pg fixture 同时 monkeypatch EXPLAIN_DB_URL = pg_test_dsn (让 lexicon_pg 连 test db)."""
        assert os.environ.get("EXPLAIN_DB_URL") == pg_test_dsn

    def test_reset_pg_resets_lexicon_meta_state(self, pg_test_dsn):
        """reset_pg fixture 重置 lexicon_meta.flush_count_since=0 + last_retro_dedup_at=NULL."""
        import psycopg

        with psycopg.connect(pg_test_dsn) as conn:
            cur = conn.execute(
                "SELECT flush_count_since, last_retro_dedup_at FROM lexicon_meta WHERE id = 1"
            )
            row = cur.fetchone()
        assert row[0] == 0
        assert row[1] is None


# ── Wave 2: lexicon_pg.py core CRUD ─────────────────────────────────────


class TestLexiconDBError:
    """Phase 17.1 Task 2.1: LexiconDBError 异常类."""

    def test_lexicon_db_error_is_exception(self):
        from explain_engine.persistence.lexicon_pg import LexiconDBError

        assert issubclass(LexiconDBError, Exception)
        err = LexiconDBError("oops")
        assert str(err) == "oops"
