"""Phase 17.1: PostgreSQL + pgvector 替代 variables.json lexicon 落地.

Public API (跟旧 lexicon.py signature 保持兼容, 由 Wave 4+ 落地):
- async def flush_to_lexicon(session, storage, llm=None, llm_canonical_top_k=3) -> int
- def get_lexicon_top_k_for_compress(storage, k=20) -> list[dict]
- def get_top_n_vars(storage, n) -> list[VariableNode]
- def _render_lexicon_for_prompt(vars_list) -> str

Internal (Wave 2):
- LexiconDBError 异常类
- _get_dsn / get_async_pool / get_sync_pool / verify_connection
- _insert_var / _find_var_by_id / _update_var_fitness / _list_vars_top_k / _delete_var
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from pgvector.psycopg import register_vector, register_vector_async
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool

DEFAULT_DSN = "postgresql://explain:changeme@127.0.0.1:5432/explain"

# Module-level pool 单例 (lazy open). reset_pg fixture teardown 时清 None
# 让下个 test 用新 dsn 重建.
_async_pool: AsyncConnectionPool | None = None
_sync_pool: ConnectionPool | None = None


class LexiconDBError(Exception):
    """PG 连接 / query 失败统一错误类."""


def _get_dsn() -> str:
    """读 EXPLAIN_DB_URL env, 未设 fallback DEFAULT_DSN."""
    return os.environ.get("EXPLAIN_DB_URL", DEFAULT_DSN)


async def get_async_pool() -> AsyncConnectionPool:
    """Lazy 单例 async pool. min/max/timeout 从 env 读 (留生产调优口).

    configure callback: 每 connection 注册 pgvector adapter, 让 vector(1024)
    列能直接绑 list[float] / np.ndarray, 读出 np.ndarray.
    """
    global _async_pool
    if _async_pool is None:
        _async_pool = AsyncConnectionPool(
            _get_dsn(),
            min_size=int(os.environ.get("EXPLAIN_DB_POOL_MIN", "2")),
            max_size=int(os.environ.get("EXPLAIN_DB_POOL_MAX", "10")),
            # default 10s: register_vector_async configure 跑 oid lookup query,
            # 远程 PG pre-warm 2 conn 时 5s 容易超 (实测 17s, 真实环境调高安全).
            timeout=int(os.environ.get("EXPLAIN_DB_CONNECT_TIMEOUT_S", "10")),
            open=False,
            configure=register_vector_async,
        )
        await _async_pool.open()
    return _async_pool


def get_sync_pool() -> ConnectionPool:
    """Lazy 单例 sync pool (cli subcommand 用, 避免 asyncio.run 包裹).

    configure callback: 同 async pool, 注册 pgvector sync adapter.
    """
    global _sync_pool
    if _sync_pool is None:
        _sync_pool = ConnectionPool(
            _get_dsn(),
            min_size=1,
            max_size=5,
            timeout=5,
            open=False,
            configure=register_vector,
        )
        _sync_pool.open()
    return _sync_pool


async def verify_connection() -> None:
    """启动 fail-fast — chat REPL 启动前调.

    成功: SELECT 1 返 1, 不抛.
    失败: 抛 LexiconDBError, 含 dsn host (mask 密码) + 原因 + Hint (server / env 检查).
    """
    try:
        pool = await get_async_pool()
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
    except Exception as exc:
        # mask 密码: 取 '@' 后部分 (host:port/db)
        dsn_tail = _get_dsn().split("@")[-1]
        raise LexiconDBError(
            f"无法连接 lexicon DB ({dsn_tail}): "
            f"{type(exc).__name__}: {exc}\n"
            f"Hint: 检查 server (ssh 172.30.26.12 'docker compose ps') / EXPLAIN_DB_URL."
        ) from exc


# ── CRUD helpers (内部, Wave 4 公开 API 层调) ────────────────────────────


async def _insert_var(conn, var: dict[str, Any]) -> None:
    """Insert 1 var (含可选 embedding 列, Wave 3).

    var 必含 14 字段: global_id, name, description, abstraction_level, epistemic,
    canonical_mechanism, canonical_signature, canonical_model_ver,
    reuse_count, avg_essentialness, avg_consistency,
    first_seen_at, last_seen_at, source_sessions.

    var.get('embedding') 可选, list[float] (len 1024) / np.ndarray / None.
    pgvector adapter 已在 pool configure 注册, list 自动转 vector.
    """
    params = {**var, "embedding": var.get("embedding")}
    await conn.execute(
        """INSERT INTO variables (
            global_id, name, description, abstraction_level, epistemic,
            canonical_mechanism, canonical_signature, canonical_model_ver,
            reuse_count, avg_essentialness, avg_consistency,
            first_seen_at, last_seen_at, source_sessions, embedding
        ) VALUES (
            %(global_id)s, %(name)s, %(description)s, %(abstraction_level)s, %(epistemic)s,
            %(canonical_mechanism)s, %(canonical_signature)s, %(canonical_model_ver)s,
            %(reuse_count)s, %(avg_essentialness)s, %(avg_consistency)s,
            %(first_seen_at)s, %(last_seen_at)s, %(source_sessions)s, %(embedding)s
        )""",
        params,
    )


async def _find_var_by_id(conn, global_id: str) -> dict[str, Any] | None:
    """SELECT 全列 by global_id, 返 dict (用 dict_row factory) 或 None."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT * FROM variables WHERE global_id = %s", (global_id,)
        )
        return await cur.fetchone()


