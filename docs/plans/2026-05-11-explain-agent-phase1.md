# Explain Agent · Phase 1 实施计划（数据底座）

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 搭建 explain agent 的数据底座，使后续 agent 编排层有可靠的数据接入：3 个 P0 Adapter 跑通、新闻语料采集与向量索引 pipeline 完成、90 天历史回填完毕、能通过向量检索拿到最近新闻。

**Architecture:** Python + uv 管理依赖。数据层复用现有 ClickHouse (`quant_data`) 和 MySQL (`quant_data`)，新增 `explain_agent` schema 存归因相关表。向量检索用 Qdrant（用户已启动）+ BGE-M3 embedding（本地 CPU 跑）。Adapter 全部实现 `DataAdapter` Protocol，便于上层统一调用。所有外部 API 调用通过 mock 单测覆盖核心转换逻辑，再用一次性手动集成验证打通真实数据。

**Tech Stack:** Python 3.11+、uv、pydantic-settings、SQLAlchemy（MySQL）、clickhouse-connect、qdrant-client、akshare、sentence-transformers (BGE-M3)、pytest、anthropic SDK（弱模型 tagger 用 Haiku）

**Phase 1 不做的：** LangGraph 编排、维度 worker、报告生成、CLI 入口。这些是 Phase 2 的事。

**前置依赖：**
- 用户已按 Qdrant compose 启动 Qdrant 服务
- 用户已有的 MySQL（`quant_data` 库）和 ClickHouse（`quant_data` 库）可访问

---

## 任务总览

| # | 任务 | 预计时长 |
|---|---|---|
| 1 | 项目骨架与依赖初始化 | 30 min |
| 2 | 配置加载层 (pydantic-settings) | 20 min |
| 3 | 数据库连接 (MySQL/CH/Qdrant) | 30 min |
| 4 | `explain_agent` MySQL schema 迁移 | 40 min |
| 5 | Qdrant collection 初始化 | 20 min |
| 6 | 核心数据结构 (Evidence / DataAdapter Protocol) | 30 min |
| 7 | `clickhouse_market` Adapter | 60 min |
| 8 | `mysql_fundamentals` Adapter | 50 min |
| 9 | `akshare_capital_flow` Adapter | 50 min |
| 10 | BGE-M3 Embedding 封装 | 40 min |
| 11 | News Crawler (akshare) | 60 min |
| 12 | News Tagger (Claude Haiku) | 50 min |
| 13 | News Indexing Pipeline (写 MySQL + Qdrant) | 40 min |
| 14 | `news_corpus` Adapter（向量检索） | 40 min |
| 15 | 90 天历史回填 CLI | 30 min |
| 16 | Phase 1 端到端验收 | 30 min |

**合计：~10 小时纯开发，预计 1 周内可完成（含调试/合规处理时间）。**

---

## Task 1: 项目骨架与依赖初始化

**目标：** 搭建 Python 项目骨架，使用 uv 管理依赖。

**Files:**
- Create: `pyproject.toml`
- Create: `uv.lock` (auto-generated)
- Create: `src/explain_agent/__init__.py`
- Create: `tests/__init__.py`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`

**Step 1: 安装 uv（若未安装）**

Run: `which uv || curl -LsSf https://astral.sh/uv/install.sh | sh`

**Step 2: 初始化项目**

```bash
cd /Users/jinziguan/Desktop/explain_everything
uv init --package --name explain-agent --python 3.11
```

**Step 3: 添加依赖**

```bash
uv add \
  pydantic pydantic-settings \
  sqlalchemy pymysql cryptography \
  clickhouse-connect \
  qdrant-client \
  akshare \
  sentence-transformers \
  anthropic \
  httpx tenacity \
  python-dotenv \
  rich typer

uv add --dev pytest pytest-asyncio pytest-mock ruff mypy
```

**Step 4: 写 .gitignore**

内容：

```gitignore
__pycache__/
*.py[cod]
.venv/
.env
.env.local
.pytest_cache/
.ruff_cache/
.mypy_cache/
dist/
*.egg-info/
.DS_Store

# 数据缓存
data/snapshots/
data/embeddings_cache/

# 日志
*.log
logs/
```

**Step 5: 写 .env.example**

```bash
# MySQL（已有 quant_data 库）
MYSQL_HOST=
MYSQL_PORT=3306
MYSQL_USER=
MYSQL_PASSWORD=
MYSQL_QUANT_DB=quant_data
MYSQL_EXPLAIN_DB=explain_agent

# ClickHouse（已有 quant_data 库）
CLICKHOUSE_HOST=
CLICKHOUSE_PORT=8123
CLICKHOUSE_USER=
CLICKHOUSE_PASSWORD=
CLICKHOUSE_DB=quant_data

# Qdrant
QDRANT_HOST=
QDRANT_PORT=6333
QDRANT_API_KEY=

# LLM 模型
ANTHROPIC_API_KEY=
WEAK_MODEL=claude-haiku-4-5-20251001
STRONG_MODEL=claude-opus-4-7

# 本地路径
SNAPSHOT_DIR=./data/snapshots
EMBEDDING_CACHE_DIR=./data/embeddings_cache
LOG_DIR=./logs
```

**Step 6: 写最小 README.md**

```markdown
# Explain Agent

A 股板块/主题异动归因 AI agent。详见 [设计文档](docs/plans/2026-05-11-explain-agent-design.md) 和 [Phase 1 实施计划](docs/plans/2026-05-11-explain-agent-phase1.md)。

## 快速开始

```bash
uv sync
cp .env.example .env  # 填写真实凭证
uv run pytest
```
```

**Step 7: 验证安装**

Run: `uv sync && uv run python -c "import akshare, qdrant_client, anthropic, sentence_transformers; print('OK')"`

Expected: 打印 `OK`，无 import error

**Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/ tests/ .gitignore .env.example README.md
git commit -m "项目骨架与依赖初始化

使用 uv 管理依赖，覆盖 phase 1 所需的 MySQL/CH/Qdrant/akshare/embedding/anthropic 等。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: 配置加载层

**目标：** 用 pydantic-settings 加载 `.env` 配置，提供类型安全的 settings 单例。

**Files:**
- Create: `src/explain_agent/config.py`
- Create: `tests/test_config.py`

**Step 1: 写失败测试**

`tests/test_config.py`：

```python
import os
from explain_agent.config import Settings


def test_settings_loads_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MYSQL_HOST", "1.2.3.4")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("MYSQL_USER", "u")
    monkeypatch.setenv("MYSQL_PASSWORD", "p")
    monkeypatch.setenv("CLICKHOUSE_HOST", "5.6.7.8")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("QDRANT_HOST", "9.10.11.12")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    s = Settings()
    assert s.mysql_host == "1.2.3.4"
    assert s.mysql_port == 3306
    assert s.clickhouse_host == "5.6.7.8"
    assert s.qdrant_host == "9.10.11.12"
    assert s.weak_model == "claude-haiku-4-5-20251001"
    assert s.strong_model == "claude-opus-4-7"


def test_mysql_url_built_correctly(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "h")
    monkeypatch.setenv("MYSQL_USER", "u")
    monkeypatch.setenv("MYSQL_PASSWORD", "p")
    monkeypatch.setenv("CLICKHOUSE_HOST", "h")
    monkeypatch.setenv("CLICKHOUSE_USER", "u")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "p")
    monkeypatch.setenv("QDRANT_HOST", "h")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    s = Settings()
    url = s.mysql_explain_url()
    assert url.startswith("mysql+pymysql://u:p@h:3306/explain_agent")
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_config.py -v`
Expected: ImportError（config 模块不存在）

**Step 3: 实现 config**

`src/explain_agent/config.py`：

