# Cognitive Engine Phase 7 — Confidence + Forward Prediction + Reflection (Design)

> 顶层文档参考: [最终哲学以及技术实现相关设计.md](../../最终哲学以及技术实现相关设计.md) §4.5 / §4.6 / §8 / §11.3 / §11.5 / §12
> 上一 phase: [Phase 6 Design](2026-05-14-cognitive-engine-phase-6-design.md)
> 上一 phase acceptance: [Phase 6 Acceptance](2026-05-14-cognitive-engine-phase-6-acceptance.md)

**日期**: 2026-05-15
**分支**: `dev` (Phase 6 final HEAD `484928f` 之后)

---

## 0. TL;DR

Phase 6 acceptance 暴露 confidence signalization 的硬伤: Phase 0-5 流程把所有 edge confidence 设成 default placeholder (causes=0.6, manifests_as=0.7), 让 Phase 6 simulation 3 个不同质量的 session 出**完全一致**的 consistency_score (0.70/0.42), negative control failed (见 Phase 6 acceptance §7).

Phase 7 干 3 件事:

**Wave A — Confidence 信号化** (修 Phase 6 数据 issue)
让 Phase 4 evaluation 的 per-edge score 和 Phase 5 expansion 的 driver plausibility 写回 `edge.confidence`. Linear mapping `conf = score / 5`. 0 新 LLM.

**Wave B — Forward Prediction + Counterfactual (B3)** (用户面向新能力)
新增 `explain predict <sid> "<intervention 文本>"` 和 `explain counterfactual <sid> "<substitute 文本>"`. 自然语言 intervention, LLM parser 拆"已有变量 + 新概念", 复用 Phase 6 propagation. 顶层 §11.3 第一次落地.

**Wave C — Reflection Engine** (runtime loop 闭环)
新增 `ReflectionEngine.reflect()`. 1 round = K expand + 1 reflect (替 Phase 5 evaluate snapshot). 用 Phase 6 consistency / essentialness 决定 next action ∈ {continue / re-expand / prune / stop}. 顶层 §4.6 + §12 第一次落地.

**Wave D — Acceptance + 文档**
`explain rescore` 命令重评 existing 3 session edges, 重跑 Phase 6 check 验证区分度.

总: 11 task, 4 Wave, 线性依赖, +74 测试, 跟 Phase 5 (10 task) 同量级.

---

## 1. Scope

### 1.1 Phase 7 内

- **Wave A 地基**: confidence signalization
  - `evaluation.py` per-edge score 写回 manifests_as `edge.confidence`
  - `expansion.py` driver plausibility 写回 causes `edge.confidence`
  - Mapping: linear `conf = score / 5` (1-5 → 0.2-1.0)

- **Wave B 用户面向**: forward prediction + counterfactual (B3)
  - LLM intervention parser (拆 existing_refs + new_concepts)
  - `ForwardPredictionEngine.predict()`: 加 new node + predicted L0 + propagate
  - `CounterfactualEngine.substitute()`: remove + LLM-generated alt narrative
  - HITL: 用户审 predicted L0 (accept / reject / edit)
  - shared propagation utility (Phase 6 simulation + Wave B 三处共用)

- **Wave C runtime 闭环**: reflection engine
  - `ReflectionEngine.reflect(state)` 决策器 (4 action: continue / re-expand / prune / stop)
  - `PhaseScheduler` 改: 1 round = K expand + 1 reflect (替 evaluate)
  - `runtime/stop.py` 加 `reflection_signaled_stop`
  - `expansion.re_expand()` 绕过 frontier check 给已有 L1 加更多 driver
  - graph mutation: prune action 删 node + cascade edges

- **Wave D acceptance + 文档**
  - `explain rescore <sid>` 重评 existing session edges (acceptance fixture)
  - 重跑 Phase 6 check 3 session 验证 Wave A 区分度 ≥ 0.15
  - 真 LLM smoke: predict / counterfactual / run with reflection
  - acceptance evidence file + README 更新

### 1.2 推到 Phase 8+

- ❌ Theory Formation Engine (§13)
- ❌ Persistent World Model / cross-session 变量复用 (§5.3)
- ❌ Multi-Perspective Runtime (§10) — perspective_shift action
- ❌ Variable Lifecycle 持久化 (§6.2 Birth/Growth/Decay/Death)
- ❌ Embedding / semantic clustering (driver 去重)
- ❌ Web search / external grounding (见 Phase 6 design 附录 B)
- ❌ Batch scoring prompt (cost 优化)
- ❌ Reflection compress action (Phase 5 已决定 round 内不放 compress)

### 1.3 Phase 7 不动的 (跟 Phase 6 同处理)

- ❌ 不动 Phase 6 `_propagation.py` 算法 (只新 import)
- ❌ 不动 `simulation.py` API
- ❌ 不动 Phase 4 compression.py 主流程 (只动 evaluation 写回)
- ❌ 不动 schema/nodes.py / edges.py (字段不动)
- ❌ 不动 persistence (新字段走 Phase 5 last_gains 同 backward compatible 处理)

---

## 2. Architecture + 目录结构

### 2.1 Module 边界 (跟 Phase 6 同 module 哲学)

```
Phase 0-5: bootstrap → compress → evaluation → expansion → runtime
           造 graph (write)

Phase 6:   simulation
           自检 graph (read-only)

Phase 7:   prediction / counterfactual / intervention_parser  ── write (加 node)
           reflection                                          ── read + 决策
           rescore                                             ── 改 edge.confidence (write)
```

### 2.2 文件结构 (新 / 改 / 不动)

```
src/explain_engine/
├── engines/
│   ├── _propagation.py            ── 不动 (Phase 6, 复用)
│   ├── simulation.py              ── 不动 (Phase 6)
│   ├── bootstrap.py               ── 不动
│   ├── compression.py             ── 不动
│   ├── evaluation.py              ── 改: per-edge score → 写回 manifests_as edge.confidence
│   ├── expansion.py               ── 改: plausibility → 写回 causes edge.confidence
│   │                                 + 新增 re_expand() 绕过 frontier check
│   ├── intervention_parser.py     ── NEW (Wave B): parse(text) → ParsedIntervention
│   ├── prediction.py              ── NEW (Wave B): predict(state, text, llm) → PredictionReport
│   ├── counterfactual.py          ── NEW (Wave B): substitute(state, text, llm) → CounterfactualReport
│   └── reflection.py              ── NEW (Wave C): reflect(state) → (ReflectionAction, target_id)
│
├── runtime/
│   ├── runtime.py                 ── 改: 加 "reflect" action 分支 + prune/re-expand 执行
│   ├── scheduler.py               ── 改: 1 round = K expand + 1 reflect (替 evaluate)
│   └── stop.py                    ── 改: 加 "reflection_signaled_stop"
│
├── schema/
│   ├── state.py                   ── 改: Action 加 "reflect" / TraceEntry 加 reflection_action
│   │                                  / CognitiveState 加 last_reflection_change_tick
│   ├── graph.py                   ── 不动 (Phase 6 outgoing_edges 已加)
│   ├── nodes.py                   ── 不动
│   └── edges.py                   ── 不动
│
├── llm/
│   └── prompts/
│       ├── intervention_parser.yaml ── NEW
│       ├── prediction.yaml          ── NEW (forward predict + counterfactual substitute 共用)
│       ├── compression.yaml         ── 不动
│       ├── expansion.yaml           ── 不动
│       ├── scoring.yaml             ── 不动
│       └── variable_extraction.yaml ── 不动
│
├── hitl/
│   └── cli_interactive.py         ── 改: 加 review_predicted_l0 函数
│
├── persistence/                    ── 不动 (新 schema 字段走向后兼容路径)
│
├── cli.py                         ── 改: 加 predict / counterfactual / rescore
└── config.py                      ── 不动

tests/
├── test_engines_evaluation_writeback.py    ── NEW (Wave A, +5)
├── test_engines_expansion_writeback.py     ── NEW (Wave A, +5)
├── test_engines_intervention_parser.py     ── NEW (Wave B, +8)
├── test_engines_prediction.py              ── NEW (Wave B, +10)
├── test_engines_counterfactual.py          ── NEW (Wave B, +8)
├── test_engines_reflection.py              ── NEW (Wave C, +10)
├── test_engines_expansion_re_expand.py     ── NEW (Wave C, +5)
├── test_runtime_scheduler_reflect.py       ── NEW (Wave C, +5)
├── test_runtime_stop_reflection.py         ── NEW (Wave C, +3)
├── test_runtime_run_reflect.py             ── NEW (Wave C, +5)
├── test_cli_predict.py                     ── NEW (Wave B, +5)
├── test_cli_counterfactual.py              ── NEW (Wave B, +5)
└── test_cli_rescore.py                     ── NEW (Wave D, +5)
```

