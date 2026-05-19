# REPL 处理中视觉反馈 (Status Spinner) — Design

> 上一 phase: [Phase 11 REPL Unification](2026-05-18-phase11-repl-unification-design.md)
> 当前 HEAD: `fc761d3` (806 PASS, ruff 0)

**日期**: 2026-05-19
**分支**: `dev`

---

## 0. TL;DR

REPL 内 LLM 调用期间 (5-15s) 加 Rich `console.status()` spinner 反馈, 让用户知道系统正在处理. 6 个 hot path wrap, ~50 行改动, 1 commit.

---

## 1. 背景

用户反馈:
> "用户输入完内容之后, 最好能够优化一下 REPL 的界面, 让用户明白系统现在正在处理自己的输入"

Phase 11 chat REPL 内输 `/compress` / `/predict` / 自然语言 → 触发 LLM 调用 5-15s. 期间终端静默 (BufferedLogHandler 接 stdout, log 不显). 用户看似"卡死", UX 差.

## 2. Scope

### 2.1 加 spinner (6 hot path)

| Hot path | 操作 | spinner text |
|---|---|---|
| `ephemeral.promote_to_persistent` | bootstrap_phenomena | "调 LLM 生现象..." |
| `_handle_compress` | propose_candidates + flush | "调 LLM 提候选 (compress)..." |
| `_handle_predict` | prediction.predict | "调 LLM 跑 prediction..." |
| `_handle_counterfactual` | counterfactual.substitute | "调 LLM 跑 counterfactual..." |
| `_handle_run` | runtime.run | "调 LLM 跑 reasoning loop..." |
| `_handle_rescore` | rescore_session | "调 LLM 重评 edge..." |

### 2.2 不做 (YAGNI)

- 立即返回 slash (`/help` / `/list` / `/show` / `/save` 等) — noise
- HITL 期间 (`review_phenomena_async` k/e/d) — prompt 已显式 wait
- 进度 percentage — LLM 不知 ETA, % 无意义
- elapsed time — Rich Status 已内置 (自动显示)
- 嵌套 spinner (e.g. `/compress` 含 `flush_to_lexicon` 含 `_build_canonical_mechanism` 多次 LLM) — 外层 1 spinner 即可

## 3. 方案

### 3.1 Rich `console.status()` context manager

```python
from rich.console import Console
console = Console()  # 或 module-level reuse

with console.status("[bold green]调 LLM 生现象..."):
    phenomena = await bootstrap_phenomena(question, llm, lexicon=lexicon)
```

Rich 自动:
- 渲染 spinner (`⠋ ⠙ ⠹ ...`) 在终端当前行
- ANSI cursor 控制不破坏前后输出
- ctx 退出自动 clean up

### 3.2 与 patch_stdout 关系

Phase 11 `read_input(pt_session)` 内 `with patch_stdout(): ...`. patch_stdout 在 prompt_async 返回后退出. **LLM 调用发生在 patch_stdout 退出之后** (在 handle_user_input 或 promote 流), sys.stdout 已恢复, Rich Status spinner 能正常渲染.

### 3.3 console 实例化

- `ephemeral.py` 已 local `console = Console()` (Phase 11 Wave 1)
- `slash_commands.py` 每 handler 内 local `console = Console()` (current pattern)
- 不抽 module-level (跨 module 复用反而 leaky)

## 4. 实施

### 4.1 改动文件 (2 个)

| 文件 | 改动 |
|---|---|
| `src/explain_engine/chat/ephemeral.py` | `promote_to_persistent` 内 bootstrap_phenomena 包 status |
| `src/explain_engine/chat/slash_commands.py` | 5 handler (compress / predict / counterfactual / run / rescore) 包 status |

总: ~6 处 `with console.status(...):` wrap, ~30-50 行 code change.

### 4.2 行为 invariant

- spinner ctx exit 后, 后续 `console.print` 正常输出 (Rich 自管 cursor restoration)
- 异常路径: LLM 抛 error → spinner ctx 自动 exit (cleanup OK) → 异常 propagate 到 handler try/except → 友好 error message
- 测试环境 (CliRunner / no-tty): Rich Status 自动 fallback 静默 (Rich 内部 `is_terminal` detection), 不破现有 test

### 4.3 测试

- 不加 spinner-specific test (Rich Status 是 cosmetic, 无法 reliably 在 test 验 spinner 动画)
- 跑 806 baseline 验所有现有 test 仍 PASS (wrap 不影响 LLM call 返回值)
- 可选 1-2 sanity test: mock LLM + dispatch_slash, 验 handler 不 raise

## 5. 不变量 / Risk

| Risk | Mitigation |
|---|---|
| Rich Status 与 BufferedLogHandler 冲突 | 不会 — Status 走 sys.stdout, BufferedLogHandler 接 logging chain, 是 2 个 channel |
| Status spinner 在 patch_stdout 内 freeze | 不会 — LLM call 不在 patch_stdout ctx 内 |
| 嵌套 status (compress 内 flush 内 canonical_mech) | 外层 1 spinner OK; inner LLM 在 spinner 下不另起 (Rich Status 不支持嵌套, inner 调时外 spinner 仍转) |
| Status text 国际化 | YAGNI — 中文 hardcode, 后续 i18n 时统一处理 |
| `/run` 多次 LLM 调用一个 spinner | text 设 "reasoning loop..." 模糊, 不暴露 iter 数. 用户看到持续转 spinner 就行 |

## 6. 决策摘要

| Q | 选项 | 决策 |
|---|---|---|
| Spinner 库 | Rich Status / prompt_toolkit bottom_toolbar / 手写 ANSI | **Rich Status** (与 Phase 11 console.print 同 channel, 0 新 dep) |
| Hot path 范围 | 全部 LLM call / 仅 long op / 用户选 | **6 个 long LLM op** (slash 立即返不加) |
| Status text 详细度 | 操作 + 模型名 + ETA / 仅操作名 | **仅操作名** (短 + 清楚) |
| 嵌套 spinner | 每 LLM call 独立 / 外层 1 spinner | **外层 1 spinner** (避 conflict) |

## 7. 落地

1 commit, 2 文件改, ~50 行, 30 min.

直接 implement, 不分 wave (scope 小).

---

## 8. 关联

- Phase 11: [2026-05-18-phase11-repl-unification-design.md](2026-05-18-phase11-repl-unification-design.md)
- Rich Status docs: https://rich.readthedocs.io/en/stable/live.html#displaying-a-status
- ephemeral.py: `src/explain_engine/chat/ephemeral.py:84-128`
- slash_commands.py handler 路径: `src/explain_engine/chat/slash_commands.py:484-787`