```python
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mysql_host: str
    mysql_port: int = 3306
    mysql_user: str
    mysql_password: str
    mysql_quant_db: str = "quant_data"
    mysql_explain_db: str = "explain_agent"

    clickhouse_host: str
    clickhouse_port: int = 8123
    clickhouse_user: str
    clickhouse_password: str
    clickhouse_db: str = "quant_data"

    qdrant_host: str
    qdrant_port: int = 6333
    qdrant_api_key: str | None = None

    anthropic_api_key: str
    weak_model: str = "claude-haiku-4-5-20251001"
    strong_model: str = "claude-opus-4-7"

    snapshot_dir: Path = Path("./data/snapshots")
    embedding_cache_dir: Path = Path("./data/embeddings_cache")
    log_dir: Path = Path("./logs")

    def mysql_quant_url(self) -> str:
        return f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}@{self.mysql_host}:{self.mysql_port}/{self.mysql_quant_db}?charset=utf8mb4"

    def mysql_explain_url(self) -> str:
        return f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}@{self.mysql_host}:{self.mysql_port}/{self.mysql_explain_db}?charset=utf8mb4"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

**Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_config.py -v`
Expected: 2 passed

**Step 5: Commit**

```bash
git add src/explain_agent/config.py tests/test_config.py
git commit -m "配置加载层 (pydantic-settings)

提供类型安全的 Settings 单例，覆盖 MySQL/CH/Qdrant/Anthropic 配置。"
```

---

## Task 3: 数据库连接封装

**目标：** 集中管理 MySQL/ClickHouse/Qdrant 客户端的创建，便于测试时替换。

**Files:**
- Create: `src/explain_agent/db/__init__.py`
- Create: `src/explain_agent/db/mysql.py`
- Create: `src/explain_agent/db/clickhouse.py`
- Create: `src/explain_agent/db/qdrant.py`
- Create: `tests/test_db_connection.py`

**Step 1: 写最小连接测试**

`tests/test_db_connection.py`（标记为 integration，CI 默认跳过）：

```python
import pytest
from explain_agent.config import get_settings
from explain_agent.db.mysql import get_engine
from explain_agent.db.clickhouse import get_client as get_ch_client
from explain_agent.db.qdrant import get_qdrant_client


@pytest.mark.integration
def test_mysql_quant_db_reachable():
    engine = get_engine("quant")
    with engine.connect() as conn:
        result = conn.exec_driver_sql("SELECT 1").scalar()
        assert result == 1


@pytest.mark.integration
def test_clickhouse_reachable():
    client = get_ch_client()
    result = client.query("SELECT 1").result_rows
    assert result[0][0] == 1


@pytest.mark.integration
def test_qdrant_reachable():
    client = get_qdrant_client()
    info = client.get_collections()
    assert info is not None
```

**Step 2: 在 pyproject.toml 添加 marker 定义**

```toml
[tool.pytest.ini_options]
markers = [
    "integration: 需要真实数据库/网络的集成测试，CI 默认跳过",
]
addopts = "-m 'not integration'"
```

**Step 3: 实现 mysql.py**

```python
from functools import lru_cache
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from explain_agent.config import get_settings


@lru_cache
def get_engine(db: str = "quant") -> Engine:
    s = get_settings()
    if db == "quant":
        url = s.mysql_quant_url()
    elif db == "explain":
        url = s.mysql_explain_url()
    else:
        raise ValueError(f"unknown db: {db}")
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600)
```

**Step 4: 实现 clickhouse.py**

```python
from functools import lru_cache
import clickhouse_connect
from clickhouse_connect.driver.client import Client
from explain_agent.config import get_settings


@lru_cache
def get_client() -> Client:
    s = get_settings()
    return clickhouse_connect.get_client(
        host=s.clickhouse_host,
        port=s.clickhouse_port,
        username=s.clickhouse_user,
        password=s.clickhouse_password,
        database=s.clickhouse_db,
    )
```

**Step 5: 实现 qdrant.py**

```python
from functools import lru_cache
from qdrant_client import QdrantClient
from explain_agent.config import get_settings


@lru_cache
def get_qdrant_client() -> QdrantClient:
    s = get_settings()
    return QdrantClient(
        host=s.qdrant_host,
        port=s.qdrant_port,
        api_key=s.qdrant_api_key,
        https=False,
    )
```

**Step 6: 跑集成测试**

填好 `.env` 后：

Run: `uv run pytest tests/test_db_connection.py -v -m integration`
Expected: 3 passed

**Step 7: Commit**

```bash
git add src/explain_agent/db/ tests/test_db_connection.py pyproject.toml
git commit -m "数据库连接封装 (MySQL/CH/Qdrant)

提供 lru_cache 包裹的 client 工厂，便于复用与 mock。集成测试需 -m integration 显式触发。"
```

---

## Task 4: explain_agent MySQL schema 迁移

**目标：** 在用户的 MySQL 上新建 `explain_agent` 库，并建好 6 张核心表。

**Files:**
- Create: `migrations/mysql/001_init_explain_schema.sql`
- Create: `scripts/run_migrations.py`
- Create: `tests/test_migrations.py`

**Step 1: 写 SQL 迁移脚本**

`migrations/mysql/001_init_explain_schema.sql`：

```sql
CREATE DATABASE IF NOT EXISTS explain_agent
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE explain_agent;

CREATE TABLE IF NOT EXISTS explain_session (
  session_id      VARCHAR(64)  NOT NULL,
  raw_question    TEXT         NOT NULL,
  domain_id       VARCHAR(64)  DEFAULT NULL,
  target          VARCHAR(128) DEFAULT NULL,
  time_window_start DATE       DEFAULT NULL,
  time_window_end   DATE       DEFAULT NULL,
  status          VARCHAR(16)  NOT NULL DEFAULT 'created',
  total_cost      DOUBLE       NOT NULL DEFAULT 0,
  created_at      DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  finished_at     DATETIME(6)  DEFAULT NULL,
  PRIMARY KEY (session_id),
  KEY idx_created_at (created_at),
  KEY idx_target (target)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS explain_evidence_tree (
  session_id   VARCHAR(64) NOT NULL,
  tree_json    LONGTEXT    NOT NULL,
  narrative    LONGTEXT    DEFAULT NULL,
  confidence   VARCHAR(8)  DEFAULT NULL,
  created_at   DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (session_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS explain_news_corpus (
  news_id        VARCHAR(64)  NOT NULL,
  url_hash       CHAR(64)     NOT NULL,
  source         VARCHAR(32)  NOT NULL,
  url            VARCHAR(1024) DEFAULT NULL,
  title          VARCHAR(512) NOT NULL,
  content        LONGTEXT     NOT NULL,
  published_at   DATETIME     NOT NULL,
  fetched_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  tags_json      JSON         DEFAULT NULL,
  snapshot_id    VARCHAR(64)  DEFAULT NULL,
  is_indexed     TINYINT(1)   NOT NULL DEFAULT 0,
  PRIMARY KEY (news_id),
  UNIQUE KEY uk_url_hash (url_hash),
  KEY idx_published_at (published_at),
  KEY idx_is_indexed (is_indexed)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS explain_policy_corpus (
  policy_id      VARCHAR(64)  NOT NULL,
  url_hash       CHAR(64)     NOT NULL,
  source         VARCHAR(32)  NOT NULL,
  url            VARCHAR(1024) DEFAULT NULL,
  title          VARCHAR(512) NOT NULL,
  content        LONGTEXT     NOT NULL,
  issued_at      DATE         NOT NULL,
  fetched_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  tags_json      JSON         DEFAULT NULL,
  snapshot_id    VARCHAR(64)  DEFAULT NULL,
  is_indexed     TINYINT(1)   NOT NULL DEFAULT 0,
  PRIMARY KEY (policy_id),
  UNIQUE KEY uk_url_hash (url_hash),
  KEY idx_issued_at (issued_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS explain_research_corpus (
  research_id    VARCHAR(64)  NOT NULL,
  url_hash       CHAR(64)     NOT NULL,
  source         VARCHAR(32)  NOT NULL,
  url            VARCHAR(1024) DEFAULT NULL,
  title          VARCHAR(512) NOT NULL,
  abstract       LONGTEXT     DEFAULT NULL,
  institution    VARCHAR(128) DEFAULT NULL,
  industry       VARCHAR(128) DEFAULT NULL,
  published_at   DATE         NOT NULL,
  fetched_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  tags_json      JSON         DEFAULT NULL,
  snapshot_id    VARCHAR(64)  DEFAULT NULL,
  is_indexed     TINYINT(1)   NOT NULL DEFAULT 0,
  PRIMARY KEY (research_id),
  UNIQUE KEY uk_url_hash (url_hash),
  KEY idx_published_at (published_at),
  KEY idx_industry (industry)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS explain_snapshot_blob (
  snapshot_id    VARCHAR(64)  NOT NULL,
  content_type   VARCHAR(32)  NOT NULL,
  storage_path   VARCHAR(1024) NOT NULL,
  size_bytes     BIGINT       NOT NULL,
  created_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (snapshot_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS explain_followup_history (
  followup_id    VARCHAR(64)  NOT NULL,
  session_id     VARCHAR(64)  NOT NULL,
  question       TEXT         NOT NULL,
  answer         LONGTEXT     DEFAULT NULL,
  intent         VARCHAR(32)  DEFAULT NULL,
  created_at     DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (followup_id),
  KEY idx_session (session_id, created_at)
) ENGINE=InnoDB;
```