### 2.3 依赖图 (单向无环)

```
cli.py
   ├─→ engines/prediction.py
   │      ├─→ engines/intervention_parser.py
   │      ├─→ engines/_propagation.py    (Phase 6, 复用)
   │      └─→ schema/* (read+write)
   │
   ├─→ engines/counterfactual.py
   │      ├─→ engines/intervention_parser.py
   │      ├─→ engines/prediction.py        (复用 generation step)
   │      └─→ engines/_propagation.py    (Phase 6, 复用)
   │
   ├─→ engines/reflection.py
   │      └─→ engines/simulation.py        (Phase 6, 复用)
   │
   ├─→ engines/evaluation.py               (Wave A 改, 不新 import)
   ├─→ engines/expansion.py                (Wave A + C 改)
   │
   └─→ runtime/runtime.py
          ├─→ engines/expansion.py
          ├─→ engines/reflection.py
          ├─→ runtime/scheduler.py
          └─→ runtime/stop.py
```

无新循环依赖. `engines/reflection.py` 调 `engines/simulation.py` (Phase 6 已有), 跟 Phase 6 simulation 调 `_propagation.py` 同性质.

### 2.4 顶层目录对齐

顶层 §15 把 reflection / simulation / counterfactual 都放 `runtime/reflection/` `runtime/simulation/` `runtime/counterfactual/`. Phase 7 同 Phase 6 处理: 这些都是"algorithm on graph", 放 `engines/` 跟 expansion/compression 同级. `runtime/` 留给 main loop + scheduler + stop. Phase 8+ Theory Formation 若需要进 runtime loop 再考虑升级.

---

## 3. Schema 改动

### 3.1 改 `schema/state.py`

```python
# 改前
Action = Literal["expand", "compress", "evaluate"]

# 改后
Action = Literal["expand", "compress", "evaluate", "reflect"]    # 加 "reflect"
ReflectionAction = Literal["continue", "re-expand", "prune", "stop"]   # 新

@dataclass
class TraceEntry:
    tick: int
    action: Action
    target_node_id: str | None
    gain_delta: float
    llm_calls: int
    timestamp: str
    reflection_action: ReflectionAction | None = None   # 新, 默认 None

    def to_dict(self) -> dict:
        return {
            # ... existing ...
            "reflection_action": self.reflection_action,   # 新
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TraceEntry":
        return cls(
            # ... existing ...
            reflection_action=d.get("reflection_action"),   # 新, 缺字段 None
        )


@dataclass
class CognitiveState:
    # ... existing fields ...
    last_reflection_change_tick: int = 0   # 新

    def to_dict(self) -> dict:
        return {
            # ... existing ...
            "last_reflection_change_tick": self.last_reflection_change_tick,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CognitiveState":
        return cls(
            # ... existing ...
            last_reflection_change_tick=d.get("last_reflection_change_tick", 0),
        )
```

### 3.2 不动的 schema

| schema | 不动原因 |
|---|---|
| `VariableNode` (nodes.py) | Phase 6 不持久化 stability_score 已决定; Wave B 加 predicted L0 用 `epistemic="speculation"` 现有字段 |
| `RelationEdge` (edges.py) | Wave A 只动 `confidence` 字段值 (现有), 不加新字段 |
| `ExplanationGraph` (graph.py) | Phase 6 已加 outgoing_edges; Wave C prune 用现有 remove_node + cascade edges (已存在) |
| `SessionMeta` / `Stage` | 不加新 stage (predict/counterfactual 都不改 stage; reflect 进 converged 流程内, run 完后仍 converged) |

### 3.3 向后兼容

旧 session JSON (Phase 5/6 saved) 反序列化:
- 缺 `last_reflection_change_tick` → default 0
- TraceEntry 缺 `reflection_action` → None
- 旧 Action Literal 是 ["expand", "compress", "evaluate"], 新加 "reflect" 是扩展不破坏

3 个 existing 3 session (s_f3beb777, s_705f0435, s_7d491774) 加载没问题.

---

## 4. Wave A — Confidence 信号化

### 4.1 Decision summary

| 决策 | 锁定 |
|---|---|
| Mapping function | (m1) linear: `conf = score / 5` |
| Score 来源 | Phase 4 evaluation per-edge score (1-5); Phase 5 expansion per-driver plausibility (1-5) |
| 写回位置 | `RelationEdge.confidence` (现有字段) |
| 重评 existing session | Wave D `explain rescore` 命令做, Wave A 只改 code path |
| Floor | 不加. PROPAGATION_THRESHOLD=0.05 自然 floor; score=1 → conf 0.2 链 4 hop 自然衰减 |

### 4.2 改 `evaluation.py`

`score_all()` 现在算 compression_gain 用 `_score_edge` 返 int. 改成同时把 score/5 写回对应 edge.confidence.

```python
# evaluation.py 改 score_all (相关 diff)

async def score_all(state, llm) -> dict[str, float]:
    # ... existing init ...
    for cid in state.insight_candidates:
        # ... existing ...
        scores_by_edge: dict[str, int] = {}   # 新: 记录每条 edge 的 score
        for e in out_edges:
            score = await _score_edge(...)
            scores.append(score)
            scores_by_edge[e.id] = score      # 新
        # ... existing compression_gain 计算 ...

        # 新: 写回 edge.confidence
        for edge_id, score in scores_by_edge.items():
            state.graph.edges[edge_id].confidence = score / 5.0

    # ... existing 降序排 + last_gains ...
```

边界 case:
- `out_edges` 为空: 不进 loop, edge.confidence 保持原值. 等价于"没 score 不动".
- LLM retry 失败抛 SchemaValidationError: 不写回 edge.confidence, 整个 score_all 失败 (跟现在行为一致).

### 4.3 改 `expansion.py`

`expand_one_frontier()` 现在出 1-3 driver, 每个 driver 自评 plausibility (1-5), gain = mean(plausibility)/5. 改成同时把 plausibility/5 写回 driver 引出的 causes edge.confidence.

```python
# expansion.py 改 expand_one_frontier (相关 diff)

for d in drivers:
    # ... existing 加 node ...
    new_edge = RelationEdge(
        id=f"e_{next_edge_id:03d}",
        source_node=d_id,
        target_node=target_id,
        relation_type="causes",
        confidence=d.plausibility / 5.0,    # 改: 原来 hard-code 0.6
        mechanism_description=d.mechanism,
    )
    state.graph.add_edge(new_edge)
    # ...
```

边界 case:
- 0 driver: 不进 loop, 不写边. 跟现在行为一致.
- 同名 driver 复用 existing node: 复用 node 但**新建 edge with 新 plausibility** (不复用旧 edge). Phase 5 行为不变, 只是新 edge 的 conf 用 plausibility/5 而非 0.6.

### 4.4 跟 Phase 6 simulation 的交互

Phase 6 `_propagation.propagate()` 沿 `edge.confidence` 衰减. Wave A 改后:
- 高 plausibility (5 → conf 1.0) → propagation 不衰减
- 低 plausibility (1 → conf 0.2) → 单边 propagated act = 0.2, 4 hop 后 0.2^4 ≈ 0.0016 < THRESHOLD 0.05, 链自然断
- Phase 6 simulation 区分 hallucinated session (LLM 给低 score) vs clean session (LLM 给高 score)

### 4.5 测试

**test_engines_evaluation_writeback.py** (~5 tests):
- score=5 → edge.confidence=1.0
- score=3 → edge.confidence=0.6
- score=1 → edge.confidence=0.2
- multi-edge: 每条 edge 独立写
- LLM retry 失败时 edge.confidence 不变

**test_engines_expansion_writeback.py** (~5 tests):
- plausibility=5 → edge.confidence=1.0
- plausibility=3 → edge.confidence=0.6
- multi-driver: 每个 driver 引出的 edge confidence 独立
- 复用 existing driver node 时新 edge 用新 plausibility
- 0 driver 时不写边

---

## 5. Wave B — Forward Prediction + Counterfactual (B3)

### 5.1 Decision summary

