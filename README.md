# Explain Engine

通用解释引擎（Cognitive Engine for explanation-centric reasoning）。

> 不是 chatbot，不是 workflow agent，是一个 World Explanation Engine。

## 设计文档

- [设计哲学](设计哲学.md) — 项目本质 / 解释哲学 / 世界观
- [需求设计](需求设计.md) — 核心能力 / 系统分层
- [技术设计 v1](技术设计v1.md) — Cognitive Runtime 完整愿景
- [技术设计 v2](技术设计v2.md) — 长期 roadmap（persistent world model / evolving variable / multi-perspective / simulation / meta-cognition）
- [MVP Design Doc](docs/plans/2026-05-13-cognitive-engine-mvp-design.md) — 当前实现的范围与取舍
- [Phase 0+1+2 Plan](docs/plans/2026-05-13-cognitive-engine-phase-0-1-2-plan.md)

## MVP 目标

从乱七八糟现象里**压出一个隐藏高维变量**（compression / hidden variable discovery）。

输入: `"为什么年轻人不消费"`
输出: `ExplanationGraph`，其中包含一个 abstract variable（如"长期不确定性"），通过 `manifests_as` 边连接到多个 concrete 现象。

## Status

🚧 **Pre-alpha** — Phase 0+1+2（scaffold + schema + LLM client）实施中。
Phase 3-6（Engine + Runtime + CLI + 评估）后续规划。

## 开发

```bash
uv sync                                  # 安装依赖
cp .env.example .env                     # 配置 LLM (详见下)
uv run pytest                            # 单测
uv run ruff check .                      # 静态检查
uv run mypy src/                         # 类型检查
```

## LLM 配置 (Phase 5+)

Phase 5 起协议跟供应商解耦，配 4 个 env var:

- `LLM_PROTOCOL`: `anthropic` 或 `openai`
- `LLM_BASE_URL`: API 入口（详见 `.env.example`）
- `LLM_API_KEY`: API key
- `LLM_MODEL`: 模型名

可选: `LLM_STRUCTURED_OUTPUT_MODE` (openai 协议下 `json_schema` (默认) / `json_object`)

### Phase 4 → Phase 5 配置迁移

| Phase 4                              | Phase 5 等价                                                                |
|---|---|
| `LLM_PROVIDER=claude`                | `LLM_PROTOCOL=anthropic` + `LLM_BASE_URL=https://api.anthropic.com`         |
| `LLM_PROVIDER=openai`                | `LLM_PROTOCOL=openai` + `LLM_BASE_URL=https://api.openai.com/v1`            |
| `LLM_PROVIDER=deepseek` (openai)     | `LLM_PROTOCOL=openai` + `LLM_BASE_URL=https://api.deepseek.com/v1` + `LLM_STRUCTURED_OUTPUT_MODE=json_object` |
| `LLM_PROVIDER=deepseek` (anthropic)  | `LLM_PROTOCOL=anthropic` + `LLM_BASE_URL=https://api.deepseek.com/anthropic` |
| `CLAUDE_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` | `LLM_API_KEY` (统一)                              |

## 历史

- `master` 分支：A 股板块归因 agent（Phase 2.D-3，**产品死亡**）
- `cognitive-engine-mvp` 分支：通用解释引擎，从 0 重做
