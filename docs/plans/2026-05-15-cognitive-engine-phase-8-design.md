# Cognitive Engine Phase 8 — Reflect Redesign + Multi-Signal + Falsifiability + Lifecycle (Design)

> 顶层文档参考: [最终哲学以及技术实现相关设计.md](../../最终哲学以及技术实现相关设计.md) §2.1 / §4.2 / §6 / §8.1 / §9.2 / §9.4 / §10.1 / §11.3 / §14.1
> 上一 phase: [Phase 7 Design](2026-05-15-cognitive-engine-phase-7-design.md)
> 上一 phase acceptance: [Phase 7 Acceptance](2026-05-15-cognitive-engine-phase-7-acceptance.md)

**日期**: 2026-05-15
**分支**: `dev` (Phase 7 final HEAD 之后)

---

## 0. TL;DR

Phase 7 acceptance (PARTIAL PASS, 9 ✅ + 2 ⚠️ + 1 ❌) 暴露 4 个根本问题:

1. **re_expand 死循环**: s_4c5f717d 案例 reflect 选 re-expand c_003 共 17 次, graph 膨胀到 39 driver. 根因: re_expand 加 incoming `causes` (driver→L1), 但 consistency_score 只看 outgoing `manifests_as` (L1→L0), **加 driver 永远修不好 L1 的 consistency**.

2. **单信号脆弱**: ConsistencyReport 只有 `avg_consistency` + `avg_essentialness` 两个 scalar. acceptance verdict 用 avg_consistency 一个数字, 错答可以高分.

3. **Mismatch 失明**: s_705f0435 mismatch session avg_consistency = 0.414 **高于** clean session 的 0.340. LLM mechanism 打分只看"L1→L0 故事自洽", 看不到"答非所问".

4. **节点无 lifecycle**: Variable 是静态 dataclass, 无 activation/stability/age. 长跑 session 节点单调增长无清理. 哲学 §6.1 "Variable 是 evolving organism" 完全没落地.

Phase 8 4 个修复 Wave (一一对应) + 1 个 acceptance Wave:

**Wave 1 — Reflect Redesign** (修死循环)
新 engine 函数 `expand_downward(state, l1_id, llm)` 给 L1 生成 manifests_as L0 子节点. reflect 决策树用它替换 `re_expand`. `re_expand` API 保留 (backward compat) 但 reflect 不再调用.

**Wave 2 — Multi-Signal Acceptance** (修单信号 + 含 Wave 3 rollout)
ConsistencyReport 加 6 个信号: weak_chains / lowest_l1 / consistency_spread / essentialness_spread / **rollout_coverage** / missing_l0. rollout_coverage 是从 L2 root 起算的全 graph reachability, 同时服务 Wave 3 的 alignment 第二层. reflect 决策树用 weak_chains 列表替单一阈值.

**Wave 3 — Falsifiability-Driven Alignment** (修 mismatch)
新 engine `input_validation` 在 `explain run` 入口检查 question vs L0 observations 对齐, fail-fast 抛 InsufficientObservationsError. 第二层 rollout_alignment 复用 Wave 2 的 rollout_coverage. CLI 加 `--no-input-check` flag 兜底.

**Wave 4 — Variable Lifecycle** (修节点堆积)
Variable 加 5 字段 (activation / stability / last_used_tick / age_ticks / lifecycle_state). 新 fitness 函数 (聚合 Wave 2 信号 + 图统计). update_lifecycle tick 函数自动推进 active → stale → decayed. reflect 加 `decay` action. 全程 0 LLM 调用.

**Wave 5 — Acceptance + Docs**
重跑 3 acceptance sessions (clean / hallucinated / mismatch) 验证: ① re-expand 计数 = 0 (Wave 1); ② mismatch session 在 input validation 阶段 fail-fast (Wave 3); ③ 长跑 session 节点稳定 (Wave 4). 写 Phase 8 acceptance doc + 更新 README.

总: ~9 task, 5 Wave, 线性依赖, 跟 Phase 7 (11 task) 同量级稍小.

---

## 1. Scope

### 1.1 Phase 8 内

- **Wave 1 修死循环**:
  - `expansion.py` 新增 `expand_downward(state, l1_id, llm)` (类似 `re_expand` 但走 manifests_as 方向, 输出 L0)
  - 新 prompt `expansion_downward.yaml` (LLM 输入 L1 description, 输出 N=1-3 个 L0 observations)
  - `reflection.py` 决策树: weak_chain 时调 `expand_downward` 替原来的 `re_expand`
  - `runtime.py` reflect dispatch 加 `expand_downward` case
  - `re_expand` engine 函数 + dispatch case 保留 (backward compat), 但 reflect 不再产生它

- **Wave 2 多信号 acceptance**:
  - `simulation.py` ConsistencyReport schema 加 6 字段 (weak_chains / lowest_l1 / consistency_spread / essentialness_spread / rollout_coverage / missing_l0)
  - `_propagation.py` 或 `simulation.py` 加 `rollout_from_roots()` 算法 (从 L2 root 沿 causes ↓ + manifests_as ↓ 收集 reachable L0)
  - `reflection.py` 决策树用 `weak_chains` 列表代替单阈值扫描
  - `cli.py` `explain status` 显示 6 新信号
  - 阈值都做成模块常量

- **Wave 3 falsifiability alignment**:
  - 新 engine `input_validation.py`: `validate(question, l0_nodes, llm) → InputAlignmentReport`
  - 新 prompt `input_validation.yaml` (结构化批判: 先识别 subject 再判 overlap)
  - 新 exception `InsufficientObservationsError` (在 `engines/__init__.py` 或新 `errors.py`)
  - `cli.py` `explain run` 入口集成 input_validation, fail-fast 路径
  - `cli.py` 加 `--no-input-check` flag 给老用户兜底
  - ConsistencyReport 加 `input_alignment` + `falsifiable_reason` 字段
  - rollout_alignment 复用 Wave 2 的 rollout_coverage (不另算)

- **Wave 4 variable lifecycle**:
  - `schema/nodes.py` VariableNode 加 5 字段 (activation / stability / last_used_tick / age_ticks / lifecycle_state)
  - 新模块 `engines/lifecycle.py`:
    - `compute_fitness(node, state) → float` (聚合 Wave 2 信号 + 图统计)
    - `update_lifecycle(state, current_tick)` 推进 active → stale → decayed
  - `reflection.py` 决策树加 `decay` action
  - `runtime.py` reflect dispatch 加 `decay` case
  - `_propagation.py` propagation 跳过 lifecycle_state == "decayed" 节点
  - `expansion.py` frontier check 跳过 decayed 节点

- **Wave 5 acceptance + 文档**:
  - 重跑 3 sessions (s_f3beb777 clean, s_705f0435 mismatch, s_7d491774 hallucinated) 验证 4 个 phase 7 问题都被解决
  - acceptance doc with PASS/PARTIAL/FAIL verdict per criterion
  - README 加 Phase 8 章节, 更新 Status 行

### 1.2 推到 Phase 9+

- ❌ Theory Formation Engine (§13)
- ❌ Persistent World Model / cross-session 变量复用 (§5.3)
- ❌ Multi-Perspective Runtime (§10) — perspective_shift action
- ❌ Lifecycle full 8-stage (Birth/Growth/Competition/Compression/Stabilization/Fragmentation/Decay/Death) — Phase 8 只做 active/stale/decayed 3 阶段
- ❌ Variable Lifecycle 持久化 — Phase 8 字段加在 schema 里 + default 值, 但跨 session 加载时不复用 lifecycle 状态 (Phase 9 memory consolidation 的事)
- ❌ Hard delete (Death state) — Phase 8 只 soft delete (lifecycle_state="decayed")
- ❌ Embedding-based alignment — Wave 3 不引入 embedding infra, 留 Phase 9
- ❌ Per-chain LLM "weakness reason" classification — Wave 2 Option γ 推到 Phase 9
- ❌ Coverage stats 之外更复杂的 graph metrics (e.g. cycle detection, modularity) — Phase 8 只做 reachability
- ❌ Reflection compress action — Phase 5 已决定 round 内不放 compress
- ❌ Web search / external grounding

### 1.3 Phase 8 不动的

| 模块 | 不动原因 |
|---|---|
| `engines/_propagation.py` 主算法 | Wave 2 加 `rollout_from_roots()` 是新函数, 不动 `propagate()` |
| `engines/simulation.py` 主 API (`simulate`) | 只加 ConsistencyReport 字段, simulate() 调用方不变 |
| `engines/bootstrap.py` | 不动 |
| `engines/compression.py` | 不动 |
| `engines/evaluation.py` | 不动 (Phase 7 已加 writeback) |
| `engines/intervention_parser.py` | 不动 (Phase 7 Wave B) |
| `engines/prediction.py` | 不动 |
| `engines/counterfactual.py` | 不动 |
| `engines/rescore.py` | 不动 |
| `runtime/scheduler.py` | 不动 (Phase 7 Wave C 已加 reflect 选择) |
| `runtime/stop.py` | 不动 (Phase 7 Wave C 已加 reflection_signaled_stop) |
| `schema/edges.py` | 字段不动 |
| `schema/graph.py` | 不动 |
| `persistence/session.py` | 不动 (新 schema 字段走向后兼容路径) |
| `hitl/cli_interactive.py` | 不动 |

---

## 2. Architecture + 目录结构

### 2.1 Module 边界

```
Phase 0-5: bootstrap → compress → evaluation → expansion → runtime
           造 graph (write)

Phase 6:   simulation
           自检 graph (read-only) → ConsistencyReport

Phase 7:   prediction / counterfactual / intervention_parser  ── write (加 node)
           reflection                                          ── read + 决策
           rescore                                             ── 改 edge.confidence (write)

Phase 8:   input_validation                                    ── read-only, fail-fast (write nothing)
           expansion.expand_downward                            ── write (加 L0 children)
           lifecycle                                            ── write (改 lifecycle 字段)
           simulation 扩展 (rollout_coverage)                  ── read-only
           reflection 决策树扩展 (decay/expand_downward)       ── 决策
```

### 2.2 文件结构 (新 / 改 / 不动)

