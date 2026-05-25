# Phase 17.1: Lexicon Storage 重构 + Performance 优化 + /compress 重入

**Date**: 2026-05-25
**Status**: brainstorm 完, 待 implementation plan
**Branch**: dev
**Related**:
- Phase 10 (Persistent World Model — variables.json lexicon 引入)
- Phase 13 (Variable Embedding — BGE-M3 cosine merge)
- Phase 16 (Theory Formation — 用 lexicon 作 cross-session graph)
- Phase 16.2 (REPL History Persistence — 上 phase, dev branch 70 commit)

---

## 1. Motivation

跑了 9 session / 31 个 cross-session var 后, 用户感知到痛点:

| 痛点 | 数据 |
|---|---|
| `variables.json` 文件膨胀快 | 900K, 32369 行 JSON, 31 个 var |
| 94% 体积是 embedding | 1024 维 float32 JSON 字面化, 单 vec ~21KB |
| `/compress` + `flush_to_lexicon` 总耗时 30-60s | LLM 调用 + numpy N² dedup + 全文件 load/write |
| `/compress` stage gate 限 1 次/session | 后续 `/predict` 加新 L0 永远停在 L0, 无法归纳 — **跟 "持续学习" 哲学矛盾** |

用户允许第三方库 + 向量数据库 + 关系型 + nosql.

**Phase 17.1 目标**: 把 lexicon 从 local JSON 文件迁到远程 PostgreSQL + pgvector 单库, 同时修 /compress 重入, 加 canonical cache, 推迟 retroactive dedup.

## 2. Goals

1. **Storage 迁移**: `variables.json` → 远程 PostgreSQL 16 + pgvector extension (docker 部署在 172.30.26.12)
   - Load 900K JSON parse 100ms → SQL query 5ms (20x)
   - 增量 upsert 替全文件覆写 (避免 lock + IO 浪费)
   - HNSW index 让 N=10k 场景 cosine query 仍 ms 级
2. **Canonical Mechanism Cache** (Track B): SQLite KV 缓存 `(node_signature, mechanism_signature) → canonical_mechanism`, 重复 var 跳 LLM call
   - LLM 调用数 -50~80% (取决于 var 重复率)
3. **Lazy Retroactive Dedup** (Track C): 推迟到 N > 100 + 5 次 flush 一跑, 小规模不触发 N² scan
4. **/compress 重入** (Track D): stage gate allowed 加 done/converged, 用户可在 `/predict` 加新 L0 后再 compress
   - 不引新 slash 命令, 仅改装饰器 1 行
5. **零 dead code**: 删 `engines/lexicon.py` + `engines/lexicon_merge.py`, 完整切换不留 dual-mode

## 3. Non-Goals

- **Chromadb / Qdrant / Weaviate** 独立 vector DB — pgvector 单库够用, 减依赖
- **SQLAlchemy ORM / Django ORM** — 直接 SQL, 31 var 规模下 ORM 开销 > 收益
- **DuckDB analytics** — 当前 N 小, 不需要 OLAP, Phase 17.2+ 再考虑
- **Read-only fallback (chat 断网仍能跑)** — fail-fast 简单且符合用户内网部署语境
- **Local cache + sync 模式** — CRDT 同步复杂度过高, YAGNI
- **Pool 自动重连无限 retry** — 1 次重连后抛错, 不 mask bug
- **SSL/TLS** — 内网 172.x, 用户后续可加 `?sslmode=require`
- **Multi-project lexicon 隔离** — 仍按 project_id 分 (sessions 同, lexicon 共享 schema 但 source_sessions 区分)
- **Session-local 文件 (graph / transcript / chat_state / repl_history) 也搬 PG** — 保持本地, 不在本 phase 范围
- **Theory cache (theories.json) 搬 PG** — 仍本地, 它是 lexicon 派生品

## 4. Architecture

### 4.1 部署架构

