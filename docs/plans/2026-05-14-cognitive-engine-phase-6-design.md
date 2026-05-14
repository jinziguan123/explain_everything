# Cognitive Engine Phase 6 — Simulation Consistency Check (Design)

> 顶层文档参考: [最终哲学以及技术实现相关设计.md](../../最终哲学以及技术实现相关设计.md) §4.5 / §8 / §11
> 上一 phase: [Phase 5 Design](2026-05-13-cognitive-engine-phase-5-design.md)
> 上一 phase acceptance: [Phase 5 Acceptance](2026-05-13-cognitive-engine-phase-5-acceptance.md)

**Goal**: 让系统能**自己验证**自己画的 explanation graph 在结构上自洽 — 即每个 abstract (L1) / driver (L2) 是否真能沿因果链 propagate 出已观察的 concrete (L0)。直接对应顶层 §8.1:

> 如果 explanation 无法推演未来, 它可能不是真机制。

Phase 6 把这条 philosophy 编程进系统, 给每个 abstract/driver 算两个 score:
- **consistency_score** (C₁): 沿 graph 已有边的 forward propagation 到达 L0 的平均强度 (decay-aware)
- **essentialness_score** (C₂): 删除该 variable 后 graph 的总活力损失 (counterfactual)

输出 `ConsistencyReport`, 通过 `explain check <sid>` CLI 命令展示。Pure rule-based, **0 LLM call**, system-internal validation。

---

## 0. 范围 + 哲学对齐

### 0.1 Phase 6 = Simulation Consistency Check (C only)

之前 brainstorm 列了 simulation engine 4 个可能 use case:
- A) Forward Prediction (intervention → predicted effects, 用户面向)
- B) Counterfactual Reasoning (remove variable → alternative trajectory, 用户面向)
- C) Theory Consistency Check (system-internal validation)
- D) Multi-Perspective Rollout

Phase 6 范围**仅 C**。理由:

1. C 是 A/B 的核心 building block (propagation 算法 100% 共用), 先做 C 给 Phase 7+ 铺路
2. C system-internal — 失败不暴露给用户, 可以反复 tune 算法直到稳定
3. C 直接对应顶层 §8.1 的核心命题, 是最 philosophy-aligned 的方向
4. C 触发顶层 §9.1 Variable Lifecycle 的**第一个真信号源** (variable.stability 怎么算, 后续 Phase 7 可以用 consistency_score 填)
5. 工程量小 (单 wave 5 task vs A+B 的 3-4 wave 10-12 task)

A/B 推到 Phase 7, D 推到 Phase 9+ (跟 Multi-Perspective Runtime 一起做)。

### 0.2 明确不做的事 (跟顶层哲学对齐)

**不做 evidence anchoring / RAG / external grounding**。

Brainstorm 早期讨论过 "用户喂数据 + 系统主动 fetch" 的 evidence 锚定路线, 但顶层 §1 明确列 "本项目并非 RAG Assistant"。Tension 解析:

- 用户原始诉求 (LLM hallucinate 编 A 股数据) 本质是 fact-finding 需求, 跟系统目标"形成认知"不匹配
- A 股 时效性议题超出 system scope, 应该接受能力边界, 不该改架构去迁就
- 联网查询能力将来如需要 (Phase 9+), 走 Reflection Operator 调度的 grounding sub-action (顶层 §12.2 暗合), 而不是 LLM 自主 tool-use

详见附录 A "废弃的 evidence 锚定方案"。

### 0.3 明确不做的事 (Phase 6 内部 scope)

- ❌ 不做 LLM diagnostic (C₃ hidden link discovery) — pure rule-based
- ❌ 不持久化 score 到 VariableNode (in-memory only, 跟 Phase 5 plausibility 同处理)
- ❌ 不动任何现有 schema 字段
- ❌ 不在 explain show 内嵌 check (单独 explain check 命令)
- ❌ 不在 Runtime.run 主循环 trigger (Phase 6 不进 runtime loop)
- ❌ 不接入 MCP / external tool / web search

---

## 1. Architecture + 目录结构

### 1.1 跟 Phase 5 module 边界

```
Phase 0-5 (已有): bootstrap → compress → evaluation → expansion → runtime
                  全部都"造 graph" (write graph)

Phase 6 (新):     simulation
                  唯一一个"自检 graph" 的 engine, 不造任何节点/边
                  pure rule-based, 0 LLM call, 0 schema 改动
                  read-only 关系: 只读 graph, 不改 state, 不写盘
```

### 1.2 目录结构 (新增/改动)