```
src/explain_engine/
├── engines/
│   ├── _propagation.py            ── 改: 加 rollout_from_roots() (新函数, 不动 propagate)
│   ├── simulation.py              ── 改: ConsistencyReport schema 加 6 + 2 字段
│   ├── bootstrap.py               ── 不动
│   ├── compression.py             ── 不动
│   ├── evaluation.py              ── 不动
│   ├── expansion.py               ── 改: 新增 expand_downward(state, l1_id, llm)
│   │                                  + frontier check 跳过 decayed
│   ├── intervention_parser.py     ── 不动
│   ├── prediction.py              ── 不动
│   ├── counterfactual.py          ── 不动
│   ├── reflection.py              ── 改: 决策树加 decay 分支 + expand_downward 替 re-expand
│   │                                  + 用 weak_chains/lowest_l1 替单阈值扫描
│   ├── rescore.py                 ── 不动
│   ├── input_validation.py        ── NEW (Wave 3): validate(question, l0_nodes, llm)
│   ├── lifecycle.py               ── NEW (Wave 4): compute_fitness + update_lifecycle
│   └── errors.py                  ── NEW (Wave 3): InsufficientObservationsError
│
├── runtime/
│   ├── runtime.py                 ── 改: reflect dispatch 加 decay + expand_downward case
│   │                                  + tick lifecycle update 调用
│   ├── scheduler.py               ── 不动
│   └── stop.py                    ── 不动
│
├── schema/
│   ├── state.py                   ── 改: ReflectionAction 加 "decay" + "expand-downward"
│   ├── nodes.py                   ── 改: VariableNode 加 5 lifecycle 字段
│   ├── edges.py                   ── 不动
│   └── graph.py                   ── 不动
│
├── llm/
│   └── prompts/
│       ├── expansion_downward.yaml ── NEW (Wave 1)
│       ├── input_validation.yaml   ── NEW (Wave 3)
│       ├── intervention_parser.yaml ── 不动
│       ├── prediction.yaml         ── 不动
│       ├── compression.yaml        ── 不动
│       ├── expansion.yaml          ── 不动
│       ├── scoring.yaml            ── 不动
│       └── variable_extraction.yaml ── 不动
│
├── hitl/                          ── 不动
├── persistence/                    ── 不动
├── cli.py                         ── 改: explain run 集成 input_validation
│                                       + --no-input-check flag
│                                       + explain status 显示新信号
└── config.py                      ── 不动

tests/
├── test_engines_expand_downward.py        ── NEW (Wave 1, +6)
├── test_runtime_reflect_expand_downward.py ── NEW (Wave 1, +4)
├── test_engines_simulation_signals.py     ── NEW (Wave 2, +8)
├── test_engines_propagation_rollout.py    ── NEW (Wave 2, +6)
├── test_engines_reflect_weak_chains.py    ── NEW (Wave 2, +5)
├── test_cli_status_signals.py             ── NEW (Wave 2, +3)
├── test_engines_input_validation.py       ── NEW (Wave 3, +8)
├── test_cli_run_input_validation.py       ── NEW (Wave 3, +6)
├── test_engines_lifecycle_fitness.py      ── NEW (Wave 4, +8)
├── test_engines_lifecycle_update.py       ── NEW (Wave 4, +6)
├── test_engines_reflect_decay.py          ── NEW (Wave 4, +5)
├── test_propagation_skip_decayed.py       ── NEW (Wave 4, +4)
└── test_schema_lifecycle_backward_compat.py ── NEW (Wave 4, +3)
```

约 +72 单元测试 (Phase 7 是 +74, 同量级).

### 2.3 依赖图 (单向无环)

```
cli.py
   ├─→ engines/input_validation.py   (Wave 3 NEW)
   │      └─→ engines/errors.py
   │
   ├─→ engines/expansion.py          (Wave 1 改)
   │      └─→ engines/lifecycle.py   (Wave 4 frontier 跳过 decayed)
   │
   ├─→ engines/reflection.py         (Wave 1 + 2 + 4 改)
   │      ├─→ engines/simulation.py
   │      └─→ engines/lifecycle.py   (Wave 4 fitness 用作 decay 决策)
   │
   ├─→ engines/simulation.py         (Wave 2 改)
   │      └─→ engines/_propagation.py (Wave 2 加 rollout_from_roots)
   │
   └─→ runtime/runtime.py
          ├─→ engines/expansion.py
          ├─→ engines/reflection.py
          └─→ engines/lifecycle.py   (Wave 4 update_lifecycle on tick)
```

无新循环依赖. `lifecycle.py` 是叶子模块, 被 `expansion.py` / `reflection.py` / `runtime.py` 单向依赖.

### 2.4 顶层目录对齐

顶层 §15 把 reflection / simulation 都放 `runtime/reflection/` `runtime/simulation/`. Phase 8 同 Phase 6/7 处理: 这些都是"algorithm on graph", 放 `engines/` 跟 expansion/compression 同级. `lifecycle.py` 同性质 (是 Variable 演化算法).

---

## 3. Schema 改动

### 3.1 改 `schema/nodes.py` — VariableNode 加 5 lifecycle 字段

```python
# Wave 4 新增字段, 全部 default 值, 完全向后兼容

@dataclass
class VariableNode:
    # ... existing fields (id, name, description, abstraction_level, ...) ...

    # ─── Wave 4 lifecycle 字段 (新) ───
    activation: float = 1.0
    """当前激活度 (0.0-1.0). Birth 时 1.0, decay 时降低. 由 simulation/expand 触达时刷新."""

    stability: float = 0.0
    """稳定性 (0.0-1.0). 重复被 expand/reflect 触达累加. 用作 fitness 加分项."""

    last_used_tick: int = 0
    """最后被 simulation/reflect/expand 触达的 tick. 配合 age_ticks 算"陈旧度"."""

    age_ticks: int = 0
    """总存活 tick 数. Birth 时 0, 每个 tick +1."""

    lifecycle_state: Literal["active", "stale", "decayed"] = "active"
    """生命阶段:
       - active: 正常参与 simulation / expand / reflect
       - stale: fitness 长期低, 候选 decay (仍参与 simulation, 仅作为 reflect 提示)
       - decayed: fitness 极低且超时, 不参与 simulation / expand, 但 trace 保留 (soft delete)
    """

    def to_dict(self) -> dict:
        return {
            # ... existing ...
            "activation": self.activation,
            "stability": self.stability,
            "last_used_tick": self.last_used_tick,
            "age_ticks": self.age_ticks,
            "lifecycle_state": self.lifecycle_state,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VariableNode":
        return cls(
            # ... existing ...
            activation=d.get("activation", 1.0),
            stability=d.get("stability", 0.0),
            last_used_tick=d.get("last_used_tick", 0),
            age_ticks=d.get("age_ticks", 0),
            lifecycle_state=d.get("lifecycle_state", "active"),
        )
```

**向后兼容**: Phase 5/6/7 saved session JSON 加载时 5 个字段都走 `.get()` 默认, 老节点全部 active 状态, 不影响行为.

### 3.2 改 `engines/simulation.py` — ConsistencyReport 加字段

```python
@dataclass(frozen=True)
class ConsistencyReport:
    # ── existing (Phase 6) ──
    per_l1: dict[str, float]
    per_l2: dict[str, float]
    avg_consistency: float
    avg_essentialness: float

    # ── Wave 2 新增 (6 个 multi-signal) ──
    weak_chains: list[str] = field(default_factory=list)
    """consistency_score < LOW_CONSISTENCY_THRESHOLD (0.5) 的 L1 id 列表, 按 score 升序."""

    lowest_l1: tuple[str, float] | None = None
    """argmin(per_l1) 的 (id, score). 空 graph 时 None."""

    consistency_spread: float = 0.0
    """max(per_l1) - min(per_l1). 区分'全军覆没'(spread 小+avg 低) vs '局部弱链'(spread 大)."""

    essentialness_spread: float = 0.0
    """max(per_l2) - min(per_l2). 用于 prune 决策."""

    rollout_coverage: float = 1.0
    """从 L2 root 起算的全 graph rollout 触达 L0 比例 (|reachable_L0| / |total_L0|).
       1.0 = 所有 L0 都能被 root rollout 推演出来 (理想)
       0.0 = root drivers 完全脱节 (极端 mismatch)
    """

    missing_l0: list[str] = field(default_factory=list)
    """rollout_coverage 中没被触达的 L0 id 列表. 给 reflect / debug 用."""

    # ── Wave 3 新增 (alignment) ──
    input_alignment: float | None = None
    """Wave 3 input_validation 输出 (overlap_score / 5.0). None = 没跑过校验.
       主要用于 acceptance / CLI 显示, 不直接驱动 reflect (reflect 走 fail-fast 路径).
    """

    falsifiable_reason: str | None = None
    """如果 input_alignment 低, LLM 给的'observations 与 question 不匹配'理由. CLI 展示用."""
```

**注**: rollout_alignment 不是单独字段, 直接复用 `rollout_coverage` (它就是 Wave 3 层 2 想要的信号). CLI 显示 alignment 时直接读 rollout_coverage.

### 3.3 改 `schema/state.py` — ReflectionAction 扩展

```python
# 改前 (Phase 7)
ReflectionAction = Literal["continue", "re-expand", "prune", "stop"]

# 改后 (Phase 8)
ReflectionAction = Literal[
    "continue",
    "re-expand",       # 保留, backward compat (但 reflect 不再产生)
    "expand-downward", # 新 (Wave 1)
    "decay",           # 新 (Wave 4)
    "prune",
    "stop",
]
```

`TraceEntry.reflection_action` 字段类型自动跟着扩展. 旧 trace JSON 加载时 reflection_action 仍然能在 ["continue", "re-expand", "prune", "stop"] 范围, 不破坏.

### 3.4 NEW `engines/errors.py` — Wave 3 异常

```python
"""Phase 8 Wave 3: cognitive engine errors (fail-fast on input mismatch)."""


class CognitiveEngineError(Exception):
    """Phase 8 base for engine-level fail-fast errors."""


class InsufficientObservationsError(CognitiveEngineError):
    """Phase 8 Wave 3: question 与 L0 observations 不对齐, 无法形成 explanation.

    哲学锚点: §9.4 可证伪性. 系统必须能说"我无法回答这个 question",
    否则会神学化 (强行编造 explanation).

    Attributes:
        overlap_score: input_validation 给的 0-5 整数分.
        question_subject: LLM 识别出的 question 核心主体.
        observation_subjects: L0 observations 各自的主体.
        falsifiable_reason: LLM 给的'为什么不对齐'的明确理由.
    """

    def __init__(
        self,
        overlap_score: int,
        question_subject: str,
        observation_subjects: list[str],
        falsifiable_reason: str,
    ):
        self.overlap_score = overlap_score
        self.question_subject = question_subject
        self.observation_subjects = observation_subjects
        self.falsifiable_reason = falsifiable_reason
        super().__init__(
            f"Input alignment too low (score={overlap_score}/5). "
            f"Question 主体: {question_subject!r}; "
            f"Observation 主体: {observation_subjects!r}. "
            f"理由: {falsifiable_reason}"
        )
```

