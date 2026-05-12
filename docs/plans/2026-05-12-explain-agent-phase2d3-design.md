# Explain Agent · Phase 2.D-3 设计文档

> Corpus 表自动化：Lazy News Ingest（self-healing 同步链路）+ 行业/宏观研报爬虫（独立 CLI batch）。让 agent 跑归因时不再依赖用户记得手动 ingest，并填补 dormant 的 `explain_research_corpus` 表。

**前置：** Phase 2.D-2 完工（commit `9561a4a`），fan_out 真并发 + strong LLM 链路并发 + 漂移率标注闭环就绪，127 单测通过。

**Phase 2.D-3 = 三件互不依赖的工作打包：**
- **B：Lazy News Ingest**（agent 同步路径，self-healing 拉 news_corpus）
- **C.1：研报爬虫**（独立 CLI batch，直接调东财底层 API 拉 qType=1 行业 + qType=2 宏观）
- **C.3：framework YAML 接入 research_corpus**（policy / industry_chain / international 三维）

**Phase 2.D-3 不做的：**
- ❌ C.2 政策爬虫（推到 Phase 2.D-4，数据源决策复杂，需独立 brainstorm）
- ❌ 研报 PDF 全文索引（YAGNI，title + 机构已经够密度）
- ❌ Lazy 拉研报（研报变化慢，每天/每周 batch 即可）
- ❌ 个股研报（不符合"市场现象深层逻辑"目标）
- ❌ schema migration（research_corpus 表 schema 已支持，不需要改）
- ❌ per-target 精准 lazy 判断（用全局 `MAX(fetched_at)`）

---

## 设计哲学

**第一性原理**：用户的真实痛点是"每次跑 explain 前要记得手动 `explain-ingest-news`"。如果 corpus 一周没更新，所有归因都基于过期数据。本期一刀切解决：

1. **News**：agent 自动检测 + 同步拉（用户感知不到，多等 30-60s 而已）
2. **Research**：从未有数据 → 第一次跑 `explain-ingest-research` 后 research_corpus 表填满 → policy/industry_chain/international 三维多一个权威源

**关键发现**：当前 framework YAML 中 policy 维度的 `data_sources` 是 `[news_corpus, web_search]`，**没用 `policy_corpus`**。也就是 policy 维度的"政策"证据全来自财经新闻媒体（虎嗅/东财/新浪等的报道）的二手稿。Phase 2.D-3 的研报接入正好补救：行业研报 + 宏观策略报告里有分析师的核心结论（如"精准有效实施适度宽松的货币政策"），比新闻媒体二手稿更权威。

---

## 整体架构

### main_graph 新拓扑

```
当前 (Phase 2.D-2)：
parse → router → load_framework → market_facts → fan_out → synth →
       → dynamic_sub → connection_explorer → report → persist

Phase 2.D-3：
parse → router → load_framework → lazy_ingest (新增) → market_facts →
       → fan_out → synth → dynamic_sub → connection_explorer → report → persist
```

为什么 `lazy_ingest` 放在 `load_framework` 之后、`market_facts` 之前：
- 需要 framework 决定 target 是否被 6 维消费（如果 framework 没匹配，直接 skip）
- 在 market_facts 前，让 fan_out 能用上 fresh 数据

### 新增组件清单

```
src/explain_agent/
  ingest/
    research_crawler.py       (新)  EastmoneyResearchCrawler
    research_indexer.py       (新)  ResearchIndexer
  adapters/
    research_corpus.py        (新)  ResearchCorpusAdapter
  graph/nodes/
    lazy_ingest.py            (新)  lazy_ingest_node
  cli/
    ingest_research.py        (新)  explain-ingest-research CLI
```

---

## B 部分：Lazy News Ingest

### B.1 节点核心逻辑