| 决策 | 锁定 |
|---|---|
| Semantic | B3 pure: 自然语言 intervention, LLM parser 拆 existing_refs + new_concepts |
| Parser 输出 | `ParsedIntervention(existing_refs, new_concepts)`; new_concepts[i] 含 expected_level |
| expected_level 决定者 | (p1.a) parser 决定, 见全 graph context |
| new_concepts 上限 | N=2 |
| 不存在 variable id | retry 1 次仍失败 raise SchemaValidationError |
| Parser 返空 | raise ValueError("无法解析 intervention") |
| Predicted L0 epistemic | "speculation" (现有 Literal) |
| HITL | 必须 (用户审 predicted L0) |
| Counterfactual 形态 | substitute 自然语言 ("用 X 替代 Y"); 退化 case: 纯 remove ("如果删除 Y") |
| Shared propagation utility | Phase 6 simulation + Wave B 三处共用 (Wave B.4 抽 helper) |

### 5.2 InterventionParser (`engines/intervention_parser.py`)

#### 5.2.1 公开 API

```python
class NewConceptSpec(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    expected_level: Literal[1, 2]


class ParsedIntervention(BaseModel):
    existing_refs: list[str] = Field(default_factory=list)
    new_concepts: list[NewConceptSpec] = Field(default_factory=list, max_length=2)


async def parse(
    state: CognitiveState,
    intervention_text: str,
    llm: LLMClient,
) -> ParsedIntervention:
    """LLM-based intervention parser.

    Args:
        state: 提供 existing graph context.
        intervention_text: 自然语言 intervention 描述.
        llm: LLM client.

    Returns:
        ParsedIntervention(existing_refs=[id list], new_concepts=[spec list])

    Raises:
        SchemaValidationError: LLM 返不存在 variable id (retry 1 次仍失败)
        ValueError: parser 返空 (existing_refs=[] 且 new_concepts=[])
    """
```

#### 5.2.2 Prompt (`intervention_parser.yaml`)

```yaml
system: |
  你是 cognitive engine 的 intervention parser sub-agent。
  
  任务: 给定一段用户写的 intervention 描述 (自然语言), 把它拆成 2 部分:
  
  1. existing_refs: 描述中提到的、已存在于 graph 的 variable id 列表
     (如果用户用 graph variable 的 name 描述, 你需要 map 回 id)
  
  2. new_concepts: 描述中引入的、graph 中没有的全新概念列表
     (最多 2 个; 多了说明 intervention 该拆多次 predict)
  
  约束:
  - existing_refs 中的每个 id 必须真在 graph 已有节点列表里 (你看到的 context).
    不要编 id; 不确定就归 new_concepts.
  - new_concepts 中每个的 expected_level (1 或 2) 由你判断:
    level=1 = abstract/mid-layer (e.g. "经济压力"、"群体认同")
    level=2 = driver/上游 (e.g. "代际记忆传递"、"激励失衡")
    判断标准: 这个概念是否能进一步上溯 cause (是 → level=1)? 还是已是机制根源 (是 → level=2)?
  - 如果 intervention 跟 root_question 完全无关 (e.g. 用户写废话), existing_refs 和
    new_concepts 都返空; 调用方会 raise.

  输出 schema:
  {
    "existing_refs": ["d_002", ...],
    "new_concepts": [
      {"name": str, "description": str, "expected_level": 1 or 2},
      ...
    ]
  }

user_template: |
  根问题: {question}

  当前 graph 已有节点 (id: name — description, abstraction_level):
  {graph_nodes_table}

  用户 intervention 描述:
  {intervention_text}

  请拆: existing_refs + new_concepts (最多 2 个 new_concept).
```

#### 5.2.3 校验

| 情况 | 处理 |
|---|---|
| LLM 返 `existing_refs` 含不存在 id | retry 1 次仍失败 SchemaValidationError |
| LLM 返 `new_concepts.length > 2` | retry 1 次仍失败 (max_length 校验) |
| LLM 返 `expected_level` 不是 1/2 | retry 1 次仍失败 |
| LLM 返空 (两个 list 都空) | ValueError "无法解析 intervention" |
| graph 无节点 (空 graph) | parser 退化 → existing_refs=[], new_concepts 必有 (否则 raise) |

### 5.3 ForwardPredictionEngine (`engines/prediction.py`)

#### 5.3.1 公开 API

```python
@dataclass(frozen=True)
class PredictionReport:
    intervention_text: str
    parsed: ParsedIntervention
    new_node_ids: list[str]           # parser 标记新增 + actually inserted
    predicted_L0_ids: list[str]       # LLM 生成的 predicted phenomena
    activated_existing_L0: list[str]  # propagate 后激活的现有 L0
    propagation_acts: dict[str, float]
    decay_trace: list[DecayStep]


class PredictedL0(BaseModel):
    name: str
    description: str
    mechanism: str   # 为什么 intervention 会 manifest 出这个 L0


class PredictionOutput(BaseModel):
    predicted_L0: list[PredictedL0] = Field(min_length=1, max_length=5)


async def predict(
    state: CognitiveState,
    intervention_text: str,
    llm: LLMClient,
) -> PredictionReport:
    """Forward prediction: intervention → predicted effects.

    流程:
      1. parser(intervention_text) → ParsedIntervention(existing_refs, new_concepts)
      2. 加 new_concepts 进 graph (level by spec, epistemic=speculation, source=llm)
      3. LLM call (prediction.yaml): given new_intervention nodes + existing_refs + graph context,
         out predicted L0 (1-5, level=0, epistemic=speculation)
      4. 加 predicted L0 进 graph + manifests_as edges from new_concepts (conf=plausibility/5)
      5. propagate(graph, sources=set(new_node_ids) | set(existing_refs))
      6. 返 PredictionReport

    副作用:
      - state.graph 新增 [0, N=2] driver/abstract nodes + 1-5 L0 nodes + edges
      - 不 commit 进 last_gains (predict 不是 reasoning loop 一部分)
      - 不动 stage / last_gain_tick / reasoning_trace

    Raises:
        SchemaValidationError: parser 或 generation LLM 输出不合规
        ValueError: parser 返空
    """
```

#### 5.3.2 Prompt (`prediction.yaml`, 跟 counterfactual 共用)

```yaml
system: |
  你是 cognitive engine 的 forward prediction sub-agent。
  
  任务: 给定一个 intervention (已 parse 出 [intervention nodes + existing nodes]),
  预测它会 manifest 出哪些新的 concrete L0 现象.
  
  约束:
  - 输出 1-5 个 predicted L0, 每个含 name / description / mechanism.
  - mechanism 必须说明 "为什么 intervention → 这个 L0".
  - 不要预测已有 L0 (graph 里已经有的 concrete). 算法会自动合并 existing L0.
  - epistemic 设 speculation (调用方写, 你不写).
  - predicted L0 是 forward (intervention 会带来的新现象), 不是 backward (intervention 是什么的结果).
  
  输出 schema:
  {
    "predicted_L0": [
      {"name": str, "description": str, "mechanism": str},
      ...
    ]
  }

user_template: |
  根问题: {question}

  Intervention (要预测它的下游):
  {intervention_summary}

  当前 graph 已有 concrete L0 (避免重复预测):
  {existing_L0_table}

  请预测 1-5 个 forward L0 现象.
```

#### 5.3.3 HITL (`hitl/cli_interactive.py` 新增 `review_predicted_l0`)

跟 Phase 3 `review_phenomena` 同结构:
- 用 Rich 渲染 predicted L0 table
- 用户对每个 L0 选 [a]ccept / [r]eject / [e]dit
- reject 的 L0 从 graph 删除
- edit 的 L0 改 name/description
- accept 保留原样

如果用户 reject 全部, 仍保留 new_concepts nodes (intervention 节点本身), 因为 propagate 仍 valid.

#### 5.3.4 边界 case

| Case | 行为 |
|---|---|
| parser 返 existing_refs=[d_002], new_concepts=[] | 退化成 B1: 直接 propagate({d_002}), 不调 prediction LLM, 不出 predicted L0 (因为 intervention 完全已知 → 顶层"forward rollout existing"语义). HITL 跳过. |
| parser 返 existing_refs=[], new_concepts=[新] | 主路径: 加新 node + LLM 出 predicted L0 + propagate |
| parser 返混合 | 主路径: 加新 + propagate({new + existing}) |
| LLM 出 0 predicted L0 | min_length=1 校验失败, retry. 仍失败 SchemaValidationError. |

### 5.4 CounterfactualEngine (`engines/counterfactual.py`)

#### 5.4.1 公开 API