```
src/explain_engine/
├── engines/
│   ├── simulation.py            ← NEW: SimulationEngine.check_consistency
│   │                                  + ConsistencyReport / DecayStep dataclass
│   ├── _propagation.py          ← NEW: 纯算法 module
│   │                                  (propagate 函数 + 4 个常量)
│   │                                  下划线前缀 = module-internal
│   ├── bootstrap.py             ← 不动
│   ├── compression.py           ← 不动
│   ├── evaluation.py            ← 不动
│   └── expansion.py             ← 不动
│
├── schema/
│   ├── graph.py                 ← 改: 加 outgoing_edges(node_id) helper
│   │                                  (read-only addition, 跟 Phase 5
│   │                                  frontier_nodes 同性质)
│   └── (其他不动)
│
├── runtime/                      ← 不动 (Phase 6 不进 Runtime loop)
├── llm/                          ← 不动 (0 LLM call)
├── hitl/                         ← 不动 (无 HITL)
├── persistence/                  ← 不动 (无落盘改动)
├── cli.py                       ← 改: 加 `check` 命令
└── config.py                    ← 不动

tests/
├── test_engines_propagation.py  ← NEW: pure algorithm test (~13 tests)
├── test_engines_simulation.py   ← NEW: check_consistency API test (~16 tests)
├── test_cli_check.py            ← NEW: CLI test (~7 tests)
├── test_schema_graph_outgoing.py ← NEW: outgoing_edges helper test (~3 tests)
└── (其他不动)
```

### 1.3 依赖图 (单向无环)

```
cli.py
   └─→ engines/simulation.py
          └─→ engines/_propagation.py
                 └─→ schema/graph.py     (read-only)
                 └─→ schema/nodes.py     (read-only)
                 └─→ schema/edges.py     (read-only)
```

Phase 6 module 不 import:
- `engines/{bootstrap, compression, evaluation, expansion}` (跟 Phase 5 engines 同级, 不互相依赖)
- `runtime/*` (不进 Runtime loop)
- `llm/*` (0 LLM)
- `hitl/*` (无 HITL)

### 1.4 跟顶层 §15 目录结构的对齐

顶层 §15 工程目录结构里把 simulation 放在 `runtime/simulation/`。Phase 6 没遵循, 而是放 `engines/simulation.py`。理由:
- 现有项目 `engines/` = "对 graph 的算法操作"; `runtime/` = "主循环 + scheduler + stop signal" (Phase 5 引入)
- SimulationEngine 是 algorithm on graph, **不是** runtime loop 一部分
- 跟 expansion / compression 同级最合理
- Phase 7 如果 forward prediction 需要进 Runtime loop, 再考虑提升

---

## 2. Schema (零字段改动)

### 2.1 不动的 schema

- `VariableNode`: 不加 `stability_score` / `explanatory_power` 字段。它们是 in-memory only, 跟 Phase 5 plausibility 同处理。Phase 7+ 实现 Reflection / Variable Lifecycle 时再持久化。
- `RelationEdge`: 不动 (propagation 只读 `confidence` 字段, 已存在)
- `CognitiveState`: 不加 simulation_state / evidence_store 字段 — Phase 6 是 stateless engine
- `SessionMeta` / `Stage`: 不加新 stage (任何 stage 都能 check, 不改 session lifecycle)

### 2.2 新增的 engine-internal dataclass

放在 `simulation.py` 内 (不进 `schema/`, 因为不是 graph 概念):

```python
# src/explain_engine/engines/simulation.py

from dataclasses import dataclass


@dataclass(frozen=True)
class DecayStep:
    """Propagation 路径上的一步, 用于 audit decay_trace。"""
    src: str                  # source node id
    dst: str                  # target node id
    edge_id: str
    activation_before: float  # src 当时的 activation
    edge_confidence: float
    activation_after: float   # propagated = before × confidence (未 noisy-OR 合并前)
    depth: int                # BFS 层数 (0 = 直接连 source)


@dataclass(frozen=True)
class ConsistencyReport:
    """单个 target 的 consistency check 结果。"""
    target_id: str
    consistency_score: float           # C₁: mean(reachable_L0 activations)
    reachable_L0: list[str]            # propagate 后 activation > 0 的 L0 (排序)
    weak_chains: list[str]             # reachable_L0 中 activation < WEAK_CHAIN_THRESHOLD 的
    essentialness_score: float         # C₂: Σ contribution / |L0|
    contribution_breakdown: dict[str, float]  # 每个 L0 的 marginal (baseline - without)
    decay_trace: list[DecayStep]       # propagation 路径 audit (C₁ trace only, 不含 C₂)
```

### 2.3 设计决策

| 决策 | 理由 |
|---|---|
| `frozen=True` dataclass | Read-only 报告, 防意外 mutate; 跟 Phase 5 TraceEntry 风格一致 |
| `decay_trace: list` 不是 dict | 同条边可能在不同 depth 多次 traverse (noisy-OR 多路径), 用 list 保留时序 |
| `contribution_breakdown: dict[L0_id, float]` | 用户 lookup "p_007 的 contribution 多少" 方便, L0 集合不大 (≤15) |
| 只存 `target_id` 不存 `target_node` 完整对象 | 减少耦合, render 时 CLI 自己 lookup |
| 不实现 to_dict / from_dict | Phase 6 不持久化 ConsistencyReport (每次现算), 如要持久化一行 `dataclasses.asdict()` |

