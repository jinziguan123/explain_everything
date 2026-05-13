# Cognitive Engine Phase 5 — Design Doc

**日期**: 2026-05-13
**分支**: `dev`（从 cognitive-engine-mvp Phase 4 完工 HEAD `fd77707` 切出）
**前置**: [Phase 4 Design](2026-05-13-cognitive-engine-phase-4-design.md), [MVP Design](2026-05-13-cognitive-engine-mvp-design.md), [最终哲学以及技术实现相关设计](../../最终哲学以及技术实现相关设计.md) PART II §3 + §4 + §5 + §6

---

## 0. TL;DR

Phase 5 让系统从"single-shot compress"→"**持续 thinking**"。新增 **ExpansionEngine（纯上溯 driver）+ PhaseScheduler + Runtime Loop + `explain run` CLI**，把 Phase 4 done 状态的 graph 通过 reasoning loop 继续向上扩展出 driver 层（level=2），最后自动收敛进入 `converged` 终态。

附带 **Wave A 地基**：Provider 抽象重构（`LLM_PROVIDER` → `LLM_PROTOCOL + BASE_URL + API_KEY`，3 client 合并 2 client）+ `last_gains` 持久化（修 Phase 4 design §11 risk #1）。

**Phase 5 不做** Multi-Perspective / Reflection / Theory / Simulation / Counterfactual / attention-based scheduler —— 全推 Phase 6+。Persistent World Model 推 v0.2+。

**wow demo（informal expectation）**：跑 `explain run s_f3beb777 --budget 15` 后，graph 长出 ≥3 个语义扎实的 driver 节点（例如 `c_001 绝对化价值框架` 的 driver 可能是 `集体身份维系压力` / `传统继承机制` / `生存威胁知觉` 这类），reasoning_trace ≥8 entry，stop signal 在 budget 耗尽前由 `no_gain_for_3_ticks` 或 `no_frontier_remaining` 软停触发。

---

## 1. Scope

### 1.1 Phase 5 内

- **Wave A 地基**
  - Provider 抽象重构（`LLM_PROVIDER` 协议/供应商绑死 → `LLM_PROTOCOL + LLM_BASE_URL + LLM_API_KEY + LLM_MODEL` 三元组转发）
  - `last_gains: dict[str, float]` 持久化（candidate_id → gain）
  - CognitiveState 新字段：`tick / budget_remaining / last_gain_tick / active_frontier / reasoning_trace`
  - `Stage` Literal 加 `"converged"`
  - 新 model `TraceEntry`
- **Wave B Expansion**
  - `ExpansionEngine.expand_one_frontier`（纯上溯 driver，新增 `causes` edge + `d_NNN` 节点）
  - `expansion.yaml` prompt（含 driver plausibility 自评）
  - `graph.frontier_nodes()` helper
- **Wave C Loop**
  - `PhaseScheduler` (K=4, 1 round = 4 expand + 1 evaluate)
  - `should_stop` (3 signal: budget / no_gain / no_frontier)
  - `Runtime.run` 主循环
- **Wave D CLI + Acceptance**
  - `explain run <session_id> [--budget 15]` CLI 命令
  - `explain show --trace` 增强
  - acceptance smoke (真 LLM 跑 s_f3beb777)

### 1.2 推到 Phase 6+

- ❌ Multi-Perspective (`P` 字段、cross-perspective mapping、perspective-scoped relations)
- ❌ Reflection Engine / 中途 HITL
- ❌ Theory Formation Engine
- ❌ Simulation / Counterfactual Engine
- ❌ `attention_map: dict[str, float]` 字段 + attention-based scheduler
- ❌ Expansion 横向 amplifier (`amplifies` edge)
- ❌ Expansion 下探 mechanism (mid-layer 节点)
- ❌ batch scoring prompt (cost 降低)
- ❌ Phase 5 round 内出新 compression candidate
- ❌ 变量去重 / semantic anchor (driver 与 candidate 语义近似时的合并)

### 1.3 推到 v0.2+

- ❌ Persistent World Model (`M` 字段 + `world_model.json` + cross-session 变量复用)
- ❌ Render (Markdown `explanation.md`)
- ❌ Web UI / 可视化
- ❌ Variable Lifecycle (Birth → Decay → Death)
- ❌ Embedding / clustering

