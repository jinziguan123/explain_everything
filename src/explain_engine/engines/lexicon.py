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
      "source_sessions": list[str],
      "embedding": list[float] | null   # Phase 13: BGE-M3 1024-dim dense vector (None for legacy entries)
    }
  ]
}

设计参考 docs/plans/2026-05-18-phase10-persistent-world-model-design.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import LLMError
from explain_engine.schema.nodes import VariableNode

if TYPE_CHECKING:
    from explain_engine.persistence.session import Session
    from explain_engine.persistence.storage_v2 import StorageV2

SCHEMA_VERSION = 1

EMBEDDING_DIM = 1024
"""Phase 13: BGE-M3 dense embedding dimension. Used by _upsert_var
validation and _build_embeddings_matrix shape."""


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
    embedding: list[float] | None = None,
) -> None:
    """Insert or update var entry. Idempotent w.r.t. (global_id, sid).

    新 var: append with reuse_count=1, source_sessions=[sid], embedding=<vec or None>.
    已有 + 新 sid: ++ reuse_count, append sid. Embedding preserved (not overwritten).
    已有 + 同 sid: 仅 update last_seen_at (不 ++ count). Embedding preserved.

    Args:
        embedding: BGE-M3 1024-dim dense vector (Phase 13). None for legacy entries
            or when embedding generation skipped (EXPLAIN_EMBEDDING_DISABLED=1).
            Validated: must be exactly 1024 elements if provided.
    """
    if embedding is not None and len(embedding) != EMBEDDING_DIM:
        raise ValueError(
            f"embedding must be {EMBEDDING_DIM}-dim (BGE-M3), got {len(embedding)}"
        )
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
            "embedding": embedding,  # Phase 13: None for legacy / disabled-env
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
    except LLMError:
        # Wave 2 review I-1: 窄化到 LLMError. 非 LLM 异常 (e.g. graph 数据
        # 损坏导致 prompt 拼接 AttributeError) 应 propagate, 不该 silent
        # 退化让 user 看不到真错误. LLMClient 内部网络/解析故障已 wrap LLMError.
        return _fallback()


async def flush_to_lexicon(
    session: Session,
    storage: StorageV2,
    llm: LLMClient | None = None,
    llm_canonical_top_k: int = 3,
) -> int:
    """Promote 高 fitness var 进 lexicon. 返 promoted count.

    Idempotent w.r.t. session_id (同 sid 多次调安全, 不 ++ count).

    Mitigation #2 (2026-05-19): `llm_canonical_top_k` cap LLM call 数量.
    promoted vars 按 node.activation desc sort, **top-K 真 LLM** 生
    canonical_mechanism (high-quality 1-line summary), 其余传 llm=None
    走 edge-based fallback (instant, 略低 quality).

    Use case: typical session 5+ promoted var 时显著省 (N-K) LLM call —
    e.g. 5 var, K=3 → 省 2 call (~10s). 大 session (Phase 12 motif
    detection) 时 leverage 更大.

    `llm_canonical_top_k=0` → 全 fallback (lazy mode).
    `llm_canonical_top_k >= N` → 全 LLM (老 Wave 2 行为).
    """
    path = storage.knowledge_dir() / "variables.json"
    lexicon = _load_lexicon(path)
    # Phase 13: lazy embedding backfill. Wired here (not _load_lexicon) so
    # read-only paths (/lexicon display, _select_top_k_vars for prompt prior)
    # don't trigger BGE-M3 model load. flush_to_lexicon is the natural write
    # path — migration runs at most once per session here.
    _migrate_lexicon_embeddings(lexicon, path)

    # 收集 promoted candidates + sort by activation desc
    candidates = [
        node
        for node in session.state.graph.nodes.values()
        if _should_promote(node)
    ]
    candidates.sort(key=lambda n: -n.activation)

    promoted = 0
    for i, node in enumerate(candidates):
        # Top-K 用 真 llm; 其余传 None 走 edge fallback
        effective_llm = llm if i < llm_canonical_top_k else None
        canonical_mech = await _build_canonical_mechanism(
            node, session, effective_llm,
        )
        _upsert_var(lexicon, node, canonical_mech, session.meta.session_id)
        promoted += 1

    if promoted > 0:
        lexicon["updated_at"] = _now_iso()
        _save_lexicon(path, lexicon)

    return promoted


# ── Wave 3: prior selection + prompt rendering ───────────────────────────────


