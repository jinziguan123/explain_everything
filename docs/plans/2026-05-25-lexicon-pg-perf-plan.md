# Phase 17.1: Lexicon PG Migration + Perf Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把 lexicon 从 local `variables.json` 迁到远程 PostgreSQL 16 + pgvector (172.30.26.12), 同时落 canonical mechanism cache + lazy retroactive dedup + /compress 重入 4 track.

**Architecture:** psycopg3 双 pool (async chat + sync cli) 走 TCP 5432 连 docker-compose 部署的 pgvector/pgvector:pg16. 单 `variables` 表含 metadata + `vector(1024)` embedding 列 + HNSW index, source_sessions 用 PG TEXT[]. Canonical cache 借表内 `canonical_signature` 列, lazy dedup 阈值 N>100 + flush>5 触发. Track D 仅改 stage_gate 1 行.

**Tech Stack:** Python 3.11 + psycopg3 (binary + pool) + pgvector PG extension + testcontainers[postgres] (dev test) + 删旧 numpy/JSON 路径.

**Design doc:** [docs/plans/2026-05-25-lexicon-pg-perf-design.md](2026-05-25-lexicon-pg-perf-design.md) — 读 §4 (架构) + §5 (PG schema) + §6 (client) + §7 (migration+errors+compress 重入) + §8 (testing) + §10 (docker-compose 附录) + §11 (风险) 先.

**Commit 规范** (项目惯例, 严格 follow):
- venv `.venv/bin/python -m pytest`, ruff `.venv/bin/ruff check` (`--fix` 自动 import order / UP017)
- 中文 commit msg, 末尾 `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer
- format: `<scope> · Phase 17.1 Task N.M: <action>` (例 `persistence/lexicon_pg · Phase 17.1 Task 2.3: insert_var SQL`)
- 大多 task 1 commit; 复杂拆 2 (red commit + green commit)
- 一次 1 task, 全绿才进
- 1135 既有 test 全程不挂 (Wave 9 删 test_lexicon.py / test_lexicon_merge.py 是预期)
- 绝不 push / amend / --no-verify / 动 git config

---

## Wave 0: 部署前置 (用户手做, 不算 commit)

**目标**: 你在 172.30.26.12 部署 pgvector docker, 让本机 `verify_connection()` 能连. 不动 git, 不算 task.

### 操作步骤

```bash
# 1. 准备目录
ssh user@172.30.26.12
sudo mkdir -p /opt/explain-db/{pgdata,init}
sudo chown $USER /opt/explain-db -R
exit

# 2. Copy 文件 (用 Wave 1 Task 1.2 生成的)
scp deploy/postgres/docker-compose.yml user@172.30.26.12:/opt/explain-db/
scp deploy/postgres/init/01-init.sql user@172.30.26.12:/opt/explain-db/init/

# 3. 启动
ssh user@172.30.26.12
cd /opt/explain-db
EXPLAIN_DB_PASSWORD=<你设密码> docker compose up -d

# 4. 验证 server
docker compose ps                                              # healthy
docker compose exec postgres psql -U explain -c "\dx"          # 显 vector extension
docker compose exec postgres psql -U explain -c "\dt"          # 显 3 表

# 5. 本机配 env
echo 'export EXPLAIN_DB_URL="postgresql://explain:<密码>@172.30.26.12:5432/explain"' >> ~/.zshrc
source ~/.zshrc

# 6. 验证本机连 (Wave 2 Task 2.3 完成后才能跑)
.venv/bin/python -c "
import asyncio
from explain_engine.persistence.lexicon_pg import verify_connection
asyncio.run(verify_connection())
print('OK')
"
```

**Wave 1 Task 1.2 会自动生成 `deploy/postgres/{docker-compose.yml, init/01-init.sql, README.md}` 三个文件**, 你 scp 完之后就能部署. Wave 1 完成前不阻塞本地 test (testcontainers 自动 spin local container).

---

## Wave 1: testcontainers + conftest + DDL 模块

**目标**: 加 dependencies + conftest fixture (pg_container session-scoped + reset_pg autouse) + DDL 常量, 让 Wave 2+ test 有真 PG 跑.

**依赖**: 无

**文件**:
- Modify: `pyproject.toml` (deps + dev-deps)
- Create: `deploy/postgres/docker-compose.yml`
- Create: `deploy/postgres/init/01-init.sql`
- Create: `deploy/postgres/README.md`
- Create: `src/explain_engine/persistence/lexicon_pg_schema.py` (DDL 常量)
- Modify: `tests/conftest.py` (pg_container + reset_pg fixture)

### Task 表

| Task | Red Test / 操作 | 关键实现 | Commit Message |
|---|---|---|---|
| 1.1 | (no test, 改 pyproject) | `pyproject.toml` deps 加 `"psycopg[binary,pool]>=3.2"`, dev-deps 加 `"testcontainers[postgres]>=4.0"`. 跑 `uv sync` 更新 lockfile | `deps · Phase 17.1 Task 1.1: psycopg3 + testcontainers[postgres] 依赖` |
| 1.2 | (no test) | 创 3 文件 `deploy/postgres/{docker-compose.yml, init/01-init.sql, README.md}`, 内容**逐字 copy 自 design §10** (docker-compose.yml / init/01-init.sql / README.md 三段) | `deploy/postgres · Phase 17.1 Task 1.2: docker-compose + init.sql + 部署 README` |
| 1.3 | `test_lexicon_pg_schema_constants_match_init_sql` (tests/test_lexicon_pg_pool.py) | 新 module `src/explain_engine/persistence/lexicon_pg_schema.py`: 读 `deploy/postgres/init/01-init.sql` 文件作 `INIT_SQL` 常量, expose `DDL_INIT_SQL: str`. test 验 constant 含 `CREATE TABLE variables` / `CREATE EXTENSION vector` / `vector(1024)` | `persistence/lexicon_pg · Phase 17.1 Task 1.3: DDL 常量 module (single source from init.sql)` |
| 1.4 | `test_pg_container_fixture_provides_dsn` (tests/test_lexicon_pg_pool.py) | conftest.py 加 `pg_container` session fixture (testcontainers `PostgresContainer("pgvector/pgvector:pg16")`, 启动后跑 `DDL_INIT_SQL`, yield psycopg-style DSN). test 验拿到 dsn 且能 `psycopg.connect(dsn).execute("SELECT 1")` | `tests/conftest · Phase 17.1 Task 1.4: pg_container session fixture (testcontainers spin)` |
| 1.5 | `test_reset_pg_truncates_between_tests` (tests/test_lexicon_pg_pool.py) | conftest.py 加 `reset_pg` autouse fixture (function-scoped, 每 test 前 `TRUNCATE variables, lexicon_merge_audit; UPDATE lexicon_meta SET flush_count_since=0`). 含 2 个 test: 第 1 个 insert 1 var, 第 2 个 verify 表空 | `tests/conftest · Phase 17.1 Task 1.5: reset_pg autouse (TRUNCATE per test)` |

### 技术参考

**conftest pg_container fixture** (Task 1.4 green 代码):

```python
# tests/conftest.py 末尾加

