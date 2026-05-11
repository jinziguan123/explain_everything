# Explain Agent · Phase 2 设计文档

- 创建日期：2026-05-11
- 状态：已确认（brainstorming 阶段完成，准备进入 implementation plan）
- 作者：jinziguan + Claude
- 上一阶段：[Phase 1 数据底座](2026-05-11-explain-agent-phase1.md) 已完工

---

## 1. Phase 2 目标与范围

### 1.1 一句话定义

在 Phase 1 数据底座上，搭建 LangGraph 编排层、6 维并行归因 worker、追问 Graph 与报告生成器，让 agent 能完整端到端回答"为什么 X 板块涨停"这类归因问题，并支持后续多轮深挖。

### 1.2 完工后用户能做的

```bash
# REPL 模式（默认）
$ explain
> 为什么半导体今天涨停
[补拉 半导体 最新新闻 ✓ 新增 12 条]
[6 维并行分析 ▰▰▰▰▱▱ 67%]
[Confidence: 高]
=== 半导体板块今日涨停归因 (2026-05-12) ===
[顶部叙事 80-150 字 + 6 维证据树展开]
报告已保存: reports/2026-05-12-半导体-归因.md

> 政策这条再展开
[150 字深挖,基于现有证据树]

> 宁德时代昨天 PE 是多少
[16.3 (2025-05-10)]

> exit

# 单次模式（脚本/cron 友好）
$ explain query "为什么白酒板块跌" --session s_2026_05_12
```

### 1.3 核心性能承诺

| 操作 | 时延 | 成本（DeepSeek 价格） |
|---|---|---|
| 单次完整归因（含按需扩充新闻） | ≤ 30 秒 | ≤ 2 元 |
| 追问深挖 / 反事实 / 质疑 | ≤ 5 秒 | ≤ 0.3 元 |
| 事实查询 | ≤ 3 秒 | ≤ 0.05 元 |
| 对比分析（轻量归因） | ≤ 15 秒 | ≤ 0.8 元 |

### 1.4 Phase 2 不做的（留到 Phase 3）

- P1 Adapter：`policy_corpus`、`research_corpus`（爬国务院/部委/研报中心）
- Web UI（FastAPI + 可视化证据树）
- 主动扫盘模式（B 方向：每日盘后扫异动 Top 10 → 自动生成归因 inbox）
- 更广 eval 集（20+ case）
- 概念板块归因（同花顺/东财 主题概念）

---

## 2. 关键决策清单

下面是 brainstorming 阶段已确认的所有决策：

| # | 决策点 | 选择 | 关键理由 |
|---|---|---|---|
| 1 | Phase 2 范围 | **C 全做**（六维归因 + 追问 Graph + 按需扩充 + 完整幻觉防控） | 一次完整交付能用的 agent，端到端验证产品形态 |
| 2 | 交互形态 | **A + Intent Classifier**：初次完整六维报告 + 追问智能路由（不重做六维） | 自用对话式体感 + 追问代价低 |
| 3 | 按需扩充新闻策略 | **方案 2**：智能新鲜度检测（2 小时阈值，进度可见） | 平衡新鲜度与延迟，CLI 透明提示 |
| 4 | 6 维 → P0 Adapter 映射 | 见 §2.1 | 仅依赖 P0 Adapter，避免 Phase 2 工程量翻倍 |
| 5 | P1 Adapter（policy/research_corpus） | **Phase 2 不做** | news 里的政策类二手解读够用，P1 留 Phase 3 |
| 6 | 维度并行控制 | **`asyncio.Semaphore(3)`** | 平衡速度（≈15 秒）和 LLM rate limit |
| 7 | thinking 模型适配 | `max_tokens` 默认 2000+（弱）/ 4000+（强）；prompt 强调"只输出 JSON/Markdown" | Phase 1 实测发现 DeepSeek v4 thinking 模型必需 |
| 8 | DeepSeek 1M context | **不剪枝、不压缩证据，直接传完整证据树给 strong model** | 用户确认 1M 可用，省去中间压缩节点 |
| 9 | CLI 形态 | **REPL（默认）+ 单次 query 模式**（`explain query "..."`） | REPL 追问自然；单次模式脚本友好 |
| 10 | 报告输出 | **终端 Rich 打印 + 同时写 `reports/YYYY-MM-DD-target-归因.md`** | 即看即用 + 永久存档（可作 Phase 2b 内容化素材） |
| 11 | Eval 集 | yaml 格式预先约定，**5 个金标案例后定**（等语料充分后选定） | 案例数据依赖真实 ingest 完成 |
| 12 | 测试策略 | **三层**：L1 节点单测 + L2 维度 worker 单测 + L3 端到端 case 测试（`@pytest.mark.eval`） | 兼顾速度（mock）和真实性（eval case） |