### 3.5 不动的 schema

| schema | 不动原因 |
|---|---|
| `RelationEdge` (edges.py) | 字段不动 |
| `ExplanationGraph` (graph.py) | 不动 |
| `SessionMeta` / `Stage` | 不加新 stage |
| `Action` (state.py) | 不变 (Phase 7 已加 "reflect") |

### 3.6 向后兼容总结

| 旧 session JSON 字段 | Phase 8 新增字段处理 |
|---|---|
| `VariableNode` 缺 5 lifecycle 字段 | 全部 `.get()` 默认值, 老节点全 active |
| `ConsistencyReport` 缺 8 新字段 | dataclass `field(default_factory=...)` 默认 |
| `TraceEntry.reflection_action` 是旧 Literal | 加载没问题 (旧值仍在新 Literal 范围) |
| 旧 simulation 调用方 | 用 `report.weak_chains` 等新字段都走 default, 老调用方读 avg_consistency 仍正常 |

3 个 Phase 7 acceptance session (s_f3beb777, s_705f0435, s_7d491774) 加载 + 重跑 sim 无 breaking change.

---

## 4. Wave 1 — Reflect Redesign (修死循环)

### 4.1 Decision summary

| 决策 | 锁定 |
|---|---|
| 死循环根因 | `re_expand` 加 `causes` (driver→L1), 但 consistency 看 `manifests_as` (L1→L0). 加 driver 永远修不好 L1 consistency. |
| 修法 | 用 `expand_downward(L1)` 给 L1 加 manifests_as L0 子节点替换 |
| `re_expand` API | 保留 (backward compat). reflect 决策树不再产生它. CLI 不暴露独立命令. |
| 新 prompt | `expansion_downward.yaml` (LLM 输入 L1 description, 输出 1-3 个 L0 observations) |
| L0 数量 | 1-3 (跟 Phase 5 expand 同上限) |
| 新 L0 epistemic | "speculation" (跟 Wave B prediction 一致) |
| 新 L0 confidence | mechanism plausibility / 5.0 (跟 Phase 7 Wave A 一致) |

### 4.2 expand_downward engine (`engines/expansion.py` 新增)

#### 4.2.1 公开 API

```python
async def expand_downward(
    state: CognitiveState,
    l1_id: str,
    llm: LLMClient,
    max_l0: int = 3,
) -> list[str]:
    """Phase 8 Wave 1: 给 L1 节点生成 manifests_as L0 子节点.

    与 expand_one_frontier (Phase 5) 的区别:
      - expand_one_frontier: 给 L1 加 incoming `causes` driver (L2→L1)
      - expand_downward: 给 L1 加 outgoing `manifests_as` 子节点 (L1→L0)

    与 re_expand (Phase 7 Wave C) 的区别:
      - re_expand 是绕过 frontier check 的 expand_one_frontier (仍加 driver)
      - expand_downward 是反方向 (加 L0 manifestation)

    哲学锚点:
      §8.1 "Explanation 必须能 rollout, 否则可能不是真机制".
      L1 consistency 低意味着 L1 难以 propagate 出 L0; 此时该让 L1 自己说出
      "如果我是真机制, 我会带来什么 concrete 现象", 然后看新 L0 与现有 L0 是否冲突.

    流程:
      1. 取 L1 node + 当前 graph context
      2. LLM call (expansion_downward.yaml): 输入 L1 description + question, 输出
         max_l0 个 PredictedL0 (name + description + mechanism + plausibility)
      3. 加新 L0 nodes (level=0, epistemic="speculation", source="llm")
      4. 加 manifests_as edges (l1_id → new_l0_id, conf=plausibility/5)
      5. 触达 L1 lifecycle (last_used_tick = current_tick, activation 提升)

    Args:
        state: 当前 cognitive state.
        l1_id: L1 node id (abstraction_level=1).
        llm: LLM client.
        max_l0: 输出 L0 数量上限 (1-3).

    Returns:
        新加的 L0 node id 列表 (长度 1-max_l0).

    Raises:
        ValueError: l1_id 不存在 / 不是 L1 / lifecycle decayed.
        SchemaValidationError: LLM 输出不合规 (retry 1 次仍失败).
    """
```

#### 4.2.2 Prompt (`expansion_downward.yaml`)

```yaml
system: |
  你是 cognitive engine 的 downward expansion sub-agent.

  任务: 给定一个 L1 abstract variable, 预测它会 manifest 出哪些新的 concrete L0 现象.

  约束:
  - 输出 1-3 个 predicted L0, 每个含 name / description / mechanism / plausibility.
  - mechanism 必须说明 "L1 为什么会 manifest 成这个 L0".
  - plausibility 是 1-5 整数, 5=机制非常可能, 1=纯猜.
  - 不要预测已有 L0 (graph 里已经有的 concrete). 调用方会自动跳过重复.
  - 输出的 L0 必须与 root question 相关 (不能引入完全新主题).

  哲学:
  - 这是 cognitive 自检. 一个 abstract variable 如果是真机制, 它必须能
    rollout 出新的 concrete observable 现象. 如果你想不出 plausible 的 L0,
    输出 plausibility 低的占位 (描述清楚为什么难想), 让 reflect 决定 prune.

  输出 schema:
  {
    "predicted_L0": [
      {"name": str, "description": str, "mechanism": str, "plausibility": 1-5},
      ...
    ]
  }

user_template: |
  根问题: {question}

  当前 L1 节点 (要扩展的):
    id: {l1_id}
    name: {l1_name}
    description: {l1_description}

  Graph 现有 L0 节点 (避免重复):
  {existing_l0_table}

  Graph 现有 L1 / L2 节点 (上下文):
  {existing_l1_l2_table}

  请输出 1-{max_l0} 个 predicted L0.
```

#### 4.2.3 边界处理

| 情况 | 处理 |
|---|---|
| `l1_id` 不存在 | `ValueError("node {l1_id} not in graph")` |
| `l1_id` 节点 abstraction_level != 1 | `ValueError("expand_downward only valid for L1 nodes")` |
| `l1_id` 节点 lifecycle_state == "decayed" | `ValueError("cannot expand decayed node {l1_id}")` |
| LLM 返 0 L0 | retry 1 次仍失败 SchemaValidationError |
| LLM 返 > max_l0 | retry 1 次仍失败 |
| LLM 返 plausibility 不在 1-5 | retry 1 次仍失败 |
| LLM 返新 L0 与现有 L0 同名 | 不去重, 直接加 (Phase 8 不引入 semantic dedup; Phase 9+ 处理) |

### 4.3 reflect 决策树改 (`engines/reflection.py`)

```python
# 改前 (Phase 7 Wave C)
def reflect(state) -> tuple[ReflectionAction, str | None]:
    if reflection_signaled_stop(state):
        return ("stop", None)
    if exhausted_re_expand_targets(state):
        # anti-thrash 兜底
        ...
    weak_l1 = find_weak_l1(state, threshold=LOW_CONSISTENCY_THRESHOLD)
    if weak_l1:
        return ("re-expand", weak_l1)   # ← Phase 7 在这里调 re_expand
    useless_l2 = find_low_essentialness_l2(state, threshold=LOW_ESSENTIALNESS_THRESHOLD)
    if useless_l2:
        return ("prune", useless_l2)
    if no_progress(state):
        return ("stop", None)
    return ("continue", None)


# 改后 (Phase 8 Wave 1+2+4 综合; 这里只看 Wave 1 的改动 = 替 re-expand)
def reflect(state) -> tuple[ReflectionAction, str | None]:
    if reflection_signaled_stop(state):
        return ("stop", None)

    # Wave 2: 用 weak_chains 列表替单阈值扫描 (5.4 节展开)
    weak_chains = state.last_consistency_report.weak_chains if state.last_consistency_report else []
    if weak_chains:
        # Wave 1 改动: re-expand → expand-downward
        target_l1 = pick_weakest_unexhausted(weak_chains, state)
        if target_l1:
            return ("expand-downward", target_l1)   # ← Phase 8 改 (Wave 1)

    # Wave 4: decay 判断 (7.5 节展开)
    decay_target = pick_decay_target(state)
    if decay_target:
        return ("decay", decay_target)

    useless_l2 = find_low_essentialness_l2(state, threshold=LOW_ESSENTIALNESS_THRESHOLD)
    if useless_l2:
        return ("prune", useless_l2)

    if no_progress(state):
        return ("stop", None)
    return ("continue", None)
```

#### 4.3.1 Anti-thrash 同步调整

Phase 7 Wave C 补丁 2 v2 用 `_exhausted_re_expand_targets()` 在 occurrence-window 里数 "re-expand" 频率. Phase 8 这个函数改名 + 同时数 expand-downward + re-expand:

```python
# 改前
def _exhausted_re_expand_targets(state) -> set[str]:
    # 数最近 LOOKBACK 个 reflect tick 中, 选了 re-expand 的 target_id 频率
    ...

# 改后
def _exhausted_expansion_targets(state) -> set[str]:
    """Wave 1: 同时数 expand-downward + re-expand (后者向后兼容).

    返回: 在 LOOKBACK_WINDOW 中被选中 ≥ THRASH_LIMIT 次的 target_id 集合.
    避免 reflect 反复 expand 同一节点.
    """
    counts = {}
    seen_reflects = 0
    for entry in reversed(state.reasoning_trace):
        if entry.action != "reflect":
            continue
        seen_reflects += 1
        if seen_reflects > RE_EXPAND_LOOKBACK_WINDOW:  # = EXPANSION_LOOKBACK_WINDOW
            break
        if entry.reflection_action in ("expand-downward", "re-expand") and entry.target_node_id:
            counts[entry.target_node_id] = counts.get(entry.target_node_id, 0) + 1
    return {t for t, c in counts.items() if c >= RE_EXPAND_THRASH_LIMIT}
```

