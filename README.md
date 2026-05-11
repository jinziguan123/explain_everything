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
