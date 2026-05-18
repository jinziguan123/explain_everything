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
from typing import TYPE_CHECKING, Any

from explain_engine.llm.client import LLMClient, Message
from explain_engine.schema.nodes import VariableNode

if TYPE_CHECKING:
    from explain_engine.persistence.session import Session
    from explain_engine.persistence.storage_v2 import StorageV2

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
    """Load lexicon from JSON file. Missing file → empty schema.

    Wave 2 fix (review M1): user 手编 partial JSON (e.g. {}) 时,
    setdefault 补齐 missing keys 防后续 KeyError.
    """
    if not path.exists():
        return {
            "version": SCHEMA_VERSION,
            "updated_at": _now_iso(),
            "variables": [],
        }
    text = path.read_text(encoding="utf-8")
    lexicon = json.loads(text)
    # Defensive: 补齐 schema missing keys
    lexicon.setdefault("version", SCHEMA_VERSION)
    lexicon.setdefault("updated_at", _now_iso())
    lexicon.setdefault("variables", [])
    return lexicon


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


async def _build_canonical_mechanism(
    node: VariableNode,
    session: Session,
    llm: LLMClient | None,
) -> str:
    """生 canonical_mechanism 1-line summary.

    有 llm: 调 LLM 用 node + neighbors 信息 prompt 出 1 句话.
    无 llm 或 LLMError: edge-based fallback —
      "通常 cause [outgoing target names]; 由 [incoming source names] cause".
    """
    g = session.state.graph
    nid = node.id

    # 收集 edge neighbors
    outgoing = [
        g.nodes[e.target_node].name
        for e in g.edges.values()
        if e.source_node == nid and e.target_node in g.nodes
    ]
    incoming = [
        g.nodes[e.source_node].name
        for e in g.edges.values()
        if e.target_node == nid and e.source_node in g.nodes
    ]

    def _fallback() -> str:
        parts = []
        if outgoing:
            parts.append(f"通常 cause {', '.join(outgoing[:3])}")
        if incoming:
            parts.append(f"由 {', '.join(incoming[:3])} cause")
        return "; ".join(parts) if parts else f"{node.name} (无 edge 上下文)"

    if llm is None:
        return _fallback()

    prompt = (
        f"Variable: {node.name} (L{node.abstraction_level})\n"
        f"Description: {node.description}\n"
        f"Outgoing (causes): {', '.join(outgoing) if outgoing else '(none)'}\n"
        f"Incoming (caused by): {', '.join(incoming) if incoming else '(none)'}\n\n"
        "请用 1 句中文 (<60 字) 总结它的 canonical mechanism, "
        "格式: '通常 cause X; 由 Y cause'. 仅输 1 行, 无解释."
    )
    try:
        response = await llm.chat(
            messages=[Message(role="user", content=prompt)],
            schema=None,
        )
        text = (response.text or "").strip()
        if not text:
            return _fallback()
        # cap 1 line + 100 chars
        first_line = text.splitlines()[0][:100]
        return first_line
    except Exception:
        # 任何 LLM 故障 (LLMError / 网络 / 解析) 都 fallback, 避 flush 整个失败
        return _fallback()


async def flush_to_lexicon(
    session: Session,
    storage: StorageV2,
    llm: LLMClient | None = None,
) -> int:
    """Promote 高 fitness var 进 lexicon. 返 promoted count.

    Idempotent w.r.t. session_id (同 sid 多次调安全, 不 ++ count).
    """
    path = storage.knowledge_dir() / "variables.json"
    lexicon = _load_lexicon(path)
    promoted = 0

    for _nid, node in session.state.graph.nodes.items():
        if not _should_promote(node):
            continue
        canonical_mech = await _build_canonical_mechanism(node, session, llm)
        _upsert_var(lexicon, node, canonical_mech, session.meta.session_id)
        promoted += 1

    if promoted > 0:
        lexicon["updated_at"] = _now_iso()
        _save_lexicon(path, lexicon)

    return promoted
