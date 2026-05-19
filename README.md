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

**Phase 12 milestone (2026-05-19)** — /show + /graph Detail (`/show` 全展开 graph 4 section + 新加 `/graph` graphviz inline 渲染)。
866 tests pass，ruff 0。Phase 0-12 全部实施完。下一步: Phase 13 Theory Formation + Candidate E Variable Embedding。

**Phase 11 milestone (2026-05-18)** — REPL Unification (default `explain` 进 ephemeral REPL + 18 slash 化)。
806 tests pass，ruff 0。

**Phase 10 milestone (2026-05-18)** — Persistent World Model (跨 session Variable Lexicon)。
728 tests pass，ruff 0。

**Phase 9 milestone (2026-05-17/18)** — Conversational Cognitive Engine (Claude Code 风格 chat REPL)。
chat /new + /resume slash 命令 + prompt_toolkit REPL UX 升级。Phase 11 直接 motivation：
cross-session motif detection on lexicon graph / real Anthropic tool_use adapter
（详见 [Phase 9 acceptance evidence](docs/plans/2026-05-17-conversational-cognitive-engine-acceptance.md)）。

## Phase 12 (2026-05-19) — /show + /graph Detail

`/show` 从原 5-line summary 升级为 **4 section 全展开** (Session → Graph by L0/L1/L2 + 核心字段 → Edges 按 relation_type 分组 → Multi-signal verdict)。新加 `/graph` slash 走 **graphviz inline 渲染** (terminal 自动检测 iTerm2/Kitty/Ghostty/chafa)，PNG 进 process-level tempdir，REPL 退出 atexit 自动清理。

**核心 (brainstorming Q&A 锁)**:
- /show: 整张 graph 全貌 (默认), 节点字段 = id+name+desc+conf+epistemic, edge 按 type 分组
- /graph: 独立新 slash (18 → 19 total), text + visual 互补 (text 看字段, visual 看拓扑)
- PNG: 临时 tempdir + atexit cleanup (退出磁盘干净)
- 终端 capability: iTerm imgcat > Kitty graphics protocol > chafa unicode > "(install chafa)" fallback

**Visual encoding**:
- L0 box lightblue / L1 ellipse lightyellow / L2 doubleoctagon lightcoral
- Lifecycle: decayed 虚线 gray80, stale 点线; weak L1 红边框
- Edge type: causes 实线, amplifies 粗, suppresses red, constrains blue, manifests_as 虚线

**19 个 slash command** (18 → 19, 新加 /graph):
- base (8): /quit /help /show /budget /compact /save /new /resume
- single-session (6 + /cf alias): /compress /run /check /predict /counterfactual /rescore /cf
- cross-session (3): /list /lexicon /migrate
- **Phase 12 (1)**: /graph

**系统 dep**:
- `brew install graphviz` (必, 提供 `dot` binary; 缺时 /graph 友好 error 不 crash)
- `brew install chafa` (可选, 通用 inline fallback; 缺时输 PNG path + install hint)

**文档**:
- design: [docs/plans/2026-05-19-slash-show-graph-detail-design.md](docs/plans/2026-05-19-slash-show-graph-detail-design.md)
- plan: [docs/plans/2026-05-19-slash-show-graph-detail-plan.md](docs/plans/2026-05-19-slash-show-graph-detail-plan.md)

## Phase 11 (2026-05-18) — REPL Unification

`explain` 默认进 **prompt_toolkit REPL ephemeral session** (in-memory, 不持久化)。用户首句自然语言 → 自动 implicit `/new` 走 bootstrap+HITL+持久化。12 个 typer cmd 全部做成 slash (含 `/cf` alias of `/counterfactual`), 所有 slash **无 explicit 参数** — 需参数走 `chat.input_provider` sequential prompt。`/budget` config 流取代 cli flag。

**核心 (brainstorming Q&A 锁)**:
- 首句自然语言: 自动 implicit /new (option 1)
- typer cmd 转 slash: 全 12 个 (15 default + /cf alias + 3 cross-session = 18 total)
- HITL 嵌入: async + input_provider (review_phenomena_async + review_insights_async)
- slash 参数化: 全无 explicit 参数, 需参数走 sequential prompt
- budget 调: /budget config slash (删 cli --tool-budget-* flag)
- cli mode: typer subcommand 路径 0 break

**18 个 slash command**:
- base (8): /quit /help /show /budget /compact /save /new /resume
- single-session (6 + /cf alias): /compress /run /check /predict /counterfactual /rescore /cf
- cross-session (3): /list /lexicon /migrate

**新 cli 入口**:
- `explain` — 默认进 REPL ephemeral
- `explain <subcommand> [args]` — 老 typer cli (12 subcommand, 0 break)

**含 Wave 0 deepseek-v4-pro 400 bug fix**: compress 频繁撞 HTTP 400 (forced tool_choice → auto → Pydantic catch malformed)。Fix: prompt 加 JSON schema 强约束 + retry 2 次兜底。

**文档**:
- design: [docs/plans/2026-05-18-phase11-repl-unification-design.md](docs/plans/2026-05-18-phase11-repl-unification-design.md)
- plan: [docs/plans/2026-05-18-phase11-repl-unification-plan.md](docs/plans/2026-05-18-phase11-repl-unification-plan.md)
- acceptance: [docs/plans/2026-05-18-phase11-repl-unification-acceptance.md](docs/plans/2026-05-18-phase11-repl-unification-acceptance.md)

