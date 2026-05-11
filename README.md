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