---

## 2. CLI & State Machine

### 2.1 新命令

```bash
explain run <session_id> [--budget 15]
```
单一职责：跑 reasoning loop (Expansion + Scheduler + Stop)。budget 默认 15。

### 2.2 Stage Literal 拓展

```python
Stage = Literal[
    "bootstrap_pending",   # HITL 1 完成 (Phase 3)
    "insight_pending",     # Compression + Evaluation 完成，等 HITL 2 (Phase 4)
    "done",                # HITL 2 完成 (Phase 4) ← Phase 5 入口
    "converged",           # NEW: explain run 跑完 (Phase 5 终态)
]
```
单调向前。`explain run` 要求 stage=`done`，跑完进 `converged`。stage=`converged` 重跑 `explain run` → exit 4。旧 session JSON `done` 反序列化兼容（pydantic Literal 默认）。

### 2.3 状态机全貌

```
explain new "..."     → s_xxx (bootstrap_pending)
                          ↓ HITL 1 (review_phenomena)
explain compress      → s_xxx (insight_pending → done)
                          ↓ HITL 2 (review_insights)
explain run           → s_xxx (done → converged)     # Phase 5
                          ↓
explain show --trace  → 渲染 graph + reasoning_trace
```

Phase 6 可能允许 `converged → run` 再次（"继续思考"），Phase 5 暂不支持。

### 2.4 数据流

```
$ explain run s_f3beb777 --budget 15
    ↓ load session, assert stage == "done"
    ↓ state.budget_remaining = 15, tick = 0
    ↓
loop while not should_stop(state):
    action = scheduler.pick(state)
    if action == "expand":
        frontier = state.graph.frontier_nodes()
        target_id = frontier[0]
        new_drivers = expansion.expand_one_frontier(state, target_id, llm)
        gain_delta = mean_plausibility(new_drivers)
    else:  # action == "evaluate"
        gain_delta = 0.0  # checkpoint, no LLM call
    state.reasoning_trace.append(TraceEntry(...))
    if gain_delta >= GAIN_THRESHOLD: state.last_gain_tick = state.tick
    state.tick += 1; state.budget_remaining -= 1
    SessionStore.save(session)   # 每 tick 落盘，Ctrl-C 可恢复
    ↓
state.meta.stage = "converged"
SessionStore.save(session)
```

---

## 3. ExpansionEngine

### 3.1 文件

```
src/explain_engine/engines/expansion.py
```

### 3.2 输入

`state: CognitiveState` + `target_id: str` (frontier 节点 id) + `llm: LLMClient`。

target 必须满足：`abstraction_level >= 1` 且 没有 incoming `causes` edge。否则抛 `ValueError`。

### 3.3 prompt 文件

```
src/explain_engine/llm/prompts/expansion.yaml
```

**system**:
> 你的任务是给定一个 abstract variable（已建立的 explanation candidate），找出**它的 driver**（更上游的 cause）。Driver 必须是可被进一步检验的机制变量，不是 cosmic 哲学名词（如 "熵增" / "进化" / "宇宙真理" 等抽象到无法 falsify 的概念）。每个 driver 必须能解释：为什么会形成这个 abstract。

**user_template** 输入字段:
- `root_question`
- `target_node`: name + description
- `target_outgoing_edges`: 该 target 解释的 concrete list（manifests_as edges）
- `existing_drivers`: 已在 graph 里的所有 `d_NNN` list（避免重复）

### 3.4 输出 schema

```python
class _DriverCandidate(BaseModel):
    name: str             # "集体身份维系压力"
    description: str      # 1-2 句定义边界
    mechanism: str        # "为什么 driver 生成 target abstract"
    plausibility: int = Field(ge=1, le=5)   # LLM 自评 driver→target mechanism

class ExpansionOutput(BaseModel):
    drivers: list[_DriverCandidate]   # 1-3
```

### 3.5 主函数

```python
async def expand_one_frontier(
    state: CognitiveState,
    target_id: str,
    llm: LLMClient,
) -> list[str]:
    """对单个 frontier 节点做一次 expansion，出 1-3 driver，灌进 graph，返回新 driver id list。

    副作用：
    - state.graph 新增 1-3 d_NNN VariableNode (level=2, source="llm", epistemic="inference")
    - state.graph 新增 1-3 causes RelationEdge (driver → target)
    返回值：
    - list[str]: 新建的 d_NNN id list (供 Runtime 算 gain 用)
    """
```