import pytest

@pytest.fixture(scope="session")
def pg_container():
    """Phase 17.1: 1 个 pgvector container per test session, 跑 init SQL."""
    pytest.importorskip("testcontainers.postgres")
    import psycopg
    from testcontainers.postgres import PostgresContainer

    from explain_engine.persistence.lexicon_pg_schema import DDL_INIT_SQL

    pg = PostgresContainer("pgvector/pgvector:pg16")
    pg.start()
    try:
        # testcontainers 默认返 SQLAlchemy 风格 dsn, 剥前缀给 psycopg3
        url = pg.get_connection_url()
        dsn = url.replace("postgresql+psycopg2://", "postgresql://")
        # Apply schema
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(DDL_INIT_SQL)
        yield dsn
    finally:
        pg.stop()
```

**conftest reset_pg fixture** (Task 1.5 green):

```python
@pytest.fixture(autouse=True)
def reset_pg(pg_container, monkeypatch):
    """每 test 前 TRUNCATE, 跨 test 隔离."""
    import psycopg

    monkeypatch.setenv("EXPLAIN_DB_URL", pg_container)
    # 清表 (synchronous, 速度足够)
    with psycopg.connect(pg_container, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE variables, lexicon_merge_audit; "
            "UPDATE lexicon_meta SET flush_count_since = 0, last_retro_dedup_at = NULL "
            "WHERE id = 1"
        )
    yield
    # 清理 module-level pool (避免 pool 跨 test 持 connection)
    from explain_engine.persistence import lexicon_pg
    lexicon_pg._async_pool = None
    lexicon_pg._sync_pool = None
```

**Schema module** (Task 1.3 green):

```python
# src/explain_engine/persistence/lexicon_pg_schema.py
"""Phase 17.1: DDL 常量 (single source — 跟 deploy/postgres/init/01-init.sql 同步).

Test conftest pg_container 跑这段 SQL 起 PG container.
Production deploy 也跑同一文件 (docker-entrypoint-initdb.d 挂载).
"""
from pathlib import Path

_INIT_SQL_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "deploy" / "postgres" / "init" / "01-init.sql"
)