```python
@dataclass(frozen=True)
class CounterfactualReport:
    intervention_text: str
    parsed: ParsedIntervention
    removed_node_ids: list[str]           # 从 graph 删的 (= existing_refs)
    added_node_ids: list[str]             # substitute 加的 new nodes
    added_predicted_L0_ids: list[str]     # substitute 生成的 alt predicted L0
    baseline_acts: dict[str, float]       # 原 graph propagate
    counterfactual_acts: dict[str, float] # 替换后 propagate
    activation_diff: dict[str, float]     # baseline - counterfactual per L0
    alt_narrative: str | None             # LLM 生成 (substitute case 才有)


async def substitute(
    state: CognitiveState,
    intervention_text: str,
    llm: LLMClient,
) -> CounterfactualReport:
    """Counterfactual: remove + (optional) substitute.

    流程:
      1. parser(intervention_text) → ParsedIntervention
         existing_refs 是 "要 remove 的"
         new_concepts 是 "要 substitute in 的" (可空)
      2. baseline_acts = propagate(original_graph, all_L1_L2)
      3. 复制 graph (counterfactual_graph) — 不动 state.graph
      4. 从 counterfactual_graph 删 existing_refs + cascade edges
      5. 如果 new_concepts 非空: 加 new nodes + LLM 出 alt predicted L0 + edges
      6. counterfactual_acts = propagate(counterfactual_graph, all_L1_L2 - removed + added)
      7. activation_diff = baseline - counterfactual per L0
      8. 如果 substitute: LLM call 出 alt_narrative ("用 X 替代 Y 后, system trajectory 是 ...")
      9. 返 CounterfactualReport

    注意: 副作用 = 0 (不动 state.graph). Counterfactual 是 what-if, 不持久化.
    LLM call 数: 1 parser + (1 generation if substitute) + (1 alt narrative if substitute) = 1-3.

    Raises:
        SchemaValidationError, ValueError 同 predict.
    """
```

#### 5.4.2 副作用边界 (重要)

`predict()` 改 graph (predict 是 "下次 reasoning 接着用" 的探索操作).

`substitute()` **不改 graph** (counterfactual 是纯 what-if). 用 graph 深拷贝跑 propagate, 拷贝丢弃.

这是 Phase 7 内部最重要的语义不对称: forward predict 写, counterfactual 读. 测试用例必须 cover.

#### 5.4.3 alt narrative prompt

Substitute case 加一段 LLM call 生 narrative:

```yaml
# counterfactual_narrative.yaml (NEW)
system: |
  你是 cognitive engine 的 counterfactual narrative sub-agent.
  
  任务: 给定一个 counterfactual scenario (removed 哪些 driver + substituted 哪些), 
  以及 activation_diff (每个 L0 in/out 程度变化), 写一段 alt trajectory narrative.
  
  约束:
  - 80-200 字, 不超过 3 段.
  - 必须基于 activation_diff 数据 (不要凭空 speculate).
  - 不要 hedging ("可能"、"也许"). 写得 confident.

user_template: |
  根问题: {question}
  Removed drivers: {removed_summary}
  Substituted: {substituted_summary}
  Activation diff (baseline - counterfactual per L0):
  {diff_table}

  请写 alt trajectory narrative.
```

Pure remove case (no substitute) → 不调 narrative LLM, alt_narrative=None.

### 5.5 Shared propagation utility (Wave B.4)

抽 Phase 6 simulation.py + Wave B prediction/counterfactual 三处共用的代码:

```python
# engines/_propagation.py 加 (跟现有 propagate 同 module)

def propagate_from_sources_with_diff(
    graph: ExplanationGraph,
    sources: set[str],
    baseline_acts: dict[str, float],
) -> tuple[dict[str, float], dict[str, float], list[DecayStep]]:
    """Propagate + 算 baseline diff. 给 counterfactual 用.
    
    Returns: (new_acts, diff, trace)
    """
    new_acts, trace = propagate(graph, sources)
    diff = {nid: baseline_acts.get(nid, 0.0) - new_acts.get(nid, 0.0)
            for nid in set(baseline_acts) | set(new_acts)}
    return new_acts, diff, trace


def get_all_L0(graph: ExplanationGraph) -> set[str]:
    """跟 simulation._get_all_L0 同, 抽 public."""
    return {nid for nid, n in graph.nodes.items() if n.abstraction_level == 0}


def get_all_L1_L2(graph: ExplanationGraph) -> set[str]:
    """跟 simulation._get_all_L1_L2 同, 抽 public."""
    return {nid for nid, n in graph.nodes.items() if n.abstraction_level >= 1}
```

Simulation.py 改 import:
```python
from explain_engine.engines._propagation import (
    propagate, get_all_L0, get_all_L1_L2, WEAK_CHAIN_THRESHOLD, DecayStep,
)
# 删 _get_all_L0 / _get_all_L1_L2 私有 helper
```

### 5.6 CLI 集成

```python
@app.command()
def predict(
    session_id: str = typer.Argument(...),
    intervention_text: str = typer.Argument(...),
) -> None:
    """Phase 7 forward prediction.
    
    Examples:
        explain predict s_f3beb777 "现代媒体放大效应"
        explain predict s_f3beb777 "教义不可妥协性强化"
    """


@app.command()
def counterfactual(
    session_id: str = typer.Argument(...),
    intervention_text: str = typer.Argument(...),
) -> None:
    """Phase 7 counterfactual substitution.
    
    Examples:
        explain counterfactual s_f3beb777 "用经济激励替代教义不可妥协性"
        explain counterfactual s_f3beb777 "如果删除集体身份维系压力"
    """
```

Exit codes:
- 0: 正常
- 1: session not found / LLM failed
- 2: parser ValueError (无法解析) / SchemaValidationError
- 4: stage 不对 (必须 stage=converged or done)

### 5.7 测试

**test_engines_intervention_parser.py** (~8 tests):
- parser 出全 existing_refs (intervention 完全已知)
- parser 出全 new_concepts (intervention 完全新)
- parser 出混合
- 不存在 variable id → SchemaValidationError after retry
- new_concepts.length > 2 → fail
- expected_level 不是 1/2 → fail
- 返空 → ValueError
- LLM mock: 验证 prompt 含 graph_nodes_table

**test_engines_prediction.py** (~10 tests):
- 主路径: 加 new nodes + predicted L0 + propagate
- B1 退化 case: existing_refs only, 跳过 generation
- predicted L0 epistemic=speculation
- predicted L0 写 manifests_as edge with conf=plausibility/5 (Wave A 同 mapping)
- LLM gen min_length=1 校验
- HITL accept/reject/edit predicted L0
- 副作用: state.graph 真的改了 (predict 改)
- propagate from 多 source (混合 case)

**test_engines_counterfactual.py** (~8 tests):
- 主路径: substitute (remove + add)
- 退化 case: pure remove (no new_concepts)
- 副作用 = 0: state.graph 不改
- activation_diff 计算正确
- alt_narrative 仅 substitute case 有
- LLM mock: 验证 narrative prompt 含 diff_table

**test_cli_predict.py / test_cli_counterfactual.py** (~5 + 5 tests):
- typer CliRunner + tmp_path + monkeypatch SESSIONS_DIR + LLM mock
- 正常 case / parser fail / stage 不对 / session not found

---

## 6. Wave C — Reflection Engine

### 6.1 Decision summary

| 决策 | 锁定 |
|---|---|
| Action set | (β) 4 action: continue / re-expand / prune / stop |
| Trigger | (t1) 1 round = K expand + 1 reflect (替 Phase 5 evaluate snapshot) |
| Score source | Phase 6 consistency (低 → re-expand) + essentialness (低 → prune) |
| 优先级 | re-expand > prune > stop > continue |
| 阈值 (Wave D tune) | LOW_CONSISTENCY=0.5, LOW_ESSENTIALNESS=0.05, CONSISTENCY_STALE_TICKS=3 |
| re-expand 绕过 frontier check | 新增 `expansion.re_expand()` |
| prune action 副作用 | graph.remove_node + cascade edges |
| reflect() LLM call | 0 (用 Phase 6 simulation) |

### 6.2 ReflectionEngine (`engines/reflection.py`)

#### 6.2.1 常量

```python
LOW_CONSISTENCY_THRESHOLD: float = 0.5
"""L1 consistency_score < 阈值 → re-expand 触发."""

LOW_ESSENTIALNESS_THRESHOLD: float = 0.05
"""L2 essentialness_score < 阈值 → prune 触发."""

CONSISTENCY_STALE_TICKS: int = 3
"""state.tick - state.last_reflection_change_tick >= 此值 → stop."""
```

#### 6.2.2 公开 API

