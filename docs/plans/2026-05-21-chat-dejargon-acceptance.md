# Phase 15 Acceptance: chat REPL 去技术化

**Date**: 2026-05-21

## Manual Smoke Test (11 步)

### 1. Ephemeral REPL 启动

```bash
.venv/bin/python -m explain_engine.cli
```

预期: banner 进入 ephemeral chat 模式 (空白状态).

### 2. /help 验输出全中文 + 6 中文 group

```
> /help
```

预期: 含 6 个中文 group header:
- `推进 session:`
- `干预分析 (需先 /compress):`
- `查看状态 (只读):`
- `管理 session:`
- `其他:`
- `帮助 / 退出:`

19 条 command desc 都含中文且 ≤50 字. 工具行 "可被 LLM 调用的工具:" + "[只读]"/"[需人工审查]" 标签. 别名行 "别名: /cf → /counterfactual".

### 3. 输自然语言 question → promote

```
> 为什么年轻人储蓄少了
```

预期: 进真 session (经 bootstrap → HITL 现象审查 → done stage).

### 4. /show 验输出中文术语

```
> /show
```

预期: 4 section:
- `=== 当前 session ===` (含 SID / 问题 / 阶段 — stage 用中文如"已归纳")
- `=== 因果图 (N 节点: x 现象 / y 模式 / z 深层原因; ...) ===`
- L0/L1/L2 → `[现象]` / `[归纳出的模式]` / `[深层原因]`
- `=== 因果关系 (N 条) ===` + edge type 含中文注释 (e.g. `manifests_as「体现为」`)
- `=== 接受度评估 ===` 含 `一致性:` / `本质重要性:` / `覆盖率:` / `薄弱因果链:` / `输入对齐度:`

不见 `L0` / `L1` / `L2` / `abstraction` / `Multi-signal` / `graph` 英文字面.

### 5. /run on bp 验 stage error 中文

stage 仍 `bootstrap_pending` 时输:

```
> /run
```

预期 (with_stage_gate gate fail):
- error: "/run 在当前阶段 (等待启动) 不能跑 — 需要阶段为: 已归纳."
- hint: "需要先 /compress 把现象归纳成模式, 才能跑这个命令."

### 6. /compress 验中文 status + 完成 msg

```
> /compress
```

预期 (假设 LLM 配置好):
- spinner "正在归纳模式..."
- spinner "正在评分候选模式..."
- info dim "(中间状态已保存, 取消审查可下次重入跳过 LLM)"
- HITL 中文 prompt: 候选模式 table 标题 "候选模式 (按归纳收益降序)" + 列 "覆盖现象数" / "归纳收益"; 单条选 prompt "[k] 保留 / [e] 编辑 / [d] 删除 / [v] 查看完整覆盖"
- spinner "正在存盘到概念库..."
- 完成 msg: "归纳完成: 加了 N 个模式, 其中 M 个写入概念库." + dedup 行 "其中 X 个与已有模式相似 (跨 session 复用), Y 个全新."
- next-step hint: "▸ 接下来可选: /run / /predict / /counterfactual..."

### 7. /run 完成 stop_reason 中文

```
> /run
```

预期:
- spinner "正在自动推理 (扩展 / 反思 / 衰减)..."
- 完成 msg: "推理完成: 在第 K 步停止 (原因: 已收敛 (无更多可推进点))." (或对应 stop_reason 翻译).
- next-step hint after_run.

### 8. /predict 中文

```
> /predict
```

预期 prompt: "干预描述 (e.g. '如果 X 增加', q 取消): "

输个干预后:
- spinner "正在预测干预影响..."
- 输出 header: "预测结果 (干预: '...'):"
- 子 section: "新增节点:" / "预测的现象:" / "激活的已有现象:" / "影响最大的 3 个中间节点 (正负号 = 激活变化方向):"
- 不见 `prediction (intervention=...)` 或 `new_nodes:` 英文.

### 9. /rescore 中文

```
> /rescore
```

预期:
- spinner "正在重评因果关系..."
- msg: "重评完成: N 条因果关系, 平均可信度 X.XX. 已存盘." 或 "重评完成: 无可 rescore 的因果关系 (体现为 / 导致 类型)."

### 10. /budget 耗尽 验 "本轮" / "本 session"

- /budget 设 per-turn=1 + per-session=2
- 让 LLM 调用快速耗尽 (输自然语言问题)
- 预期 BudgetExhaustedEvent 渲染: `[yellow]预算耗尽 (本轮). 用 /budget 重设或开新一轮对话.[/yellow]` (per_session 时 → "本 session")

### 11. /quit 验中文 farewell

```
> /quit
```

预期: "再见, session 已存盘."

## 通过标准

11 步全过, **任何步骤输出有 L0/L1/L2/abstraction/propagation/lexicon/HITL/reasoning loop 等 jargon 直露 = FAIL**.

跑完后再跑全量 pytest + ruff:

```bash
.venv/bin/python -m pytest
```

→ 0 失败.

```bash
.venv/bin/ruff check src/ tests/
```

→ 0 警告.

## 已知 jargon 保留点 (intentional)

- `manifests_as` / `causes` (edge relation_type raw key) — engine field, 用户 cross-reference 用. 已加中文注释 `「体现为」` / `「导致」`.
- `Session` 词在 `Session 列表` / `当前 session` 标题保留 (跟 sid 概念绑, 改成"会话"会比"session"更陌生).
- `LLM` 缩写在错误消息保留 ("/predict 需要 LLM (启动时没配置).").
- `dot` / `graphviz` / `chafa` 工具名 (安装命令用).
- `slash` 命令前缀字面 `/` (无中文等价).