**Step 2: 写迁移执行脚本**

`scripts/run_migrations.py`：

```python
import sys
from pathlib import Path
from sqlalchemy import create_engine, text
from explain_agent.config import get_settings


def main() -> int:
    s = get_settings()
    migrations_dir = Path(__file__).parent.parent / "migrations" / "mysql"
    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        print("no migration files")
        return 1

    bootstrap_url = (
        f"mysql+pymysql://{s.mysql_user}:{s.mysql_password}"
        f"@{s.mysql_host}:{s.mysql_port}/?charset=utf8mb4"
    )
    engine = create_engine(bootstrap_url)

    with engine.begin() as conn:
        for f in files:
            print(f"applying {f.name}...")
            sql = f.read_text(encoding="utf-8")
            for stmt in [x.strip() for x in sql.split(";") if x.strip()]:
                conn.exec_driver_sql(stmt)
    print("migrations done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Step 3: 写迁移验证测试**

`tests/test_migrations.py`：

```python
import pytest
from sqlalchemy import inspect
from explain_agent.db.mysql import get_engine


@pytest.mark.integration
def test_explain_tables_exist():
    engine = get_engine("explain")
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    expected = {
        "explain_session",
        "explain_evidence_tree",
        "explain_news_corpus",
        "explain_policy_corpus",
        "explain_research_corpus",
        "explain_snapshot_blob",
        "explain_followup_history",
    }
    missing = expected - tables
    assert not missing, f"missing tables: {missing}"
```

**Step 4: 执行迁移**

Run: `uv run python scripts/run_migrations.py`
Expected: `applying 001_init_explain_schema.sql...` 然后 `migrations done`

**Step 5: 跑验证测试**

Run: `uv run pytest tests/test_migrations.py -v -m integration`
Expected: 1 passed

**Step 6: Commit**

```bash
git add migrations/ scripts/run_migrations.py tests/test_migrations.py
git commit -m "新增 explain_agent MySQL schema

7 张表：session/evidence_tree/news/policy/research/snapshot/followup。
提供最小迁移脚本与表存在性验证测试。"
```

---

## Task 5: Qdrant collection 初始化

**目标：** 在 Qdrant 上创建 `news_v1`、`policy_v1`、`research_v1` 三个 collection。

**Files:**
- Create: `src/explain_agent/db/qdrant_init.py`
- Create: `tests/test_qdrant_init.py`

**Step 1: 写初始化脚本**

`src/explain_agent/db/qdrant_init.py`：

```python
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    VectorParams,
    PayloadSchemaType,
)
from explain_agent.db.qdrant import get_qdrant_client


COLLECTIONS = {
    "news_v1": 1024,
    "policy_v1": 1024,
    "research_v1": 1024,
}


def ensure_collections(client: QdrantClient | None = None) -> dict[str, bool]:
    client = client or get_qdrant_client()
    existing = {c.name for c in client.get_collections().collections}
    created: dict[str, bool] = {}
    for name, dim in COLLECTIONS.items():
        if name in existing:
            created[name] = False
            continue
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        client.create_payload_index(name, "published_at", PayloadSchemaType.DATETIME)
        client.create_payload_index(name, "source", PayloadSchemaType.KEYWORD)
        client.create_payload_index(name, "tags", PayloadSchemaType.KEYWORD)
        created[name] = True
    return created
```

**Step 2: 写集成测试**

`tests/test_qdrant_init.py`：

```python
import pytest
from explain_agent.db.qdrant import get_qdrant_client
from explain_agent.db.qdrant_init import ensure_collections, COLLECTIONS


@pytest.mark.integration
def test_ensure_collections_idempotent():
    result1 = ensure_collections()
    result2 = ensure_collections()
    # 第二次调用全部应为 False（已存在）
    assert all(not v for v in result2.values())
    # 三个 collection 都存在
    client = get_qdrant_client()
    names = {c.name for c in client.get_collections().collections}
    for c in COLLECTIONS:
        assert c in names
```

**Step 3: 执行初始化**

Run: `uv run python -c "from explain_agent.db.qdrant_init import ensure_collections; print(ensure_collections())"`
Expected: `{'news_v1': True, 'policy_v1': True, 'research_v1': True}`

**Step 4: 跑测试验证幂等**

Run: `uv run pytest tests/test_qdrant_init.py -v -m integration`
Expected: 1 passed

**Step 5: Commit**

```bash
git add src/explain_agent/db/qdrant_init.py tests/test_qdrant_init.py
git commit -m "Qdrant collection 初始化

news/policy/research 三个 collection，1024 维 cosine 距离。幂等可重复执行。"
```

---

## Task 6: 核心数据结构

**目标：** 定义 `Evidence`、`DataAdapter` Protocol、`AdapterQuery` 等所有 Adapter 共享的数据契约。

**Files:**
- Create: `src/explain_agent/core/__init__.py`
- Create: `src/explain_agent/core/types.py`
- Create: `src/explain_agent/core/adapter.py`
- Create: `tests/test_core_types.py`

**Step 1: 写测试**

`tests/test_core_types.py`：

```python
from datetime import datetime, date
from explain_agent.core.types import Evidence, AdapterQuery


def test_evidence_serialization():
    e = Evidence(
        id="e1",
        source="news_corpus",
        source_type="news",
        url="https://example.com",
        title="测试标题",
        snippet="正文片段",
        raw_payload=None,
        snapshot_id=None,
        timestamp=datetime(2026, 5, 1, 9, 30),
        metadata={"tag": "半导体"},
    )
    d = e.model_dump()
    assert d["id"] == "e1"
    assert d["source_type"] == "news"
    assert d["metadata"]["tag"] == "半导体"


def test_adapter_query_defaults():
    q = AdapterQuery(
        keywords=["半导体", "国产替代"],
        time_window=(date(2026, 4, 1), date(2026, 5, 1)),
        target="半导体",
    )
    assert q.filters == {}
    assert q.limit == 50
```

**Step 2: 实现 types.py**

```python
from datetime import datetime, date
from typing import Literal, Protocol, runtime_checkable
from pydantic import BaseModel, Field


SourceType = Literal["news", "policy", "research", "market_data", "capital_flow"]


class Evidence(BaseModel):
    id: str
    source: str
    source_type: SourceType
    url: str | None = None
    title: str | None = None
    snippet: str
    raw_payload: dict | None = None
    snapshot_id: str | None = None
    timestamp: datetime
    metadata: dict = Field(default_factory=dict)


