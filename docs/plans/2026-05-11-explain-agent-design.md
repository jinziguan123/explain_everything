# Explain Agent · 设计文档

- 创建日期：2026-05-11
- 状态：已确认（brainstorming 阶段完成，准备进入 implementation plan）
- 作者：jinziguan + Claude

---

## 1. 项目目标与范围

### 1.1 一句话定义

一个针对 A 股板块/主题异动进行多维度归因分析的 AI agent。它不是普通的问答 LLM，而是通过"自顶向下拆解 + 自底向上证据汇总"的方式，给出有据可查、可追溯、可深挖的归因报告。

### 1.2 核心用户与目标

- **MVP 阶段**：自用工具，辅助投资复盘与市场理解
- **长期方向**：演化为面向内容生产（公众号/newsletter/视频脚本）的归因草稿生成器

两者复用同一套分析引擎，差别只在输出层包装。

### 1.3 MVP 锁定的能力

| 维度 | 锁定值 |
|---|---|
| 市场 | A 股 |
| 颗粒度 | 板块 / 主题概念 |
| 问题形态 | 事后归因（"为什么 X 板块涨/跌"），不做预测 |
| 工作流形态 | 被动问答 + 可对话深挖（A + C），不做主动每日扫描（B 留到后续） |
| 用户接入 | CLI 起步，Web UI 留架构位置 |

---

## 2. 关键战略决策清单

下面是 brainstorming 阶段已确认的所有决策，按重要性排序：

| # | 决策点 | 选择 | 关键理由 |
|---|---|---|---|
| 1 | 拆解框架架构 | **分层架构**：领域路由 + 领域框架库（YAML）+ 子分支动态扩展 | 兼顾稳定性（must-check 维度不漏）和灵活性（LLM 可发散子分支）；天花板高（加新领域只需加 YAML） |
| 2 | 金融领域核心维度 | 政策/宏观、产业链/基本面、资金面、情绪/事件、国际映射、技术面（6 维） | 覆盖 A 股归因的主要驱动因素，是 must-check 维度 |
| 3 | 数据源策略 | **免费数据为主 + Adapter 模式抽象** | A 股盘后归因免费数据够用；adapter 模式为未来升级付费源留接口 |
| 4 | LLM 模型策略 | **混合**：核心拆解/synthesizer/narrative 用强模型（Opus/GPT-5），中间查询/评估/摘要用弱模型（Haiku/DeepSeek） | 质量与成本平衡 |
| 5 | 输出形态 | **混合形态**：顶部叙事段 + 中部证据树 + 底部链接列表 | 自用看叙事段、深挖看证据树、内容化时叙事段直接用 |
| 6 | 证据可追溯性 | **强追溯**：每个论点必须挂接到具体 evidence id，无数据时诚实标注"无相关证据"，不强行编结论 | 金融场景幻觉成本极高，必须强约束 |
| 7 | 编排架构 | **方案 3**：骨架工作流 + 局部 Agentic Loop（外层 LangGraph workflow，单维内部小 ReAct loop，子分支扩展和追问深挖 agentic） | 稳定性（骨架）+ 灵活性（局部 agentic）兼得 |
| 8 | 框架 | **Python + LangGraph** | 生态成熟、状态机直观、与 akshare/tushare 等数据库无缝 |
| 9 | 存储 | **复用现有 MySQL + ClickHouse + 新增 Qdrant**；不在本机存任何重要数据 | 复用已有量化数据基础设施；Qdrant 比 Chroma 性能好 |
| 10 | 向量库 | **Qdrant**（自部署，对比过 PageIndex 暂不采用） | 新闻是 80% 数据量，传统向量库更适合；PageIndex 长文档优势可后续作为 Adapter 升级 |
| 11 | Embedding | **BGE-M3** | 中文 SOTA 开源、本地跑、CPU 可用 |
| 12 | 单维 worker 检索轮数 | **硬上限 10 轮 + 软终止**（连续 2 轮无信息增益则提前终止） | 平衡彻底性与成本 |
| 13 | 动态子分支上限 | **≤ 2 个**，由 strong synthesizer 决策 | 防止发散 |
| 14 | 历史新闻回填 | **90 天** | 足以覆盖大多数板块异动归因场景 |
| 15 | CLI 输出 | **Rich 库彩色排版** | 自用工具体验差异巨大 |
| 16 | Agent 人设 | **审慎的研究员**：保守、爱说"证据有限"、不预测、不教唆 | 进一步抑制幻觉 |

---

## 3. 架构总览

四层分层架构：