### 4.4 runtime dispatch (`runtime/runtime.py`)

```python
# 改: reflect 分支 dispatch 加 expand-downward case

if action == "reflect":
    reflection_action, target_id = reflect(state)
    state.reasoning_trace.append(TraceEntry(
        tick=state.current_tick,
        action="reflect",
        target_node_id=target_id,
        gain_delta=0.0,
        llm_calls=0,
        timestamp=now_iso(),
        reflection_action=reflection_action,
    ))

    if reflection_action == "stop":
        state.reflection_signaled_stop = True
    elif reflection_action == "expand-downward":            # ← NEW (Wave 1)
        new_l0_ids = await expand_downward(state, target_id, llm)
        state.last_reflection_change_tick = state.current_tick
        # gain_delta 怎么算? expand_downward 不直接给 gain.
        # Wave 2 会让 simulation 在下一 reflect 时重算 weak_chains, 自然观察 gain.
    elif reflection_action == "re-expand":                   # ← 保留 (backward compat)
        await re_expand(state, target_id, llm)
        state.last_reflection_change_tick = state.current_tick
    elif reflection_action == "decay":                       # ← NEW (Wave 4, 7.5 节)
        soft_decay_node(state, target_id)
        state.last_reflection_change_tick = state.current_tick
    elif reflection_action == "prune":
        prune_node(state, target_id)
        state.last_reflection_change_tick = state.current_tick
    # "continue" 不做任何事
```

### 4.5 测试 (TDD)

**test_engines_expand_downward.py** (~6 tests):

1. `test_expand_downward_l1_node_creates_manifests_as_edges`
2. `test_expand_downward_invalid_l1_id_raises`
3. `test_expand_downward_non_l1_node_raises`
4. `test_expand_downward_decayed_node_raises`
5. `test_expand_downward_writes_plausibility_to_edge_confidence` (Wave A 一致)
6. `test_expand_downward_llm_retry_failure_raises_schema_error`

**test_runtime_reflect_expand_downward.py** (~4 tests):

1. `test_reflect_weak_chain_returns_expand_downward` (verify Wave 1 决策替换)
2. `test_runtime_dispatches_expand_downward_to_engine`
3. `test_anti_thrash_counts_expand_downward_and_re_expand_together`
4. `test_re_expand_action_still_dispatched_for_backward_compat` (旧 trace 加载后)

---

## 5. Wave 2 — Multi-Signal Acceptance (含 Wave 3 rollout 合并)

### 5.1 Decision summary

| 决策 | 锁定 |
|---|---|
| 新增信号数 | 6 (weak_chains / lowest_l1 / consistency_spread / essentialness_spread / rollout_coverage / missing_l0) |
| LLM 调用 | 0 (全部聚合 + 图遍历) |
| rollout_coverage 算法 | 从 L2 root 沿 causes ↓ + manifests_as ↓ 收集 reachable L0 集合 |
| Wave 3 alignment 第二层 | 直接复用 `rollout_coverage` (Q6.2 Option Y) |
| reflect 用 weak_chains | 替原来"扫一遍 per_l1 找 < threshold"的循环 |
| 阈值 | LOW_CONSISTENCY_THRESHOLD = 0.5 (Phase 7 已用) |
| CLI 显示 | explain status 加 "Multi-signal" section |

### 5.2 ConsistencyReport schema (3.2 节已展开)

新增字段都用 dataclass field default, 老调用方读 `avg_consistency` 仍正常.

### 5.3 simulation engine 加 rollout_coverage

#### 5.3.1 新算法 `_propagation.rollout_from_roots()`

```python
def rollout_from_roots(graph: ExplanationGraph) -> tuple[set[str], set[str]]:
    """Phase 8 Wave 2: 从 L2 root 起算的全 graph reachability.

    哲学锚点: §8.1 "Explanation 必须能 rollout, 否则可能不是真机制".
    一个 graph 的 explanatory power 应该让 root drivers rollout 出所有 L0.

    算法 (BFS):
      1. roots = {n for n in graph.nodes if n.abstraction_level == 2}
         (L2 root drivers)
         Note: 如果 graph 没有 L2 (只有 L0/L1), roots = {l1 nodes} (退化)
      2. visited = roots.copy()
      3. queue = roots.copy()
      4. while queue:
           current = queue.pop()
           for edge in graph.outgoing_edges[current]:
             # 沿 causes (L2→L1) + manifests_as (L1→L0) 都走 (二者都是 forward)
             if edge.target not in visited and edge.target node not decayed:
               visited.add(edge.target)
               queue.append(edge.target)
      5. reachable_l0 = {n for n in visited if n.level == 0}
         all_l0 = {n for n in graph.nodes if n.level == 0}
         missing_l0 = all_l0 - reachable_l0
      6. return (reachable_l0, missing_l0)
    """
```

注意: `causes` edge 在 schema 里是 `source=L2, target=L1`, 所以从 L2 出发沿 outgoing edges 自然到 L1. 同样 `manifests_as` `source=L1, target=L0`. 两种 edge 都是 forward.

#### 5.3.2 simulation.py 调用

```python
async def simulate(state, llm) -> ConsistencyReport:
    # ... existing per_l1 + per_l2 计算 ...

    # ── Wave 2 新 multi-signal 聚合 (无 LLM) ──
    weak_chains = sorted(
        [l1_id for l1_id, score in per_l1.items() if score < LOW_CONSISTENCY_THRESHOLD],
        key=lambda l1: per_l1[l1],
    )
    lowest_l1 = min(per_l1.items(), key=lambda kv: kv[1]) if per_l1 else None
    consistency_spread = (max(per_l1.values()) - min(per_l1.values())) if per_l1 else 0.0
    essentialness_spread = (max(per_l2.values()) - min(per_l2.values())) if per_l2 else 0.0

    # ── Wave 2 rollout (无 LLM) ──
    reachable_l0, missing_l0 = rollout_from_roots(state.graph)
    all_l0 = {n.id for n in state.graph.nodes.values() if n.abstraction_level == 0}
    rollout_coverage = (len(reachable_l0) / len(all_l0)) if all_l0 else 1.0

    return ConsistencyReport(
        per_l1=per_l1,
        per_l2=per_l2,
        avg_consistency=avg_consistency,
        avg_essentialness=avg_essentialness,
        weak_chains=weak_chains,
        lowest_l1=lowest_l1,
        consistency_spread=consistency_spread,
        essentialness_spread=essentialness_spread,
        rollout_coverage=rollout_coverage,
        missing_l0=sorted(missing_l0),
        # input_alignment / falsifiable_reason 由 Wave 3 注入, 这里默认 None
    )
```

### 5.4 reflect 用 weak_chains (`engines/reflection.py`)

```python
# 改前 (Phase 7)
def find_weak_l1(state, threshold) -> str | None:
    report = state.last_consistency_report
    if not report:
        return None
    candidates = [(l1, s) for l1, s in report.per_l1.items() if s < threshold]
    if not candidates:
        return None
    return min(candidates, key=lambda kv: kv[1])[0]

# 改后 (Phase 8 Wave 2)
def pick_weakest_unexhausted(weak_chains: list[str], state) -> str | None:
    """从 weak_chains (升序按 consistency) 选第一个未 exhausted 的 L1."""
    exhausted = _exhausted_expansion_targets(state)
    for l1_id in weak_chains:
        if l1_id in exhausted:
            continue
        # Wave 4 加: 也跳过 decayed
        node = state.graph.nodes.get(l1_id)
        if node and node.lifecycle_state == "decayed":
            continue
        return l1_id
    return None
```

`reflect()` 主流程改用 `state.last_consistency_report.weak_chains` (Wave 2 新字段) 而非临时扫描:

```python
def reflect(state) -> tuple[ReflectionAction, str | None]:
    # ... reflection_signaled_stop check ...
    report = state.last_consistency_report
    if report:
        weak_chains = report.weak_chains  # Wave 2 新字段
        if weak_chains:
            target = pick_weakest_unexhausted(weak_chains, state)
            if target:
                return ("expand-downward", target)
    # ... rest ...
```

### 5.5 CLI status 改 (`cli.py`)

`explain status <sid>` 输出加 "Multi-signal" section:

```
═══ Multi-signal acceptance (Phase 8 Wave 2) ═══
weak_chains          [c_002, c_005]
lowest_L1            c_002 (consistency=0.31)
consistency_spread   0.42
essentialness_spread 0.18
rollout_coverage     0.82  (8 / 10 L0 nodes reachable from L2 roots)
missing_l0           [o_007, o_009]

═══ Falsifiability (Phase 8 Wave 3) ═══
input_alignment      4/5  ✓
rollout_alignment    0.82 (= rollout_coverage)
```

如果 input_alignment = None (没跑 input check, 或老 session): 显示 `(not checked)`.

### 5.6 测试

**test_engines_simulation_signals.py** (~8 tests):

1. `test_weak_chains_returns_l1_below_threshold_sorted`
2. `test_lowest_l1_returns_argmin`
3. `test_lowest_l1_empty_graph_returns_none`
4. `test_consistency_spread_max_minus_min`
5. `test_essentialness_spread_max_minus_min`
6. `test_rollout_coverage_full_when_all_l0_reachable`
7. `test_rollout_coverage_partial_when_disconnected_l0`
8. `test_missing_l0_lists_unreachable_nodes`

**test_engines_propagation_rollout.py** (~6 tests):

1. `test_rollout_from_roots_l2_to_l1_to_l0_chain`
2. `test_rollout_from_roots_skips_decayed_nodes` (Wave 4 集成)
3. `test_rollout_from_roots_handles_no_l2_falls_back_to_l1`
4. `test_rollout_from_roots_empty_graph_returns_empty`
5. `test_rollout_from_roots_disconnected_l0_in_missing`
6. `test_rollout_from_roots_does_not_loop_on_cycle` (BFS 防环)

**test_engines_reflect_weak_chains.py** (~5 tests):

1. `test_reflect_uses_weak_chains_from_report`
2. `test_reflect_picks_weakest_unexhausted_first`
3. `test_reflect_skips_exhausted_targets`
4. `test_reflect_skips_decayed_l1` (Wave 4 集成)
5. `test_reflect_no_weak_chains_falls_through`

**test_cli_status_signals.py** (~3 tests):

1. `test_status_renders_multi_signal_section`
2. `test_status_renders_falsifiability_section`
3. `test_status_handles_old_session_with_no_signals`