### 3.6 校验

| 情况 | 处理 |
|---|---|
| LLM 返 0 driver | warn，跳过该 frontier，**不阻断 loop**（Runtime 选下一个 frontier）|
| LLM 返 >3 driver | 截断前 3 |
| driver.name 跟现有节点字符串完全相等 | 复用现有 id（不新建 node），只加 edge |
| driver.name 跟现有节点高语义相似 (cosine ≥ 0.9 etc) | Phase 5 不做去重，Phase 6 加（embedding） |
| LLM 输出不合 schema | retry 1 次，仍失败抛 `SchemaValidationError`（复用 Phase 4 异常体系） |
| target 不满足 frontier 条件 | 抛 `ValueError` (defensive) |

### 3.7 expansion_gain

Phase 4 有 `compression_gain`。Phase 5 新增 `expansion_gain` 给 Runtime 算 stop signal:

```python
expansion_gain = mean(d.plausibility for d in new_drivers) / 5.0
```

合并在 expansion LLM 调用内（driver 自评 plausibility），不另调 scoring.yaml。Phase 6 改独立 scoring（避免 LLM 自我吹捧）。

### 3.8 ID 规则

- driver: `d_001`, `d_002`, ... （d = driver，跟 c_NNN / p_NNN 独立计数）
- edge: `e_NNN`（沿用现规则）
- 前缀代表 **来源 engine**，level 代表 **抽象高度**，正交。Phase 5 之后若 Compression 出 level=2 仍用 `c_NNN`（来源是 compression），不复用 `d_NNN`。

---

## 4. Scheduler / Stop Signal

### 4.1 PhaseScheduler

```python
# src/explain_engine/runtime/scheduler.py
class PhaseScheduler:
    K: int = 4

    def pick(self, state: CognitiveState) -> Literal["expand", "evaluate"]:
        if state.tick % (self.K + 1) < self.K:
            return "expand"
        return "evaluate"
```

1 round = K expand + 1 evaluate = 5 tick (默认 K=4)。budget=15 → 3 round = 12 expand + 3 evaluate。LLM call ≈ 12 × 1 (expansion 合并 plausibility 自评) = **~12 LLM call/run**。

### 4.2 为什么 Phase 5 round 内**不放 compress**

最早 brainstorm 时考虑 1 round = 4 expand + 1 compress + 1 evaluate。design 阶段重新审视："compress 在 Phase 5 round 内干什么？"

- 如果 = **出新 candidate**：Phase 4 Compression 输入是 concrete pool，Phase 5 Expansion 出的是 driver 不是 concrete，pool 没变，重跑会出几乎一样的 candidate
- 如果 = **重 score 已有 candidate**：每次 35 LLM call (5 candidate × 7 edge)，3 round = 105 call，cost 失控
- 如果 = **no-op 占位**：浪费 budget

**结论**：Phase 5 不放 compress。新 candidate / 重 score 推 Phase 6（跟 batch scoring prompt 一起）。

### 4.3 evaluate 在 Phase 5 干什么

✅ **graph health checkpoint**：0 LLM call，只写 reasoning_trace 一个 snapshot entry（当前 frontier 剩多少 / depth max / abstract 节点数 / driver 节点数）。是"停下来 snapshot"而非"重 score"。Phase 6 加 reflection 后可在此 hook 触发反思。

### 4.4 stop_signal

```python
# src/explain_engine/runtime/stop.py
def should_stop(state: CognitiveState) -> tuple[bool, str | None]:
    if state.budget_remaining <= 0:
        return True, "budget_exhausted"
    if state.tick - state.last_gain_tick >= 3:
        return True, "no_gain_for_3_ticks"
    if not state.graph.frontier_nodes():
        return True, "no_frontier_remaining"
    return False, None
```

3 个 signal，按顺序检查谁触发。stop reason 写入 reasoning_trace 末尾 entry 的 `target_node_id` field（特殊值，CLI 渲染时识别）。

---

## 5. Runtime Loop

### 5.1 文件

