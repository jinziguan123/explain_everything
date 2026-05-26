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
