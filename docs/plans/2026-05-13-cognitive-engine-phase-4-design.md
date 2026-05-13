# Cognitive Engine Phase 4 — Design Doc

**日期**: 2026-05-13
**分支**: `cognitive-engine-mvp`
**前置**: [Phase 0+1+2 完工](2026-05-13-cognitive-engine-phase-0-1-2-plan.md), [Phase 3 设计](2026-05-13-cognitive-engine-phase-3-design.md), [MVP Design](2026-05-13-cognitive-engine-mvp-design.md), [最终哲学以及技术实现相关设计](../../最终哲学以及技术实现相关设计.md) PART II §4 + §7

---

## 0. TL;DR

Phase 4 把 Phase 3 留下的 12 现象**压成 abstract**。新增 **Compression Engine + Evaluation Engine + `explain compress <id>` CLI 命令 + HITL 2**。Compression LLM 一次出 3-5 个 abstract 候选（每候选 = 1 个 abstract node + N 条 `manifests_as` edge），Evaluation 用 `compression_gain = representation_reduction × explanatory_preservation` 排序，HITL 2 让用户 keep/edit/drop。

**Phase 4 不做** Expansion / Scheduler / 完整 Loop —— 推 Phase 5。理由：s_e3fb6675 已有 12 跨域现象，Compression 本身就够 wow，先把这一刀切深。

**wow demo（informal expectation）**: 跑 `explain compress s_e3fb6675` 后，候选里至少有一个名字接近 "长期不确定性" / "社会竞争结构" 这类高维隐藏变量，gain ≥ 0.5，coverage ≥ 7/12。

---

## 1. Scope

### 1.1 Phase 4 内

- **Compression Engine** — 调 `compression.yaml` prompt 生 3-5 个 abstract 候选
- **Evaluation Engine** — 调 `scoring.yaml` 给每条 `manifests_as` edge 打 mechanism_plausibility 1-5，算 `compression_gain`，排序
- **HITL 2** (`review_insights`) — rich table 总览 + 逐候选 keep/edit/drop/view-full
- **CLI**: `explain compress <session_id>`
- **新 prompt**: `compression.yaml`, `scoring.yaml`
- **新异常类**: `LLMError`, `SchemaValidationError`（顺手回填 Phase 3 `_run_new`）
- **schema 改动**:
  - `Stage` Literal 加 `"insight_pending"` / `"done"`
  - `VariableNode` 加 `source: Literal["llm", "user"]`
  - `ExplanationGraph` 加 `remove_node` / `remove_edge`

### 1.2 推到 Phase 5+

- ❌ `ExpansionEngine`（上溯到 driver / 横向 amplifier）
- ❌ Scheduler（轮转 expand/compress/evaluate）
- ❌ 完整 Reasoning Loop
- ❌ `explain expand` / `explain run` 命令
- ❌ `CognitiveState.attention_map` / `reasoning_trace`
- ❌ 多轮 Compression（同一 session 多次跑出新 abstract）
- ❌ "用户 challenge 一条 edge" 的 mechanism

### 1.3 推到 v0.2+

- ❌ Render（Markdown / `explanation.md`）
- ❌ Web UI / 可视化
- ❌ HITL 2 中"用户自己加 abstract 候选"

---

## 2. CLI & State Machine

### 2.1 命令

```
explain compress <session_id>
```
单一职责：跑 Compression + Evaluation + HITL 2。Phase 5 加 Expansion 时另起命令（`explain expand` 或并入 `explain run`），不重构本命令。

### 2.2 Stage Literal 拓展

```python
Stage = Literal[
    "bootstrap_pending",   # Phase 3 现状: HITL 1 完成
    "insight_pending",     # 新: Compression + Evaluation 完成，等 HITL 2
    "done",                # 新: HITL 2 完成
]
```

**单调向前，不可回退**。重跑 `done` → exit 4 报错。重跑 `insight_pending` → 跳过 LLM，直接进 HITL 2（省 LLM 钱）。

### 2.3 数据流