```
src/explain_engine/runtime/__init__.py     NEW
src/explain_engine/runtime/runtime.py      NEW (Runtime.run)
src/explain_engine/runtime/scheduler.py    NEW (PhaseScheduler)
src/explain_engine/runtime/stop.py         NEW (should_stop + signals)
```

### 5.2 主循环

```python
async def run(
    state: CognitiveState,
    llm: LLMClient,
    budget: int,
) -> str:
    """Phase 5 reasoning loop. 返回 stop reason。"""
    state.budget_remaining = budget
    state.tick = 0
    state.last_gain_tick = 0
    scheduler = PhaseScheduler(K=4)

    while True:
        stop, reason = should_stop(state)
        if stop:
            break

        action = scheduler.pick(state)
        target_id, gain_delta, llm_calls = None, 0.0, 0

        if action == "expand":
            frontier = state.graph.frontier_nodes()
            if frontier:
                target_id = frontier[0]   # pick lowest level (c_NNN 先于 d_NNN)
                new_drivers = await expansion.expand_one_frontier(state, target_id, llm)
                gain_delta = mean_plausibility(new_drivers)
                llm_calls = 1
        # action == "evaluate": no-op, snapshot only

        state.reasoning_trace.append(TraceEntry(
            tick=state.tick,
            action=action,
            target_node_id=target_id,
            gain_delta=gain_delta,
            llm_calls=llm_calls,
            timestamp=datetime.now().isoformat(),
        ))

        if gain_delta >= GAIN_THRESHOLD:   # 0.1
            state.last_gain_tick = state.tick

        state.tick += 1
        state.budget_remaining -= 1

    state.meta.stage = "converged"
    return reason
```

### 5.3 GAIN_THRESHOLD

`0.1`（即 plausibility ≥ 0.5 / 5 算"有 gain"）。Phase 5 跑 ≥1 真实 session 后 tune。常量放 `runtime/stop.py`。

### 5.4 落盘策略

Phase 4 HITL 中 Ctrl-C 不落盘（半成品状态）。Phase 5 loop 每 tick 后 SessionStore.save() **一次**（loop 中间状态可恢复，用户中途 Ctrl-C 不丢进度）。

cost: budget=15 → 15 次磁盘 IO/run，session.json ~50KB，可接受。

stage 在 loop 中保持 `done`，loop 正常结束 + 最后一次 save 时才改 `converged`。Ctrl-C 留 stage=`done` 不变，重跑 `explain run` 从 tick=0 重头跑（不支持续跑；Phase 6 加）。

---

## 6. Schema 改动

### 6.1 `state.py`：Stage Literal

```python
Stage = Literal["bootstrap_pending", "insight_pending", "done", "converged"]
```

### 6.2 `state.py`：CognitiveState 新字段

```python
class CognitiveState(BaseModel):
    # 已有 (Phase 0-4)
    graph: ExplanationGraph
    insight_candidates: list[str]
    meta: SessionMeta

    # NEW Phase 5
    tick: int = 0
    budget_remaining: int = 0           # CLI 启动时注入
    last_gain_tick: int = 0
    last_gains: dict[str, float] = {}   # Phase 4 evaluation 持久化
    active_frontier: list[str] = []     # 当前 round 待 expand 的 c_NNN list
    reasoning_trace: list[TraceEntry] = []
```

旧 session JSON 反序列化默认值兼容（pydantic Field default）。

### 6.3 `state.py`：TraceEntry (新 model)

```python
class TraceEntry(BaseModel):
    tick: int
    action: Literal["expand", "compress", "evaluate"]
    target_node_id: str | None
    gain_delta: float
    llm_calls: int
    timestamp: str   # iso8601
```

放 `state.py`（跟 CognitiveState 紧耦合），不单独 `trace.py`。

### 6.4 `nodes.py`：不动

`abstraction_level: int` 已支持 driver（level=2）。`source` / `epistemic` / `id` / `name` / `description` / `confidence` 均不变。

### 6.5 `edges.py`：不动

`relation_type` Literal 已含 `causes`。Phase 4 只用过 `manifests_as`，Phase 5 第一次激活 `causes`。

### 6.6 `graph.py`：加 helper

```python
def frontier_nodes(self) -> list[str]:
    """abstraction_level >= 1 且没有 incoming causes edge 的节点 id list。

    返回顺序：按 abstraction_level 升序（level=1 c_NNN 先于 level=2 d_NNN）。
    同 level 内按 node id 字符串升序。
    """
```