```python
def reflect(state: CognitiveState) -> tuple[ReflectionAction, str | None]:
    """Reflection decision. 0 LLM call (用 Phase 6 simulation).
    
    决策优先级 (顶层 §12.2):
      1. re-expand: 低 consistency L1 abstract (graph 长得不健康, 补 driver)
      2. prune: ≈0 essentialness L2 driver (saturated, 删了不损)
      3. stop: consistency 3 tick 无变化 (converged)
      4. continue: 其他
    
    Returns: (action, target_id)
      - re-expand → target_id = lowest-consistency L1 id
      - prune → target_id = lowest-essentialness L2 id
      - stop → target_id = None
      - continue → target_id = None
    """
    if not state.graph.nodes:
        return ("continue", None)
    
    L1_L2 = get_all_L1_L2(state.graph)
    if not L1_L2:
        return ("continue", None)
    
    reports = check_consistency_batch(state)
    
    # 1. re-expand 低 consistency L1
    low_c = sorted(
        [r for r in reports
         if state.graph.nodes[r.target_id].abstraction_level == 1
         and r.consistency_score < LOW_CONSISTENCY_THRESHOLD],
        key=lambda r: r.consistency_score,
    )
    if low_c:
        return ("re-expand", low_c[0].target_id)
    
    # 2. prune 低 essentialness L2
    low_e = sorted(
        [r for r in reports
         if state.graph.nodes[r.target_id].abstraction_level == 2
         and r.essentialness_score < LOW_ESSENTIALNESS_THRESHOLD],
        key=lambda r: r.essentialness_score,
    )
    if low_e:
        return ("prune", low_e[0].target_id)
    
    # 3. stale 检测
    if state.tick - state.last_reflection_change_tick >= CONSISTENCY_STALE_TICKS:
        return ("stop", None)
    
    return ("continue", None)
```

#### 6.2.3 决策语义解释

| Action | 触发条件 | 副作用 | 跟顶层 §12.2 对应 |
|---|---|---|---|
| re-expand | 存在 L1 with `consistency < 0.5` | 调 `expansion.re_expand(state, target, llm)`, 加更多 driver | "continue expansion" 但 targeted |
| prune | 存在 L2 with `essentialness < 0.05` | `graph.remove_node(target) + cascade edges` | "prune" |
| stop | 3 tick 无 graph change | runtime loop terminate, stage→converged | "stop reasoning" |
| continue | 否则 | next tick 继续 expand 主流程 | "continue expansion" (隐式) |

注: 顶层 §12.2 列了 "compress" 和 "perspective shift", Phase 7 不实现:
- compress: Phase 5 已决定 round 内不放 compress (concrete pool 未变, 重跑出同 candidate)
- perspective shift: 需要 multi-perspective runtime, 推 Phase 9+

### 6.3 改 `expansion.py`: 新增 `re_expand()`

`expand_one_frontier()` 要求 target 无 incoming causes (frontier 条件), 限制于 first-pass expansion. Reflection 的 re-expand 需要对 already-driver-covered L1 加更多 driver.

```python
async def re_expand(
    state: CognitiveState,
    target_id: str,
    llm: LLMClient,
    max_drivers: int = 2,   # 默认 2, 比 first-pass 少 (避免膨胀)
) -> tuple[list[str], float]:
    """Re-expansion: 给 already-driver-covered L1 加更多 driver.
    
    跟 expand_one_frontier 99% 同, 区别:
      - 不 check "no incoming causes" (允许已 expanded)
      - max_drivers 默认 2 (而非 3)
      - prompt 中 existing_drivers 包括 already-incoming 的 d_NNN (LLM 避免重复)
    
    抽 helper: 把 expand_one_frontier 拆 _do_expansion(state, target, llm, max_drivers, allow_re_expand)
    """
```

实现: 拆 `_do_expansion()` helper, expand_one_frontier 和 re_expand 都调.

### 6.4 改 `runtime/scheduler.py`

```python
# 改前
class PhaseScheduler:
    K: int = 4
    def pick(self, state) -> Literal["expand", "evaluate"]:
        if state.tick % (self.K + 1) < self.K:
            return "expand"
        return "evaluate"

# 改后
class PhaseScheduler:
    K: int = 4
    def pick(self, state) -> Literal["expand", "reflect"]:
        if state.tick % (self.K + 1) < self.K:
            return "expand"
        return "reflect"     # 替换 evaluate
```

1 round = 4 expand + 1 reflect = 5 tick.

向后兼容: 已有 session 的 reasoning_trace 含 action="evaluate" 没问题 (Literal 包含 evaluate), 只是新 reasoning loop 不再产 evaluate.

### 6.5 改 `runtime/runtime.py`

新增 "reflect" action 分支:

```python
async def run(state, llm, budget, ...):
    # ... existing init ...
    while True:
        stop, reason = stop_mod.should_stop(state)
        if stop:
            return reason
        
        action = sched.pick(state)
        target_id = None
        gain_delta = 0.0
        llm_calls = 0
        reflection_action = None
        
        if action == "expand":
            # ... existing Phase 5 expand 逻辑 ...
        elif action == "reflect":
            reflection_action, target = reflect(state)
            if reflection_action == "re-expand" and target:
                new_ids, gain_delta = await expansion.re_expand(state, target, llm)
                llm_calls = 1
                target_id = target
                state.last_reflection_change_tick = state.tick
            elif reflection_action == "prune" and target:
                state.graph.remove_node(target)   # cascade edges
                target_id = target
                state.last_reflection_change_tick = state.tick
            elif reflection_action == "stop":
                # signal stop, will trigger reflection_signaled_stop in next loop
                # 不 break, 让 should_stop() 处理 (跟 budget 同节奏)
                state.last_reflection_change_tick = state.tick - CONSISTENCY_STALE_TICKS - 1
                # 上述触发 stale 信号, runtime/stop.py 下个 tick 抓到
            # continue: 无副作用
        
        state.reasoning_trace.append(TraceEntry(
            tick=state.tick,
            action=action,
            target_node_id=target_id,
            gain_delta=gain_delta,
            llm_calls=llm_calls,
            timestamp=datetime.now(UTC).isoformat(),
            reflection_action=reflection_action,  # 新
        ))
        
        if gain_delta >= GAIN_THRESHOLD:
            state.last_gain_tick = state.tick
        
        state.tick += 1
        state.budget_remaining -= 1
        if on_tick: on_tick(state)
```

### 6.6 改 `runtime/stop.py`

```python
# 新 stop signal
def should_stop(state) -> tuple[bool, str | None]:
    if state.budget_remaining <= 0:
        return True, "budget_exhausted"
    if state.tick - state.last_gain_tick >= 3:
        return True, "no_gain_for_3_ticks"
    if state.tick - state.last_reflection_change_tick >= CONSISTENCY_STALE_TICKS + 1:
        # +1 因为 reflect 自己也 +1 tick
        return True, "reflection_signaled_stop"
    if not state.graph.frontier_nodes() and not _has_low_consistency_L1(state):
        # frontier 空且无 L1 可 re-expand
        return True, "no_frontier_remaining"
    return False, None


def _has_low_consistency_L1(state) -> bool:
    """Phase 7: frontier 空时仍可能有低 consistency L1 (re-expand 候选)."""
    if not get_all_L1_L2(state.graph):
        return False
    reports = check_consistency_batch(state)
    return any(
        r.consistency_score < LOW_CONSISTENCY_THRESHOLD
        and state.graph.nodes[r.target_id].abstraction_level == 1
        for r in reports
    )
```

### 6.7 测试

**test_engines_reflection.py** (~10 tests):
- empty graph → continue
- 无 L1/L2 → continue
- 1 low consistency L1 → re-expand 返该 id
- 多 low consistency L1 → re-expand 返 lowest
- 低 essentialness L2 但无 low consistency → prune 返该 id
- 全高 + last_change_tick 远 → stop
- 全高 + last_change_tick 近 → continue
- 优先级: low consistency L1 + low essentialness L2 同时存在 → re-expand 优先
- 阈值边界: consistency = 0.5 exactly → 不触发 (严格 <)
- 0 LLM call 验证

**test_engines_expansion_re_expand.py** (~5 tests):
- target 有 incoming causes (frontier 拒绝) → re_expand 接受
- target 不在 graph → ValueError
- target level=0 → ValueError (re-expand 只对 L1)
- prompt 含 existing drivers (避免 LLM 重复出)
- max_drivers=2 截断

**test_runtime_scheduler_reflect.py** (~5 tests):
- K=4, tick 0..3 → expand, tick 4 → reflect, tick 5..8 → expand, tick 9 → reflect
- 跟 Phase 5 evaluate 行为对偶

**test_runtime_stop_reflection.py** (~3 tests):
- reflection_signaled_stop 触发 (last_reflection_change_tick stale)
- no_frontier_remaining 现需要检查 L1 consistency (frontier 空但有 low consistency L1 → 不 stop)
- 优先级: budget < no_gain < reflection_stop < no_frontier