---

## 6. Wave 3 — Falsifiability-Driven Alignment (修 mismatch)

### 6.1 Decision summary

| 决策 | 锁定 |
|---|---|
| 双层结构 | 层 1 = input validation (explain run 入口); 层 2 = rollout verification (复用 Wave 2 rollout_coverage) |
| 层 1 失败行为 | Fail-fast: 抛 InsufficientObservationsError, session 不开始 (Q6.1 Option A) |
| Bypass flag | `--no-input-check` (CLI level) |
| 层 1 LLM 调用次数 | 1 次 / explain run |
| Prompt 设计 | 结构化批判 (先识别 question_subject + observation_subjects, 再判 overlap_score) |
| Overlap 阈值 | overlap_score < 2 (即 1 或 0/5) → fail. 给 LLM 留缓冲. |
| 层 2 算法 | rollout_coverage (Wave 2 已实现) |
| ConsistencyReport 整合 | input_alignment (= overlap_score / 5.0) + falsifiable_reason 字段 |

### 6.2 input_validation engine (`engines/input_validation.py`)

#### 6.2.1 公开 API

```python
from pydantic import BaseModel, Field

from explain_engine.schema.state import CognitiveState
from explain_engine.schema.nodes import VariableNode
from explain_engine.llm.client import LLMClient
from explain_engine.engines.errors import InsufficientObservationsError


# 阈值
MIN_OVERLAP_SCORE = 2  # < 2 (即 0 或 1) 触发 fail-fast


class InputAlignmentReport(BaseModel):
    """LLM 结构化批判结果."""

    question_subject: str = Field(min_length=1)
    """LLM 识别出的 question 核心主体 (e.g. '员工 A 的离职原因')."""

    observation_subjects: list[str] = Field(min_length=0)
    """每条 L0 observation 的核心主体列表."""

    overlap_score: int = Field(ge=0, le=5)
    """Question 主体与 observation 主体重叠度: 0=完全无关, 5=高度匹配."""

    falsifiable_reason: str = Field(min_length=1)
    """LLM 给的明确理由 (无论 score 高低都给).
       用于 (a) fail-fast 时给用户解释; (b) 调试 / acceptance review.
    """


async def validate(
    question: str,
    l0_nodes: list[VariableNode],
    llm: LLMClient,
) -> InputAlignmentReport:
    """Phase 8 Wave 3: 入口 input validation.

    哲学锚点:
      §9.4 "Theory 必须可失败, 否则系统会神学化".
      §4.2 "Explanation 是对历史生成关系的重建" — 如果 input observations
      不在 question 描述的'历史'范围, 系统应该说"无法重建", 而不是强行编造.

    Args:
        question: root question 文本.
        l0_nodes: 当前 graph 的 L0 nodes (Phase 0-2 bootstrap 后, expand 前).
        llm: LLM client.

    Returns:
        InputAlignmentReport (LLM 结构化判断).

    Raises:
        SchemaValidationError: LLM retry 1 次仍失败.

    Note:
        本函数只 *返回* report, 不 *抛* InsufficientObservationsError.
        是否 fail-fast 由调用方 (cli.py) 根据 --no-input-check flag 决定.
    """
```

#### 6.2.2 Prompt (`input_validation.yaml`)

```yaml
system: |
  你是 cognitive engine 的 input validation sub-agent.

  任务: 判断用户给的 question 与 observations 是否对齐.

  关键: 这不是简单的"是否相关"判断, 而是结构化批判.

  你必须按 3 步走:

  步骤 1: 识别 question 的核心主体
    - 主体 = question 想要解释的'对象/现象/事件'
    - 例: "为什么员工 A 离职?" → 主体 = "员工 A 的离职原因"
    - 例: "近 5 年公司收入下滑的原因?" → 主体 = "公司收入下滑的原因"

  步骤 2: 识别每条 observation 的核心主体
    - 同样, 每条 L0 描述的'对象/现象/事件'

  步骤 3: 判断 overlap_score (0-5 整数)
    - 5 = observation 主体与 question 主体高度匹配 (e.g. 都关于 '员工 A 的工作行为')
    - 3 = 部分相关 (e.g. observation 关于'团队氛围', question 问'员工 A 离职')
    - 1 = 几乎无关 (e.g. observation 关于'公司股价', question 问'员工 A 离职')
    - 0 = 完全无关

  特别注意:
  - 要识别"X 的成因"vs"X 的影响"的方向差异. 如果 question 问'X 为什么发生',
    observations 全是'X 带来的后果', overlap_score 应该 ≤ 2 (方向不对).
  - 不要因为有相同关键词就给高分. 例: question 问'员工流失', observations
    全关于'员工招聘', 主体不同 (流失 vs 招聘), overlap 应该低.

  输出 schema:
  {
    "question_subject": str,
    "observation_subjects": [str, ...],
    "overlap_score": 0-5,
    "falsifiable_reason": str   # 无论 score 高低都给, 解释为什么是这个分.
  }

user_template: |
  Question:
  {question}

  L0 Observations:
  {l0_table}

  请按 3 步走输出.
```

#### 6.2.3 Pydantic 校验

| 情况 | 处理 |
|---|---|
| LLM 返 `overlap_score` 不在 0-5 | retry 1 次仍失败 SchemaValidationError |
| LLM 返 `question_subject` 空 | retry |
| LLM 返 `observation_subjects` 长度 ≠ len(l0_nodes) | 不强制 (LLM 可能合并相似 subject), 但 prompt 提示尽量 1:1 |
| LLM 返 `falsifiable_reason` 空 | retry (强制 LLM 给理由) |

### 6.3 explain run 入口集成 (`cli.py`)

```python
@cli.command("run")
@click.argument("session_id")
@click.option("--budget", default=10, help="Max ticks")
@click.option("--no-input-check", is_flag=True, help="Phase 8 Wave 3: skip input validation fail-fast")
def cmd_run(session_id, budget, no_input_check):
    state = session_store.load(session_id)

    # ── Wave 3 Phase 8: input validation (新) ──
    if not no_input_check and state.current_tick == 0:
        # 只在第一次 run 调 (current_tick=0 = 还没开始 expand)
        l0_nodes = [n for n in state.graph.nodes.values() if n.abstraction_level == 0]
        try:
            report = asyncio.run(validate(state.root_question, l0_nodes, llm_client))
            # 写回 ConsistencyReport (lazy: 写到 state, simulation 时合并)
            state.last_input_alignment_report = report
            if report.overlap_score < MIN_OVERLAP_SCORE:
                raise InsufficientObservationsError(
                    overlap_score=report.overlap_score,
                    question_subject=report.question_subject,
                    observation_subjects=report.observation_subjects,
                    falsifiable_reason=report.falsifiable_reason,
                )
        except InsufficientObservationsError as e:
            click.echo(f"\n❌ Input validation failed (overlap={e.overlap_score}/5)\n", err=True)
            click.echo(f"Question 主体: {e.question_subject}", err=True)
            click.echo(f"Observation 主体:", err=True)
            for s in e.observation_subjects:
                click.echo(f"  - {s}", err=True)
            click.echo(f"\n理由: {e.falsifiable_reason}", err=True)
            click.echo(f"\n💡 If you believe this is a false positive, retry with --no-input-check", err=True)
            sys.exit(2)

    # ── 正常 run loop ──
    asyncio.run(runtime.run(state, budget=budget, llm_client=llm_client))
    session_store.save(state)
```

### 6.4 ConsistencyReport 整合 (Wave 2 + 3)

`simulate()` 在生成 ConsistencyReport 时, 把 `state.last_input_alignment_report` 注入:

```python
async def simulate(state, llm) -> ConsistencyReport:
    # ... per_l1 / per_l2 / weak_chains / rollout_coverage ...

    input_alignment = None
    falsifiable_reason = None
    if state.last_input_alignment_report is not None:
        input_alignment = state.last_input_alignment_report.overlap_score / 5.0
        falsifiable_reason = state.last_input_alignment_report.falsifiable_reason

    return ConsistencyReport(
        # ... existing ...
        input_alignment=input_alignment,
        falsifiable_reason=falsifiable_reason,
    )
```

新加 `state.last_input_alignment_report: InputAlignmentReport | None = None` 到 `CognitiveState`. 不持久化 (跨 run 时会重新校验, 用户可能改了 observations).

### 6.5 测试

**test_engines_input_validation.py** (~8 tests):

1. `test_validate_high_overlap_returns_high_score`
2. `test_validate_low_overlap_returns_low_score`
3. `test_validate_returns_question_and_observation_subjects`
4. `test_validate_returns_falsifiable_reason_always`
5. `test_validate_llm_retry_failure_raises_schema_error`
6. `test_validate_empty_l0_returns_low_score` (no observations to align)
7. `test_validate_does_not_raise_insufficient_obs_error` (validate 只返 report)
8. `test_insufficient_observations_error_str_format`

**test_cli_run_input_validation.py** (~6 tests):

1. `test_cli_run_low_overlap_exits_with_code_2_and_message`
2. `test_cli_run_high_overlap_proceeds_normally`
3. `test_cli_run_no_input_check_flag_skips_validation`
4. `test_cli_run_only_validates_on_first_tick` (再次 run 不重复 validate)
5. `test_cli_run_writes_alignment_to_state` (state.last_input_alignment_report)
6. `test_cli_run_old_session_loads_with_alignment_none` (backward compat)

---

## 7. Wave 4 — Variable Lifecycle (修节点堆积)

### 7.1 Decision summary

| 决策 | 锁定 |
|---|---|
| Lifecycle 字段 | 5 个 (activation / stability / last_used_tick / age_ticks / lifecycle_state) |
| Lifecycle 状态 | 3 个 (active / stale / decayed). 不做 birth/death (Phase 9+) |
| Fitness 公式 | 聚合 Wave 2 信号 (consistency, essentialness) + 图统计 (degree) + lifecycle 字段 (activation, stability) - 反向 (redundancy) |
| Decay 行为 | Soft delete: lifecycle_state = "decayed". 不删 node, 不删 trace. propagation/expand 跳过. |
| Auto-update 频率 | 每个 reflect tick 触发 update_lifecycle |
| Reflect decay action | 选 fitness < DECAY_THRESHOLD 的节点 decay |
| 持久化 | Phase 8 字段写入 JSON (向后兼容), 但**不**在 Phase 8 实现 cross-session lifecycle 复用. Phase 9 memory consolidation 来做. |
| LLM 调用 | 0 (全程算法 + 图统计) |