```python
# src/explain_agent/graph/nodes/lazy_ingest.py
from datetime import datetime, timedelta
import asyncio

from explain_agent.graph.state import AttributionState


async def lazy_ingest_node(
    state: AttributionState,
    news_crawler=None,
    news_indexer=None,
    threshold_hours: int = 3,
    timeout_seconds: float = 60.0,
) -> dict:
    """parse 完后检查 corpus 时效, 落后 > threshold_hours 触发 akshare 增量 ingest。
    任何步骤异常都不阻塞 main graph, 失败 return {} 让 fan_out 用 stale 数据继续。
    """
    if news_crawler is None or news_indexer is None:
        return {}

    target = state.get("target")
    time_window = state.get("time_window")
    if not target or not time_window:
        return {}

    try:
        # 1. 查 corpus 全局最新一条 fetched_at（即上次 ingest 时间）
        max_fetched = await asyncio.to_thread(_get_corpus_max_fetched_at, news_indexer.engine)

        # 2. 距 now 落后超过 threshold_hours 才触发
        threshold = datetime.now() - timedelta(hours=threshold_hours)
        if max_fetched is not None and max_fetched >= threshold:
            return {"lazy_ingest_count": 0, "lazy_ingest_skipped": True}

        # 3. akshare 增量拉
        items = await asyncio.wait_for(
            asyncio.to_thread(news_crawler.crawl_symbol, target),
            timeout=timeout_seconds,
        )
        if not items:
            return {"lazy_ingest_count": 0}

        # 4. index（去重 + embedding + INSERT + snapshot）
        n = await asyncio.wait_for(
            asyncio.to_thread(news_indexer.index, items),
            timeout=timeout_seconds * 2,
        )
        return {"lazy_ingest_count": n}
    except (asyncio.TimeoutError, Exception):
        return {}  # 静默失败, 不阻塞 fan_out


def _get_corpus_max_fetched_at(engine):
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT MAX(fetched_at) FROM explain_agent.explain_news_corpus"
        ).fetchone()
    return row[0] if row else None
```

### B.2 触发条件设计

**用 `MAX(fetched_at)` 而非 `MAX(published_at)`**：

| 字段 | 含义 | 问题 |
|---|---|---|
| `published_at` | 新闻发布时间（虎嗅写稿那一刻） | akshare 返回的最新新闻往往就是几小时前发布的，拉了一遍 published_at 仍然落后 now 几小时，**下次问问题再次触发**，thrashing |
| `fetched_at` | ingest 写库那一刻 | 拉完后 `MAX(fetched_at) = NOW()`，连续问问题不会重复触发 |

**3h 阈值**：每天最多触发 ~5 次（开盘 / 午休 / 收盘 / 晚饭 / 睡前），可接受。

### B.3 main_graph 集成

```python
# src/explain_agent/graph/main_graph.py
def build_main_graph(
    market_adapter, worker_factory, weak_llm, strong_llm, engine,
    adapter_registry=None,
    news_crawler=None,       # 新增
    news_indexer=None,       # 新增
    on_node_event=None,
):
    # ...

    async def _lazy_ingest(state):
        return await lazy_ingest_node(state, news_crawler=news_crawler, news_indexer=news_indexer)

    g.add_node("lazy_ingest", _trace("lazy_ingest", _lazy_ingest))
    g.add_edge("load_framework", "lazy_ingest")
    g.add_edge("lazy_ingest", "market_facts")
    # 删掉原 load_framework → market_facts 这条边
```

### B.4 REPL / smoke 注入

REPL `_build_environment()` 和 `scripts/run_main_graph_smoke.py` 都加：

```python
news_crawler = AkshareNewsCrawler()
news_indexer = NewsIndexer(
    engine=explain_engine, tagger=NewsTagger(),
    embedder=embedder, qdrant=get_qdrant_client(),
    snapshot_store=snapshot_store,
)
graph = build_main_graph(..., news_crawler=news_crawler, news_indexer=news_indexer)
```

**顺手解决**：lazy_ingest 的 NewsIndexer 注入了 snapshot_store，等于 lazy 拉的新闻也会落 snapshot。

### B.5 State schema 扩展

加 2 个可选字段到 `AttributionState`：

```python
class AttributionState(TypedDict, total=False):
    # ...已有
    lazy_ingest_count: int       # 本次 lazy 拉新增的新闻条数
    lazy_ingest_skipped: bool    # 是否跳过（corpus 已 fresh）
```

---

## C.1 部分：研报爬虫

### C.1.1 数据源

直接调东财底层 API：`https://reportapi.eastmoney.com/report/list`

| qType | 类型 | 样例 |
|---|---|---|
| **1** | **行业研报** | "银行行业快评报告：精准有效实施适度宽松的货币政策" |
| **2** | **宏观策略** | "4 月通胀数据跟踪：输入性通胀走强，内生动能边际改善" |

样本统计：qType=1 历史 ~1.6M 条，每天新增 50-200 条。

字段：`title / orgSName / publishDate / industryName / researcher / infoCode`。**没有正文**，但 title 已经是核心结论。

### C.1.2 ResearchItem 数据模型