```
$ explain compress s_e3fb6675
    ↓ load session, assert stage == "bootstrap_pending"
    ↓
Compression.propose_candidates(state, llm)
    ↓ → 3-5 candidates，每候选灌进 graph (c_NNN + manifests_as edges)
    ↓
Evaluation.score_all(state, llm)
    ↓ → 每候选算 compression_gain，state.insight_candidates 按 gain 降序
    ↓
SessionStore.save(session)   # ⬅ 中间落地点 (stage=insight_pending)
    ↓
review_insights(state, console)   # HITL 2
    ↓ keep/edit/drop/view-full
    ↓ drop 的 candidate 调 graph.remove_node() 级联删
    ↓ edit 的 candidate.source 升级 "llm" → "user"
    ↓
state.insight_candidates 清空
state.meta.stage = "done"
SessionStore.save(session)
```

---

## 3. Compression Engine

### 3.1 文件

```
src/explain_engine/engines/compression.py
```

### 3.2 输出 schema

```python
class _CoverageItem(BaseModel):
    concrete_id: str           # 必须 ∈ existing p_NNN
    mechanism: str             # "为什么 abstract manifests as 这个 concrete"

class _CompressionCandidate(BaseModel):
    name: str                  # "长期不确定性"
    description: str           # 1-2 句定义边界
    coverage: list[_CoverageItem]   # ≥2 才算 compression

class CompressionOutput(BaseModel):
    candidates: list[_CompressionCandidate]   # 3-5
```

### 3.3 主函数

```python
async def propose_candidates(
    state: CognitiveState,
    llm: LLMClient,
    min_count: int = 3,
    max_count: int = 5,
) -> None:
    """LLM 出 3-5 个 abstract 候选，灌 state.graph，落 state.insight_candidates。

    副作用：
    - state.graph 新增 N 个 level=1 VariableNode (id=c_001..c_00N)
    - state.graph 新增若干 manifests_as RelationEdge (id=e_001..)
    - state.insight_candidates = [c_001, ..., c_00N]（未排序，Evaluation 阶段排）
    """
```

### 3.4 校验逻辑

| 校验 | 处理 |
|---|---|
| LLM 输出 candidates < 3 | warn，接受 |
| LLM 输出 candidates > 5 | 截断前 5 |
| candidate.coverage < 2 | 淘汰该候选（不进 graph）|
| coverage.concrete_id ∉ existing p_NNN | retry 1 次同 prompt，仍失败抛 `SchemaValidationError` |
| 多候选 coverage 重叠（同一 concrete 被多个 abstract 覆盖）| 允许（多角度解释是 feature）|

### 3.5 ID 规则

- abstract: `c_001`, `c_002`, ... （c = compression candidate）
- edge: `e_001`, `e_002`, ... （现有规则）
- HITL 2 drop 不 reset ID（c_002 被 drop 后 c_003 不前移），保持 ID 单调

---

## 4. Evaluation Engine

### 4.1 文件

```
src/explain_engine/engines/evaluation.py
```

### 4.2 compression_gain 公式

```python
representation_reduction = covered_concrete_count / total_concrete_count
   # Python，确定性，跨问题可比

explanatory_preservation = mean(mechanism_plausibility for edge in manifests_as) / 5.0
   # LLM-as-judge 调 scoring.yaml，每条 edge 评 1-5 整数

compression_gain = representation_reduction × explanatory_preservation
```

### 4.3 主函数

```python
async def score_all(
    state: CognitiveState,
    llm: LLMClient,
) -> dict[str, float]:
    """对 state.insight_candidates 每个 candidate 算 compression_gain。

    副作用：
    - state.insight_candidates 按 gain 降序重排
    返回值：
    - dict[candidate_id, gain]（供 HITL 2 渲染）
    """
```

### 4.4 边界

| 情况 | 处理 |
|---|---|
| candidate 无 outgoing edge | gain = 0，但 candidate 已被 Compression 阶段淘汰，不应到此 |
| LLM scoring 返非 1-5 整数 | retry 1 次，仍失败抛 `SchemaValidationError` |
| candidate 覆盖 1/12 但 mechanism 满分 | gain ≈ 0.083 × 1.0 = 0.083，垫底 |
| candidate 覆盖 12/12 但 mechanism 全 1 分 | gain = 1.0 × 0.2 = 0.2，垫底（空洞抽象兜底）|

### 4.5 cost 估算

5 候选 × 平均 7 coverage = 35 次 scoring LLM 调用。加上 1 次 compression LLM 调用 = ~36 次/run。**Phase 5 考虑批量 scoring prompt**（一次评多条 edge），Phase 4 不做。

---

## 5. HITL 2 — `review_insights`

### 5.1 文件

