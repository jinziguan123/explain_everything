# Phase 14 Acceptance: Chat Stage Flow + Next-Step Hints

**Date**: 2026-05-21
**Plan**: [2026-05-21-chat-stage-and-hints-plan.md](2026-05-21-chat-stage-and-hints-plan.md)
**Design**: [2026-05-21-chat-stage-and-hints-design.md](2026-05-21-chat-stage-and-hints-design.md)

---

## Scope

5 mutating slash 命令 (`/compress` / `/run` / `/predict` / `/counterfactual` / `/rescore`)
全部走 `with_stage_gate` 装饰器, REPL 全程闭环 stage 流转 + 静态 next-step hint
(灰色 dim 渲染). `/help` 6 分组. `/compress` 中断恢复 (mid-stage `insight_pending`).

## 自动化 verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
```

预期: 977 pass + ruff clean (Task 17 收尾时已确认).

## Manual smoke (10 步)

每步标注**验证点**: 看终端输出对照预期, 不通过直接 abort 上报.

### Step 1: Ephemeral REPL 启动

```bash
.venv/bin/python -m explain_engine.cli
```

**验证**:
- 输出 `Explain REPL — ephemeral session.` banner
- 提示输自然语言 / `/help` / `/list` 等

### Step 2: 自然语言输入 → promote 到真 session

```
> 为什么年轻人储蓄少了
```

**验证**:
- bootstrap 跑 (LLM call 2-3 个), 完成后 banner 提示已 promote 到 `s_xxxxxxxx`
- prompt prefix 从 `(eph)` 变 `(s_xxxxxxxx)`

### Step 3: /show 验 stage=bootstrap_pending

```
> /show
```

**验证**:
- 输出含 `Stage: bootstrap_pending`
- 显示 graph 当前 root + 几个 L0 observation node

### Step 4: /run 拒 + hint (灰色)

```
> /run
```

**验证**:
- `slash_error` 文本: `/run 在当前 stage='bootstrap_pending' 不允许 (需 stage ∈ ['done']).`
- **灰色 dim hint**: `需要先 /compress 压缩 graph 抽出 abstraction 层. 当前 stage 不允许这个命令.`
- stage 不变

### Step 5: /compress → 推 done + hint

```
> /compress
[propose 候选 — Status spinner 5-15s]
[score_all — Status spinner 5-15s]
[(中间状态已保存, 即便 review 取消也能下次重入跳过 LLM)]  ← Task 14 dim 提示
[HITL review 列候选, Enter accept]
[写入 lexicon — Status spinner]
```

**验证**:
- 完成提示: `compress 完成. N 候选保留. M var 写入 lexicon. compress dedup: ...`
- **灰色 dim hint**:
  ```
  ▸ 下一步可选:
    /run — 自动跑 reasoning loop 推 drivers (推荐)
    /predict <现象> — 预测某干预的下游效果
    /counterfactual <现象> — 反事实分析
  ```
- `/show` 验 `Stage: done`

### Step 6: /run → 推 converged + hint

```
> /run
[reasoning loop — Status spinner, 多次 LLM 调用, 几十秒-几分钟]
```

**验证**:
- 完成: `reasoning loop 完成: stop_reason=..., tick=N`
- **灰色 dim hint**:
  ```
  ▸ session 已收敛. 可选:
    /predict <现象> — 干预预测
    /counterfactual <现象> — 反事实
    /show — 看完整 graph
  ```
- `/show` 验 `Stage: converged`

### Step 7: /predict <text> → stage 不变 + hint

```
> /predict
> (sub-prompt) 假设 X 行业资本撤退
[Status spinner, 一次 LLM call ~10s]
```

**验证**:
- 输出 prediction (new_nodes / predicted_L0 / propagation)
- **灰色 dim hint**: `▸ 可继续 /predict 或 /counterfactual 探索, /show 看 graph 更新.`
- `/show` 验 `Stage: converged` (未变)

### Step 8: /compress 重跑 → gate 拒 (stage=converged) + hint

```
> /compress
```

**验证**:
- `slash_error`: `/compress 在当前 stage='converged' 不允许 (需 stage ∈ ['bootstrap_pending', 'insight_pending']).`
- **灰色 dim hint**: `session 还没启动 — 自然语言输入一个 question 先建 session, 然后再 /compress.`
- 无副作用 (不进 propose)

### Step 9: /help → 6 分组

```
> /help
```

**验证 (输出形如)**:
```
Available slash commands (local, bypass LLM):

  Session 推进:
    /compress — Compress 当前 session ...
    /run — 跑 reasoning loop ...
    /rescore — 重评 edge.confidence ...

  Session 干预 (需先 /compress):
    /predict — Forward prediction: ...
    /counterfactual — Counterfactual: ...

  Inspection (read-only):
    /show — Show graph snapshot ...
    /graph — 渲染 graph 可视化 ...
    /check — Multi-signal acceptance report ...

  Session 管理:
    /new — 重置 chat: ...
    /resume — 列历史 session, ...
    /list — 列当前 project 所有 session ...
    /lexicon — 列 cross-session lexicon variables.

  其他:
    /budget — Show budget + interactive config ...
    /compact — Force trigger sessionMemory compaction.
    /save — Explicit flush of all sidecar files.
    /migrate — 一次性迁老 sessions/*.json → storage_v2 layout.

  帮助 / 退出:
    /help — List slash commands ...
    /quit — Exit chat session (saves first).

  Alias: /cf → /counterfactual

Available tools (LLM-callable):
  expand
  compress
  ...
```

**验证**:
- 6 个 group header 都在
- 18 个 command 都列出
- `Alias: /cf → /counterfactual` 单行
- Available tools 段保留

### Step 10: Mid-stage resilience

```
> /new
[REPL 重置回 ephemeral]
> 为什么 GDP 增速放缓
[promote 到新 session]
> /compress
[propose 跑]
[score 跑]
[(中间状态已保存, 即便 review 取消也能下次重入跳过 LLM)]
[HITL review 跳出第一个候选, 输 `q` 取消]
[KeyboardInterrupt 或类似中断]
> /show
```

**验证 (中断后)**:
- `/show` 验 `Stage: insight_pending`

```
> /compress
```

**验证 (重入)**:
- 终端显示: `(检测到 stage=insight_pending, 跳过 LLM 直接进入审查)`
- 直接进 HITL review (无 propose / score LLM 调用 — 秒级响应)
- 完成 review + flush → stage 推到 `done` + hint

---

## 通过标准

10 步全部 ✓ = Phase 14 acceptance pass.

任一步 ✗ → 回滚到 plan 相关 task 重审 / 补 test 锁住该 case.

## 已知 caveat

- `/predict` / `/counterfactual` 装饰器 `success_stage=None` (不动 stage). 即用了多次也不破 converged. 设计意图: prediction 是 read-only-ish 探索, 不应推 stage 回退或前进.
- `/rescore` `allowed=None` (任意 stage 允许). 设计: edge confidence 重评是 maintenance 操作, 跟 stage 无关.
- `/compress` 短路用 `dedup_stats` fallback (`0 near-dup / N new`) 因 ip 入口无 embedding 重计 — 显示数字会跟 bp 首次跑的不一致, 这是 known trade-off (避免 LLM cost).