### 2.1 六维 → P0 Adapter 映射

| 维度 | 使用的 Adapter | 说明 |
|---|---|---|
| 政策/宏观 | `news_corpus`（过滤 `tags.policy_type` 非空） | 没有 policy_corpus，靠 news 里政策类新闻替代 |
| 产业链/基本面 | `mysql_fundamentals` + `clickhouse_market` + `news_corpus`（过滤 `tags.industries`） | 数值面齐全，研报缺 |
| 资金面 | `akshare_capital_flow` + `clickhouse_market`（成交量异常） | 完整 |
| 情绪/事件 | `news_corpus`（过滤 `tags.event_type ∈ {事件,产业链}`） | 完整 |
| 国际映射 | `news_corpus`（过滤 `tags.event_type = 海外`） | 缺 web_search，覆盖度有限 |
| 技术面 | `clickhouse_market`（K 线计算） | 完整 |

---

## 3. 架构增量

```
┌──────────────────────────────────────────────────────────────┐
│  接入层 (新增)                                                │
│  CLI: REPL (默认) + 单次 query 模式                          │
│       报告同时打印 + 写 reports/YYYY-MM-DD-target-归因.md     │
└────────────────────┬─────────────────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────────────────┐
│  Agent 编排层 (新增 - LangGraph)                              │
│                                                              │
│  Intent Classifier (弱模型, 路由 3 种意图)                    │
│    │                                                         │
│    ├─→ attribution  → Main Graph (六维归因)                  │
│    ├─→ followup     → Followup Graph (基于现有证据树)        │
│    └─→ factual      → Factual Handler (简单事实查询)         │
│                                                              │
│  Main Graph:                                                 │
│    parse → router → framework → market_facts                 │
│    → 6 维 fan-out (Semaphore=3) → synthesizer                │
│    → report_builder → persist                                │
│                                                              │
│  Followup Graph:                                             │
│    parse_followup → 4 类 handler (deepen/verify/counter/cmp) │
│    → incremental_answer                                      │
└────────────────────┬─────────────────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────────────────┐
│  能力层 (Phase 1 基础上扩展)                                  │
│  + Lazy News Ingest (NewsCorpusAdapter 内嵌新鲜度检测)        │
│  + Framework YAML Loader (cn_equity_sector_attribution.yaml)  │
│  + Report Generator (叙事段 + 证据树 + Confidence 评估)       │
│  + Eval Framework (yaml case → 自动评分)                      │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. Main Graph 设计

### 4.1 节点定义

| 节点 | 输入 | 输出（state 增量） | 用的 LLM | 备注 |
|---|---|---|---|---|
| `intent_classifier` | raw_question, history | intent ∈ {attribution, followup, factual} | 弱 | 第一道关，最便宜的轻量调用 |
| `parse_question` | raw_question | target, time_window, intent | 弱 | 提取标的、时间窗、归因方向 |
| `domain_router` | parsed | domain_id | 弱 | MVP 只有 `cn_equity_sector_attribution` |
| `load_framework` | domain_id | framework dict | — | 加载 YAML 配置 |
| `fetch_market_facts` | target, time_window | market_facts | — | **客观锚点**：调 `clickhouse_market` 拿涨跌幅/龙头股/联动板块 |
| `fan_out_dimensions` | framework + market_facts | dimension_results | 弱 × 6 | **Semaphore(3)**，每维内部 mini ReAct |
| `synthesizer` | dimension_results | needs_subbranch + subbranches | **强** | 看完整证据树，决定是否触发动态子分支（≤2） |
| `dynamic_subbranches` | subbranches | subbranch_results | 弱 | 复用维度 worker，动态维度名 |
| `report_builder` | 完整证据树 | narrative + dim_reports + citations + confidence | **强** | 用 1M context 直接塞所有 evidence |
| `persist` | full state | session_id | — | 写 MySQL `explain_session` + `explain_evidence_tree` |

### 4.2 State Schema

```python
class AttributionState(TypedDict):
    # 输入
    raw_question: str
    asked_at: datetime
    session_id: str

    # parse
    target: str
    time_window: tuple[date, date]
    intent: Literal["up", "down", "volatile", "general"]

    # router/framework
    domain_id: str
    framework: dict

    # 客观锚点
    market_facts: dict

    # 维度结果
    dimension_results: dict[str, DimensionResult]

    # 动态扩展
    needs_subbranch: bool
    subbranches: list[SubBranchSpec]
    subbranch_results: dict[str, DimensionResult]

    # 最终输出
    narrative: str
    dimension_reports: dict[str, str]
    citations: list[Citation]
    confidence: Literal["high", "medium", "low"]

    # 元数据
    llm_calls: dict[str, int]
    total_cost: float
    errors: list[str]
