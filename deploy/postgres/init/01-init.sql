-- Phase 17.1: explain-engine lexicon schema (PG + pgvector).
--
-- 此文件被 docker-compose 通过 /docker-entrypoint-initdb.d/ 挂载, 容器首次
-- 启动自动跑 (后续重启不跑, 数据保留在 ./pgdata volume).
--
-- 同一文件也被 src/explain_engine/persistence/lexicon_pg_schema.py 读作
-- DDL_INIT_SQL 常量, conftest pg_container fixture 跑同一段 SQL 起 test container.

-- ── 1. pgvector extension (单库一体: relational + vector) ──
CREATE EXTENSION IF NOT EXISTS vector;


-- ── 2. variables 主表 ──
CREATE TABLE IF NOT EXISTS variables (
    global_id           TEXT PRIMARY KEY,                                -- 'v_<8hex>'
    name                TEXT NOT NULL,
    description         TEXT NOT NULL,
    abstraction_level   SMALLINT NOT NULL CHECK (abstraction_level IN (1, 2)),
    epistemic           TEXT NOT NULL,

    -- canonical mechanism + cache (Track B)
    canonical_mechanism TEXT NOT NULL,
    canonical_signature TEXT NOT NULL,                                   -- sha256[:16], cache hit key
    canonical_model_ver TEXT NOT NULL DEFAULT 'v1',                      -- bump 此值 invalidate 全 cache

    -- fitness (拍平 5 字段)
    reuse_count         INT NOT NULL DEFAULT 1,
    avg_essentialness   REAL NOT NULL,
    avg_consistency     REAL NOT NULL,
    first_seen_at       TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,

    -- source_sessions: PG 数组列 (GIN 索引反查)
    source_sessions     TEXT[] NOT NULL DEFAULT '{}',

    -- pgvector embedding (BGE-M3 1024 维; NULL = legacy entry 无 embedding)
    embedding           vector(1024),

    -- audit
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ── 3. 索引 (4 个) ──

-- HNSW vector index — cosine similarity dedup (O(log N), N=10k 时 ~5ms)
CREATE INDEX IF NOT EXISTS idx_variables_embedding
    ON variables
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- GIN array index — source_sessions 反查 (WHERE 'sid_xxx' = ANY(source_sessions))
CREATE INDEX IF NOT EXISTS idx_variables_source_sessions
    ON variables
    USING gin (source_sessions);

-- canonical cache lookup (Track B)
CREATE INDEX IF NOT EXISTS idx_variables_canonical_signature
    ON variables (canonical_signature);

-- name 搜索 (Phase 17+ 可能 /search lexicon)
CREATE INDEX IF NOT EXISTS idx_variables_name
    ON variables (name);


-- ── 4. updated_at trigger (自动刷新 timestamp) ──
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


-- ── 5. lexicon_merge_audit 表 (替代旧 logs/merge_audit.jsonl 文件) ──
CREATE TABLE IF NOT EXISTS lexicon_merge_audit (
    id                   BIGSERIAL PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    merged_into          TEXT NOT NULL,                                  -- winner global_id
    merged_into_canon    TEXT NOT NULL,                                  -- 前 80 字 sample
    merged_from_canon    TEXT NOT NULL,
    sim                  REAL NOT NULL,                                  -- cosine sim
    evidence_session_ids TEXT[] NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_merge_audit_ts ON lexicon_merge_audit (ts DESC);
CREATE INDEX IF NOT EXISTS idx_merge_audit_merged_into ON lexicon_merge_audit (merged_into);


-- ── 6. lexicon_meta 表 (Track C — lazy retroactive dedup 状态) ──
CREATE TABLE IF NOT EXISTS lexicon_meta (
    id                  SMALLINT PRIMARY KEY CHECK (id = 1),             -- 单 row
    last_retro_dedup_at TIMESTAMPTZ,
    flush_count_since   INT NOT NULL DEFAULT 0,
    canonical_model_ver TEXT NOT NULL DEFAULT 'v1'
);

INSERT INTO lexicon_meta (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
