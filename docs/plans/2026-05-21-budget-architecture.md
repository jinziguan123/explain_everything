# Budget 体系架构 (chat vs runtime 两套预算)

**Date**: 2026-05-21
**Status**: 现状 + Phase 15.1 hotfix 决策记录
**Related**:
- Phase 5 (引入 `CognitiveState.budget_remaining` runtime tick budget)
- Phase 9 (引入 `chat_state.budget_per_turn/session_limit` chat LLM call budget)
- Phase 11 Wave 3 (chat `/run` 用 `state.budget_remaining` 作 tick budget)
- Phase 15.1 hotfix (修 `/run` 不 honor `/budget` 设无限的 UX 坑)

## 1. 为什么有这个 doc

用户 (2026-05-21) 跑 chat REPL 时:
1. `/budget` 设 `per_turn=0, per_session=0` (无限)
2. 跑 `/run` → `推理完成: 在第 5 步停止 (原因: 预算耗尽).`
3. 困惑: "为什么会预算耗尽? 我都设无限了啊"

排查后发现 chat 有 **两套命名相同语义独立的 budget**, `/budget` 命令只动其中一套, `/run` 用另一套. 这违反 "一处设全场动" mental model.

本 doc:
- 文档化 2 套 budget 各自语义 + 触发点
- 记录 Phase 15.1 hotfix 决策 (为什么选 bridge 不选 unify)
- 列后续可能 unify / rename 方向 (defer follow-up)

## 2. 两套 budget 对照表

| 维度 | chat LLM call budget | runtime tick budget |
|---|---|---|
| **字段** | `chat_state.budget_per_turn_limit` + `_per_session_limit` + `_remaining` 计数器 | `CognitiveState.budget_remaining` (int) |
| **对象** | `ChatState` (chat REPL 状态) | `CognitiveState` (graph + reasoning state) |
| **粒度** | 每次 LLM 调用 -1 (chat tool_use loop 每个 tool call 1 次) | 每个 reasoning tick -1 (`runtime.run` 内 expand / reflect / decay each = 1 tick) |
| **`/budget` 命令读写它?** | ✓ (`_handle_budget` 改这两个 `_limit`) | ✗ (handler 完全不动) |
| **`/run` 受它限?** | 间接 — chat budget 耗尽 → `BudgetExhaustedEvent` 终止 chat turn (含 LLM call) | ✓ 直接 — `runtime.run` 每 tick `state.budget_remaining -= 1`, ≤0 → `"budget_exhausted"` |
| **持久化?** | ✓ `chat_state.json` sidecar | ✓ 包含在 graph state sidecar (跟 graph 一起序列化) |
| **初始值** | 默认 `0` (无限). 用户 `/budget` 改 | `default_budget=20` (config.py), `CognitiveState.bootstrap(question, budget=20)` 时 set |
| **0 表示啥** | `0` = 无限 (`_format_budget_value` 显示 "无限 (已用 K)") | `0` = 耗尽 (`stop.py` 触发 `budget_exhausted`) |
| **设计来源** | Phase 9 (chat infra) | Phase 5 (reasoning loop runtime) |

**关键不对称**: 两套 budget 的 `0` 语义完全相反 — chat budget 的 `0` 是"无限", runtime budget 的 `0` 是"耗尽".

## 3. 数据流图

```
用户 /budget → _handle_budget → chat_state.budget_per_turn_limit / _per_session_limit
                                              ↓
                                  影响: chat turn 内 LLM tool call 计数
                                  (跟 reasoning loop tick 解耦)

用户 /run → _handle_run → state.budget_remaining (读取)
                                  ↓
                          runtime.run(budget=...) (写入 state.budget_remaining = budget)
                                  ↓
                          每 tick: state.budget_remaining -= 1
                                  ↓
                          stop.py: budget_remaining <= 0 → "budget_exhausted"

(Phase 15.1 hotfix 加的 bridge):
                          if chat_state.budget_per_session_limit == 0:  # 用户设无限
                              budget = 10**9  # 等价无限给 runtime
                          else:
                              budget = max(state.budget_remaining, 1)
```

## 4. Phase 15.1 hotfix 决策

### 4.1 Bug 描述

`/run` 老代码:
```python
budget = max(chat.state.budget_remaining, 1)  # 只读 CognitiveState, 不读 chat_state
reason = await runtime_run(chat.state, chat.llm, budget=budget)
```

不管用户怎么设 `/budget`, `/run` 永远受 `state.budget_remaining` 限制. 而 `state.budget_remaining` 初始 `20`, 跑过几次后递减, 用尽后 `max(0, 1) = 1` 兜底也只够 1 tick.

### 4.2 候选方案

**方案 A (bridge, MVP)** — `/run` 读 `chat_state.budget_per_session_limit == 0` 时跳过 tick 限制:
```python
if chat.chat_state.budget_per_session_limit == 0:
    budget = 10**9  # 实际等价无限
else:
    budget = max(chat.state.budget_remaining, 1)
```
- pros: 1 行改, 0 schema 改, 0 persistence 改, 兼容老 session
- cons: `10**9` magic number; 仍是两套独立 budget, 仅 special-case `0` 时桥接

