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
