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


# ── Task 7.2: ON CONFLICT DO NOTHING idempotent ──────────────────────────


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestMigrateIdempotent:
    """Phase 17.1 Task 7.2: ON CONFLICT DO NOTHING — 重跑只插新, 已存在 skip.

    模拟用户重跑 migrate (e.g. 上次成功后误把 .migrated 改回 variables.json)
    或在多机器上重复跑 — 第 2 次应全 skip, 不报错不重复 insert.
    """

    @pytest.mark.asyncio
    async def test_migrate_idempotent_on_conflict_skips(self, tmp_path):
        """跑 2 次 migrate, 第 2 次返 {migrated:0, skipped:N}, DB count 不变."""
        from explain_engine.persistence.lexicon_migrations import (
            migrate_json_to_pg,
        )
        from explain_engine.persistence.lexicon_pg import get_async_pool
        from explain_engine.persistence.storage_v2 import StorageV2

        storage = StorageV2()
        kd = storage.knowledge_dir()
        var = _make_legacy_var(global_id="v_idem001", name="幂等测试")
        json_path = _write_legacy_lexicon(kd, [var])

        # 第 1 次: 正常 insert
        r1 = await migrate_json_to_pg(storage)
        assert r1 == {"migrated": 1, "skipped": 0}
        assert not json_path.exists()  # 已 rename .migrated

        # 模拟用户重跑: 把 .migrated 改回 variables.json
        backup = json_path.with_suffix(".json.migrated")
        backup.rename(json_path)

        # 第 2 次: ON CONFLICT → 全 skip
        r2 = await migrate_json_to_pg(storage)
        assert r2 == {"migrated": 0, "skipped": 1}

        # DB count 仍 1
        pool = await get_async_pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM variables WHERE global_id = %s",
                ("v_idem001",),
            )
            count = (await cur.fetchone())[0]
        assert count == 1

        # json 没 rename (因 inserted == 0, 保留让用户 inspect)
        assert json_path.exists()


# ── Task 7.3: legacy signature 算法 (no edges) ───────────────────────────


class TestComputeSignatureForLegacy:
    """Phase 17.1 Task 7.3: _compute_signature_for_legacy 算法验证.

    legacy var 没 graph 上下文 → edges 段固定空, 仅 name+desc+level+epi 进 hash.
    跟 Wave 5 compute_canonical_signature 同 sha256[:16] 格式, deterministic.
    """

    def test_same_var_yields_same_hash(self):
        from explain_engine.persistence.lexicon_migrations import (
            _compute_signature_for_legacy,
        )

        var = _make_legacy_var(name="A 概念")
        s1 = _compute_signature_for_legacy(var)
        s2 = _compute_signature_for_legacy(var)
        assert s1 == s2

    def test_diff_name_diff_hash(self):
        from explain_engine.persistence.lexicon_migrations import (
            _compute_signature_for_legacy,
        )

        v1 = _make_legacy_var(name="A 概念")
        v2 = _make_legacy_var(name="B 概念")
        assert _compute_signature_for_legacy(v1) != _compute_signature_for_legacy(v2)

    def test_diff_description_diff_hash(self):
        from explain_engine.persistence.lexicon_migrations import (
            _compute_signature_for_legacy,
        )

        v1 = _make_legacy_var(description="描述 X")
        v2 = _make_legacy_var(description="描述 Y")
        assert _compute_signature_for_legacy(v1) != _compute_signature_for_legacy(v2)

    def test_diff_level_diff_hash(self):
        from explain_engine.persistence.lexicon_migrations import (
            _compute_signature_for_legacy,
        )

        v1 = _make_legacy_var(abstraction_level=1)
        v2 = _make_legacy_var(abstraction_level=2)
        assert _compute_signature_for_legacy(v1) != _compute_signature_for_legacy(v2)

    def test_diff_epistemic_diff_hash(self):
        from explain_engine.persistence.lexicon_migrations import (
            _compute_signature_for_legacy,
        )

        v1 = _make_legacy_var(epistemic="insight")
        v2 = _make_legacy_var(epistemic="speculation")
        assert _compute_signature_for_legacy(v1) != _compute_signature_for_legacy(v2)

    def test_signature_is_16_char_hex(self):
        from explain_engine.persistence.lexicon_migrations import (
            _compute_signature_for_legacy,
        )

        sig = _compute_signature_for_legacy(_make_legacy_var())
        assert len(sig) == 16
        # 16 char lowercase hex
        assert all(c in "0123456789abcdef" for c in sig)


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestMigratedModelVersion:
    """Phase 17.1 Task 7.3: migration var canonical_model_ver = 'v1-migrated'.

    跟 Wave 5 LLM-built canonical (v1) 区分 — 让后续 cache invalidation
    (bump v2) 仍能影响 migrated entries (二者都 != 'v2'), 但 audit 时能
    分辨这条 entry 是 LLM 生还是 migration 灌入.
    """

    @pytest.mark.asyncio
    async def test_migrated_var_has_v1_migrated_model_ver(self, tmp_path):
        from explain_engine.persistence.lexicon_migrations import (
            migrate_json_to_pg,
        )
        from explain_engine.persistence.lexicon_pg import get_async_pool
        from explain_engine.persistence.storage_v2 import StorageV2

        storage = StorageV2()
        kd = storage.knowledge_dir()
        _write_legacy_lexicon(
            kd, [_make_legacy_var(global_id="v_modelver01")],
        )
        await migrate_json_to_pg(storage)

        pool = await get_async_pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT canonical_model_ver FROM variables WHERE global_id = %s",
                ("v_modelver01",),
            )
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "v1-migrated"


