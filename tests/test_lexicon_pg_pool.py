"""Phase 17.1: lexicon_pg.py + lexicon_pg_schema.py + conftest pg_container/reset_pg tests."""
from __future__ import annotations


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
