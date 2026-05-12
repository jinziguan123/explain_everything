# Explain Agent · Phase 2.D-3 实施计划（Lazy News Ingest + 研报爬虫 + framework 接入）

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 corpus 表自动保鲜（agent 跑归因时自动 lazy ingest news）+ 填补 dormant 的研报表（独立 CLI batch 拉东财行业/宏观研报）+ framework 接入研报作为 policy/industry_chain/international 三维的权威源。

**Architecture:** main_graph 新插一个 `lazy_ingest` 节点（load_framework 之后、market_facts 之前），用 `MAX(fetched_at) < now - 3h` 触发同步 akshare 增量拉新闻 + snapshot 落盘；新增 `EastmoneyResearchCrawler` + `ResearchIndexer` + `ResearchCorpusAdapter` + `explain-ingest-research` CLI 走 batch 路径；framework YAML 三维 `data_sources` 加 `research_corpus`。

**Tech Stack:** Python 3.11+、`httpx`（直接调东财底层 API，绕过 akshare 的 per-symbol 封装）、Phase 2.A-2.D-2 已有的 akshare / pydantic / sqlalchemy / qdrant / langgraph / pytest-asyncio。

**Phase 2.D-3 不做的：**
- 政策爬虫 C.2（推到 Phase 2.D-4，数据源决策复杂需独立 brainstorm）
- 研报 PDF 全文索引（YAGNI，title + 机构 + 行业已是核心结论密度）
- 个股研报（不符合"市场现象深层逻辑"目标）
- per-target 精准 lazy 判断（用全局 `MAX(fetched_at)`）
- schema migration（研报表已存在）

**前置依赖：**
- Phase 2.D-2 完工（commit `9561a4a`），127 单测通过
- Phase 2.D-3 设计文档（commit `cec1281`，`docs/plans/2026-05-12-explain-agent-phase2d3-design.md`）
- `.env` 已就绪，含真实 MySQL/ClickHouse/Qdrant 凭证

---

## 任务总览

| # | 任务 | 预估 |
|---|---|---|
| 1 | State schema · 加 lazy_ingest_count / lazy_ingest_skipped 字段 | 15 min |
| 2 | EastmoneyResearchCrawler + ResearchItem + 3 单测 | 60 min |
| 3 | ResearchIndexer + 2 单测 | 50 min |
| 4 | ResearchCorpusAdapter + 2 单测 | 50 min |
| 5 | explain-ingest-research CLI + pyproject.toml 入口 + 真实跑一次验证 | 30 min |
| 6 | lazy_ingest_node 实现 + 5 单测 | 90 min |
| 7 | main_graph 集成 lazy_ingest + 改 test_main_graph mock | 30 min |
| 8 | framework YAML 三维加 research_corpus + 3 单测 | 20 min |
| 9 | REPL + smoke 脚本注入 news_crawler/news_indexer + research adapter | 40 min |
| 10 | 端到端 smoke + README 更新 + push | 60 min |

**合计：约 7.5 小时，预计 1 个工作日完成。**

---

## Task 1: State schema 扩展

**目标：** 加 `lazy_ingest_count` 与 `lazy_ingest_skipped` 字段，让 lazy_ingest_node 能输出可追溯状态。

**Files:**
- Modify: `src/explain_agent/graph/state.py`
- Modify: `tests/test_graph_state.py`

**Step 1: 写测试**

在 `tests/test_graph_state.py` 末尾追加：

```python
def test_attribution_state_optional_lazy_ingest_fields():
    """新加的 lazy_ingest_count / lazy_ingest_skipped 是可选字段, 默认不在 dict 中。"""
    s = new_attribution_state(raw_question="test")
    assert "lazy_ingest_count" not in s
    assert "lazy_ingest_skipped" not in s
    # 但 TypedDict 允许后续赋值
    s["lazy_ingest_count"] = 5
    s["lazy_ingest_skipped"] = False
    assert s["lazy_ingest_count"] == 5
    assert s["lazy_ingest_skipped"] is False
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_graph_state.py::test_attribution_state_optional_lazy_ingest_fields -v`
Expected: 应该是 PASS（TypedDict 本身允许任意 key），但要确认。

如果未通过（assertion 错误），那是测试本身问题。

实际上：TypedDict total=False 时可以任意 key？不，它检查 key 是否在 schema 中。所以需要先改 schema。

让我们改成更明确的"schema 添加"逻辑：

**Step 1 (revised): 改 state.py**

在 `src/explain_agent/graph/state.py` 的 `AttributionState` class 末尾（在 `errors: list[str]` 之后）追加：

```python
    # lazy ingest（Phase 2.D-3 新增, 可选字段）
    lazy_ingest_count: int
    lazy_ingest_skipped: bool
```

`new_attribution_state` 默认值字典**不需要**初始化这两个字段（让它们保持"not in dict"，节点跑了才有）。

**Step 2: 写并跑测试**

替换 Step 1 的测试为：

```python
def test_attribution_state_supports_lazy_ingest_fields():
    """lazy_ingest_count / lazy_ingest_skipped 是 AttributionState 合法字段。"""
    from explain_agent.graph.state import AttributionState
    s: AttributionState = new_attribution_state(raw_question="test")
    s["lazy_ingest_count"] = 5
    s["lazy_ingest_skipped"] = False
    assert s["lazy_ingest_count"] == 5
    assert s["lazy_ingest_skipped"] is False

    # 默认 new_attribution_state 不放这两个字段（保持"未跑过"的状态）
    s2 = new_attribution_state(raw_question="test2")
    assert "lazy_ingest_count" not in s2
    assert "lazy_ingest_skipped" not in s2
```

Run: `uv run pytest tests/test_graph_state.py -v`
Expected: 9 passed（8 老的 + 1 新的）

**Step 3: 全量回归**

Run: `uv run pytest`
Expected: 128 passed（127 + 1）

**Step 4: Commit**

```bash
git add src/explain_agent/graph/state.py tests/test_graph_state.py
git commit -m "$(cat <<'EOF'
State schema · 加 lazy_ingest_count / lazy_ingest_skipped 字段

为 Phase 2.D-3 lazy_ingest_node 做准备。两个字段都是 optional, 节点
没跑就保持"未在 dict 中"状态, 持久化到 tree_json 时缺失不报错。
EOF
)"
```

---

## Task 2: EastmoneyResearchCrawler + 单测