```
┌─────────────────────────────┐         ┌─────────────────────────────────┐
│ 本机 (开发机)               │         │ 172.30.26.12 (内网服务器)        │
│                             │         │                                  │
│  uv run explain (chat REPL) │ TCP 5432│  docker-compose:                 │
│   ↓                         │ ◄──────►│   - pgvector/pgvector:pg16       │
│   psycopg3 client (async)   │         │   - volume: ./pgdata:/var/lib    │
│   ↓                         │         │   - env: POSTGRES_DB=explain     │
│   pool size 2-10            │         │   - port 5432:5432               │
│                             │         │                                  │
│  本地保留 (per session):    │         │                                  │
│   - graph.json              │         │                                  │
│   - transcript.jsonl        │         │                                  │
│   - repl_history.jsonl      │         │                                  │
│   - chat_state.json         │         │                                  │
│  (lexicon 全部 PG, 无本地)  │         │                                  │
└─────────────────────────────┘         └─────────────────────────────────┘
```

**关键**:
- Lexicon (variables 表) 全在 PG, 本机不留 copy
- Chat REPL 启动 → `verify_connection()` SELECT 1, 失败 fail-fast Exit(1)
- Session-local 文件保留 (Phase 16.2 完成的 path 不动)
- 配置: 单环境变量 `EXPLAIN_DB_URL=postgresql://user:pass@172.30.26.12:5432/explain`

### 4.2 客户端 Module 边界

```
新增 (Phase 17.1):
  src/explain_engine/persistence/lexicon_pg.py            # ~600 LOC, 单文件
  src/explain_engine/persistence/lexicon_migrations.py    # ~100 LOC, migration 脚本

修改:
  src/explain_engine/cli.py                               # +migrate-lexicon-pg subcommand
  src/explain_engine/chat/slash_commands.py               # _handle_compress @with_stage_gate +done/converged
  .env.example                                            # +EXPLAIN_DB_URL 示例
  README.md                                               # +"Phase 17.1 PG 部署" 一节

删除 (Wave 9 收尾):
  src/explain_engine/engines/lexicon.py                   # 703 LOC, 老 JSON impl
  src/explain_engine/engines/lexicon_merge.py             # audit log 改入 PG 表
  tests/test_lexicon.py
  tests/test_lexicon_merge.py
```

### 4.3 Fallback 策略

| 场景 | 行为 |
|---|---|
| 启动连不上 PG | fail-fast Exit(1) + 友好 Hint (docker compose ps / EXPLAIN_DB_URL) |
| Query 中 connection drop | psycopg pool 自动重连 1 次, 失败抛 `LexiconDBError` |
| Migration 中途失败 | transaction rollback, json 仍在原位, 可 retry |
| pgvector index 缺 | INIT SQL 自动跑, 不应发生 |

---

## 5. PostgreSQL Schema

### 5.1 主表 `variables`

```sql
CREATE TABLE variables (
    global_id           TEXT PRIMARY KEY,                    -- 'v_<8hex>'
    name                TEXT NOT NULL,
    description         TEXT NOT NULL,
    abstraction_level   SMALLINT NOT NULL CHECK (abstraction_level IN (1, 2)),
    epistemic           TEXT NOT NULL,

    -- canonical mechanism + cache (Track B)
    canonical_mechanism TEXT NOT NULL,
    canonical_signature TEXT NOT NULL,                       -- sha256[:16], cache hit key
    canonical_model_ver TEXT NOT NULL DEFAULT 'v1',          -- bump 此值 invalidate 全部 cache

    -- fitness (拍平 5 字段)
    reuse_count         INT NOT NULL DEFAULT 1,
    avg_essentialness   REAL NOT NULL,
    avg_consistency     REAL NOT NULL,
    first_seen_at       TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,

    -- source_sessions: PG 数组列 (GIN 索引可反查 var-of-session)
    source_sessions     TEXT[] NOT NULL DEFAULT '{}',

    -- pgvector embedding (BGE-M3 1024 维, NULL = legacy entry 没 embedding)
    embedding           vector(1024),

    -- audit
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 5.2 索引

```sql
-- 1. HNSW vector index — dedup query (cosine similarity O(log N))
CREATE INDEX idx_variables_embedding
    ON variables
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 2. source_sessions 反查 (GIN array index)
CREATE INDEX idx_variables_source_sessions
    ON variables
    USING gin (source_sessions);

