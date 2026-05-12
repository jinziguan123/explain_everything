# Explain Agent · Phase 2.D-1 设计文档

> connection_explorer 节点 + Tavily web search（白名单 4 处）+ 永久快照三件事打包做。让 agent 在 6 维核心之外有"延伸思考"，并让所有 evidence 都有不会失效的快照可追溯。

**前置：** Phase 2.C 已完工（commit `b8b41c9`），CLI REPL + Followup 已可用。Phase 3 路线图占位文档（commit `934f9d6`）确认 connection_explorer 是 Phase 3 前置。

**Phase 2.D 拆为 -1 / -2 两期：**
- **-1（本期，本文档）**：connection_explorer + web search + 永久快照
- **-2（后续，独立 brainstorm）**：fan_out 性能优化 + Lazy News Ingest

---

## 设计哲学

三件事看似独立，实际**一根线串起来**：

```
   connection_explorer 提议延伸议题
         ↓
   强模型自决: need_web_search?
         ↓ (true)
   WebSearchAdapter (Tavily) 检索
         ↓
   Tavily 返回 content
         ↓
   SnapshotStore 落盘 (与 news_corpus 同 schema)
         ↓
   Evidence 入证据池, citation 可追溯
```

新爬 news 也同样过 SnapshotStore，统一存储。

---

## 整体架构

### main_graph 新拓扑

```
parse → router → load_framework → market_facts → fan_out → synth →
       dynamic_sub → connection_explorer (新增) → report → persist
                            ↓
                    可选触发 web_search (Tavily)
```

### 数据流变化

**Phase 2.C**：

```
6 维 worker → synthesizer → dynamic_sub → report (含 narrative + 6 维报告)
```

**Phase 2.D-1**：

```
6 维 worker (policy/sentiment/international 可触发 web search)
   → synthesizer
   → dynamic_sub
   → connection_explorer (≤3 延伸议题, 强模型自决是否查 web)
   → report (含 narrative + 6 维报告 + 延伸思考段落)
```

---

## 设计决策（15 个）

### A. connection_explorer 节点

| # | 决策 | 值 |
|---|---|---|
| 1 | 位置 | 独立节点，`dynamic_sub → connection_explorer → report` |
| 2 | 提议数量上限 | ≤3 |
| 3 | 深度 | 1（不递归追问） |
| 4 | 置信度自评阈值 | ≥3/5 才保留 |
| 5 | 新颖度检查 | LLM 自评 `overlap_with_main_dims: bool`，True 砍掉 |
| 6 | 单议题 web 返回 | 5 条 |
| 7 | 单议题回答字数 | 100-200 字 |
| 8 | 失败 fallback | 整个节点失败 → 跳过，main report 正常输出 |
| 9 | 输出形态 | "延伸思考"段落 + `connection_threads` state 字段 |

### B. Web Search

| # | 决策 | 值 |
|---|---|---|
| 10 | Provider | Tavily（`TAVILY_API_KEY`，免费 1000/月） |
| 11 | 启用范围 | policy / sentiment_event / international 三维 + connection_explorer |
| 12 | 触发条件 | 强模型自决（输出 `need_web_search: bool`） |

### C. 永久快照

| # | 决策 | 值 |
|---|---|---|
| 13 | 存储位置 | 本地磁盘 `{SNAPSHOT_DIR}/{yyyy/mm/dd}/{snapshot_id}.txt` |
| 14 | 内容格式 | 仅清洗后正文（`trafilatura` 提取） |
| 15 | 触发时机 | 新爬即存，**不回填** 178 条历史；Tavily 结果同等处理 |

---

## State Schema 变更

```python
class ConnectionThread(TypedDict):
    title: str                  # "美国 BIS 制裁 HBM 进展"
    hypothesis: str             # "为什么这是值得延伸的方向"
    content: str                # 100-200 字回答
    evidence_ids: list[str]
    source: Literal["local", "web", "mixed"]
    confidence: int             # 1-5

class AttributionState(TypedDict, total=False):
    # ...已有字段不动
    connection_threads: list[ConnectionThread]
```

`new_attribution_state` 默认值新增 `connection_threads: []`。

---

## 组件设计

### 1. SnapshotStore（新增）