### 7.2 Variable lifecycle 字段 (3.1 节已展开)

### 7.3 Fitness 公式 (`engines/lifecycle.py`)

```python
"""Phase 8 Wave 4: Variable lifecycle engine.

哲学锚点:
  §6.1 "Variable 是 evolving conceptual organism, 不是静态 node".
  §9.2 Variable Fitness 公式:
    fitness = explanatory_power + reuse_frequency + compression_value
              + predictive_utility + graph_centrality - vagueness - redundancy
  §11.3 "目标不是最大知识量, 而是最低 entropy 下的最大解释力" — 所以要 decay.
"""

# 阈值常量
STALE_THRESHOLD = 0.3
"""fitness < 此阈值 → 候选 stale (短期低 fitness)."""

DECAY_THRESHOLD = 0.1
"""fitness < 此阈值 → 候选 decayed (长期极低 fitness)."""

STALE_TO_DECAYED_TICKS = 5
"""节点在 stale 状态停留多少 tick 后, 升级为 decayed."""


def compute_fitness(node: VariableNode, state: CognitiveState) -> float:
    """Phase 8 Wave 4: 节点 fitness 公式 (Phase 8 简化版, 无 LLM).

    顶层 §9.2 完整公式有 7 项. Phase 8 实现其中 5 项 (用现有信号近似), 2 项推到 Phase 9+:
      - explanatory_power     ≈ Wave 2 per_l1 / per_l2 (consistency / essentialness)
      - reuse_frequency       ≈ activation (lifecycle 字段)
      - compression_value     ≈ Phase 4 stability (复用 Phase 7 attribute)
      - predictive_utility    [Phase 9+, 需要 prediction 命中率统计]
      - graph_centrality      ≈ degree(node) / max_degree
      - vagueness             [Phase 9+, 需要 NLP 评估 description 模糊度]
      - redundancy            ≈ semantic_neighbor_count(node)  [Phase 8 算近似]

    Returns:
        fitness ∈ [0.0, 1.0+] (理论上无上界, 但实际 < 2.0). 越大 = 节点越"健康".
    """
    # explanatory power (来自 Wave 2 per_l1 / per_l2 信号)
    if node.abstraction_level == 1 and state.last_consistency_report:
        explanatory = state.last_consistency_report.per_l1.get(node.id, 0.0)
    elif node.abstraction_level == 2 and state.last_consistency_report:
        explanatory = state.last_consistency_report.per_l2.get(node.id, 0.0)
    else:
        explanatory = 0.5  # L0 没有 consistency 概念, 给中性默认

    # reuse frequency
    reuse = node.activation

    # stability (Phase 4 已写)
    stability = node.stability

    # graph centrality
    degree = (
        len(state.graph.outgoing_edges.get(node.id, []))
        + len(state.graph.incoming_edges.get(node.id, []))
    )
    max_degree = max(
        len(state.graph.outgoing_edges.get(nid, [])) + len(state.graph.incoming_edges.get(nid, []))
        for nid in state.graph.nodes
    ) if state.graph.nodes else 1
    centrality = degree / max_degree if max_degree > 0 else 0.0

    # redundancy (Phase 8 近似: 同 level 同 outgoing target set 的兄弟数)
    siblings_with_same_targets = 0
    my_targets = frozenset(e.target_node for e in state.graph.outgoing_edges.get(node.id, []))
    for sib_id, sib in state.graph.nodes.items():
        if sib_id == node.id:
            continue
        if sib.abstraction_level != node.abstraction_level:
            continue
        sib_targets = frozenset(e.target_node for e in state.graph.outgoing_edges.get(sib_id, []))
        if sib_targets == my_targets and len(my_targets) > 0:
            siblings_with_same_targets += 1
    redundancy = min(siblings_with_same_targets * 0.2, 0.5)  # 上限 0.5

    fitness = explanatory + reuse * 0.3 + stability * 0.2 + centrality * 0.3 - redundancy
    return max(0.0, fitness)  # clamp 下界
```

#### 7.3.1 Fitness 公式调优注

公式权重 (0.3 / 0.2 / 0.3) 是 Phase 8 起始值, 通过 acceptance run 后调整 (Wave 5 阶段). 都做成模块常量便于 tweak.

### 7.4 update_lifecycle 算法

```python
def update_lifecycle(state: CognitiveState, current_tick: int) -> dict[str, str]:
    """Phase 8 Wave 4: 在每个 reflect tick 推进所有节点的 lifecycle.

    返回: {node_id: new_state} (变更日志, 给 trace 用).

    状态机:
        active → stale: fitness < STALE_THRESHOLD
        stale → decayed: stale 累积 ≥ STALE_TO_DECAYED_TICKS
        stale → active: fitness 回到 ≥ STALE_THRESHOLD (复活)
        decayed → ?  (Phase 8 不复活 decayed; Phase 9 用 memory consolidation 处理)

    副作用:
        - 更新所有 node 的 lifecycle_state, age_ticks
        - 节点 fitness 高时, activation 提升 (rollout 触达自动衰减?)
        - 不删除任何节点 (soft delete)
    """
    changes = {}
    for node_id, node in state.graph.nodes.items():
        node.age_ticks = current_tick - 0  # node 创建 tick 不存, 简化为 current_tick (Phase 9 加 birth_tick)

        if node.lifecycle_state == "decayed":
            continue  # decayed 不再变化

        fitness = compute_fitness(node, state)

        if node.lifecycle_state == "active":
            if fitness < STALE_THRESHOLD:
                node.lifecycle_state = "stale"
                node._stale_since_tick = current_tick  # 临时字段, 不持久化
                changes[node_id] = "stale"

        elif node.lifecycle_state == "stale":
            if fitness >= STALE_THRESHOLD:
                # 复活
                node.lifecycle_state = "active"
                node._stale_since_tick = None
                changes[node_id] = "active"
            elif current_tick - getattr(node, "_stale_since_tick", current_tick) >= STALE_TO_DECAYED_TICKS:
                node.lifecycle_state = "decayed"
                changes[node_id] = "decayed"

    return changes
```

注: `_stale_since_tick` 是 in-memory 临时字段, 不持久化 (重启后 stale 节点会重新数 5 tick). Phase 8 这是已知 limitation, Phase 9 加 lifecycle persistence 时一并修.

### 7.5 reflect decay action (`engines/reflection.py`)

```python
def pick_decay_target(state: CognitiveState) -> str | None:
    """Phase 8 Wave 4: 选 fitness 最低且 < DECAY_THRESHOLD 的节点 decay."""
    if not state.graph.nodes:
        return None

    candidates = [
        (nid, compute_fitness(node, state))
        for nid, node in state.graph.nodes.items()
        if node.lifecycle_state in ("active", "stale")
    ]
    candidates = [(nid, f) for nid, f in candidates if f < DECAY_THRESHOLD]
    if not candidates:
        return None

    # 选 fitness 最低的
    return min(candidates, key=lambda kv: kv[1])[0]


# reflect 决策树插入位置 (4.3 节已展示):
# weak_chains → expand-downward
# pick_decay_target → decay     ← Wave 4 NEW
# useless_l2 → prune
# no_progress → stop
```

### 7.6 simulation/expand 跳过 decayed 节点

#### 7.6.1 propagation 跳过

```python
# _propagation.py 改 propagate (Phase 6) + rollout_from_roots (Wave 2)
# 在 BFS 推进时:

for edge in graph.outgoing_edges.get(current, []):
    target_node = graph.nodes.get(edge.target_node)
    if target_node is None:
        continue
    if target_node.lifecycle_state == "decayed":   # ← Wave 4 新
        continue
    # ... existing propagation logic ...
```

#### 7.6.2 expansion 跳过

```python
# expansion.py — frontier 选择跳过 decayed
def find_expansion_frontier(graph) -> list[str]:
    return [
        nid for nid, node in graph.nodes.items()
        if node.abstraction_level == 1
        and node.lifecycle_state != "decayed"   # ← Wave 4 新
        and ...  # existing frontier logic
    ]
```

#### 7.6.3 lifecycle update on expand/simulate 触达

每次节点被 expand/simulation/reflect 触达时, 提升 activation:

```python
def touch_node(state, node_id, current_tick):
    """Phase 8 Wave 4 helper: 节点被 simulation/expand/reflect 触达时调."""
    node = state.graph.nodes.get(node_id)
    if not node:
        return
    node.last_used_tick = current_tick
    node.activation = min(1.0, node.activation + 0.2)  # 提升, 上限 1.0
    node.stability = min(1.0, node.stability + 0.05)   # 缓慢提升
```

不触达的节点会自然 decay (activation 在 update_lifecycle 时不主动衰减; 但 fitness 因 reuse 部分降低 → 可能 stale).

### 7.7 测试

**test_engines_lifecycle_fitness.py** (~8 tests):

1. `test_compute_fitness_high_consistency_high_fitness`
2. `test_compute_fitness_low_activation_low_fitness`
3. `test_compute_fitness_redundant_node_lower_fitness`
4. `test_compute_fitness_high_centrality_higher_fitness`
5. `test_compute_fitness_l0_node_uses_default_explanatory`
6. `test_compute_fitness_no_consistency_report_uses_default`
7. `test_compute_fitness_empty_graph_handles_gracefully`
8. `test_compute_fitness_clamps_to_non_negative`

**test_engines_lifecycle_update.py** (~6 tests):

1. `test_update_lifecycle_active_to_stale_on_low_fitness`
2. `test_update_lifecycle_stale_to_active_on_recovery`
3. `test_update_lifecycle_stale_to_decayed_after_window`
4. `test_update_lifecycle_decayed_does_not_revive`
5. `test_update_lifecycle_returns_change_log`
6. `test_update_lifecycle_increments_age_ticks`

**test_engines_reflect_decay.py** (~5 tests):

1. `test_pick_decay_target_returns_lowest_fitness_below_threshold`
2. `test_pick_decay_target_returns_none_when_all_above_threshold`
3. `test_pick_decay_target_skips_already_decayed`
4. `test_reflect_returns_decay_action_when_target_found`
5. `test_runtime_dispatches_decay_to_soft_delete`

**test_propagation_skip_decayed.py** (~4 tests):

