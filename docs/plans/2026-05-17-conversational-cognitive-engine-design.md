# Phase 9 — Conversational Cognitive Engine (Design)

> 顶层文档参考: [最终哲学以及技术实现相关设计.md](../../最终哲学以及技术实现相关设计.md) §2.4 / §5.3 / §10 / §13
> Claude Code 架构参考: `/Users/jinziguan/Desktop/Claude-code-architecture-instruction/`
> 上一 phase: [Phase 8 Design](2026-05-15-cognitive-engine-phase-8-design.md)
> 上一 phase acceptance: [Phase 8 Acceptance](2026-05-15-cognitive-engine-phase-8-acceptance.md)

**日期**: 2026-05-17
**分支**: `dev` (Phase 8 final HEAD `f02cb82` 之后)

---

## 0. TL;DR

**Phase 9 主题**: 把 cognitive engine 从"CLI 命令式"升级为"对话式 agent". 用户 `explain new` 后进入持续对话 loop, LLM 自主调度 expand / compress / check / predict / counterfactual / add_observation / read_node 7 个 tool, 边推理边给 narrative. 配套 3-tier context compaction (Claude Code 风格) + per-turn/session budget + smart HITL gate + 全新项目-based 持久化目录结构 (`~/.explain/projects/<proj>/sessions/<sid>/`, 为 Phase 10+ cross-session knowledge pool 铺路).

**核心哲学落地**: §2.4 question → graph formation → mechanism stabilization → abstraction compression → insight emergence — Phase 9 让这整条流水线在 LLM agent 内自然发生, 而不是 user 手动调 8 个 CLI. §10.1 Meta-cognition (reflect + lifecycle 作为 post-turn hook 自动跑). §5.3 Persistent World Model 通过新目录结构铺路.

**总: ~12-15 task, 7 Wave 线性, +90 tests (482 → 572), ~7 周.**

---

## 1. 背景与动机

### 1.1 Phase 0-8 局限

Phase 0-8 是 "CLI 命令式" — 用户必须显式调 8 个命令 (`new` / `compress` / `run` / `check` / `predict` / `counterfactual` / `rescore` / `show`). 每个命令独立进程, 没有 conversation context, 不能自然提"对了我刚想到一个观察" 或 "这个 driver 是不是太弱了你帮我看看".

### 1.2 顶层哲学 §2.4 的缺口

```text
question
→ graph formation
→ mechanism stabilization
→ abstraction compression
→ insight emergence
```

Phase 0-8 实现了每个箭头的 engine, 但没有把它们组装成"自然连续的认知过程". 用户体验是"命令式输入数据", 不是"协作式发现 insight".

### 1.3 Claude Code 的工程启示

调研 `/Users/jinziguan/Desktop/Claude-code-architecture-instruction/` 提炼 5 关键模式:
1. **Two-layer loop**: outer (lifecycle / persistence) wraps inner (LLM ↔ tools while-loop)
2. **Tool = capability wrap**: 把 engine 包成有 input_schema / description / call 的 Tool 对象, LLM 用 native `tool_use` API 调用
3. **PostSamplingHook**: 后台异步跑 self-check / summarize, 不阻塞用户
4. **3-tier compaction**: microCompact (drop stale tool results) + sessionMemory (background summary file) + emergency (sync LLM)
5. **Project-based directory**: `~/.claude/projects/<cwd-hash>/` 隔离 + 容纳 cross-project knowledge

### 1.4 Phase 9 是 §5.3 Persistent World Model 的入口

User 选 γ-1 (全迁移) 的关键动机: 后期 cross-session knowledge pool — 一个 session 的 graph 可以提炼成 "全量知识" 供其他 session 调用. 这需要 project-level 目录结构 (`~/.explain/projects/<proj>/{sessions/, knowledge/}`), 而 Phase 0-8 的 flat `sessions/*.json` 不容纳这个 future.

---

## 2. Scope

### 2.1 Phase 9 内

**Wave A — Persistence Migration (1 task)**
- `storage_v2.py` 新建 project-based filesystem (`~/.explain/projects/<proj>/sessions/<sid>/`)
- `migration.py` 一次性迁移 + `explain migrate` 命令
- 老 SessionStore 兼容: delegate to storage_v2

**Wave B — Tool Layer (2 tasks)**
- `chat/tools.py` Tool dataclass + 7 tool wrappers:
  - 5 engine tools: `expand` / `compress` / `check` / `predict` / `counterfactual` (包 existing engines)
  - 1 mutation tool: `add_observation` (LLM 自动检出 user 意图加 L0)
  - 1 read tool: `read_node` (lazy-load 完整 description, lean context 必需)
- 每 tool 的 `description()` 动态 (含 graph 状态 hint)

**Wave C — Chat Loop (2 tasks)**
- `chat/session.py` ChatSession outer loop (lifecycle / persistence / I/O)
- `chat/loop.py` query_loop async generator (LLM ↔ tools while-loop)
- `chat/system_prompt.py` 动态 system prompt 拼装 (per Claude Code fetchSystemPromptParts)