### 6.7 ID 规则总览

| 前缀 | 含义 | 来源 |
|---|---|---|
| `p_NNN` | concrete (level 0) | Bootstrap (Phase 3) |
| `c_NNN` | abstract (level 1) | Compression (Phase 4) |
| `d_NNN` | driver (level 2+) | Expansion (Phase 5) |
| `e_NNN` | edge | 跨所有 edge type |

前缀代表 **来源 engine**，level 代表 **抽象高度**。

---

## 7. Provider 抽象重构 + last_gains (Wave A)

### 7.1 .env 改动

**旧 (Phase 4)**:
```env
LLM_PROVIDER=claude|openai|deepseek
CLAUDE_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
LLM_MODEL=claude-opus-4-7
```

**新 (Phase 5)**:
```env
LLM_PROTOCOL=anthropic|openai
LLM_BASE_URL=https://api.anthropic.com      # 或 https://api.deepseek.com/anthropic, https://api.openai.com/v1, etc.
LLM_API_KEY=sk-xxx
LLM_MODEL=claude-opus-4-7
```

3 client → 2 client：协议跟供应商解耦。DeepSeek 既能跑 OpenAI 协议（`/v1`）又能跑 Anthropic 协议（`/anthropic`），通过切 `LLM_BASE_URL` 实现。

### 7.2 代码改动

```
src/explain_engine/llm/
├── client.py              MODIFY: factory by LLM_PROTOCOL
├── anthropic_protocol.py  NEW: 用 anthropic SDK + base_url
├── openai_protocol.py     NEW: 用 openai SDK + base_url
├── errors.py              不变
├── claude.py              DELETE
├── openai.py              DELETE
└── deepseek.py            DELETE
```

`client.py` factory:
```python
def make_client() -> LLMClient:
    proto = os.environ["LLM_PROTOCOL"]
    base_url = os.environ["LLM_BASE_URL"]
    api_key = os.environ["LLM_API_KEY"]
    model = os.environ["LLM_MODEL"]

    match proto:
        case "anthropic":
            return AnthropicProtocolClient(base_url, api_key, model)
        case "openai":
            return OpenAIProtocolClient(base_url, api_key, model)
        case _:
            raise ValueError(f"Unknown LLM_PROTOCOL: {proto}")
```

### 7.3 SDK 复用

- `anthropic` SDK 原生支持 `base_url` 配置（DeepSeek anthropic endpoint / Bedrock / Vertex 都通过 base_url 切）
- `openai` SDK 原生支持 `base_url` 配置（DeepSeek openai endpoint / Azure / Together / Groq 同理）

structured output 实现复用 Phase 4 策略：
- Anthropic 协议: `tools(input_schema=schema.model_json_schema())`
- OpenAI 协议: `response_format={"type": "json_schema", "schema": ...}`

### 7.4 .env 迁移

不写迁移脚本。`.env.example` 写新格式，`README.md` 加 Phase 4→Phase 5 mapping 表（5-10 行）：

| Phase 4 | Phase 5 等价 |
|---|---|
| `LLM_PROVIDER=claude` | `LLM_PROTOCOL=anthropic` + `LLM_BASE_URL=https://api.anthropic.com` |
| `LLM_PROVIDER=openai` | `LLM_PROTOCOL=openai` + `LLM_BASE_URL=https://api.openai.com/v1` |
| `LLM_PROVIDER=deepseek` (openai 协议) | `LLM_PROTOCOL=openai` + `LLM_BASE_URL=https://api.deepseek.com/v1` |
| `LLM_PROVIDER=deepseek` (anthropic 协议) | `LLM_PROTOCOL=anthropic` + `LLM_BASE_URL=https://api.deepseek.com/anthropic` |
| `CLAUDE_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY` | `LLM_API_KEY` (统一) |

`.env.bak` 已存在（用户在 brainstorm 前手动备份），不动。

### 7.5 last_gains 持久化

```python
# Phase 4 EvaluationEngine.score_all 末尾追加：
state.last_gains = {cid: gain for cid, gain in zip(state.insight_candidates, gains)}
```

