"""Phase 17.1: lexicon_pg.py + lexicon_pg_schema.py + conftest fixture tests."""
from __future__ import annotations

import os
from datetime import UTC, datetime

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


class TestGetDsn:
    """Phase 17.1 Task 2.2: _get_dsn 读 env / fallback default."""

    def test_get_dsn_returns_env_or_default(self, monkeypatch):
        from explain_engine.persistence.lexicon_pg import DEFAULT_DSN, _get_dsn

        # env 已设 → 返 env value
        monkeypatch.setenv("EXPLAIN_DB_URL", "postgresql://test:pw@host:5432/db")
        assert _get_dsn() == "postgresql://test:pw@host:5432/db"

        # env 未设 → 返 DEFAULT_DSN
        monkeypatch.delenv("EXPLAIN_DB_URL", raising=False)
        assert _get_dsn() == DEFAULT_DSN


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestGetAsyncPool:
    """Phase 17.1 Task 2.2: get_async_pool 单例 + lazy open."""

    @pytest.mark.asyncio
    async def test_get_async_pool_returns_singleton(self):
        from explain_engine.persistence.lexicon_pg import get_async_pool

        pool1 = await get_async_pool()
        pool2 = await get_async_pool()
        assert pool1 is pool2


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestVerifyConnectionOk:
    """Phase 17.1 Task 2.3: verify_connection 走真 PG 应不抛."""

    @pytest.mark.asyncio
    async def test_verify_connection_ok(self):
        from explain_engine.persistence.lexicon_pg import verify_connection

        # reset_pg fixture 已 set EXPLAIN_DB_URL = test dsn, verify 应成功不抛
        await verify_connection()


class TestVerifyConnectionFail:
    """Phase 17.1 Task 2.3: verify_connection 失败抛 LexiconDBError + 友好 Hint."""

    @pytest.mark.asyncio
    async def test_verify_connection_fails_with_friendly_hint(self, monkeypatch):
        from explain_engine.persistence import lexicon_pg
        from explain_engine.persistence.lexicon_pg import (
            LexiconDBError,
            verify_connection,
        )

        # 指向不存在的 PG (port 1, 无服务)
        monkeypatch.setenv(
            "EXPLAIN_DB_URL", "postgresql://nouser:nopass@127.0.0.1:1/nodb"
        )
        # 缩短 pool connect timeout 避免 test 跑太久
        monkeypatch.setenv("EXPLAIN_DB_CONNECT_TIMEOUT_S", "2")
        # 清单例 pool, 让重新用新 dsn 建
        lexicon_pg._async_pool = None
        try:
            with pytest.raises(LexiconDBError) as exc_info:
                await verify_connection()
            msg = str(exc_info.value)
            assert "无法连接" in msg
            assert "Hint" in msg
        finally:
            # 清理避免污染其他 test
            lexicon_pg._async_pool = None