```python
# src/explain_agent/storage/snapshot.py
class SnapshotStore:
    def __init__(self, base_dir: Path, engine):
        self.base_dir = base_dir  # SNAPSHOT_DIR
        self.engine = engine

    def save(self, content: str, content_type: str = "news") -> str:
        """返回 snapshot_id，已落盘 + 已写 explain_snapshot_blob。"""

    def load(self, snapshot_id: str) -> str | None:
        """根据 snapshot_id 读回正文。"""
```

- `save()` 内部：生成 snapshot_id → 写文件 `{base_dir}/{yyyy/mm/dd}/{snapshot_id}.txt` → INSERT `explain_snapshot_blob`（snapshot_id, content_type, storage_path, size_bytes, created_at）
- 路径用日期分目录，避免单目录文件过多

### 2. WebSearchAdapter（新增）

```python
# src/explain_agent/adapters/web_search.py
class WebSearchAdapter:
    name = "web_search"

    def __init__(self, tavily_api_key: str, snapshot_store: SnapshotStore):
        self.tavily = TavilyClient(api_key=tavily_api_key)
        self.snapshot_store = snapshot_store

    async def query(self, q: AdapterQuery) -> list[Evidence]:
        # 调 tavily.search(query=" ".join([q.target] + q.keywords),
        #                  max_results=5, search_depth="basic")
        # 每条结果: snapshot_id = self.snapshot_store.save(content, "web_search")
        # 返回 Evidence(source="web_search", source_type="news", snapshot_id=..., url=...)
```

- 接入 registry 与其他 4 个 Adapter 同形
- failures (network / api key 错) 不抛，返回 `[]`

### 3. framework YAML 改造

`frameworks/cn_equity_sector_attribution.yaml` 三个维度加 web_search：

```yaml
dimensions:
  - id: policy
    data_sources:
      - news_corpus
      - web_search          # 新增
    # ...

  - id: sentiment_event
    data_sources:
      - news_corpus
      - web_search          # 新增
    # ...

  - id: international
    data_sources:
      - news_corpus
      - web_search          # 新增
    # ...
```

industry_chain / capital_flow / technical **不动**（这三个用结构化数据更可靠）。

### 4. connection_explorer 节点（新增）

```python
# src/explain_agent/graph/nodes/connection_explorer.py
async def connection_explorer_node(
    state: AttributionState,
    llm: LLMClient,
    adapter_registry: dict,
    web_search_adapter,
) -> dict:
    # 1. 强模型阅读 6 维 evidence_tree + dim_reports, 提议 ≤3 个延伸议题
    #    输出 JSON: {"threads": [{"title": ..., "hypothesis": ..., "need_web_search": bool,
    #                              "confidence": 1-5, "overlap_with_main_dims": bool}]}
    # 2. 过滤: overlap_with_main_dims=True 或 confidence<3 → 砍
    # 3. 对每个保留的议题:
    #    - need_web_search=True → 调 web_search_adapter
    #    - 否则 → 调 news_corpus
    #    - 强模型基于检索结果写 100-200 字回答, 标 evidence_ids
    # 4. 返回 {"connection_threads": [...]}
    # 5. 任何步骤异常 → return {"connection_threads": []} 不阻塞
```

### 5. report_builder 改造

在维度报告之后追加"延伸思考"段落渲染。`dimension_reports` 字段不变，新增一个 `connection_section` 字段：

```python
# 现有逻辑保持
dim_reports = {dim_id: _rewrite_dim_report(...) for ...}

# 新增延伸思考段落
threads = state.get("connection_threads") or []
connection_section = _render_connection_section(threads)  # 仅格式化, 不调 LLM

# 返回中追加
return {
    ...原有字段,
    "connection_section": connection_section,
}
```

REPL 在打印 main report 末尾追加 `connection_section`。

### 6. persist 改造

`persist_node` 的 `tree_json` 字典加上 `connection_threads`：

```python
tree = {
    ...原有字段,
    "connection_threads": state.get("connection_threads", []),
    "connection_section": state.get("connection_section", ""),
}
```

### 7. ingest pipeline 改造

修改新闻采集脚本（`src/explain_agent/ingest/news_crawler.py` 或类似入口），新闻 fetch 后立刻：