```python
class ResearchItem(BaseModel):
    research_id: str
    url_hash: str              # sha256(infoCode)
    source: str                # "东方财富研报"
    url: str | None            # 详情页 URL
    title: str
    institution: str           # "万联证券"
    industry: str              # "银行Ⅱ" (qType=1) / "宏观策略" (qType=2)
    published_at: datetime
    q_type: int                # 1 / 2
    researcher: str
    info_code: str
```

### C.1.3 EastmoneyResearchCrawler

```python
class EastmoneyResearchCrawler:
    BASE_URL = "https://reportapi.eastmoney.com/report/list"

    def crawl(
        self,
        q_type: Literal[1, 2],
        begin: date,
        end: date,
        page_size: int = 50,
        max_pages: int = 5,
    ) -> list[ResearchItem]:
        # 翻页拉取, 解析每条记录, sha256(infoCode) 做 url_hash
```

### C.1.4 ResearchIndexer（仿 NewsIndexer）

```python
class ResearchIndexer:
    def __init__(self, engine, embedder, qdrant, collection="research_v1"):
        ...

    def _filter_existing(self, items) -> list[ResearchItem]:
        # 按 url_hash 查重

    def _to_embedding_text(self, item: ResearchItem) -> str:
        # 高密度短文本: "行业研报标题 | 机构 | 行业"
        return f"{item.title} | {item.institution} | {item.industry}"

    def index(self, items) -> int:
        # 1. 去重
        # 2. embedding
        # 3. INSERT explain_research_corpus（snapshot_id=NULL, abstract=title）
        # 4. Qdrant upsert（research_v1 collection）
```

**研报不集成 SnapshotStore**：没有 PDF 全文，title 已在 corpus 表，没必要单独 snapshot。

### C.1.5 ResearchCorpusAdapter（仿 NewsCorpusAdapter）

```python
class ResearchCorpusAdapter:
    name = "research_corpus"

    def __init__(self, qdrant, embedder, engine, collection="research_v1"):
        ...

    async def query(self, q: AdapterQuery) -> list[Evidence]:
        # 1. embed q.target + q.keywords
        # 2. Qdrant filter by published_at in time_window
        # 3. SELECT title, institution, industry, url FROM explain_research_corpus
        # 4. 包装成 Evidence(source="research_corpus", source_type="research", snippet=title)
```

**新 source_type "research" 已在 [core/types.py](src/explain_agent/core/types.py) 的 `SourceType` Literal 中**，无需改。

### C.1.6 CLI 入口

```python
# cli/ingest_research.py
@app.command()
def main(
    days: int = typer.Option(7),
    q_types: list[int] = typer.Option([1, 2]),
    max_pages_per_type: int = typer.Option(5),
):
    crawler = EastmoneyResearchCrawler()
    indexer = ResearchIndexer(...)
    for qt in q_types:
        items = crawler.crawl(qt, begin, end, max_pages=max_pages_per_type)
        n = indexer.index(items)
        print(f"qType={qt}: 新增 {n} 条")
```

`pyproject.toml`：
```toml
[project.scripts]
explain-ingest-research = "explain_agent.cli.ingest_research:app"
```

---

## C.3 部分：framework YAML 接入

### 改动 3 个维度的 `data_sources`

```yaml
- id: policy
  data_sources:
    - news_corpus
    - web_search
    - research_corpus       # 新增（qType=2 宏观策略对政策维度高价值）

- id: industry_chain
  data_sources:
    - mysql_fundamentals
    - clickhouse_market
    - news_corpus
    - research_corpus       # 新增（qType=1 行业研报核心场景）

- id: international
  data_sources:
    - news_corpus
    - web_search
    - research_corpus       # 新增（qType=2 海外宏观策略）
```

### 不动的维度

| 维度 | 原因 |
|---|---|
| **capital_flow** | 研报不涉及具体资金流向 |
| **sentiment_event** | 研报滞后于事件（往往 1-3 天后才发） |
| **technical** | 研报不讨论技术形态 |

---

## 失败模式与回退

| 故障 | 回退 |
|---|---|
| 东财 API 失败 | crawler 返回 `[]`, indexer 不写入, CLI 打印 0 条新增 |
| qType 响应 JSON 结构变化 | crawler 单条解析失败 → skip 这条继续, 整体不抛 |
| `MAX(fetched_at)` 查询失败 | lazy_ingest_node 静默 return `{}` |
| akshare 抛异常 / 60s 超时 | lazy_ingest_node 静默 return `{}` |
| `NewsIndexer.index` 抛异常 / 120s 超时 | lazy_ingest_node 静默 return `{}` |
| `ResearchCorpusAdapter.query` 抛异常 | 返回 `[]` |
| 研报表为空（首次安装） | adapter 返回 `[]`, 三维度回退到 news_corpus + web_search |

