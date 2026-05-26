"""Phase 17.1 Wave 6: lazy retroactive dedup tests (Track C).

每 task 一 TestXxx class, DB 涉及的标 `@_skip_no_test_db` + `@reset_pg`.
不 mock psycopg — 真 PG (远程 explain_test db, .env EXPLAIN_TEST_DB_URL).

阈值: N > 100 + flush_count_since >= 5 才跑 N² cross-join dedup.
合并 winner = 早 first_seen, loser 入 winner 后 DELETE loser, audit 入
lexicon_merge_audit 表.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

# 没设 EXPLAIN_TEST_DB_URL 时, lexicon_pg test 自动 SKIP.
_skip_no_test_db = pytest.mark.skipif(
    os.environ.get("EXPLAIN_TEST_DB_URL") is None,
    reason="EXPLAIN_TEST_DB_URL not set (see deploy/postgres/README.md '建 test db' 一节)",
)


def _sample_var(
    global_id: str = "v_ddup0001",
    first_seen_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    **overrides,
) -> dict:
    """Wave 6 local helper — 构造一个完整 14+1 字段 var dict (含可选 embedding)."""
    now = first_seen_at or datetime.now(UTC)
    base = {
        "global_id": global_id,
        "name": "测试 var",
        "description": "用于 dedup test",
        "abstraction_level": 1,
        "epistemic": "insight",
        "canonical_mechanism": "test mech",
        "canonical_signature": "abc12345",
        "canonical_model_ver": "v1",
        "reuse_count": 1,
        "avg_essentialness": 0.5,
        "avg_consistency": 0.7,
        "first_seen_at": now,
        "last_seen_at": last_seen_at or now,
        "source_sessions": ["s_test0001"],
    }
    base.update(overrides)
    return base


# ── Wave 6 Task 6.1: lexicon_meta seed 验证 ─────────────────────────────


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestLexiconMetaSeed:
    """Phase 17.1 Task 6.1: init.sql 跑后 lexicon_meta 表已有 1 row id=1.

    reset_pg fixture 把 flush_count_since 重 0 + last_retro_dedup_at NULL,
    canonical_model_ver 默认 'v1' (init.sql 写死).
    """

    @pytest.mark.asyncio
    async def test_lexicon_meta_has_one_row_id_1(self):
        from explain_engine.persistence.lexicon_pg import get_async_pool

        pool = await get_async_pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, flush_count_since, last_retro_dedup_at, canonical_model_ver "
                "FROM lexicon_meta"
            )
            rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 1
        # reset_pg 已清
        assert rows[0][1] == 0
        assert rows[0][2] is None
        assert rows[0][3] == "v1"


# ── Wave 6 Task 6.2: _maybe_lazy_dedup N<100 skip ───────────────────────


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestMaybeLazyDedupSkipsBelow100:
    """Phase 17.1 Task 6.2: N < 100 时 _maybe_lazy_dedup 早 return.

    Verify:
      - 返 0 (没触发 dedup)
      - flush_count_since 未变 (强 set 10 验, 仍 10 — 证早 return 没碰 meta)
    """

    @pytest.mark.asyncio
    async def test_maybe_lazy_dedup_skips_when_n_below_100(self):
        from explain_engine.persistence.lexicon_pg import (
            _insert_var,
            _maybe_lazy_dedup,
            get_async_pool,
        )

        pool = await get_async_pool()
        async with pool.connection() as conn:
            # 仅 5 行 (远 < 100), insert 快
            for i in range(5):
                await _insert_var(
                    conn, _sample_var(global_id=f"v_below{i:03d}")
                )
            # 强 set flush_count_since = 10 (远超阈值 5)
            await conn.execute(
                "UPDATE lexicon_meta SET flush_count_since = 10 WHERE id = 1"
            )

        merged = await _maybe_lazy_dedup(pool)
        assert merged == 0

        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT flush_count_since, last_retro_dedup_at FROM lexicon_meta WHERE id = 1"
            )
            row = await cur.fetchone()
        # N<100 早 return — flush_count_since 仍 10 (没 increment), last_retro_at 仍 NULL
        assert row[0] == 10
        assert row[1] is None