---

## 3. Propagation 算法核心 (`engines/_propagation.py`)

### 3.1 公共常量

```python
PROPAGATION_THRESHOLD: float = 0.05    # 单边 propagation 后 activation < 此值不再扩散
MAX_DEPTH: int = 4                     # 顶层 §11.4. BFS 层数上限
MAX_ACTIVE_VARIABLES: int = 12         # 顶层 §11.4. 单层 active 节点 top-k 剪枝
WEAK_CHAIN_THRESHOLD: float = 0.15     # C₁ weak_chains 判定阈值

FORWARD_RELATIONS: frozenset[str] = frozenset({"causes", "manifests_as"})
# 当前 graph 只产出这两种 forward edge. contradicts / influences 等
# Phase 7+ 真出现再补.
```

全部 module-level, 允许测试 `monkeypatch.setattr` 调到极值跑边界 case。

### 3.2 propagate 函数完整签名

```python
def propagate(
    graph: ExplanationGraph,
    sources: set[str],
) -> tuple[dict[str, float], list[DecayStep]]:
    """Multi-source forward propagation 沿 causes / manifests_as 边。

    每个 source 起始 activation = 1.0. 单边公式:
        propagated = activations[src] × edge.confidence

    多路径汇聚 (noisy-OR):
        merged = 1 - (1 - existing) × (1 - propagated)

    剪枝:
        if propagated < PROPAGATION_THRESHOLD: skip
        if len(new_active_in_layer) > MAX_ACTIVE_VARIABLES: top-k by activation
        if depth >= MAX_DEPTH: stop

    Args:
        graph: 只读 (不动 graph)
        sources: 起始 node id 集合. 必须全在 graph.nodes 内, 否则 ValueError.
                 空集合直接返 ({}, []).

    Returns:
        (activations, decay_trace)
        activations: dict[node_id, final_activation ∈ (0, 1]]
                     只含 activation > 0 的 node, 含 source 自身 (= 1.0)
        decay_trace: 完整 BFS traversal 路径 (按 depth + source order),
                     audit 用. 不包含被 PROPAGATION_THRESHOLD 剪掉的步.
    """
```

### 3.3 算法 pseudocode

```python
def propagate(graph, sources):
    missing = sources - set(graph.nodes)
    if missing:
        raise ValueError(f"sources not in graph: {sorted(missing)}")
    if not sources:
        return {}, []

    activations: dict[str, float] = {s: 1.0 for s in sources}
    trace: list[DecayStep] = []
    frontier: set[str] = set(sources)

    for depth in range(MAX_DEPTH):
        # 1. 收集本层 propagation 候选
        candidates = []
        for src in frontier:
            for edge in graph.outgoing_edges(src):
                if edge.relation_type not in FORWARD_RELATIONS:
                    continue
                propagated = activations[src] * edge.confidence
                if propagated < PROPAGATION_THRESHOLD:
                    continue
                candidates.append((src, edge.target_node, edge, propagated))

        if not candidates:
            break

        # 2. 按 dst 分组算 noisy-OR (本层内多 source 合并)
        new_layer: dict[str, float] = {}
        for src, dst, edge, propagated in candidates:
            existing = new_layer.get(dst, 0.0)
            new_layer[dst] = 1.0 - (1.0 - existing) * (1.0 - propagated)
            trace.append(DecayStep(
                src=src, dst=dst, edge_id=edge.id,
                activation_before=activations[src],
                edge_confidence=edge.confidence,
                activation_after=propagated,
                depth=depth,
            ))

        # 3. MAX_ACTIVE_VARIABLES top-k 剪枝
        if len(new_layer) > MAX_ACTIVE_VARIABLES:
            top_k = sorted(new_layer.items(), key=lambda x: -x[1])[:MAX_ACTIVE_VARIABLES]
            new_layer = dict(top_k)

        # 4. 跨层合并 (跟历史 activations 再 noisy-OR)
        next_frontier: set[str] = set()
        for nid, new_act in new_layer.items():
            existing = activations.get(nid, 0.0)
            merged = 1.0 - (1.0 - existing) * (1.0 - new_act)
            activations[nid] = merged
            next_frontier.add(nid)

        frontier = next_frontier

    return activations, trace
```

### 3.4 关键设计决策

| 决策 | 理由 |
|---|---|
| BFS by depth (非 DFS) | §11.4 MAX_DEPTH 是 BFS 层数; noisy-OR 在每层内同时聚合 = semantically "同时性激活" |
| Multiplicative 单边 (`act × confidence`) | 跟 §11.5 "低 confidence 边传播时逐渐衰减" 直接对应; 链长 N 的 chain 终态 = ∏ confidence_i 自然反映"链越长越弱" |
| Noisy-OR 多路径 | 多 parent 同时支持 → activation 增强, 跟 §13.2 "recurrence_frequency" 暗合; 比 max 更 reward "多路径冗余" |
| 顶层默认常量 (DEPTH=4, ACTIVE=12) | acceptance 后 tune (跟 Phase 5 GAIN_THRESHOLD=0.1 同处理) |
| 只 propagate `causes` / `manifests_as` | 当前 graph 只有这两种 forward edge; `contradicts` / `influences` 真出现再补 (Phase 7+) |
| BFS 不显式记录 visited | MAX_DEPTH cap 保证 termination; cycle 上同 node 可多次 traverse 通过 noisy-OR 跟历史 merge, 语义合理 |