```
┌──────────────────────────────────────────────────────────────┐
│  接入层  CLI (Phase 1) → 轻量 Web (Phase 4)                   │
│  - 问答入口、追问会话、报告展示                                │
└────────────────────┬─────────────────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────────────────┐
│  Agent 编排层  LangGraph 状态机                               │
│  parse → router → load_framework → fetch_market_facts        │
│      → 6 维并行 worker (mini ReAct) → synthesizer            │
│      → [optional 动态子分支] → report_builder → persist      │
│                                                              │
│  追问 Graph (独立)：复用证据树 + 增量检索                     │
└────────────────────┬─────────────────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────────────────┐
│  能力层  Tools & Adapters                                    │
│  - 数据 Adapter:  行情/资金/新闻/政策/研报/公告              │
│  - LLM Adapter:  强模型 + 弱模型                              │
│  - 检索 Adapter:  向量检索 (Qdrant) / 关键词 / Web 搜索       │
└────────────────────┬─────────────────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────────────────┐
│  存储层                                                       │
│  - ClickHouse (已有):  行情、因子值                          │
│  - MySQL (已有):  股票元数据、行业、基本面、交易日历          │
│  - MySQL (新增 explain_* 表): 会话、证据树、新闻/政策/研报语料 │
│  - Qdrant (新增): 新闻/政策/研报向量索引                      │
│  - 文件 / 对象存储: 原始 HTML/JSON 快照                       │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. 存储层设计

### 4.1 复用现有基础设施

**ClickHouse `quant_data`：**
- `stock_bar_1d` / `stock_bar_1m` / `stock_spot_realtime`：行情
- `factor_value_1d`：因子值

**MySQL `quant_data`：**
- `stock_symbol` / `fr_instrument`：股票元数据
- `fr_industry_current` / `fr_industry_history`：申万行业分类
- `fr_index_constituent`：指数成分股
- `fr_daily_market_cap` / `fr_daily_pb`：市值/PB
- `fr_fundamental_profit`：基本面（ROE/利润率等）
- `fr_trade_calendar`：交易日历

### 4.2 新增表（推荐独立 schema `explain_agent`，与 `quant_data` 物理隔离）

| 表名 | 作用 |
|---|---|
| `explain_session` | 问答会话元信息（session_id、首问、时间） |
| `explain_evidence_tree` | 归因证据树（root question + 树状 JSON，每节点含 evidence id 引用） |
| `explain_news_corpus` | 新闻原文 + 元数据（来源、URL、时间、关联 tag） |
| `explain_policy_corpus` | 政策文件 + 元数据 |
| `explain_research_corpus` | 研报标题/摘要 + 元数据 |
| `explain_snapshot_blob` | 原始 HTML/JSON 快照（永久存档用于证据溯源） |
| `explain_followup_history` | 追问深挖记录 |

### 4.3 Qdrant Collection 设计

- `news_v1`：新闻语料，BGE-M3 1024 维
- `policy_v1`：政策文件分段后向量化
- `research_v1`：研报标题+摘要向量化

每个 collection 的 payload 包含：`corpus_id`（指向 MySQL 主键）、`source`、`timestamp`、`tags`，便于检索后回查原文。

### 4.4 缺失的数据（MVP 期间需要新增采集）

- 新闻文本（财联社/新浪/东财，akshare `stock_news_em` 是 MVP 入口）
- 政策文件（国务院、各部委官网）
- 研报标题/摘要（akshare 研报中心）
- 公告（巨潮资讯网 / akshare）
- 概念板块成分（同花顺/东财，与申万分类互补）
- 北向资金、龙虎榜、ETF 申赎（akshare）

---

## 5. 编排层（LangGraph 状态机）

### 5.1 主流程图

```
              [parse_question]               解析：标的、时间窗、意图
                     ↓
              [domain_router]                路由到金融归因领域
                     ↓
              [load_framework]               加载 6 维 YAML 配置
                     ↓
              [fetch_market_facts]           取客观市场锚点
                     ↓
        ╔════════════════════════════╗
        ║   并行 fan-out (6 worker)   ║
        ║  政策   产业链   资金       ║       每个 worker 是 mini ReAct loop
        ║  情绪   国际    技术面      ║
        ╚════════════════════════════╝
                     ↓
              [evidence_synthesizer]         agentic：评估 + 动态扩展决策
                     ↓
              ┌──────┴───────┐
        无新分支              触发动态子分支 (≤2)
              │                    ↓
              │           [dynamic_subbranch_fetch]
              │                    ↓
              └──────┬─────────────┘
                     ↓
              [report_builder]               叙事段（强）+ 维度报告（弱）
                     ↓
              [persist]                       证据树 + 快照入库
