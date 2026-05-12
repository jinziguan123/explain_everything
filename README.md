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

> 当前 ingest CLI 暂未注入 `SnapshotStore`，新爬新闻不会自动落 snapshot；待 Phase 2.D-2 补齐。Tavily web search 与历史 snapshot 不受影响。

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
- [ ] 端到端 smoke：手动用户验收（需要 TAVILY_API_KEY）