**方案 B (unify)** — 删 `CognitiveState.budget_remaining`, runtime 也读 `chat_state` budget:
- pros: 真"一处设全场动", mental model clean
- cons: 重大 schema 改 (CognitiveState 是 reasoning loop 核心 input), 影响 cli `explain run` 子命令路径 (它不走 chat_state), persistence migrate 老 session 麻烦

**方案 C (rename)** — `state.budget_remaining` rename 成 `state.tick_budget_remaining`, `chat_state.budget_*` 改成 `chat_state.llm_call_budget_*`, 文档化两者独立:
- pros: 不修 bug, 仅消除命名歧义, 用户清楚两套
- cons: 不改 UX (用户仍要分别设两个), 仅缓解 confusion

**方案 D (UX 增强)** — `/budget` 输出加注 "本设置仅影响 LLM 调用, 不影响 /run 推理步预算 (后者请用 /run-budget 设置)" + 加 `/run-budget` 命令:
- pros: 清晰分离 user-facing concepts
- cons: 命令面变大, 用户要记两个命令

### 4.3 选 A (Phase 15.1 hotfix)

**理由**:
- 1 行修复 + 2 test, 符合 Phase 15 polish 性质 (中文化 + UX bugfix), 不外溢 scope
- 用户最常见 mental model 是 `/budget` = 全部, A 直接 honor 之
- A 是 forward-compatible — 未来如选 B/C/D, A 这行 if 改起来易 (1 处)

**遗留 (defer 到 future polish)**:
- `chat.chat_state.budget_per_turn_limit == 0` 这条 fix 没考虑 (`/run` 跟 per_turn 概念不直接对应 — `/run` 是单条命令)
- `state.budget_remaining` 在 sidecar 持久化, 跨 `/run` 累积. 用户 mental model 是 "每次 /run 都 fresh"? 还是 "session 总量被耗尽"? Phase 5 设计是后者, 但 UX 没解释. (Phase 16+ 可考虑改第一种 + 在 `/run <n>` 显式 arg)
- `_render_budget_value` 在 chat 端有 "0 = 无限" UX, 但 cli `explain run` 子命令 budget 是 typer arg, 0 不视作无限 — 不一致

## 5. 未来可能的 refactor 方向 (按工作量排)

### 小 (1-2 小时)
- F-1: `/budget` 输出加注释 "本设置仅影响 LLM 调用预算, 不影响 /run 推理步预算"
- F-2: 命名 rename `state.budget_remaining` → `state.tick_budget_remaining` (engine 内部 + 不破 sidecar via migration field rename)

### 中 (1-2 天)
- F-3: `/run <n>` 加 explicit arg, 用户可 `/run 50` 强制传 50 tick budget
- F-4: 把 `chat_state.budget_per_turn_limit == 0` 也桥到 `/run` (跟 Phase 15.1 hotfix 同模式)

### 大 (Phase 16+ 重设计)
- F-5: 拆 `/budget` 成 `/llm-budget` + `/run-budget` 显式分两个命令
- F-6: 真 unify — `CognitiveState.budget_remaining` 改成 `chat_state` budget 的 view (delete or alias), `runtime.run` 读 `chat_state`, cli 子命令路径走同 budget pool

## 6. 测试覆盖

Phase 15.1 hotfix 加的 2 个 test (`tests/test_chat_slash_commands.py::TestSlashRun`):

- `test_run_with_unlimited_session_budget_passes_large_budget` — 设 `per_session_limit=0` + `state.budget_remaining=5` → `/run` 传给 runtime 的 budget `>= 10**6` (验证 bridge 生效, 不再受 5 限制)
- `test_run_with_finite_session_budget_uses_state_remaining` — 设 `per_session_limit=100` (有限) + `state.budget_remaining=5` → budget = 5 (验证老行为兼容)

既有 `test_happy_path_mock` (用 default chat_state, `per_session_limit=0` 默认 0=无限) 不需改 — 它 mock `runtime.run`, 不真正 decrement tick, 跟 budget 大小无关.

## 7. 类似坑提醒 (写给未来的我)

1. **同名跨对象字段** 是 UX confusion 高发点. 若必须共名 (历史包袱), 必须在 user-facing copy 显式 disambiguate. 写新 chat-layer concept 时检查现有 engine-layer 是否已用同名.
2. **`0` 的语义反差** 也是高风险 — chat 端 `0 = 无限`, runtime 端 `0 = 耗尽`. 同名字段不同语义比同义字段不同名字更坑.
3. **bridge fix 应该有 follow-up** — Phase 15.1 选 A 是工程权衡, 但 long-term 应 unify or rename. 这 doc 就是给后续 phase 的 prompt.