```python
content = trafilatura.extract(html)  # 清洗
snapshot_id = snapshot_store.save(content, "news")
# 写 explain_news_corpus 时填上 snapshot_id 字段
```

---

## 失败模式与回退

| 故障 | 回退 |
|---|---|
| Tavily API 不通 / key 错 | `WebSearchAdapter.query` 返回 `[]`；触发 web search 的维度得不到 web 证据，本地证据仍在 |
| `trafilatura.extract` 失败 | snapshot 跳过（不写 blob），ingest 继续，corpus 表 `snapshot_id IS NULL` |
| `connection_explorer` 强模型 JSON 解析失败 | 返回 `{"connection_threads": []}`，main report 正常 |
| 磁盘满 | `SnapshotStore.save` 抛异常 → ingest pipeline catch 后 `snapshot_id=None` 继续 |

---

## 测试策略

### 单测

- `tests/test_snapshot_store.py`：save/load 往返、按日期分目录、blob 表插入、失败容错
- `tests/test_web_search_adapter.py`：mock TavilyClient 验证返回 Evidence、snapshot_id 关联、API 错误返回 []
- `tests/test_connection_explorer.py`：mock 强模型返回不同 JSON 形态（包含 / 不含 web_search 触发 / 0 个 thread / >3 自动截断）
- `tests/test_node_report_builder.py`：扩展 1 用例，验证 `connection_section` 含 thread title

### 集成测试

- `tests/test_main_graph.py`：`fake_strong_llm.side_effect` 增加 connection_explorer 的 1 次调用，verify `connection_threads` 在 result 中
- `tests/test_node_persist.py`：扩展 1 用例验证 tree_json 含 `connection_threads`

### 端到端 smoke

- 复用 `scripts/run_repl_smoke.py` 或新增 `scripts/run_main_graph_smoke.py` 跑同一问题，对比 Phase 2.C vs 2.D-1：
  - tree_json 含 `connection_threads`
  - 延伸思考段落至少 1 个 thread
  - 至少 1 条 evidence 有 snapshot_id（验证 SnapshotStore 工作）

---

## 配置变更

### `.env` 新增

```
TAVILY_API_KEY=tvly-xxxxx
# SNAPSHOT_DIR 已有, 复用
```

### 依赖新增

```
uv add tavily-python trafilatura
```

---

## 任务总览

| # | 任务 | 预估 |
|---|---|---|
| 1 | 添加 `tavily-python` + `trafilatura` 依赖 | 15 min |
| 2 | SnapshotStore 实现 + 单测 | 1 h |
| 3 | WebSearchAdapter (Tavily) + 单测 | 1 h |
| 4 | ingest pipeline 集成 SnapshotStore | 1 h |
| 5 | framework YAML 三个维度加 web_search | 15 min |
| 6 | State schema: ConnectionThread + `connection_threads` | 20 min |
| 7 | `connection_explorer_node` 实现 + 单测 | 2 h |
| 8 | main_graph 接入 connection_explorer | 30 min |
| 9 | report_builder 追加 `connection_section` | 30 min |
| 10 | persist 节点把 `connection_threads` 写 tree_json | 15 min |
| 11 | 集成测试 + 端到端 smoke 验收 | 1 h |

**合计：约 8 小时纯开发，预计 1-2 个工作日完成。**

---

## 完成后

Phase 2.D-1 完成后的产出：

- ✅ `SnapshotStore`（本地磁盘 + MySQL 指针表）落地
- ✅ 新爬新闻全部有 `snapshot_id`，citation 兜底可用
- ✅ `WebSearchAdapter`（Tavily）接入，policy/sentiment/international 三维 + connection_explorer 可用
- ✅ `connection_explorer` 节点（≤3 延伸议题，强模型自决 web，置信度门控）
- ✅ "延伸思考"段落在 main report 末尾呈现
- ✅ State schema 扩展 + tree_json 落库

**下一步：进入 Phase 2.D-2 brainstorm**（fan_out 性能优化 + Lazy News Ingest），等 -1 跑通 + 用户日常使用一段时间后再做。

**Phase 3 路线图同步更新**：Phase 3-A 的 connection_explorer 节点骨架本期已完工，Phase 3-A 启动条件改为"-1 跑出 20-30 个真实案例的漂移率标注"。