class AdapterQuery(BaseModel):
    keywords: list[str]
    time_window: tuple[date, date]
    target: str
    filters: dict = Field(default_factory=dict)
    limit: int = 50


@runtime_checkable
class DataAdapter(Protocol):
    name: str

    async def query(self, q: AdapterQuery) -> list[Evidence]: ...
```

**Step 3: 实现 adapter.py（基类工具）**

```python
from explain_agent.core.types import DataAdapter, AdapterQuery, Evidence


class AdapterError(RuntimeError):
    pass


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, DataAdapter] = {}

    def register(self, adapter: DataAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> DataAdapter:
        if name not in self._adapters:
            raise AdapterError(f"adapter not registered: {name}")
        return self._adapters[name]

    def list_names(self) -> list[str]:
        return sorted(self._adapters.keys())
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_core_types.py -v`
Expected: 2 passed

**Step 5: Commit**

```bash
git add src/explain_agent/core/ tests/test_core_types.py
git commit -m "核心数据结构: Evidence/AdapterQuery/DataAdapter Protocol

所有 Adapter 共享的契约，使用 pydantic 模型保证类型安全。"
```

---

## Task 7: `clickhouse_market` Adapter

**目标：** 实现行情数据 Adapter，输出板块/个股的涨跌幅、成交量、龙头股、量价异常。

**Files:**
- Create: `src/explain_agent/adapters/clickhouse_market.py`
- Create: `tests/test_clickhouse_market_adapter.py`

**Step 1: 写单测（mock CH client）**

`tests/test_clickhouse_market_adapter.py`：

```python
from datetime import date, datetime
from unittest.mock import MagicMock
import pytest
from explain_agent.core.types import AdapterQuery
from explain_agent.adapters.clickhouse_market import ClickHouseMarketAdapter


@pytest.mark.asyncio
async def test_query_returns_market_evidence_for_industry():
    mock_ch = MagicMock()
    mock_ch.query.return_value.result_rows = [
        # (symbol_id, name, pct_chg, amount, is_leader)
        (1001, "中芯国际", 8.32, 1.2e9, 1),
        (1002, "韦尔股份", 6.10, 5.4e8, 0),
    ]
    mock_mysql_resolver = MagicMock()
    mock_mysql_resolver.resolve_industry_symbols.return_value = [1001, 1002]

    adapter = ClickHouseMarketAdapter(
        ch_client=mock_ch,
        industry_resolver=mock_mysql_resolver,
    )
    q = AdapterQuery(
        keywords=["半导体"],
        time_window=(date(2026, 5, 1), date(2026, 5, 11)),
        target="半导体",
    )
    out = await adapter.query(q)
    assert len(out) >= 1
    assert out[0].source_type == "market_data"
    assert "中芯国际" in out[0].snippet or any("中芯国际" in e.snippet for e in out)
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_clickhouse_market_adapter.py -v`
Expected: ImportError

**Step 3: 实现 Adapter**

`src/explain_agent/adapters/clickhouse_market.py`：

```python
from datetime import datetime
from uuid import uuid4
from explain_agent.core.types import DataAdapter, AdapterQuery, Evidence


class IndustryResolver:
    """从 MySQL 解析行业/板块名到 symbol_id 列表。"""

    def __init__(self, engine):
        self.engine = engine

    def resolve_industry_symbols(self, industry_name: str) -> list[int]:
        sql = """
        SELECT s.symbol_id
        FROM quant_data.fr_industry_current ic
        JOIN quant_data.stock_symbol s
          ON s.symbol = ic.symbol OR s.code = ic.symbol
        WHERE ic.industry_l1 = %(name)s AND s.is_active = 1
        """
        with self.engine.begin() as conn:
            rows = conn.exec_driver_sql(sql, {"name": industry_name}).fetchall()
        return [r[0] for r in rows]


class ClickHouseMarketAdapter:
    name = "clickhouse_market"

    def __init__(self, ch_client, industry_resolver):
        self.ch = ch_client
        self.resolver = industry_resolver

    async def query(self, q: AdapterQuery) -> list[Evidence]:
        symbol_ids = self.resolver.resolve_industry_symbols(q.target)
        if not symbol_ids:
            return []

        sql = """
        SELECT
            s.symbol_id,
            argMax(b.close, b.trade_date) AS last_close,
            (argMax(b.close, b.trade_date) / argMin(b.close, b.trade_date) - 1) * 100 AS pct_chg,
            sum(b.amount_k) * 1000 AS total_amount
        FROM stock_bar_1d b
        WHERE b.symbol_id IN %(ids)s
          AND b.trade_date BETWEEN %(start)s AND %(end)s
        GROUP BY s.symbol_id
        ORDER BY pct_chg DESC
        LIMIT 20
        """
        result = self.ch.query(
            sql,
            parameters={
                "ids": tuple(symbol_ids),
                "start": q.time_window[0],
                "end": q.time_window[1],
            },
        )
        rows = result.result_rows
        if not rows:
            return []

        leaders = rows[:3]
        leader_snippet = "; ".join(f"symbol={r[0]} 涨跌={r[2]:.2f}%" for r in leaders)
        return [
            Evidence(
                id=str(uuid4()),
                source=self.name,
                source_type="market_data",
                snippet=f"{q.target} 板块 {q.time_window[0]}~{q.time_window[1]} 龙头股: {leader_snippet}",
                raw_payload={"rows": [list(r) for r in rows]},
                timestamp=datetime.now(),
                metadata={"target": q.target, "kind": "industry_leaders"},
            )
        ]
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_clickhouse_market_adapter.py -v`
Expected: 1 passed

**Step 5: 集成 smoke**

写一个临时 script 跑一遍真实查询验证：

```bash
uv run python -c "
import asyncio
from datetime import date
from explain_agent.core.types import AdapterQuery
from explain_agent.adapters.clickhouse_market import ClickHouseMarketAdapter, IndustryResolver
from explain_agent.db.mysql import get_engine
from explain_agent.db.clickhouse import get_client

adapter = ClickHouseMarketAdapter(get_client(), IndustryResolver(get_engine('quant')))
q = AdapterQuery(keywords=['半导体'], time_window=(date(2026,4,1), date(2026,5,1)), target='半导体')
print(asyncio.run(adapter.query(q)))
"
```

Expected: 打印出至少 1 条 Evidence

**Step 6: Commit**

```bash
git add src/explain_agent/adapters/clickhouse_market.py tests/test_clickhouse_market_adapter.py
git commit -m "新增 clickhouse_market Adapter

聚合行业内个股的涨跌幅与成交额，输出龙头股 Evidence。
依赖 IndustryResolver 从 MySQL 申万分类映射 symbol_id。"
```

---

## Task 8: `mysql_fundamentals` Adapter

**目标：** 从 MySQL 取行业 ROE/净利润/营收的近期变化，作为产业链/基本面维度证据。

**Files:**
- Create: `src/explain_agent/adapters/mysql_fundamentals.py`
- Create: `tests/test_mysql_fundamentals_adapter.py`

**Step 1: 写测试**

```python
from datetime import date, datetime
from unittest.mock import MagicMock
import pytest
from explain_agent.core.types import AdapterQuery
from explain_agent.adapters.mysql_fundamentals import MySQLFundamentalsAdapter


@pytest.mark.asyncio
async def test_returns_industry_fundamentals_summary():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    mock_conn.exec_driver_sql.return_value.fetchall.return_value = [
        ("000001.SZ", 12.5, 0.18, 0.45, 1.2e8, date(2026, 3, 31)),
        ("000002.SZ", 8.3, 0.10, 0.35, 8.0e7, date(2026, 3, 31)),
    ]

    adapter = MySQLFundamentalsAdapter(engine=mock_engine)
    q = AdapterQuery(
        keywords=["半导体"],
        time_window=(date(2026, 1, 1), date(2026, 5, 1)),
        target="半导体",
    )
    out = await adapter.query(q)
    assert len(out) >= 1
    assert out[0].source_type == "market_data"
    assert "ROE" in out[0].snippet or "净利润" in out[0].snippet
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_mysql_fundamentals_adapter.py -v`

**Step 3: 实现**

```python
from datetime import datetime
from uuid import uuid4
from explain_agent.core.types import AdapterQuery, Evidence


class MySQLFundamentalsAdapter:
    name = "mysql_fundamentals"

    def __init__(self, engine):
        self.engine = engine

    async def query(self, q: AdapterQuery) -> list[Evidence]:
        sql = """
        SELECT
            fp.symbol,
            fp.roe_avg,
            fp.np_margin,
            fp.gp_margin,
            fp.net_profit,
            fp.report_date
        FROM quant_data.fr_fundamental_profit fp
        JOIN quant_data.fr_industry_current ic
          ON fp.symbol = ic.symbol
        WHERE ic.industry_l1 = %(name)s
          AND fp.announcement_date BETWEEN %(start)s AND %(end)s
        ORDER BY fp.announcement_date DESC, fp.roe_avg DESC
        LIMIT 50
        """
        with self.engine.begin() as conn:
            rows = conn.exec_driver_sql(
                sql,
                {"name": q.target, "start": q.time_window[0], "end": q.time_window[1]},
            ).fetchall()
        if not rows:
            return []

        avg_roe = sum((r[1] or 0) for r in rows) / len(rows)
        avg_np_margin = sum((r[2] or 0) for r in rows) / len(rows)
        top3 = rows[:3]
        top_snippet = "; ".join(f"{r[0]} ROE={r[1]}" for r in top3)
        return [
            Evidence(
                id=str(uuid4()),
                source=self.name,
                source_type="market_data",
                snippet=(
                    f"{q.target} 行业近期基本面快照（{len(rows)} 家公司）："
                    f"平均 ROE {avg_roe:.2f}%、平均净利率 {avg_np_margin:.2%}；"
                    f"ROE Top 3: {top_snippet}"
                ),
                raw_payload={"rows": [list(r) for r in rows]},
                timestamp=datetime.now(),
                metadata={"target": q.target, "kind": "industry_fundamentals"},
            )
        ]
```

**Step 4: 跑测试 + 集成 smoke**

Run: `uv run pytest tests/test_mysql_fundamentals_adapter.py -v`

集成 smoke（手动运行一次）：

```bash
uv run python -c "
import asyncio
from datetime import date
from explain_agent.core.types import AdapterQuery
from explain_agent.adapters.mysql_fundamentals import MySQLFundamentalsAdapter
from explain_agent.db.mysql import get_engine

adapter = MySQLFundamentalsAdapter(get_engine('quant'))
q = AdapterQuery(keywords=['半导体'], time_window=(date(2025,1,1), date(2026,5,1)), target='半导体')
print(asyncio.run(adapter.query(q)))
"
```

**Step 5: Commit**

```bash
git add src/explain_agent/adapters/mysql_fundamentals.py tests/test_mysql_fundamentals_adapter.py
git commit -m "新增 mysql_fundamentals Adapter

按申万一级行业聚合 ROE/净利率/净利润等基本面指标，输出基本面快照 Evidence。"
```

---

## Task 9: `akshare_capital_flow` Adapter

**目标：** 实现资金面 Adapter，覆盖北向资金、主力净流入两项最关键的指标。

**Files:**
- Create: `src/explain_agent/adapters/akshare_capital_flow.py`
- Create: `tests/test_akshare_capital_flow_adapter.py`

**Step 1: 写测试**

```python
from datetime import date
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
from explain_agent.core.types import AdapterQuery
from explain_agent.adapters.akshare_capital_flow import AkshareCapitalFlowAdapter


@pytest.mark.asyncio
async def test_industry_main_flow_evidence():
    mock_df = pd.DataFrame(
        {
            "日期": ["2026-05-09", "2026-05-08"],
            "主力净流入-净额": [1.2e9, -8.5e8],
            "主力净流入-净占比": [4.5, -3.2],
        }
    )
    with patch("akshare.stock_sector_fund_flow_hist", return_value=mock_df):
        adapter = AkshareCapitalFlowAdapter()
        q = AdapterQuery(
            keywords=["半导体"],
            time_window=(date(2026, 5, 1), date(2026, 5, 11)),
            target="半导体",
        )
        out = await adapter.query(q)
    assert len(out) >= 1
    assert "主力净流入" in out[0].snippet
```

**Step 2: 实现**

```python
from datetime import datetime
from uuid import uuid4
import akshare as ak
import pandas as pd
from explain_agent.core.types import AdapterQuery, Evidence


class AkshareCapitalFlowAdapter:
    name = "akshare_capital_flow"

    async def query(self, q: AdapterQuery) -> list[Evidence]:
        evidences: list[Evidence] = []
        evidences.extend(self._industry_main_flow(q))
        return evidences

    def _industry_main_flow(self, q: AdapterQuery) -> list[Evidence]:
        try:
            df: pd.DataFrame = ak.stock_sector_fund_flow_hist(symbol=q.target)
        except Exception as e:
            return []
        if df is None or df.empty:
            return []

        df = df.copy()
        if "日期" in df.columns:
            df["日期"] = pd.to_datetime(df["日期"]).dt.date
            mask = (df["日期"] >= q.time_window[0]) & (df["日期"] <= q.time_window[1])
            df = df[mask]
        if df.empty:
            return []

        net_col = next((c for c in df.columns if "主力净流入-净额" in c), None)
        pct_col = next((c for c in df.columns if "主力净流入-净占比" in c), None)
        if net_col is None:
            return []

        total = df[net_col].sum()
        avg_pct = df[pct_col].mean() if pct_col else 0
        sign = "净流入" if total > 0 else "净流出"
        snippet = (
            f"{q.target} 板块在 {q.time_window[0]}~{q.time_window[1]} 期间主力{sign}"
            f"累计 {abs(total)/1e8:.2f} 亿，平均日占比 {avg_pct:.2f}%"
        )
        return [
            Evidence(
                id=str(uuid4()),
                source=self.name,
                source_type="capital_flow",
                snippet=snippet,
                raw_payload={"records": df.to_dict(orient="records")},
                timestamp=datetime.now(),
                metadata={"target": q.target, "kind": "industry_main_flow"},
            )
        ]
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_akshare_capital_flow_adapter.py -v`
Expected: 1 passed

**Step 4: 集成 smoke**

```bash
uv run python -c "
import asyncio
from datetime import date
from explain_agent.core.types import AdapterQuery
from explain_agent.adapters.akshare_capital_flow import AkshareCapitalFlowAdapter

adapter = AkshareCapitalFlowAdapter()
q = AdapterQuery(keywords=['半导体'], time_window=(date(2026,4,1), date(2026,5,11)), target='半导体')
print(asyncio.run(adapter.query(q)))
"
```

Expected: 至少 1 条 Evidence

**Step 5: Commit**

```bash
git add src/explain_agent/adapters/akshare_capital_flow.py tests/test_akshare_capital_flow_adapter.py
git commit -m "新增 akshare_capital_flow Adapter

MVP 仅覆盖行业主力资金净流入，北向/龙虎榜留 P1。"
```

---

## Task 10: BGE-M3 Embedding 封装

**目标：** 提供本地 embedding 服务，输入文本列表 → 1024 维向量列表。

**Files:**
- Create: `src/explain_agent/embedding/__init__.py`
- Create: `src/explain_agent/embedding/bge_m3.py`
- Create: `tests/test_embedding.py`

**Step 1: 写测试**

```python
import pytest
from explain_agent.embedding.bge_m3 import BGEM3Embedder


@pytest.mark.integration
@pytest.mark.slow
def test_embed_returns_1024_dim_vectors():
    embedder = BGEM3Embedder()
    texts = ["半导体板块今日大涨", "美联储宣布加息"]
    vectors = embedder.embed(texts)
    assert len(vectors) == 2
    assert len(vectors[0]) == 1024
    # 同主题文本应当相似度高
    import numpy as np
    v1, v2 = np.array(vectors[0]), np.array(vectors[1])
    cos = (v1 @ v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
    assert 0 <= cos <= 1
```

**Step 2: 在 pyproject.toml 加 marker `slow`**

```toml
markers = [
    "integration: 需要真实数据库/网络的集成测试",
    "slow: 较慢的测试（模型加载、大批量数据）",
]
```

**Step 3: 实现**

```python
from functools import lru_cache
from sentence_transformers import SentenceTransformer


class BGEM3Embedder:
    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self._model_name = model_name
        self._model: SentenceTransformer | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self.model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=16,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]


@lru_cache
def get_embedder() -> BGEM3Embedder:
    return BGEM3Embedder()
```

**Step 4: 跑测试（注意第一次会下载模型 ~2GB）**

Run: `uv run pytest tests/test_embedding.py -v -m "integration and slow"`
Expected: 1 passed（首次模型下载需几分钟）

**Step 5: Commit**

```bash
git add src/explain_agent/embedding/ tests/test_embedding.py pyproject.toml
git commit -m "BGE-M3 Embedding 封装

本地 SentenceTransformer 加载，1024 维向量。首次使用自动下载模型。"
```

---

## Task 11: News Crawler

**目标：** 调用 akshare 拉取财经新闻列表，输出标准化新闻条目。

**Files:**
- Create: `src/explain_agent/ingest/__init__.py`
- Create: `src/explain_agent/ingest/news_crawler.py`
- Create: `tests/test_news_crawler.py`

**Step 1: 写测试**

```python
from datetime import datetime
from unittest.mock import patch
import pandas as pd
from explain_agent.ingest.news_crawler import AkshareNewsCrawler


def test_crawl_returns_normalized_items():
    mock_df = pd.DataFrame(
        {
            "关键词": ["半导体", "半导体"],
            "新闻标题": ["半导体涨停", "AI 利好"],
            "新闻内容": ["正文1", "正文2"],
            "发布时间": ["2026-05-10 09:30:00", "2026-05-10 10:00:00"],
            "文章来源": ["东方财富", "新浪财经"],
            "新闻链接": ["http://a.com/1", "http://a.com/2"],
        }
    )
    with patch("akshare.stock_news_em", return_value=mock_df):
        items = AkshareNewsCrawler().crawl_symbol("半导体")
    assert len(items) == 2
    assert items[0].title == "半导体涨停"
    assert items[0].source == "东方财富"
    assert isinstance(items[0].published_at, datetime)
    assert items[0].url_hash  # 32 字符 hash


def test_crawl_dedupes_by_url_hash():
    mock_df = pd.DataFrame(
        {
            "新闻标题": ["t1", "t1"],
            "新闻内容": ["c", "c"],
            "发布时间": ["2026-05-10 09:30:00", "2026-05-10 09:30:00"],
            "文章来源": ["s", "s"],
            "新闻链接": ["http://a.com/x", "http://a.com/x"],
        }
    )
    with patch("akshare.stock_news_em", return_value=mock_df):
        items = AkshareNewsCrawler().crawl_symbol("半导体")
    assert len(items) == 1
```

**Step 2: 实现**

```python
import hashlib
from datetime import datetime
from uuid import uuid4
import akshare as ak
import pandas as pd
from pydantic import BaseModel


class NewsItem(BaseModel):
    news_id: str
    url_hash: str
    source: str
    url: str | None
    title: str
    content: str
    published_at: datetime


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


class AkshareNewsCrawler:
    def crawl_symbol(self, symbol_or_keyword: str) -> list[NewsItem]:
        try:
            df: pd.DataFrame = ak.stock_news_em(symbol=symbol_or_keyword)
        except Exception:
            return []
        if df is None or df.empty:
            return []

        df = df.copy()
        col_title = next(c for c in df.columns if "标题" in c)
        col_content = next((c for c in df.columns if "内容" in c), None)
        col_time = next(c for c in df.columns if "时间" in c)
        col_source = next((c for c in df.columns if "来源" in c), None)
        col_url = next((c for c in df.columns if "链接" in c), None)

        seen: set[str] = set()
        out: list[NewsItem] = []
        for _, row in df.iterrows():
            url = str(row.get(col_url, "") or "")
            url_h = _url_hash(url or str(row[col_title]) + str(row[col_time]))
            if url_h in seen:
                continue
            seen.add(url_h)
            out.append(
                NewsItem(
                    news_id=str(uuid4()),
                    url_hash=url_h,
                    source=str(row.get(col_source, "akshare")) if col_source else "akshare",
                    url=url or None,
                    title=str(row[col_title]),
                    content=str(row.get(col_content, "")) if col_content else "",
                    published_at=pd.to_datetime(row[col_time]).to_pydatetime(),
                )
            )
        return out
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_news_crawler.py -v`
Expected: 2 passed

**Step 4: Commit**

```bash
git add src/explain_agent/ingest/ tests/test_news_crawler.py
git commit -m "新增 akshare 新闻采集器

支持按关键词/symbol 拉取，url_hash 去重，输出标准化 NewsItem。"
```

---

## Task 12: News Tagger（弱模型）

**目标：** 用 Claude Haiku 给每条新闻打"行业/概念/政策类型"等标签，便于检索过滤。

**Files:**
- Create: `src/explain_agent/ingest/tagger.py`
- Create: `tests/test_tagger.py`

**Step 1: 写测试（mock anthropic 调用）**

```python
import json
from unittest.mock import MagicMock, patch
from explain_agent.ingest.tagger import NewsTagger


def test_tagger_parses_llm_json():
    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(text=json.dumps({
        "industries": ["半导体"],
        "concepts": ["国产替代", "HBM"],
        "policy_type": None,
        "event_type": "产业链",
    }))]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_resp

    tagger = NewsTagger(client=fake_client, model="claude-haiku-4-5-20251001")
    tags = tagger.tag(title="美国扩大对华芯片出口管制", content="...")
    assert "半导体" in tags["industries"]
    assert "国产替代" in tags["concepts"]
    assert tags["event_type"] == "产业链"