```

`DimensionResult` 结构：

```python
class DimensionResult(TypedDict):
    evidence: list[Evidence]
    mini_summary: str
    retry_count: int
    no_data: bool
    confidence: Literal["high", "medium", "low"]
```

### 4.3 维度 Worker 内核（mini ReAct loop）

```
[市场锚点 + 维度配置] (注入只读上下文)
        ↓
[生成查询关键词] (弱)        ← round 1
        ↓
[调 Adapter 查询]
        ↓
[评估证据相关性 + 是否足够] (弱)
        ↓
   足够 → 写 mini_summary → 维度结束
   不够 → 精化关键词 → 回到调 Adapter   (round 2-10)
   连续 2 轮无信息增益 → 软终止
   真无数据 → 标 no_data=true → 维度结束（合法状态）

约束:
- 硬上限 10 轮
- 软终止: 连续 2 轮无新高相关证据
- market_facts 全程注入，避免漫无目的的查询
```

### 4.4 Synthesizer 局部 agentic

1. 浏览 6 维所有证据
2. 识别"反复出现但未被框架覆盖"的实体/事件/政策（如某具体公司、政策名）
3. 强模型判定是否值得开子分支
4. 输出 ≤ 2 个子分支 spec
5. 子分支 worker 复用维度 worker（维度名动态）

---

## 5. Followup Graph 设计

```
[intent_classifier] → followup
        ↓
[parse_followup] (弱)
   识别意图 + 抽取上下文锚点 (维度id / 证据id / 对比对象)
        ↓
   ┌─────────────┬──────────────┬──────────────┬─────────────┐
   "深挖"        "质疑证据"     "反事实"       "对比"
        ↓             ↓               ↓             ↓
  [deepen_dim]   [verify_evidence]  [counterfactual] [compare]
        ↓             ↓               ↓             ↓
  调单维 worker   查 snapshot       strong model    触发轻量归因
  (deeper search) 给原文            纯推演         (限定维度)
        ↓             ↓               ↓             ↓
   └─────────────┴──────────────┴──────────────┴─────────────┘
                        ↓
              [incremental_answer] (强)
              基于追问类型 + 增量数据写回答
                        ↓
              写 explain_followup_history
```

### 5.1 四类 Handler 调用画像

| Handler | LLM 调用 | Adapter 调用 | 时延 |
|---|---|---|---|
| `deepen` | 弱(查询生成) + 弱(评估) + 弱(总结) + 强(answer) | 单维 deeper search | 5-15 秒 |
| `verify` | 强(answer) | 只查 snapshot blob | 2-5 秒 |
| `counter` | 强(answer) | 不查新数据 | 3-8 秒 |
| `compare` | parse + 限定 2-3 维 mini fan-out + 强(answer) | 部分 Adapter | 10-20 秒 |

### 5.2 关键约束

**追问 Graph 绝不调用六维并行 worker。** 即使是 `compare` 也只跑 2-3 个限定维度的轻量归因。

---

## 6. Intent Classifier

第一道关，弱模型轻量调用：

```python
SYSTEM = """
你是 explain agent 的意图分类器。分析用户输入，输出 JSON:
{
  "intent": "attribution" | "followup" | "factual",
  "reason": "一句话理由"
}

规则:
- attribution: 用户在问"为什么 X 涨/跌/异动"这类需要多维归因的问题
- followup: 用户在追问之前归因的某个细节（"政策再展开"、"这条新闻可信吗"、
  "如果..."、"和...对比"）。需要 session_id 上下文。
- factual: 简单事实查询（"今天 PE 多少"、"龙头股是谁"）。不需要多维分析。

session_history (前 3 轮): {history_snippet}
当前输入: {raw_question}
"""
```

无 history 时，强制不会判 followup。

---

## 7. Lazy News Ingest（NewsCorpusAdapter 内嵌）

### 7.1 触发逻辑

```python
async def query(self, q: AdapterQuery) -> list[Evidence]:
    if self._needs_refresh(q):
        await self._refresh(q)
    return await self._search(q)

