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
    """Lazy 单例 async pool. min/max/timeout 从 env 读 (留生产调优口)."""
    global _async_pool
    if _async_pool is None:
        _async_pool = AsyncConnectionPool(
            _get_dsn(),
            min_size=int(os.environ.get("EXPLAIN_DB_POOL_MIN", "2")),
            max_size=int(os.environ.get("EXPLAIN_DB_POOL_MAX", "10")),
            timeout=int(os.environ.get("EXPLAIN_DB_CONNECT_TIMEOUT_S", "5")),
            open=False,
        )
        await _async_pool.open()
    return _async_pool


def get_sync_pool() -> ConnectionPool:
    """Lazy 单例 sync pool (cli subcommand 用, 避免 asyncio.run 包裹)."""
    global _sync_pool
    if _sync_pool is None:
        _sync_pool = ConnectionPool(
            _get_dsn(),
            min_size=1,
            max_size=5,
            timeout=5,
            open=False,
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
    """Insert 1 var (无 embedding 列, Wave 3 加 embedding 支持).

    var 必含 14 字段: global_id, name, description, abstraction_level, epistemic,
    canonical_mechanism, canonical_signature, canonical_model_ver,
    reuse_count, avg_essentialness, avg_consistency,
    first_seen_at, last_seen_at, source_sessions.
    """
    await conn.execute(
        """INSERT INTO variables (
            global_id, name, description, abstraction_level, epistemic,
            canonical_mechanism, canonical_signature, canonical_model_ver,
            reuse_count, avg_essentialness, avg_consistency,
            first_seen_at, last_seen_at, source_sessions
        ) VALUES (
            %(global_id)s, %(name)s, %(description)s, %(abstraction_level)s, %(epistemic)s,
            %(canonical_mechanism)s, %(canonical_signature)s, %(canonical_model_ver)s,
            %(reuse_count)s, %(avg_essentialness)s, %(avg_consistency)s,
            %(first_seen_at)s, %(last_seen_at)s, %(source_sessions)s
        )""",
        var,
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