-- 3. canonical cache lookup
CREATE INDEX idx_variables_canonical_signature
    ON variables (canonical_signature);

-- 4. name 搜索 (Phase 17+ 可能 /search lexicon)
CREATE INDEX idx_variables_name
    ON variables (name);
```

### 5.3 `updated_at` Trigger

```sql
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER variables_set_updated_at
    BEFORE UPDATE ON variables
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
```

### 5.4 Merge Audit 表

```sql
CREATE TABLE lexicon_merge_audit (
    id                   BIGSERIAL PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    merged_into          TEXT NOT NULL,
    merged_into_canon    TEXT NOT NULL,                      -- 前 80 字 sample
    merged_from_canon    TEXT NOT NULL,
    sim                  REAL NOT NULL,
    evidence_session_ids TEXT[] NOT NULL
);

CREATE INDEX idx_merge_audit_ts ON lexicon_merge_audit (ts DESC);
CREATE INDEX idx_merge_audit_merged_into ON lexicon_merge_audit (merged_into);
```

### 5.5 Lexicon Meta 表 (Track C 用)

```sql
CREATE TABLE lexicon_meta (
    id                  SMALLINT PRIMARY KEY CHECK (id = 1),   -- 单 row
    last_retro_dedup_at TIMESTAMPTZ,
    flush_count_since   INT NOT NULL DEFAULT 0,
    canonical_model_ver TEXT NOT NULL DEFAULT 'v1'
);
INSERT INTO lexicon_meta (id) VALUES (1) ON CONFLICT DO NOTHING;
```

### 5.6 Canonical Signature 算法

```python
import hashlib

CANONICAL_MODEL_VERSION = "v1"   # 改 LLM prompt 时 bump 'v2', invalidate 全 cache

def compute_canonical_signature(node: VariableNode, edges: list[RelationEdge]) -> str:
    """稳定哈希 — name + desc + level + epi + sorted edge keys."""
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
```

---

## 6. Client Integration (psycopg3)

### 6.1 Connection Pool

```python
# src/explain_engine/persistence/lexicon_pg.py
import os
from psycopg_pool import AsyncConnectionPool, ConnectionPool

DEFAULT_DSN = "postgresql://explain:changeme@127.0.0.1:5432/explain"

_async_pool: AsyncConnectionPool | None = None
_sync_pool: ConnectionPool | None = None

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
    """cli typer subcommand 路径用 (避免强转 asyncio.run)."""
    global _sync_pool
    if _sync_pool is None:
        _sync_pool = ConnectionPool(_get_dsn(), min_size=1, max_size=5, open=False)
        _sync_pool.open()
    return _sync_pool


class LexiconDBError(Exception):
    """统一 DB 错误类型."""


async def verify_connection() -> None:
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

### 6.2 环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `EXPLAIN_DB_URL` | `postgresql://explain:changeme@127.0.0.1:5432/explain` | 主 DSN |
| `EXPLAIN_DB_POOL_MIN` | `2` | 池最小 connection |
| `EXPLAIN_DB_POOL_MAX` | `10` | 池最大 connection |
| `EXPLAIN_DB_CONNECT_TIMEOUT_S` | `5` | 连接超时秒数 |

### 6.3 Public API (保持现 lexicon.py signature)

所有现有 caller 不动. 内部全切 PG:

```python
async def flush_to_lexicon(
    session: Session, storage: StorageV2,
    llm: LLMClient | None = None,
    llm_canonical_top_k: int = 3,
) -> int:
    """Track A + B + C 一体: pgvector dedup + canonical cache + lazy retro."""
    pool = await get_async_pool()
    candidates = [n for n in session.state.graph.nodes.values() if _should_promote(n)]
    candidates.sort(key=lambda n: -n.activation)

    # Track B: cache-lookup canonical
    canonicals: list[str] = []
    for i, node in enumerate(candidates):
        effective_llm = llm if i < llm_canonical_top_k else None
        canon = await _build_canonical_mechanism_cached(node, session, effective_llm, pool)
        canonicals.append(canon)

    embeddings = await _batch_embed(canonicals)

    # Upsert with pgvector dedup
    promoted = 0
    async with pool.connection() as conn, conn.transaction():
        for node, canon, emb in zip(candidates, canonicals, embeddings, strict=True):
            if emb is not None:
                existing = await conn.execute(
                    """SELECT global_id FROM variables
                       WHERE embedding IS NOT NULL
                       AND (1 - (embedding <=> %s::vector)) > 0.85
                       ORDER BY embedding <=> %s::vector LIMIT 1""",
                    (emb, emb),
                ).fetchone()
                if existing:
                    await _merge_into_existing(conn, existing[0], node, session, canon)
                    continue
            await _insert_new(conn, node, canon, emb, session)
            promoted += 1

    # Track C: lazy retroactive dedup
    await _maybe_lazy_dedup(pool)
    return promoted
```

---

## 7. Migration + Error Handling + /compress 重入

### 7.1 Migration 路径 (4 步)

```
Step 1: 部署 server (你在 172.30.26.12)
   ssh user@172.30.26.12
   mkdir -p /opt/explain-db/{pgdata,init}
   # copy docker-compose.yml + init/01-init.sql (见 §10 附录)
   cd /opt/explain-db
   EXPLAIN_DB_PASSWORD=<密码> docker compose up -d
   # 验证: docker compose ps 显 healthy
   # 验证: docker compose exec postgres psql -U explain -c "\dx" 显 vector

Step 2: 本机配 env
   echo 'export EXPLAIN_DB_URL="postgresql://explain:<密码>@172.30.26.12:5432/explain"' >> ~/.zshrc
   source ~/.zshrc

Step 3: Dry-run migrate
   .venv/bin/python -m explain_engine.cli migrate-lexicon-pg --dry-run

Step 4: 实跑 migrate (idempotent)
   .venv/bin/python -m explain_engine.cli migrate-lexicon-pg
   # 成功: variables.json → variables.json.migrated (backup)
   # 失败: transaction rollback, json 仍在原位
```

### 7.2 Error Handling 矩阵

| 场景 | 行为 |
|---|---|
| Chat 启动连不上 PG | typer 红字 + Hint + Exit(1) |
| Query 中 connection drop | psycopg 重连 1 次, 失败抛 `LexiconDBError` |
| pgvector extension 缺 | INIT SQL 自动跑, fail-fast |
| HNSW index 缺失 | INIT SQL 自动建; verify 用 EXPLAIN ANALYZE |
| Migration 中途失败 | transaction rollback, json 原位, 可 retry |
| Canonical cache hit 但 stale | 改 LLM prompt 时手 bump `CANONICAL_MODEL_VERSION` |
| Embedding NULL (BGE-M3 失败) | `embedding=NULL` 仍 store, 下次 lazy_dedup backfill |
| Pool 满 (timeout) | 抛 PoolTimeout → `LexiconDBError`; 调高 max_size 修 |

### 7.3 Track D: /compress 重入 (改 1 行)

```python
# src/explain_engine/chat/slash_commands.py:889

# 当前:
@with_stage_gate(
    allowed=["bootstrap_pending", "insight_pending"],
    success_stage="done",
    ...
)

# Phase 17.1 改:
@with_stage_gate(
    allowed=["bootstrap_pending", "insight_pending", "done", "converged"],
    success_stage="done",
    ...
)
```

行为变化:

| 入口 stage | 出口 stage | 含义 |
|---|---|---|
| bootstrap_pending → done | done | 首次 (原行为) |
| insight_pending → done | done | review 重入 (原行为) |
| **done → done** (新) | done | /predict 加新 L0 后重新归纳 |
| **converged → done** (新) | done | /run 完后加新 L0, 重新归纳 + 之后可再 /run |