**目标：** 实现一个直接调东财 `reportapi.eastmoney.com/report/list` 的爬虫，支持 qType=1（行业研报）+ qType=2（宏观策略），翻页 + 解析 + url_hash 去重。

**Files:**
- Create: `src/explain_agent/ingest/research_crawler.py`
- Create: `tests/test_research_crawler.py`

**Step 1: 写 3 个单测**

`tests/test_research_crawler.py`:

```python
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest


def test_crawler_parses_qtype1_industry_response():
    """qType=1 返回行业研报, 字段映射到 ResearchItem。"""
    from explain_agent.ingest.research_crawler import EastmoneyResearchCrawler

    fake_resp_p1 = MagicMock()
    fake_resp_p1.json.return_value = {
        "TotalPage": 1,
        "data": [{
            "title": "银行行业快评报告：精准有效实施适度宽松的货币政策",
            "orgSName": "万联证券",
            "publishDate": "2026-05-12 00:00:00.000",
            "infoCode": "AP202605121822224959",
            "industryName": "银行Ⅱ",
            "researcher": "郭懿",
        }],
    }
    with patch("httpx.get", return_value=fake_resp_p1):
        crawler = EastmoneyResearchCrawler()
        items = crawler.crawl(q_type=1, begin=date(2026, 5, 1), end=date(2026, 5, 12), max_pages=1)

    assert len(items) == 1
    it = items[0]
    assert it.title == "银行行业快评报告：精准有效实施适度宽松的货币政策"
    assert it.institution == "万联证券"
    assert it.industry == "银行Ⅱ"
    assert it.researcher == "郭懿"
    assert it.info_code == "AP202605121822224959"
    assert it.q_type == 1
    assert it.published_at == datetime(2026, 5, 12, 0, 0, 0)
    assert it.url and "AP202605121822224959" in it.url
    assert it.url_hash  # sha256 of infoCode


def test_crawler_qtype2_macro_uses_industry_placeholder():
    """qType=2 (宏观策略) 没有 industryName, 用占位 '宏观策略'。"""
    from explain_agent.ingest.research_crawler import EastmoneyResearchCrawler

    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "TotalPage": 1,
        "data": [{
            "title": "4月通胀数据跟踪",
            "orgSName": "万联证券",
            "publishDate": "2026-05-12 00:00:00.000",
            "infoCode": "AP202605121822224969",
            "industryName": "",
            "researcher": "于天旭",
        }],
    }
    with patch("httpx.get", return_value=fake_resp):
        crawler = EastmoneyResearchCrawler()
        items = crawler.crawl(q_type=2, begin=date(2026, 5, 1), end=date(2026, 5, 12), max_pages=1)

    assert len(items) == 1
    assert items[0].industry == "宏观策略"
    assert items[0].q_type == 2


def test_crawler_handles_empty_response():
    """API 返回空 data 时返回空列表。"""
    from explain_agent.ingest.research_crawler import EastmoneyResearchCrawler

    fake_resp = MagicMock()
    fake_resp.json.return_value = {"TotalPage": 0, "data": []}
    with patch("httpx.get", return_value=fake_resp):
        crawler = EastmoneyResearchCrawler()
        items = crawler.crawl(q_type=1, begin=date(2026, 5, 1), end=date(2026, 5, 12))
    assert items == []
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_research_crawler.py -v`
Expected: ImportError（research_crawler 不存在）

**Step 3: 实现 research_crawler.py**

`src/explain_agent/ingest/research_crawler.py`:

```python
import hashlib
from datetime import date, datetime
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel


class ResearchItem(BaseModel):
    research_id: str
    url_hash: str
    source: str
    url: str | None
    title: str
    institution: str
    industry: str
    published_at: datetime
    q_type: int  # 1=行业研报, 2=宏观策略
    researcher: str
    info_code: str


def _url_hash(info_code: str) -> str:
    return hashlib.sha256(info_code.encode("utf-8")).hexdigest()


class EastmoneyResearchCrawler:
    """直接调东财底层 API, 绕过 akshare 的 per-symbol 封装。"""

    BASE_URL = "https://reportapi.eastmoney.com/report/list"

    def crawl(
        self,
        q_type: Literal[1, 2],
        begin: date,
        end: date,
        page_size: int = 50,
        max_pages: int = 5,
    ) -> list[ResearchItem]:
        out: list[ResearchItem] = []
        for page in range(1, max_pages + 1):
            params = {
                "industryCode": "*", "pageSize": str(page_size), "industry": "*",
                "rating": "*", "ratingChange": "*",
                "beginTime": begin.isoformat(), "endTime": end.isoformat(),
                "pageNo": str(page), "qType": str(q_type), "code": "*",
                "p": str(page), "pageNum": str(page), "pageNumber": str(page),
            }
            try:
                resp = httpx.get(self.BASE_URL, params=params, timeout=15.0)
                data = resp.json()
            except Exception:
                break

            rows = data.get("data") or []
            if not rows:
                break

            for row in rows:
                try:
                    info_code = row.get("infoCode") or ""
                    if not info_code:
                        continue
                    industry_name = row.get("industryName") or ""
                    if q_type == 2 and not industry_name:
                        industry_name = "宏观策略"

                    published_str = (row.get("publishDate") or "").split(".")[0]
                    if not published_str:
                        continue
                    published = datetime.strptime(published_str, "%Y-%m-%d %H:%M:%S")

                    out.append(ResearchItem(
                        research_id=str(uuid4()),
                        url_hash=_url_hash(info_code),
                        source="东方财富研报",
                        url=f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf",
                        title=row.get("title") or "",
                        institution=row.get("orgSName") or "",
                        industry=industry_name,
                        published_at=published,
                        q_type=q_type,
                        researcher=row.get("researcher") or "",
                        info_code=info_code,
                    ))
                except Exception:
                    continue  # 单条解析失败不影响整体

            if page >= int(data.get("TotalPage") or 0):
                break

        return out
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_research_crawler.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add src/explain_agent/ingest/research_crawler.py tests/test_research_crawler.py
git commit -m "$(cat <<'EOF'
新增 EastmoneyResearchCrawler（行业 + 宏观研报）

直接调东财底层 reportapi.eastmoney.com/report/list, 绕过 akshare
的 per-symbol 封装。支持 qType=1（行业研报）+ qType=2（宏观策略,
industry 占位"宏观策略"）。url_hash = sha256(infoCode) 保证去重,
单条解析失败不阻塞整体。
EOF
)"
```