def _needs_refresh(self, q: AdapterQuery) -> bool:
    # 仅当 time_window 覆盖"今天"且 target 在 corpus 中近 2 小时无入库
    if q.time_window[1] < date.today():
        return False
    sql = """
    SELECT MAX(fetched_at) FROM explain_news_corpus
    WHERE JSON_SEARCH(tags, 'one', %(target)s, NULL, '$.industries') IS NOT NULL
       OR JSON_SEARCH(tags, 'one', %(target)s, NULL, '$.concepts') IS NOT NULL
    """
    last = self.engine.execute(sql, {"target": q.target}).scalar()
    if last is None:
        return True
    return (datetime.now() - last) > timedelta(hours=2)

async def _refresh(self, q: AdapterQuery):
    self.progress_cb(f"补拉 {q.target} 最新新闻...")
    items = self.crawler.crawl_symbol(q.target)
    n = self.indexer.index(items)
    self.progress_cb(f"补拉 {q.target} 最新新闻 ✓ 新增 {n} 条")
```

### 7.2 用户可见进度

```
[补拉 半导体 最新新闻...]
[补拉 半导体 最新新闻 ✓ 新增 12 条]
[6 维并行分析 ▰▰▰▰▱▱ 67%]
```

---

## 8. Report Generator

### 8.1 叙事段 prompt（strong model）

```python
SYSTEM = """
你是审慎的金融研究员。基于以下证据，写一段 80-150 字的归因叙事。

要求：
1. 每个结论必须挂接到 evidence_ids，输出格式严格如下 JSON:
   {"narrative": "...",
    "claims": [{"text":"...", "evidence_ids":["e_xxx"]}],
    "confidence": "high|medium|low"}
2. 涉及数字必须直接引用 market_facts/capital_flow 提供的结构化数据，
   禁止从新闻文本中"估算"
3. 没有证据的论点直接砍掉，不要编造
4. Confidence 评估：
   - high: ≥3 维有强证据，相互闭环
   - medium: 1-2 维有强证据，部分维度数据稀疏
   - low: 多维 no_data，主要靠少量证据推断

DeepSeek 1M context — 完整证据树直接塞，不要摘要。
"""
```

### 8.2 维度报告（弱模型，6 个并行）

每维一段 300-500 字。约束：
- 必须列出该维所有 evidence（按相关性排序）
- 数字必须来自 raw_payload
- `no_data=true` → 直接写"本维度未检索到相关证据"

### 8.3 五层幻觉防控

| 层 | 实现 |
|---|---|
| L1 强制 ground-in-evidence | LLM 输出含 `evidence_ids` 字段，后处理砍无引用 claim |
| L2 双重校验 | 弱模型生成的 claim→evidence 映射，由 strong model 在叙事段再校一遍 |
| L3 数值校验 | 数字字段强制从 market_facts/capital_flow 取，prompt 占位符替换 |
| L4 Confidence 评分 | strong model 自评，低 confidence 加 disclaimer |
| L5 永久快照 | NewsIndexer 时已经把 url+content 入库；新增 explain_snapshot_blob 存原始 JSON |

### 8.4 输出样例

```
================================================================
  半导体板块今日涨停归因  · 2026-05-12 · Confidence: 高
================================================================

【一句话总结】
受美国新一轮 HBM 制裁催化（政策维度，e_x1）+ 国产替代订单
落地（产业链维度，e_x2）+ 主力净流入 12.3 亿（资金面 e_x3）
三因素共振，板块整体涨停。

【六维归因】

▎政策/宏观 (3 条证据)                       Confidence: 高
  - 美国 BIS 5/11 发布制裁 ... [e_x1] (score 0.89)
  - ...

▎产业链/基本面 (5 条证据)                    Confidence: 高
▎资金面 (2 条证据)                           Confidence: 中
▎情绪/事件 (4 条证据)                        Confidence: 高
▎国际映射 (1 条证据)                         Confidence: 低
▎技术面 (1 条证据)                           Confidence: 中

【动态子分支：HBM 产业链】(synthesizer 触发)
...

【证据链接】
e_x1: https://... (snapshot_id: s_001)
e_x2: ...
================================================================
报告已保存: reports/2026-05-12-半导体-归因.md
```

---

## 9. CLI 设计

### 9.1 入口

```toml
[project.scripts]
explain = "explain_agent.cli.main:app"
explain-ingest-news = "explain_agent.cli.ingest_news:app"  # Phase 1 已有
```

### 9.2 REPL 实现（用 prompt_toolkit）

```python
@app.command()
def repl(session: str | None = None):
    session_id = session or f"s_{uuid4().hex[:8]}"
    history = []
    while True:
        q = prompt_toolkit.prompt("> ", history=FileHistory(".explain_history"))
        if q.lower() in ("exit", "quit"):
            break
        result = handle_query(q, session_id, history)
        history.append({"q": q, "intent": result.intent, "summary": result.short_summary})