# ── Task 7.4: --dry-run mode ──────────────────────────────────────────────


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestMigrateDryRun:
    """Phase 17.1 Task 7.4: dry_run=True 仅 preview, 不写 DB, 不 rename json."""

    @pytest.mark.asyncio
    async def test_migrate_dry_run_writes_nothing(self, tmp_path):
        from explain_engine.persistence.lexicon_migrations import (
            migrate_json_to_pg,
        )
        from explain_engine.persistence.lexicon_pg import get_async_pool
        from explain_engine.persistence.storage_v2 import StorageV2

        storage = StorageV2()
        kd = storage.knowledge_dir()
        json_path = _write_legacy_lexicon(
            kd,
            [
                _make_legacy_var(global_id="v_dry001"),
                _make_legacy_var(global_id="v_dry002", name="二号"),
            ],
        )

        r = await migrate_json_to_pg(storage, dry_run=True)
        assert r == {"would_migrate": 2, "dry_run": True}

        # DB count = 0 (没写)
        pool = await get_async_pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM variables WHERE global_id LIKE %s",
                ("v_dry%",),
            )
            count = (await cur.fetchone())[0]
        assert count == 0

        # variables.json 还在 (没 rename)
        assert json_path.exists()
        assert not json_path.with_suffix(".json.migrated").exists()


# ── Task 7.5: 成功 rename .migrated, 失败 / 零 inserted 保留 json ──────────


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestRenameLogic:
    """Phase 17.1 Task 7.5: rename trigger 是 `if inserted > 0`.

    3 case:
      1. 成功 insert N > 0 → json → .migrated (Task 7.1 已验, 这里 regression)
      2. 空 variables.json (vars_list = []) → migrated:0, json 保留
      3. 重跑全 skip → migrated:0, json 保留 (Task 7.2 已验, 这里 explicit)

    设计意图: 0 inserted 表示 "啥都没新增", 保 json 给用户 inspect / retry 机会
    (说不定要 fix 字段后再灌).
    """

    @pytest.mark.asyncio
    async def test_migrate_renames_on_success(self, tmp_path):
        """sanity: insert 成功 → rename trigger."""
        from explain_engine.persistence.lexicon_migrations import (
            migrate_json_to_pg,
        )
        from explain_engine.persistence.storage_v2 import StorageV2

        storage = StorageV2()
        kd = storage.knowledge_dir()
        json_path = _write_legacy_lexicon(
            kd, [_make_legacy_var(global_id="v_rn001")],
        )
        await migrate_json_to_pg(storage)
        assert not json_path.exists()
        assert json_path.with_suffix(".json.migrated").exists()

    @pytest.mark.asyncio
    async def test_migrate_keeps_json_when_empty_vars_list(self, tmp_path):
        """空 vars_list → migrated:0, json 保留 (inserted==0 不触发 rename)."""
        from explain_engine.persistence.lexicon_migrations import (
            migrate_json_to_pg,
        )
        from explain_engine.persistence.storage_v2 import StorageV2

        storage = StorageV2()
        kd = storage.knowledge_dir()
        json_path = _write_legacy_lexicon(kd, [])  # empty
        r = await migrate_json_to_pg(storage)
        assert r == {"migrated": 0, "skipped": 0}
        # json 仍在原位 (没新 insert, 没必要 backup)
        assert json_path.exists()
        assert not json_path.with_suffix(".json.migrated").exists()

    @pytest.mark.asyncio
    async def test_migrate_keeps_json_when_all_skip(self, tmp_path):
        """重跑 (vars 已存 DB) → migrated:0/skipped:N, json 保留."""
        from explain_engine.persistence.lexicon_migrations import (
            migrate_json_to_pg,
        )
        from explain_engine.persistence.storage_v2 import StorageV2

        storage = StorageV2()
        kd = storage.knowledge_dir()
        var = _make_legacy_var(global_id="v_skip001")

        # 第 1 次 insert + 自动 rename
        _write_legacy_lexicon(kd, [var])
        await migrate_json_to_pg(storage)

        # 第 2 次 (重写 variables.json 模拟用户重跑) — DB 已存, 全 skip
        json_path = _write_legacy_lexicon(kd, [var])
        r2 = await migrate_json_to_pg(storage)
        assert r2 == {"migrated": 0, "skipped": 1}

        # all skip → json 保留, 没 rename
        assert json_path.exists()
        # 注意: 第 1 次 backup 仍在 — 不应被覆盖 (rename 不触发,
        # backup 文件路径 = json_path.with_suffix('.json.migrated') 仍是
        # 第 1 次 rename 的产物)
        assert json_path.with_suffix(".json.migrated").exists()