**Wave D — Hooks + Budget + HITL (2 tasks)**
- `chat/hooks.py`:
  - `reflect_post_turn` (跑 Phase 7 reflection, 但不直接 trigger action; 把 reflect 决策附在 transcript 给 LLM 看)
  - `lifecycle_post_turn` (跑 Phase 8 update_lifecycle, mark stale / decayed)
  - `session_memory_writer` (background async, 每 5 turn 触发)
- `chat/budget.py` per-turn (10) + per-session (50) counter; `--tool-budget-per-turn` / `--tool-budget-per-session` flag
- `chat/hitl.py` smart gate: `add_observation(source="llm_inferred")` 触发 mid-turn confirm prompt

**Wave E — Context Compaction (2 tasks)**
- `chat/compaction.py` 3-tier:
  - microCompact: drop tool results > N turn old (替换为 `[stale tool result dropped]`)
  - sessionMemory: 后台 hook 写 `memory.md`, compaction 时 splice in 替换旧 prefix
  - emergency: 真 token 超限 → sync LLM call 总结全 transcript

**Wave F — Slash Commands + CLI (1 task)**
- `chat/slash_commands.py` 默认 6 个: `/quit` `/help` `/show` `/budget` `/compact` `/save`
- `cli.py` 新增 `explain chat <sid>` Typer command
- Streaming UX (Rich Live render)

**Wave G — Acceptance + 文档 (2 tasks)**
- 真实 chat run 验证 (新建 session, 跑 ~10 turn, 验证 7 tool 都被调到)
- 老 482 测试全部 migration (conftest.py 提供 ~/.explain 临时路径 fixture)
- acceptance doc + README 更新 + Phase 10 motivations
- **A+B 折叠修复**:
  - A: `rollout_from_roots` 当所有 L2 decayed → fallback to L1 active roots (3 行 fix + 1 test, 在 Wave E 阶段顺便)
  - B: `last_input_alignment_report` 持久化到 chat_state.json (Wave A 自然包含, 不算 follow-up)

### 2.2 推到 Phase 10+

- ❌ **cross-session knowledge pool 实际填充** (Phase 9 留目录, Phase 10 写提炼算法)
- ❌ **Theory Formation Engine** (§13) — Phase 11
- ❌ **Multi-Perspective Runtime** (§10 完整, 包括 perspective_shift action) — Phase 11+
- ❌ **Variable lifecycle 完整 8 阶段** (cross-session birth/death) — Phase 10
- ❌ **Embedding-based semantic dedup / vagueness scoring** — Phase 11+
- ❌ **HITL gate per destructive action 扩展** (现仅 add_observation; predict/counterfactual 等不加)
- ❌ **Web search / external grounding**
- ❌ **Multi-LLM cross-validate** (Wave 3 之外的 mismatch detection)

### 2.3 不动的

| 模块 | 不动原因 |
|---|---|
| `engines/_propagation.py` 主算法 | Phase 8 已稳定; Wave G 仅修 rollout edge case (3 行) |
| `engines/simulation.py` | 仅扩 `aggregate_acceptance` 的 caller (作为 check tool) |
| `engines/bootstrap.py` / `compression.py` / `evaluation.py` / `expansion.py` 主体 | 仅包成 tool, 不改逻辑 |
| `engines/reflection.py` / `lifecycle.py` | 仅作为 post-turn hook 调用, 不改 |
| `engines/prediction.py` / `counterfactual.py` / `intervention_parser.py` | 仅包成 tool |
| `engines/input_validation.py` / `errors.py` | 不动 (Wave A 持久化 alignment 通过 storage_v2) |
| `engines/rescore.py` | 不动 (acceptance internal, Phase 9 不 expose) |
| `schema/*` | nodes / edges / graph 不动. state.py 可能加 chat-related field (TBD) |
| `runtime/runtime.py` / `scheduler.py` / `stop.py` | 不动 (`explain run` 仍走老 reasoning loop; chat 是 alternative path) |

---

## 3. Architecture

### 3.1 Module 边界

```
explain_engine/
├── chat/                   ── NEW (Phase 9 核心, 8 文件)
│   ├── session.py          ── Outer loop: ChatSession (lifecycle / I/O / persistence)
│   ├── loop.py             ── Inner loop: query_loop async generator
│   ├── tools.py            ── Tool dataclass + 7 tool definitions
│   ├── hooks.py            ── reflect / lifecycle / sessionMemory writer
│   ├── compaction.py       ── 3-tier microCompact / sessionMemory / emergency
│   ├── slash_commands.py   ── /quit /help /show /budget /compact /save
│   ├── system_prompt.py    ── 动态 system prompt 拼装
│   ├── budget.py           ── per-turn + per-session counter
│   └── hitl.py             ── mid-turn confirmation prompt
│
├── persistence/
│   ├── session.py          ── 改: delegate 到 storage_v2 (保持老接口)
│   ├── storage_v2.py       ── NEW: project-based filesystem
│   └── migration.py        ── NEW: 一次性 migrate 老 sessions/
│
├── engines/                ── 不动 (Wave A rollout edge case 3 行除外)
└── cli.py                  ── 改: 加 chat command + migrate command
```

### 3.2 7-layer 架构 (从外到内)