风险: 重复 propose 由 `existing_lexicon` prior + pgvector dedup 兜底.

### 7.4 Track C: Lazy Retroactive Dedup

阈值: `N > 100 且 flush_count_since > 5` 才触发. 用 `lexicon_meta` 表 `FOR UPDATE` 锁状态.

```python
async def _maybe_lazy_dedup(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn:
        n = (await conn.execute("SELECT COUNT(*) FROM variables").fetchone())[0]
        if n < 100:
            return
        last = await conn.execute(
            "SELECT flush_count_since FROM lexicon_meta WHERE id = 1 FOR UPDATE"
        ).fetchone()
        if last[0] < 5:
            await conn.execute(
                "UPDATE lexicon_meta SET flush_count_since = flush_count_since + 1 WHERE id = 1"
            )
            return
        await _retroactive_dedup_pg(conn)
        await conn.execute(
            "UPDATE lexicon_meta SET last_retro_dedup_at = NOW(), "
            "flush_count_since = 0 WHERE id = 1"
        )
```

---

## 8. Testing 策略

### 8.1 Test 文件分布

```
tests/conftest.py                       # +pg_container session fixture + reset_pg autouse
tests/test_lexicon_pg_pool.py           # Wave 1+2 (pool / CRUD)
tests/test_lexicon_pg_vector.py         # Wave 3 (pgvector + cosine dedup)
tests/test_lexicon_pg_api.py            # Wave 4 (public API)
tests/test_lexicon_pg_cache.py          # Wave 5 (canonical cache)
tests/test_lexicon_pg_dedup.py          # Wave 6 (lazy retro dedup)
tests/test_lexicon_migrations.py        # Wave 7 (json → pg migration)
tests/test_chat_slash_commands.py       # Wave 8 (+compress 重入)
tests/test_lexicon_pg_e2e.py            # Wave 10 (端到端)

# 删除
tests/test_lexicon.py                   # Wave 9
tests/test_lexicon_merge.py             # Wave 9
```

### 8.2 TDD Wave 拆分

| Wave | 目标 | Task | 估时 | 依赖 |
|---|---|---|---|---|
| 1 | testcontainers[postgres] dep + conftest pg_container + reset_pg + DDL 常量 module | 5 | 1.5 hr | 无 |
| 2 | lexicon_pg.py core: pool / verify_connection / insert / find / update | 8 | 2 hr | 1 |
| 3 | pgvector embedding store + HNSW index + cosine dedup query | 6 | 1.5 hr | 2 |
| 4 | Public API 兼容层 (flush_to_lexicon / get_lexicon_top_k_for_compress / get_top_n_vars) | 7 | 2 hr | 3 |
| 5 | Track B: canonical signature + cache hit/miss + CANONICAL_MODEL_VERSION bump | 6 | 1.5 hr | 4 |
| 6 | Track C: lexicon_meta + _maybe_lazy_dedup + _retroactive_dedup_pg | 6 | 1.5 hr | 3 |
| 7 | Migration: migrate_json_to_pg + dry-run + idempotent + json backup + cli subcommand | 6 | 1.5 hr | 4+5 |
| **8** | **Track D**: _handle_compress @with_stage_gate +done/converged + 4 case | 4 | 0.5 hr | **独立** |
| 9 | 删 lexicon.py + lexicon_merge.py + update imports + 全量 test 不挂 | 3 | 0.5 hr | 7+8 |
| 10 | e2e smoke + acceptance doc | 4 | 1 hr | 全 |
| **合计** | **55 task / ~12 commit / ~13 hr** | | | |

(subagent batch 实跑估 4-6 hr)

### 8.3 Test 设计原则

- testcontainers 自动 spin pgvector container per session (首次 ~150 MB image)
- 每 test TRUNCATE (跨 test 隔离)
- 不 mock psycopg — 真 PG (testcontainers)
- embedding mock: `EXPLAIN_EMBEDDING_DISABLED=1`, test 用 fixed 1024 维 numpy
- testcontainers Pre-init 跑 `01-init.sql`

---