def test_tagger_handles_invalid_json():
    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(text="抱歉我不能回答")]
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_resp

    tagger = NewsTagger(client=fake_client, model="claude-haiku-4-5-20251001")
    tags = tagger.tag(title="标题", content="内容")
    # 失败时返回空标签，不抛异常
    assert tags == {"industries": [], "concepts": [], "policy_type": None, "event_type": None}
```

**Step 2: 实现**

```python
import json
from anthropic import Anthropic
from explain_agent.config import get_settings


SYSTEM_PROMPT = """你是金融新闻分类标注器。读完新闻后，输出 JSON：
{
  "industries": ["..."],     // 申万一级行业，无相关填 []
  "concepts": ["..."],       // 主题/概念，例如"国产替代"、"HBM"、"减肥药"
  "policy_type": "..."|null, // 货币/财政/产业/监管/外交，无相关填 null
  "event_type": "..."|null   // 产业链/事件/数据/公司公告/海外/无关
}
只输出 JSON，无任何其他文字。"""


_EMPTY = {"industries": [], "concepts": [], "policy_type": None, "event_type": None}


class NewsTagger:
    def __init__(self, client: Anthropic | None = None, model: str | None = None):
        s = get_settings()
        self.client = client or Anthropic(api_key=s.anthropic_api_key)
        self.model = model or s.weak_model

    def tag(self, title: str, content: str) -> dict:
        prompt = f"标题: {title}\n正文: {content[:1500]}"
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=300,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
            data = json.loads(text)
        except Exception:
            return dict(_EMPTY)
        return {
            "industries": data.get("industries", []) or [],
            "concepts": data.get("concepts", []) or [],
            "policy_type": data.get("policy_type"),
            "event_type": data.get("event_type"),
        }
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_tagger.py -v`
Expected: 2 passed

**Step 4: Commit**

```bash
git add src/explain_agent/ingest/tagger.py tests/test_tagger.py
git commit -m "新增新闻标签器 (Claude Haiku)

