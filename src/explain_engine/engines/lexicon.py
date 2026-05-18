"""Phase 10 Variable Lexicon — cross-session 高 fitness L1/L2 abstractions.

knowledge/variables.json schema:
{
  "version": 1,
  "updated_at": "<ISO8601>",
  "variables": [
    {
      "global_id": "v_<8hex>",
      "name": str,
      "description": str,
      "abstraction_level": int (1 or 2),
      "epistemic": str,
      "fitness": {
        "reuse_count": int,
        "avg_essentialness": float,
        "avg_consistency": float,
        "first_seen_at": str (ISO8601),
        "last_seen_at": str (ISO8601),
      },
      "canonical_mechanism": str (1-line summary),
      "source_sessions": list[str]
    }
  ]
}

设计参考 docs/plans/2026-05-18-phase10-persistent-world-model-design.md.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from explain_engine.schema.nodes import VariableNode

SCHEMA_VERSION = 1


def _now_iso() -> str:
    """ISO8601 UTC, 'Z' suffix."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compute_global_id(name: str, canonical_mechanism: str) -> str:
    """global_id = 'v_' + sha256(name + '::' + canonical_mechanism)[:8].

    name 或 canonical_mechanism 任一变 → 新 global_id (conservative split —
    宁可重复存, 不要 wrong merge).
    """
    s = f"{name}::{canonical_mechanism}".encode()
    return "v_" + hashlib.sha256(s).hexdigest()[:8]


def _load_lexicon(path: Path) -> dict[str, Any]:
    """Load lexicon from JSON file. Missing file → empty schema."""
    if not path.exists():
        return {
            "version": SCHEMA_VERSION,
            "updated_at": _now_iso(),
            "variables": [],
        }
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def _save_lexicon(path: Path, lexicon: dict[str, Any]) -> None:
    """Atomic write: .tmp → rename. 同 StorageV2._write_atomic pattern."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(lexicon, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _should_promote(node: VariableNode) -> bool:
    """Phase 10 第一版 fitness filter.

    - skip L0 (observations 不进 lexicon)
    - skip non-active (stale/decayed)
    - skip activation < 0.5 (conservative threshold)
    """
    return (
        node.abstraction_level >= 1
        and node.lifecycle_state == "active"
        and node.activation >= 0.5
    )


def _upsert_var(
    lexicon: dict[str, Any],
    node: VariableNode,
    canonical_mechanism: str,
    sid: str,
) -> None:
    """Insert or update var entry. Idempotent w.r.t. (global_id, sid).

    新 var: append with reuse_count=1, source_sessions=[sid].
    已有 + 新 sid: ++ reuse_count, append sid.
    已有 + 同 sid: 仅 update last_seen_at (不 ++ count).
    """
    global_id = _compute_global_id(node.name, canonical_mechanism)
    entries = lexicon["variables"]
    existing = next((v for v in entries if v["global_id"] == global_id), None)

    now = _now_iso()

    if existing is None:
        entries.append({
            "global_id": global_id,
            "name": node.name,
            "description": node.description,
            "abstraction_level": node.abstraction_level,
            "epistemic": node.epistemic,
            "fitness": {
                "reuse_count": 1,
                "avg_essentialness": node.activation,  # Phase 10 第一版 proxy
                "avg_consistency": node.stability,  # Phase 10 第一版 proxy
                "first_seen_at": now,
                "last_seen_at": now,
            },
            "canonical_mechanism": canonical_mechanism,
            "source_sessions": [sid],
        })
        return

    if sid in existing["source_sessions"]:
        # 同 session 重复 flush — 仅 update last_seen
        existing["fitness"]["last_seen_at"] = now
        return

    # 新 sid → ++ reuse_count
    existing["source_sessions"].append(sid)
    fitness = existing["fitness"]
    new_count = fitness["reuse_count"] + 1
    # Running avg: new_avg = (old_avg * old_count + new_value) / new_count
    fitness["avg_essentialness"] = (
        fitness["avg_essentialness"] * fitness["reuse_count"] + node.activation
    ) / new_count
    fitness["avg_consistency"] = (
        fitness["avg_consistency"] * fitness["reuse_count"] + node.stability
    ) / new_count
    fitness["reuse_count"] = new_count
    fitness["last_seen_at"] = now