```
src/explain_engine/hitl/cli_interactive.py    # ADD review_insights()
```

### 5.2 Step 1: 总览 rich table

```
   候选 (按 compression_gain 降序)
ID      名称              描述                      Coverage   Mechanism   Gain
c_001   长期不确定性       对未来收入/政策...        9/12       0.82        0.62
c_002   社会竞争结构       同辈比较 + 资源稀缺...    7/12       0.85        0.50
c_003   生活成本上涨       住房/教育/医疗刚性...     5/12       0.78        0.32
c_004   传统价值观瓦解     婚育/储蓄/集体...         4/12       0.65        0.22
c_005   技术替代消费       短视频/共享 / 数字...     3/12       0.70        0.18
```

### 5.3 Step 2: 逐候选交互

```
[1/5] c_001  长期不确定性  (gain=0.62)
       描述: 对未来收入、政策、社会保障的弥散担忧
       覆盖 9 条 (默认收起)
       [k]eep / [e]dit / [d]rop / [v]iew-full ? > 
```

- **keep**: 保留 candidate 及其所有 edges
- **drop**: `graph.remove_node(c_001)` 级联删 outgoing edges
- **edit**: 改 name + description；node.source 升级 `"llm" → "user"`；coverage 不可改（Phase 5 加）
- **view-full**: 展开所有 coverage 的 mechanism_description，看完回到 `[k/e/d/v]` 提示

### 5.4 Step 3: 收尾

- 清 `state.insight_candidates`
- `state.meta.stage = "done"`
- 落盘
- 允许 0 keep（warn "未保留任何 insight，session 标为 done。可 explain new 重跑同问题"）

### 5.5 Ctrl-C 行为

中途 Ctrl-C **不落盘**（HITL 中状态半成品）。但因 stage=insight_pending 在 LLM 跑完后已经落过盘，重跑 `explain compress` 时检测到 stage 直接跳进 HITL 2，**不会重调 LLM**。

---

## 6. Schema 改动

### 6.1 `state.py`：Stage Literal

```python
Stage = Literal["bootstrap_pending", "insight_pending", "done"]
```

旧 session JSON 反序列化时 stage="bootstrap_pending" 兼容。

### 6.2 `nodes.py`：VariableNode 加 `source`

```python
class VariableNode(BaseModel):
    ...
    source: Literal["llm", "user"] = "llm"
```

- BootstrapEngine 出的 p_NNN：source="llm"
- HITL 1 add 的 phenomena：source="user"（不再用 `p_user_NNN` 前缀，统一 `p_NNN`，ID 顺延 LLM 最大 +1）
- Compression 出的 c_NNN：source="llm"
- HITL 2 edit 过的 c_NNN：source 升级 "llm" → "user"

旧 session 反序列化默认 "llm"（pydantic Field default）。

### 6.3 `graph.py`：加 mutation 方法

```python
def remove_node(self, node_id: str) -> None:
    """删 node + 所有 incident edges（incoming + outgoing）。raises if not found."""

def remove_edge(self, edge_id: str) -> None:
    """删单 edge。raises if not found."""
```

**Phase 4 mutation 语义**：
- ✅ append node / edge
- ✅ remove node + cascade incident edges（HITL 2 drop 用）
- ✅ remove edge（备用，Phase 4 实际不用）
- ❌ update existing node（HITL 2 edit 在 candidate 进 graph 前修改不算）
- ❌ update existing edge

### 6.4 `CognitiveState` 不加字段

- `attention_map`：推 Phase 5（scheduler 需要时再加）
- `reasoning_trace`：推 Phase 5（loop 需要时再加）

理由：Phase 4 无 loop / scheduler，加了等于占位空 dict，序列化进 JSON 误导。

### 6.5 `state.insight_candidates` 字段含义升级

- stage="bootstrap_pending"：空
- stage="insight_pending"：candidate id list，按 compression_gain 降序
- stage="done"：清空

类型仍 `list[str]`，不变。

---

## 7. Error Handling

### 7.1 新异常类

```python
# src/explain_engine/llm/errors.py
class LLMError(Exception):
    """网络 / API / 超时 / rate limit。"""

class SchemaValidationError(Exception):
    """LLM 输出不 fit Pydantic schema（retry 1 次后仍失败）。"""
```

### 7.2 Provider client 改造

