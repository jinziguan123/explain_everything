# Explain Agent

A 股板块/主题异动归因 AI agent。

- [设计文档](docs/plans/2026-05-11-explain-agent-design.md)
- [Phase 1 实施计划](docs/plans/2026-05-11-explain-agent-phase1.md)

## 快速开始

```bash
uv sync
cp .env.example .env  # 填写真实凭证
uv run pytest
```

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

