"""Phase 17.1 Wave 7: lexicon_migrations.py — variables.json → PG migration tests.

每 task 独立 TestXxx class, 用 reset_pg fixture (TRUNCATE per test) +
EXPLAIN_DB_URL 透明指 explain_test 库. 没设 EXPLAIN_TEST_DB_URL 时自动 skip.

绝不连真生产 explain 库 — reset_pg fixture guard:
  1. EXPLAIN_TEST_DB_URL 未设 → skip
  2. dsn 必须含 '_test' 子串 (explain_test)
  3. dsn ≠ EXPLAIN_DB_URL (production)
"""
from __future__ import annotations

import json
import os
from typing import Any

import pytest

# 没设 EXPLAIN_TEST_DB_URL 时 skip (同 test_lexicon_pg_api.py)
_skip_no_test_db = pytest.mark.skipif(
    os.environ.get("EXPLAIN_TEST_DB_URL") is None,
    reason="EXPLAIN_TEST_DB_URL not set (见 deploy/postgres/README.md '建 test db' 一节)",
)


def _make_legacy_var(
    global_id: str = "v_abc12345",
    name: str = "长期不确定性",
    abstraction_level: int = 1,
    epistemic: str = "insight",
    reuse_count: int = 3,
    avg_essentialness: float = 0.75,
    avg_consistency: float = 0.65,
    first_seen_at: str = "2026-05-20T10:30:00+00:00",
    last_seen_at: str = "2026-05-25T12:00:00+00:00",
    source_sessions: list[str] | None = None,
    embedding: list[float] | None = None,
    description: str | None = None,
    canonical_mechanism: str | None = None,
) -> dict[str, Any]:
    """Build a single legacy var dict with full schema (跟 ~/.explain 真 variables.json
    格式一致): fitness nested + source_sessions + optional embedding.
    """
    return {
        "global_id": global_id,
        "name": name,
        "description": description or f"{name} 的描述",
        "abstraction_level": abstraction_level,
        "epistemic": epistemic,
        "canonical_mechanism": canonical_mechanism or "通常 cause A, B; 由 C cause",
        "fitness": {
            "reuse_count": reuse_count,
            "avg_essentialness": avg_essentialness,
            "avg_consistency": avg_consistency,
            "first_seen_at": first_seen_at,
            "last_seen_at": last_seen_at,
        },
        "source_sessions": source_sessions or ["s_aaa11111"],
        "embedding": embedding,
    }


def _write_legacy_lexicon(storage_knowledge_dir, vars_list: list[dict[str, Any]]):
    """Write lexicon JSON to storage.knowledge_dir() / variables.json."""
    storage_knowledge_dir.mkdir(parents=True, exist_ok=True)
    path = storage_knowledge_dir / "variables.json"
    path.write_text(
        json.dumps({"variables": vars_list}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


# ── Task 7.1: migrate_json_to_pg 基础流程 ────────────────────────────────


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestMigrateJsonToPg:
    """Phase 17.1 Task 7.1: migrate_json_to_pg 主流程.

    读 storage.knowledge_dir()/variables.json, transaction 内逐 var INSERT,
    成功返 {"migrated": N, "skipped": M}, json → .migrated backup.
    """

    @pytest.mark.asyncio
    async def test_migrate_json_to_pg_basic(self, tmp_path):
        """1 var legacy json → PG insert success, return dict 正确, json 备份 rename."""
        from explain_engine.persistence.lexicon_migrations import (
            migrate_json_to_pg,
        )
        from explain_engine.persistence.lexicon_pg import get_async_pool
        from explain_engine.persistence.storage_v2 import StorageV2

        storage = StorageV2()
        kd = storage.knowledge_dir()
        var = _make_legacy_var(
            global_id="v_basic001",
            name="基础变量",
            reuse_count=2,
            avg_essentialness=0.8,
            avg_consistency=0.6,
            source_sessions=["s_x1", "s_x2"],
        )
        json_path = _write_legacy_lexicon(kd, [var])

        result = await migrate_json_to_pg(storage)
        assert result == {"migrated": 1, "skipped": 0}

        # DB verify: 1 row exist with 正确字段
        pool = await get_async_pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT global_id, name, abstraction_level, reuse_count, "
                "avg_essentialness, source_sessions, canonical_model_ver "
                "FROM variables WHERE global_id = %s",
                ("v_basic001",),
            )
            row = await cur.fetchone()
        assert row is not None
        gid, name, level, reuse, ess, sessions, model_ver = row
        assert gid == "v_basic001"
        assert name == "基础变量"
        assert level == 1
        assert reuse == 2
        assert abs(ess - 0.8) < 1e-5
        assert sessions == ["s_x1", "s_x2"]
        assert model_ver == "v1-migrated"

        # File backup: json → .migrated, 原 json 已不在
        assert not json_path.exists()
        backup = json_path.with_suffix(".json.migrated")
        assert backup.exists()

    @pytest.mark.asyncio
    async def test_migrate_no_variables_json_returns_reason(self, tmp_path):
        """无 variables.json 时返 {migrated: 0, reason: ...}, 不抛."""
        from explain_engine.persistence.lexicon_migrations import (
            migrate_json_to_pg,
        )
        from explain_engine.persistence.storage_v2 import StorageV2

        storage = StorageV2()
        # 不写 variables.json
        result = await migrate_json_to_pg(storage)
        assert result["migrated"] == 0
        assert "reason" in result