### 3.5 边界 case

| Case | propagate 行为 |
|---|---|
| `sources = ∅` | 返 `({}, [])` |
| `sources` 含不存在的 id | 抛 `ValueError("sources not in graph: [...]")` |
| graph 无 edges | 返 `({src: 1.0 for src in sources}, [])` |
| 全 graph 无 forward edges | 同上 |
| 所有 propagation 第一步就 < THRESHOLD | 返 `({sources}, [])` |
| 单 source 无 outgoing | 同上 |

### 3.6 复杂度

- 时间: O(MAX_DEPTH × V × E_per_node), 你 graph 几十节点几十边, 单 propagation < 1ms
- 空间: O(V + trace_size) ≈ O(MAX_DEPTH × E) ≈ 几 KB
- batch check (N target): N+1 个 propagation, 总 < 25ms

---

## 4. SimulationEngine API (`engines/simulation.py`)

### 4.1 公开 API

```python
def check_consistency(
    state: CognitiveState,
    target_id: str,
) -> ConsistencyReport:
    """对单个 target (L1 abstract 或 L2 driver) 跑 C₁ + C₂.

    Raises:
        ValueError: target_id 不在 graph / level=0 (L0 不可 check)
    """

def check_consistency_batch(
    state: CognitiveState,
    target_ids: Iterable[str] | None = None,
) -> list[ConsistencyReport]:
    """Batch check, baseline propagation 共用 (节省 N 次重复计算).

    Args:
        target_ids: None = 全 graph 所有 L1+L2 (按 id 升序);
                    显式 list = 指定 target. 必须全是 L1/L2.

    Raises:
        ValueError: 任一 target_id 不存在 / level=0 (fail-fast, 不 partial)
    """
```

### 4.2 内部实现 pseudocode

```python
def _validate_target(state, target_id):
    if target_id not in state.graph.nodes:
        raise ValueError(f"target {target_id!r} not found in graph")
    level = state.graph.nodes[target_id].abstraction_level
    if level == 0:
        raise ValueError(
            f"target {target_id!r} has level=0 (concrete), "
            f"only L1/L2 can be consistency-checked"
        )


def _check_with_baseline(state, target_id, baseline_acts):
    graph = state.graph
    L0_nodes = {nid for nid, n in graph.nodes.items() if n.abstraction_level == 0}
    all_L1_L2 = {nid for nid, n in graph.nodes.items() if n.abstraction_level >= 1}

    # ─── C₁: single-source propagation ───
    c1_acts, c1_trace = propagate(graph, {target_id})
    reachable_L0 = sorted(nid for nid in L0_nodes if c1_acts.get(nid, 0.0) > 0)
    consistency_score = (
        sum(c1_acts[nid] for nid in reachable_L0) / len(reachable_L0)
        if reachable_L0 else 0.0
    )
    weak_chains = sorted(
        nid for nid in reachable_L0 if c1_acts[nid] < WEAK_CHAIN_THRESHOLD
    )

    # ─── C₂: counterfactual ───
    if baseline_acts is None:
        baseline_acts, _ = propagate(graph, all_L1_L2)
    without_acts, _ = propagate(graph, all_L1_L2 - {target_id})
    contribution = {
        nid: baseline_acts.get(nid, 0.0) - without_acts.get(nid, 0.0)
        for nid in L0_nodes
    }
    essentialness_score = (
        sum(contribution.values()) / len(L0_nodes) if L0_nodes else 0.0
    )

    return ConsistencyReport(
        target_id=target_id,
        consistency_score=consistency_score,
        reachable_L0=reachable_L0,
        weak_chains=weak_chains,
        essentialness_score=essentialness_score,
        contribution_breakdown=contribution,
        decay_trace=c1_trace,
    )


def check_consistency(state, target_id):
    _validate_target(state, target_id)
    return _check_with_baseline(state, target_id, baseline_acts=None)


def check_consistency_batch(state, target_ids=None):
    if target_ids is None:
        target_ids = sorted(
            nid for nid, n in state.graph.nodes.items()
            if n.abstraction_level >= 1
        )
    else:
        target_ids = list(target_ids)
    if not target_ids:
        return []
    for tid in target_ids:
        _validate_target(state, tid)

    all_L1_L2 = {nid for nid, n in state.graph.nodes.items() if n.abstraction_level >= 1}
    baseline_acts, _ = propagate(state.graph, all_L1_L2)
    return [_check_with_baseline(state, tid, baseline_acts) for tid in target_ids]
```