HITL 2 `review_insights` 渲染 candidate gain 列时复用 `state.last_gains[candidate_id]`，stage=insight_pending 重入时不再显 0.0。

drop 候选时 `state.last_gains.pop(candidate_id, None)` 级联清。

修 Phase 4 design §11 risk #1 (reentry 限制)。

---

## 8. Testing

### 8.1 L1 Schema (~6)

`test_schema_state_stage_converged.py`:
- 旧 stage `"done"` 反序列化兼容
- 新 stage `"converged"` round-trip
- Stage 单调向前（converged 不能回 done，pydantic 不验，靠 CLI 校验）

`test_schema_state_phase5_fields.py`:
- CognitiveState 新字段默认值 (tick=0, budget_remaining=0, etc.)
- last_gains round-trip + drop 清理
- reasoning_trace round-trip
- 旧 JSON 无新字段反序列化默认值

`test_schema_trace_entry.py`:
- TraceEntry 必需字段
- timestamp ISO 8601 校验
- action Literal 校验

`test_schema_graph_frontier.py`:
- frontier_nodes 返 level≥1 且 没 incoming causes
- 排序按 level 升序
- 空 graph → []

### 8.2 L1 Engines (~10)

`test_engines_expansion.py`:
- mock LLM 返 3 driver → 验证 d_NNN id 顺序 + level=2 + epistemic="inference" + source="llm"
- LLM 返 4 driver → 截断 3
- LLM 返 0 driver → warn + 不阻断
- driver.name 跟现有 node 字符串相等 → 复用现有 id
- LLM 输出不合 schema → retry 1 次后抛 `SchemaValidationError`
- target 不满足 frontier 条件 → ValueError
- expansion_gain 公式正确（mean plausibility / 5）
- existing_drivers 传入 prompt 正确
- causes edge 建立 (driver → target)
- prompt 调用入参 `target_outgoing_edges` 正确

### 8.3 L1 Scheduler / Loop (~6)

`test_runtime_scheduler.py`:
- PhaseScheduler.pick K=4 各 tick 返回值（tick 0-3 → expand, tick 4 → evaluate, tick 5-8 → expand, ...）
- K=3 / K=5 自定义 round 长度

`test_runtime_stop.py`:
- budget=0 → budget_exhausted
- tick=5, last_gain_tick=2 → no_gain_for_3_ticks
- empty frontier → no_frontier_remaining
- 多 signal 同时触发返回顺序（budget 优先）

`test_runtime_run.py`:
- mock LLM 走通完整 budget=5 path → stage=converged
- last_gain_tick 更新（gain ≥ threshold tick 才记）
- reasoning_trace 完整记录（每 tick 1 entry）
- 落盘每 tick 调用 SessionStore.save (mock 验证 call count = budget)

### 8.4 L1 Provider 重构 (~6)

`test_llm_anthropic_protocol.py`:
- base_url 配置传入 SDK
- API_KEY missing → 报错
- structured output (tools) 路径

`test_llm_openai_protocol.py`:
- base_url 配置传入 SDK
- API_KEY missing → 报错
- structured output (response_format) 路径

`test_llm_client_factory.py`:
- LLM_PROTOCOL=anthropic → AnthropicProtocolClient
- LLM_PROTOCOL=openai → OpenAIProtocolClient
- LLM_PROTOCOL 不存在 → ValueError

### 8.5 L1 last_gains (~2)

`test_engines_evaluation_last_gains.py`:
- score_all 写入 last_gains 跟 insight_candidates 顺序一致
- drop candidate → last_gains[id] pop

`test_hitl_review_insights_last_gains.py`:
- HITL 2 render gain 列读 state.last_gains 而非临时算
- stage=insight_pending 重入 render gain 不显 0.0

### 8.6 L3 CLI (~5)

`test_cli_run.py`:
- full mock flow (run + tick=5 loop) → stage="converged"
- session id 不存在 → exit 1
- stage="bootstrap_pending" 重跑 → exit 4
- stage="converged" 重跑 → exit 4
- --budget 0 → 立即 stop (budget_exhausted)
- LLM 抛 `LLMError` → exit 1

`test_cli_show_trace.py`:
- explain show --trace 渲染 reasoning_trace 表
- stop reason 在表末尾显示

### 8.7 L4 Integration (1, skip default)