1. `test_propagate_skips_decayed_target`
2. `test_rollout_from_roots_skips_decayed`
3. `test_expansion_frontier_excludes_decayed`
4. `test_simulation_consistency_excludes_decayed_chain`

**test_schema_lifecycle_backward_compat.py** (~3 tests):

1. `test_old_session_json_loads_with_default_lifecycle_fields`
2. `test_new_node_serialized_with_lifecycle_fields`
3. `test_round_trip_preserves_lifecycle_state`

---

## 8. Wave 5 — Acceptance + Docs

### 8.1 重跑 acceptance

3 个 Phase 7 acceptance sessions 重跑, 验证 Phase 8 4 个修复都生效:

| Session | Phase 7 状态 | Phase 8 验证目标 |
|---|---|---|
| s_f3beb777 (clean) | avg_consistency 0.340 (PASS) | rollout_coverage > 0.7; input_alignment ≥ 4/5; 无 expand-downward thrash |
| s_705f0435 (mismatch) | avg_consistency 0.414 (FAIL: 反高于 clean) | **`explain run` 入口直接 fail-fast** with InsufficientObservationsError |
| s_7d491774 (hallucinated) | re_expand 死循环 (FAIL) | 无 re_expand; expand-downward 触发 ≤ 2 次; 节点数稳定 (无 39 driver 堆积); 部分 fitness 低节点进 decayed |

### 8.2 Acceptance criteria

| # | Criterion | 期望 | 来源 |
|---|---|---|---|
| 1 | Wave 1 fix re_expand 死循环 | s_7d491774 重跑无 re-expand action; expand-downward 调用 ≤ 5 次 | Phase 7 Wave C 补丁验证 |
| 2 | Wave 2 multi-signal 区分 | clean 与 mismatch 在 weak_chains / rollout_coverage / consistency_spread 至少 1 项有显著差异 | Phase 7 acceptance §7 |
| 3 | Wave 3 fail-fast | s_705f0435 重跑直接 exit(2) with falsifiable_reason | 哲学 §9.4 |
| 4 | Wave 3 false positive 控制 | s_f3beb777 (clean) overlap_score ≥ 3 (不被误杀) | Wave 3 prompt 验证 |
| 5 | Wave 4 lifecycle 工作 | s_7d491774 重跑节点数从 Phase 7 的 39 降到 < 20; 至少 5 个节点进 stale, 2 个进 decayed | Wave 4 设计 |
| 6 | Backward compat | 3 个 old session JSON load 不报错; old trace reflection_action 正常显示 | Phase 8 §3.6 |
| 7 | Test 全 PASS | `pytest` 全绿; 新 +72 测试 + 老 ~462 测试 全 PASS | TDD 标准 |
| 8 | Code quality | `ruff check` 0 errors; `mypy` 0 errors | Phase 7 标准 |
| 9 | CLI UX | `--no-input-check` flag 工作; status 显示 multi-signal section; fail-fast 错误信息友好 | UX 标准 |
| 10 | 哲学契合 | 文档对照表说明每个 Wave 对应的哲学锚点 | 附录 A |

### 8.3 acceptance doc

`docs/plans/2026-05-15-cognitive-engine-phase-8-acceptance.md`:

- §0 TL;DR + verdict (PASS / PARTIAL / FAIL)
- §1 重跑 evidence (3 session before/after diff)
- §2 10 criteria 逐条 ✅/⚠️/❌
- §3 真 LLM 数据 (e.g. input_validation 输出 sample, expand_downward LLM trace)
- §4 与 Phase 7 acceptance 对比表
- §5 Phase 9+ 推动力 (lifecycle persistence, theory formation, ...)

### 8.4 README update

加 Phase 8 章节:

```markdown
## Phase 8 (2026-05-15) — Reflect Redesign + Falsifiability + Lifecycle

修 Phase 7 暴露的 4 个问题:
- ✅ re_expand 死循环 → expand_downward (Wave 1)
- ✅ 单信号 acceptance → 6 multi-signal + rollout_coverage (Wave 2)
- ✅ Mismatch 失明 → input_validation fail-fast (Wave 3)
- ✅ 节点无生命 → Variable lifecycle 3 阶段 (Wave 4)

新 CLI:
- `explain run --no-input-check`: 跳过入口校验 (Wave 3 兜底)
- `explain status` 显示 multi-signal + falsifiability section

文档:
- design: docs/plans/2026-05-15-cognitive-engine-phase-8-design.md
- plan:   docs/plans/2026-05-15-cognitive-engine-phase-8-plan.md
- acceptance: docs/plans/2026-05-15-cognitive-engine-phase-8-acceptance.md
```

更新 Status 行: `Phase 7 milestone (Confidence + Prediction + Reflection)` → `Phase 8 milestone (Reflect Redesign + Falsifiability + Lifecycle)`.

---

## 9. 跨 Wave 决策 (Q6)

### 9.1 Q6.1: Fail-fast input validation (Option A)

**决策**: Wave 3 层 1 input validation 失败时, 直接 fail-fast 抛 InsufficientObservationsError, `explain run` exit(2).

**为什么不选 B (warn-only)**:
- 哲学 §9.4 要求"系统能说我无法回答". Warn-only 等于把神学化漏洞留着.
- 实践: 大部分用户不看 warn (跑了几小时没结果才发现).

**为什么不选 C (reflect-only, 不在入口检查)**:
- 失去 fail-fast 核心价值 (mismatch session 已经跑了一半才发现).
- 浪费 LLM 调用 (Phase 5 expand 一次 ~5 个 LLM).

**误杀缓解**:
- 阈值保守 (overlap_score < 2 才 fail, 给 LLM 留缓冲)
- 结构化批判 prompt (强制 LLM 先识别 subject, 不是简单 yes/no)
- `--no-input-check` flag 给老用户 / debug 兜底

### 9.2 Q6.2: Wave 2 + Wave 3 层 2 合并 (Option Y)

**决策**: Wave 2 实现 `rollout_coverage` 作为 first-class ConsistencyReport 字段. Wave 3 层 2 不另算, 直接 Wave 3 的 `rollout_alignment = ConsistencyReport.rollout_coverage`.

**为什么不选 X (各算各的)**:
- 算法本质相同 (从 root rollout 看 L0 触达), 重复实现是 dead weight.
- ConsistencyReport schema 会有冗余字段.

**为什么不选 Z (顺序倒置)**:
- 顺序违反"从简到繁". Wave 2 的 quick stats 比 Wave 3 的 input_validation 简单.

**好处**:
- simulation 一次 rollout, 服务两个 Wave.
- 字段语义清晰: `rollout_coverage` 一个名字, 两个用途 (acceptance signal + alignment signal).
- 节省 0.5-1 任务量.

---

## 10. 反对的设计 (rejected options)

### 10.1 Wave 1 — 为什么不选 β / γ?

- **Option β** (re_expand 加 outgoing capability, 让它"双向"): 违反单一职责 (一个函数做两件事). 而且双向需要 prompt 大改.
- **Option γ** (彻底删 re_expand, 不留 backward compat): 老 trace 加载会失败. 而且 re_expand 作为 engine API 仍可被 CLI / 未来 perspective shift 用, 不该删.

### 10.2 Wave 2 — 为什么不选 α / γ?

- **Option α** (只加 4 quick stats, 不做 rollout): rollout 是哲学 §8.1 的核心, 不做 rollout 等于把 Wave 3 第二层留空, Wave 3 还得自己实现.
- **Option γ** (加 LLM weakness classification): 引入额外 LLM 不确定性, 与 Phase 8 "修复优先" 方向冲突. 且当前没明确 downstream consumer. Phase 9+ 再考虑.

### 10.3 Wave 3 — 为什么不选 α / β?

- **Option α** (LLM critical judge 作为单一 alignment 分): 违反哲学 §9.4 (没有 fail-fast 路径, 系统没有"说不"的能力). 而且 LLM 自我合理化风险.
- **Option β** (Embedding similarity): 哲学违反 §8.2 (static analysis); 引入 embedding infra 超 Phase 8 scope; "X 的成因"vs"X 的影响"在 embedding 空间不可分.

### 10.4 Wave 4 — 为什么不选 α / γ?

- **Option α** (只加字段, 没 fitness, 没自动 update): Variable 是 organism 流于摆设. 哲学 §6.1 不会满意.
- **Option γ** (加 persistence + Death state): persistence 是 Phase 9 memory consolidation 的事; Death (hard delete) 与哲学 §9.3 semantic anchoring 冲突.

### 10.5 Q6.1 — 为什么不选 B / C? (9.1 节已说明)

### 10.6 Q6.2 — 为什么不选 X / Z? (9.2 节已说明)

---

## 11. 任务清单 (草稿)

> 详细 task-by-task TDD 节奏由后续 `writing-plans` skill 展开. 本节仅给出初步分解.

| # | Wave | 任务 | 工作量 | 依赖 |
|---|------|------|--------|------|
| 1 | 1 | 实现 `expand_downward()` engine + `expansion_downward.yaml` prompt + 单元测试 | 1 | 无 |
| 2 | 1 | reflect 决策树用 expand_downward 替 re-expand + runtime dispatch + anti-thrash 同步 + 单元测试 | 0.5 | #1 |
| 3 | 2 | ConsistencyReport schema 加 6 字段 + simulation 聚合逻辑 + 单元测试 | 0.5 | 无 |
| 4 | 2 | `_propagation.rollout_from_roots()` 算法 + 单元测试 | 1 | 无 |
| 5 | 2 | reflect 用 weak_chains / pick_weakest_unexhausted + CLI status multi-signal section + 单元测试 | 1 | #2, #3, #4 |
| 6 | 3 | input_validation engine + prompt + InsufficientObservationsError + 单元测试 | 1 | 无 |
| 7 | 3 | `explain run` 入口集成 input_validation + `--no-input-check` flag + ConsistencyReport input_alignment 字段 + 单元测试 | 0.5 | #6, #3 |
| 8 | 4 | VariableNode 加 5 lifecycle 字段 + backward compat 测试 + lifecycle.compute_fitness() + 单元测试 | 1 | #3 |
| 9 | 4 | lifecycle.update_lifecycle() + reflect decay action + runtime dispatch + propagation/expansion 跳过 decayed + 单元测试 | 1.5 | #8, #2, #4 |
| 10 | 5 | 重跑 3 acceptance sessions + 写 acceptance doc + 更新 README + 哲学对照表 | 1 | 全部 |