**test_runtime_run_reflect.py** (~5 tests):
- run with K=2 budget=8 → trace 应含 expand×4 + reflect×4 + stop
- reflect re-expand: graph 真的加 driver, last_reflection_change_tick 更新
- reflect prune: graph 真的删 driver, last_reflection_change_tick 更新
- reflect stop: 触发 reflection_signaled_stop
- on_tick callback 同 Phase 5 行为

---

## 7. Wave D — Acceptance + 文档

### 7.1 Decision summary

| 决策 | 锁定 |
|---|---|
| Rescore 命令 | 新增 `explain rescore <sid>` |
| Rescore 改对象 | manifests_as edges (用 scoring.yaml) + causes edges (用 expansion plausibility 评估) |
| Rescore 副作用 | 改 state.graph edges in-place, save session |
| Acceptance session | 现有 3 session (s_f3beb777, s_705f0435, s_7d491774) |
| Acceptance 区分度阈值 | consistency 差 ≥ 0.15 (s_7d491774 < s_f3beb777) |

### 7.2 `explain rescore <sid>` 命令

```python
@app.command()
def rescore(
    session_id: str = typer.Argument(...),
) -> None:
    """Phase 7 Wave D: 重评 existing session 的 edge confidence.
    
    Wave A 改了 evaluation/expansion 的 code path, 但 existing session 的 edges 还
    是 Phase 0-5 default placeholder. 跑 rescore 把 default conf 替换成 LLM-evaluated.
    
    LLM cost: ~17 manifests_as edges + ~8 causes edges = ~25 LLM call per session.
    
    Examples:
        explain rescore s_f3beb777
    """
```

#### 7.2.1 实现 (`engines/rescore.py` 或直接放 cli.py)

```python
async def rescore_session(state: CognitiveState, llm: LLMClient) -> dict[str, float]:
    """Rescore all manifests_as + causes edges.
    
    Returns: dict[edge_id, new_confidence] (供 CLI render diff 表)
    """
    new_confidences = {}
    
    # 1. manifests_as: 用 scoring.yaml (跟 Phase 4 evaluation._score_edge 同 prompt)
    for edge in state.graph.edges.values():
        if edge.relation_type == "manifests_as":
            score = await _rescore_manifests_edge(state, edge, llm)
            new_conf = score / 5.0
            edge.confidence = new_conf
            new_confidences[edge.id] = new_conf
    
    # 2. causes: 用新 prompt rescoring_causes.yaml (driver→target 1-5 plausibility)
    for edge in state.graph.edges.values():
        if edge.relation_type == "causes":
            plausibility = await _rescore_causes_edge(state, edge, llm)
            new_conf = plausibility / 5.0
            edge.confidence = new_conf
            new_confidences[edge.id] = new_conf
    
    return new_confidences
```

新 prompt `rescoring_causes.yaml` 跟 expansion.yaml 的 plausibility 自评 prompt 同结构, 但是给定一个 driver + target 评 1-5 (不生新 driver).

实现简化方案: Wave D.1 直接复用 scoring.yaml 对 causes edges 也评分 (改 user_template), 不新加 yaml. 跟 Phase 6 design "一切从简" 一致.

### 7.3 Acceptance plan

#### 7.3.1 步骤

```bash
# Step 1: 重评 3 session edges (Wave A 生效)
.venv/bin/python -m explain_engine.cli rescore s_f3beb777
.venv/bin/python -m explain_engine.cli rescore s_705f0435
.venv/bin/python -m explain_engine.cli rescore s_7d491774

# Step 2: 重跑 Phase 6 check
.venv/bin/python -m explain_engine.cli check s_f3beb777
.venv/bin/python -m explain_engine.cli check s_705f0435
.venv/bin/python -m explain_engine.cli check s_7d491774

# Step 3: 跑 Wave B 真 LLM
.venv/bin/python -m explain_engine.cli predict s_f3beb777 "现代媒体放大效应"
.venv/bin/python -m explain_engine.cli counterfactual s_f3beb777 "用经济激励替代教义不可妥协性"

# Step 4: 跑 Wave C run with reflection (新 session 或 reset 已有)
.venv/bin/python -m explain_engine.cli new "为什么大公司的会议总是低效"
# HITL 1 → compress → HITL 2 → run
```

#### 7.3.2 Acceptance checklist

- [ ] **Wave A 区分度**: s_7d491774 avg consistency < s_f3beb777 avg consistency, 差 ≥ 0.15
- [ ] **Wave A essentialness 区分度**: 同 session 跨 target 跨度 ≥ 0.1 (Phase 6 失败的 ≥ 0.2 太严, 放宽到 0.1)
- [ ] **Wave B predict**: `explain predict s_f3beb777 "现代媒体放大效应"` 跑通 + HITL accept ≥ 1 predicted L0
- [ ] **Wave B counterfactual**: `explain counterfactual s_f3beb777 "用经济激励替代教义不可妥协性"` 跑通, activation_diff 非零, alt_narrative 非空
- [ ] **Wave C reflect 触发**: 跑新 session `explain run --budget 15`, reasoning_trace 含至少 1 reflection_action ≠ "continue"
- [ ] **Wave C re-expand 有效**: 在 s_f3beb777 重跑 run 后 L0 orphan rate 从 50% 降到 < 30%
- [ ] **Wave C reflection_signaled_stop**: 至少 1 个 session 触发 reflection_signaled_stop (而非 budget_exhausted)
- [ ] **测试**: 350+ PASS
- [ ] **ruff check**: 0 errors
- [ ] **向后兼容**: 3 个 existing session 加载不破

### 7.4 Acceptance evidence file

`docs/plans/2026-05-15-cognitive-engine-phase-7-acceptance.md`:
- 跑法 + LLM provider 信息
- Wave A 重跑 Phase 6 check 数据对比 (前后 consistency_score 表)
- Wave B predict / counterfactual 输出样例
- Wave C reasoning_trace 含 reflection_action 样例
- Acceptance checklist 打勾
- Tune 决策 (常量是否需调)
- Phase 8 起点

### 7.5 README 更新

加 Phase 7 边界说明:
```markdown
## Phase 7 (2026-05-15) — Confidence + Forward Prediction + Reflection

新命令:
- `explain predict <sid> "<intervention>"` — 自然语言 forward prediction
- `explain counterfactual <sid> "<substitute>"` — counterfactual 替换 / 删除
- `explain rescore <sid>` — 重评 edge confidence (Wave A acceptance fixture)

`explain run` 现含 Reflection: loop 内动态决定 re-expand / prune / stop.

边界 (跟 Phase 6 一致):
- 系统适合: 历史 / 常识 / 结构性 why-questions
- 系统不适合: 实时分析 / 强时效议题 / 依赖具体新近数据
- Phase 7 forward prediction 适合 structural-mechanism 议题 (e.g. "如果加入 X 因素 / 移除 Y 因素"); 不适合时事预测.
```

---

## 8. 顶层文档对齐 (附录 A)

| 顶层 § | Phase 7 对齐方式 |
|---|---|
| §3.2 Variable 字段 | 不持久化 stability_score / explanatory_power, 跟 Phase 6 同处理 (推 Phase 8+ Variable Lifecycle) |
| §4.5 Simulation Operator | Wave B forward predict + counterfactual 第一次落地, propagate 算法复用 Phase 6 |
| §4.6 Reflection Operator | Wave C 第一次落地, reflect() 跟 §12.2 4-action set 严格对应 |
| §6.2 Variable Lifecycle | 不持久化, Phase 7 prune action 是 in-runtime 一次性删除, 不写 Death status |
| §8.1 Explanation 必须能 rollout | Phase 7 forward predict 直接是"explanation rollout 出 predicted 现象", 是 §8.1 的最直接落地 |
| §8.3 Counterfactual Thinking | Wave B counterfactual substitute 直接是"如果某变量不存在/被替代" |
| §11.3 Counterfactual Engine | substitute() 严格对应 `Remove(variable) → rerun rollout` + LLM 生 narrative |
| §11.4 MAX_DEPTH / MAX_ACTIVE | Phase 6 默认值不调, Phase 7 复用 |
| §11.5 Stability Regularization | Wave A linear mapping 让 score=1 (投机) → conf 0.2, propagation 4 hop 自然衰减 — 严格符合 |
| §12.1 Reflection Input | reflect(state) 输入是 CurrentState (full state), 不显式分拆 EnergyLandscape / AttentionDynamics |
| §12.2 Reflection Actions | continue / re-expand / prune / stop 严格对应; compress 和 perspective_shift 推 Phase 8+ |
| §13.1 Theory Discovery | Phase 7 不实现 theory formation; consistency_score 是 theory stability 的前奏 (Phase 8 复用) |
| §14.1 Cognitive Energy | 不显式 minimize energy 函数; reflection 用 consistency/essentialness 双信号近似 |