def _select_top_k_vars(
    lexicon: dict[str, Any],
    k: int = 20,
) -> list[dict[str, Any]]:
    """选 Top-K composite-score vars 作 LLM prior.

    composite = reuse_count × (avg_essentialness + 0.1)
    +0.1 防 essentialness=0 时 score 完全清零 (高 reuse 仍可入选).
    k<=0 → empty. k>total → 全返.
    """
    if k <= 0:
        return []
    variables = lexicon.get("variables", [])
    if not variables:
        return []

    def _score(v: dict[str, Any]) -> float:
        fitness = v.get("fitness", {})
        reuse = fitness.get("reuse_count", 0)
        ess = fitness.get("avg_essentialness", 0.0)
        return reuse * (ess + 0.1)

    return sorted(variables, key=_score, reverse=True)[:k]


def _build_embeddings_matrix(
    lexicon: dict[str, Any],
) -> tuple[np.ndarray, dict[str, int]]:
    """Stack embedding vectors of all variables that have one.

    Phase 13 Wave 1 Task 4: caller (lexicon flush, /compress dedup) uses
    this matrix for batch cosine vs incoming candidate embeddings.

    Returns:
        matrix: shape (M, EMBEDDING_DIM) float32, M = # vars with embedding
            (skips None values AND missing 'embedding' key from Phase 10/11
            legacy entries). Empty lexicon → (0, EMBEDDING_DIM) empty array.
        global_id_to_matrix_idx: maps `global_id` → row index in matrix.
            Only includes vars present in matrix (legacy / None excluded).
    """
    rows: list[list[float]] = []
    idx_map: dict[str, int] = {}
    for var in lexicon.get("variables", []):
        emb = var.get("embedding")
        if emb is None:
            continue
        idx_map[var["global_id"]] = len(rows)
        rows.append(emb)
    if rows:
        matrix = np.asarray(rows, dtype=np.float32)
    else:
        matrix = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    return matrix, idx_map


def _migrate_lexicon_embeddings(
    lexicon: dict[str, Any],
    path: Path | None,
) -> int:
    """Phase 13 Wave 1 Task 5: batch embed any var lacking embedding field.

    Lazy migration: caller invokes (typically `flush_to_lexicon` at start).
    Mutates lexicon dict in-place, writes back to `path` if provided
    AND at least 1 var migrated.

    Env `EXPLAIN_EMBEDDING_DISABLED=1` short-circuits to 0 (no embedder load).
    Embedder load / encode failure → log warning, leave var dict unchanged
    (caller continues with whatever vars are migrated; partial state OK).

    Args:
        lexicon: dict with shape per top-of-file schema docstring.
        path: where to atomic write-back; None → in-memory only (no I/O).

    Returns:
        Number of vars that gained an embedding field.
    """
    if os.environ.get("EXPLAIN_EMBEDDING_DISABLED") == "1":
        return 0

    needs_migration: list[dict[str, Any]] = [
        var for var in lexicon.get("variables", [])
        if var.get("embedding") is None
    ]
    if not needs_migration:
        return 0

    try:
        from rich.console import Console

        from explain_engine.embedding.bge_m3 import get_embedder
        console = Console()
        with console.status(
            f"首次升级 lexicon embedding: {len(needs_migration)} entries...",
            spinner="dots",
        ):
            embedder = get_embedder()
            texts = [var["canonical_mechanism"] for var in needs_migration]
            vecs = embedder.embed(texts)
        for var, vec in zip(needs_migration, vecs, strict=True):
            var["embedding"] = vec.tolist()
    except Exception as exc:
        logging.warning(
            f"Lexicon embedding migration failed: {type(exc).__name__}: {exc}. "
            "Falling back to string-match path for entries lacking embedding."
        )
        return 0

    migrated = len(needs_migration)
    if migrated > 0 and path is not None:
        lexicon["updated_at"] = _now_iso()
        _save_lexicon(path, lexicon)
    return migrated


def _render_lexicon_for_prompt(vars_list: list[dict[str, Any]]) -> str:
    """渲染 lexicon prior section 进 LLM prompt.

    每行: `- {global_id} 「{name}」(L{level}, reused {N}x): {desc[:80]} — {mech[:60]}`
    末尾加 disclaimer — let LLM 知道 lexicon 是 hint 不是 rule (避免被框死).
    Token budget: 单 var ~80 token, Top-K=20 默认 ≈ 1.7k token.
    """
    if not vars_list:
        return ""

    lines = ["[已知 abstractions — 仅供参考, 不强制使用]"]
    for v in vars_list:
        gid = v.get("global_id", "?")
        name = v.get("name", "?")
        level = v.get("abstraction_level", 0)
        fitness = v.get("fitness", {})
        reuse = fitness.get("reuse_count", 0)
        desc = (v.get("description") or "")[:80]
        mech = (v.get("canonical_mechanism") or "")[:60]
        lines.append(
            f"- {gid} 「{name}」(L{level}, reused {reuse}x): {desc} — {mech}"
        )
    return "\n".join(lines)