## Phase 10 (2026-05-18) — Persistent World Model (Variable Lexicon)

`knowledge/` 目录从 Phase 9 占位空目录变 **跨 session Variable Lexicon**. 高 fitness L1/L2 节点累积成 reusable abstractions, 新 session bootstrap 时 LLM 看 Top-K 作 prior.

**核心 (design Q&A 锁)**:
- 单位: Variable Lexicon (mechanism / theory 留 Phase 11)
- 写入: session done auto-flush (compress 完 + chat aclose)
- 读取: bootstrap 看 Top-K=20 by composite fitness (reuse × essentialness)
- token cap: per-var render cap, 总 ~1.7k token (deterministic, 不依赖 embedding)
- storage: local JSON (远程存 Neo4j/pgvector 留 Phase 11+ 再评估)

**新 CLI commands**:
- `explain lexicon` — 列 lexicon (Rich Table)
- `explain lexicon --dump-json` — raw JSON
- `explain lexicon --top-k N` — 仅显 top-K
- `explain new --lexicon-top-k 0` — 跳过 lexicon prior

**文档**:
- design: [docs/plans/2026-05-18-phase10-persistent-world-model-design.md](docs/plans/2026-05-18-phase10-persistent-world-model-design.md)
- plan: [docs/plans/2026-05-18-phase10-persistent-world-model-plan.md](docs/plans/2026-05-18-phase10-persistent-world-model-plan.md)
- acceptance: [docs/plans/2026-05-18-phase10-persistent-world-model-acceptance.md](docs/plans/2026-05-18-phase10-persistent-world-model-acceptance.md)

## Phase 9 (2026-05-17) — Conversational Cognitive Engine

把 cognitive engine 从 CLI 命令式升级为 Claude Code 风格 conversational agent.
用户 `explain chat <sid>` 进 REPL, LLM 自主调度 7 tool 边推理边给 narrative.

**7 个 tool** (LLM 可调度): expand / compress / check / predict / counterfactual / add_observation / read_node
**8 个 slash command** (本地 intercept, bypass LLM): /quit /help /show /budget /compact /save /new /resume

新 CLI commands:

- `explain chat <sid>` — 进 conversational REPL (Phase 9 主入口)
- `explain migrate [--dry-run]` — 一次性把 Phase 0-8 老 `sessions/*.json` 迁到 `~/.explain/projects/<proj>/sessions/<sid>/`

新基础设施 (Q1-Q7 brainstorming 锁定):

- ✅ **Full Agent loop** (Q1β) — Two-layer outer/inner pattern (ChatSession + query_loop)
- ✅ **Curated 7-tool set** (Q2β + Q4β) — 5 engine wrap + add_observation (mutation) + read_node (lazy load)
- ✅ **老 CLI 并存** (Q3B) — 482 老 test 零回归
- ✅ **Dual budget + smart HITL** (Q5γ) — per-turn (10) + per-session (50), `add_observation(source=llm_inferred)` 触发 user confirm
- ✅ **3-tier compaction** (Q6γ) — microCompact (drop stale tool_result) + sessionMemory splice + emergency sync compact
- ✅ **Project-based persistence** (Q7γ-1) — `~/.explain/projects/<project_id>/sessions/<sid>/` 5 sidecar files + `knowledge/` 占位 Phase 10+

**2026-05-18 增强** — chat 内 session 管理 + prompt_toolkit REPL UX:

- ✅ **/new `<question>`** — 在 chat 内建新 session (复用 bootstrap + HITL), 自动 in-process 热切到新 session
- ✅ **/resume** — numbered picker 列当前 project 历史 session, 选号热切 (`SessionStore.list()` 直接复用)
- ✅ **slash 自动联想** — 输 `/` 弹下拉菜单含 8 cmd + description, 输 `/r` filter 到 `resume`
- ✅ **ctrl+o log popup** — chat 模式期间 LLM HTTP + session_memory_writer log 默认隐藏 (灰色样式), ctrl+o 弹 message_dialog 看 buffered log
- ✅ **bottom toolbar** — 显示 `ctrl+o: log (N lines buffered)` + 退出提示
- ✅ **patch_stdout** — LLM 调用期间 log 不撞 prompt 编辑行 (修 Phase 9 原 readline 撞 prompt regression)
- ✅ **中文 backspace 无残影** — prompt_toolkit 替 readline, 修 Phase 9 多字节 cursor 错位 bug

设计 / 落地 docs:
- design: [docs/plans/2026-05-18-chat-new-resume-slash-design.md](docs/plans/2026-05-18-chat-new-resume-slash-design.md), [docs/plans/2026-05-18-chat-repl-prompt-toolkit-design.md](docs/plans/2026-05-18-chat-repl-prompt-toolkit-design.md)
- acceptance: [docs/plans/2026-05-18-chat-repl-prompt-toolkit-acceptance.md](docs/plans/2026-05-18-chat-repl-prompt-toolkit-acceptance.md) (8 步手测 checklist)

文档:

- design: [docs/plans/2026-05-17-conversational-cognitive-engine-design.md](docs/plans/2026-05-17-conversational-cognitive-engine-design.md)
- plan: [docs/plans/2026-05-17-conversational-cognitive-engine-plan.md](docs/plans/2026-05-17-conversational-cognitive-engine-plan.md)
- acceptance: [docs/plans/2026-05-17-conversational-cognitive-engine-acceptance.md](docs/plans/2026-05-17-conversational-cognitive-engine-acceptance.md)

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