输出 industries/concepts/policy_type/event_type 四类标签，
失败时优雅降级到空标签不阻塞流水线。"
```

---

## Task 13: News Indexing Pipeline

**目标：** 把 crawler 输出经过 tagger 标注、embedding 向量化后，写入 MySQL + Qdrant。

**Files:**
- Create: `src/explain_agent/ingest/news_indexer.py`
- Create: `tests/test_news_indexer.py`

**Step 1: 写测试（mock 所有 IO）**

```python
from datetime import datetime
from unittest.mock import MagicMock
from explain_agent.ingest.news_crawler import NewsItem
from explain_agent.ingest.news_indexer import NewsIndexer


def make_item(i: int) -> NewsItem:
    return NewsItem(
        news_id=f"id_{i}",
        url_hash=f"h_{i}",
        source="东方财富",
        url=f"http://a.com/{i}",
        title=f"标题 {i}",
        content="内容",
        published_at=datetime(2026, 5, 10, 9, 30),
    )


def test_indexer_writes_mysql_and_qdrant():
    items = [make_item(i) for i in range(3)]

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = []  # 没有重复

    mock_tagger = MagicMock()
    mock_tagger.tag.return_value = {"industries": ["半导体"], "concepts": [], "policy_type": None, "event_type": None}

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024 for _ in items]

    mock_qdrant = MagicMock()

    indexer = NewsIndexer(
        engine=mock_engine,
        tagger=mock_tagger,
        embedder=mock_embedder,
        qdrant=mock_qdrant,
    )
    n = indexer.index(items)
    assert n == 3
    # MySQL 写入
    assert mock_conn.exec_driver_sql.called
    # Qdrant upsert
    assert mock_qdrant.upsert.called