def _sample_var(global_id: str = "v_test0001", **overrides) -> dict:
    """构造一个完整 14 字段 var dict (无 embedding), test fixture helper."""
    now = datetime.now(UTC)
    base = {
        "global_id": global_id,
        "name": "测试 var",
        "description": "用于 test",
        "abstraction_level": 1,
        "epistemic": "insight",
        "canonical_mechanism": "test mech",
        "canonical_signature": "abc12345",
        "canonical_model_ver": "v1",
        "reuse_count": 1,
        "avg_essentialness": 0.5,
        "avg_consistency": 0.7,
        "first_seen_at": now,
        "last_seen_at": now,
        "source_sessions": ["s_test0001"],
    }
    base.update(overrides)
    return base


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestInsertVar:
    """Phase 17.1 Task 2.4: _insert_var (不含 embedding 列, Wave 3 加)."""

    @pytest.mark.asyncio
    async def test_insert_var_basic_no_embedding(self):
        import psycopg

        from explain_engine.persistence.lexicon_pg import (
            _insert_var,
            get_async_pool,
        )

        var = _sample_var()
        pool = await get_async_pool()
        async with pool.connection() as conn:
            await _insert_var(conn, var)
            cur = await conn.execute(
                "SELECT global_id, name, source_sessions, embedding "
                "FROM variables WHERE global_id = %s",
                (var["global_id"],),
            )
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "v_test0001"
        assert row[1] == "测试 var"
        assert row[2] == ["s_test0001"]
        # embedding 列存为 NULL (Wave 3 才加)
        assert row[3] is None
        # 顺带 sanity: psycopg 真的连了一次
        assert isinstance(conn, psycopg.AsyncConnection)


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestFindVarById:
    """Phase 17.1 Task 2.5: _find_var_by_id 返 dict 或 None."""

    @pytest.mark.asyncio
    async def test_find_var_by_id_returns_dict(self):
        from explain_engine.persistence.lexicon_pg import (
            _find_var_by_id,
            _insert_var,
            get_async_pool,
        )

        var = _sample_var(global_id="v_findme01", name="找我")
        pool = await get_async_pool()
        async with pool.connection() as conn:
            await _insert_var(conn, var)
            row = await _find_var_by_id(conn, "v_findme01")
        assert row is not None
        assert isinstance(row, dict)
        assert row["global_id"] == "v_findme01"
        assert row["name"] == "找我"
        assert row["source_sessions"] == ["s_test0001"]
        assert row["reuse_count"] == 1

    @pytest.mark.asyncio
    async def test_find_var_by_id_returns_none_when_missing(self):
        from explain_engine.persistence.lexicon_pg import (
            _find_var_by_id,
            get_async_pool,
        )

        pool = await get_async_pool()
        async with pool.connection() as conn:
            row = await _find_var_by_id(conn, "v_nonexistent")
        assert row is None


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestUpdateVarFitness:
    """Phase 17.1 Task 2.6: _update_var_fitness UPDATE + trigger 自动 updated_at."""

    @pytest.mark.asyncio
    async def test_update_var_fitness_changes_fields_and_bumps_updated_at(self):
        from explain_engine.persistence.lexicon_pg import (
            _find_var_by_id,
            _insert_var,
            _update_var_fitness,
            get_async_pool,
        )

        var = _sample_var(global_id="v_update01")
        pool = await get_async_pool()
        # 分 2 connection: INSERT 跑完 commit, 让 UPDATE 拿新 txn timestamp
        # (PG NOW() = transaction_timestamp(), 同 txn 内返同值, 必须 commit
        # 才能让 trigger 看到新 NOW())
        async with pool.connection() as conn1:
            await _insert_var(conn1, var)
        async with pool.connection() as conn2:
            before = await _find_var_by_id(conn2, "v_update01")
            assert before["reuse_count"] == 1
            assert before["created_at"] == before["updated_at"]

        async with pool.connection() as conn3:
            new_last_seen = datetime.now(UTC)
            await _update_var_fitness(
                conn3,
                "v_update01",
                reuse_count=5,
                avg_essentialness=0.8,
                avg_consistency=0.9,
                last_seen_at=new_last_seen,
            )
        async with pool.connection() as conn4:
            after = await _find_var_by_id(conn4, "v_update01")

        assert after["reuse_count"] == 5
        assert abs(after["avg_essentialness"] - 0.8) < 1e-4
        assert abs(after["avg_consistency"] - 0.9) < 1e-4
        # trigger 自动 bump updated_at
        assert after["updated_at"] > before["updated_at"]
        # created_at 不变
        assert after["created_at"] == before["created_at"]


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestListVarsTopK:
    """Phase 17.1 Task 2.7: _list_vars_top_k ORDER BY composite score DESC."""

    @pytest.mark.asyncio
    async def test_list_vars_top_k_by_composite_score(self):
        from explain_engine.persistence.lexicon_pg import (
            _insert_var,
            _list_vars_top_k,
            get_async_pool,
        )

        # 3 var, composite = avg_ess * avg_cons * reuse_count
        # v_low:    0.1 * 0.1 * 1  = 0.01
        # v_mid:    0.5 * 0.5 * 4  = 1.00
        # v_top:    0.9 * 0.9 * 10 = 8.10
        var_low = _sample_var(
            global_id="v_low00001",
            avg_essentialness=0.1,
            avg_consistency=0.1,
            reuse_count=1,
        )
        var_mid = _sample_var(
            global_id="v_mid00001",
            avg_essentialness=0.5,
            avg_consistency=0.5,
            reuse_count=4,
        )
        var_top = _sample_var(
            global_id="v_top00001",
            avg_essentialness=0.9,
            avg_consistency=0.9,
            reuse_count=10,
        )
        pool = await get_async_pool()
        async with pool.connection() as conn:
            for v in (var_low, var_mid, var_top):
                await _insert_var(conn, v)
            top2 = await _list_vars_top_k(conn, k=2)

        assert len(top2) == 2
        gids = [r["global_id"] for r in top2]
        assert gids == ["v_top00001", "v_mid00001"]
        # v_low 应被截

    @pytest.mark.asyncio
    async def test_list_vars_top_k_default_k_20(self):
        """k 默认 20 — 全装得下 3 var."""
        from explain_engine.persistence.lexicon_pg import (
            _insert_var,
            _list_vars_top_k,
            get_async_pool,
        )

        pool = await get_async_pool()
        async with pool.connection() as conn:
            for i in range(3):
                await _insert_var(conn, _sample_var(global_id=f"v_dflt{i:04d}"))
            rows = await _list_vars_top_k(conn)
        assert len(rows) == 3


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestDeleteVar:
    """Phase 17.1 Task 2.8: _delete_var DELETE FROM variables WHERE global_id."""

    @pytest.mark.asyncio
    async def test_delete_var_removes_row(self):
        from explain_engine.persistence.lexicon_pg import (
            _delete_var,
            _find_var_by_id,
            _insert_var,
            get_async_pool,
        )

        var = _sample_var(global_id="v_delete01")
        pool = await get_async_pool()
        async with pool.connection() as conn:
            await _insert_var(conn, var)
            assert await _find_var_by_id(conn, "v_delete01") is not None
            deleted = await _delete_var(conn, "v_delete01")
        assert deleted is True

        async with pool.connection() as conn2:
            assert await _find_var_by_id(conn2, "v_delete01") is None

    @pytest.mark.asyncio
    async def test_delete_var_missing_returns_false(self):
        from explain_engine.persistence.lexicon_pg import (
            _delete_var,
            get_async_pool,
        )

        pool = await get_async_pool()
        async with pool.connection() as conn:
            result = await _delete_var(conn, "v_doesnt_exist")
        assert result is False