```
┌─ Entry ─────────────────────────────────────────────────────────┐
│ explain chat <sid>  (Typer command, 加 --tool-budget flag)       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─ ChatSession (outer) ───────────────────────────────────────────┐
│ - 加载 sidecar (transcript.jsonl, memory.md, chat_state.json)    │
│ - 解析用户 input: slash command? → local intercept                │
│ - 否则: 调 query_loop, stream events 到 CLI                      │
│ - 自动 persist (debounced)                                       │
│ - 退出时 flush + 写 memory.md (final sync compaction if needed)  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─ query_loop (inner async generator) ────────────────────────────┐
│ while True:                                                      │
│     messages = compaction.prepare(transcript, memory)            │
│     sys_prompt = system_prompt.assemble(state, tools, budget)    │
│     response = await llm.chat(messages, sys_prompt, tools=...)   │
│     yield AssistantText(response.text) [if any]                  │
│     if not response.tool_uses: yield TurnComplete; return        │
│     for tool_use in response.tool_uses:                          │
│         result = await dispatch_tool(tool_use, state, hitl)      │
│         yield ToolResult(result)                                 │
│         budget.consume(); if exceeded: yield BudgetBreak; return │
│     # loop continues with new tool_results appended              │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─ Tool layer (7 tools) ──────────────────────────────────────────┐
│ Tool dataclass:                                                  │
│   name, input_schema (Pydantic), description (callable),         │
│   call (async), is_destructive, is_readonly                      │
│                                                                  │
│ 7 tools:                                                         │
│   expand          → engines.expansion (auto-pick downward/frontier)│
│   compress        → engines.compression                          │
│   check           → engines.simulation.aggregate_acceptance      │
│   predict         → engines.prediction                           │
│   counterfactual  → engines.counterfactual                       │
│   add_observation → graph.add_node (L0); HITL gate if LLM-inferred│
│   read_node       → state.graph.nodes[id] full description       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─ Post-turn hooks (fire after each TurnComplete) ────────────────┐
│ - reflect_post_turn: 跑 reflection.reflect, 把决策附 transcript   │
│ - lifecycle_post_turn: 跑 lifecycle.update_lifecycle, mark状态   │
│ - session_memory_writer (only if turn_count % 5 == 0):           │
│     spawn background subagent → 总结 transcript prefix →         │
│     写 memory.md (next compaction picks up)                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─ Context compaction (3-tier, on every loop iter) ───────────────┐
│ tier 1 microCompact:   tool result age > N tick → replace stub  │
│ tier 2 sessionMemory:  if memory.md fresher than transcript     │
│                        prefix → splice in, drop old prefix      │
│ tier 3 emergency:      if still > token limit → sync LLM        │
│                        summarize all → 替换 context              │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─ Persistence (storage_v2) ──────────────────────────────────────┐
│ ~/.explain/projects/<project_id>/sessions/<sid>/                 │
│   metadata.json                                                  │
│   graph.json                                                     │
│   transcript.jsonl   (append-only, 一行 JSON 一个 message)        │
│   memory.md          (background hook 写)                        │
│   chat_state.json    (budget remaining, last compact, alignment) │
│                                                                  │
│ project_id = sha256(cwd absolute path)[:8] OR EXPLAIN_PROJECT_ID │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 数据流 (1 个 user turn 完整 trace)

```python
# 用户输入: "为什么富人更易获取财富? 帮我建一个解释 graph"

# 1. ChatSession receives input
chat.transcript.append(UserMessage("为什么富人更易获取财富? 帮我建一个解释 graph"))

# 2. Not slash command → query_loop iteration 1
sys_prompt = """
You are a cognitive analysis agent. Your job: build an explanation graph
for the user's question via the 7 tools.
Available tools: expand, compress, check, predict, counterfactual,
add_observation, read_node.
Current graph: 0 L0 + 0 L1 + 0 L2 = empty
Budget remaining: 50 (session) / 10 (per-turn)
..."""

# 3. LLM call returns tool_use
response = {
  "text": "我来开始. 先听你的观察, 你给我 5-10 条关于'富人易获财富'的具体现象.",
  "stop_reason": "end_turn"
}
yield AssistantText("我来开始...")
yield TurnComplete

# 4. User: "OK 第一: 资本收益税率低于工资税率. 第二: 富人社交圈密集..."
# (continues for several turns; LLM uses add_observation tool when user gives obs)

# 5. After 5 observations collected, LLM auto-calls compress
yield ToolUse("compress", {})  # LLM decides
# tool dispatched → engines.compression.compress() runs (multiple internal LLM calls)
# 返回: "compressed, 4 L1 candidates: c_001, c_002, c_003, c_004"
yield ToolResult("4 L1 candidates added: c_001..c_004")
budget.consume(1)  # per-turn 9 remaining

# 6. LLM continues: auto-calls expand for each L1
yield ToolUse("expand", {"l1_id": "c_001", "direction": "downward"})
# 内部调 expand_downward
yield ToolResult("3 L0 added under c_001")
budget.consume(1)
# ... repeat for c_002 c_003 c_004 (4 more tool calls)
budget.consume(4)  # per-turn 4 remaining

