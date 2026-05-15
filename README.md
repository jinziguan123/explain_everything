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

**Phase 8 milestone (2026-05-15)** — Reflect Redesign + Multi-Signal + Falsifiability + Lifecycle。
482 tests pass，ruff 0。Phase 0-8 全部实施完，Phase 9 直接 motivation：
cross-session memory consolidation / theory formation / lifecycle 完整 8 阶段
（详见 [Phase 8 acceptance evidence](docs/plans/2026-05-15-cognitive-engine-phase-8-acceptance.md)）。

## Phase 8 (2026-05-15) — Reflect Redesign + Multi-Signal + Falsifiability + Lifecycle

修 Phase 7 acceptance 暴露的 4 个根本问题:

- ✅ re_expand 死循环 → `expand_downward` 替换 (Wave 1, 哲学 §8.1 rollout)
- ✅ 单信号 acceptance → 6 multi-signal + rollout_coverage (Wave 2, 哲学 §11.3)
- ✅ Mismatch 失明 → `input_validation` fail-fast (Wave 3, 哲学 §9.4 可证伪性)
- ✅ 节点无生命 → Variable lifecycle 3 阶段 + fitness + auto decay (Wave 4, 哲学 §6.1/§9.2/§9.3)

新 CLI flag:

- `explain run --no-input-check` — 跳过入口 question/observations 对齐校验 (兜底用)
- `explain check` 现含 "Multi-signal acceptance" + "Falsifiability" 两个 section

文档:

- design: [docs/plans/2026-05-15-cognitive-engine-phase-8-design.md](docs/plans/2026-05-15-cognitive-engine-phase-8-design.md)
- plan: [docs/plans/2026-05-15-cognitive-engine-phase-8-plan.md](docs/plans/2026-05-15-cognitive-engine-phase-8-plan.md)
- acceptance: [docs/plans/2026-05-15-cognitive-engine-phase-8-acceptance.md](docs/plans/2026-05-15-cognitive-engine-phase-8-acceptance.md)

## Phase 7 (2026-05-15) — Confidence + Forward Prediction + Reflection

新命令:

- `explain predict <sid> "<intervention>"` — 自然语言 forward prediction（B3 parser + LLM 生 predicted L0）
- `explain counterfactual <sid> "<substitute>"` — counterfactual 替换 / 删除（副作用 = 0）
- `explain rescore <sid>` — 重评 edge confidence（Wave A acceptance fixture, 重评后 Phase 6 simulation 真信号化）

`explain run` 现含 Reflection: loop 内动态决定 re-expand / prune / stop。1 round = K expand + 1 reflect。

边界（跟 Phase 6 一致）:

- 系统适合: 历史 / 常识 / 结构性 why-questions
- 系统不适合: 实时分析 / 强时效议题 / 依赖具体新近数据（LLM 易 hallucinate）
- Phase 7 forward prediction 适合 structural-mechanism 议题（"如果加入 X / 移除 Y"）; 不适合时事预测

Phase 7 数据点（详见 [Phase 7 acceptance evidence](docs/plans/2026-05-15-cognitive-engine-phase-7-acceptance.md)）:

- Wave A 真信号化生效, 但单 mechanism-scoring signal 区分 hallucinated 较弱（0.036 vs 0.15 设计阈值）。Multi-signal（weak_chains count）更强。
- Wave B 自然语言 intervention parser + forward predict + counterfactual user-facing 跑通。
- Wave C reflection 闭环（4 action: continue/re-expand/prune/stop）, anti-thrash 防死循环。

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
