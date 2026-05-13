# Explain Agent

A 股板块/主题异动归因 AI agent。

- [设计文档](docs/plans/2026-05-11-explain-agent-design.md)
- [Phase 1 实施计划](docs/plans/2026-05-11-explain-agent-phase1.md)

## 如何启动这个项目

### 0. 前置基础设施

需要先准备好以下外部服务：

| 服务 | 用途 | 必需 |
|---|---|---|
| MySQL 8.0+ | `quant_data` 库（行情/基本面，已有）+ `explain_agent` 库（agent 运行态） | 是 |
| ClickHouse | 日线行情 / 板块涨跌幅（复用 `quant_data` 库） | 是 |
| Qdrant 1.x | 新闻 / 政策 / 研报 embedding 向量库 | 是 |
| 强模型 API（Claude / DeepSeek 等） | synthesizer / narrative / followup_answer | 是 |
| 弱模型 API（DeepSeek 等） | query gen / evidence eval / mini summary | 是 |
| Tavily API | connection_explorer 与三个时效维度的 web search | 否（可选） |

### 1. 安装依赖与配置

```bash
uv sync
cp .env.example .env
# 编辑 .env 填入真实凭证：
#   MYSQL_* / CLICKHOUSE_* / QDRANT_*
#   WEAK_LLM_* / STRONG_LLM_*
#   TAVILY_API_KEY=tvly-xxx  (可选，注册 https://tavily.com 拿免费 1000/月)
```

### 2. 初始化存储

```bash
# MySQL schema（创建 explain_agent 库 + 7 张表）
uv run python scripts/run_migrations.py

# Qdrant 三个 collection（news_v1 / policy_v1 / research_v1）
uv run python -c "from explain_agent.db.qdrant_init import ensure_collections; print(ensure_collections())"
```

### 3. 数据准备：采集新闻语料

```bash
# 默认拉 18 个行业关键词，每个最多 200 条
uv run explain-ingest-news

# 或指定关键词文件
uv run explain-ingest-news --keywords-file my_keywords.txt --limit-per-kw 100
```

> 新爬新闻会自动经 trafilatura 抽取正文 → SnapshotStore 落盘 +
> `explain_snapshot_blob` 指针表，与 Tavily web search 链路统一存储。

### 4. 运行

**交互式 REPL（推荐日常使用）：**

```bash
uv run explain
# explain> 为什么半导体板块今天涨
# explain> /sessions          # 查看历史
# explain> /load s_xxx        # 加载历史 session 进入追问模式
# explain> /clear             # 清空当前 session 进入新一轮
# explain> /help              # 查看所有 slash 命令
# explain> /quit
```

**端到端 smoke（一次性跑完整链路 + 详细追踪）：**

```bash
PYTHONUNBUFFERED=1 uv run python -u scripts/run_main_graph_smoke.py "为什么半导体板块今天涨"
# 单次约 8-12 分钟，输出 6 维归因 + 延伸思考 + 全部 evidence/citation
```

### 5. 测试

```bash
# 默认排除 integration 标记，~6 秒跑完
uv run pytest

# 含集成测试（需真实 DB 凭证）
uv run pytest -m ""
```

### 故障排查

**首次启动卡在 BGE-M3 下载（`SSL: UNEXPECTED_EOF` / `Cannot send a request`）：**
HuggingFace 在国内不稳定。在 `.env` 加一行启用镜像：

```
HF_ENDPOINT=https://hf-mirror.com
```

模型下载到本地 HF cache 后即可离线复用。镜像配置由 `config.py` 启动时
通过 `dotenv.load_dotenv()` 推送到 `os.environ`，无需手动 `export`。

## 项目结构

```
src/explain_agent/
  config.py            配置加载
  db/                  MySQL / ClickHouse / Qdrant 客户端
  core/                Evidence / DataAdapter 等核心数据结构
  adapters/            各类数据源 Adapter
  embedding/           BGE-M3 embedding 封装
  ingest/              新闻采集 + 标注 + 索引 pipeline
  cli/                 命令行入口

tests/                 单元测试 + 集成测试
migrations/mysql/      MySQL schema 迁移脚本
docs/plans/            设计文档 / 实施计划
```