# ── Wave 3: embedding + pgvector cosine dedup ───────────────────────────


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestInsertVarWithEmbedding:
    """Phase 17.1 Task 3.1: _insert_var 支持 embedding 列 (vector(1024))."""

    @pytest.mark.asyncio
    async def test_insert_var_with_embedding(self):
        import numpy as np

        from explain_engine.persistence.lexicon_pg import (
            _insert_var,
            get_async_pool,
        )

        emb = np.random.rand(1024).astype(np.float32).tolist()
        var = _sample_var(global_id="v_emb0001", embedding=emb)
        pool = await get_async_pool()
        async with pool.connection() as conn:
            await _insert_var(conn, var)
            cur = await conn.execute(
                "SELECT embedding FROM variables WHERE global_id = %s",
                (var["global_id"],),
            )
            row = await cur.fetchone()
        assert row is not None
        assert row[0] is not None
        # pgvector adapter 应返 numpy array (1024,)
        assert len(row[0]) == 1024


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestFindDuplicate:
    """Phase 17.1 Task 3.2: _find_duplicate pgvector cosine query.

    pgvector <=> 是 cosine distance (0 = 完全相同, 2 = 完全反向).
    similarity = 1 - distance, threshold > 0.85 即相似度 > 85%.
    """

    @pytest.mark.asyncio
    async def test_find_duplicate_returns_similar_winner(self):
        import numpy as np

        from explain_engine.persistence.lexicon_pg import (
            _find_duplicate,
            _insert_var,
            get_async_pool,
        )

        rng = np.random.default_rng(seed=1)
        base = rng.random(1024).astype(np.float32)
        # near = base + 极小扰动 (sim > 0.95)
        near = base + rng.normal(0, 0.001, 1024).astype(np.float32)
        # far = 不同 random (sim ~ 0.7 在 1024 维 random)
        far = rng.random(1024).astype(np.float32)

        pool = await get_async_pool()
        async with pool.connection() as conn:
            await _insert_var(
                conn, _sample_var(global_id="v_near0001", embedding=near.tolist())
            )
            await _insert_var(
                conn, _sample_var(global_id="v_far00001", embedding=far.tolist())
            )
            # query with base, near 应胜出 (sim 接近 1)
            winner = await _find_duplicate(conn, base.tolist(), threshold=0.85)
        assert winner == "v_near0001"

    @pytest.mark.asyncio
    async def test_find_duplicate_returns_none_when_nothing_above_threshold(self):
        import numpy as np

        from explain_engine.persistence.lexicon_pg import (
            _find_duplicate,
            _insert_var,
            get_async_pool,
        )

        rng = np.random.default_rng(seed=2)
        # 库里 1 个明显远的 var
        far = rng.random(1024).astype(np.float32)
        query = rng.random(1024).astype(np.float32)

        pool = await get_async_pool()
        async with pool.connection() as conn:
            await _insert_var(
                conn, _sample_var(global_id="v_only0001", embedding=far.tolist())
            )
            # threshold = 0.99 几乎不可能 hit
            winner = await _find_duplicate(conn, query.tolist(), threshold=0.99)
        assert winner is None

    @pytest.mark.asyncio
    async def test_find_duplicate_skips_rows_with_null_embedding(self):
        """embedding IS NULL 的行不参与 cosine query (避免 NULL pg 报错)."""
        import numpy as np

        from explain_engine.persistence.lexicon_pg import (
            _find_duplicate,
            _insert_var,
            get_async_pool,
        )

        rng = np.random.default_rng(seed=3)
        emb = rng.random(1024).astype(np.float32)

        pool = await get_async_pool()
        async with pool.connection() as conn:
            # 1 行无 embedding (legacy)
            await _insert_var(
                conn, _sample_var(global_id="v_legacy01", embedding=None)
            )
            # 库里只 legacy, query 应返 None (没 embedding 可比)
            winner = await _find_duplicate(conn, emb.tolist(), threshold=0.5)
        assert winner is None


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestHNSWIndexUsed:
    """Phase 17.1 Task 3.3: 验 HNSW 索引被 query 命中 (EXPLAIN ANALYZE).

    N=20 时 PG planner 偏 Seq Scan (cost 12.75 < HNSW lookup overhead, 真实合理),
    所以本 test 用 `SET LOCAL enable_seqscan = off` 强制走 index, 目的是验证:
    1. idx_variables_embedding 索引在 schema 里真存在并可用
    2. <=> cosine query 形态 + 参数绑定真能命中 HNSW (而非 fallback Seq Scan)

    生产 N=10k+ 时 planner 自然倾向 HNSW (本 test 不验 planner 选择, 只验 index OK).
    """

    @pytest.mark.asyncio
    async def test_hnsw_index_used_in_query_plan(self):
        import numpy as np

        from explain_engine.persistence.lexicon_pg import (
            _insert_var,
            get_async_pool,
        )

        pool = await get_async_pool()
        async with pool.connection() as conn:
            for i in range(20):
                emb = np.random.rand(1024).astype(np.float32).tolist()
                await _insert_var(
                    conn,
                    _sample_var(global_id=f"v_idx{i:04d}", embedding=emb),
                )
            # 显式开 txn + SET LOCAL 强制 HNSW (N=20 时 planner 偏 Seq Scan)
            async with conn.transaction():
                await conn.execute("SET LOCAL enable_seqscan = off")
                query_emb = np.random.rand(1024).astype(np.float32).tolist()
                cur = await conn.execute(
                    "EXPLAIN ANALYZE SELECT * FROM variables "
                    "ORDER BY embedding <=> %s::vector LIMIT 1",
                    (query_emb,),
                )
                plan_lines = [row[0] for row in await cur.fetchall()]
        plan_text = "\n".join(plan_lines)
        # 强制 seqscan=off 后, 应走 HNSW index scan
        assert "idx_variables_embedding" in plan_text, (
            f"plan did not use HNSW index:\n{plan_text}"
        )
