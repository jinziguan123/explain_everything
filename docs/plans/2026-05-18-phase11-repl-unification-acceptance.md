# Phase 11 REPL Unification — Acceptance Checklist

> Design: [2026-05-18-phase11-repl-unification-design.md](2026-05-18-phase11-repl-unification-design.md)
> Plan: [2026-05-18-phase11-repl-unification-plan.md](2026-05-18-phase11-repl-unification-plan.md)

需 LLM key + 真终端. 10 步手测 cover ephemeral REPL + 18 slash + Wave 0 bug fix.

## Setup

1. HEAD = Wave 5 commit 或之后
2. `.venv/bin/python -m pytest -x` 应全 PASS (~806)
3. `.env` 含 LLM 配置 (LLM_PROTOCOL / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL)

## Smoke Steps

### S1: `explain` 默认进 REPL ephemeral

```bash
.venv/bin/python -m explain_engine.cli
# 或 `explain` (若已 pip install)
```

**预期**: 进 REPL, 顶端显
`Explain REPL — ephemeral session. 输入问题创建持久 session, /help 看 slash, /quit 退出, ctrl+o 看 log buffer.`

**失败模式**:
- 直接 typer 给 help (说明 callback 没注册成默认入口)
- BufferedLogHandler init 错 → log root 没 swap

### S2: 18 slash 列表

REPL 内输 `/help`. **预期**: 18 slash 全列 + 7 tool catalog.

- base (8): /quit /help /show /budget /compact /save /new /resume
- single-session (6 + /cf alias = 7 行): /compress /run /check /predict /counterfactual /cf /rescore
- cross-session (3): /list /lexicon /migrate

**失败模式**:
- 数 < 18 (某 wave 漏注册)
- alias `/cf` 缺 (Wave 3 没 fold)

### S3: ephemeral 时 single-session slash reject

REPL 内输 `/show`. **预期**: `slash_error` "尚未持久化, 输自然语言新建 session 或 /resume 选历史 session" (或类似 friendly hint).

同 `/compress` / `/check` / `/predict` / `/counterfactual` / `/rescore` 应都 reject (ephemeral session 没 sid + 没 graph).

**失败模式**:
- handler 直接 crash (没识别 ephemeral)
- 渲染 dict 字面 dump (UX noise)

### S4: ephemeral 时 cross-session slash work

REPL 内输 `/list`. **预期**: Rich Table 显当前 project 所有 session (sid / question / stage / updated), OR "当前 project 无 session" 友好提示.

同 `/lexicon` 应 work (lexicon 是 cross-session 数据, 不依赖 current chat sid). 空库时显 "暂无变量" 提示.

`/migrate --dry-run` 也应 work (一次性迁移命令).

**失败模式**:
- `/list` 强求 sid 报错
- table 显示空白 cell

### S5: 输自然语言触发 implicit /new (Wave 1 + Wave 2 HITL)

REPL 内输 "为什么年轻人不消费". **预期**:
- 进 LLM bootstrap (`promote_to_persistent`)
- 生 5-10 phenomena
- 弹 HITL k/e/d/q prompt (Wave 2 `review_phenomena_async` 真实装, 走 `chat.input_provider` → prompt_toolkit `read_input`)
- 接受 全 phenomena 后, 显 `Session s_xxx 已创建, 进入 chat 模式.`

**失败模式**:
- 弹 raw `input()` 撞 prompt_toolkit (Wave 2 async 没接通)
- bootstrap 失败 LLM error (查 .env)

### S6: 真 chat 模式 + /compress (Wave 3 + Wave 0 bug fix)

进 chat 后输 `/compress`. **预期**:
- LLM `propose_candidates` 5 候选
- HITL `review_insights_async` k/e/d 互动 (Wave 2 真实装)
- 完成时显 `compress 完成. N 候选保留. M var 写入 lexicon.`
- **关键 (Wave 0)**: 若 LLM 返 free text, anthropic_protocol retry 2 次后恢复. 连跑 3-5 次 /compress 不应稳定撞 "Pydantic validation failed" / SchemaValidationError.

**失败模式**:
- HITL 走 raw input() (Wave 2 没切 async)
- /compress 频繁撞 SchemaValidationError (Wave 0 prompt 强约束 / retry 失效)

### S7: /predict 走 input_provider 收 intervention

REPL 内输 `/predict`. **预期**: 弹 prompt "intervention 描述 (e.g. '如果 X 增加', q 取消): ". 输 "如果 X 增加" → `prediction.predict` 跑 → 显 new_node_ids / predicted_L0_ids.

输 `q` 应 cancel 干净 (slash_error "cancelled").

同 `/counterfactual` / `/cf` 走 sequential prompt 收 substitute.

**失败模式**:
- 不弹 prompt, 直接报 "missing arg" (slash 参数化没真接通)
- 弹 raw input() 撞 prompt_toolkit

### S8: /budget 改 limit (Wave 2.5 + review I-A)

REPL 内输 `/budget`. **预期**: 显 current limit (e.g. `per-turn: 10, per-session: 50, used N/M`) + sequential prompt 收 new per-turn / per-session. 输 `20` + `100` → 显 `[已更新] per-turn: 10 → 20, per-session: 50 → 100`.

退出 REPL 后, `cat ~/.explain/projects/<pid>/sessions/<sid>/chat_state.json | jq .budget` 应含 `{"limit_per_turn": 20, "limit_per_session": 100, ...}` (Wave 2.5 review I-A: 即时 persist, 不等 aclose).