```

### 5.2 State Schema

```python
class AttributionState(TypedDict):
    raw_question: str
    asked_at: datetime
    session_id: str

    # parse
    target: str
    target_code: Optional[str]
    time_window: tuple[date, date]
    intent: Literal["up", "down", "volatile", "general"]

    # router
    domain_id: str
    framework: dict

    # facts
    market_facts: dict

    # dimensions
    dimension_results: dict[str, DimensionResult]
    needs_subbranch: bool
    subbranches: list[SubBranchSpec]
    subbranch_results: dict[str, DimensionResult]

    # output
    narrative: str
    dimension_reports: dict[str, str]
    citations: list[Citation]

    # metadata
    llm_calls: dict[str, int]
    total_cost: float
    errors: list[str]
```

### 5.3 单维 Worker（mini ReAct loop）

```
[生成查询关键词] (弱)
        ↓
[调 Adapter 查询]
        ↓
[评估证据] (弱)
        ↓
   够用 → 写 mini_summary (弱) → 结束
   不够 → 精化关键词 → 回到查询
   无关 → 标 no_data=true → 结束

约束:
  - 硬上限 10 轮
  - 软终止: 连续 2 轮无新高相关证据则提前终止
  - market_facts 作为只读上下文注入每一轮
```

### 5.4 Evidence Synthesizer（局部 agentic）

1. 浏览 6 维所有证据
2. 识别"反复出现但未被框架覆盖"的实体/事件/政策
3. 强模型判定是否值得开子分支
4. 输出 ≤ 2 个子分支 spec
5. 子分支 worker 复用维度 worker（维度名动态）

### 5.5 追问 Graph（独立小图，复用证据树）

```
[parse_followup]
      ↓
   ┌──┴──┬──────────┬──────────┐
"深挖某维度"  "质疑某证据"  "反事实"   "对比分析"
   ↓          ↓            ↓          ↓
[deepen]   [verify]     [counter]  [compare]
   ↓          ↓            ↓          ↓
   └──────────┴────┬───────┴──────────┘
                   ↓
            [incremental_answer]    复用已有证据树，按需增量检索
```

---

## 6. 金融领域框架配置（YAML）

`frameworks/cn_equity_sector_attribution.yaml`（结构示意）：

```yaml
domain_id: cn_equity_sector_attribution
display_name: A股板块/主题异动归因
match_patterns:
  - "为什么.*(板块|主题|概念|行业).*(涨|跌|异动|涨停|大跌)"

dimensions:
  - id: policy
    name: 政策/宏观
    priority: 1
    data_sources: [policy_search, news_vector, web_search]
    query_template: "最近 {time_window} 内与 {target} 相关的政策、监管表态"
    evidence_filter:
      time_decay_days: 14
      min_relevance: 0.6

  - id: industry_chain
    name: 产业链/基本面
    priority: 1
    data_sources: [clickhouse, mysql, research_search, news_vector]
    query_template: "{target} 产业链上下游近期变化"
    needs_market_context: true

  - id: capital_flow
    name: 资金面
    priority: 2
    data_sources: [akshare_capital_flow, clickhouse]
    query_template: "{time_window} 内 {target} 资金流向、龙虎榜、ETF 申赎"

  - id: sentiment_event
    name: 情绪/事件
    priority: 2
    data_sources: [news_vector, web_search]

  - id: international
    name: 国际映射
    priority: 3
    data_sources: [news_vector, web_search]

  - id: technical
    name: 技术面
    priority: 3
    data_sources: [clickhouse]

models:
  router: weak
  parse: weak
  query_gen: weak
  evidence_eval: weak
  mini_summary: weak
  synthesizer: strong
  narrative: strong
  dimension_report: weak
  followup_router: weak
  followup_answer: strong
```

**未来添加新领域 = 添加新 YAML，Python 代码不动。**

---

## 7. 数据 Adapter

### 7.1 统一接口

```python
class DataAdapter(Protocol):
    name: str

    async def query(
        self,
        keywords: list[str],
        time_window: tuple[date, date],
        target: str,
        filters: dict | None = None,
    ) -> list[Evidence]: ...

class Evidence(TypedDict):
    id: str
    source: str
    source_type: Literal["news", "policy", "research", "market_data", "capital_flow"]
    url: str | None
    title: str | None
    snippet: str
    raw_payload: dict | None
    snapshot_id: str | None
    timestamp: datetime
    metadata: dict