## Phase 1 验收状态

- [x] Qdrant 已启动且 3 个 collection 已创建
- [x] explain_agent MySQL schema 7 张表已就绪
- [x] 4 个 P0 Adapter 单测通过：clickhouse_market / mysql_fundamentals / akshare_capital_flow / news_corpus
- [x] BGE-M3 embedding 本地可跑
- [x] News 采集 + 标签 + 向量化 pipeline 完整
- [x] CLI explain-ingest-news 可执行
- [x] 端到端集成测试通过：4 个 Adapter 中 ≥3 个能返回真实 Evidence

## Phase 2.A 验收状态

- [x] LangGraph 状态机骨架（parse → router → framework → market_facts → fan_out → synth → sub → report → persist）
- [x] AttributionState schema + 框架 YAML 配置
- [x] DimensionWorker mini ReAct 内核（10 轮上限 + 软终止 + no_data 合法）
- [x] Semaphore(3) 受控并发
- [x] 节点单测全部通过（L1）
- [x] 维度 worker 单测全部通过（L2）
- [x] main_graph 集成测试通过（mock 节点）
- [x] 真实端到端 smoke：能对"为什么半导体板块今天涨"输出 6 维归因报告

## Phase 2.B 验收状态

- [x] 数据兜底：parse "今天" → 5 天窗口；DimensionWorker 单日空时扩到 7 天；akshare 重试
- [x] State schema 扩展：NarrativeClaim、narrative_claims、unverified_drops
- [x] 强模型叙事：JSON 输出 + 每 claim 挂 evidence_ids
- [x] 数值校验：narrative 中数字必须能在证据中精确匹配，否则整句删除
- [x] 维度报告重写：6 维 strong model 重写 + [e_xxx] 引用
- [x] confidence 重打分：cited_count × source_type 多样性
- [x] 全部单测通过（66 passed，含 persist Phase 2.B 字段回归保护）
- [x] 端到端 smoke：session `s_869d3239` 跑通，6 维全部有数据（vs 2.A 的 4/6），61 条 citations，3 条带引用 narrative_claims，confidence=low

## Phase 2.C 验收状态

- [x] CLI REPL（prompt_toolkit + 启动列表 + slash command 本地拦截）
- [x] Followup 链路（inline async + 中等 ~10K 上下文 + 后台异步落盘）
- [x] Session 管理（`/sessions` `/load` `/clear` `/new` `/help` `/quit`）
- [x] symbol_id 翻译为"公司名(代码)"格式
- [x] narrative prompt 鼓励多 source_type 引用
- [x] 全部单测通过（93 passed，Phase 2.A/2.B 全部回归不破）
- [ ] 端到端 REPL smoke（用户手动验收，因 main graph 单次需 ~10 min）

## Phase 2.D-1 验收状态

- [x] SnapshotStore（本地磁盘 + MySQL 指针）
- [x] WebSearchAdapter（Tavily 封装，可选启用）
- [x] ingest pipeline 集成 snapshot（新爬即存，不回填历史）
- [x] framework YAML 三个时效维度加 web_search
- [x] connection_explorer 节点（≤3 延伸议题 + 强模型自决 web）
- [x] State schema 扩展（ConnectionThread / connection_threads / connection_section）
- [x] report_builder 渲染"延伸思考"段落
- [x] persist tree_json 补 connection_threads + connection_section
- [x] main_graph 拓扑：dynamic_sub → connection_explorer → report
- [x] 全部单测通过（115 passed，含 Phase 2.A/2.B/2.C 回归）
- [x] 端到端 smoke：session `s_17f25967`，2 条 connection_threads，70 条 web snapshot

## Phase 2.D-3 后续小补丁（2026-05-13）

REPL 实际使用暴露的两个体验问题修复：
- [x] **F1 followup prompt 放宽**：金融相关追问不再因"超主题"被直接拒；
  改成"先尝试基于现有 evidence 推测 + 标注信息局限 + 建议 /new"
- [x] **F2 parse 加 intent_qualifier**（"上午"/"下午"/"今天"/"本周"/"近期"）：
  写到 narrative + dim_report prompt，让 LLM 自约束引用 evidence 时段