`@pytest.mark.integration test_run_real_llm`:
- 复用 s_f3beb777，跑 `explain run --budget 5` (cheap version)
- 验证 ≥1 d_NNN 出现，trace 完整，stop_reason ∈ {budget_exhausted, no_gain, no_frontier}

### 8.8 总数

~34 个新增，total 159 + 34 ≈ **193**。Phase 0-4 测试不破。

---

## 9. 文件结构

```
src/explain_engine/
├── engines/
│   ├── bootstrap.py
│   ├── compression.py
│   ├── evaluation.py             MODIFY (last_gains 持久化)
│   └── expansion.py              NEW
├── runtime/                      NEW directory
│   ├── __init__.py
│   ├── runtime.py                NEW (Runtime.run)
│   ├── scheduler.py              NEW (PhaseScheduler)
│   └── stop.py                   NEW (should_stop + GAIN_THRESHOLD)
├── hitl/
│   └── cli_interactive.py        MODIFY (review_insights 读 last_gains)
├── llm/
│   ├── client.py                 MODIFY (factory)
│   ├── anthropic_protocol.py     NEW
│   ├── openai_protocol.py        NEW
│   ├── claude.py                 DELETE
│   ├── openai.py                 DELETE
│   ├── deepseek.py               DELETE
│   ├── errors.py
│   └── prompts/
│       ├── variable_extraction.yaml
│       ├── compression.yaml
│       ├── scoring.yaml
│       └── expansion.yaml        NEW
├── schema/
│   ├── nodes.py
│   ├── edges.py
│   ├── graph.py                  MODIFY (frontier_nodes)
│   └── state.py                  MODIFY (Stage + 字段 + TraceEntry)
└── cli.py                        MODIFY (explain run + show --trace)

tests/
├── test_schema_state_stage_converged.py        NEW
├── test_schema_state_phase5_fields.py          NEW
├── test_schema_trace_entry.py                  NEW
├── test_schema_graph_frontier.py               NEW
├── test_engines_expansion.py                   NEW
├── test_engines_evaluation_last_gains.py       NEW
├── test_hitl_review_insights_last_gains.py     NEW
├── test_runtime_scheduler.py                   NEW
├── test_runtime_stop.py                        NEW
├── test_runtime_run.py                         NEW
├── test_llm_anthropic_protocol.py              NEW
├── test_llm_openai_protocol.py                 NEW
├── test_llm_client_factory.py                  NEW
├── test_cli_run.py                             NEW
└── test_cli_show_trace.py                      NEW

.env.example                      MODIFY (新格式)
README.md                         MODIFY (mapping 表)
```

---

## 10. 验收标准

- [ ] `explain run s_f3beb777 --budget 15` 真 LLM 跑通，stage 变 converged
- [ ] graph 长出 ≥3 d_NNN（driver 层），名字定性扎实（informal 人评跟 Phase 4 wow demo 同标准）
- [ ] reasoning_trace 完整（每 tick 1 entry），stop reason 写入末尾 entry
- [ ] stop signal 触发原因合理 (budget_exhausted / no_gain_for_3_ticks / no_frontier_remaining 之一)
- [ ] `explain show s_f3beb777 --trace` 渲染 graph + trace 表
- [ ] HITL 2 重入显 last_gains 非 0.0（Phase 4 risk #1 修复）
- [ ] Provider 重构后 `LLM_PROTOCOL=anthropic + LLM_BASE_URL=https://api.deepseek.com/anthropic` 能跑通 DeepSeek（验证协议/供应商正交）
- [ ] L1+L2+L3 测试 ≥30 PASS
- [ ] Phase 0-4 测试 159 全 PASS（不破）
- [ ] ruff check 0 error
- [ ] 6 tension 全闭环：
  - #1 scope size: Q1=A 完整 loop
  - #2 Provider 重构: Wave A 解决
  - #3 last_gains: Wave A 解决
  - #4 Compression coverage: Q7=B observe-then-act（Phase 5 末尾 measure，仍偏低再 prompt iterate）
  - #5 Multi-Perspective: Q8 推 Phase 6
  - #6 CognitiveState 字段: R 加, A/M/P/S/C/T 推 Phase 6+ / v0.2

---

## 11. 已知风险 / Open Questions