核心原则：本期所有新增组件失败都不阻塞 fan_out，只是少一个数据源。

---

## 测试策略

| 测试文件 | 用例数 | 内容 |
|---|---|---|
| `tests/test_research_crawler.py` | 3 | mock httpx response 验证 qType=1/2 解析 / 翻页 / 空容错 |
| `tests/test_research_indexer.py` | 2 | mock engine + qdrant 验证 INSERT + upsert / url_hash 去重 |
| `tests/test_research_corpus_adapter.py` | 2 | mock qdrant.query_points / filter by time_window |
| `tests/test_node_lazy_ingest.py` | 5 | fresh skip / stale 拉 / crawler 异常 fallback / indexer 异常 fallback / None 注入 noop |
| `tests/test_main_graph.py` | 改 1 | fake crawler/indexer 注入, lazy_ingest 节点跑通 |
| `tests/test_framework_loader.py` | +3 | 验证 policy/industry_chain/international 含 research_corpus |

**新单测总计：~17 个**，全量测试 127 → 144。

---

## 验收标准

### 单测

全部通过（144 passed），含 lazy_ingest 5 个 fallback case 护栏。

### CLI 验收

```bash
uv run explain-ingest-research --days 7
# 预期：qType=1 新增 N1 条，qType=2 新增 N2 条
# DB 查 explain_research_corpus 应有 N1+N2 条新记录

uv run explain-ingest-research --days 7   # 第二次跑
# 预期：去重后新增 0 条
```

### 端到端 smoke

跑同样 `"为什么半导体板块今天涨"`：

| 指标 | 2.D-2 基线 | 2.D-3 目标 |
|---|---|---|
| 总耗时 | 5.8 min | **≤ 7 min**（多 lazy_ingest ~30-60s + research_corpus 检索几秒） |
| **lazy_ingest** 触发？ | — | ✅ 看 `MAX(fetched_at)` |
| industry_chain evidence | 20 条 | **≥ 25 条** |
| policy evidence | 77 条 | **≥ 80 条** |
| narrative_claims 引用 research_corpus | 无 | **≥ 1 条** |
| connection_threads | 3 | 不退化 |

**通过条件**：
1. 全部单测通过
2. CLI 跑通，DB 有研报数据
3. smoke 跑出 lazy_ingest 节点（触发或 skip 都算）
4. industry_chain 维度 evidence 至少 1 条 `source="research_corpus"`

---

## 任务总览

| # | 任务 | 预估 |
|---|---|---|
| 1 | `EastmoneyResearchCrawler` + 单测（3 个） | 60 min |
| 2 | `ResearchIndexer` + 单测（2 个） | 50 min |
| 3 | `ResearchCorpusAdapter` + 单测（2 个） | 50 min |
| 4 | `explain-ingest-research` CLI + 跑一次真实 ingest 验证 | 30 min |
| 5 | `lazy_ingest_node` 实现 + 单测（5 个） | 90 min |
| 6 | `main_graph` 集成 lazy_ingest + 改 test_main_graph mock | 30 min |
| 7 | REPL `_build_environment` + smoke 脚本注入 news_crawler/news_indexer + research_corpus adapter | 30 min |
| 8 | framework YAML 加 research_corpus + 单测（3 个） | 20 min |
| 9 | State schema 加 lazy_ingest_count / lazy_ingest_skipped 字段 | 10 min |
| 10 | 端到端 smoke + README 更新验收清单 | 60 min |

**合计：约 7 小时纯开发，预计 1 个工作日完成。**

---

## 完成后

Phase 2.D-3 完成后的产出：

- ✅ `lazy_ingest_node`：corpus 自动保鲜（3h 阈值，self-healing）
- ✅ `EastmoneyResearchCrawler` + `ResearchIndexer`：行业 + 宏观研报入库
- ✅ `ResearchCorpusAdapter`：policy/industry_chain/international 三维多一个权威源
- ✅ `explain-ingest-research` CLI（手动 batch + 未来可定时跑）
- ✅ Lazy 拉的新闻顺手落 snapshot（对齐 Phase 2.D-1 设计）
- ✅ State schema 扩展（lazy_ingest_count / lazy_ingest_skipped）
- ✅ 全部单测通过（144 passed, 含 Phase 2.A-2.D-2 回归）

**下一步**：Phase 2.D-4 brainstorm（政策爬虫 C.2），数据源决策：自爬政府官网 vs 第三方 API vs Tavily 兜底。