---

## Task 3: ResearchIndexer + 单测

**目标：** 仿 NewsIndexer 实现 ResearchIndexer，去重 + embedding + INSERT explain_research_corpus + Qdrant research_v1 upsert。

**Files:**
- Create: `src/explain_agent/ingest/research_indexer.py`
- Create: `tests/test_research_indexer.py`

**Step 1: 写 2 个单测**

`tests/test_research_indexer.py`:

```python
from datetime import datetime
from unittest.mock import MagicMock

from explain_agent.ingest.research_crawler import ResearchItem


def make_item(i: int, qt: int = 1) -> ResearchItem:
    return ResearchItem(
        research_id=f"id_{i}", url_hash=f"h_{i}",
        source="东方财富研报",
        url=f"https://x.com/{i}.pdf",
        title=f"研报标题 {i}",
        institution="测试证券",
        industry="银行Ⅱ" if qt == 1 else "宏观策略",
        published_at=datetime(2026, 5, 10, 9, 0),
        q_type=qt, researcher="测试分析师",
        info_code=f"AP_TEST_{i}",
    )


def test_indexer_writes_mysql_and_qdrant():
    from explain_agent.ingest.research_indexer import ResearchIndexer

    items = [make_item(i) for i in range(3)]
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = []

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024 for _ in items]
    mock_qdrant = MagicMock()

    indexer = ResearchIndexer(
        engine=mock_engine, embedder=mock_embedder, qdrant=mock_qdrant,
    )
    n = indexer.index(items)
    assert n == 3

    # embedding 用的是 "title | 机构 | 行业" 短文本
    texts = mock_embedder.embed.call_args.args[0]
    assert "研报标题 0" in texts[0]
    assert "测试证券" in texts[0]
    assert "银行Ⅱ" in texts[0]

    # INSERT explain_research_corpus 至少 3 次
    insert_calls = [c for c in mock_conn.exec_driver_sql.call_args_list
                    if "INSERT INTO explain_agent.explain_research_corpus" in c.args[0]]
    assert len(insert_calls) == 3
    # 第一条 INSERT 参数检查
    first_params = insert_calls[0].args[1]
    assert "id_0" in first_params      # research_id
    assert "h_0" in first_params       # url_hash

    # Qdrant upsert 调用
    assert mock_qdrant.upsert.called
    points = mock_qdrant.upsert.call_args.kwargs.get("points") or mock_qdrant.upsert.call_args.args[1]
    assert len(points) == 3


def test_indexer_dedupes_by_url_hash():
    """已存在的 url_hash 自动跳过。"""
    from explain_agent.ingest.research_indexer import ResearchIndexer

    items = [make_item(0), make_item(1)]
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    # h_0 已存在, h_1 是新的
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = [("h_0",)]

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024]
    mock_qdrant = MagicMock()

    indexer = ResearchIndexer(engine=mock_engine, embedder=mock_embedder, qdrant=mock_qdrant)
    n = indexer.index(items)
    assert n == 1
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_research_indexer.py -v`
Expected: ImportError

**Step 3: 实现 research_indexer.py**

`src/explain_agent/ingest/research_indexer.py`:

```python
import json

from qdrant_client.http.models import PointStruct
from sqlalchemy.engine import Engine

from explain_agent.embedding.bge_m3 import BGEM3Embedder
from explain_agent.ingest.research_crawler import ResearchItem


class ResearchIndexer:
    """与 NewsIndexer 同形, 复用 BGE-M3 + Qdrant research_v1 collection。"""

    def __init__(
        self,
        engine: Engine,
        embedder: BGEM3Embedder,
        qdrant,
        collection: str = "research_v1",
    ):
        self.engine = engine
        self.embedder = embedder
        self.qdrant = qdrant
        self.collection = collection

    def _filter_existing(self, items: list[ResearchItem]) -> list[ResearchItem]:
        if not items:
            return []
        hashes = [i.url_hash for i in items]
        placeholders = ",".join(["%s"] * len(hashes))
        with self.engine.begin() as conn:
            rows = conn.exec_driver_sql(
                f"SELECT url_hash FROM explain_agent.explain_research_corpus WHERE url_hash IN ({placeholders})",
                tuple(hashes),
            ).fetchall()
        existing = {r[0] for r in rows}
        return [i for i in items if i.url_hash not in existing]

    def _to_embedding_text(self, item: ResearchItem) -> str:
        return f"{item.title} | {item.institution} | {item.industry}"

    def index(self, items: list[ResearchItem]) -> int:
        items = self._filter_existing(items)
        if not items:
            return 0

        texts = [self._to_embedding_text(i) for i in items]
        vectors = self.embedder.embed(texts)

        with self.engine.begin() as conn:
            for item in items:
                tags = {
                    "q_type": item.q_type,
                    "researcher": item.researcher,
                    "info_code": item.info_code,
                }
                conn.exec_driver_sql(
                    """
                    INSERT INTO explain_agent.explain_research_corpus
                      (research_id, url_hash, source, url, title, abstract,
                       institution, industry, published_at, tags, is_indexed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        item.research_id, item.url_hash, item.source, item.url,
                        item.title, item.title,  # abstract = title (没有正文)
                        item.institution, item.industry, item.published_at,
                        json.dumps(tags, ensure_ascii=False),
                    ),
                )

        points = [
            PointStruct(
                id=item.research_id,
                vector=vec,
                payload={
                    "research_id": item.research_id,
                    "title": item.title,
                    "institution": item.institution,
                    "industry": item.industry,
                    "published_at": item.published_at.isoformat(),
                    "q_type": item.q_type,
                    "url": item.url,
                },
            )
            for item, vec in zip(items, vectors)
        ]
        self.qdrant.upsert(collection_name=self.collection, points=points)
        return len(items)
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_research_indexer.py -v`
Expected: 2 passed

**Step 5: Commit**

```bash
git add src/explain_agent/ingest/research_indexer.py tests/test_research_indexer.py
git commit -m "$(cat <<'EOF'
新增 ResearchIndexer（仿 NewsIndexer）

复用 BGE-M3 + Qdrant research_v1 collection。embedding 文本是
"title | 机构 | 行业" 高密度短文本。INSERT explain_research_corpus
时 abstract 字段先用 title 占位（没有 PDF 正文）, snapshot_id
留空（研报不集成 SnapshotStore）。url_hash 去重保证幂等。
EOF
)"
```