1. **空洞 driver**：LLM 上溯 2 层后容易出 "宇宙真理 / 进化 / 熵增" 这种 cosmic 名词。prompt 反例 + plausibility 自评打分 1-5 是软兜底；硬兜底等 Phase 6 加 driver 去重 + 语义 anchor。

2. **plausibility 自评偏高**：LLM 给自己出的 driver 评 plausibility 容易自我吹捧（4-5 偏多），导致 expansion_gain 总在 0.8 以上，stop signal `no_gain_for_3_ticks` 永远不触发。Phase 6 改用独立 scoring LLM 调用（跟 Phase 4 scoring.yaml 一致）。

3. **GAIN_THRESHOLD = 0.1 拍脑袋**：plausibility ≥ 0.5 / 5 算"有 gain"。Phase 5 跑 ≥1 真实 session 后 tune。常量集中在 `runtime/stop.py` 便于调。

4. **K=4 拍脑袋**：1 round = 4 expand + 1 evaluate。Phase 5 跑 ≥1 真实 session 后 tune（K=3 / 4 / 5 之间）。

5. **frontier 选择策略**：Phase 5 取 `frontier[0]` (level 最低先)，简单但可能漏到深层。Phase 6 加 attention score 后改 `pick(highest_score)`。

6. **graph 过深退化**：3 层（concrete + abstract + driver）已经接近 LLM 上下文承载力。Phase 5 不防 4+ 层（d_NNN 之上还有 super-driver d_NNN），但 stop signal `no_gain_for_3_ticks` + plausibility 真实降低时兜底。

7. **`explain run` Ctrl-C 半进度**：Phase 5 loop 每 tick 后落盘，但 stage 保持 `done` 直到正常结束。重跑 `explain run` 从 tick=0 重头跑（不续跑；Phase 6 加"resume from tick N"）。中间 trace 被覆盖。

8. **Provider 重构 cost**：3 client → 2 client 是结构改动，整个 codebase ~10 个 import + ~15 个 mock 路径要改。Wave A 集中做完，避免 dev 分支期间双层 client 并存的混淆。

9. **Compression coverage 不主动修**（Q7=B 决策）：Phase 5 acceptance 后再 measure，如果 driver 层加进后 coverage 仍 ≤ Phase 4 水平（5 candidate × 2/12），**Phase 5 末尾** 加一个 prompt iteration cleanup task 做软约束。这是已知的"observe-then-act"路径，不是 risk。

10. **TraceEntry.action Literal 含 "compress"**：Phase 5 不用，但 schema 留位（reasoning_trace 反序列化对 Phase 6 兼容）。Phase 6 加 compress action 后无需改 schema。

---

## 12. Phase 5 之后

### 12.1 Phase 6 brainstorm 启动条件

- Phase 5 跑通 ≥1 真实 session（建议 s_f3beb777 + 至少 1 个新种子问题）
- 知道 K / GAIN_THRESHOLD 真实合理范围
- 知道 driver plausibility 自评是否够用（还是必须独立 scoring）
- 知道 graph 深度上限（3 层是否够）
- 知道 Compression coverage 是否随 Expansion 自然提升（Q7=B 决策的验证）

### 12.2 Phase 6 起点（按 leverage 降序）

- **必做 1**: attention-based Scheduler + `attention_map: dict[str, float]` 字段（替换 PhaseScheduler，引入 P §6 Attention Dynamics）
- **必做 2**: Multi-Perspective (`P: list[Perspective]` 字段 + perspective-scoped relations + cross-perspective mapping，引入哲学文档 PART I §七 explanation diversity)
- **必做 3**: Reflection Engine + 中途 HITL（meta-cognition，§II §4.6 / §12）
- **必做 4**: batch scoring prompt（cost 降低，Phase 4 design §11 risk #6 修复）
- **选做 5**: Expansion 横向 amplifier (`amplifies` edge) + 下探 mechanism (mid-layer 节点)
- **选做 6**: 变量去重 / semantic anchor（Phase 6 driver 多了之后必然要做）

### 12.3 推 v0.2+

- Persistent World Model (M 字段 + world_model.json + cross-session 变量复用)
- Theory Formation Engine
- Simulation / Counterfactual Engine
- Variable Lifecycle (Birth → Decay → Death)
- Render (explanation.md)
- Web UI / 可视化

---