## 9. Deliverables

完整交付物 (Phase 17.1 落地后):

| 文件 | 路径 | 状态 |
|---|---|---|
| docker-compose.yml | `deploy/postgres/docker-compose.yml` | 新建 (§10 附录) |
| 01-init.sql | `deploy/postgres/init/01-init.sql` | 新建 (§10 附录) |
| deploy README | `deploy/postgres/README.md` | 新建 (部署步骤) |
| lexicon_pg.py | `src/explain_engine/persistence/lexicon_pg.py` | 新建 ~600 LOC |
| lexicon_migrations.py | `src/explain_engine/persistence/lexicon_migrations.py` | 新建 ~100 LOC |
| cli subcommand | `cli.py` `migrate-lexicon-pg` | 加 |
| .env.example | 更新 | +`EXPLAIN_DB_URL` |
| README.md | 更新 | +"Phase 17.1 部署" 一节 |
| pyproject.toml | 更新 | +`psycopg[binary,pool]`, +`testcontainers[postgres]` (dev) |
| Design doc | 本文档 | 完成 |
| Plan doc | `docs/plans/2026-05-25-lexicon-pg-perf-plan.md` | 待 writing-plans |
| Acceptance doc | `docs/plans/2026-05-25-lexicon-pg-perf-acceptance.md` | Wave 10 写 |

---

## 10. 部署附录 — docker-compose.yml + init.sql

### 10.1 `deploy/postgres/docker-compose.yml`

```yaml
version: "3.9"

services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: explain-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: explain
      POSTGRES_USER: explain
      POSTGRES_PASSWORD: ${EXPLAIN_DB_PASSWORD:-changeme}
      PGDATA: /var/lib/postgresql/data/pgdata
    ports:
      - "5432:5432"
    volumes:
      - ./pgdata:/var/lib/postgresql/data
      - ./init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U explain -d explain"]
      interval: 5s
      timeout: 3s
      retries: 5
```

