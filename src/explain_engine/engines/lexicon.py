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

from explain_engine.engines.lexicon_merge import find_duplicate, write_merge_audit
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
    log_dir: Path | None = None,
) -> None:
    """Insert or update var entry. Idempotent w.r.t. (global_id, sid).

    Phase 13 Wave 2 Task 3: cosine-first lookup. 当 embedding 给定时,
    先 build embeddings matrix + find_duplicate (cos > 0.85) → 命中
    则 merge into 该 entry (覆盖 hash match), 写 audit log. 不命中 /
    embedding=None → 回退到 Phase 10 hash lookup (_compute_global_id).

    新 var: append with reuse_count=1, source_sessions=[sid], embedding=<vec or None>.
    已有 + 新 sid: ++ reuse_count, append sid. Embedding preserved (not overwritten).
    已有 + 同 sid: 仅 update last_seen_at (不 ++ count). Embedding preserved.

    Phase 13: cosine-merge 时 candidate 的 name/description/canonical_mechanism
    被丢弃 (existing entry 的 metadata wins). 仅 source_sessions / fitness 累加.
    audit log merged_from 保留 candidate canonical 前 80 char + merged_into_canonical
    保留 target canonical 前 80 char, 双向 forensic.

    Args:
        embedding: BGE-M3 1024-dim dense vector (Phase 13). None for legacy entries
            or when embedding generation skipped (EXPLAIN_EMBEDDING_DISABLED=1).
            Validated: must be exactly 1024 elements if provided.
        log_dir: Phase 13 audit log directory. If provided AND merge happens via
            embedding (not hash), append JSONL record via write_merge_audit.
    """
    if embedding is not None and len(embedding) != EMBEDDING_DIM:
        raise ValueError(
            f"embedding must be {EMBEDDING_DIM}-dim (BGE-M3), got {len(embedding)}"
        )

    entries = lexicon["variables"]
    matched_var: dict[str, Any] | None = None
    sim_value: float | None = None  # set only if matched via cosine

    # Phase 13 Wave 2: Try embedding-based cosine match FIRST (if embedding given)
    if embedding is not None:
        matrix, id_to_idx = _build_embeddings_matrix(lexicon)
        if matrix.shape[0] > 0:
            emb_arr = np.asarray(embedding, dtype=np.float32)
            idx = find_duplicate(emb_arr, matrix)
            if idx is not None:
                matched_global_id = next(
                    gid for gid, i in id_to_idx.items() if i == idx
                )
                matched_var = next(
                    v for v in entries if v["global_id"] == matched_global_id
                )
                # Compute exact sim for audit log
                row = matrix[idx]
                row_norm = float(np.linalg.norm(row))
                emb_norm = float(np.linalg.norm(emb_arr))
                if row_norm * emb_norm > 1e-9:
                    sim_value = float(
                        np.dot(row, emb_arr) / (row_norm * emb_norm)
                    )
                else:
                    sim_value = 0.0

    # Fall back to Phase 10 hash-based lookup
    if matched_var is None:
        global_id = _compute_global_id(node.name, canonical_mechanism)
        matched_var = next(
            (v for v in entries if v["global_id"] == global_id), None
        )

    now = _now_iso()

    if matched_var is None:
        # Brand new entry (no match via cosine or hash)
        entries.append({
            "global_id": _compute_global_id(node.name, canonical_mechanism),
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

    if sid in matched_var["source_sessions"]:
        # 同 session 重复 flush — 仅 update last_seen
        matched_var["fitness"]["last_seen_at"] = now
        return

    # 新 sid → ++ reuse_count
    matched_var["source_sessions"].append(sid)
    fitness = matched_var["fitness"]
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

    # Phase 13 Wave 2: audit log when matched VIA EMBEDDING (not hash, not creation)
    if sim_value is not None and log_dir is not None:
        write_merge_audit(
            log_dir=log_dir,
            merged_into=matched_var["global_id"],
            merged_into_canonical=matched_var["canonical_mechanism"][:80],
            merged_from=canonical_mechanism[:80],
            sim=sim_value,
            evidence_ids=[sid],
        )


async def _build_canonical_mechanism(
    node: VariableNode,
    session: Session,
    llm: LLMClient | None,
    light_llm: LLMClient | None = None,
) -> str:
    """生 canonical_mechanism 1-line summary.

    有 llm: 调 LLM 用 node + neighbors 信息 prompt 出 1 句话.
    无 llm 或 LLMError: edge-based fallback —
      "通常 cause [outgoing target names]; 由 [incoming source names] cause".

    Phase 17.2 Task 7: optional light_llm 参数 — 不 None 时通过
    with_light_fallback 先走 cheap model, 失败自动 fallback 主 llm.
    None (默) → 行为跟现一致 (主 llm 直接调), 零回归.
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

    async def _do_call(active_llm: LLMClient):
        return await active_llm.chat(
            messages=[Message(role="user", content=prompt)],
            schema=None,
        )

    try:
        if light_llm is not None:
            from explain_engine.llm.light_fallback import with_light_fallback
            response = await with_light_fallback(light_llm, llm, _do_call)
        else:
            response = await _do_call(llm)
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
    light_llm: LLMClient | None = None,
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

    Phase 17.2 Task 7 (final review fix): optional light_llm — 不 None 时透传给
    _build_canonical_mechanism, 走 with_light_fallback (cheap model 优先, 失败
    回退主 llm). None (默) → 行为跟现一致, 零回归.
    """
    path = storage.knowledge_dir() / "variables.json"
    lexicon = _load_lexicon(path)
    log_dir = storage.knowledge_dir().parent / "logs"
    # Phase 13: lazy embedding backfill. Wired here (not _load_lexicon) so
    # read-only paths (/lexicon display, _select_top_k_vars for prompt prior)
    # don't trigger BGE-M3 model load. flush_to_lexicon is the natural write
    # path — migration runs at most once per session here.
    _migrate_lexicon_embeddings(lexicon, path)
    # Phase 13 hotfix #6 (2026-05-20): retroactive cross-pair cosine merge.
    # 兼修 batch embed 早期静默失败导致的 dirty data (entries 应合但分散) +
    # 防同 scenario 再发 (下次 flush 必跑一遍). 计算 NxN cosine matrix, N=26
    # 时毫秒级. 真大 lexicon (1k+) 也秒级 — 可接受.
    _retroactive_dedup(lexicon, path, log_dir)

    # 收集 promoted candidates + sort by activation desc
    candidates = [
        node
        for node in session.state.graph.nodes.values()
        if _should_promote(node)
    ]
    candidates.sort(key=lambda n: -n.activation)

    # Phase 13 Wave 2 Task 3: 先 build canonicals 全部, 再 batch embed,
    # 然后 upsert 时 cosine-first merge.
    canonicals: list[str] = []
    for i, node in enumerate(candidates):
        # Top-K 用 真 llm; 其余传 None 走 edge fallback
        effective_llm = llm if i < llm_canonical_top_k else None
        # Phase 17.2 Task 7 (final review fix): light_llm 仅在用真 llm 的 top-K
        # 名额上下文里生效 (effective_llm 非 None). 其余 idx 走 edge fallback,
        # light_llm 一并跟 effective_llm=None, 不引出额外 LLM call.
        effective_light = light_llm if effective_llm is not None else None
        canonical_mech = await _build_canonical_mechanism(
            node, session, effective_llm, light_llm=effective_light,
        )
        canonicals.append(canonical_mech)

    # Phase 13 Wave 2: batch embed canonicals (best-effort, optional).
    # Embedder load / encode failure → log warning, fall back to None for all.
    embeddings: list[list[float] | None] = [None] * len(canonicals)
    if (
        canonicals
        and os.environ.get("EXPLAIN_EMBEDDING_DISABLED") != "1"
    ):
        try:
            from explain_engine.embedding.bge_m3 import get_embedder
            embedder = get_embedder()
            vecs = embedder.embed(canonicals)
            embeddings = [vec.tolist() for vec in vecs]
        except Exception as exc:
            # Phase 13 hotfix #6 (2026-05-20): warning → error.
            # 旧 silent warning 让用户不知道 batch embed 失败 → entries 全 None
            # 插入, dedup 全没 trigger. 升 error 让用户立刻看到 (console 红字),
            # 配合 retroactive_dedup 修旧 dirty data, 双层保护.
            # 不 raise — 保持 fallback 行为 (entries 仍能写入, 只是无 dedup),
            # 下次 flush 时 _migrate_lexicon_embeddings 会 backfill + dedup 兜底.
            logging.error(
                f"flush_to_lexicon batch embed failed: "
                f"{type(exc).__name__}: {exc}. "
                "Falling back to embedding=None for all candidates. "
                "(retroactive_dedup will merge on next flush 兜底, "
                "but please check BGE-M3 setup: model download / MPS / disk.)"
            )

    # log_dir 在前面 retroactive_dedup 处已计算, 这里复用.

    promoted = 0
    for node, canonical_mech, embedding in zip(
        candidates, canonicals, embeddings, strict=True,
    ):
        _upsert_var(
            lexicon, node, canonical_mech, session.meta.session_id,
            embedding=embedding,
            log_dir=log_dir,
        )
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


def get_lexicon_top_k_for_compress(
    storage: StorageV2,
    k: int = 20,
) -> list[tuple[str, str, int]]:
    """Phase 13 Wave 3: load lexicon + return Top-K tuples for compression prompt.

    Top-K ranked by composite fitness (delegates to _select_top_k_vars).
    Returns empty list if lexicon file missing / k <= 0.

    Each tuple: (global_id, canonical_mechanism, reuse_count) — matches
    the existing_lexicon param of compression.propose_candidates.
    """
    path = storage.knowledge_dir() / "variables.json"
    lexicon = _load_lexicon(path)
    top_k_vars = _select_top_k_vars(lexicon, k=k)
    return [
        (v["global_id"], v["canonical_mechanism"], v["fitness"]["reuse_count"])
        for v in top_k_vars
    ]


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


def _retroactive_dedup(
    lexicon: dict[str, Any],
    path: Path | None,
    log_dir: Path | None = None,
    threshold: float = 0.85,
) -> int:
    """Phase 13 hotfix #6 (2026-05-20): cross-pair cosine merge sweep.

    根因: 早期 flush 时 batch embed 静默 except (BGE-M3 首次下载未完 / MPS 初始
    crash) → entries 全 embedding=None 插入, _upsert_var 走 hash match 全 miss,
    全 brand new. 后续 _migrate_lexicon_embeddings backfill embedding 但
    entries 已分散 — 没自动合. 这条 sweep 在每次 flush 时跑, 把 sim > 0.85 的
    entry pair 合并 (chronologically: 早 first_seen 的为 winner). 兼修旧 dirty
    data + 防同 scenario 再发.

    Algorithm (greedy chronological):
        1. 收集有 embedding 的 entry (None / 缺 key 跳过).
        2. 按 first_seen_at asc 排序 (早的为 winner candidate).
        3. 算 NxN cosine matrix. 遍历 j ∈ [1, N): 找最早未被吸收的 i (i<j) 满足
           sim(i,j) > threshold → j 被 i 吸收. 找到 break.
        4. 应用合并: 把 loser 的 source_sessions / fitness 累加进 winner.
           winner 的 name/description/canonical 不动 (chronological 保留).
        5. 写 audit log per merge (沿用 write_merge_audit, 跟 _upsert_var 同形).
        6. 从 lexicon["variables"] 移除被吸收的 entry; merged > 0 时存盘.

    Args:
        lexicon: dict, mutated in-place.
        path: 写回 location. None → 不 I/O (test-only / dry-run).
        log_dir: audit log 目录. None → 不写 audit.
        threshold: cosine cutoff. default 0.85 (= LEXICON_MERGE_THRESHOLD).

    Returns:
        被合并的 entry 数 (= 从 lexicon 移除的数).
    """
    entries = lexicon.get("variables", [])
    if len(entries) < 2:
        return 0

    # 收集有 embedding 的 (保留原 index 用于后续 remove)
    embedded: list[tuple[int, dict[str, Any]]] = [
        (i, e) for i, e in enumerate(entries) if e.get("embedding")
    ]
    if len(embedded) < 2:
        return 0

    # 按 first_seen 升序 排 (早的 = winner)
    embedded.sort(key=lambda x: x[1]["fitness"]["first_seen_at"])

    # NxN cosine matrix
    matrix = np.asarray([e["embedding"] for _, e in embedded], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    norms_safe = np.maximum(norms, 1e-9)
    normalized = matrix / norms_safe[:, None]
    sims = normalized @ normalized.T

    N = len(embedded)
    absorbed_into: dict[int, int] = {}  # sorted_idx_j → sorted_idx_i (winner)
    for j in range(1, N):
        if j in absorbed_into:
            continue
        for i in range(j):
            if i in absorbed_into:
                continue
            if sims[i, j] > threshold:
                absorbed_into[j] = i
                break

    if not absorbed_into:
        return 0

    # 应用 merge + 收 audit records
    audit_records: list[dict[str, Any]] = []
    for j, i in absorbed_into.items():
        winner = embedded[i][1]
        loser = embedded[j][1]

        # Union source_sessions (preserve order, skip dup)
        for sid in loser["source_sessions"]:
            if sid not in winner["source_sessions"]:
                winner["source_sessions"].append(sid)

        # Running-avg merge fitness
        wf = winner["fitness"]
        lf = loser["fitness"]
        w_count = wf["reuse_count"]
        l_count = lf["reuse_count"]
        new_count = w_count + l_count
        wf["avg_essentialness"] = (
            wf["avg_essentialness"] * w_count
            + lf["avg_essentialness"] * l_count
        ) / new_count
        wf["avg_consistency"] = (
            wf["avg_consistency"] * w_count
            + lf["avg_consistency"] * l_count
        ) / new_count
        wf["reuse_count"] = new_count
        wf["last_seen_at"] = max(wf["last_seen_at"], lf["last_seen_at"])

        audit_records.append({
            "merged_into": winner["global_id"],
            "merged_into_canonical": winner["canonical_mechanism"][:80],
            "merged_from": loser["canonical_mechanism"][:80],
            "sim": float(sims[i, j]),
            "evidence_ids": list(loser["source_sessions"]),
        })

    # Audit log
    if log_dir is not None:
        for rec in audit_records:
            write_merge_audit(
                log_dir=log_dir,
                merged_into=rec["merged_into"],
                merged_into_canonical=rec["merged_into_canonical"],
                merged_from=rec["merged_from"],
                sim=rec["sim"],
                evidence_ids=rec["evidence_ids"],
            )

    # Remove absorbed entries (按 lexicon["variables"] 原 idx)
    absorbed_original_indices = {embedded[j][0] for j in absorbed_into}
    lexicon["variables"] = [
        e for idx, e in enumerate(entries)
        if idx not in absorbed_original_indices
    ]

    merged_count = len(absorbed_into)
    if merged_count > 0 and path is not None:
        lexicon["updated_at"] = _now_iso()
        _save_lexicon(path, lexicon)

    return merged_count


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


# ════════════════════════════════════════════════════════════════════════
# Phase 17.1 Wave 9: backend auto-fallback dispatcher
# ════════════════════════════════════════════════════════════════════════
# 启动时 init_lexicon_backend() 检 PG 可达性:
#   PG 通 → _PG_BACKEND_ACTIVE = True, 公共 API 调 lexicon_pg 远程 PG impl,
#           顺带 sync local JSON 残留 var → PG (keep_json=True, JSON 留作 offline
#           fallback buffer).
#   PG 断 → _PG_BACKEND_ACTIVE = False, 公共 API 回退本机 JSON impl (即上面老代码),
#           chat 仍可用, 写入仅留 local JSON, 不同步 PG. 重连后下次启动 init 自动 sync.
#
# 公共 API (flush_to_lexicon / get_lexicon_top_k_for_compress / _render_lexicon_for_prompt)
# 末尾 def 同名 dispatcher override 前面老 def. Python from-import 拿的是最末 def.
#
# 老 caller 不动. 切换透明 — 仅 chat REPL 启动时多 1 行 `await init_lexicon_backend()`.

_PG_BACKEND_ACTIVE: bool | None = None  # None=未 init / True=PG / False=JSON fallback

# Save 老 JSON impl 引用 (上面 def 即老 JSON 实现)
_flush_to_lexicon_json_impl = flush_to_lexicon
_get_lexicon_top_k_for_compress_json_impl = get_lexicon_top_k_for_compress
_render_lexicon_for_prompt_json_impl = _render_lexicon_for_prompt


async def init_lexicon_backend() -> bool:
    """Phase 17.1 Wave 9: REPL 启动调 1 次. PG 通 → True (用 PG); 断 → False (本机 JSON).

    Idempotent — 重调返 cached _PG_BACKEND_ACTIVE. test reset 时 force re-init
    需手 set _PG_BACKEND_ACTIVE = None.

    PG 通时顺带 startup sync: 把 local JSON 残留 var (offline 期写) 一次性
    push 到 PG (idempotent, ON CONFLICT skip). 不删 JSON (留作 fallback buffer).
    """
    global _PG_BACKEND_ACTIVE
    if _PG_BACKEND_ACTIVE is not None:
        return _PG_BACKEND_ACTIVE
    try:
        from explain_engine.persistence.lexicon_migrations import migrate_json_to_pg
        from explain_engine.persistence.lexicon_pg import verify_connection
        from explain_engine.persistence.storage_v2 import StorageV2

        await verify_connection()
        # Startup sync: local JSON → PG, idempotent, 不 rename JSON
        result = await migrate_json_to_pg(StorageV2(), keep_json=True)
        _PG_BACKEND_ACTIVE = True
        logging.info(
            f"lexicon backend: PG ✓ (startup sync: {result})"
        )
    except Exception as exc:
        _PG_BACKEND_ACTIVE = False
        logging.warning(
            f"lexicon backend: PG 不可达 ({type(exc).__name__}: {exc}), "
            "fallback 本机 JSON. Chat 仍可用; lexicon 修改不会同步 PG, "
            "重连后下次启动 init 自动 sync."
        )
    return _PG_BACKEND_ACTIVE


# ── Public API dispatcher (override 前面 def) ──


async def flush_to_lexicon(
    session: Session, storage: StorageV2,
    llm: LLMClient | None = None,
    llm_canonical_top_k: int = 3,
    light_llm: LLMClient | None = None,
) -> int:
    """Wave 9 dispatcher: PG 可达走 lexicon_pg, 断走 JSON fallback.

    Phase 17.2 Task 7 (final review fix): light_llm 透传给 PG / JSON impl.
    """
    if _PG_BACKEND_ACTIVE:
        from explain_engine.persistence.lexicon_pg import (
            flush_to_lexicon as pg_impl,
        )
        return await pg_impl(
            session, storage, llm, llm_canonical_top_k, light_llm=light_llm,
        )
    return await _flush_to_lexicon_json_impl(
        session, storage, llm, llm_canonical_top_k, light_llm=light_llm,
    )


def get_lexicon_top_k_for_compress(
    storage: StorageV2,
    k: int = 20,
) -> list[dict[str, Any]] | list[tuple[str, str, int]]:
    """Wave 9 dispatcher: sync version.

    Phase 19 真终端 Bug B 调研: dispatcher 实际返 shape 跟 backend 同步 ——
    PG impl 返 `list[dict]` (psycopg dict_row), JSON impl 返
    `list[tuple[str, str, int]]`. 老 type hint 只写 `list[dict[str, Any]]`,
    误导 caller (e.g. compression._render_lexicon_topk_section 老写 for-unpack
    3-tuple, JSON path 工作, PG path 抛 ValueError). 现 type 标双 shape.

    consumer 必须用 `isinstance(entry, dict)` 分支处理 (见
    compression._render_lexicon_topk_section / chat/tools._compress_call).
    """
    if _PG_BACKEND_ACTIVE:
        from explain_engine.persistence.lexicon_pg import (
            get_lexicon_top_k_for_compress as pg_impl,
        )
        return pg_impl(storage, k)
    return _get_lexicon_top_k_for_compress_json_impl(storage, k)


def _render_lexicon_for_prompt(vars_list: list[dict[str, Any]]) -> str:
    """Wave 9 dispatcher: PG flat dict / JSON nested fitness 两兼容 (lexicon_pg
    版本已含兼容逻辑, JSON 版本仅认 fitness nested)."""
    if _PG_BACKEND_ACTIVE:
        from explain_engine.persistence.lexicon_pg import (
            _render_lexicon_for_prompt as pg_impl,
        )
        return pg_impl(vars_list)
    return _render_lexicon_for_prompt_json_impl(vars_list)