`claude.py` / `openai.py` / `deepseek.py` 底层 SDK 异常 wrap：
- `anthropic.APIError` / `openai.OpenAIError` / `requests.RequestException` → `LLMError`
- pydantic `ValidationError` → `SchemaValidationError`

### 7.3 Phase 3 `_run_new` 回填（reviewer Q3）

把 Phase 3 `_run_new` 的 `except Exception` 一把抓拆成 `except LLMError` / `except SchemaValidationError` / `except OSError`。一次性把 reviewer Q3 解决。

### 7.4 Exit Code 表

| Code | 含义 |
|---|---|
| 0 | 成功 |
| 1 | LLM 调用失败（`LLMError`）|
| 2 | LLM 输出不合规（`SchemaValidationError`）|
| 3 | session 保存失败（`OSError`）|
| 4 | stage 不对（如 `done` 重跑 `explain compress`）|
| 130 | Ctrl-C |

### 7.5 Retry 策略

- 复用 Phase 3 tenacity 装饰器（3 次指数退避）
- `SchemaValidationError` 同 prompt retry 1 次，仍失败抛错（不混入 retry 3 次循环）

---

## 8. Testing

### 8.1 L1 Schema (4-6)

`test_schema_state_stage_literal.py`：
- 旧 stage 兼容（`"bootstrap_pending"` 反序列化）
- 新 stage 序列化 round-trip

`test_schema_nodes_source.py`：
- source 字段 default "llm"
- 旧 JSON 无 source 字段 → 默认 "llm"
- source="user" 序列化 round-trip

`test_schema_graph_remove.py`：
- `remove_node` 级联删 incident edges (in + out)
- `remove_node` raises if not found
- `remove_edge` 删单 edge
- `remove_edge` raises if not found

### 8.2 L1 Engines (12-15)

`test_engines_compression.py`：
- mock LLM 返 5 候选，验证 c_NNN id 顺序 + level=1 + epistemic="insight" + source="llm"
- LLM 返 6 候选 → 截断 5
- LLM 返 2 候选 → 接受（warn）
- candidate.coverage < 2 → 淘汰
- coverage.concrete_id 不存在 → retry 1 次后抛 `SchemaValidationError`
- 多候选 coverage 重叠 → 允许
- 调用 prompt 时正确传入 phenomena_table

`test_engines_evaluation.py`：
- representation_reduction = covered/total（纯结构，无 LLM）
- explanatory_preservation = mean(score)/5（mock LLM 返 1-5）
- 候选按 gain 降序排
- mock 返非 1-5 → retry 1 次后抛 `SchemaValidationError`
- 0 covered → gain 0
- 12/12 + mean(1) → gain = 0.2（空洞抽象兜底验证）

### 8.3 L1 Prompts (2-3)

`test_llm_prompts_compression_loader.py` / `test_llm_prompts_scoring_loader.py`：
- yaml 解析成功
- 必需 key 存在（system / user_template / 各 placeholder）

### 8.4 L2 HITL (6-8)

`test_hitl_cli_interactive_insights.py`（mock `Prompt.ask`）：
- 总览 table 渲染含 5 行 + 关键列
- keep 全部 → graph 不变 + stage=done
- drop 全部 → graph 0 abstract + stage=done + warn
- drop 1 → graph 少 1 abstract + 其 edges 级联删
- edit name+description → source 升级 "user"
- view-full → console 包含全部 mechanism

### 8.5 L3 CLI (5-7)

`test_cli_compress.py`（typer.testing.CliRunner + patch LLM）：
- full mock flow（compress + HITL keep all）→ session 落地 stage="done"
- session id 不存在 → exit 1
- stage="done" 重跑 → exit 4
- stage="insight_pending" 重跑 → 跳过 LLM 直接 HITL 2
- LLM 抛 `LLMError` → exit 1
- LLM 抛 `SchemaValidationError` → exit 2

### 8.6 L4 Integration (1, skip default)

`@pytest.mark.integration test_compress_real_llm`：
- 跑 s_e3fb6675（真 LLM），验证 3-5 候选生成 + 至少 1 候选 gain > 0.3 + HITL mock keep all + session 落地

### 8.7 总数

新增 ~35 个，total 96 + 35 ≈ 131。Phase 0-3 测试不破。

---

## 9. 文件结构

