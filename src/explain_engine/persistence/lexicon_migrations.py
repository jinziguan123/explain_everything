"""Phase 17.1: variables.json → PostgreSQL lexicon 一次性 migration.

cli 入口: `explain migrate-lexicon-pg [--dry-run]`

Idempotent: ON CONFLICT (global_id) DO NOTHING — 重跑只插新 var, 已存在 skip.

成功 (insert > 0): variables.json → variables.json.migrated (backup, 不删).
失败 (transaction rollback): json 仍在原位, 可 retry.
0 inserted (空 json / 全 skip): json 保留, 让用户能再 retry / inspect.

legacy var 没 graph context (无 edges), signature 用 name+desc+level+epi hash.
canonical_model_ver 写 'v1-migrated' 跟 Wave 5 'v1' 区分, 留 cache invalidation 路径
(改 LLM canonical prompt 时手 bump 'v2', 'v1-migrated' 仍跟 'v1' 一起 invalidate).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from explain_engine.persistence.lexicon_pg import get_async_pool


def _compute_signature_for_legacy(var: dict[str, Any]) -> str:
    """legacy var 没 graph 上下文, signature 仅用 name+desc+level+epi hash.

    跟 Wave 5 compute_canonical_signature 同 sha256[:16] 风格,
    但 edges 段固定空 (legacy 无 graph context).

    deterministic — 同 var dict 永远算同 hash.
    """
    payload = "\n".join([
        f"name={var.get('name', '?')}",
        f"desc={var.get('description', '?')}",
        f"level={var.get('abstraction_level', 0)}",
        f"epi={var.get('epistemic', '?')}",
        "edges=",  # legacy 无 graph 上下文, 固定空段
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def migrate_json_to_pg(
    storage: Any,
    dry_run: bool = False,
) -> dict[str, Any]:
    """一次性 migrate variables.json → PG, idempotent.

    Args:
      storage: StorageV2 实例 — 用 storage.knowledge_dir() 找 variables.json.
      dry_run: True 时仅 count, 不写 DB, 不 rename json.

    Returns:
      - {"migrated": N, "skipped": M} (正常路径)
      - {"would_migrate": N, "dry_run": True} (dry-run)
      - {"migrated": 0, "reason": str} (无 variables.json)

    成功 (inserted > 0): variables.json → variables.json.migrated.
    全 skip (inserted == 0): json 保留, 可手动 inspect / retry.

    Behavior:
      - 全 var 在单个 transaction 内 insert — 中途 fail 全 rollback (json 保留).
      - ON CONFLICT (global_id) DO NOTHING: 已存在 var skip (重跑安全).
      - cur.rowcount > 0 区分 insert vs skip (psycopg ON CONFLICT 返 rowcount=0 时 skip).
    """
    json_path = storage.knowledge_dir() / "variables.json"
    if not json_path.exists():
        return {"migrated": 0, "reason": "no variables.json"}

    lexicon = json.loads(json_path.read_text(encoding="utf-8"))
    vars_list = lexicon.get("variables", [])

    if dry_run:
        return {"would_migrate": len(vars_list), "dry_run": True}

    pool = await get_async_pool()
    inserted = 0
    skipped = 0

    async with pool.connection() as conn:
        async with conn.transaction():
            for var in vars_list:
                cur = await conn.execute(
                    """INSERT INTO variables (
                        global_id, name, description, abstraction_level, epistemic,
                        canonical_mechanism, canonical_signature, canonical_model_ver,
                        reuse_count, avg_essentialness, avg_consistency,
                        first_seen_at, last_seen_at, source_sessions, embedding
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, 'v1-migrated',
                        %s, %s, %s, %s, %s, %s, %s
                    ) ON CONFLICT (global_id) DO NOTHING""",
                    (
                        var["global_id"],
                        var["name"],
                        var["description"],
                        var["abstraction_level"],
                        var["epistemic"],
                        var["canonical_mechanism"],
                        _compute_signature_for_legacy(var),
                        var["fitness"]["reuse_count"],
                        var["fitness"]["avg_essentialness"],
                        var["fitness"]["avg_consistency"],
                        var["fitness"]["first_seen_at"],
                        var["fitness"]["last_seen_at"],
                        var["source_sessions"],
                        var.get("embedding"),
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1

    if inserted > 0:
        backup_path = json_path.with_suffix(".json.migrated")
        json_path.rename(backup_path)

    return {"migrated": inserted, "skipped": skipped}