**总计**: 9 task, ~9 工作单位. 跟 Phase 7 (11 task) 同量级稍小.

**依赖图**:
```
#1 ─→ #2 ─┐
#3 ────┬───┴─→ #5 ──→ #10
#4 ────┘
#6 ──→ #7 ──→ #10
#3 ──→ #8 ──→ #9 ──→ #10
#2,#4 ───────→ #9
```

可并行 batch: {1, 3, 4, 6, 8} (Wave 起始任务都无依赖). 顺序执行更稳妥但可以并行加速.

---

## 12. 风险 & 缓解

| # | 风险 | 概率 | 影响 | 缓解 |
|---|------|------|------|------|
| 1 | input_validation LLM 误杀 clean session | 中 | 高 | 阈值保守 (< 2/5 才 fail); 结构化批判 prompt; `--no-input-check` flag |
| 2 | Wave 1 expand_downward 与 Phase 5 expand 重复 | 中 | 中 | expand_downward = 抽出 Phase 5 expand 的 L0 生成模板, 反向调用. 不重写. |
| 3 | rollout_coverage 在大 graph 慢 | 低 | 中 | BFS 是 O(V+E), 当前 graph < 50 nodes 量级, 无性能问题 |
| 4 | Wave 4 fitness 公式调参困难 | 中 | 中 | 阈值 (STALE_THRESHOLD / DECAY_THRESHOLD / 权重) 都是模块常量, acceptance 后调 |
| 5 | Wave 4 节点过早 decay (false positive) | 中 | 高 | STALE_TO_DECAYED_TICKS = 5 缓冲; recovery 路径 (stale → active); decay 仅 soft delete |
| 6 | Backward compat 漏 (老 session 加载失败) | 低 | 高 | 全部新字段走 `.get()` 默认; test_schema_lifecycle_backward_compat.py 专测 |
| 7 | Wave 1 expand_downward 误把 question-irrelevant L0 引入 | 中 | 中 | Prompt 强约束 "must relate to root question"; LLM 输出 plausibility 低 → 自然 fitness 低 → Wave 4 decay |
| 8 | Wave 3 input_validation 增加 LLM 成本 (1 次 / explain run) | 低 | 低 | 1 次相对 Phase 5 的 ~50 LLM call 是 2% 增量, 可忽略 |
| 9 | Wave 4 lifecycle 字段不持久化导致 tick 后 stale 重置 | 低 | 低 | 已知 limitation, Phase 9 修. _stale_since_tick 是 in-memory 临时字段. 不影响 active/decayed 状态正确性. |
| 10 | reflect 决策树越来越复杂, 难调试 | 中 | 中 | 每个 ReflectionAction 独立 helper 函数 (pick_*); decision tree 单元测试覆盖 |

---

## 13. 与 Phase 7 / Phase 9 的关系

### 13.1 向后看: 修复 Phase 7

| Phase 7 问题 | Phase 8 修复 Wave |
|---|---|
| Wave C re_expand 死循环 (s_4c5f717d) | Wave 1 expand_downward 替换 |
| 单信号 acceptance (12 criteria 9 ✅+2 ⚠️+1 ❌) | Wave 2 multi-signal |
| Mismatch session avg_consistency 反高 (s_705f0435) | Wave 3 fail-fast |
| 节点无 lifecycle (39 driver 堆积) | Wave 4 lifecycle |

### 13.2 向前看: 给 Phase 9 铺路

| Phase 9+ 目标 | Phase 8 铺垫 |
|---|---|
| Cross-session memory consolidation (§5.3) | Wave 4 lifecycle 字段已加 (default 兼容); Phase 9 加 persistence + cross-session reuse |
| Theory Formation (§13) | Wave 2 weak_chains / lowest_l1 已是 "theory weak point" 信号; Phase 9 用 stable patterns 形成 theory candidate |
| Multi-Perspective Runtime (§10) | Wave 3 input_validation 可作为 perspective generation 入口校验 |
| Variable full lifecycle (§6.2 birth/death) | Phase 8 active/stale/decayed 是 8 阶段的 3 个; Phase 9 加 birth (cross-session 复活) + death (hard delete after grace period) |
| Embedding-based semantic dedup | Phase 8 fitness redundancy 项是占位 (用 same target set 近似); Phase 9 用 embedding 替换 |

---

## 14. Open questions / 推 Phase 9

1. **Lifecycle 字段持久化**: Phase 8 字段加在 schema 里 (向后兼容), 但 cross-session 加载后 lifecycle_state 该怎么用? Phase 8 决定: 加载时全部置 "active" (放弃跨 session lifecycle 状态). Phase 9 memory consolidation 来定义 "曾经 stable 的 variable 复活后是 active 还是 stale".

2. **Fitness 公式 7 项里的 2 项**: predictive_utility (需要 prediction 命中率统计) 和 vagueness (需要 NLP 评估). Phase 8 用 5 项近似. Phase 9 加.

3. **Decayed 节点的二次复活**: Phase 8 决定 decayed 不复活. 但如果用户在新一轮 expand 中显式引用了 decayed 节点 (比如 intervention parser 返回它作为 existing_ref), 该怎么办? **Phase 8 处理**: parser 仍能返回 decayed 节点 id, 但 expand 时检测到 decayed 会 raise ValueError. CLI 给用户建议 "use --revive-node flag" (Phase 9 加).

4. **Wave 3 input_validation 的可争议性**: 如果用户故意给 indirect observations (比如想用宏观经济数据解释微观个体行为), Wave 3 会 fail. 这是 feature 还是 bug? 哲学层面是 feature (强迫用户提供 directly relevant observations). 工程层面用 `--no-input-check` 兜底.

5. **expand_downward 的 prompt 设计**: 当前 prompt 让 LLM 输出 1-3 个 L0. 但有些 L1 可能根本不该 manifest 出新 L0 (e.g. L1 已经是充分压缩). 这种情况 LLM 会编. **Phase 8 处理**: Plausibility 低的 L0 会被 Wave 2 weak_chains 自然过滤; Wave 4 fitness 低也会自然 decay. 不在 prompt 层面处理.

---

## 附录 A: 哲学锚点对照表

| Wave | 哲学章节 | 原话 | Phase 8 实现 |
|------|----------|------|------------|
| 1 | §8.1 Simulation 哲学 | "Explanation 必须能 rollout, 否则可能不是真机制" | expand_downward 让 L1 产生新 L0 manifestation, 检验 L1 是不是真机制 |
| 1 | §10.1 Meta-Cognition | "系统必须思考自己的思考" | reflect 决策替换体现 second-order cognition |
| 2 | §14.1 Cognitive Energy | 公式包含 explanatory_density, simulation_consistency | rollout_coverage 衡量 explanatory_density |
| 2 | §11.3 Cognitive Entropy | "目标不是最大知识量, 而是最低 entropy 下的最大解释力" | weak_chains / consistency_spread 衡量 entropy 不均 |
| 3 | §9.4 可证伪性 | "Theory 必须可失败, 否则系统会神学化" | InsufficientObservationsError fail-fast |
| 3 | §4.2 Explanation 本质 | "Explanation 是对历史生成关系的重建" | input_validation 检查 observations 是否在 question 描述的 history 范围 |
| 3 | §8.1 rollout 哲学 | (同 Wave 1) | rollout_alignment 复用 rollout_coverage 验证 root drivers 能不能 rollout 出 L0 |
| 4 | §6.1 Variable 是生命体 | "evolving conceptual organism" | activation/stability/lifecycle_state 字段 + update_lifecycle 算法 |
| 4 | §6.2 Variable Lifecycle | 8 阶段 (Birth → Death) | Phase 8 实现 3 阶段 (active/stale/decayed); Phase 9 加其余 |
| 4 | §9.2 Variable Fitness | 7 项公式 | compute_fitness 实现 5 项 (近似); 2 项推 Phase 9 |
| 4 | §9.3 Semantic Anchoring | "保留 canonical mechanisms 防 semantic drift" | decay = soft delete (lifecycle_state 改但节点保留) |
| 4 | §11.3 Cognitive Entropy | (同 Wave 2) | 自动 decay 控制 graph 体积 |
| 跨 Wave | §2.1 智能本质 | "智能 = 概念演化能力, 而非在已有概念上计算" | Wave 4 让 Variable 演化, Wave 1 让 explanation 自检, 都不是 LLM 单次打分 |

---

## 附录 B: 与顶层目录的偏离声明

| 顶层目录 (§15) | 本项目实现 | 理由 |
|---|---|---|
| `runtime/reflection/` | `engines/reflection.py` | "algorithm on graph", 跟 expansion 同级 |
| `runtime/simulation/` | `engines/simulation.py` | 同上 |
| `runtime/counterfactual/` | `engines/counterfactual.py` | 同上 |
| `runtime/scheduler/` | `runtime/scheduler.py` | 这个对齐 |
| `runtime/memory/` | (Phase 8 未实现) | Phase 9+ memory consolidation 时建 |
| `runtime/theory/` | (Phase 8 未实现) | Phase 9+ theory formation 时建 |

Phase 8 沿用 Phase 6/7 的 `engines/` + `runtime/` 二分约定. Phase 9+ 当 theory / memory module 真出现时, 重新讨论是否升级到顶层目录结构.

---

## 附录 C: 与 Phase 7 design doc 的对比

| 方面 | Phase 7 | Phase 8 |
|---|---|---|
| Wave 数 | 4 (A/B/C/D) | 5 (1/2/3/4/5) |
| 主题 | 信号化 + 新能力 (predict / counterfactual) + reflection | 修 Phase 7 漏洞 + 哲学落地 + Phase 9 铺路 |
| 任务数 | 11 | ~9 |
| 测试增量 | +74 | +72 |
| 新 LLM prompt | +2 (intervention_parser, prediction) | +2 (expansion_downward, input_validation) |
| 新 CLI 命令 | +3 (predict, counterfactual, rescore) | 0 (改 explain run + status, 加 flag) |
| Schema 改动 | TraceEntry + CognitiveState 加字段 | VariableNode + ConsistencyReport + ReflectionAction 加字段 |
| Breaking change | 无 | 1 个 (`explain run` 默认 fail-fast on input mismatch); 用 `--no-input-check` 兜底 |

---

**文档结束.** 下一步: `writing-plans` skill 展开 task-by-task 实施计划, 落地到 `2026-05-15-cognitive-engine-phase-8-plan.md`.