---

## 9. Brainstorm 关键决策 (附录 B)

| 决策点 | 选择 | 理由 |
|---|---|---|
| Phase 7 scope | A+B+C (3 Wave + acceptance) | A 单做退化成 Phase 6 hotfix; B/C 共享 A 的 confidence 修复; D 推 Phase 8+ |
| Wave 排序 | A → B → C → D 线性 | A 是 B/C 共同基础; B 不动 runtime loop; C 改 runtime loop |
| Forward predict semantic | B3 pure (自然语言 + parser) | 顶层 §11.3 对齐; 用户不该被迫学 graph schema; B3 比 B2 多 1 LLM call 但避免 graph 膨胀 |
| Parser 输出 expected_level | parser 决定 | parser 见全 graph context, judge 比 generation 准 |
| new_concepts 上限 | N=2 | 用户单 intervention 通常 1-2 concept; 多了说明 intervention 该拆 |
| Parser 返空 | raise ValueError | 让用户看 error 重试; 而非静默退化 |
| Predicted L0 epistemic | "speculation" (现有 Literal) | 跟 Phase 5 expansion driver 同 epistemic, 跟现有 schema 兼容 |
| Counterfactual 副作用 | 0 (不改 graph) | counterfactual 是 what-if, 改 graph 等于持久化 alternate reality |
| Reflection action set | (β) 4 action | (α) 太弱不能修 low consistency; (γ) compress 在 Phase 5 已决定不放, perspective 推 Phase 9+ |
| Reflection trigger | (t1) per K expand | deterministic; 跟 Phase 5 scheduler 同 structure |
| Reflection 优先级 | re-expand > prune > stop > continue | prune 优先会让 low consistency L1 永远没机会修 |
| Reflection LLM call | 0 (reflect() 用 Phase 6 simulation) | re-expand 副作用走 expansion 1 LLM, 跟 Phase 5 expand 1 call/tick 一致 |
| Wave A mapping function | (m1) linear conf=score/5 | 零超参数; 严格对应顶层 §11.5; score 1 → conf 0.2 链自然断 |
| Wave A floor | 不加 | PROPAGATION_THRESHOLD=0.05 自然 floor; 加 floor 反而干扰 negative control 区分 |
| Acceptance approach | rescore existing 3 session (Wave D) | 现有 3 session 是 Phase 6 精心 negative control baseline; 新 session 不可比 |
| Rescore prompt | 复用 scoring.yaml (改 user_template) | "一切从简", 跟 Phase 6 design 哲学 |
| shared propagation util | 抽 `_propagation.py` (Wave B.4) | Phase 6 simulation + prediction + counterfactual 三处 DRY |
| Schema 改动 | 改 state.py (3 处), 不动 nodes/edges/graph | 最小侵入; Phase 6 零改 schema 但 Phase 7 必须扩 Action / TraceEntry |
| 命令分工 | predict / counterfactual / rescore 各独立 | 不内嵌 explain run; 跟 Phase 6 check 同处理 |

---

## 10. Wave / Task breakdown (详)

### 10.1 总规模 vs Phase 5/6

| | Phase 5 | Phase 6 | Phase 7 |
|---|---|---|---|
| Wave | 4 | 1 | 4 |
| Task | 10 | 5 | 11 |
| 新 schema 字段 | 4 | 0 | 3 (state.py) |
| 新 CLI 命令 | 1 + 改 1 | 1 | 3 |
| 新 prompt yaml | 1 | 0 | 3 |
| 新 engine module | 0 | 1 (simulation + _propagation) | 4 (parser/prediction/counterfactual/reflection) |
| LLM call increment / use | 真 LLM (eval/expansion) | 0 | 真 LLM (parser/gen/narrative/rescore) |
| 测试增量 | +73 | +43 | +74 |
| 累计测试 | 232 | 275 | ~349 |

### 10.2 Task split

```
Wave A — Confidence 信号化 (~10 step, +10 test)
├── Task A.1: evaluation.py 写回 manifests_as edge.confidence + tests
│             (~5 step, +5 test)
└── Task A.2: expansion.py 写回 causes edge.confidence + tests
              (~5 step, +5 test)

Wave B — Forward Prediction + Counterfactual B3 (~30 step, +39 test)
├── Task B.1: intervention_parser.py + intervention_parser.yaml + tests
│             (~7 step, +8 test)
├── Task B.2: prediction.py + prediction.yaml + cli explain predict
│             + HITL review_predicted_l0 + tests
│             (~10 step, +10 engine test + 5 CLI test)
├── Task B.3: counterfactual.py + counterfactual_narrative.yaml + cli
│             explain counterfactual + tests
│             (~8 step, +8 engine test + 5 CLI test)
└── Task B.4: shared propagation utility refactor (抽 get_all_L0 /
              get_all_L1_L2 / propagate_from_sources_with_diff 进
              _propagation.py, simulation.py 改 import) + tests
              (~5 step, +3 test)

Wave C — Reflection Engine (~17 step, +25 test)
├── Task C.1: reflect() decision + 常量 + tests (0 LLM)
│             (~6 step, +10 test)
├── Task C.2: expansion.re_expand() + tests + scheduler 改 +
│             runtime.py 加 reflect 分支 + tests
│             (~6 step, +5 re_expand test + 5 scheduler test + 5 runtime test)
└── Task C.3: runtime/stop.py 加 reflection_signaled_stop + tests
              (~5 step, +3 test)
   
Wave D — Acceptance + 文档 (~9 step, +5 test)
├── Task D.1: explain rescore CLI + rescore engine + 真 LLM 重跑
│             3 session + Phase 6 check 对比 + tests
│             (~6 step, +5 test)
└── Task D.2: acceptance evidence file + README 更新 (跟 Phase 6 同处理)
              (~3 step, 0 test)
```

### 10.3 Task 依赖图

```
A.1 evaluation 写回
A.2 expansion 写回
    └─→ B.1 parser
            └─→ B.2 prediction
                    └─→ B.3 counterfactual
                            └─→ B.4 shared util refactor
                                    └─→ C.1 reflect()
                                            └─→ C.2 re_expand + scheduler + runtime
                                                    └─→ C.3 stop signal
                                                            └─→ D.1 rescore + acceptance
                                                                    └─→ D.2 evidence + README
```

A.1 / A.2 内部可并行 (改不同 module). B.4 shared util refactor 放 B 最后, 是因为 B.2/B.3 实现时跑通后才知道哪些 helper 真共用了, 再抽更准. C.2 内 re_expand / scheduler / runtime 三个 sub-step 严格依赖 (runtime 调 scheduler 和 re_expand).

每 task 内部 TDD 节奏:
```
failing test → impl → green → ruff → commit
```

每 Wave 完跑全测 + ruff + checkpoint (用户审).

### 10.4 测试增量 (累计 from 276)

| Task | 新 tests | 累计 |
|---|---|---|
| A.1 | +5 | 281 |
| A.2 | +5 | 286 |
| B.1 | +8 | 294 |
| B.2 | +15 | 309 |
| B.3 | +13 | 322 |
| B.4 | +3 | 325 |
| C.1 | +10 | 335 |
| C.2 | +15 | 350 |
| C.3 | +3 | 353 |
| D.1 | +5 | 358 |
| D.2 | 0 | 358 |

预计 Phase 7 完工: **358 PASS** (Phase 6 完工 276 + Phase 7 +82).

(B.2 + B.3 拆 engine test + CLI test 是因为 typer CliRunner mock 跟 engine unit test 复杂度不一样, 拆开易写易调.)

### 10.5 LLM cost 估算

| 操作 | LLM call/次 | 测试期实际跑 |
|---|---|---|
| Wave A code path (新流程 evaluation/expansion) | 0 new (跟现有同) | 0 (用现有 mock) |
| Wave B `explain predict` | 1 parser + 1 gen + (1 narrative 若 substitute) = 1-3 | 0 (mock LLM) |
| Wave B `explain counterfactual` | 1 parser + 1 gen + 1 narrative = 3 | 0 (mock LLM) |
| Wave C `reflect()` | 0 (用 Phase 6 simulation) | 0 |
| Wave C `re_expand` (run 内) | 1/次 | 0 (mock LLM) |
| Wave D `explain rescore` (3 session) | ~25/session = **~75 total** | acceptance 阶段真 LLM |
| Wave D `explain predict / counterfactual` smoke | ~3/调用 = **~6 total** | acceptance 真 LLM |
| Wave D `explain run` smoke (1-2 session) | budget=15 × ~1/tick = **~30 total** | acceptance 真 LLM |