- [x] 全部 150 单测通过（143 → 150，+7 新单测）
- [x] Smoke 验证：跑"总结一下今天上午的行情"，dim_reports 明确说"该时段可用证据
  有限"（policy / international / sentiment_event），narrative 引用非今日事件
  时明确标注日期（如"5月11日早盘..."）而非模糊化为"今天上午"

**仍未解决（推到 Phase 2.D-4 brainstorm "全市场综述形态"）**：
- Q1: technical / capital_flow 对宽泛 target（如"上午行情"）无结果（IndustryResolver 不命中）
- Q4: 热点新闻主动获取（无"今日热点"自主发现）

## Phase 2.D-3 验收状态

Corpus 自动化 + 研报 evidence。lazy_ingest 让 agent 不再依赖手动 ingest；行业/宏观研报填补 dormant 表，让 policy / industry_chain / international 三维多一个权威源。

- [x] `lazy_ingest_node` corpus 自动保鲜（MAX(fetched_at) < now-3h 触发, 60s 超时, 异常静默 fallback）
- [x] `EastmoneyResearchCrawler` 直接调东财底层 reportapi (qType=1 行业 + qType=2 宏观)
- [x] `ResearchIndexer` + Qdrant research_v1 collection + snapshot_id=NULL（研报不落 snapshot）
- [x] `ResearchCorpusAdapter` 向量检索 + MySQL 补全 title/机构/行业 → Evidence(source_type="research")
- [x] `explain-ingest-research` CLI 手动 batch 入口（首次跑落地 200 条研报）
- [x] framework YAML 三维（policy / industry_chain / international）加 research_corpus
- [x] main_graph 拓扑：load_framework → lazy_ingest → market_facts
- [x] 全部单测通过（143 passed, Phase 2.A-2.D-2 全部回归不破）
- [x] 端到端 smoke：session `s_1beeb6fb`
  - lazy_ingest 触发拉 47s（上次 ingest > 3h）
  - **92 条 research_corpus evidence 真实进入归因**：policy 52 / industry_chain 20 / international 20
  - 总耗时 7.7 min（含 lazy_ingest 47s + policy 187s 10 轮）
  - connection_threads 3 条（含 web search 拉的真实存储扩产数据）
  - narrative_claims 3 条 + Citations 166 条

## Phase 2.D-2 验收状态

性能 + 漂移率标注闭环 + REPL 可观测性。fan_out 真并发 + strong LLM 链路并发将单次耗时从 13min 压到 5.8min（减半）。

- [x] LLMClient 双 API（sync `chat` + async `achat`，AsyncAnthropic / AsyncOpenAI lazy 创建）
- [x] 9 处 graph 链路 chat → await achat（dimension_worker / parse / synth / followup / connection_explorer / report_builder）
- [x] fan_out `return_exceptions=True` + 异常隔离 + max_concurrency=6
- [x] fan_out 真并发护栏单测 `test_fan_out_runs_dimensions_concurrently`（0.5s 远小于串行 3s）
- [x] strong LLM 链路并发：report_builder narrative + 6 维 dim_reports 并发、connection_explorer N 个 thread 并发
- [x] strong LLM 并发护栏单测：`test_dim_reports_run_concurrently` + `test_connection_explorer_processes_threads_concurrently`
- [x] `/annotate` + `/stats` 漂移率标注闭环（`explain_annotation` 表 + UNIQUE thread_index）
- [x] REPL 进度可观测（接上 on_node_event / on_done / on_round 回调）
- [x] 全部单测通过（127 passed，Phase 2.A/2.B/2.C/2.D-1 全部回归不破）
- [x] 端到端 smoke：session `s_b6c55c02`，**总耗时 5.8 min（vs 2.D-1 的 13 min）**
  - fan_out 142s（policy 偶然跑 7 轮；典型场景 < 90s）
  - report 56s（vs 2.D-1 的 146s，-62%）
  - connection_explorer 59s（vs 2.D-1 的 117s，-50%）
  - 3 条 connection_threads，109 条 citations