@app.command()
def query(question: str, session: str | None = None):
    handle_query(question, session or new_session_id(), history=[])
```

### 9.3 输出渲染

- `Console().print()` 实时输出，含进度条/表格
- `Console(record=True).save_text()` 同步写 markdown 到 `reports/YYYY-MM-DD-target-归因.md`

---

## 10. Eval Framework

### 10.1 目录结构

```
eval/
  cases/
    cn_equity/
      2024-09-24-real-estate-up.yaml
      ... (5 个金标案例)
  runner.py
  metrics.py
  reports/
    YYYY-MM-DD-HH-eval.md
```

### 10.2 Case 格式

```yaml
case_id: 2024-09-24-real-estate-up
date: 2024-09-24
question: "2024-09-24 房地产板块为什么涨停"
target: 房地产
intent: up
time_window:
  start: 2024-09-17
  end: 2024-09-24

golden_dimensions:
  primary: [policy, capital_flow]
  secondary: [sentiment_event]
  not_relevant: [technical, international]

golden_evidence_keywords:
  - 央行降准
  - 证监会
  - 北向资金回流
  - 一线城市限购松绑

forbidden_in_narrative:
  - 中东战争
  - 美国大选
  - 半导体制裁

expected_confidence: high
```

### 10.3 评分指标

| 指标 | 计算 | 权重 |
|---|---|---|
| 维度命中率 | (primary 命中/total)×0.7 + (secondary 命中/total)×0.3 | 40% |
| 关键证据覆盖率 | golden_evidence_keywords 在输出中出现的比例 | 30% |
| 幻觉率（反向） | forbidden 出现 = -50 分/个 | 关键否决 |
| Confidence 对齐 | agent confidence == expected ? 1 : 0.5 | 10% |
| 成本 | 单次归因 token + 金额 | 20%（软目标） |

总分 ≥ 70 算通过。

### 10.4 跑测命令

```bash
uv run python eval/runner.py --cases eval/cases/cn_equity --output eval/reports/
```

---

## 11. 测试策略

| 层 | 范围 | 数量 | 备注 |
|---|---|---|---|
| L1 节点单测 | 每个 LangGraph 节点输入→输出 | 8-12 | mock LLM/Adapter，纯逻辑 |
| L2 维度 Worker 单测 | mini ReAct 完整循环 | 4-6 | 验证终止条件、no_data、retry 上限 |
| L3 端到端 case 测试 | Eval Framework 跑 5 个金标 | 5 case | 真 LLM 真 DB，`@pytest.mark.eval` 显式触发 |

新增 marker `eval`：

```toml
[tool.pytest.ini_options]
markers = [
    ...
    "eval: 端到端 eval case，需真 LLM/DB，慢",
]
```

---

## 12. Phase 2 里程碑

| Phase | 目标 | 验收 | 时长 |
|---|---|---|---|
| **2.A · LangGraph 骨架 + Main Graph** | parse → router → market_facts → 6 维 (Semaphore=3) → synthesizer → report → persist 全跑通 | L1+L2 单测全过；手动跑 1 真实案例输出完整报告 | 4-5 天 |
| **2.B · 报告生成器 + 五层幻觉防控** | 叙事 + 6 维报告 + Confidence + 快照 | 人工核验：所有 claim 有 evidence_id、数字来自 raw_payload | 2-3 天 |
| **2.C · CLI + Followup Graph + Lazy News** | REPL + 单次 + 4 类追问 + 按需补新闻 | 真实端到端 1 案例 + 3 轮追问无卡顿 | 3-4 天 |
| **2.D · Eval Framework + 5 案例验收** | 5 case yaml + runner + 5 案例打分 ≥ 70 | Eval 报告通过 | 3-5 天 |

**总计：约 2-3 周。**

---

## 13. Phase 2 实施时再细化的开放问题

- 5 个金标案例的具体选定（等 news 语料 ingest 充分后再选）
- `cn_equity_sector_attribution.yaml` 中每维的 `query_template` 与 `evidence_filter` 微调
- Intent Classifier 在边界 case 上的精度（"为什么 X 涨 Y 也涨" 这种带子主题的归因）
- 追问 Graph 在多轮深挖后的 context 长度管理（虽然 1M 够用，但累积速度需要观察）
- 报告 markdown 文件的命名冲突处理（同日多次归因同 target）
- 单次归因总成本阈值的具体数值（草案 2 元，需实测调整）