### 4.3 关键设计决策

| 决策 | 理由 |
|---|---|
| L0 raise ValueError | L0 是 ground truth, propagate from self trivial=1.0, 无信息量; fail-fast 防误用 |
| Batch fail-fast | 任一 target 错就全 raise (vs partial result), 跟 Phase 5 evaluation.score_all 风格一致 |
| Batch 默认 target_ids=None 跑全 L1+L2 | 跟 CLI "explain check <sid>" 不带 var_id 跑 batch 行为一致 |
| Baseline 共用 | batch N target 复杂度: 1 baseline + 2N propagation (vs 3N 重复); graph 几十节点共 <25ms |
| decay_trace 只保 C₁, 不保 C₂ baseline/without | C₂ trace 不可读 (跟 target 没关), 占空间; C₁ trace 是 audit 的 |

### 4.4 完整 edge case 表

| Case | check_consistency 行为 |
|---|---|
| target 不在 graph | raise ValueError |
| target level=0 | raise ValueError |
| target 无 outgoing forward edge | consistency_score=0, reachable_L0=[], essentialness ≥ 0 |
| graph 无 L0 | consistency_score=0, essentialness=0 (没分母) |
| graph 只有 target 一个 L1/L2 + 有 L0 | essentialness 反映 target 独自覆盖能力 |
| graph 无 forward edges | consistency_score=0 |
| batch(target_ids=[]) | 返 [] |
| batch(None) 且 graph 无 L1/L2 | 返 [] |

### 4.5 API 不做的事 (YAGNI)

- ❌ 不返回 "理想 graph 长啥样" 的建议 (LLM diagnostic, C₃ 跳过)
- ❌ 不持久化 score 到 VariableNode
- ❌ 不接受 LLM 参数 (0 LLM call)
- ❌ 不支持自定义 propagation 算法 / strategy pluggable
- ❌ 不计算跨 session theory stability (Phase 8 Theory Formation 的事)

---

## 5. CLI 集成 (`explain check`)

### 5.1 命令签名

```python
@app.command()
def check(
    session_id: str = typer.Argument(...),
    target_id: str | None = typer.Argument(None),
) -> None:
    """Phase 6 consistency check: 数学验证 abstract/driver 能否 rollout 出 concrete.

    Pure rule-based, 0 LLM call. 适合在已 converged session 上 audit graph 质量。

    Examples:
        explain check s_f3beb777              # batch: 全 graph L1+L2
        explain check s_f3beb777 c_001        # 单 target
    """
```

### 5.2 Batch rendering (摘要表)

```
                  Consistency Check: s_f3beb777
              (graph: 12 L0 + 3 L1 + 8 L2 = 23 nodes)

┏━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ ID    ┃ 名称              ┃ Lvl ┃ Consistency ┃ Essentialness ┃
┡━━━━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ c_001 │ 绝对化价值框架     │ 1   │ 0.73 (G)    │ 0.62 (Y)      │
│ c_003 │ 非人化认知建构     │ 1   │ 0.81 (G)    │ 0.55 (Y)      │
│ d_005 │ 宗教领袖煽动诠释   │ 2   │ 0.42 (Y)    │ 0.18 (R)      │
│ ...   │                    │     │             │               │
└───────┴────────────────────┴─────┴─────────────┴───────────────┘

Lowest consistency: d_005 (0.42)
Lowest essentialness: d_005 (0.18)
Weak chains in any: p_007, p_011
```

Color thresholds (Rich color cell):
- ≥ 0.7: green (G)
- 0.4 ≤ x < 0.7: yellow (Y)
- < 0.4: red (R)

### 5.3 Single rendering (详细报告)

```
ConsistencyReport: s_f3beb777 → c_001 绝对化价值框架 (L1)

  consistency_score:     0.73   (mean activation over reachable L0)
  essentialness_score:   0.62   (Σ contribution / |L0|)
  reachable L0:          5     [p_001, p_002, p_005, p_006, p_007]
  weak chains (<0.15):   2     [p_007 (act=0.08), p_011 (act=0.05)]

Contribution Breakdown:
  p_001 神圣不可妥协性          0.45
  p_002 最高奖赏激发狂热        0.38
  p_005 象征性目标放大化        0.30
  p_006 排他性群体认同          0.42
  p_007 世代仇恨积累            0.08

Decay Trace (top 8 by activation_after):
  depth  src     →   dst     edge       conf   act_before → act_after
  0      c_001       p_001   e_001      0.7    1.0        → 0.70
  0      c_001       p_002   e_002      0.7    1.0        → 0.70
  ...
```

`--trace-all` flag 显示全部 trace 而非默认 top 8。

### 5.4 命令位置 + Exit codes

`new` → `show` → `compress` → `run` → **`check`** ← Phase 6 新插 → `list`