---

## Task 4: ResearchCorpusAdapter + 单测

**目标：** 仿 NewsCorpusAdapter 实现 ResearchCorpusAdapter，让 DimensionWorker 能查 research_corpus。

**Files:**
- Create: `src/explain_agent/adapters/research_corpus.py`
- Create: `tests/test_research_corpus_adapter.py`

**Step 1: 写 2 个单测**

`tests/test_research_corpus_adapter.py`:

```python
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from explain_agent.core.types import AdapterQuery


@pytest.mark.asyncio
async def test_query_returns_evidence_with_research_source_type():
    from explain_agent.adapters.research_corpus import ResearchCorpusAdapter

    fake_qdrant = MagicMock()
    fake_hit = MagicMock()
    fake_hit.id = "research_1"
    fake_hit.score = 0.85
    fake_hit.payload = {
        "research_id": "research_1",
        "title": "半导体行业策略 2026 春季",
        "institution": "中信证券",
        "industry": "电子",
        "published_at": "2026-05-10T09:00:00",
        "q_type": 1,
        "url": "https://x.com/r1.pdf",
    }
    fake_qdrant.query_points.return_value = MagicMock(points=[fake_hit])

    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [[0.1] * 1024]

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = [
        ("research_1", "半导体行业策略 2026 春季", "中信证券", "电子"),
    ]

    adapter = ResearchCorpusAdapter(qdrant=fake_qdrant, embedder=fake_embedder, engine=mock_engine)
    q = AdapterQuery(
        keywords=["半导体", "产业链"],
        time_window=(date(2026, 5, 1), date(2026, 5, 12)),
        target="半导体",
        limit=5,
    )
    out = await adapter.query(q)
    assert len(out) == 1
    ev = out[0]
    assert ev.source == "research_corpus"
    assert ev.source_type == "research"
    assert "半导体行业策略" in ev.snippet
    assert "中信证券" in ev.snippet
    assert ev.url == "https://x.com/r1.pdf"


@pytest.mark.asyncio
async def test_query_returns_empty_when_no_hits():
    from explain_agent.adapters.research_corpus import ResearchCorpusAdapter

    fake_qdrant = MagicMock()
    fake_qdrant.query_points.return_value = MagicMock(points=[])
    fake_embedder = MagicMock()
    fake_embedder.embed.return_value = [[0.1] * 1024]

    adapter = ResearchCorpusAdapter(qdrant=fake_qdrant, embedder=fake_embedder, engine=MagicMock())
    q = AdapterQuery(
        keywords=["x"], time_window=(date(2026, 5, 1), date(2026, 5, 12)),
        target="x", limit=5,
    )
    out = await adapter.query(q)
    assert out == []
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_research_corpus_adapter.py -v`
Expected: ImportError

**Step 3: 实现 research_corpus.py**

`src/explain_agent/adapters/research_corpus.py`:

```python
from datetime import datetime, time

from qdrant_client.http.models import Filter, FieldCondition, DatetimeRange

from explain_agent.core.types import AdapterQuery, Evidence


class ResearchCorpusAdapter:
    name = "research_corpus"

    def __init__(self, qdrant, embedder, engine, collection: str = "research_v1"):
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
                    range=DatetimeRange(gte=start_iso, lte=end_iso),
                ),
            ]
        )
        try:
            resp = self.qdrant.query_points(
                collection_name=self.collection,
                query=vec,
                query_filter=flt,
                limit=q.limit,
                with_payload=True,
            )
        except Exception:
            return []
        hits = resp.points if hasattr(resp, "points") else resp
        if not hits:
            return []

        # 从 MySQL 补全 title / institution / industry / abstract (snippet 用)
        ids = [h.id for h in hits]
        placeholders = ",".join(["%s"] * len(ids))
        try:
            with self.engine.begin() as conn:
                rows = conn.exec_driver_sql(
                    f"""SELECT research_id, title, institution, industry
                        FROM explain_agent.explain_research_corpus
                        WHERE research_id IN ({placeholders})""",
                    tuple(ids),
                ).fetchall()
            meta_map = {r[0]: (r[1], r[2], r[3]) for r in rows}
        except Exception:
            meta_map = {}

        out: list[Evidence] = []
        for h in hits:
            title, inst, industry = meta_map.get(h.id, (
                h.payload.get("title", ""),
                h.payload.get("institution", ""),
                h.payload.get("industry", ""),
            ))
            snippet = f"{title} | {inst} | {industry}"
            out.append(Evidence(
                id=h.id,
                source=self.name,
                source_type="research",
                url=h.payload.get("url"),
                title=title,
                snippet=snippet,
                timestamp=datetime.fromisoformat(h.payload["published_at"]),
                metadata={
                    "institution": inst,
                    "industry": industry,
                    "q_type": h.payload.get("q_type"),
                    "score": h.score,
                },
            ))
        return out
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_research_corpus_adapter.py -v`
Expected: 2 passed

**Step 5: Commit**

```bash
git add src/explain_agent/adapters/research_corpus.py tests/test_research_corpus_adapter.py
git commit -m "$(cat <<'EOF'
新增 ResearchCorpusAdapter（仿 NewsCorpusAdapter）

向量检索 Qdrant research_v1 + 从 MySQL 补全 title/institution/
industry → Evidence(source="research_corpus", source_type="research",
snippet="title | 机构 | 行业") 短文本高密度。Qdrant 失败时返回 []
不阻塞 worker, MySQL 失败时降级用 Qdrant payload。
EOF
)"
```

---

## Task 5: explain-ingest-research CLI + 真实跑一次

**目标：** 新增 typer CLI 入口，默认拉最近 7 天 qType=1 + qType=2 研报。注册 pyproject.toml entry。真实跑一次验证。

**Files:**
- Create: `src/explain_agent/cli/ingest_research.py`
- Modify: `pyproject.toml`

**Step 1: 实现 CLI**

`src/explain_agent/cli/ingest_research.py`:

```python
from datetime import date, timedelta

import typer
from rich.console import Console

from explain_agent.db.mysql import get_engine
from explain_agent.db.qdrant import get_qdrant_client
from explain_agent.embedding.bge_m3 import get_embedder
from explain_agent.ingest.research_crawler import EastmoneyResearchCrawler
from explain_agent.ingest.research_indexer import ResearchIndexer


app = typer.Typer()
console = Console()


@app.command()
def main(
    days: int = typer.Option(7, help="回溯天数"),
    q_types: list[int] = typer.Option([1, 2], help="qType: 1=行业, 2=宏观"),
    max_pages_per_type: int = typer.Option(5, help="每种 qType 最多翻多少页"),
    page_size: int = typer.Option(50, help="单页条数"),
):
    console.print(f"[bold]开始拉研报, 回溯 {days} 天, qType={q_types}[/bold]")

    crawler = EastmoneyResearchCrawler()
    embedder = get_embedder()
    embedder.embed(["warm up"])
    qdrant = get_qdrant_client()
    engine = get_engine("explain")

    indexer = ResearchIndexer(engine=engine, embedder=embedder, qdrant=qdrant)

    end = date.today()
    begin = end - timedelta(days=days)

    total = 0
    for qt in q_types:
        console.print(f"[dim]拉 qType={qt} ...[/dim]")
        items = crawler.crawl(
            q_type=qt, begin=begin, end=end,
            page_size=page_size, max_pages=max_pages_per_type,
        )
        n = indexer.index(items)
        console.print(f"  qType={qt}: 抓到 {len(items)} 条, 新增 {n} 条")
        total += n

    console.print(f"[bold green]完成, 共新增 {total} 条研报[/bold green]")


if __name__ == "__main__":
    app()
```

**Step 2: 注册 entry point**

修改 `pyproject.toml` 的 `[project.scripts]` 块：

```toml
[project.scripts]
explain-ingest-news = "explain_agent.cli.ingest_news:app"
explain-ingest-research = "explain_agent.cli.ingest_research:app"  # 新增
explain = "explain_agent.cli.repl.loop:main"
```

Run: `uv sync`（让 entry point 注册到 venv）
Expected: 不报错。

**Step 3: 真实跑一次**

Run: `uv run explain-ingest-research --days 7 --max-pages-per-type 2`

Expected：
- 打印 `qType=1: 抓到 N1 条, 新增 N1 条`（首次跑全是新数据）
- 打印 `qType=2: 抓到 N2 条, 新增 N2 条`
- 总耗时 30-60s
- DB 验证：
  ```bash
  uv run python -c "
  from explain_agent.db.mysql import get_engine
  engine = get_engine('explain')
  with engine.begin() as conn:
      rows = conn.exec_driver_sql(
          \"SELECT q_type_from_tags, COUNT(*) FROM (SELECT JSON_EXTRACT(tags, '\$.q_type') AS q_type_from_tags FROM explain_agent.explain_research_corpus) t GROUP BY q_type_from_tags\"
      ).fetchall()
      print(rows)
  "
  ```
  期望看到 q_type=1 和 q_type=2 两组数据。

**Step 4: 跑全量回归确认前面任务不破**

Run: `uv run pytest`
Expected: 全部通过（128 → 135：128 + 3 crawler + 2 indexer + 2 adapter = 135）

**Step 5: Commit**

```bash
git add src/explain_agent/cli/ingest_research.py pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
新增 explain-ingest-research CLI

typer 入口, 默认拉最近 7 天 qType=1 + qType=2 研报, 每种最多 5
页（250 条）。复用 ResearchIndexer + BGE-M3 embedder + Qdrant
research_v1 collection。pyproject.toml 注册为 explain-ingest-research
全局命令。
EOF
)"
```

---

## Task 6: lazy_ingest_node 实现 + 5 单测

**目标：** 实现 lazy_ingest_node，5 个测试用例覆盖 fresh skip / stale 拉 / crawler 异常 / indexer 异常 / None 注入。

**Files:**
- Create: `src/explain_agent/graph/nodes/lazy_ingest.py`
- Create: `tests/test_node_lazy_ingest.py`

**Step 1: 写 5 个单测**

`tests/test_node_lazy_ingest.py`:

```python
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from explain_agent.graph.state import new_attribution_state


def _make_state():
    s = new_attribution_state("test")
    s["target"] = "半导体"
    s["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    return s


@pytest.mark.asyncio
async def test_lazy_ingest_skips_when_corpus_fresh():
    """MAX(fetched_at) 距 now < 3h, skip。"""
    from explain_agent.graph.nodes.lazy_ingest import lazy_ingest_node

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    # MAX(fetched_at) = 1 小时前
    mock_conn.exec_driver_sql.return_value.fetchone.return_value = (datetime.now() - timedelta(hours=1),)

    mock_crawler = MagicMock()
    mock_crawler.crawl_symbol = MagicMock(return_value=[])
    mock_indexer = MagicMock()
    mock_indexer.engine = mock_engine
    mock_indexer.index = MagicMock(return_value=0)

    out = await lazy_ingest_node(_make_state(), news_crawler=mock_crawler, news_indexer=mock_indexer)
    assert out.get("lazy_ingest_skipped") is True
    mock_crawler.crawl_symbol.assert_not_called()


@pytest.mark.asyncio
async def test_lazy_ingest_pulls_when_corpus_stale():
    """MAX(fetched_at) 距 now > 3h, 触发拉。"""
    from explain_agent.graph.nodes.lazy_ingest import lazy_ingest_node
    from explain_agent.ingest.news_crawler import NewsItem

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    # MAX(fetched_at) = 5 小时前
    mock_conn.exec_driver_sql.return_value.fetchone.return_value = (datetime.now() - timedelta(hours=5),)

    fake_item = NewsItem(
        news_id="n1", url_hash="h1", source="test",
        url="http://a.com", title="x", content="y",
        published_at=datetime(2026, 5, 10),
    )

    mock_crawler = MagicMock()
    mock_crawler.crawl_symbol = MagicMock(return_value=[fake_item])
    mock_indexer = MagicMock()
    mock_indexer.engine = mock_engine
    mock_indexer.index = MagicMock(return_value=1)

    out = await lazy_ingest_node(_make_state(), news_crawler=mock_crawler, news_indexer=mock_indexer)
    assert out.get("lazy_ingest_count") == 1
    assert out.get("lazy_ingest_skipped") is None or "lazy_ingest_skipped" not in out
    mock_crawler.crawl_symbol.assert_called_once_with("半导体")


@pytest.mark.asyncio
async def test_lazy_ingest_handles_crawler_exception():
    """crawler 抛异常 -> 静默 fallback, return {}。"""
    from explain_agent.graph.nodes.lazy_ingest import lazy_ingest_node

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchone.return_value = (datetime.now() - timedelta(hours=5),)

    mock_crawler = MagicMock()
    mock_crawler.crawl_symbol = MagicMock(side_effect=RuntimeError("network down"))
    mock_indexer = MagicMock()
    mock_indexer.engine = mock_engine

    out = await lazy_ingest_node(_make_state(), news_crawler=mock_crawler, news_indexer=mock_indexer)
    assert out == {}


@pytest.mark.asyncio
async def test_lazy_ingest_handles_indexer_exception():
    """indexer.index 抛异常 -> 静默 fallback。"""
    from explain_agent.graph.nodes.lazy_ingest import lazy_ingest_node
    from explain_agent.ingest.news_crawler import NewsItem

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchone.return_value = (datetime.now() - timedelta(hours=5),)

    fake_item = NewsItem(
        news_id="n1", url_hash="h1", source="test",
        url="http://a.com", title="x", content="y",
        published_at=datetime(2026, 5, 10),
    )

    mock_crawler = MagicMock()
    mock_crawler.crawl_symbol = MagicMock(return_value=[fake_item])
    mock_indexer = MagicMock()
    mock_indexer.engine = mock_engine
    mock_indexer.index = MagicMock(side_effect=RuntimeError("DB down"))

    out = await lazy_ingest_node(_make_state(), news_crawler=mock_crawler, news_indexer=mock_indexer)
    assert out == {}


@pytest.mark.asyncio
async def test_lazy_ingest_noop_when_crawler_or_indexer_none():
    """crawler 或 indexer 是 None 时直接 return {}, 不查 DB。"""
    from explain_agent.graph.nodes.lazy_ingest import lazy_ingest_node

    out = await lazy_ingest_node(_make_state(), news_crawler=None, news_indexer=None)
    assert out == {}

    out = await lazy_ingest_node(_make_state(), news_crawler=MagicMock(), news_indexer=None)
    assert out == {}
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_node_lazy_ingest.py -v`
Expected: ImportError（lazy_ingest 不存在）