**总 Phase 7 真 LLM cost: ~111 call** (acceptance 一次性, code path 测试全 mock).

---

## 11. Phase 8 起点 (Phase 7 完工后)

Phase 7 完工后系统具备:

1. **Confidence 信号化** — Phase 6 simulation 真能区分 graph 质量, 给 Reflection 提供有意义信号
2. **Forward prediction + Counterfactual** — 用户面向"如果 X 发生" / "如果 X 不存在" 第一次落地
3. **Reflection 闭环** — runtime loop 能自我修正 (re-expand 低 consistency, prune 冗余), 是顶层 §4.6 / §12 第一次工程落地
4. **Variable-level 信号源**: consistency / essentialness 在 Wave A 之后真信号化, 为 Phase 8 Variable Lifecycle 准备

Phase 8 推荐方向 (按 ROI 排, 跟 Phase 6 acceptance §"Phase 7 起点" 推荐演化):

**优先级 1: Variable Lifecycle + Stability 持久化** (跟顶层 §6.2 / §9.1 对齐)
- VariableNode 加 `stability_score / explanatory_power / activation_level / compression_value` 字段
- Phase 6 simulation 结果持久化到 node 字段
- Lifecycle 状态机: Birth → Growth → Stabilization → Decay → Death
- Reflection 多一个 action: "decay" (mark variable 即将 death)

**优先级 2: Theory Formation Engine (V0)** (跟顶层 §13 对齐)
- 跨 session 累积 stable variable + recurring mechanism
- Theory candidate 形成: 多 session 重复出现 + 高 consistency + 高 essentialness 的 variable cluster
- Theory stability score = recurrence_frequency + compression_strength + simulation_consistency

**优先级 3: Persistent World Model 雏形** (跟顶层 §5.3 对齐)
- `sessions/_world_model.json` 跨 session shared storage
- 累积 stable variable / recurring theory
- 新 session bootstrap 时 prime with world_model 中 relevant variable

**优先级 4: Multi-Perspective Runtime** (跟顶层 §7 / §10 对齐)
- 每 perspective 独立 graph; cross-perspective relation propagation
- Reflection 加 perspective_shift action

**关键**: Phase 8 复用 Phase 7 Reflection 框架 (reflect() 决策器架构) + Phase 6 propagate() (从 single graph 扩到 multi-graph) + Phase 7 prediction (从 single session 扩到 cross-session theory transfer). 几乎所有 mechanics 已 production-ready.

---

## 12. 风险 + 反例

### 12.1 LLM 给 hallucinated session 高 plausibility

**风险**: Wave A 改后, 如果 LLM 在 s_7d491774 (hallucinated A 股 议题) 给 edge 同样高的 score (e.g. 4-5), consistency_score 跟 clean session 仍接近, negative control 仍 fail.

**应对**: 
1. Wave C reflection 是 backstop — 即使 confidence 信号弱, reflection 也能识别"3 tick 无改进"自动 stop
2. Wave D acceptance 若 fail, 单独诊断: 是 LLM 偏低 (改 scoring prompt 提示 hallucination 风险) 还是 graph 结构本身不区分 (改 simulation 算法)
3. 不在 Phase 7 内修复 — 接受 LLM noise, 后续 Phase 8+ Variable Lifecycle 用 simulation_consistency drive Decay, 自动淘汰

### 12.2 Parser 误判 existing_refs

**风险**: 用户写 "教义不可妥协" (没说 d_002), parser LLM 没 map 回 d_002 而判 new_concept, 导致 graph 膨胀重复.

**应对**:
1. Prompt 强制 "如果 graph 已有同义节点, 优先 existing_refs"
2. 测试用 hand-crafted intervention 同义近似 case 覆盖
3. acceptance 看真 LLM 行为, 若 frequent 误判 → Phase 8 加 embedding 去重 (Phase 5/6 design 多次 punt 的)

### 12.3 Reflection 阈值 0.5 不合理

**风险**: LOW_CONSISTENCY_THRESHOLD=0.5 可能让所有 L1 都触发 re-expand (Phase 6 已观察 L1 consistency=0.7 是 default placeholder = "好"), 或一个都不触发 (全 ≥ 0.5).

**应对**:
1. Wave D acceptance 跑完看实际分布, tune
2. 阈值放 reflection.py module-level constant, 测试 monkeypatch 即可
3. 备用 fallback 阈值: 0.4 / 0.6 都 tune-able 范围

### 12.4 Phase 5 GAIN_THRESHOLD 跟 Phase 7 Reflection 冲突

**风险**: Phase 5 用 `state.tick - last_gain_tick >= 3` 触发 no_gain_for_3_ticks stop. Phase 7 加 reflection_signaled_stop. 两 stop signal 优先级冲突时.

**应对**:
- 顺序检查: budget → no_gain → reflection_stop → no_frontier (在 stop.py 内 explicit 顺序)
- Tests cover 优先级 (test_runtime_stop_reflection.py)

### 12.5 Counterfactual graph 深拷贝 O(n²) 影响 perf

**风险**: substitute() 用 graph 深拷贝跑 propagate, 几十节点几十边 deep copy 也是 µs 级别, 但语义上每次调用都拷贝.

**应对**:
- graph 不大 (几十节点), pickle.dumps + loads 一次 < 1ms, 可接受
- 若 Phase 10+ graph 大几千节点再考虑 immutable propagation strategy (graph 不动, propagation 单独维护一个 act_overrides dict)

---

## 13. 不做的事 (附录 C)

### 13.1 Phase 7 范围内不做

- ❌ Variable schema 加新字段 (stability / explanatory_power / activation_level) — Phase 8+
- ❌ Persistent World Model — Phase 8+ (跨 session 文件改动大)
- ❌ Multi-Perspective Runtime — Phase 9+
- ❌ Theory Formation — Phase 8+
- ❌ Embedding / clustering 去重 — Phase 8+
- ❌ Compress as reflect action — Phase 5 design §4.2 已决定不放
- ❌ Perspective_shift as reflect action — 需要 multi-perspective
- ❌ Reflection 内嵌 attention dynamics (顶层 §6) — Phase 9+
- ❌ Energy minimization 显式实现 (顶层 §14.1) — Phase 7 用 consistency + essentialness 双信号近似
- ❌ Reflection compress (round 内 re-compress 已有 candidate) — Phase 6 design §4.2 同理由

### 13.2 一般 YAGNI

- ❌ Property-based testing (Hypothesis) — table-driven 够
- ❌ Perf benchmark
- ❌ Fuzz test
- ❌ Web UI / Markdown render
- ❌ Batch scoring prompt 优化 — Phase 7 不优化 LLM cost, Phase 7+ 再说
- ❌ Reflection 学习 (RL on reflect() decisions) — 顶层 §10 meta-cognition, Phase 10+

---

## 附录 D — 跟 Phase 6 design 的差异

| 维度 | Phase 6 | Phase 7 |
|---|---|---|
| 工作量 | 1 Wave 5 task | 4 Wave 11 task |
| Schema 改动 | 0 | 3 字段 (state.py) |
| LLM call | 0 (pure rule-based) | 真 LLM (parser / gen / rescore) |
| 新 engine | simulation + _propagation | parser / prediction / counterfactual / reflection |
| Runtime loop | 不进 | 改 scheduler + stop + runtime |
| 用户面向能力 | 0 (system internal validation) | 3 (predict / counterfactual / reflective run) |
| 哲学落地 § | §8.1 (rollout) | §4.5 (simulation) + §4.6 (reflection) + §11.3 (counterfactual) + §11.5 (regularization) |

---

## 附录 E — 验收前 review checklist

跑 Wave D 之前 review:

- [ ] All 4 Wave commits 有中文 commit message
- [ ] Co-Authored-By: Claude Opus 4.7 (1M context) trailer
- [ ] 全测 350+ PASS
- [ ] ruff 0 errors
- [ ] mypy (如启用) 0 errors
- [ ] 现有 3 session JSON 加载不报错 (反序列化向后兼容)
- [ ] CLI help output 含新命令
- [ ] Phase 6 design 附录 A 中提到的 "Phase 7+ 必须解决 confidence" — Wave A 完成
- [ ] Phase 6 design "Phase 7 起点" 推荐方向 Reflection — Wave C 完成
- [ ] Phase 6 design "Phase 7 起点" 推荐方向 Forward Prediction + Counterfactual — Wave B 完成