Exit codes:
- 0: 正常 (含 "graph 无 L1/L2")
- 1: session not found
- 2: invalid target (不在 graph / level=0)

### 5.5 不动 `explain show`

之前 brainstorm 拒绝了 "show 内嵌 --check flag" 方案 (option C)。要看 consistency 走单独的 `explain check`, 分工:
- `show`: graph 现状
- `check`: graph 自检

---

## 6. 测试策略

### 6.1 新增测试文件 (~39 tests)

| 文件 | tests | 内容 |
|---|---|---|
| `test_engines_propagation.py` | ~13 | 算法核心, table-driven, 0 mock |
| `test_engines_simulation.py` | ~16 | API + edge cases, 含 1 个 perf-related (batch baseline 共用) |
| `test_cli_check.py` | ~7 | typer CliRunner + tmp_path + monkeypatch SESSIONS_DIR |
| `test_schema_graph_outgoing.py` | ~3 | outgoing_edges helper |

### 6.2 Test stack 特征

- **0 LLM mock** (Phase 6 不调 LLM)
- **0 async** (propagate / simulation / CLI 全 sync)
- **0 落盘**
- **Pure deterministic** — 没有 random / time / network

跟 Phase 5 大量 `@pytest.mark.asyncio + mock LLM` 复杂度对比, Phase 6 测试简单一档。

### 6.3 不做的事 (YAGNI)

- ❌ 不做 property-based testing (Hypothesis) — 范围小, table-driven 够
- ❌ 不做 perf benchmark (graph 几十节点 <25ms 不是 perf 问题)
- ❌ 不做 fuzz test
- ❌ 不 mock 任何 LLM (0 LLM)

### 6.4 跟 Phase 0-5 测试关系

- 不破任何现有 232 tests (Phase 6 不改 Phase 5 文件)
- 唯一改动: `schema/graph.py` 加 `outgoing_edges()` (read-only addition, 不破现有 graph 测试)
- Phase 6 完工总测试: **232 + 39 ≈ 271 PASS**

---

## 7. Acceptance plan

### 7.1 Acceptance target sessions

3 个跨议题 session 跑 batch check:

| Session | Question | Phase 5 driver 质量 | Phase 6 期望 |
|---|---|---|---|
| s_f3beb777 | 为什么宗教战争是最血腥的战争 | 9-driver 全定性扎实 | 整体 consistency 高 (0.6-0.85), 区分度小 |
| s_705f0435 | 特朗普访华影响 (方向 mismatch) | 8-driver 看着 ok 但跟原 question 半对不上 | consistency 中等, essentialness 区分大 |
| s_7d491774 | 2026-05-14 A 股 (LLM hallucinated L0) | 8-driver 拼凑常识, 跟编造 L0 弱关联 | **negative control**: 整体 consistency 应该低于 s_f3beb777 |

s_7d491774 作为 negative control 是关键 — 如果 Phase 6 真能识别 hallucinated graph 链条弱, 它的 score 应该明显低于 cleanly-built graph。这给我们一个客观的 "graph quality" 信号。

### 7.2 验收 checklist

- [ ] **算法 deterministic**: 同 session 跑 2 次结果完全一致
- [ ] **batch perf**: 单 session batch check (含 8-15 个 target) < 100ms
- [ ] **跨 session 信号区分**: s_7d491774 avg consistency < s_f3beb777 avg consistency
- [ ] **MAX_ACTIVE_VARIABLES=12 不过严**: decay_trace 看是否经常触发 top-k 剪枝
- [ ] **WEAK_CHAIN_THRESHOLD=0.15 合理**: weak_chains 输出不总是空 / 总是全部
- [ ] **essentialness 区分度**: 同 session 内不同 target 跨度 ≥ 0.2
- [ ] **L0 节点全 reachable**: batch baseline 后至少 90% L0 activation > 0
- [ ] **rendering 不破**: 中文 column 对齐, color threshold 正确, trace 不溢出
- [ ] **Phase 0-5 测试不破**: 271 PASS
- [ ] **ruff check 0 errors**

### 7.3 Tune-after-acceptance

跑完可能要 tune:
- `MAX_ACTIVE_VARIABLES`: 12 可能过严, 调到 16-20
- `WEAK_CHAIN_THRESHOLD`: 0.15 可能过宽/过窄

Tune 不是另一个 task, 写进 acceptance commit message (跟 Phase 5 GAIN_THRESHOLD 同处理)。

### 7.4 Acceptance evidence file

`docs/plans/2026-05-14-cognitive-engine-phase-6-acceptance.md`, 跟 Phase 5 acceptance file 风格一致:
- 跑法 + LLM provider (N/A, 0 LLM)
- 3 session 数据快照 (avg consistency / essentialness / lowest target / hallucination flag)
- 验收 checklist 全打勾
- 算法行为观察 (剪枝率 / 阈值触发率)
- Tune 决策 (如有)
- Phase 7 起点

---

## 8. Wave / task breakdown

