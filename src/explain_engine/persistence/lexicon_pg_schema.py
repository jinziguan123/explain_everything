"""Phase 17.1: DDL 常量 — single source 同步 deploy/postgres/init/01-init.sql.

Test conftest pg_container fixture 跑 DDL_INIT_SQL 起 testcontainers pgvector container.
Production deploy 跑同一文件 (docker-entrypoint-initdb.d 挂载).

设计: 文件 in repo (`deploy/postgres/init/01-init.sql`), Python 端 read 该文件作字符串
常量. 改 schema 时只改一处 SQL, 自动两边同步.
"""
from __future__ import annotations

from pathlib import Path

# repo root = 本文件 (src/explain_engine/persistence/lexicon_pg_schema.py) 上 3 层
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_INIT_SQL_PATH = _REPO_ROOT / "deploy" / "postgres" / "init" / "01-init.sql"

DDL_INIT_SQL: str = _INIT_SQL_PATH.read_text(encoding="utf-8")
"""完整 DDL: CREATE EXTENSION vector + 3 表 (variables / lexicon_merge_audit /
lexicon_meta) + 4 索引 (HNSW / GIN / 2 btree) + updated_at trigger + meta seed row."""