# 7. LLM: "我觉得 graph 形态 OK 了, 跑 check 看 multi-signal"
yield ToolUse("check", {})
yield ToolResult("avg_consistency=0.65, weak_chain_l1s=['c_002'], rollout=0.8")
budget.consume(1)  # per-turn 3 remaining

# 8. LLM: "c_002 弱, 我 expand_downward 加 L0"
yield ToolUse("expand", {"l1_id": "c_002", "direction": "downward"})
yield ToolResult("2 L0 added")
budget.consume(1)  # per-turn 2 remaining

# 9. LLM: TurnComplete with narrative
yield AssistantText("已完成首轮 graph 构建. 4 个 L1 (税制/网络/杠杆/人力资本),
12 个 L0 观察, avg_consistency 0.65. c_002 改善后 rollout 提到 0.85.
你想我 predict 加新干预 / counterfactual 探什么?")
yield TurnComplete

# 10. Post-turn hooks (fire-and-forget)
asyncio.create_task(reflect_post_turn(chat.state))   # 跑 reflection 加 transcript hint
asyncio.create_task(lifecycle_post_turn(chat.state)) # update lifecycle marks
# session_memory_writer 不 fire (turn_count=1, % 5 != 0)

# 11. ChatSession persist sidecar files
chat.persist_async()
```

### 3.4 依赖图 (单向无环)

```
cli.py
   ├─→ chat/session.py
   │      ├─→ chat/loop.py
   │      │      ├─→ chat/system_prompt.py
   │      │      ├─→ chat/tools.py
   │      │      │      ├─→ engines/* (5 个老 engine + add_observation/read_node 新)
   │      │      │      └─→ chat/hitl.py
   │      │      ├─→ chat/budget.py
   │      │      └─→ chat/compaction.py
   │      ├─→ chat/hooks.py
   │      │      ├─→ engines/reflection.py
   │      │      ├─→ engines/lifecycle.py
   │      │      └─→ chat/compaction.py (sessionMemory writer)
   │      ├─→ chat/slash_commands.py
   │      └─→ persistence/storage_v2.py
   │
   └─→ persistence/migration.py (explain migrate command)
```

无新循环依赖. `chat/*` 是叶子, 依赖 `engines/*` + `persistence/*`. 老 `runtime/*` 不变.

---

## 4. Schema 改动

### 4.1 storage_v2 项目结构

```
~/.explain/
└── projects/<project_id>/
    ├── README.md                  # project 元: cwd path, 创建时间
    ├── sessions/<sid>/
    │   ├── metadata.json          # (= 老 Session.meta 字段)
    │   ├── graph.json             # (= 老 Session.state.graph + 老 state 其他字段)
    │   ├── transcript.jsonl       # 新: append-only
    │   ├── memory.md              # 新: background-written summary
    │   └── chat_state.json        # 新: budget / last compact / input_alignment
    └── knowledge/                  # Phase 10+ 占位
        └── .gitkeep
```

### 4.2 transcript.jsonl 格式

每行一个 JSON object (Anthropic API messages 格式 + Phase 9 扩展):
```jsonl
{"role": "user", "content": "为什么富人更易获取财富?", "timestamp": "...", "turn": 0}
{"role": "assistant", "content": [{"type": "text", "text": "..."}, {"type": "tool_use", "id": "tu_001", "name": "compress", "input": {}}], "turn": 0}
{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_001", "content": "4 L1 candidates: c_001..c_004"}], "turn": 0}
{"role": "assistant", "content": [...], "turn": 0}
...
```

### 4.3 memory.md 格式

Markdown, 人类可读, LLM friendly (无需 deserialize):

```markdown
# Session Memory: s_xxx (last updated turn 15)

## Question
为什么富人更易获取财富

## Graph Snapshot
- 12 L0 observations (税制 / 网络 / 杠杆 / 教育)
- 4 L1 abstracts: c_001 结构性税收 / c_002 网络优势 / c_003 杠杆能力 / c_004 人力资本
- 10 L2 drivers (其中 5 active, 5 decayed)

## Recent Insights
- Turn 8: user added observation about generational wealth transfer
- Turn 12: counterfactual on c_002 → essentialness 0.32 → kept as core driver
- Turn 14: reflect 决定 stop, graph 收敛

## Pending Threads (LLM context)
- c_003 wak_chain_l1s 上一次 check 后 ([c_003]), 但 user 没回应是否要 expand
- predict 关于"如果税法改革" 还未做
```

### 4.4 chat_state.json 字段

```python
@dataclass
class ChatState:
    budget_per_turn_remaining: int
    budget_per_session_remaining: int
    last_compact_at_turn: int
    last_input_alignment_report: InputAlignmentReport | None  # Phase 8 B 修复: 持久化
    pending_hitl: dict | None  # 中断/恢复 时的 HITL 状态
    project_id: str
    created_at: str
    last_active_at: str
```

### 4.5 向后兼容 (Migration)

`persistence/migration.py`:
- 检测旧 `sessions/s_*.json` 存在
- 计算 project_id = sha256(cwd)[:8]
- 创建 `~/.explain/projects/<project_id>/sessions/<sid>/`
- 拆 `sessions/s_xxx.json` → `metadata.json` + `graph.json` (字段映射)
- transcript / memory / chat_state 新建空 (老 session 没 chat 历史)
- 老 `sessions/s_xxx.json` 移到 `sessions/.legacy/` (不删除, 但移开避免双 source)

CLI: `explain migrate` 一次性 + `explain migrate --dry-run` 预览.

老 SessionStore 接口 (`SessionStore.load(sid)`, `SessionStore.save(session)`, `SessionStore.list()`) 改为 delegate to storage_v2, 接口不变. 482 老 test 通过 fixture `tmp_path` + monkeypatch 改 `EXPLAIN_HOME=tmp_path/.explain` 自动 isolation.

---

## 5. 7 Tools 详细 spec

### 5.1 Tool dataclass

```python
@dataclass
class Tool:
    name: str
    input_schema: type[BaseModel]   # Pydantic
    description: Callable[[ChatContext], str]   # dynamic, 含 graph 状态
    call: Callable[[BaseModel, ChatContext], Awaitable[str]]
    is_readonly: bool = False
    is_destructive: bool = False
    requires_hitl: bool = False     # True → 走 hitl gate
```

### 5.2 7 tool 定义

| Tool | Input schema | 包的 engine | description hint | readonly | hitl |
|------|-------------|------------|-----------------|----------|------|
| `expand` | `{l1_id: str?, direction: "downward"\|"upward"\|"auto"}` | `expansion.expand_downward` / `expand_one_frontier` (auto 选) | "扩 L1 (downward 加 L0, upward 加 driver, auto LLM 决定)" | no | no |
| `compress` | `{}` | `compression.compress` + `evaluation.score` | "把 L0 observations 抽象成 L1 candidates" | no | no |
| `check` | `{target_id: str?}` | `simulation.aggregate_acceptance` (无 target) / `check_consistency` (有 target) | "跑 multi-signal acceptance check, 看 graph 健康度" | yes | no |
| `predict` | `{intervention_text: str}` | `prediction.predict` (内部调 intervention_parser) | "forward predict: 如果加入 X 会怎样" | no | no |
| `counterfactual` | `{intervention_text: str}` | `counterfactual.substitute` | "counterfactual: 如果移除/替换 Y" | no | no |
| `add_observation` | `{name: str, description: str, source: "user_explicit"\|"llm_inferred"}` | `state.graph.add_node` (level=0, epistemic="observation") | "user 提到新观察时加 L0; source=llm_inferred 触发 HITL confirm" | no | yes (if source=llm_inferred) |
| `read_node` | `{node_id: str}` | dict access | "读节点完整 description (lean context lazy load)" | yes | no |

### 5.3 reflect / lifecycle 不暴露原因

- Reflect 是 meta-cognition, LLM "决定要不要反思" 容易陷入 (类似 GPT 反思自己反思自己). 做成 post-turn hook 跑, 决策结果作为 hint 注入下一 turn LLM context.
- Lifecycle (update_lifecycle / decay) 是 background 维护, 不该消耗 LLM tool budget.

---

## 6. Post-turn hooks 详细

### 6.1 reflect_post_turn

```python
async def reflect_post_turn(state: CognitiveState, chat: ChatSession):
    """Run after each TurnComplete. NOT a tool; LLM doesn't decide."""
    # Refresh acceptance cache
    state.last_acceptance_report = aggregate_acceptance(state)
    # Run lifecycle update (advance stale → decayed)
    lifecycle.update_lifecycle(state, current_tick=chat.turn_count)
    # Run reflect decision
    action, target = reflection.reflect(state)
    # DO NOT execute action — append as hint to next turn's system prompt
    chat.next_turn_hint = f"reflect 建议: {action} (target={target}). 你可以 follow 或忽略."
```

### 6.2 session_memory_writer (background)

```python
async def session_memory_writer(chat: ChatSession):
    """每 5 turn 后台跑. Background subagent style (Claude Code PostSamplingHook 同款)."""
    if chat.turn_count % 5 != 0: return
    # Take snapshot of transcript prefix (oldest N turns to summarize)
    prefix_msgs = chat.transcript[:chat.last_compacted_idx + N]
    # LLM call to summarize
    summary = await llm.chat(
        sys_prompt="把以下对话总结成 markdown 给未来 LLM 看的 session_memory",
        messages=prefix_msgs,
    )
    # Write atomically (rename for safety)
    write_atomic(chat.paths.memory_md, summary)
    chat.last_memory_write_at_turn = chat.turn_count
```

### 6.3 Hook 调度 (fire-and-forget vs sync)

| Hook | 时机 | 同步? |
|------|------|------|
| reflect_post_turn | after TurnComplete | sync (~10ms, no LLM) |
| lifecycle_post_turn | after TurnComplete | sync (~10ms, no LLM) |
| session_memory_writer | after TurnComplete, if turn_count % 5 == 0 | async fire-and-forget |
| input_validation | first turn only, in query_loop | sync (1 LLM call) |
| add_observation HITL | mid-tool-dispatch | sync (blocks user input) |

---

## 7. 3-tier Context Compaction

### 7.1 tier 1 — microCompact (per loop iter, cheap)

```python
def micro_compact(transcript: list, current_turn: int) -> list:
    """Drop stale tool results > N turn old, replace with stub."""
    STALE_TURN_AGE = 5
    result = []
    for msg in transcript:
        if msg.is_tool_result() and (current_turn - msg.turn) > STALE_TURN_AGE:
            result.append(msg.replace(content="[stale tool result dropped]"))
        else:
            result.append(msg)
    return result
```

### 7.2 tier 2 — sessionMemory splice (per loop iter, free if memory.md fresh)

```python
def session_memory_splice(transcript: list, memory_md: str, last_memory_turn: int) -> list:
    """If memory.md 比 transcript prefix 新, splice in 替换前 N turn."""
    if not memory_md or last_memory_turn < 5:
        return transcript
    # Replace prefix [0:last_memory_turn] with single "memory" system-like message
    memory_msg = SystemMessage(f"[Session memory through turn {last_memory_turn}]\n\n{memory_md}")
    return [memory_msg] + transcript[last_memory_turn:]
```

### 7.3 tier 3 — emergency sync compact (rare, last resort)

```python
async def emergency_compact(transcript: list, llm) -> list:
    """Token limit hit. Synchronous LLM call to summarize ALL, replace context."""
    summary = await llm.chat(
        sys_prompt="紧急压缩: 总结这段长对话, 给下一轮 LLM 看",
        messages=transcript,
    )
    return [SystemMessage(f"[Emergency compaction - prior context]\n\n{summary}")]
```

### 7.4 Compaction pipeline (per loop iter)

```python
def prepare_messages(transcript, memory_md, last_memory_turn, current_turn, llm):
    msgs = micro_compact(transcript, current_turn)
    msgs = session_memory_splice(msgs, memory_md, last_memory_turn)
    if token_count(msgs) > LIMIT * 0.85:
        msgs = await emergency_compact(msgs, llm)  # sync, expensive
    return msgs
```

---

## 8. Budget + HITL gate

### 8.1 BudgetCounter

```python
@dataclass
class BudgetCounter:
    per_turn_limit: int = 10
    per_session_limit: int = 50
    per_turn_used: int = 0     # reset每 user turn
    per_session_used: int = 0  # 持久化, never resets except /budget-reset (no such command)

    def consume(self, n=1):
        self.per_turn_used += n
        self.per_session_used += n

    def turn_exhausted(self): return self.per_turn_used >= self.per_turn_limit
    def session_exhausted(self): return self.per_session_used >= self.per_session_limit
```

`explain chat <sid> --tool-budget-per-turn 20 --tool-budget-per-session 100` 启动 override.

### 8.2 HITL gate (add_observation only)

```python
async def hitl_gate(tool_use, ctx: ChatContext) -> bool:
    """Returns True if approved, False if denied."""
    tool = tool_use.name
    input = tool_use.input

    if tool != "add_observation":
        return True  # 其他 tool 不 gate

    if input.get("source") == "user_explicit":
        return True  # user 明确说"加这个观察" → trust

    # LLM-inferred → ask user
    ctx.cli.print(f"[yellow]LLM 想加 observation: '{input['name']}'[/yellow]")
    ctx.cli.print(f"[dim]description: {input['description']}[/dim]")
    answer = ctx.cli.input("加吗? (y/n): ").strip().lower()
    return answer == "y"
```

---

## 9. CLI integration

### 9.1 新 commands

```bash
# 进 chat
explain chat <sid>
explain chat <sid> --tool-budget-per-turn 20 --tool-budget-per-session 100
explain chat <sid> --no-budget  # power user 开 α 模式 (无限制)

# Migration (一次性)
explain migrate            # 把老 sessions/*.json 迁到 ~/.explain/
explain migrate --dry-run  # 预览不动文件
```

### 9.2 Chat REPL UX (Rich Live render)

```
> explain chat s_xxx
[dim]Loading session s_xxx... 24 nodes, 12 turns history[/dim]
[bold cyan]Chat ready. /help for commands. Ctrl+C to interrupt.[/bold cyan]

> 你帮我看看这个 graph c_002 弱在哪
[dim]Tool: check[/dim]
[dim]Tool: read_node(c_002)[/dim]
我看了 c_002 (网络优势). consistency=0.30, 弱在它只 manifest 到 1 个 L0
(富人社交圈密集). rollout 时它无法覆盖到其他 4 个网络相关 observations.

建议: expand_downward c_002, 让它说更多 manifestations.
要我做吗?

> ok
[dim]Tool: expand (l1_id=c_002, direction=downward)[/dim]
加了 2 个 L0: 富人通过校友网络获信息 / 富人通过 club 会员制度合作.
重 check 一下...
[dim]Tool: check[/dim]
c_002 consistency 从 0.30 升到 0.62. weak_chain_l1s 现 [].

> /show
[dim]== Multi-signal acceptance (Phase 8 Wave 2) ==[/dim]
avg_consistency       0.658
weak_chain_l1s        []
...

> /budget
per-turn remaining: 7 / 10
per-session remaining: 38 / 50

> /quit
[dim]Saving... done. transcript: 28 messages, memory: 1240 chars.[/dim]
```

---

## 10. 任务清单 (草稿)

| # | Wave | 任务 | 工作量 | 依赖 |
|---|------|------|--------|------|
| 1 | A | storage_v2 + migration + 482 test fixture 迁 | 1 周 | 无 |
| 2 | B | Tool dataclass + 5 engine tool wrappers | 0.5 周 | #1 |
| 3 | B | add_observation + read_node tools | 0.5 周 | #2 |
| 4 | C | ChatSession outer loop + persistence integration | 1 周 | #1 |
| 5 | C | query_loop inner + system_prompt 动态拼装 | 1 周 | #2, #4 |
| 6 | D | budget counter + smart HITL gate | 0.5 周 | #5 |
| 7 | D | reflect / lifecycle / session_memory_writer hooks | 0.5 周 | #5 |
| 8 | E | 3-tier compaction (micro + sessionMemory + emergency) | 1 周 | #5, #7 |
| 9 | E | A+B fix (rollout edge case + alignment 持久化通过 chat_state) | 0.5 周 | #1, #8 |
| 10 | F | 6 slash commands + Rich Live streaming UX | 0.5 周 | #4 |
| 11 | F | CLI explain chat + explain migrate command | 0.5 周 | #1, #4 |
| 12 | G | acceptance (real chat run + 老 test 全部 migrate verify) | 1 周 | 全部 |
| 13 | G | acceptance doc + README + Phase 10 motivations | 0.5 周 | #12 |

**总: 13 task / ~9 工作单位 / ~7 实际周 (含 review fix iterations).**

依赖图:
```
#1 → #2 → #3 ──┐
     ↓         ├─→ #5 → #6 ─→ #8 ─→ #12 → #13
     #4 ───────┘    ↓     ↓     ↓
                    #7 ───┘     #9
                    ↓
                    #10, #11
```

Parallel batches:
- Batch 1: #1 (foundation)
- Batch 2: #2 + #4 (并行, 依赖 #1)
- Batch 3: #3 + #5 (并行, 依赖 #2)
- Batch 4: #6 + #7 + #10 + #11 (并行, 依赖 #5)
- Batch 5: #8 + #9 (并行)
- Batch 6: #12 → #13 (顺序)

---

## 11. 风险 & 缓解

| # | 风险 | 概率 | 影响 | 缓解 |
|---|------|------|------|------|
| 1 | 482 test migration 失败 / 漏 | 中 | 高 | conftest.py `tmp_path` fixture + monkeypatch `EXPLAIN_HOME`; CI 同时跑老路径 + 新路径 1 个版本; 老 SessionStore 接口完全兼容 |
| 2 | LLM 误调 add_observation 大量 hallu 观察 | 中 | 中 | source="user_explicit" vs "llm_inferred" 区分 + HITL gate; prompt 强约束 "只在 user 明确说加观察时调" |
| 3 | session_memory_writer 后台跑跟 main loop race | 低 | 中 | atomic file write (临时文件 + rename); writer 是 read-only on state |
| 4 | 3-tier compaction 复杂, 调参困难 | 中 | 中 | STALE_TURN_AGE / token LIMIT 阈值都 module 常量; Wave G acceptance 调; emergency tier 是 last resort 不常 fire |
| 5 | project_id = cwd hash 不稳定 (用户 mv 目录) | 低 | 中 | env var override `EXPLAIN_PROJECT_ID=myproj`; `explain migrate-project --from-cwd <old>` 命令 |
| 6 | budget 触发 mid-LLM-response (tool 已部分调) | 中 | 低 | budget check 在 dispatch_tool 之前, 不会半途打断; budget 超 → 完成当前 tool, 不开新 tool |
| 7 | HITL gate 阻塞 streaming UX | 中 | 中 | mid-turn confirm 用 inline prompt (Rich `console.input`); 用户拒绝 → tool_result = "user denied", LLM 看到决定 next move |
| 8 | 老用户已有脚本依赖 `sessions/` | 高 | 低 | `sessions/.legacy/` 保留 symlink 兼容 1 个 phase; 文档明确 deprecation |
| 9 | cross-session knowledge pool (Phase 10) 设计未定, knowledge/ 占位可能不对 | 低 | 低 | knowledge/ 是空目录 + .gitkeep, Phase 10 时随便用; 影响 0 |
| 10 | Chat 内存泄漏 (transcript 不释放) | 低 | 中 | per turn debounced sidecar flush; ChatSession 退出时 explicit close |

---

## 12. 与 Phase 8 / Phase 10 关系

### 12.1 向后看 (Phase 0-8)

- **复用 100% 现有 engines** — 没动 expansion / compression / simulation / reflection / lifecycle / prediction / counterfactual / input_validation / intervention_parser 任何主体逻辑
- **runtime/ 完全不动** — `explain run` 老 reasoning loop 仍可用 (Phase 0-8 acceptance 仍可重跑)
- **schema/ 不动** — nodes / edges / graph / state 不加新字段
- **持久化 migration 是关键 breaking change** — Wave A 一次性迁移 + 老 SessionStore 接口兼容
- **修 2 个 Phase 8 follow-up** (A rollout edge case + B input_alignment 持久化) — 在 Wave E + Wave A 顺便

### 12.2 向前看 (Phase 10+)

| Phase 10+ 目标 | Phase 9 铺垫 |
|---|---|
| Cross-session knowledge pool (§5.3 Persistent World Model) | `~/.explain/projects/<proj>/knowledge/` 目录已留 |
| Variable lifecycle 完整 8 阶段 (cross-session birth/death) | Phase 8 lifecycle 字段已持久化; Phase 10 加 cross-session 复活路径 |
| Theory Formation Engine (§13) | Phase 9 weak_chain_l1s + reflect 决策 + chat-driven exploration → Phase 10 提炼成 theory candidate |
| Multi-Perspective Runtime (§10) | Phase 9 add_observation 可扩展为 add_perspective; system_prompt 模板预留 perspective field |
| Theory candidate cross-validate (multi-LLM) | Phase 9 LLM client 已抽 (config), Phase 10 加 second LLM instance config |

---

## 13. Open questions / 推 Phase 10

1. **Project_id 默认策略**: cwd hash 在 mv 目录后会 orphan. Phase 9 用 cwd, 给 env var override. Phase 10 可考虑 git remote URL hash 或 user 命名.

2. **Chat 中节点 ID 命名冲突**: 现 `p_001..p_999` `c_001..c_009` `d_001..d_009` 编号. 长 chat 后可能用尽. Phase 9 不动 (现 99 个节点足够 MVP). Phase 10 考虑 UUID.

3. **Multi-session chat 同时跑**: 一个 user 开 2 个 chat 同 session → 写 transcript 冲突. Phase 9 用 file lock 单 chat per session. Phase 10 可加 multi-chat conflict resolution.

4. **chat-driven graph mutation 持久化语义**: chat 内通过 add_observation / expand 加节点, 退出后 sidecar 保存. 但如果 user 后悔某 chat 的所有改动? Phase 9 不支持 chat-level undo. Phase 10 加 transcript replay (从某 turn 重做).

5. **Knowledge pool 提炼算法 (Phase 10 核心)**:
   - input: 一个 session 的 graph + transcript + memory.md
   - output: knowledge/{variables.json, theories.json, knowledge.md} 增量更新
   - 算法 TBD: 频率统计? LLM-driven? embedding clustering?

---

## 附录 A: 哲学锚点对照表

| Wave | 哲学章节 | 原话 | Phase 9 实现 |
|------|----------|------|------------|
| C 全部 | §2.4 question → graph formation → insight emergence | (链条) | chat loop 让整条链条在一个 user 对话里自然发生 |
| C / D | §10.1 Meta-Cognition | "系统必须思考自己的思考" | reflect 作为 post-turn hook 跑, 决策 hint 注入下一 turn LLM context |
| D | §10.2 Reflection second-order cognition | (同) | reflect_post_turn 是 second-order, LLM 看 reflect 的决策反思 |
| A 全部 | §5.3 Persistent World Model | (跨 session) | `~/.explain/projects/<proj>/sessions/` + `knowledge/` 目录铺路 |
| B / C | §11.1 Cognition 是动力系统 | "graph 在状态空间中的演化" | chat 内多 turn 累积演化 (每 turn 改 graph), 非单次 build |
| F | §11.4 Self-Organization | "self-organizing conceptual dynamics" | Agent 自主决定 expand/compress/check 序列, LLM-driven emergence |
| D / E | §11.3 Cognitive Entropy | "最低 entropy 下的最大解释力" | budget 控成本; compaction 控 context entropy |

---

## 附录 B: 与 Phase 8 design 对比

| 方面 | Phase 8 | Phase 9 |
|------|---------|---------|
| Wave 数 | 5 (1/2/3/4/5) | 7 (A/B/C/D/E/F/G) |
| 任务数 | 10 | 13 |
| 主题 | 修 Phase 7 漏洞 + 哲学落地 + Phase 9 铺路 | 升级到 conversational, 复用 Phase 0-8 全部 engines |
| 测试增量 | +92 | +90 |
| 新模块 | engines/lifecycle + errors + input_validation + _llm_retry | chat/ 目录 9 文件 + persistence/storage_v2 + migration |
| 新 CLI 命令 | 0 (改 explain run 加 flag) | 2 (explain chat + explain migrate) |
| Schema 改动 | VariableNode 6 lifecycle 字段 | 0 (新目录结构, schema 不动) |
| Breaking change | 1 (input_validation fail-fast on tick=0) | 1 (sessions/ → ~/.explain/, migration 自动) |
| 哲学锚点数 | 5 章节 (§6.1, §8.1, §9.2, §9.3, §9.4, §11.3) | 5 章节 (§2.4, §5.3, §10.1, §11.1, §11.3, §11.4) |

---

**文档结束.** 下一步: `writing-plans` skill 把 design 展开成 task-by-task 实施计划, 落地到 `2026-05-17-conversational-cognitive-engine-plan.md`.