**失败模式**:
- 改完不 persist (重启 REPL 看老值)
- 输入非 int 报 crash 而非 friendly error

### S9: Wave 0 bug fix 验证 (long-running)

连跑 10 次 `/compress` (重复 prompt 重 compress 同 session 或不同 session), 观察 console:

- "Forced tool_choice rejected by model (deepseek-v4-pro)" / "retrying with auto" log 仍可能出 (vendor 端 issue, 不能完全消除)
- 但 SchemaValidationError 报错频率应**大幅降低** (Wave 0 前用户报 "概率较高", 加 prompt JSON-mode 强约束 + retry 2 次后, 应 < 10% trial)

**失败模式**:
- 仍每次 /compress 都撞 SchemaValidationError (Wave 0 没真改 prompt yaml / retry logic 没 wire)

**注意**: Wave 0 fix 是统计性 mitigation (LLM 行为非确定), 不是 100% 解决. Pass 标准是 "明显改善" 而非 "零撞".

### S10: cli backward compat (老 typer cmd)

REPL 内 `/quit` 退出. 跑老 cli:

```bash
.venv/bin/python -m explain_engine.cli show <sid1>
.venv/bin/python -m explain_engine.cli compress <sid2>  # 注: compress 无 --no-chat flag (仅 new 有)
.venv/bin/python -m explain_engine.cli new "新问题" --no-chat
.venv/bin/python -m explain_engine.cli lexicon
.venv/bin/python -m explain_engine.cli list
.venv/bin/python -m explain_engine.cli check <sid1>
.venv/bin/python -m explain_engine.cli predict <sid1> "如果 X 增加"
.venv/bin/python -m explain_engine.cli counterfactual <sid1> "把 Y 替换为 Z"
.venv/bin/python -m explain_engine.cli rescore <sid1>
.venv/bin/python -m explain_engine.cli run <sid1>
.venv/bin/python -m explain_engine.cli migrate --dry-run
.venv/bin/python -m explain_engine.cli chat <sid1>
```

**预期**: 全部老 typer subcommand 仍 work, 0 break. 12 个 cmd 是 Phase 0-10 的接口, Phase 11 不许动.

**失败模式**:
- 某 cmd 报 unknown subcommand (`@app.callback()` 抢了路由)
- 某 cmd 内部依赖 chat_state 改了 schema 没 migrate

## Pass/Fail 标准

- **S1-S6**: 必过 (Phase 11 核心 flow)
- **S7-S8**: 应过 (interactive slash + budget persist)
- **S9**: best-effort (Wave 0 fix 是统计 mitigation)
- **S10**: 必过 (backward compat, 0 break 强约束)

任一 必过 step 不过 → 写 issue 含具体 step + 预期 vs 实际.

## Wave review fold ack (Wave 5 内)

Wave 5 顺手 fold 之前 wave review 留下的 doc minor:

| Wave | Issue | Fold |
|---|---|---|
| 0 | `anthropic_protocol.MAX_RETRIES_ON_MALFORMED` 注释含糊 | 加注释解释 "本 retry 仅 cover `parsed=None` (free-text fallback); schema-shape malformed (Pydantic ValidationError) 走调用方 outer `_llm_retry` layered defense" |
| 3 | `slash_commands._command_by_name` docstring 写 "8 commands" (Phase 9 时数) | 改 "small N (~20 commands)" generic |
| 4 I-A | `repl_entry.py` real chat slash_switch_session 处理用 immediate-break, vs `cli._run_chat_repl_async` 的 post-loop swap | 加注释解释为何不同 (REPL 这边 handle_user_input 通常只 yield 1 个 slash_switch_session 来自 /resume 单 dispatch; cli 的 post-loop swap 是 forward-proof). 保 break idiom, 不强制统一 |

## 长期 (Phase 12+) follow-up

- **Phase 12 Theory Formation**: motif detection on cross-session graph (lexicon 是输入数据层), 评估 Neo4j (图 pattern matching)
- **Candidate E Variable Embedding**: sentence-transformer + pgvector 解决 canonical_mechanism 漂移导致的 split
- **Candidate D Cognitive Energy**: reflection 量化 + attractor detection
- **/new HITL review_phenomena 加 sync 版**: Wave 2 simplification (单一 add-loop), 看用户能否真需要 sync 版
- **/run budget UX**: `chat.state.budget_remaining` 低时主动提示 user

## 参考

- Wave 0-4 commits:
  - Wave 0: `a246a80` (LLM retry + JSON 强约束)
  - Wave 1: `bb890a5` (EphemeralChatSession + hitl async stub) + `7fc009d` (enter_repl_async + cli 入口)
  - Wave 2: `d4f0f8b` (HITL review_*_async 真实装)
  - Wave 2.5: `2b44b70` (/budget config 流) + `a907e05` (review I-A/I-B fix)
  - Wave 3: `a992579` (6 single-session slash + /cf alias)
  - Wave 4: `ee853c1` (3 cross-session slash + Wave 1 I-4 fold)
- Design: [2026-05-18-phase11-repl-unification-design.md](2026-05-18-phase11-repl-unification-design.md)
- Plan: [2026-05-18-phase11-repl-unification-plan.md](2026-05-18-phase11-repl-unification-plan.md)