```

### 7.2 Adapter 优先级清单

| 优先级 | Adapter | 数据来源 | 覆盖维度 |
|---|---|---|---|
| P0 | `clickhouse_market` | 已有 CH | 技术面、产业链 |
| P0 | `mysql_fundamentals` | 已有 MySQL | 产业链 |
| P0 | `news_corpus` | 自建采集 + Qdrant | 政策、情绪、产业链 |
| P0 | `akshare_capital_flow` | akshare | 资金面 |
| P1 | `policy_corpus` | 自建采集 + Qdrant | 政策 |
| P1 | `research_corpus` | akshare 研报 + Qdrant | 产业链 |
| P2 | `web_search` | DuckDuckGo / SerpAPI | 兜底/国际 |
| P2 | `akshare_dragon_tiger` | akshare | 资金面 |

### 7.3 新闻采集 MVP 方案

```
[每日定时 (盘后)] → [akshare stock_news_em 拉取] → [Dedupe by url_hash]
                                                       ↓
                              [LLM 弱模型 Tagger 打标签：行业/概念/政策类型]
                                                       ↓
                              [BGE-M3 Embedding]
                                                       ↓
                              [写入 MySQL explain_news_corpus + Qdrant news_v1]
```

首次部署回填 90 天历史。

---

## 8. 错误处理

| 失败场景 | 策略 |
|---|---|
| Adapter 失败 | 单维内重试 2 次，仍失败则标注"数据源不可用"，不阻塞其他维度 |
| Qdrant 不可达 | 降级到 MySQL `LIKE` 关键词检索，报告底部标注 |
| LLM 失败 | 指数退避重试 3 次；仍失败则节点失败，保留已完成的部分数据 |
| 单次成本 > $1 | 在 synthesizer 节点设硬关，不开子分支直接出报告 |
| 维度 10 轮无证据 | 标 `no_data=true`，叙事段如实写"未检索到相关证据" |
| 非金融问题 | router 返回 `unknown`，礼貌拒绝，不强行回答 |

---

## 9. 幻觉防控（五层防御）

1. **强制 ground-in-evidence**：LLM 输出格式约束为 `{claim, evidence_ids}` 结构化 JSON，无 evidence_ids 的论点后处理时砍掉
2. **证据相关性双重校验**：弱模型 → 强模型 narrative 阶段二次校验"证据是否支持论点"
3. **数值校验**：具体数字必须来自结构化 Adapter（行情/资金流），不允许从新闻文本提取
4. **自我评估打分**：强模型对自身报告打 confidence；低置信报告顶部加 disclaimer
5. **永久快照**：所有证据原文落盘 `explain_snapshot_blob`，引用链接指向快照 id

---

## 10. 测试策略

### 层 1 · 组件单测（必须）
- 每个 Adapter mock 上游 → assert Evidence 结构
- 状态机节点构造 state → assert 状态变化
- 工具函数固定输入 → assert 输出 schema

### 层 2 · Eval 集（核心）
- 维护 20-50 个人工标注的"金标归因案例库"
- 指标：维度命中率、证据相关性、幻觉率、成本
- **MVP 至少 5 个案例**

### 层 3 · 回归对照（持续）
- 每次改动跑同问题，diff 输出，人工评估变化方向

---

## 11. MVP 里程碑（4-6 周）

### Phase 1 · 数据底座（1 周）
- Qdrant 启动与连通
- 新增 MySQL 表（explain_* 系列）
- P0 Adapter：clickhouse_market、mysql_fundamentals、akshare_capital_flow
- 新闻采集脚本（每日定时 + 90 天回填）
- ✅ 验收：能查到最近 30 天新闻向量检索结果

### Phase 2 · 主流程跑通（1-2 周）
- LangGraph 状态机骨架
- 维度 worker mini ReAct
- 报告生成器（叙事 + 结构化）
- Rich CLI 入口：`explain "为什么 X 板块今天涨"`
- ✅ 验收：5 个真实案例跑出有理有据的报告

### Phase 3 · 追问 + 幻觉防控（1 周）
- 追问 Graph
- 五层幻觉防控落地
- Eval 集 5 个案例 + 自动评估脚本
- ✅ 验收：eval 集幻觉率 < 10%、维度命中率 > 70%

### Phase 4 · P1/P2 + 体验打磨（1-2 周）
- policy_corpus / research_corpus / web_search Adapter
- 简单 Web UI（FastAPI + 静态页查看证据树）
- Markdown 报告导出
- ✅ 验收：可作为日常工具使用

---

## 12. 待 implementation plan 阶段细化的开放问题

- 具体使用哪个强模型 / 弱模型（Claude Opus vs GPT-5 vs DeepSeek vs Qwen）的最终选定
- 新闻去重的 url_hash 算法细节（含 querystring 处理）
- LLM 弱模型 tagger 的 tag 体系设计
- Qdrant collection 的索引参数（HNSW m/ef_construct 等）
- 政策文件采集源的具体列表
- Web UI 技术栈选择（Phase 4 再定）
- 单次归因总成本阈值的具体数值（草案 $1，需 Phase 2 跑通后实测调整）