DDL_INIT_SQL: str = _INIT_SQL_PATH.read_text(encoding="utf-8")
```

### Verify 命令

```bash
.venv/bin/python -m pytest tests/test_lexicon_pg_pool.py -v
.venv/bin/ruff check src/explain_engine/persistence/lexicon_pg_schema.py tests/conftest.py
```

预期 Wave 1 完: 5 PASS (test_lexicon_pg_pool.py 5 个 fixture/schema test), 全量既有 1135 不挂 (新 fixture autouse 但 reset_pg 只 truncate 不存在的表 → 老 test 不踩 pg_container 路径 → 自动 skip 改 testcontainers, 但若 conftest autouse 强制 init pg_container 会让所有 test 启动慢. **注意**: pg_container 是 session-scoped + lazy, reset_pg fixture 会 invoke pg_container, 这会让所有 test 启动时都 spin container. **改进**: reset_pg 只在 test 模块 import lexicon_pg 时才 truncate. 见 Task 1.5 实现细节).

**关键 risk**: Wave 1 完后既有 1135 test 不能挂. 若 reset_pg 让所有 test spin container 慢, 改成 `@pytest.fixture(autouse=False)` + 每 lexicon_pg test class 显式 `@pytest.mark.usefixtures("reset_pg")`.

---

## Wave 2: lexicon_pg.py Core CRUD

**目标**: 主 module 落地 — pool / verify / 基础 CRUD (insert / find / update / list / delete). 不含 embedding (Wave 3) / API (Wave 4).

**依赖**: Wave 1

**文件**:
- Create: `src/explain_engine/persistence/lexicon_pg.py`
- Modify: `tests/test_lexicon_pg_pool.py`

### Task 表

| Task | Red Test | 关键实现 | Commit Message |
|---|---|---|---|
| 2.1 | `test_lexicon_db_error_is_exception` | `class LexiconDBError(Exception): pass` | `persistence/lexicon_pg · Phase 17.1 Task 2.1: LexiconDBError 异常类` |
| 2.2 | `test_get_dsn_returns_env_or_default` + `test_get_async_pool_returns_singleton` | `_get_dsn()` 读 env / fallback default; `get_async_pool()` 单例 + lazy open | `persistence/lexicon_pg · Phase 17.1 Task 2.2: _get_dsn + get_async_pool + get_sync_pool 单例` |
| 2.3 | `test_verify_connection_ok` + `test_verify_connection_fails_with_friendly_hint` | `verify_connection()` SELECT 1, 失败抛 `LexiconDBError` 含 Hint | `persistence/lexicon_pg · Phase 17.1 Task 2.3: verify_connection fail-fast + Hint` |
| 2.4 | `test_insert_var_basic_no_embedding` | `_insert_var(conn, var_dict)` 拼 SQL INSERT (不含 embedding 列), test 验 row 存入 | `persistence/lexicon_pg · Phase 17.1 Task 2.4: _insert_var (no embedding)` |
| 2.5 | `test_find_var_by_id` | `_find_var_by_id(conn, global_id)` 返 dict 或 None | `persistence/lexicon_pg · Phase 17.1 Task 2.5: _find_var_by_id` |
| 2.6 | `test_update_var_fitness` | `_update_var_fitness(conn, gid, reuse_count, avg_essentialness, ...)` UPDATE fitness 字段, `updated_at` trigger 自动刷 | `persistence/lexicon_pg · Phase 17.1 Task 2.6: _update_var_fitness (trigger 自动 updated_at)` |
| 2.7 | `test_list_vars_top_k_by_composite_score` | `_list_vars_top_k(conn, k=20)` SELECT ORDER BY (avg_essentialness * avg_consistency * reuse_count) DESC LIMIT k | `persistence/lexicon_pg · Phase 17.1 Task 2.7: _list_vars_top_k composite score` |
| 2.8 | `test_delete_var_removes_row` | `_delete_var(conn, gid)` DELETE FROM variables WHERE global_id = %s | `persistence/lexicon_pg · Phase 17.1 Task 2.8: _delete_var` |

### 技术参考

**主 module 骨架** (Task 2.2 + 2.3 green):

```python
# src/explain_engine/persistence/lexicon_pg.py
"""Phase 17.1: PostgreSQL + pgvector 替代 variables.json.

Public API (跟旧 lexicon.py signature 保持):
- async def flush_to_lexicon(session, storage, llm=None, llm_canonical_top_k=3) -> int
- def get_lexicon_top_k_for_compress(storage, k=20) -> list[dict]
- def get_top_n_vars(storage, n) -> list[VariableNode]
- def _render_lexicon_for_prompt(vars_list) -> str
- (internal) _insert_var / _find_var_by_id / _update_var_fitness / _list_vars_top_k / _delete_var
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from psycopg_pool import AsyncConnectionPool, ConnectionPool

if TYPE_CHECKING:
    pass  # ChatSession / StorageV2 等仅在 API 层 import


DEFAULT_DSN = "postgresql://explain:changeme@127.0.0.1:5432/explain"

_async_pool: AsyncConnectionPool | None = None
_sync_pool: ConnectionPool | None = None


class LexiconDBError(Exception):
    """PG 连接 / query 失败统一错误类."""


def _get_dsn() -> str:
    return os.environ.get("EXPLAIN_DB_URL", DEFAULT_DSN)


async def get_async_pool() -> AsyncConnectionPool:
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
    global _sync_pool
    if _sync_pool is None:
        _sync_pool = ConnectionPool(
            _get_dsn(), min_size=1, max_size=5, timeout=5, open=False,
        )
        _sync_pool.open()
    return _sync_pool


async def verify_connection() -> None:
    """启动 fail-fast — chat REPL 启动前调."""
    try:
        pool = await get_async_pool()
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
    except Exception as exc:
        raise LexiconDBError(
            f"无法连接 lexicon DB ({_get_dsn().split('@')[-1]}): "
            f"{type(exc).__name__}: {exc}\n"
            f"Hint: 检查 server (ssh 172.30.26.12 'docker compose ps') / EXPLAIN_DB_URL."
        ) from exc
```

**`_insert_var`** (Task 2.4 green, 不含 embedding):

```python
async def _insert_var(conn, var: dict[str, Any]) -> None:
    """Insert 1 var, embedding 列由 Wave 3 加. fitness 字段必含."""
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
```

### Red Test 示例 (Task 2.4)

```python
# tests/test_lexicon_pg_pool.py

import pytest
from datetime import datetime, UTC

class TestInsertVar:
    @pytest.mark.asyncio
    async def test_insert_var_basic_no_embedding(self):
        from explain_engine.persistence.lexicon_pg import (
            get_async_pool, _insert_var, _find_var_by_id,
        )
        pool = await get_async_pool()
        var = {
            "global_id": "v_test0001",
            "name": "测试 var",
            "description": "用于 test",
            "abstraction_level": 1,
            "epistemic": "insight",
            "canonical_mechanism": "test mech",
            "canonical_signature": "abc12345",
            "canonical_model_ver": "v1",
            "reuse_count": 1,
            "avg_essentialness": 0.5,
            "avg_consistency": 0.7,
            "first_seen_at": datetime.now(UTC),
            "last_seen_at": datetime.now(UTC),
            "source_sessions": ["s_test0001"],
        }
        async with pool.connection() as conn:
            await _insert_var(conn, var)
            row = await _find_var_by_id(conn, "v_test0001")
        assert row is not None
        assert row["name"] == "测试 var"
        assert row["source_sessions"] == ["s_test0001"]
```

### Verify 命令

```bash
.venv/bin/python -m pytest tests/test_lexicon_pg_pool.py -v
.venv/bin/ruff check src/explain_engine/persistence/lexicon_pg.py
```

---

## Wave 3: pgvector Embedding Store + HNSW + Cosine Dedup

**目标**: 加 embedding 列支持 + HNSW index + cosine dedup query. 验证 query plan 用 index.

**依赖**: Wave 2

**文件**:
- Modify: `src/explain_engine/persistence/lexicon_pg.py`
- Create: `tests/test_lexicon_pg_vector.py`

### Task 表

| Task | Red Test | 关键实现 | Commit Message |
|---|---|---|---|
| 3.1 | `test_insert_var_with_embedding` | `_insert_var` 加 `embedding` 字段支持 (vector(1024)). psycopg `register_vector` 注册 numpy → vector adapter | `persistence/lexicon_pg · Phase 17.1 Task 3.1: _insert_var 加 embedding 列 + register_vector` |
| 3.2 | `test_find_duplicate_by_cosine` | `_find_duplicate(conn, embedding, threshold=0.85)` SELECT WHERE `1 - (embedding <=> %s) > threshold` ORDER BY distance LIMIT 1 | `persistence/lexicon_pg · Phase 17.1 Task 3.2: _find_duplicate cosine query` |
| 3.3 | `test_hnsw_index_used_in_query_plan` | EXPLAIN ANALYZE SELECT ... `<=>` ... 输出应含 `Index Scan using idx_variables_embedding` | `persistence/lexicon_pg · Phase 17.1 Task 3.3: 验 HNSW 索引被命中 (EXPLAIN ANALYZE)` |
| 3.4 | `test_pgvector_cosine_equals_numpy_cosine` | 同 2 个 random vec, pgvector `1 - (a <=> b)` 应等于 numpy `np.dot(a,b) / (np.linalg.norm(a)*np.linalg.norm(b))` (容差 1e-4) | `persistence/lexicon_pg · Phase 17.1 Task 3.4: pgvector vs numpy cosine 数学等价验证` |
| 3.5 | `test_insert_var_embedding_null_allowed` | embedding=None insert 不报错, find 返 row 含 embedding=None | `persistence/lexicon_pg · Phase 17.1 Task 3.5: embedding NULL legacy 支持` |
| 3.6 | `test_merge_into_existing_unions_sessions_and_fitness` | `_merge_into_existing(conn, winner_id, loser_node, session, canon)` 把 loser source_sessions 合入 winner array, fitness running-avg 合并 | `persistence/lexicon_pg · Phase 17.1 Task 3.6: _merge_into_existing source_sessions union + fitness 合并` |

### 技术参考

**register_vector** (Task 3.1 green, module-level setup):

```python
# src/explain_engine/persistence/lexicon_pg.py

from pgvector.psycopg import register_vector_async, register_vector

async def get_async_pool() -> AsyncConnectionPool:
    global _async_pool
    if _async_pool is None:
        _async_pool = AsyncConnectionPool(
            _get_dsn(),
            ...,
            configure=lambda conn: register_vector_async(conn),  # 每 connection 注册 vector adapter
        )
        await _async_pool.open()
    return _async_pool

def get_sync_pool() -> ConnectionPool:
    global _sync_pool
    if _sync_pool is None:
        _sync_pool = ConnectionPool(
            _get_dsn(),
            ...,
            configure=lambda conn: register_vector(conn),
        )
        _sync_pool.open()
    return _sync_pool
```

**`_find_duplicate`** (Task 3.2 green):

```python
async def _find_duplicate(
    conn, embedding: list[float] | np.ndarray, threshold: float = 0.85,
) -> str | None:
    """pgvector cosine dedup query. 返 winner global_id 或 None."""
    row = await conn.execute(
        """SELECT global_id, 1 - (embedding <=> %s::vector) AS sim
           FROM variables
           WHERE embedding IS NOT NULL
           AND 1 - (embedding <=> %s::vector) > %s
           ORDER BY embedding <=> %s::vector
           LIMIT 1""",
        (embedding, embedding, threshold, embedding),
    ).fetchone()
    return row[0] if row else None
```

**`_merge_into_existing`** (Task 3.6 green):

```python
async def _merge_into_existing(
    conn, winner_id: str, loser_node, session, loser_canonical: str,
) -> None:
    """合 loser 入 winner: 加 source_sessions + running-avg fitness."""
    # 现有 winner 数据
    win = await conn.execute(
        """SELECT reuse_count, avg_essentialness, avg_consistency,
                  source_sessions, last_seen_at
           FROM variables WHERE global_id = %s FOR UPDATE""",
        (winner_id,),
    ).fetchone()
    new_count = win[0] + 1
    new_ess = (win[1] * win[0] + loser_node.activation) / new_count
    new_cons = win[2]  # loser 没 consistency 数据, 保 winner
    sessions = list(win[3]) + [session.meta.session_id]
    # union, dedup
    sessions = list(dict.fromkeys(sessions))
    await conn.execute(
        """UPDATE variables SET
             reuse_count = %s,
             avg_essentialness = %s,
             source_sessions = %s,
             last_seen_at = NOW()
           WHERE global_id = %s""",
        (new_count, new_ess, sessions, winner_id),
    )
```

### Verify

```bash
.venv/bin/python -m pytest tests/test_lexicon_pg_vector.py -v
.venv/bin/ruff check src/explain_engine/persistence/lexicon_pg.py tests/test_lexicon_pg_vector.py
```

---

## Wave 4: Public API 兼容层

**目标**: 重写 `flush_to_lexicon` / `get_lexicon_top_k_for_compress` / `get_top_n_vars` / `_render_lexicon_for_prompt` 用 PG, 保持现有 caller signature.

**依赖**: Wave 3

**文件**:
- Modify: `src/explain_engine/persistence/lexicon_pg.py`
- Create: `tests/test_lexicon_pg_api.py`

### Task 表

| Task | Red Test | 关键实现 | Commit Message |
|---|---|---|---|
| 4.1 | (无 test, 复用) | 从旧 `lexicon.py` copy `_should_promote(node)` 到 `lexicon_pg.py` (3 行) | `persistence/lexicon_pg · Phase 17.1 Task 4.1: _should_promote 移植` |
| 4.2 | `test_build_canonical_mechanism_no_llm` | `_build_canonical_mechanism(node, session, llm=None)` 走 edge fallback (复用旧逻辑) | `persistence/lexicon_pg · Phase 17.1 Task 4.2: _build_canonical_mechanism (无 cache, edge fallback)` |
| 4.3 | `test_batch_embed` | `_batch_embed(canonicals)` 调 BGE-M3, fallback `[None]*N` on disable | `persistence/lexicon_pg · Phase 17.1 Task 4.3: _batch_embed (BGE-M3 + disable fallback)` |
| 4.4 | `test_flush_to_lexicon_new_var_inserts` + `test_flush_to_lexicon_dup_merges` | `flush_to_lexicon(session, storage, llm, llm_canonical_top_k=3)` 主流程: candidates → canonicals → embeddings → for each: dedup query → insert/merge → return promoted count | `persistence/lexicon_pg · Phase 17.1 Task 4.4: flush_to_lexicon 主流程 (cache + lazy_dedup 留 Wave 5/6)` |
| 4.5 | `test_get_lexicon_top_k_for_compress` | `def get_lexicon_top_k_for_compress(storage, k=20) -> list[dict]` 用 sync_pool SELECT top-k by composite score | `persistence/lexicon_pg · Phase 17.1 Task 4.5: get_lexicon_top_k_for_compress (sync)` |
| 4.6 | `test_get_top_n_vars_returns_variable_nodes` | `def get_top_n_vars(storage, n) -> list[VariableNode]` 用 sync_pool SELECT + 转 VariableNode dataclass | `persistence/lexicon_pg · Phase 17.1 Task 4.6: get_top_n_vars` |
| 4.7 | `test_render_lexicon_for_prompt` | `_render_lexicon_for_prompt(vars_list) -> str` 拼 markdown 列表 (复用旧 format) | `persistence/lexicon_pg · Phase 17.1 Task 4.7: _render_lexicon_for_prompt` |

### 技术参考

`flush_to_lexicon` (Task 4.4 green, 主流程):

```python
async def flush_to_lexicon(
    session, storage,
    llm=None,
    llm_canonical_top_k: int = 3,
) -> int:
    """Phase 17.1: PG impl 替老 JSON. signature 不变."""
    pool = await get_async_pool()
    candidates = [n for n in session.state.graph.nodes.values() if _should_promote(n)]
    candidates.sort(key=lambda n: -n.activation)

    # Build canonicals (Wave 5 加 cache)
    canonicals: list[str] = []
    for i, node in enumerate(candidates):
        effective_llm = llm if i < llm_canonical_top_k else None
        canon = await _build_canonical_mechanism(node, session, effective_llm)
        canonicals.append(canon)

    # Batch embed
    embeddings = await _batch_embed(canonicals)

    # Upsert + dedup
    promoted = 0
    async with pool.connection() as conn:
        async with conn.transaction():
            for node, canon, emb in zip(candidates, canonicals, embeddings, strict=True):
                winner_id = None
                if emb is not None:
                    winner_id = await _find_duplicate(conn, emb, threshold=0.85)
                if winner_id:
                    await _merge_into_existing(conn, winner_id, node, session, canon)
                    continue
                await _insert_new_var(conn, node, canon, emb, session)
                promoted += 1

    # Wave 6 加: await _maybe_lazy_dedup(pool)
    return promoted
```

### Verify

```bash
.venv/bin/python -m pytest tests/test_lexicon_pg_api.py -v
.venv/bin/ruff check src/explain_engine/persistence/lexicon_pg.py tests/test_lexicon_pg_api.py
```

---

## Wave 5: Track B — Canonical Mechanism Cache

**目标**: signature 计算 + cache hit/miss path + `CANONICAL_MODEL_VERSION` bump 机制.

**依赖**: Wave 4

**文件**:
- Modify: `src/explain_engine/persistence/lexicon_pg.py`
- Create: `tests/test_lexicon_pg_cache.py`

### Task 表

| Task | Red Test | 关键实现 | Commit Message |
|---|---|---|---|
| 5.1 | `test_compute_canonical_signature_stable` + `test_signature_changes_on_edge_topology` | `compute_canonical_signature(node, edges) -> str` sha256[:16] of (name + desc + level + epi + sorted edges). 2 个 test: 相同输入 same hash; 改 1 个 edge target hash 变 | `persistence/lexicon_pg · Phase 17.1 Task 5.1: compute_canonical_signature` |
| 5.2 | `test_get_node_edges_returns_relevant` | `_get_node_edges(node, session)` 取 session.state.graph.edges 中 source/target == node.id 的 list | `persistence/lexicon_pg · Phase 17.1 Task 5.2: _get_node_edges helper` |
| 5.3 | `test_build_canonical_mechanism_cached_lookup_first` | `_build_canonical_mechanism_cached(node, session, llm, pool)` 先 query cache (signature + model_ver match), miss 才 LLM | `persistence/lexicon_pg · Phase 17.1 Task 5.3: cache-lookup-first wrapper` |
| 5.4 | `test_cache_hit_skips_llm` | mock LLM 抛 RuntimeError, signature 命中时 cache 直接返已存 canonical, **不应** 调 LLM | `persistence/lexicon_pg · Phase 17.1 Task 5.4: cache hit 跳 LLM 验证` |
| 5.5 | `test_cache_miss_calls_llm_and_stores` | 第 1 次 (cache 空) 调 LLM, 返 canonical 后下次 query 命中 | `persistence/lexicon_pg · Phase 17.1 Task 5.5: cache miss → LLM → 后续 hit` |
| 5.6 | `test_model_ver_bump_invalidates_cache` | bump `CANONICAL_MODEL_VERSION = "v2"`, 旧 v1 cache 不命中, 触发 LLM 重 build | `persistence/lexicon_pg · Phase 17.1 Task 5.6: CANONICAL_MODEL_VERSION bump invalidate` |

**注意 Task 5.6 实现**: `CANONICAL_MODEL_VERSION` 是 module-level 常量, test 用 `monkeypatch.setattr` 改值, 不需重启 process.

### 技术参考

```python
import hashlib

CANONICAL_MODEL_VERSION = "v1"   # 改 LLM prompt 时手 bump 'v2', invalidate 全 cache

def compute_canonical_signature(node, edges) -> str:
    edge_keys = sorted(
        f"{e.source_node}→{e.relation_type}→{e.target_node}"
        for e in edges if e.source_node == node.id or e.target_node == node.id
    )
    payload = "\n".join([
        f"name={node.name}",
        f"desc={node.description}",
        f"level={node.abstraction_level}",
        f"epi={node.epistemic}",
        f"edges={'|'.join(edge_keys)}",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _get_node_edges(node, session) -> list:
    return [
        e for e in session.state.graph.edges.values()
        if e.source_node == node.id or e.target_node == node.id
    ]


async def _build_canonical_mechanism_cached(node, session, llm, pool) -> str:
    edges = _get_node_edges(node, session)
    signature = compute_canonical_signature(node, edges)
    async with pool.connection() as conn:
        row = await conn.execute(
            """SELECT canonical_mechanism FROM variables
               WHERE canonical_signature = %s AND canonical_model_ver = %s
               LIMIT 1""",
            (signature, CANONICAL_MODEL_VERSION),
        ).fetchone()
    if row:
        return row[0]
    return await _build_canonical_mechanism(node, session, llm)
```

Wave 4 Task 4.4 `flush_to_lexicon` 内调 `_build_canonical_mechanism` 改成 `_build_canonical_mechanism_cached(node, session, llm, pool)`.

### Verify

```bash
.venv/bin/python -m pytest tests/test_lexicon_pg_cache.py -v
.venv/bin/ruff check src/explain_engine/persistence/lexicon_pg.py
```

---

## Wave 6: Track C — Lazy Retroactive Dedup

**目标**: lexicon_meta 表交互 + 阈值判定 (N>100, flush>5) + pgvector cross-join dedup query + merge audit 入表.

**依赖**: Wave 3 (vector ops)

**文件**:
- Modify: `src/explain_engine/persistence/lexicon_pg.py`
- Create: `tests/test_lexicon_pg_dedup.py`

### Task 表

| Task | Red Test | 关键实现 | Commit Message |
|---|---|---|---|
| 6.1 | `test_lexicon_meta_table_exists_and_seeded` | (无新 impl, 验 init.sql 跑后 `SELECT * FROM lexicon_meta` 有 1 row id=1) | `persistence/lexicon_pg · Phase 17.1 Task 6.1: lexicon_meta 表初始化验证` |
| 6.2 | `test_maybe_lazy_dedup_skips_below_100` | N=50 时 `_maybe_lazy_dedup` 返 0, `flush_count_since` 未变 | `persistence/lexicon_pg · Phase 17.1 Task 6.2: _maybe_lazy_dedup N<100 skip` |
| 6.3 | `test_maybe_lazy_dedup_increments_count_then_runs` | N=150 时连调 5 次 `flush_count_since` 累到 5, 第 5 次跑 dedup 后重置 0 | `persistence/lexicon_pg · Phase 17.1 Task 6.3: flush_count 阈值 5 触发` |
| 6.4 | `test_retroactive_dedup_pg_merges_sim_above_threshold` | 造 N=110 var (其中 2 个 sim > 0.85) + 强 flush_count=5 触发, dedup 后 N=109 (merge 1 pair) | `persistence/lexicon_pg · Phase 17.1 Task 6.4: _retroactive_dedup_pg cross-join + 合并` |
| 6.5 | `test_retroactive_dedup_writes_audit_record` | 跑完后 lexicon_merge_audit 表 1 row 含 merged_into/merged_from/sim | `persistence/lexicon_pg · Phase 17.1 Task 6.5: merge audit 入 PG 表` |
| 6.6 | `test_maybe_lazy_dedup_resets_count_after_run` | 跑完 dedup 后 lexicon_meta.flush_count_since = 0, last_retro_dedup_at = NOW() | `persistence/lexicon_pg · Phase 17.1 Task 6.6: 跑完重置 flush_count + 更新 last_retro_at` |

### 技术参考

```python
async def _maybe_lazy_dedup(pool: AsyncConnectionPool) -> int:
    """N > 100 + flush_count > 5 才跑. 返 merged 数."""
    async with pool.connection() as conn:
        async with conn.transaction():
            n = (await (await conn.execute(
                "SELECT COUNT(*) FROM variables"
            )).fetchone())[0]
            if n < 100:
                return 0
            row = await (await conn.execute(
                "SELECT flush_count_since FROM lexicon_meta WHERE id = 1 FOR UPDATE"
            )).fetchone()
            if row[0] < 5:
                await conn.execute(
                    "UPDATE lexicon_meta SET flush_count_since = flush_count_since + 1 "
                    "WHERE id = 1"
                )
                return 0
            merged = await _retroactive_dedup_pg(conn)
            await conn.execute(
                "UPDATE lexicon_meta SET last_retro_dedup_at = NOW(), "
                "flush_count_since = 0 WHERE id = 1"
            )
            return merged


async def _retroactive_dedup_pg(conn) -> int:
    """pgvector cross-join, sim > 0.85 合并. winner = 早 first_seen."""
    rows = await (await conn.execute(
        """SELECT a.global_id AS winner_id, b.global_id AS loser_id,
                  1 - (a.embedding <=> b.embedding) AS sim,
                  b.source_sessions AS loser_sessions,
                  b.canonical_mechanism AS loser_canon,
                  a.canonical_mechanism AS winner_canon,
                  b.reuse_count, b.avg_essentialness, b.avg_consistency
           FROM variables a, variables b
           WHERE a.global_id < b.global_id
             AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
             AND 1 - (a.embedding <=> b.embedding) > 0.85
             AND a.first_seen_at <= b.first_seen_at
           ORDER BY a.first_seen_at ASC"""
    )).fetchall()
    merged = 0
    for r in rows:
        # Audit
        await conn.execute(
            """INSERT INTO lexicon_merge_audit
               (merged_into, merged_into_canon, merged_from_canon, sim, evidence_session_ids)
               VALUES (%s, %s, %s, %s, %s)""",
            (r[0], r[5][:80], r[4][:80], r[2], r[3]),
        )
        # 合 sessions + fitness 入 winner
        # ... (跟 _merge_into_existing 同模) ...
        # 删 loser
        await conn.execute(
            "DELETE FROM variables WHERE global_id = %s", (r[1],)
        )
        merged += 1
    return merged
```

Wave 4 Task 4.4 `flush_to_lexicon` 末加 `await _maybe_lazy_dedup(pool)`.

### Verify

```bash
.venv/bin/python -m pytest tests/test_lexicon_pg_dedup.py -v
```

---

## Wave 7: Migration — variables.json → PG

**目标**: cli subcommand `explain migrate-lexicon-pg`, dry-run + 实跑 + idempotent + json backup.

**依赖**: Wave 4+5 (API ready)

**文件**:
- Create: `src/explain_engine/persistence/lexicon_migrations.py`
- Modify: `src/explain_engine/cli.py`
- Create: `tests/test_lexicon_migrations.py`

### Task 表

| Task | Red Test | 关键实现 | Commit Message |
|---|---|---|---|
| 7.1 | `test_migrate_json_to_pg_basic` | `migrate_json_to_pg(storage, dry_run=False)` 读 storage.knowledge_dir()/variables.json + INSERT 全部 var, 返 {"migrated": N, "skipped": M} | `persistence/lexicon_migrations · Phase 17.1 Task 7.1: migrate_json_to_pg 基础流程` |
| 7.2 | `test_migrate_idempotent_on_conflict_skips` | 跑 2 次, 第 2 次 returned migrated=0 / skipped=N | `persistence/lexicon_migrations · Phase 17.1 Task 7.2: ON CONFLICT DO NOTHING idempotent` |
| 7.3 | `test_migrate_legacy_signature_no_edges` | legacy var 没 graph 上下文, signature 用 `_compute_signature_for_legacy(var)` (name+desc+level+epi 无 edges) | `persistence/lexicon_migrations · Phase 17.1 Task 7.3: legacy signature 算法 (no edges)` |
| 7.4 | `test_migrate_dry_run_writes_nothing` | dry_run=True 时返 {"would_migrate": N}, DB 不写 | `persistence/lexicon_migrations · Phase 17.1 Task 7.4: --dry-run mode` |
| 7.5 | `test_migrate_renames_json_to_migrated_on_success` | 成功后 variables.json → variables.json.migrated; 失败时 json 原位 | `persistence/lexicon_migrations · Phase 17.1 Task 7.5: 成功 rename .migrated 备份` |
| 7.6 | `test_cli_migrate_lexicon_pg_smoke` (typer test) | cli.py 加 `@app.command("migrate-lexicon-pg") def migrate_lexicon_pg_cmd(dry_run: bool = False)` | `cli · Phase 17.1 Task 7.6: explain migrate-lexicon-pg subcommand` |

### 技术参考

```python
# src/explain_engine/persistence/lexicon_migrations.py
import json
from pathlib import Path
from typing import Any

from explain_engine.persistence.lexicon_pg import get_async_pool


async def migrate_json_to_pg(storage, dry_run: bool = False) -> dict[str, Any]:
    json_path = storage.knowledge_dir() / "variables.json"
    if not json_path.exists():
        return {"migrated": 0, "reason": "无 variables.json"}
    lexicon = json.loads(json_path.read_text(encoding="utf-8"))
    vars_list = lexicon.get("variables", [])
    if dry_run:
        return {"would_migrate": len(vars_list), "dry_run": True}
    pool = await get_async_pool()
    inserted = skipped = 0
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
                        var["global_id"], var["name"], var["description"],
                        var["abstraction_level"], var["epistemic"],
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
        json_path.rename(json_path.with_suffix(".json.migrated"))
    return {"migrated": inserted, "skipped": skipped}


def _compute_signature_for_legacy(var: dict) -> str:
    import hashlib
    payload = "\n".join([
        f"name={var['name']}",
        f"desc={var['description']}",
        f"level={var['abstraction_level']}",
        f"epi={var['epistemic']}",
        "edges=",  # legacy 无 graph context
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
```

### Verify

```bash
.venv/bin/python -m pytest tests/test_lexicon_migrations.py -v
.venv/bin/python -m explain_engine.cli migrate-lexicon-pg --help    # typer subcommand 验证
```

---

## Wave 8: Track D — /compress 重入 (跟 1-7 并行)

**目标**: `_handle_compress` 装饰器 allowed 加 done/converged, 让用户 /predict 后能再 compress.

**依赖**: 独立 (不需 PG, 仅改 chat slash)

**文件**:
- Modify: `src/explain_engine/chat/slash_commands.py` (1 行)
- Modify: `tests/test_chat_slash_commands.py` (+test)

### Task 表

| Task | Red Test | 关键实现 | Commit Message |
|---|---|---|---|
| 8.1 | `test_compress_allowed_at_done` (新加 TestCompressReentry class) | `_handle_compress` @with_stage_gate allowed 改成 `["bootstrap_pending", "insight_pending", "done", "converged"]`. test 验 stage=done 时 /compress 不被 reject | `chat/slash · Phase 17.1 Task 8.1: _handle_compress allowed +done/converged` |
| 8.2 | `test_compress_allowed_at_converged_stage_falls_back_to_done` | stage=converged 时 /compress 跑后 stage = done (success_stage 不变) | `chat/slash · Phase 17.1 Task 8.2: converged → done 回退验证` |
| 8.3 | `test_compress_still_works_at_bootstrap_pending` | 既有行为 bootstrap_pending → done 不挂 (regression) | `chat/slash · Phase 17.1 Task 8.3: 既有 bootstrap_pending 路径 regression` |
| 8.4 | `test_compress_still_works_at_insight_pending` | 既有行为 insight_pending → done 不挂 (regression) | `chat/slash · Phase 17.1 Task 8.4: 既有 insight_pending 路径 regression` |

### 实现 (Task 8.1)

```python
# src/explain_engine/chat/slash_commands.py:889

# 当前:
@with_stage_gate(
    allowed=["bootstrap_pending", "insight_pending"],
    ...
)

# 改:
@with_stage_gate(
    allowed=["bootstrap_pending", "insight_pending", "done", "converged"],   # +2
    ...
)
```

### Verify

```bash
.venv/bin/python -m pytest "tests/test_chat_slash_commands.py::TestCompressReentry" -v
.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestSlashCompress -v   # 既有不挂
```

---

## Wave 9: 删旧 lexicon.py + 切 imports

**目标**: 删 `engines/lexicon.py` + `engines/lexicon_merge.py` + 改全部 caller 用 `persistence.lexicon_pg`.

**依赖**: Wave 7 + 8 全完

**文件**:
- Delete: `src/explain_engine/engines/lexicon.py` (703 LOC)
- Delete: `src/explain_engine/engines/lexicon_merge.py`
- Delete: `tests/test_lexicon.py`
- Delete: `tests/test_lexicon_merge.py`
- Modify: 各 caller (chat/slash_commands.py / cli.py / engines/theory/loader.py / engines/bootstrap.py)

### Task 表

| Task | 操作 | Commit Message |
|---|---|---|
| 9.1 | grep 全 imports: `grep -rn "from explain_engine.engines.lexicon" src/ tests/`. 改全部 imports 为 `from explain_engine.persistence.lexicon_pg import ...`. 跑全量 test 不挂 | `chat/slash + cli + theory · Phase 17.1 Task 9.1: 切 imports 到 lexicon_pg` |
| 9.2 | `git rm src/explain_engine/engines/lexicon.py src/explain_engine/engines/lexicon_merge.py tests/test_lexicon.py tests/test_lexicon_merge.py`. 跑全量 test 不挂 | `engines · Phase 17.1 Task 9.2: 删旧 lexicon.py + lexicon_merge.py (703+ LOC dead code)` |
| 9.3 | grep 漏 imports: `grep -rn "engines\.lexicon" src/ tests/` 应空. ruff check 全绿 | `engines · Phase 17.1 Task 9.3: dead import 残留扫描 + ruff 全绿` |

### Verify

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
grep -rn "engines\.lexicon" src/ tests/   # 应空输出
```

---

## Wave 10: E2E Smoke + Acceptance Doc

**目标**: 端到端集成 test (真 testcontainers PG 走完整 compress → predict → recompress 流程) + 写 acceptance doc.

**依赖**: 全前置

**文件**:
- Create: `tests/test_lexicon_pg_e2e.py`
- Modify: `.env.example` (加 EXPLAIN_DB_URL 示例)
- Modify: `README.md` (Phase 17.1 部署一节)
- Create: `docs/plans/2026-05-25-lexicon-pg-perf-acceptance.md`

### Task 表

| Task | Red Test / 操作 | Commit Message |
|---|---|---|
| 10.1 | `test_e2e_compress_promotes_to_pg` + `test_e2e_dedup_via_pgvector` (tests/test_lexicon_pg_e2e.py): 完整 flush_to_lexicon 跑通; 2 个 sim > 0.85 var dedup 合 | `tests · Phase 17.1 Task 10.1: e2e flush + dedup smoke (testcontainers)` |
| 10.2 | `test_e2e_compress_reentry_after_predict`: 跑 /compress → /predict (mock 加 L0) → /compress (验 stage gate 不挂 + 新 var 入 PG) | `tests · Phase 17.1 Task 10.2: e2e /compress 重入 流程` |
| 10.3 | `.env.example` 加 `EXPLAIN_DB_URL=postgresql://...` 示例; `README.md` 加 "Phase 17.1 PG 部署" 一节 (链 deploy/postgres/README.md) | `docs · Phase 17.1 Task 10.3: .env.example + README 部署引导` |
| 10.4 | 写 `docs/plans/2026-05-25-lexicon-pg-perf-acceptance.md`: 跟 Phase 16.2 acceptance 同模 (commit hash list + 全量 verify + manual smoke 步骤 + 风险 retro). 全量 pytest + ruff 截图 (text) 嵌入 | `docs/plans · Phase 17.1 Task 10.4: acceptance doc + 全量 verify` |

---

## Acceptance Verify 命令

```bash
# 1. 全量 pytest (1135 + Phase 17.1 新, 估 +~50 = 1185)
.venv/bin/python -m pytest tests/ --tb=short -q

# 2. ruff
.venv/bin/ruff check src/ tests/

# 3. 手 smoke (用户跑, 需 server 在线)
.venv/bin/python -m explain_engine.cli migrate-lexicon-pg --dry-run    # 预览
.venv/bin/python -m explain_engine.cli migrate-lexicon-pg              # 实跑
.venv/bin/python -m explain_engine.cli                                 # 进 chat REPL, 跑 /compress + /predict + /compress (重入)
.venv/bin/python -m explain_engine.cli lexicon list                    # 验 var 在 PG

# 4. PG 直查验证 (在 172.30.26.12)
ssh user@172.30.26.12 'docker exec explain-postgres psql -U explain -c "SELECT COUNT(*) FROM variables"'

# 5. commit hash list
git log --oneline --grep "Phase 17.1"
```

---

## 风险点 + 应对 (从 design §11 抽)

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| testcontainers 装不上 (Docker missing 在 dev 机器) | 中 | dev test 跑不了 | conftest pg_container fixture 用 `pytest.importorskip("testcontainers.postgres")`; 缺失时 lexicon_pg test skip + warn |
| pgvector image 拉不下 (公网慢) | 低 | testcontainers fail | 预先 `docker pull pgvector/pgvector:pg16` |
| HNSW 跟 numpy cosine 数学差异 | 低 | dedup 漏 | Wave 3 Task 3.4 显式验证数学等价 |
| 31 var migrate 部分失败 | 中 | partial migrate | transaction 包 + ON CONFLICT, all-or-nothing |
| 删 lexicon.py 后 dead caller | 低 | import error | Wave 9 Task 9.1 grep 全 |
| chat 连不上 PG 用户 panic | 中 | frustration | `verify_connection` 错信息含明确 Hint |
| canonical cache model_ver 升级流程 | 低 | 用户不知何时 bump | acceptance doc §3 写明 |
| 远程 PG latency (内网) | 中 | 每 query +ms | 内网 sub-ms; WAN 不推荐 |
| Migration 期 chat 同跑 race | 低 | partial state | docs §部署 提示 migrate 时关 chat |
| pgvector dedup 慢 (N > 100k) | 低 | query > 100ms | Phase 17.2 调 IVF/HNSW ef_search |
| Pool 满 timeout | 低 | single fail | 调 EXPLAIN_DB_POOL_MAX |
| Wave 1 fixture 让既有 1135 test 启动慢 (testcontainers spin) | 中 | dev iteration 慢 | reset_pg fixture 仅 lexicon_pg test 用 (不 autouse to all), 加 `@pytest.mark.usefixtures("reset_pg")` 显式标 |

---

## 进入执行 Skill 选项

Plan 写完, 70 行/Wave × 10 Wave + 头 + 尾 ≈ 1300 行. 55 task / ~12 commit / 13 hr 估时 (subagent batch 实跑 4-6 hr).

下一步 2 选 1:

1. **Subagent-Driven (本 session)**: 派 fresh subagent per task / per Wave, 主 session inline review. 跟 Phase 16.2 同模, 快速迭代.
2. **Parallel Session**: 开新 session 跑 `superpowers:executing-plans`, batch + checkpoint.

按 user prompt 当前不进 subagent-driven-development. 由 user 下次决定执行方式 (推荐策略: Wave 0 部署 → 主 session 做 Wave 1 (新依赖 + fixture, 风险中) → subagent batch Wave 2-7 → 主 session 做 Wave 8 (1 行改 + 4 test) → subagent Wave 9-10).