### 10.2 `deploy/postgres/init/01-init.sql`

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS variables (
    global_id           TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL,
    abstraction_level   SMALLINT NOT NULL CHECK (abstraction_level IN (1, 2)),
    epistemic           TEXT NOT NULL,
    canonical_mechanism TEXT NOT NULL,
    canonical_signature TEXT NOT NULL,
    canonical_model_ver TEXT NOT NULL DEFAULT 'v1',
    reuse_count         INT NOT NULL DEFAULT 1,
    avg_essentialness   REAL NOT NULL,
    avg_consistency     REAL NOT NULL,
    first_seen_at       TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,
    source_sessions     TEXT[] NOT NULL DEFAULT '{}',
    embedding           vector(1024),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_variables_embedding
    ON variables USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_variables_source_sessions
    ON variables USING gin (source_sessions);

CREATE INDEX IF NOT EXISTS idx_variables_canonical_signature
    ON variables (canonical_signature);

CREATE INDEX IF NOT EXISTS idx_variables_name
    ON variables (name);

CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS variables_set_updated_at ON variables;
CREATE TRIGGER variables_set_updated_at
    BEFORE UPDATE ON variables
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TABLE IF NOT EXISTS lexicon_merge_audit (
    id                   BIGSERIAL PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    merged_into          TEXT NOT NULL,
    merged_into_canon    TEXT NOT NULL,
    merged_from_canon    TEXT NOT NULL,
    sim                  REAL NOT NULL,
    evidence_session_ids TEXT[] NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_merge_audit_ts ON lexicon_merge_audit (ts DESC);
CREATE INDEX IF NOT EXISTS idx_merge_audit_merged_into ON lexicon_merge_audit (merged_into);

CREATE TABLE IF NOT EXISTS lexicon_meta (
    id                  SMALLINT PRIMARY KEY CHECK (id = 1),
    last_retro_dedup_at TIMESTAMPTZ,
    flush_count_since   INT NOT NULL DEFAULT 0,
    canonical_model_ver TEXT NOT NULL DEFAULT 'v1'
);

INSERT INTO lexicon_meta (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
```

### 10.3 `deploy/postgres/README.md` (部署步骤)

```markdown
# Explain Engine PostgreSQL 部署

## 一次性部署 (in 172.30.26.12)

```bash
ssh user@172.30.26.12
sudo mkdir -p /opt/explain-db/{pgdata,init}
sudo chown $USER /opt/explain-db -R

# Copy 文件 (在本机跑)
scp docker-compose.yml user@172.30.26.12:/opt/explain-db/
scp init/01-init.sql user@172.30.26.12:/opt/explain-db/init/

# 启动 (in 172.30.26.12)
cd /opt/explain-db
EXPLAIN_DB_PASSWORD=<你设密码> docker compose up -d

# 验证
docker compose ps                                              # 应 healthy
docker compose exec postgres psql -U explain -c "\dx"          # 应显 vector
docker compose exec postgres psql -U explain -c "\dt"          # 应显 3 表
```

## 本机配置

```bash
echo 'export EXPLAIN_DB_URL="postgresql://explain:<密码>@172.30.26.12:5432/explain"' >> ~/.zshrc
source ~/.zshrc
```

## 数据迁移

```bash
.venv/bin/python -m explain_engine.cli migrate-lexicon-pg --dry-run    # 预览
.venv/bin/python -m explain_engine.cli migrate-lexicon-pg              # 实跑
```

## 备份 (可选)

```bash
# 手动 pg_dump
ssh user@172.30.26.12 'docker exec explain-postgres pg_dump -U explain explain' > backup_$(date +%Y%m%d).sql

# Cron 自动:
0 3 * * * ssh user@172.30.26.12 'docker exec explain-postgres pg_dump -U explain explain' \
  > /backup/explain_$(date +\%Y\%m\%d).sql 2>&1
```

## 升级

```bash
ssh user@172.30.26.12 'cd /opt/explain-db && docker compose pull && docker compose up -d'
```
```

---

## 11. 风险点 + 应对

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| testcontainers 在 dev 机器装不上 (Docker missing) | 中 | dev test 跑不了 | conftest 检测 docker, 缺失 skip pg_* test + warn |
| pgvector image 拉不下 | 低 | testcontainers fail | docker proxy 或 local registry 预拉 |
| HNSW 跟 numpy cosine 度量差异 | 低 | dedup 漏 | `vector_cosine_ops` 跟 numpy cosine 数学等价, test 验 |
| 31 var migrate 部分失败 | 中 | partial migrate | transaction 包, all-or-nothing |
| 删 lexicon.py 后 dead caller 漏 | 低 | import error | Wave 9 前 grep 全确认 |
| Chat 连不上 PG 用户 panic | 中 | frustration | 错信息含明确 Hint |
| canonical cache model_ver 升级流程不清 | 低 | 用户不知何时 bump | acceptance doc 写明 |
| 远程 PG latency 影响 chat | 中 | +5-50ms 依网络 | 内网 sub-ms 可忽略 |
| Migration 期 chat 同跑 race | 低 | partial state | docs 提示 migrate 时关 chat |
| pgvector dedup 慢 (N > 100k) | 低 | query > 100ms | Phase 17.2 调 HNSW `ef_search` 或上 IVF |
| Pool 满 timeout | 低 | 单 query fail | 调 `EXPLAIN_DB_POOL_MAX` |

---

## 12. 后续 Phase 17+ 候选 (不在本 phase)

1. **Tier 5**: 拆 `/compress` → `/propose` + `/score` + `/flush` 3 个 slash (UX 提升, 跟性能无关)
2. **Phase 17.2**: 加 IVF index 或调 HNSW 参数 (N > 100k 才值)
3. **Phase 17.3**: lexicon 上 read replica (跨地域 chat 用户)
4. **Phase 17.4**: theory cache (theories.json) 也移 PG (跟 lexicon 同库, 共享 transaction)
5. **Phase 17.5**: session-local 文件 (graph.json / transcript.jsonl) 也移 PG (但本地 IO 极快, ROI 低, 可不做)