**Step 3: 实现 lazy_ingest_node**

`src/explain_agent/graph/nodes/lazy_ingest.py`:

```python
import asyncio
from datetime import datetime, timedelta

from explain_agent.graph.state import AttributionState


async def lazy_ingest_node(
    state: AttributionState,
    news_crawler=None,
    news_indexer=None,
    threshold_hours: int = 3,
    timeout_seconds: float = 60.0,
) -> dict:
    """parse 完后检查 corpus 时效, MAX(fetched_at) 距 now 落后 > threshold_hours
    触发 akshare 增量拉。任何步骤异常都不阻塞 main graph, 失败 return {}
    让 fan_out 用 stale 数据继续。
    """
    if news_crawler is None or news_indexer is None:
        return {}

    target = state.get("target")
    time_window = state.get("time_window")
    if not target or not time_window:
        return {}

    try:
        max_fetched = await asyncio.to_thread(_get_max_fetched_at, news_indexer.engine)

        threshold = datetime.now() - timedelta(hours=threshold_hours)
        if max_fetched is not None and max_fetched >= threshold:
            return {"lazy_ingest_count": 0, "lazy_ingest_skipped": True}

        items = await asyncio.wait_for(
            asyncio.to_thread(news_crawler.crawl_symbol, target),
            timeout=timeout_seconds,
        )
        if not items:
            return {"lazy_ingest_count": 0}

        n = await asyncio.wait_for(
            asyncio.to_thread(news_indexer.index, items),
            timeout=timeout_seconds * 2,
        )
        return {"lazy_ingest_count": n}
    except (asyncio.TimeoutError, Exception):
        return {}


def _get_max_fetched_at(engine):
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT MAX(fetched_at) FROM explain_agent.explain_news_corpus"
        ).fetchone()
    return row[0] if row else None
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_node_lazy_ingest.py -v`
Expected: 5 passed

**Step 5: 跑全量回归**

Run: `uv run pytest`
Expected: 140 passed（135 + 5）

**Step 6: Commit**

```bash
git add src/explain_agent/graph/nodes/lazy_ingest.py tests/test_node_lazy_ingest.py
git commit -m "$(cat <<'EOF'
新增 lazy_ingest_node（corpus 自动保鲜）

parse 完后查 MAX(fetched_at), 距 now > 3h 触发 akshare 增量拉新闻。
60s 超时, 任何异常静默 fallback return {} 不阻塞 fan_out。
crawler / indexer 为 None 时直接 noop。覆盖 5 个 fallback case 单测。
EOF
)"
```

---

## Task 7: main_graph 集成 lazy_ingest

**目标：** main_graph 加 news_crawler / news_indexer 参数，注入 lazy_ingest 节点到拓扑（load_framework 之后、market_facts 之前）。改 test_main_graph mock。

**Files:**
- Modify: `src/explain_agent/graph/main_graph.py`
- Modify: `tests/test_main_graph.py`

**Step 1: 改 main_graph.py**

打开 `src/explain_agent/graph/main_graph.py`，做以下改动：

1. 加 import：
```python
from explain_agent.graph.nodes.lazy_ingest import lazy_ingest_node
```

2. `build_main_graph` 函数签名加两个参数（默认 None）：
```python
def build_main_graph(
    market_adapter, worker_factory, weak_llm, strong_llm, engine,
    adapter_registry: dict | None = None,
    news_crawler=None,        # 新增
    news_indexer=None,        # 新增
    on_node_event: NodeEvent | None = None,
):
```

3. 在 `async def _facts(state):` 上方加：
```python
    async def _lazy_ingest(state):
        return await lazy_ingest_node(state, news_crawler=news_crawler, news_indexer=news_indexer)
```

4. `g.add_node` 区块加：
```python
    g.add_node("lazy_ingest", _trace("lazy_ingest", _lazy_ingest))
```
位置在 `g.add_node("load_framework", ...)` 后、`g.add_node("market_facts", ...)` 之前。

5. 改边：
- 删除 `g.add_edge("load_framework", "market_facts")`
- 新增 `g.add_edge("load_framework", "lazy_ingest")`
- 新增 `g.add_edge("lazy_ingest", "market_facts")`

**Step 2: 改 test_main_graph.py**

`tests/test_main_graph.py` 需要传 `news_crawler` 和 `news_indexer` 参数。最简单是传 None，让 lazy_ingest 直接 noop：