```

**Step 2: 实现**

```python
import json
from qdrant_client.http.models import PointStruct
from sqlalchemy.engine import Engine
from explain_agent.ingest.news_crawler import NewsItem
from explain_agent.ingest.tagger import NewsTagger
from explain_agent.embedding.bge_m3 import BGEM3Embedder


class NewsIndexer:
    def __init__(
        self,
        engine: Engine,
        tagger: NewsTagger,
        embedder: BGEM3Embedder,
        qdrant,
        collection: str = "news_v1",
    ):
        self.engine = engine
        self.tagger = tagger
        self.embedder = embedder
        self.qdrant = qdrant
        self.collection = collection

    def _filter_existing(self, items: list[NewsItem]) -> list[NewsItem]:
        if not items:
            return []
        hashes = [i.url_hash for i in items]
        placeholders = ",".join(["%s"] * len(hashes))
        with self.engine.begin() as conn:
            rows = conn.exec_driver_sql(
                f"SELECT url_hash FROM explain_agent.explain_news_corpus WHERE url_hash IN ({placeholders})",
                tuple(hashes),
            ).fetchall()
        existing = {r[0] for r in rows}
        return [i for i in items if i.url_hash not in existing]

    def index(self, items: list[NewsItem]) -> int:
        items = self._filter_existing(items)
        if not items:
            return 0

        tags = [self.tagger.tag(i.title, i.content) for i in items]
        texts = [f"{i.title}。{i.content[:1500]}" for i in items]
        vectors = self.embedder.embed(texts)

        with self.engine.begin() as conn:
            for item, tag in zip(items, tags):
                conn.exec_driver_sql(
                    """
                    INSERT INTO explain_agent.explain_news_corpus
                      (news_id, url_hash, source, url, title, content, published_at, tags_json, is_indexed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        item.news_id,
                        item.url_hash,
                        item.source,
                        item.url,
                        item.title,
                        item.content,
                        item.published_at,
                        json.dumps(tag, ensure_ascii=False),
                    ),
                )

        points = [
            PointStruct(
                id=item.news_id,
                vector=vec,
                payload={
                    "news_id": item.news_id,
                    "source": item.source,
                    "title": item.title,
                    "published_at": item.published_at.isoformat(),
                    "tags": tag.get("industries", []) + tag.get("concepts", []),
                    "url": item.url,
                },
            )
            for item, vec, tag in zip(items, vectors, tags)
        ]
        self.qdrant.upsert(collection_name=self.collection, points=points)
        return len(items)
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_news_indexer.py -v`
Expected: 1 passed

**Step 4: Commit**

```bash
git add src/explain_agent/ingest/news_indexer.py tests/test_news_indexer.py
git commit -m "新增 news indexer pipeline

按 url_hash 去重 → 弱模型打标签 → BGE-M3 向量化 → 写 MySQL + Qdrant。"
```

---

## Task 14: `news_corpus` Adapter（向量检索）

**目标：** 实现新闻向量检索 Adapter，给 agent 上层用。

**Files:**
- Create: `src/explain_agent/adapters/news_corpus.py`
- Create: `tests/test_news_corpus_adapter.py`

**Step 1: 写测试**

```python
from datetime import date, datetime
from unittest.mock import MagicMock
import pytest
from explain_agent.core.types import AdapterQuery
from explain_agent.adapters.news_corpus import NewsCorpusAdapter


@pytest.mark.asyncio
async def test_news_corpus_returns_evidence_from_qdrant():
    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024]

    mock_qdrant = MagicMock()
    mock_hit = MagicMock()
    mock_hit.id = "news_1"
    mock_hit.score = 0.85
    mock_hit.payload = {
        "news_id": "news_1",
        "title": "半导体涨停",
        "source": "东方财富",
        "published_at": "2026-05-10T09:30:00",
        "url": "http://a.com/1",
        "tags": ["半导体"],
    }
    mock_qdrant.search.return_value = [mock_hit]

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = [("news_1", "正文片段...")]

    adapter = NewsCorpusAdapter(qdrant=mock_qdrant, embedder=mock_embedder, engine=mock_engine)
    q = AdapterQuery(
        keywords=["半导体", "涨停原因"],
        time_window=(date(2026, 5, 1), date(2026, 5, 11)),
        target="半导体",
    )
    out = await adapter.query(q)
    assert len(out) == 1
    assert out[0].source_type == "news"
    assert out[0].title == "半导体涨停"
```

**Step 2: 实现**

```python
from datetime import datetime, time
from qdrant_client.http.models import Filter, FieldCondition, Range
from explain_agent.core.types import AdapterQuery, Evidence


class NewsCorpusAdapter:
    name = "news_corpus"

    def __init__(self, qdrant, embedder, engine, collection: str = "news_v1"):
        self.qdrant = qdrant
        self.embedder = embedder
        self.engine = engine
        self.collection = collection

    async def query(self, q: AdapterQuery) -> list[Evidence]:
        query_text = " ".join([q.target] + q.keywords)
        vec = self.embedder.embed([query_text])[0]
        start_iso = datetime.combine(q.time_window[0], time.min).isoformat()
        end_iso = datetime.combine(q.time_window[1], time.max).isoformat()
        flt = Filter(
            must=[
                FieldCondition(
                    key="published_at",
                    range=Range(gte=start_iso, lte=end_iso),
                ),
            ]
        )
        hits = self.qdrant.search(
            collection_name=self.collection,
            query_vector=vec,
            query_filter=flt,
            limit=q.limit,
            with_payload=True,
        )
        if not hits:
            return []

        ids = [h.id for h in hits]
        placeholders = ",".join(["%s"] * len(ids))
        with self.engine.begin() as conn:
            rows = conn.exec_driver_sql(
                f"SELECT news_id, content FROM explain_agent.explain_news_corpus WHERE news_id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
        content_map = {r[0]: r[1] for r in rows}

        return [
            Evidence(
                id=h.id,
                source=self.name,
                source_type="news",
                url=h.payload.get("url"),
                title=h.payload.get("title"),
                snippet=(content_map.get(h.id, "") or h.payload.get("title", ""))[:600],
                timestamp=datetime.fromisoformat(h.payload["published_at"]),
                metadata={
                    "tags": h.payload.get("tags", []),
                    "score": h.score,
                    "source_name": h.payload.get("source"),
                },
            )
            for h in hits
        ]
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_news_corpus_adapter.py -v`
Expected: 1 passed

**Step 4: Commit**

```bash
git add src/explain_agent/adapters/news_corpus.py tests/test_news_corpus_adapter.py
git commit -m "新增 news_corpus Adapter

Qdrant 向量检索 + MySQL 回填正文，输出新闻 Evidence。
时间窗口通过 payload filter 过滤。"
```

---

## Task 15: 90 天历史回填 CLI

**目标：** 提供命令行入口，按关键词列表批量拉取 90 天新闻并 index。

**Files:**
- Create: `src/explain_agent/cli/__init__.py`
- Create: `src/explain_agent/cli/ingest_news.py`
- Modify: `pyproject.toml`（添加 console script）

**Step 1: 实现 CLI**

`src/explain_agent/cli/ingest_news.py`：

```python
import typer
from rich.console import Console
from rich.progress import track
from explain_agent.config import get_settings
from explain_agent.db.mysql import get_engine
from explain_agent.db.qdrant import get_qdrant_client
from explain_agent.embedding.bge_m3 import get_embedder
from explain_agent.ingest.news_crawler import AkshareNewsCrawler
from explain_agent.ingest.tagger import NewsTagger
from explain_agent.ingest.news_indexer import NewsIndexer


app = typer.Typer()
console = Console()


DEFAULT_KEYWORDS = [
    "半导体", "新能源", "光伏", "锂电池", "人工智能", "医药",
    "白酒", "银行", "地产", "军工", "煤炭", "钢铁", "化工",
    "电力", "通信", "传媒", "汽车", "家电",
]


@app.command()
def main(
    keywords_file: str | None = typer.Option(None, help="一行一个关键词的文件"),
    limit_per_kw: int = typer.Option(200, help="每个关键词最多取多少条"),
):
    s = get_settings()
    if keywords_file:
        kws = [x.strip() for x in open(keywords_file, encoding="utf-8") if x.strip()]
    else:
        kws = DEFAULT_KEYWORDS

    console.print(f"[bold]开始回填，关键词数 {len(kws)}[/bold]")

    crawler = AkshareNewsCrawler()
    tagger = NewsTagger()
    embedder = get_embedder()
    qdrant = get_qdrant_client()
    indexer = NewsIndexer(
        engine=get_engine("explain"),
        tagger=tagger,
        embedder=embedder,
        qdrant=qdrant,
    )

    total = 0
    for kw in track(kws, description="拉取中"):
        items = crawler.crawl_symbol(kw)[:limit_per_kw]
        n = indexer.index(items)
        total += n
        console.print(f"  {kw}: 新增 {n} 条")

    console.print(f"[bold green]完成，共新增 {total} 条新闻[/bold green]")


if __name__ == "__main__":
    app()
```

**Step 2: 在 pyproject.toml 添加 console script**

```toml
[project.scripts]
explain-ingest-news = "explain_agent.cli.ingest_news:app"
```

**Step 3: 执行回填**

Run: `uv run explain-ingest-news`
Expected: 进度条 + 每个关键词的新增数量，最后总数 > 0

**Step 4: 验证 Qdrant 有数据**

```bash
uv run python -c "
from explain_agent.db.qdrant import get_qdrant_client
c = get_qdrant_client()
info = c.get_collection('news_v1')
print(f'points: {info.points_count}')
"
```
Expected: `points: ` 后面是个非 0 数字

**Step 5: Commit**

```bash
git add src/explain_agent/cli/ pyproject.toml
git commit -m "新增 90 天历史回填 CLI (explain-ingest-news)

默认 18 个核心关键词，可通过文件覆盖。Rich 进度条展示。"
```

---

## Task 16: Phase 1 端到端验收

**目标：** 跑一遍真实查询，验证全链路通畅。

**Files:**
- Create: `tests/test_e2e_phase1.py`

**Step 1: 写端到端测试**

```python
from datetime import date
import pytest
from explain_agent.core.types import AdapterQuery
from explain_agent.adapters.clickhouse_market import ClickHouseMarketAdapter, IndustryResolver
from explain_agent.adapters.mysql_fundamentals import MySQLFundamentalsAdapter
from explain_agent.adapters.akshare_capital_flow import AkshareCapitalFlowAdapter
from explain_agent.adapters.news_corpus import NewsCorpusAdapter
from explain_agent.db.mysql import get_engine
from explain_agent.db.clickhouse import get_client as ch_client
from explain_agent.db.qdrant import get_qdrant_client
from explain_agent.embedding.bge_m3 import get_embedder


@pytest.mark.integration
@pytest.mark.asyncio
async def test_phase1_all_adapters_return_evidence_for_semiconductor():
    q = AdapterQuery(
        keywords=["涨停原因", "国产替代"],
        time_window=(date(2026, 4, 1), date(2026, 5, 11)),
        target="半导体",
    )

    market = ClickHouseMarketAdapter(ch_client(), IndustryResolver(get_engine("quant")))
    fund = MySQLFundamentalsAdapter(get_engine("quant"))
    flow = AkshareCapitalFlowAdapter()
    news = NewsCorpusAdapter(
        qdrant=get_qdrant_client(),
        embedder=get_embedder(),
        engine=get_engine("explain"),
    )

    r_market = await market.query(q)
    r_fund = await fund.query(q)
    r_flow = await flow.query(q)
    r_news = await news.query(q)

    print(f"market: {len(r_market)} | fundamentals: {len(r_fund)} | flow: {len(r_flow)} | news: {len(r_news)}")
    # 至少 3 个 adapter 返回了数据
    nonempty = sum(bool(r) for r in [r_market, r_fund, r_flow, r_news])
    assert nonempty >= 3, "至少 3 个 Adapter 应返回数据"
```

**Step 2: 执行端到端**

Run: `uv run pytest tests/test_e2e_phase1.py -v -m integration -s`
Expected: 1 passed，打印各 Adapter 返回的 Evidence 数量

**Step 3: 写 Phase 1 验收清单到 README**

在 README.md 末尾追加：

```markdown
## Phase 1 验收状态

- [x] Qdrant 已启动且 3 个 collection 已创建
- [x] explain_agent MySQL schema 7 张表已就绪
- [x] 4 个 P0 Adapter 单测通过：clickhouse_market / mysql_fundamentals / akshare_capital_flow / news_corpus
- [x] BGE-M3 embedding 本地可跑
- [x] 90 天新闻回填完成（详见 Qdrant points_count）
- [x] 端到端集成测试通过：4 个 Adapter 中 ≥3 个能返回真实 Evidence
```

**Step 4: Commit**

```bash
git add tests/test_e2e_phase1.py README.md
git commit -m "Phase 1 端到端验收测试 + 验收清单"
```

---

## 完成后

Phase 1 完成后的产出：

- ✅ 完整的 Python 项目骨架
- ✅ 4 个 P0 Adapter 全部跑通
- ✅ 新闻语料 + 向量索引完整 pipeline
- ✅ 90 天历史回填完成
- ✅ 单元测试 + 集成测试覆盖核心逻辑

下一步：进入 **Phase 2 实施计划**，搭建 LangGraph 状态机、6 维 worker、报告生成器，跑通第一个端到端归因案例。
