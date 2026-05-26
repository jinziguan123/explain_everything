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


async def _bulk_insert_dummies(conn, n: int, prefix: str = "v_dum") -> None:
    """Wave 6 test helper — executemany 批量 insert N 行无 embedding var.

    跑 ~100 行远程 PG <2s (避免 N 次 single-row insert 慢).
    embedding 全 NULL — Wave 6 dedup test 需要 N ≥ 100 触发, 但不一定需要
    所有行有 embedding (cross-join WHERE embedding IS NOT NULL 自然跳).
    """
    now = datetime.now(UTC)
    args = [
        (
            f"{prefix}{i:04d}", "测试 var", "用于 dedup test", 1, "insight",
            "test mech", "abc12345", "v1",
            1, 0.5, 0.7,
            now, now, ["s_test0001"], None,
        )
        for i in range(n)
    ]
    await conn.cursor().executemany(
        """INSERT INTO variables (
            global_id, name, description, abstraction_level, epistemic,
            canonical_mechanism, canonical_signature, canonical_model_ver,
            reuse_count, avg_essentialness, avg_consistency,
            first_seen_at, last_seen_at, source_sessions, embedding
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        args,
    )


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


# ── Wave 6 Task 6.3: flush_count 阈值 5 触发 ────────────────────────────


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestMaybeLazyDedupIncrementsCountThenRuns:
    """Phase 17.1 Task 6.3: N >= 100 时 flush_count_since 阈值算法.

    阈值 logic (lexicon_pg._maybe_lazy_dedup):
      - flush_count < 5 → +1, return 0
      - flush_count >= 5 → 跑 _retroactive_dedup_pg + reset 0

    Test scenario (reset_pg 起始 flush_count = 0):
      调用次数 1: count 0 → 1, return 0
      调用次数 2: count 1 → 2, return 0
      调用次数 3: count 2 → 3, return 0
      调用次数 4: count 3 → 4, return 0
      调用次数 5: count 4 → 5, return 0
      调用次数 6: count 5 >= 5, 跑 dedup → reset 0, last_retro_at = NOW()

    Task 6.4 前 _retroactive_dedup_pg 是 stub 返 0, 所以 merged 字段 == 0;
    但 reset + last_retro_at 写入 行为应该已就绪.
    """

    @pytest.mark.asyncio
    async def test_increments_then_runs_after_threshold(self):
        from explain_engine.persistence.lexicon_pg import (
            _maybe_lazy_dedup,
            get_async_pool,
        )

        pool = await get_async_pool()
        # 批量 insert 110 dummy var (跨 N>=100 阈值; executemany 1 round)
        async with pool.connection() as conn:
            await _bulk_insert_dummies(conn, 110)

        # 前 5 次 — flush_count_since 累 1→5, return 0, last_retro_at 仍 NULL
        for expected_count in range(1, 6):
            merged = await _maybe_lazy_dedup(pool)
            assert merged == 0, f"call #{expected_count} 应 return 0 (count<阈值)"
            async with pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT flush_count_since, last_retro_dedup_at FROM lexicon_meta WHERE id=1"
                )
                row = await cur.fetchone()
            assert row[0] == expected_count, (
                f"call #{expected_count}: flush_count 应 {expected_count}, got {row[0]}"
            )
            assert row[1] is None, "未到阈值, last_retro_at 应仍 NULL"

        # 第 6 次 — flush_count 已 5 >= 5, 触发 dedup, reset 0, 写 last_retro_at
        merged = await _maybe_lazy_dedup(pool)
        assert merged == 0  # Task 6.2 stub 返 0; Task 6.4 后真合 sim>0.85 才 > 0
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT flush_count_since, last_retro_dedup_at FROM lexicon_meta WHERE id=1"
            )
            row = await cur.fetchone()
        assert row[0] == 0, f"第 6 次后 flush_count_since 应 reset 0, got {row[0]}"
        assert row[1] is not None, "第 6 次后 last_retro_dedup_at 应写 NOW()"