async def _update_var_fitness(
    conn,
    global_id: str,
    reuse_count: int,
    avg_essentialness: float,
    avg_consistency: float,
    last_seen_at: datetime,
) -> None:
    """UPDATE fitness 4 字段 (reuse_count / avg_ess / avg_cons / last_seen_at).

    updated_at 由 variables_set_updated_at trigger 自动刷 NOW().
    """
    await conn.execute(
        """UPDATE variables SET
            reuse_count = %s,
            avg_essentialness = %s,
            avg_consistency = %s,
            last_seen_at = %s
           WHERE global_id = %s""",
        (reuse_count, avg_essentialness, avg_consistency, last_seen_at, global_id),
    )


async def _list_vars_top_k(conn, k: int = 20) -> list[dict[str, Any]]:
    """SELECT top-k vars by composite score (avg_ess * avg_cons * reuse_count) DESC.

    composite score 反映 fitness 综合质量 (essential + consistent + 复用频繁).
    返 list[dict] (dict_row factory), 长度 <= k.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """SELECT * FROM variables
               ORDER BY (avg_essentialness * avg_consistency * reuse_count) DESC
               LIMIT %s""",
            (k,),
        )
        return await cur.fetchall()


async def _delete_var(conn, global_id: str) -> bool:
    """DELETE FROM variables WHERE global_id. 返 True iff 真删了 1 行."""
    cur = await conn.execute(
        "DELETE FROM variables WHERE global_id = %s", (global_id,)
    )
    return cur.rowcount > 0


# ── Wave 3: pgvector cosine dedup ───────────────────────────────────────


async def _find_duplicate(
    conn,
    embedding: list[float] | Any,  # list[float] | np.ndarray
    threshold: float = 0.85,
) -> str | None:
    """pgvector cosine dedup query — N=10k ms 级 (HNSW O(log N)).

    pgvector `<=>` 是 cosine distance ∈ [0, 2]; similarity = 1 - distance.
    threshold 是 similarity 下限 (相似度 > threshold 才算 dup).

    embedding IS NULL 的行天然不进比 (`<=>` NULL 会 NULL 而非比). 显式
    `WHERE embedding IS NOT NULL` 防 planner 把 NULL 行带进 ORDER BY.

    返胜出的 global_id (sim 最大那行), 或 None (库空 / 无超 threshold).
    """
    cur = await conn.execute(
        """SELECT global_id, 1 - (embedding <=> %s::vector) AS sim
           FROM variables
           WHERE embedding IS NOT NULL
             AND 1 - (embedding <=> %s::vector) > %s
           ORDER BY embedding <=> %s::vector
           LIMIT 1""",
        (embedding, embedding, threshold, embedding),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def _merge_into_existing(
    conn,
    winner_id: str,
    loser_node: Any,  # VariableNode (有 .activation 属性)
    session: Any,  # Session (有 .meta.session_id)
    loser_canonical: str,  # 当前 round canonical, 暂不进 winner (保 winner 自己 canonical) — 留参 audit log Wave 4+ 用
) -> None:
    """合 loser 入 winner: source_sessions union + fitness running-avg + last_seen_at NOW().

    Winner 的 canonical_* 字段全保留不动 (loser_canonical 仅 audit log 用, 暂不写).

    用 `FOR UPDATE` 锁行防并发 merge 冲突. winner 已被删 (edge case) → noop.
    """
    cur = await conn.execute(
        """SELECT reuse_count, avg_essentialness, avg_consistency, source_sessions
           FROM variables WHERE global_id = %s FOR UPDATE""",
        (winner_id,),
    )
    row = await cur.fetchone()
    if row is None:
        # winner 已被删, edge case (并发 retro_dedup 可能), silent noop
        return
    old_count, old_ess, old_cons, old_sessions = row
    new_count = old_count + 1
    # running-avg essentialness: 旧总和 + 新 / 新 count
    new_ess = (old_ess * old_count + loser_node.activation) / new_count
    # consistency 保 winner (loser 没新 cons 数据)
    new_cons = old_cons
    # union sessions (preserve order, dedup)
    sessions = list(old_sessions)
    sid = session.meta.session_id
    if sid not in sessions:
        sessions.append(sid)
    await conn.execute(
        """UPDATE variables SET
             reuse_count = %s,
             avg_essentialness = %s,
             avg_consistency = %s,
             source_sessions = %s,
             last_seen_at = NOW()
           WHERE global_id = %s""",
        (new_count, new_ess, new_cons, sessions, winner_id),
    )


# ── Wave 4: public API 替老 lexicon.py JSON ─────────────────────────────


def _should_promote(node: Any) -> bool:
    """Phase 17.1 Task 4.1: 直接复用老 lexicon._should_promote 语义.

    - skip L0 (observations 不进 lexicon)
    - skip non-active (stale/decayed)
    - skip activation < 0.5 (conservative threshold)
    """
    return (
        node.abstraction_level >= 1
        and node.lifecycle_state == "active"
        and node.activation >= 0.5
    )


async def _build_canonical_mechanism(
    node: Any,  # VariableNode (有 .id .name .abstraction_level .description)
    session: Any,  # Session (有 .state.graph.{nodes, edges})
    llm: Any | None,  # LLMClient | None
) -> str:
    """Phase 17.1 Task 4.2: 复用老 lexicon._build_canonical_mechanism (无 cache).

    有 llm: 调 LLM 用 node + neighbors 信息 prompt 出 1 句话.
    无 llm 或 LLMError: edge-based fallback —
      "通常 cause [outgoing target names]; 由 [incoming source names] cause".

    Wave 5 会在前面套 canonical_signature cache 层 (cache hit 直返, miss 才调本函数),
    Wave 4 先 ship 无 cache 版本, 保持跟老 lexicon.py 行为一致.
    """
    from explain_engine.llm.client import Message
    from explain_engine.llm.errors import LLMError

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
        # 老 lexicon Wave 2 review I-1: 窄化到 LLMError (非 LLM 异常应 propagate).
        return _fallback()


def _compute_signature(name: str, canonical_mechanism: str) -> str:
    """sha256(name + '::' + canonical_mechanism)[:16] — 用作 canonical cache key
    + global_id 前 8 char (Track B Wave 5 cache 复用).

    Phase 17.1 Wave 4: 暂只用作 canonical_signature 字段, Wave 5 加 cache 查找.
    """
    import hashlib

    s = f"{name}::{canonical_mechanism}".encode()
    return hashlib.sha256(s).hexdigest()[:16]


def _compute_global_id(name: str, canonical_mechanism: str) -> str:
    """global_id = 'v_' + sha256(name + '::' + canonical_mechanism)[:8].

    跟老 lexicon._compute_global_id 同语义 — 同 (name, canonical) 同 id (conservative
    split, 宁可重复存不要 wrong merge). Wave 4 用作 insert 新 var 的 PK.
    """
    return "v_" + _compute_signature(name, canonical_mechanism)[:8]


async def _batch_embed(canonicals: list[str]) -> list[list[float] | None]:
    """Phase 17.1 Task 4.3: BGE-M3 batch embed canonical mechanisms.

    Behavior:
    - 空 input 直返 [] (省 embedder load).
    - EXPLAIN_EMBEDDING_DISABLED=1 → 返 [None] * N (test 模式默认走这条).
    - Embedder load / encode 异常 → log warning, 返 [None] * N (best-effort,
      不 raise — 让 flush_to_lexicon 仍能继续 insert, embedding=None 即 legacy).
    """
    if not canonicals:
        return []
    if os.environ.get("EXPLAIN_EMBEDDING_DISABLED") == "1":
        return [None] * len(canonicals)
    try:
        from explain_engine.embedding.bge_m3 import get_embedder

        embedder = get_embedder()
        vecs = embedder.embed(canonicals)
        return [vec.tolist() for vec in vecs]
    except Exception as exc:
        import logging

        logging.warning(
            f"_batch_embed: BGE-M3 batch embed failed: "
            f"{type(exc).__name__}: {exc}. Falling back to embedding=None for all."
        )
        return [None] * len(canonicals)


async def _insert_new_var(
    conn,
    node: Any,  # VariableNode
    canonical: str,
    embedding: list[float] | None,
    session: Any,  # Session (有 .meta.session_id)
) -> None:
    """Wave 4 helper: 把 (VariableNode, canonical, embedding, session) 转成 var dict
    并调 _insert_var. 跟老 lexicon._upsert_var 'brand new entry' 分支同语义.
    """
    sid = session.meta.session_id
    now = datetime.now(__import__("datetime").timezone.utc)
    var = {
        "global_id": _compute_global_id(node.name, canonical),
        "name": node.name,
        "description": node.description,
        "abstraction_level": node.abstraction_level,
        "epistemic": node.epistemic,
        "canonical_mechanism": canonical,
        "canonical_signature": _compute_signature(node.name, canonical),
        "canonical_model_ver": "v1",
        "reuse_count": 1,
        "avg_essentialness": node.activation,  # Phase 10 第一版 proxy (同老)
        "avg_consistency": node.stability,  # Phase 10 第一版 proxy
        "first_seen_at": now,
        "last_seen_at": now,
        "source_sessions": [sid],
        "embedding": embedding,
    }
    await _insert_var(conn, var)


async def flush_to_lexicon(
    session: Any,
    storage: Any,  # 保 signature, Wave 4 PG impl 不再读 storage path
    llm: Any | None = None,
    llm_canonical_top_k: int = 3,
) -> int:
    """Phase 17.1 Wave 4: PG impl, 替老 lexicon.flush_to_lexicon JSON 行为.

    Pipeline:
      1. session.state.graph.nodes 过 _should_promote → candidates
      2. sort by node.activation desc (高 activation 优先享受 LLM canonical 名额)
      3. 前 llm_canonical_top_k 个走 LLM canonical, 其余 edge fallback
      4. _batch_embed 全 canonicals (test mode: disabled → 全 None)
      5. async with transaction: per (node, canon, emb):
           - emb 非 None → _find_duplicate(threshold=0.85)
           - 命中 winner → _merge_into_existing (loser 入 winner) 不算 promoted
           - 未命中 / emb=None → _insert_new_var, promoted += 1
      6. 返 promoted (新 insert 数; merge 算 0).

    Wave 5 加 canonical_signature cache 层 (无需重算 LLM canonical),
    Wave 6 加 lazy retroactive dedup. 本 Wave 先 ship 最小 PG 替 JSON.

    NOTE: signature 跟老 lexicon.flush_to_lexicon 完全一致 (session, storage,
    llm, llm_canonical_top_k), chat / cli 调点不变. storage 参数保留兼容 (未使用).
    """
    pool = await get_async_pool()
    candidates = [
        node
        for node in session.state.graph.nodes.values()
        if _should_promote(node)
    ]
    candidates.sort(key=lambda n: -n.activation)

    canonicals: list[str] = []
    for i, node in enumerate(candidates):
        effective_llm = llm if i < llm_canonical_top_k else None
        canon = await _build_canonical_mechanism(node, session, effective_llm)
        canonicals.append(canon)

    embeddings = await _batch_embed(canonicals)

    promoted = 0
    async with pool.connection() as conn:
        async with conn.transaction():
            for node, canon, emb in zip(
                candidates, canonicals, embeddings, strict=True
            ):
                winner_id: str | None = None
                if emb is not None:
                    winner_id = await _find_duplicate(conn, emb, threshold=0.85)
                if winner_id:
                    await _merge_into_existing(
                        conn, winner_id, node, session, canon,
                    )
                    continue
                await _insert_new_var(conn, node, canon, emb, session)
                promoted += 1
    return promoted


def _dict_to_variable_node(row: dict[str, Any]) -> Any:
    """Phase 17.1 Task 4.6: DB row dict → VariableNode 实例.

    lexicon DB 不存 confidence (老 lexicon JSON 也不存), 默认填 0.7 — 跟老
    _make_node helper 默认一致. 其他必填字段直接从 row 映射.

    Wave 4 暂不映射 fitness 字段进 VariableNode (它们是 var 维度 metadata,
    VariableNode 是 session-level state). Wave 7+ 视需求再加.
    """
    from explain_engine.schema.nodes import VariableNode

    return VariableNode(
        id=row["global_id"],
        name=row["name"],
        description=row["description"],
        abstraction_level=row["abstraction_level"],
        confidence=0.7,  # lexicon var 不存 confidence, 默认 0.7
        epistemic=row["epistemic"],
    )


def get_top_n_vars(storage: Any, n: int) -> list[Any]:
    """Phase 17.1 Task 4.6: sync API — 返 list[VariableNode] (跟老 lexicon 同).

    内部复用 get_lexicon_top_k_for_compress → 转 VariableNode list.
    chat /compress 上下文显式需要 VariableNode 实例 (不是 dict) 时调本函数.
    """
    rows = get_lexicon_top_k_for_compress(storage, k=n)
    return [_dict_to_variable_node(row) for row in rows]


def get_lexicon_top_k_for_compress(
    storage: Any,  # 保 signature, PG impl 不读 file
    k: int = 20,
) -> list[dict[str, Any]]:
    """Phase 17.1 Task 4.5: sync API — chat /compress 前调取 LLM prior.

    ORDER BY composite score (avg_essentialness * avg_consistency * reuse_count) DESC,
    LIMIT k. 返 list[dict] (psycopg dict_row factory), 含 variables 全列.

    sync 而非 async — chat /compress 调点是 sync subcommand handler (避免上层
    强行套 asyncio.run). 用 module-level sync ConnectionPool (跟 async pool 分开
    实例, 不依赖 event loop).

    Wave 4 默认 k=20 (跟老 lexicon._select_top_k_vars 同). k<=0 返 [].
    """
    if k <= 0:
        return []
    pool = get_sync_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT * FROM variables
                   ORDER BY (avg_essentialness * avg_consistency * reuse_count) DESC
                   LIMIT %s""",
                (k,),
            )
            return cur.fetchall()