```python
graph = build_main_graph(
    market_adapter=fake_market_adapter,
    worker_factory=fake_worker_factory,
    weak_llm=fake_weak_llm,
    strong_llm=fake_strong_llm,
    engine=mock_engine,
    # news_crawler 和 news_indexer 不传 (默认 None), lazy_ingest 自动 noop
)
```

如果当前 test_main_graph 已经包含其它断言，确认 `result.get("lazy_ingest_count")` 不存在（因为 noop return {}）。可加一行：

```python
assert "lazy_ingest_count" not in result
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_main_graph.py -v`
Expected: 1 passed

**Step 4: 跑全量回归**

Run: `uv run pytest`
Expected: 140 passed（不增加, 因为只改了 graph 集成不加新测试）

**Step 5: Commit**

```bash
git add src/explain_agent/graph/main_graph.py tests/test_main_graph.py
git commit -m "$(cat <<'EOF'
main_graph 集成 lazy_ingest_node

拓扑变更：load_framework → lazy_ingest → market_facts。新增可选
news_crawler / news_indexer 参数, 不传时 lazy_ingest 自动 noop
（兼容 Phase 2.A-2.D-2 已有的 main_graph 调用方）。集成测试更新断言
lazy_ingest_count 不在 result（noop 路径）。
EOF
)"
```

---

## Task 8: framework YAML 三维加 research_corpus

**目标：** 让 policy / industry_chain / international 三维 `data_sources` 末尾加 `research_corpus`。新增 3 个白名单测试。

**Files:**
- Modify: `frameworks/cn_equity_sector_attribution.yaml`
- Modify: `tests/test_framework_loader.py`（或新建 `tests/test_framework_research.py`）

**Step 1: 写 3 个单测**

在 `tests/test_framework_loader.py` 末尾追加（如果文件存在），或新建 `tests/test_framework_research.py`：

```python
from explain_agent.graph.framework_loader import load_framework


def test_policy_dim_includes_research_corpus():
    fw = load_framework("cn_equity_sector_attribution")
    policy = next(d for d in fw["dimensions"] if d["id"] == "policy")
    assert "research_corpus" in policy["data_sources"]


def test_industry_chain_dim_includes_research_corpus():
    fw = load_framework("cn_equity_sector_attribution")
    ic = next(d for d in fw["dimensions"] if d["id"] == "industry_chain")
    assert "research_corpus" in ic["data_sources"]


def test_international_dim_includes_research_corpus():
    fw = load_framework("cn_equity_sector_attribution")
    intl = next(d for d in fw["dimensions"] if d["id"] == "international")
    assert "research_corpus" in intl["data_sources"]
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_framework_loader.py::test_policy_dim_includes_research_corpus -v` (or new file)
Expected: AssertionError（YAML 还没改）

**Step 3: 改 YAML**

打开 `frameworks/cn_equity_sector_attribution.yaml`，把：

```yaml
- id: policy
  data_sources:
    - news_corpus
    - web_search
```
改为：
```yaml
- id: policy
  data_sources:
    - news_corpus
    - web_search
    - research_corpus
```

`industry_chain`：
```yaml
- id: industry_chain
  data_sources:
    - mysql_fundamentals
    - clickhouse_market
    - news_corpus
    - research_corpus
```

`international`：
```yaml
- id: international
  data_sources:
    - news_corpus
    - web_search
    - research_corpus
```

不动 capital_flow / sentiment_event / technical。

**Step 4: 跑测试**

Run: `uv run pytest tests/test_framework_loader.py -v`（或新文件）
Expected: 3 个新测试通过 + 已有测试也通过

Run: `uv run pytest`
Expected: 143 passed（140 + 3）

**Step 5: Commit**

```bash
git add frameworks/cn_equity_sector_attribution.yaml tests/test_framework_loader.py
git commit -m "$(cat <<'EOF'
framework YAML · policy / industry_chain / international 三维加 research_corpus

让三个分析"市场现象深层逻辑"的维度多一个权威源（行业研报 + 宏观策略）。
DimensionWorker 在 news_corpus + web_search 之外多查 research_corpus,
预期 industry_chain 维度 evidence 多 5-10 条研报标题。
capital_flow / sentiment_event / technical 不动（研报不适合这三维）。
EOF
)"
```

---

## Task 9: REPL + smoke 脚本注入 news_crawler / news_indexer + research adapter

**目标：** 让生产 REPL 和 smoke 脚本都构造好 lazy_ingest 需要的 NewsCrawler / NewsIndexer，并注入 ResearchCorpusAdapter 到 registry。

**Files:**
- Modify: `src/explain_agent/cli/repl/loop.py`
- Modify: `scripts/run_main_graph_smoke.py`

**Step 1: 改 REPL `_build_environment()`**

打开 `src/explain_agent/cli/repl/loop.py`，在 `_build_environment` 函数中：

1. 顶部 import 块加：
```python
    from explain_agent.adapters.research_corpus import ResearchCorpusAdapter
    from explain_agent.ingest.news_crawler import AkshareNewsCrawler
    from explain_agent.ingest.news_indexer import NewsIndexer
    from explain_agent.ingest.tagger import NewsTagger
```

2. `registry` 字典构造之后加 research_corpus adapter：
```python
    registry["research_corpus"] = ResearchCorpusAdapter(
        qdrant=get_qdrant_client(), embedder=embedder, engine=explain_engine,
    )
```

3. 在 `snapshot_store = ...` 之后加 NewsCrawler + NewsIndexer（带 snapshot_store）：
```python
    news_crawler = AkshareNewsCrawler()
    news_indexer = NewsIndexer(
        engine=explain_engine,
        tagger=NewsTagger(),
        embedder=embedder,
        qdrant=get_qdrant_client(),
        snapshot_store=snapshot_store,
    )
```

4. `build_main_graph(...)` 调用加两个参数：
```python
    graph = build_main_graph(
        market_adapter=registry["clickhouse_market"],
        worker_factory=worker_factory,
        weak_llm=weak, strong_llm=strong, engine=explain_engine,
        adapter_registry=registry,
        news_crawler=news_crawler,        # 新增
        news_indexer=news_indexer,        # 新增
        on_node_event=on_node_event,
    )
```

**Step 2: 改 smoke 脚本**