```
src/explain_engine/
├── engines/
│   ├── bootstrap.py
│   ├── compression.py            NEW
│   └── evaluation.py             NEW
├── hitl/
│   └── cli_interactive.py        ADD review_insights()
├── llm/
│   ├── errors.py                 NEW (LLMError, SchemaValidationError)
│   ├── client.py
│   ├── claude.py                 MODIFY (wrap exceptions)
│   ├── openai.py                 MODIFY
│   ├── deepseek.py               MODIFY
│   └── prompts/
│       ├── variable_extraction.yaml
│       ├── compression.yaml      NEW
│       └── scoring.yaml          NEW
├── schema/
│   ├── nodes.py                  MODIFY (source field)
│   ├── edges.py
│   ├── graph.py                  MODIFY (remove_node / remove_edge)
│   └── state.py                  MODIFY (Stage Literal)
└── cli.py                        ADD compress 命令 + 回填 new 异常分类

tests/
├── test_engines_compression.py            NEW
├── test_engines_evaluation.py             NEW
├── test_hitl_cli_interactive_insights.py  NEW
├── test_cli_compress.py                   NEW
├── test_schema_state_stage_literal.py     NEW
├── test_schema_nodes_source.py            NEW
├── test_schema_graph_remove.py            NEW
├── test_llm_prompts_compression_loader.py NEW
└── test_llm_prompts_scoring_loader.py     NEW
```

---

## 10. 验收标准

- [ ] `explain compress s_e3fb6675` 真实 LLM 跑通：3-5 候选生成，按 gain 降序，HITL 2 全 path
- [ ] **wow check (informal)**：候选里至少有一个名字接近 "长期不确定性" / "社会竞争结构" 这类高维隐藏变量，gain ≥ 0.5，coverage ≥ 7/12
- [ ] HITL 2 Ctrl-C 后重跑能从 `insight_pending` 跳过 LLM 直接审查
- [ ] L1+L2+L3 测试 ≥35 个 PASS
- [ ] Phase 0-3 96 测试不破
- [ ] ruff check 0 error
- [ ] Phase 3 `_run_new` 也用上 `LLMError` / `SchemaValidationError` 异常分类（reviewer Q3 回填）
- [ ] `compression_score()` 旧方法删掉或废弃（被 `compression_gain` 替代，tension #1 解掉）

---

## 11. 已知风险 / Open Questions

1. **LLM 出 5 个候选名字相似**（同义改写"长期不确定性"/"未来焦虑"/"经济悲观"）— prompt 强调"多角度互不冗余"，仍失败 → Phase 5 加 redundancy_penalty
2. **mechanism scoring 1-5 离散度低**（可能 4/4/4/5/4）— Phase 5 考虑 7 级 or 0-100
3. **DeepSeek 嵌套 schema 解析失败率未知** — Phase 4 不专门处理，smoke 验证后再决定 fallback
4. **cost**：每次 `explain compress` ≈ 36 次 LLM 调用 — Phase 5 加批量 scoring prompt
5. **HITL 2 edit 不能改 coverage** — 用户若强烈不同意某 1-2 条 mechanism 只能整 drop。Phase 5 加 fine-grained edit
6. **0 keep 兜底**：所有候选都被 drop → session done 但 graph 无 insight。Phase 5 加 "重生成更多候选" 路径
7. **多候选 coverage 重叠**：同一 concrete 被多个 abstract 解释，HITL 2 keep 多个时 graph 上同一 concrete 有多 incoming edge — 这是 feature，但未来 render 时要明确"多重解释" UI
8. **Phase 4 不删 `compression_score()` 旧方法的影响**：被 `compression_gain` 替代后是死代码，§10 验收要求清理

---

## 12. Phase 4 之后

Phase 5 brainstorm 启动条件：
- Phase 4 跑通 ≥1 真实 session（s_e3fb6675 + 至少 1 个新种子问题）
- 知道 HITL 2 实际体验如何
- 知道 cost / latency 数量级（36 次 LLM 调用是否可接受）
- 知道 mechanism scoring 离散度是否够

Phase 5 起点：
- `ExpansionEngine`（上溯到 driver、横向 amplifier）
- Scheduler（轮转 / attention-based）
- 完整 Reasoning Loop（while not should_stop）
- `CognitiveState.attention_map` / `reasoning_trace` 加字段
- `explain expand` / `explain run` 命令
- 多轮 Compression（同一 session 多次跑）