### 8.1 总规模 vs Phase 5

| | Phase 5 | Phase 6 |
|---|---|---|
| Wave | 4 | 1 |
| Task | 10 | 5 |
| Step | ~100 | ~35 |
| LLM call | 真 LLM acceptance | 0 |
| 新 schema 字段 | 4 | 0 |
| 新 CLI 命令 | 1 + 改 1 | 1 |
| 测试增量 | +73 | +39 |

Phase 6 工作量 ≈ Phase 5 的 1/3 - 1/2。单 wave 跑完合理。

### 8.2 Task split

```
Wave A — Phase 6 Simulation Consistency
├── Task 6.1: schema/graph.py outgoing_edges() helper     (~5 step, 3 tests)
├── Task 6.2: engines/_propagation.py 算法 + 常量          (~10 step, 13 tests)
├── Task 6.3: engines/simulation.py API + ConsistencyReport (~10 step, 16 tests)
├── Task 6.4: cli.py explain check + rich rendering       (~5 step, 7 tests)
└── Task 6.5: Acceptance smoke on 3 sessions + tune + evidence (~5 step, 0 tests)
```

### 8.3 Task 依赖图 (线性, 不可并行)

```
6.1 outgoing_edges
    └─→ 6.2 propagation
            └─→ 6.3 simulation API
                    └─→ 6.4 CLI
                            └─→ 6.5 acceptance
```

每个 task TDD: failing test → impl → green → commit。

### 8.4 测试增量预期

| Task | 新 tests | 累计 (232 起) |
|---|---|---|
| 6.1 | +3 | 235 |
| 6.2 | +13 | 248 |
| 6.3 | +16 | 264 |
| 6.4 | +7 | 271 |
| 6.5 | +0 | 271 |

---

## 9. Phase 7 起点 (Phase 6 完工后)

Phase 6 完工后系统具备:
1. **Propagation algorithm 是 production-ready** — Phase 7 forward prediction 0 重写直接复用 `propagate(graph, sources)`
2. **Variable-level "structural quality" 信号** (consistency / essentialness) — Phase 7 Reflection Engine 可以读
3. **Acceptance 数据点** (3 session 的 score 分布) — 给 Phase 7 tune Reflection threshold 用

Phase 7 推荐方向 (跟 brainstorm 对齐):
- A) Forward Prediction (intervention → propagated effects, LLM 生新 predicted L0)
- B) Counterfactual Reasoning (Remove/Substitute, 跟 A 共 80% mechanics)
- + 必要时引入 Reflection Engine (用 consistency_score 调度)

**关键**: Phase 7 复用 `propagate(graph, sources)`, 加 wrap 层 (intervention parsing / LLM 生新 node / output rendering / HITL)。不重写算法。

---

## 附录 A: 跟顶层文档对齐表

| 顶层文档章节 | Phase 6 对齐方式 |
|---|---|
| §1 "不是 RAG Assistant" | 不引入 evidence anchoring / web search / external tool (废弃 RAG 方案见附录 B) |
| §2.4 认知不是搜索 | Phase 6 用 graph 已有结构跑数学 propagation, 不 query 外部 |
| §3.1 Variable 是机制变量 | 不改 Variable schema, evidence_ids 字段 (Phase 0 加的) Phase 6 不使用 |
| §4.5 Simulation Operator | Phase 6 实现简化版 (consistency check), forward prediction / counterfactual 推 Phase 7 |
| §8.1 explanation 必须能 rollout | **直接落地** — consistency_score 是 "能不能 rollout" 的量化 |
| §8.3 counterfactual thinking | Phase 6 C₂ essentialness 是 system-internal mini-counterfactual; 用户面向 counterfactual 留 Phase 7 |
| §9.1 Variable Lifecycle | consistency_score 是 stability_score 的真信号源, Phase 7+ 用它驱动 Decay / Death |
| §9.2 Variable Fitness | essentialness_score 对应 `explanatory_power` 字段, Phase 7+ 持久化时填 |
| §11.1 Propagation 公式 | 严格按 `State(t+1) = Propagation(State(t), ActiveRelations, AttentionField)` 实现 (no Attention 字段在 Phase 6 内, 用 multi-source uniform activation 简化) |
| §11.4 MAX_DEPTH=4 / MAX_ACTIVE=12 | 顶层默认值直接使用 |
| §11.5 Stability Regularization | Multiplicative 单边公式 (`act × confidence`) 实现 "低 confidence 衰减" |
| §12.2 Reflection Actions | Phase 6 不实现 Reflection; Phase 7+ Reflection 可以 trigger `check_consistency` 作为 sub-action |
| §13.1 Theory Discovery Pipeline | "Simulation Rollout" + "Counterfactual Testing" 两步骤分别对应 C₁ + C₂; Phase 8 Theory Formation 直接复用 |

---

## 附录 B: 废弃的 evidence 锚定方案 (RAG 方向)

### B.1 原始动机