打开 `scripts/run_main_graph_smoke.py`，做完全相同的改动（imports + registry + news_crawler/indexer 注入 + build_main_graph 参数）。

**Step 3: 验证 REPL 测试不破**

Run: `uv run pytest tests/test_repl_loop.py tests/test_repl_integration.py -v`
Expected: 5 passed

**Step 4: 跑全量回归**

Run: `uv run pytest`
Expected: 143 passed

**Step 5: Commit**

```bash
git add src/explain_agent/cli/repl/loop.py scripts/run_main_graph_smoke.py
git commit -m "$(cat <<'EOF'
REPL + smoke 注入 lazy_ingest 依赖 + research_corpus adapter

_build_environment 与 smoke 脚本同步：
- registry 加 research_corpus（ResearchCorpusAdapter）
- 构造 AkshareNewsCrawler + NewsIndexer(snapshot_store=...) 传给
  build_main_graph 的 news_crawler / news_indexer 参数
- lazy 拉的新闻顺手落 snapshot（对齐 Phase 2.D-1 设计）
EOF
)"
```

---

## Task 10: 端到端 smoke + README 更新 + push

**目标：** 跑真实 smoke 验证 lazy_ingest 工作 + research_corpus 被 industry_chain 维度采纳；更新 README 加 Phase 2.D-3 验收清单；push origin。

**Files:**
- Modify: `README.md`
- 跑 CLI + smoke 手动验收

**Step 1: 先跑一次 explain-ingest-research**

```bash
uv run explain-ingest-research --days 7 --max-pages-per-type 3
```

期望：
- qType=1 新增几十到几百条
- qType=2 新增几十条
- DB 验证：
  ```bash
  uv run python -c "
  from explain_agent.db.mysql import get_engine
  engine = get_engine('explain')
  with engine.begin() as conn:
      n = conn.exec_driver_sql('SELECT COUNT(*) FROM explain_agent.explain_research_corpus').fetchone()[0]
      print('research_corpus 总条数:', n)
  "
  ```

**Step 2: 跑端到端 smoke**

```bash
PYTHONUNBUFFERED=1 uv run python -u scripts/run_main_graph_smoke.py "为什么半导体板块今天涨" 2>&1 | tee /tmp/smoke_2d3.log
```

**验收点**（必须全部满足）：
1. log 中能看到 `▶ lazy_ingest ...` + `✓ lazy_ingest (...)` 节点（触发或 skip 都行）
2. 总耗时 ≤ 7 min
3. evidence 中**至少 1 条** `source="research_corpus"` 出现在 industry_chain 或 policy 或 international 维度
4. connection_threads ≥ 1 条（不退化）
5. 单测 143 passed 不破

验证 #3：smoke 输出会打印 evidence_id；可以查 MySQL：

```bash
uv run python -c "
import json
from explain_agent.db.mysql import get_engine
engine = get_engine('explain')
with engine.begin() as conn:
    # 取最新 session
    row = conn.exec_driver_sql(
        'SELECT session_id, tree_json FROM explain_agent.explain_evidence_tree ORDER BY created_at DESC LIMIT 1'
    ).fetchone()
    print('session:', row[0])
    tree = json.loads(row[1])
    research_evidence = []
    for dim_id, r in tree.get('dimension_results', {}).items():
        for e in r.get('evidence', []):
            if e.get('source') == 'research_corpus':
                research_evidence.append((dim_id, e.get('title', '')[:50]))
    print(f'research_corpus evidence: {len(research_evidence)} 条')
    for dim, title in research_evidence[:5]:
        print(f'  [{dim}] {title}')
"
```

期望：`research_corpus evidence: N 条`，N ≥ 1。

如果 N = 0，停下报告原因（可能 Qdrant filter / embedding / framework YAML 接入有问题）。

**Step 3: 更新 README**

在 README 末尾追加 Phase 2.D-3 验收清单：

```markdown

## Phase 2.D-3 验收状态

- [x] `lazy_ingest_node` corpus 自动保鲜（MAX(fetched_at) < now-3h 触发, 60s 超时, 异常静默 fallback）
- [x] `EastmoneyResearchCrawler` 直接调东财底层 reportapi (qType=1 行业 + qType=2 宏观)
- [x] `ResearchIndexer` + Qdrant research_v1 collection + snapshot_id=NULL（研报不落 snapshot）
- [x] `ResearchCorpusAdapter` 向量检索 + MySQL 补全 title/机构/行业 → Evidence(source_type="research")
- [x] `explain-ingest-research` CLI 手动 batch 入口
- [x] framework YAML 三维（policy / industry_chain / international）加 research_corpus
- [x] main_graph 拓扑：load_framework → lazy_ingest → market_facts
- [x] 全部单测通过（143 passed, Phase 2.A/2.B/2.C/2.D-1/2.D-2 全部回归不破）
- [x] 端到端 smoke：lazy_ingest 节点工作，industry_chain 维度至少 1 条 research_corpus evidence
```

**Step 4: Commit + push**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Phase 2.D-3 完工 · README 加验收清单

Lazy News Ingest 让 corpus 自动保鲜, 用户不再需要记得手动 ingest。
行业 + 宏观研报填补了 explain_research_corpus dormant 表, policy /
industry_chain / international 三维多了一个权威源（研报原文标题
作为 evidence, 比新闻媒体二手稿更直接）。下一步是 Phase 2.D-4
brainstorm 政策爬虫。
EOF
)"

git push origin master
```

---

## 完成后

Phase 2.D-3 完成后的产出：

- ✅ `lazy_ingest_node`（agent 同步 self-healing 拉 news_corpus）
- ✅ `EastmoneyResearchCrawler` + `ResearchIndexer` + `ResearchCorpusAdapter`
- ✅ `explain-ingest-research` CLI
- ✅ framework YAML 三维接入 research_corpus
- ✅ State schema 扩展（lazy_ingest_count / lazy_ingest_skipped）
- ✅ Lazy 拉的新闻顺手落 snapshot（对齐 Phase 2.D-1）
- ✅ 143 单测通过, 端到端 smoke ≤ 7 min

**下一步**：Phase 2.D-4 brainstorm（政策爬虫 C.2）。关键决策点：
- 数据源选择（自爬政府官网 / 第三方 API / Tavily 兜底）
- 是否需要正文（公告/通知有清晰结构，研报只有 PDF 摘要不同）
- ingest 节奏（政策一周几条，不需要 lazy，定时 batch 即可）