用户报告两类痛点:
1. **时事性 hallucination**: 问 "2026-05-14 上午 A 股" 时 LLM 编造数据 (s_7d491774)
2. **方向 mismatch**: 问 "特朗普访华影响" 时 LLM 返结构性 explanation 而非 forward prediction (s_705f0435)

初次响应曾 brainstorm "evidence 锚定" 路线 (B+C 复合):
- B 路径: `explain ingest <file>` 用户喂数据
- C 路径: 接 5 个 tool (web_search / fetch_url / query_user_db / query_qdrant / news_rss), LLM 自主 tool-use

### B.2 为什么废弃

读顶层文档后发现 6 个 tension:

1. **顶层 §1** 明确列 "本项目并非 RAG Assistant" — evidence + tool 是 RAG pipeline 实质
2. **顶层 §2.4** "认知不是搜索" — `question → search → answer` 是被否定的范式
3. **顶层 §3.1** Variable 是机制变量, 不是 fact — evidence_ids 锚到 L1/L2 会让 abstraction 退化成关键词
4. **优先级颠倒**: 顶层 7 个 Operator (§4) 没 simulation 没 reflection 没 theory formation, 上 evidence 是 scope creep
5. **第一个议题 (s_705f0435) 实际痛点是 Simulation Engine 缺失**, evidence 锚定也救不了 (没有 forward simulation 还是 backward explanation)
6. **第二个议题 (s_7d491774) 本身就是 out-of-scope question** — 接受能力边界, 不该改架构去迁就时事 fact-finding

### B.3 联网查询将来如需怎么接 (Phase 9+)

明确 5 原则:

1. **联网 = 辅助 layer, 不是 system 核心** — 独立 `grounding/` 模块, 不进 cognitive runtime
2. **Fact 必须 factualize 转 variable, 不直接进 graph** — 原始 fact → LLM 转 → "可生成其他现象的机制变量"
3. **Grounding 受 Reflection Operator 调度, 不是 LLM 自主 tool-use** — 跟 §12.2 一致
4. **Grounding source 走 Adapter pattern, 可插拔** — `class GroundingSource(Protocol)`
5. **接入时机 Phase 9+** — Reflection (Phase 7) + Memory Consolidation (Phase 8 / 9) 都到位之后

详见 brainstorm session transcript。

### B.4 时事性议题的明确边界

`docs/` 或 README 应该注明:

> 系统适合: 历史/常识/结构性 why-questions ("为什么人会爱"、"为什么会议总是低效")
> 系统不适合: 实时分析 ("今天上午 A 股")、未来预测 ("访华会带来什么影响" — Phase 7 后部分支持)、依赖具体新近数据的题

Phase 6 完工 commit 顺便加这段到 README, 让用户提前避雷。

---

## 附录 C: Brainstorm 关键决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| Phase 6 范围 | C only (Simulation Consistency) | A/B 推 Phase 7 (需要 LLM 生新 node + CLI/HITL, 复杂度 3-4x); C 是 A/B 的核心 building block |
| 算法范式 | Pure rule-based (跳过 C₃ LLM diagnostic) | 跟顶层 §11.1 公式严格对齐; system-internal 反复 tune 不暴露给用户; deterministic 测试 |
| Score 指标 | C₁ + C₂ (consistency + essentialness) | C₁ 测链条 strength, C₂ 测 variable indispensability; 共用 propagation 函数 |
| Activation 持久化 | In-memory only | 跟 Phase 5 plausibility 风格一致; Phase 7+ 真需要再加 schema 字段 |
| Single-edge 公式 | Multiplicative (`act × confidence`) | 跟 §11.5 直接对应; 跨链 strength 衰减自然 |
| Multi-path 汇聚 | Noisy-OR | reward 多 parent 冗余, 跟 §13.2 recurrence 暗合 |
| 常量默认 | 顶层 §11.4 (DEPTH=4, ACTIVE=12) | 跟 doc 一致, acceptance 后 tune |
| Edge filter | causes / manifests_as | 当前 graph 只产出这两种, 其他 Phase 7+ 真出现再补 |
| C₂ propagation 起点 | Multi-source from all L1+L2 | 对称 + 跟 §11.3 Counterfactual 公式直接对应 |
| Expected L0 定义 | Reachable L0 (非全 graph L0) | C₁ 关心链条 strength 而非覆盖广度; 覆盖广度由 C₂ essentialness 反映 |
| CLI 形态 | 独立 `explain check` 命令 | 不内嵌 explain show, 分工清晰; 支持 batch 默认 |
| Batch 优化 | baseline 共用 (1 + 2N propagation) | 比 3N 节省 1/3; graph 几十节点共 <25ms |
| Wave 切分 | 单 Wave A, 5 task | 工作量小 + 线性依赖, 不值得拆 wave |
| 目录归属 | `engines/simulation.py` (非 `runtime/simulation/`) | 跟 expansion/compression 同级 (algorithm on graph), 不进 runtime loop |
