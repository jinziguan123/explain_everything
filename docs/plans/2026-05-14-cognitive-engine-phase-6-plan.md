# Cognitive Engine Phase 6 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Phase 6 design 实施落地 —— SimulationEngine Consistency Check (C₁ decay-aware reachability + C₂ counterfactual essentialness)。让系统从"画 graph"进化到"自检 graph 结构是否自洽", 直接落地顶层 §8.1 "如果 explanation 无法推演未来, 它可能不是真机制"。

**Architecture:** 5 task, TDD 流水线, **单 Wave A 线性执行**。Pure rule-based, 0 LLM call, 不动 schema 字段, 新增 1 个 CLI 命令 (`explain check`)。`engines/_propagation.py` 是算法核, `engines/simulation.py` 是 API 层, CLI 是 user-facing 入口。

**Tech Stack:** Python 3.11+ / dataclasses (frozen) / typer / rich / pytest / pytest-mock。Phase 0-5 完全复用, 无新增 dependency。

**Branch:** `dev` (latest: `03168e1` 设计 · Phase 6 Simulation Consistency Check)

**Design Doc:** [2026-05-14-cognitive-engine-phase-6-design.md](2026-05-14-cognitive-engine-phase-6-design.md)

**Phase 0-5 现状:** 232 tests pass, ruff 0 errors。3 个 converged session 可用 (s_f3beb777 / s_705f0435 / s_7d491774) 作 acceptance target。

---

## 与 Design Doc 的偏差说明

Plan 起草阶段, design doc 跟现有代码无 reconcile 缺口 (Phase 6 是纯增量, 0 schema 字段改动, 1 个 helper 加 + 1 个 engine 加 + 1 个 CLI 命令加)。如果实施中发现 reconcile, 在对应 Task 内 explicit 说明。

唯一明确的实现约定 (不算偏差):

1. **graph.outgoing_edges() 用 `Iterator[RelationEdge]` 返回**, 不是 `list` — 让 caller 灵活 (propagate 算法用 `for edge in graph.outgoing_edges(src)` 不需要 list materialization)
2. **CLI 测试 fixture 用 `SESSIONS_DIR` env var** (跟 conftest.py + Phase 5 test 一致, 不是顶层 design doc 没明说的 `EXPLAIN_SESSIONS_DIR`)
3. **acceptance Task 6.5 不写 test**, 而是 evidence document + 实际 CLI 跑通输出 (跟 Phase 5 Task 5.10 同处理)

---

## 任务索引

- **Wave A (单 wave, 线性依赖)**:
  - Task 6.1 schema/graph.py 加 `outgoing_edges(node_id)` helper (3 tests)
  - Task 6.2 engines/_propagation.py 算法 + 4 个常量 + DecayStep (13 tests)
  - Task 6.3 engines/simulation.py API + ConsistencyReport (16 tests)
  - Task 6.4 cli.py 加 `explain check` 命令 + rich rendering (7 tests)
  - Task 6.5 Acceptance smoke on 3 sessions + tune + evidence (0 tests)

总: 5 task / ~35 step / +39 tests (232 → 271 final)。

---

# Task 6.1: Schema — `ExplanationGraph.outgoing_edges(node_id)` helper

**目的**: 给 `ExplanationGraph` 加一个 read-only helper, 返某 node 的所有 outgoing edges。是 Task 6.2 propagation 算法的 dependency。Phase 5 加 `frontier_nodes()` 同性质 (read-only addition)。

**Files:**
- Modify: `src/explain_engine/schema/graph.py` (在 `frontier_nodes()` 方法附近加)
- Create: `tests/test_schema_graph_outgoing.py`

---

## Step 1: 写失败测试

Create `tests/test_schema_graph_outgoing.py`:

```python
"""ExplanationGraph.outgoing_edges(node_id) helper — Phase 6 propagation 用。"""

import pytest

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid: str, level: int = 1) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7, epistemic="insight",
    )


def _edge(eid: str, src: str, dst: str, rel: str = "manifests_as") -> RelationEdge:
    return RelationEdge(
        id=eid, source_node=src, target_node=dst,
        relation_type=rel, confidence=0.7,
        mechanism_description="m",
    )


class TestOutgoingEdges:
    def test_outgoing_edges_of_existing_node(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        g.add_node(_node("p_002", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001"))
        g.add_edge(_edge("e_002", "c_001", "p_002"))
        # 不该返 incoming edges
        g.add_node(_node("d_001", level=2))
        g.add_edge(_edge("e_003", "d_001", "c_001", rel="causes"))

        outs = list(g.outgoing_edges("c_001"))
        assert len(outs) == 2
        out_ids = sorted(e.id for e in outs)
        assert out_ids == ["e_001", "e_002"]

    def test_outgoing_edges_of_isolated_node_returns_empty(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        # 无 edges
        outs = list(g.outgoing_edges("c_001"))
        assert outs == []

    def test_outgoing_edges_of_nonexistent_node_raises(self) -> None:
        g = ExplanationGraph(root_question="why")
        with pytest.raises(ValueError, match="not found|不存在"):
            list(g.outgoing_edges("nonexistent"))
```

## Step 2: 跑测试验证失败

Run: `.venv/bin/python -m pytest tests/test_schema_graph_outgoing.py -v`
Expected: 3 个 ERROR / FAIL (`AttributeError: 'ExplanationGraph' object has no attribute 'outgoing_edges'`)。

## Step 3: 实现 outgoing_edges 方法

Modify `src/explain_engine/schema/graph.py` — 在 `frontier_nodes()` 方法下方加：

```python
def outgoing_edges(self, node_id: str) -> Iterator[RelationEdge]:
    """返 node_id 的所有 outgoing RelationEdge (Phase 6 propagation 用)。

    Raises:
        ValueError: node_id 不存在.
    """
    if node_id not in self._nodes:
        raise ValueError(f"node {node_id!r} not found in graph")
    for edge in self._edges.values():
        if edge.source_node == node_id:
            yield edge
```

顶部 import 加 `Iterator`:

```python
from collections.abc import Iterator, Mapping
```

(如果已有 `from collections.abc import Mapping`, 改成 `Iterator, Mapping`; 否则新加 import line)

## Step 4: 跑测试验证通过

Run: `.venv/bin/python -m pytest tests/test_schema_graph_outgoing.py -v`
Expected: 3 个 PASS。

## Step 5: 跑全测试 + ruff 确认不破

Run: `.venv/bin/python -m pytest tests/ -q --tb=short`
Expected: 235 PASS (232 + 3 新)。

Run: `.venv/bin/python -m ruff check src tests`
Expected: 0 errors。

## Step 6: Commit

```bash
git add tests/test_schema_graph_outgoing.py src/explain_engine/schema/graph.py
git commit -m "$(cat <<'EOF'
schema · ExplanationGraph.outgoing_edges() helper (Phase 6 propagation 用)

返某 node 的所有 outgoing RelationEdge (Iterator),
Phase 6 _propagation.propagate() 沿 forward edges traverse 用。

不存在的 node_id 抛 ValueError. 跟 Phase 5 frontier_nodes() 同性质
read-only addition, 不动现有 schema 字段.

测试: 3 PASS (existing / isolated / nonexistent). 全 235 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 6.2: `engines/_propagation.py` — 算法核心 + 4 个常量 + DecayStep

**目的**: Phase 6 算法核心 module。`propagate(graph, sources) → (activations, decay_trace)` 是 SimulationEngine 的唯一算法 dependency。Multi-source generalized, multiplicative single-edge, noisy-OR multi-path, 顶层 §11.4 默认 constraints。

**Files:**
- Create: `src/explain_engine/engines/_propagation.py`
- Create: `tests/test_engines_propagation.py`

---

## Step 1: 写 propagation basics 失败测试

Create `tests/test_engines_propagation.py`:

```python
"""propagate() 算法 unit tests — table-driven, 0 mock, 0 LLM。"""

import pytest

from explain_engine.engines._propagation import (
    DecayStep,
    propagate,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid: str, level: int = 1) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7, epistemic="insight",
    )


def _edge(
    eid: str, src: str, dst: str,
    rel: str = "manifests_as", conf: float = 0.7,
) -> RelationEdge:
    return RelationEdge(
        id=eid, source_node=src, target_node=dst,
        relation_type=rel, confidence=conf,
        mechanism_description="m",
    )


class TestPropagationBasics:
    def test_empty_sources_returns_empty(self) -> None:
        g = ExplanationGraph(root_question="why")
        acts, trace = propagate(g, set())
        assert acts == {}
        assert trace == []

    def test_missing_source_raises_value_error(self) -> None:
        g = ExplanationGraph(root_question="why")
        with pytest.raises(ValueError, match=r"sources not in graph"):
            propagate(g, {"nonexistent"})

    def test_source_with_no_outgoing_returns_source_only(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        acts, trace = propagate(g, {"c_001"})
        # source 自己 activation = 1.0
        assert acts == {"c_001": 1.0}
        assert trace == []
```

## Step 2: 跑测试验证失败

Run: `.venv/bin/python -m pytest tests/test_engines_propagation.py -v`
Expected: 3 个 ERROR (`ImportError: cannot import name 'DecayStep' from 'explain_engine.engines._propagation'`)。

## Step 3: 创建 _propagation.py 骨架 + DecayStep

Create `src/explain_engine/engines/_propagation.py`:

```python
"""Phase 6 Propagation 算法 — pure rule-based.

参考 docs/plans/2026-05-14-cognitive-engine-phase-6-design.md §3.

Multi-source forward propagation 沿 causes / manifests_as 边:
  - single-edge: act[child] = act[parent] × edge.confidence  (multiplicative)
  - multi-path: noisy-OR merge
  - 约束: MAX_DEPTH=4, MAX_ACTIVE_VARIABLES=12, PROPAGATION_THRESHOLD=0.05
  - cap: top-k by activation
"""

from __future__ import annotations

from dataclasses import dataclass

from explain_engine.schema.graph import ExplanationGraph

# ─── 常量 ───────────────────────────────────────────
PROPAGATION_THRESHOLD: float = 0.05
"""单边 propagation 后 activation < 阈值不再扩散 (BFS 剪枝)。"""

MAX_DEPTH: int = 4
"""顶层 §11.4. BFS 层数上限, 防爆。"""

MAX_ACTIVE_VARIABLES: int = 12
"""顶层 §11.4. 单层 (depth 内) 同时 active 节点数上限,
超过按 activation 降序取 top-k。"""

WEAK_CHAIN_THRESHOLD: float = 0.15
"""C₁ weak_chains 判定阈值: reachable_L0 中 activation < 此值的列入 weak_chains."""

FORWARD_RELATIONS: frozenset[str] = frozenset({"causes", "manifests_as"})
"""只沿这两种边 forward propagate.
contradicts / influences 当前 graph 没出现, Phase 7+ 真出现再补."""


# ─── DecayStep dataclass ──────────────────────────
@dataclass(frozen=True)
class DecayStep:
    """Propagation 路径上的一步, 用于 audit decay_trace。"""

    src: str
    dst: str
    edge_id: str
    activation_before: float
    edge_confidence: float
    activation_after: float
    depth: int


# ─── propagate 函数 ────────────────────────────────
def propagate(
    graph: ExplanationGraph,
    sources: set[str],
) -> tuple[dict[str, float], list[DecayStep]]:
    """Multi-source forward propagation. 详见 design §3."""
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
        candidates: list[tuple[str, str, "RelationEdge", float]] = []  # noqa: F821
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
            trace.append(
                DecayStep(
                    src=src,
                    dst=dst,
                    edge_id=edge.id,
                    activation_before=activations[src],
                    edge_confidence=edge.confidence,
                    activation_after=propagated,
                    depth=depth,
                )
            )

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

## Step 4: 跑 basics 测试通过

Run: `.venv/bin/python -m pytest tests/test_engines_propagation.py::TestPropagationBasics -v`
Expected: 3 PASS。

## Step 5: 写单边 propagation 测试

Append to `tests/test_engines_propagation.py`:

```python
class TestSingleEdgePropagation:
    def test_multiplicative_decay(self) -> None:
        """act[dst] = act[src] × edge.confidence"""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.7))

        acts, trace = propagate(g, {"c_001"})
        assert acts["c_001"] == 1.0
        assert abs(acts["p_001"] - 0.7) < 1e-9
        assert len(trace) == 1
        step = trace[0]
        assert step.src == "c_001"
        assert step.dst == "p_001"
        assert step.edge_id == "e_001"
        assert step.activation_before == 1.0
        assert step.edge_confidence == 0.7
        assert abs(step.activation_after - 0.7) < 1e-9
        assert step.depth == 0
```

## Step 6: 跑单边测试通过

Run: `.venv/bin/python -m pytest tests/test_engines_propagation.py -v`
Expected: 4 PASS (3 basics + 1 single edge)。

## Step 7: 写 noisy-OR 多路径测试

Append:

```python
class TestNoisyOR:
    def test_two_parents_combine_via_noisy_or(self) -> None:
        """两 parent 同时支持一个 child: act = 1 - (1-p1)(1-p2)"""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("c_002"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.7))
        g.add_edge(_edge("e_002", "c_002", "p_001", conf=0.5))

        acts, _ = propagate(g, {"c_001", "c_002"})
        # propagated_1 = 1.0 × 0.7 = 0.7
        # propagated_2 = 1.0 × 0.5 = 0.5
        # noisy-OR: 1 - (1-0.7)(1-0.5) = 1 - 0.15 = 0.85
        assert abs(acts["p_001"] - 0.85) < 1e-9

    def test_three_parents_combine(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("c_002"))
        g.add_node(_node("c_003"))
        g.add_node(_node("p_001", level=0))
        for src, eid, conf in (("c_001", "e_001", 0.5), ("c_002", "e_002", 0.5), ("c_003", "e_003", 0.5)):
            g.add_edge(_edge(eid, src, "p_001", conf=conf))

        acts, _ = propagate(g, {"c_001", "c_002", "c_003"})
        # noisy-OR: 1 - (1-0.5)^3 = 1 - 0.125 = 0.875
        assert abs(acts["p_001"] - 0.875) < 1e-9
```

## Step 8: 跑 noisy-OR 测试通过

Run: `.venv/bin/python -m pytest tests/test_engines_propagation.py -v`
Expected: 6 PASS。

## Step 9: 写多 hop chain 测试

Append:

```python
class TestMultiHopChain:
    def test_two_hop_decay_d_to_c_to_p(self) -> None:
        """driver → abstract → concrete (depth=2)"""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("d_001", level=2))
        g.add_node(_node("c_001", level=1))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "d_001", "c_001", rel="causes", conf=0.6))
        g.add_edge(_edge("e_002", "c_001", "p_001", rel="manifests_as", conf=0.7))

        acts, trace = propagate(g, {"d_001"})
        # depth 0: c_001 = 1.0 × 0.6 = 0.6
        # depth 1: p_001 = 0.6 × 0.7 = 0.42
        assert acts["d_001"] == 1.0
        assert abs(acts["c_001"] - 0.6) < 1e-9
        assert abs(acts["p_001"] - 0.42) < 1e-9
        assert len(trace) == 2
        assert [s.depth for s in trace] == [0, 1]

    def test_three_hop_decay(self) -> None:
        g = ExplanationGraph(root_question="why")
        for nid, lvl in (("a", 2), ("b", 2), ("c", 1), ("d", 0)):
            g.add_node(_node(nid, level=lvl))
        g.add_edge(_edge("e1", "a", "b", rel="causes", conf=0.9))
        g.add_edge(_edge("e2", "b", "c", rel="causes", conf=0.9))
        g.add_edge(_edge("e3", "c", "d", rel="manifests_as", conf=0.9))

        acts, _ = propagate(g, {"a"})
        # 0.9 × 0.9 × 0.9 = 0.729
        assert abs(acts["d"] - 0.729) < 1e-9
```

## Step 10: 跑多 hop 测试通过

Run: `.venv/bin/python -m pytest tests/test_engines_propagation.py -v`
Expected: 8 PASS。

## Step 11: 写 constraints (threshold + max_depth + max_active) 测试

Append:

```python
class TestConstraints:
    def test_threshold_prunes_weak_propagation(self, monkeypatch) -> None:
        """propagation < THRESHOLD 不扩散."""
        monkeypatch.setattr(
            "explain_engine.engines._propagation.PROPAGATION_THRESHOLD",
            0.5,
        )
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.3))  # 0.3 < 0.5

        acts, trace = propagate(g, {"c_001"})
        assert acts == {"c_001": 1.0}  # p_001 not added
        assert trace == []

    def test_max_depth_caps_chain(self, monkeypatch) -> None:
        """MAX_DEPTH=2 时 chain 第 3 跳不应该出现."""
        monkeypatch.setattr(
            "explain_engine.engines._propagation.MAX_DEPTH",
            2,
        )
        g = ExplanationGraph(root_question="why")
        for i in range(5):
            g.add_node(_node(f"n_{i}", level=1))
        for i in range(4):
            g.add_edge(_edge(f"e_{i}", f"n_{i}", f"n_{i+1}", conf=0.9))

        acts, _ = propagate(g, {"n_0"})
        # depth 0: n_1 = 0.9
        # depth 1: n_2 = 0.81
        # depth 2 cap → n_3 not reached
        assert "n_1" in acts
        assert "n_2" in acts
        assert "n_3" not in acts

    def test_max_active_top_k_pruning(self, monkeypatch) -> None:
        """MAX_ACTIVE=2 时单层只保留 top 2 by activation."""
        monkeypatch.setattr(
            "explain_engine.engines._propagation.MAX_ACTIVE_VARIABLES",
            2,
        )
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        confs = [0.9, 0.8, 0.7, 0.6, 0.5]
        for i, conf in enumerate(confs):
            g.add_node(_node(f"p_{i}", level=0))
            g.add_edge(_edge(f"e_{i}", "c_001", f"p_{i}", conf=conf))

        acts, _ = propagate(g, {"c_001"})
        # only top 2 (p_0 conf=0.9, p_1 conf=0.8) retained
        assert "p_0" in acts
        assert "p_1" in acts
        assert "p_2" not in acts
        assert "p_3" not in acts
        assert "p_4" not in acts
```

## Step 12: 跑 constraints 测试通过

Run: `.venv/bin/python -m pytest tests/test_engines_propagation.py -v`
Expected: 11 PASS。

## Step 13: 写 edge type filter + cycle + multi-source 测试

Append:

```python
class TestEdgeTypeFilter:
    def test_only_causes_and_manifests_as_propagate(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", rel="influences", conf=0.9))

        acts, trace = propagate(g, {"c_001"})
        # influences edge not propagated
        assert acts == {"c_001": 1.0}
        assert trace == []


class TestCycleHandling:
    def test_cycle_terminates_at_max_depth(self) -> None:
        """A → B → C → A 循环 graph, MAX_DEPTH cap 保证终止 (无死循环)."""
        g = ExplanationGraph(root_question="why")
        for nid in ("a", "b", "c"):
            g.add_node(_node(nid))
        g.add_edge(_edge("e_ab", "a", "b", conf=0.9))
        g.add_edge(_edge("e_bc", "b", "c", conf=0.9))
        g.add_edge(_edge("e_ca", "c", "a", conf=0.9))

        # 完成在 timeout 内即算 PASS
        acts, _ = propagate(g, {"a"})
        assert "a" in acts
        assert "b" in acts
        assert "c" in acts


class TestMultiSourcePropagation:
    def test_two_sources_simultaneous_starts(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("c_002"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.7))
        g.add_edge(_edge("e_002", "c_002", "p_001", conf=0.5))

        acts, _ = propagate(g, {"c_001", "c_002"})
        # 两 source 都 activation=1, noisy-OR 合并到 p_001
        assert acts["c_001"] == 1.0
        assert acts["c_002"] == 1.0
        # 1 - (1-0.7)(1-0.5) = 0.85
        assert abs(acts["p_001"] - 0.85) < 1e-9
```

## Step 14: 跑全部 _propagation 测试通过

Run: `.venv/bin/python -m pytest tests/test_engines_propagation.py -v`
Expected: 13 PASS。

## Step 15: 跑全测试 + ruff

Run: `.venv/bin/python -m pytest tests/ -q --tb=short`
Expected: 248 PASS (235 + 13 新)。

Run: `.venv/bin/python -m ruff check src tests`
Expected: 0 errors。如果有 unused import 报错, 删之 (常见: `# noqa: F821` 那行的 `RelationEdge` 字符串引用)。

## Step 16: Commit

```bash
git add tests/test_engines_propagation.py src/explain_engine/engines/_propagation.py
git commit -m "$(cat <<'EOF'
engines · _propagation.py (Phase 6 算法核心 + DecayStep + 4 常量)

Multi-source forward propagation 沿 causes / manifests_as 边:
- single-edge: act[child] = act[parent] × edge.confidence (multiplicative)
- multi-path: noisy-OR (1 - (1-a)(1-b))
- 约束: MAX_DEPTH=4, MAX_ACTIVE_VARIABLES=12, PROPAGATION_THRESHOLD=0.05
- top-k by activation 剪枝单层 > MAX_ACTIVE

常量 module-level 允许测试 monkeypatch (跟 Phase 5 runtime/stop 同处理).
DecayStep dataclass frozen, audit decay_trace 用.
0 LLM call, pure deterministic.

测试: 13 PASS (basics / single-edge / noisy-OR / multi-hop / constraints /
edge type filter / cycle / multi-source). 全 248 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 6.3: `engines/simulation.py` — API + ConsistencyReport

**目的**: 对外 API 层。`check_consistency(state, target_id)` 跑 C₁ + C₂, 返 `ConsistencyReport`. `check_consistency_batch()` 共用 baseline 优化。

**Files:**
- Create: `src/explain_engine/engines/simulation.py`
- Create: `tests/test_engines_simulation.py`

---

## Step 1: 写 validation 测试

Create `tests/test_engines_simulation.py`:

```python
"""SimulationEngine API tests."""

import pytest

from explain_engine.engines.simulation import (
    ConsistencyReport,
    check_consistency,
    check_consistency_batch,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _node(nid: str, level: int = 1) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7, epistemic="insight",
    )


def _edge(
    eid: str, src: str, dst: str,
    rel: str = "manifests_as", conf: float = 0.7,
) -> RelationEdge:
    return RelationEdge(
        id=eid, source_node=src, target_node=dst,
        relation_type=rel, confidence=conf,
        mechanism_description="m",
    )


def _make_state() -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(_node("p_001", level=0))
    g.add_node(_node("p_002", level=0))
    g.add_node(_node("c_001", level=1))
    g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.7))
    g.add_edge(_edge("e_002", "c_001", "p_002", conf=0.7))
    return CognitiveState(graph=g, budget_remaining=0, root_question="why")


class TestCheckConsistencyValidation:
    def test_target_not_in_graph_raises(self) -> None:
        state = _make_state()
        with pytest.raises(ValueError, match=r"not found in graph"):
            check_consistency(state, "nonexistent")

    def test_target_level_0_raises(self) -> None:
        """L0 是 ground truth, 不能被 check (会 trivial=1.0)."""
        state = _make_state()
        with pytest.raises(ValueError, match=r"level=0"):
            check_consistency(state, "p_001")
```

## Step 2: 跑测试验证失败

Run: `.venv/bin/python -m pytest tests/test_engines_simulation.py -v`
Expected: 2 个 ERROR (`ImportError: cannot import name 'check_consistency'`)。

## Step 3: 创建 simulation.py 骨架 + ConsistencyReport + validation

Create `src/explain_engine/engines/simulation.py`:

```python
"""Phase 6 SimulationEngine — Consistency Check (C₁ + C₂).

参考 docs/plans/2026-05-14-cognitive-engine-phase-6-design.md §4.

API:
  check_consistency(state, target_id) → ConsistencyReport
  check_consistency_batch(state, target_ids?) → list[ConsistencyReport]

Pure rule-based, 0 LLM call. L0 不可 check (是 ground truth).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from explain_engine.engines._propagation import (
    WEAK_CHAIN_THRESHOLD,
    DecayStep,
    propagate,
)
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState


@dataclass(frozen=True)
class ConsistencyReport:
    """单个 target 的 consistency check 结果。"""

    target_id: str
    consistency_score: float
    reachable_L0: list[str]
    weak_chains: list[str]
    essentialness_score: float
    contribution_breakdown: dict[str, float]
    decay_trace: list[DecayStep]


def _validate_target(state: CognitiveState, target_id: str) -> None:
    if target_id not in state.graph.nodes:
        raise ValueError(f"target {target_id!r} not found in graph")
    level = state.graph.nodes[target_id].abstraction_level
    if level == 0:
        raise ValueError(
            f"target {target_id!r} has level=0 (concrete), "
            f"only L1/L2 can be consistency-checked "
            f"(L0 is ground truth, not subject to verification)"
        )


def _get_all_L0(graph: ExplanationGraph) -> set[str]:
    return {nid for nid, n in graph.nodes.items() if n.abstraction_level == 0}


def _get_all_L1_L2(graph: ExplanationGraph) -> set[str]:
    return {nid for nid, n in graph.nodes.items() if n.abstraction_level >= 1}


def _check_with_baseline(
    state: CognitiveState,
    target_id: str,
    baseline_acts: dict[str, float] | None,
) -> ConsistencyReport:
    """单 target 的 C₁ + C₂. baseline 可传入 (batch 共用)."""
    graph = state.graph
    L0_nodes = _get_all_L0(graph)
    all_L1_L2 = _get_all_L1_L2(graph)

    # ─── C₁: single-source propagation ─────────────
    c1_acts, c1_trace = propagate(graph, {target_id})
    reachable_L0 = sorted(nid for nid in L0_nodes if c1_acts.get(nid, 0.0) > 0)
    if reachable_L0:
        consistency_score = sum(c1_acts[nid] for nid in reachable_L0) / len(reachable_L0)
    else:
        consistency_score = 0.0
    weak_chains = sorted(
        nid for nid in reachable_L0 if c1_acts[nid] < WEAK_CHAIN_THRESHOLD
    )

    # ─── C₂: counterfactual ────────────────────────
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


def check_consistency(state: CognitiveState, target_id: str) -> ConsistencyReport:
    """对单个 target (L1 abstract 或 L2 driver) 跑 C₁ + C₂.

    Raises:
        ValueError: target_id 不在 graph / level=0
    """
    _validate_target(state, target_id)
    return _check_with_baseline(state, target_id, baseline_acts=None)


def check_consistency_batch(
    state: CognitiveState,
    target_ids: Iterable[str] | None = None,
) -> list[ConsistencyReport]:
    """Batch check, baseline propagation 共用.

    Args:
        target_ids: None = 全 graph 所有 L1+L2 (按 id 升序); list = 指定.

    Raises:
        ValueError: 任一 target 不在 graph / level=0 (fail-fast).
    """
    if target_ids is None:
        target_id_list = sorted(_get_all_L1_L2(state.graph))
    else:
        target_id_list = list(target_ids)

    if not target_id_list:
        return []

    for tid in target_id_list:
        _validate_target(state, tid)

    all_L1_L2 = _get_all_L1_L2(state.graph)
    baseline_acts, _ = propagate(state.graph, all_L1_L2)

    return [
        _check_with_baseline(state, tid, baseline_acts)
        for tid in target_id_list
    ]
```

## Step 4: 跑 validation 测试通过

Run: `.venv/bin/python -m pytest tests/test_engines_simulation.py::TestCheckConsistencyValidation -v`
Expected: 2 PASS。

## Step 5: 写 C₁ consistency_score 测试

Append to `tests/test_engines_simulation.py`:

```python
class TestC1ConsistencyScore:
    def test_score_is_mean_over_reachable_L0(self) -> None:
        state = _make_state()
        report = check_consistency(state, "c_001")
        # propagate from c_001:
        # p_001: 1.0 × 0.7 = 0.7
        # p_002: 1.0 × 0.7 = 0.7
        # mean = 0.7
        assert sorted(report.reachable_L0) == ["p_001", "p_002"]
        assert abs(report.consistency_score - 0.7) < 1e-9

    def test_score_zero_when_no_reachable_L0(self) -> None:
        """target 没 outgoing forward edge → 0 reachable, score=0."""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
        report = check_consistency(state, "c_001")
        assert report.consistency_score == 0.0
        assert report.reachable_L0 == []
        assert report.weak_chains == []

    def test_weak_chains_below_threshold(self) -> None:
        """activation < WEAK_CHAIN_THRESHOLD (0.15) 的 reachable 列入 weak_chains."""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("c_002"))
        g.add_node(_node("p_strong", level=0))
        g.add_node(_node("p_weak", level=0))
        g.add_edge(_edge("e_001", "c_001", "c_002", conf=0.3))   # depth 0
        g.add_edge(_edge("e_002", "c_002", "p_weak", conf=0.3))  # depth 1: 1.0×0.3×0.3=0.09 < 0.15
        g.add_edge(_edge("e_003", "c_001", "p_strong", conf=0.8))  # depth 0: 0.8

        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
        report = check_consistency(state, "c_001")
        assert "p_weak" in report.weak_chains
        assert "p_strong" not in report.weak_chains

    def test_score_one_when_all_paths_perfect(self) -> None:
        """edge conf=1.0 → activation 不衰减."""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=1.0))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
        report = check_consistency(state, "c_001")
        assert abs(report.consistency_score - 1.0) < 1e-9
```

## Step 6: 跑 C₁ 测试通过

Run: `.venv/bin/python -m pytest tests/test_engines_simulation.py::TestC1ConsistencyScore -v`
Expected: 4 PASS。

## Step 7: 写 C₂ essentialness 测试

Append:

```python
class TestC2Essentialness:
    def test_high_when_target_unique_explainer(self) -> None:
        """target 单独覆盖某 L0, 删后 activation 大幅下降 → essentialness 高."""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.9))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")

        report = check_consistency(state, "c_001")
        # baseline = propagate({c_001}) = {c_001: 1, p_001: 0.9}
        # without = propagate(∅) = {}
        # contribution[p_001] = 0.9 - 0 = 0.9
        # essentialness = 0.9 / 1 = 0.9
        assert abs(report.essentialness_score - 0.9) < 1e-9
        assert abs(report.contribution_breakdown["p_001"] - 0.9) < 1e-9

    def test_zero_when_target_fully_redundant(self) -> None:
        """两 L1 都通过同 confidence edge 到同 L0 → 删一个 essentialness 偏低
        (但因 noisy-OR 非简单 max, 不会真为 0)."""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001"))
        g.add_node(_node("c_002"))
        g.add_node(_node("p_001", level=0))
        g.add_edge(_edge("e_001", "c_001", "p_001", conf=0.7))
        g.add_edge(_edge("e_002", "c_002", "p_001", conf=0.7))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")

        # baseline propagate {c_001, c_002}: p_001 = 1 - (1-0.7)(1-0.7) = 0.91
        # without c_001: propagate {c_002}: p_001 = 0.7
        # contribution[p_001] = 0.91 - 0.7 = 0.21
        report = check_consistency(state, "c_001")
        assert abs(report.essentialness_score - 0.21) < 1e-2  # noqa: PLR2004

    def test_contribution_per_concrete(self) -> None:
        """contribution_breakdown dict 完整列每个 L0 的差值."""
        state = _make_state()
        report = check_consistency(state, "c_001")
        assert set(report.contribution_breakdown.keys()) == {"p_001", "p_002"}
        for v in report.contribution_breakdown.values():
            assert v >= 0   # 删 target 后 activation 只可能降不可能升
```

## Step 8: 跑 C₂ 测试通过

Run: `.venv/bin/python -m pytest tests/test_engines_simulation.py::TestC2Essentialness -v`
Expected: 3 PASS。

## Step 9: 写 batch + edge case 测试

Append:

```python
class TestCheckConsistencyBatch:
    def test_batch_default_includes_all_L1_L2(self) -> None:
        """target_ids=None 默认跑全 L1+L2."""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("p_001", level=0))
        for cid in ("c_001", "c_002", "c_003"):
            g.add_node(_node(cid, level=1))
            g.add_edge(_edge(f"e_{cid}", cid, "p_001", conf=0.7))
        g.add_node(_node("d_001", level=2))
        g.add_edge(_edge("e_d_c", "d_001", "c_001", rel="causes", conf=0.6))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")

        reports = check_consistency_batch(state)
        # 应包含 c_001/c_002/c_003/d_001, 按 id 升序
        target_ids = [r.target_id for r in reports]
        assert target_ids == ["c_001", "c_002", "c_003", "d_001"]

    def test_batch_explicit_target_ids(self) -> None:
        state = _make_state()
        reports = check_consistency_batch(state, ["c_001"])
        assert len(reports) == 1
        assert reports[0].target_id == "c_001"

    def test_batch_empty_target_ids_returns_empty(self) -> None:
        state = _make_state()
        assert check_consistency_batch(state, []) == []

    def test_batch_no_L1_L2_in_graph_returns_empty(self) -> None:
        """graph 只有 L0 → batch 默认空."""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("p_001", level=0))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
        assert check_consistency_batch(state) == []

    def test_batch_fail_fast_on_invalid_target(self) -> None:
        state = _make_state()
        # 一个 valid + 一个 invalid → 应该 raise 而非 partial
        with pytest.raises(ValueError, match=r"not found"):
            check_consistency_batch(state, ["c_001", "nonexistent"])

    def test_batch_baseline_shared_optimization(self, mocker) -> None:
        """Batch N target 应该只算 1 次 baseline, 总 1+2N propagation."""
        from explain_engine.engines import simulation as sim_mod
        spy = mocker.spy(sim_mod, "propagate")

        g = ExplanationGraph(root_question="why")
        g.add_node(_node("p_001", level=0))
        for cid in ("c_001", "c_002", "c_003"):
            g.add_node(_node(cid, level=1))
            g.add_edge(_edge(f"e_{cid}", cid, "p_001", conf=0.7))
        state = CognitiveState(graph=g, budget_remaining=0, root_question="why")

        check_consistency_batch(state)
        # N=3: 1 baseline + 3 C₁ + 3 without = 7 propagations
        # (无 baseline 共用要 3 + 3 + 3 = 9)
        assert spy.call_count == 7


class TestDecayTraceContent:
    def test_returns_c1_trace_only_not_c2(self) -> None:
        """decay_trace 只含 C₁ propagation 的 step (从 target 起), 不含 C₂ baseline/without."""
        state = _make_state()
        report = check_consistency(state, "c_001")
        # C₁ propagate {c_001}: 2 step (c_001 → p_001, c_001 → p_002)
        assert len(report.decay_trace) == 2
        for step in report.decay_trace:
            assert step.src == "c_001"
```

## Step 10: 跑 batch + trace 测试通过

Run: `.venv/bin/python -m pytest tests/test_engines_simulation.py -v`
Expected: 16 PASS (2 validation + 4 C₁ + 3 C₂ + 6 batch + 1 trace)。

注: `test_batch_baseline_shared_optimization` 用 `mocker.spy` 数 `propagate` 调用次数, 需要 `simulation.py` 顶部从 `_propagation` import `propagate`, 而不是用 `_propagation.propagate(...)` (因为 `mocker.spy` 是基于 attribute, 必须在 `simulation` module 名空间内才能被 spy). 已在 Step 3 用 `from explain_engine.engines._propagation import propagate`, 符合。

## Step 11: 跑全测试 + ruff

Run: `.venv/bin/python -m pytest tests/ -q --tb=short`
Expected: 264 PASS (248 + 16 新)。

Run: `.venv/bin/python -m ruff check src tests`
Expected: 0 errors。

## Step 12: Commit

```bash
git add tests/test_engines_simulation.py src/explain_engine/engines/simulation.py
git commit -m "$(cat <<'EOF'
engines · simulation.py (Phase 6 ConsistencyReport + check_consistency API)

API:
  check_consistency(state, target_id) → ConsistencyReport
  check_consistency_batch(state, target_ids?) → list[ConsistencyReport]

ConsistencyReport (frozen dataclass):
  target_id / consistency_score / reachable_L0 / weak_chains /
  essentialness_score / contribution_breakdown / decay_trace

C₁ consistency_score = mean(c1_acts[nid] for nid in reachable_L0)
C₂ essentialness_score = sum(baseline_acts - without_acts over L0) / |L0|

L0 不可 check (raise ValueError, 因为 propagate from self trivial=1.0).
Batch fail-fast on invalid target (vs partial result).
Batch baseline 共用: 1 baseline + 2N propagation (vs 3N 重复).

测试: 16 PASS (2 validation + 4 C₁ + 3 C₂ + 6 batch + 1 decay_trace).
全 264 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 6.4: CLI — `explain check` 命令 + rich rendering

**目的**: 让用户能 `explain check <sid> [<var_id>]` 看 consistency report。Rich table 渲染, color threshold (green ≥ 0.7 / yellow ≥ 0.4 / red < 0.4)。不动 `explain show`。

**Files:**
- Modify: `src/explain_engine/cli.py` (加 `check` 命令在 `run` 后 `list_cmd` 前)
- Create: `tests/test_cli_check.py`

---

## Step 1: 写 CLI 失败测试 (happy path single + batch)

Create `tests/test_cli_check.py`:

```python
"""explain check <sid> [<var_id>] CLI 测试."""

from pathlib import Path

from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.persistence.session import (
    Session,
    SessionMeta,
    SessionStore,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _prepare_session(tmp_path: Path, sid: str = "s_aaaabbbb") -> str:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id="p_001", name="p1", description="d", abstraction_level=0,
        confidence=0.8, epistemic="observation",
    ))
    g.add_node(VariableNode(
        id="c_001", name="c1", description="d", abstraction_level=1,
        confidence=0.7, epistemic="insight",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m",
    ))
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    meta = SessionMeta(
        session_id=sid, question="why", stage="converged",
        created_at=1.0, updated_at=1.0,
    )
    SessionStore(directory=tmp_path).save(Session(meta=meta, state=state))
    return sid


def test_check_batch_happy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["check", sid])
    assert result.exit_code == 0, result.output
    # batch 渲染应该含 c_001
    assert "c_001" in result.output
    assert "Consistency" in result.output


def test_check_single_target_happy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["check", sid, "c_001"])
    assert result.exit_code == 0, result.output
    assert "c_001" in result.output
    # 详细模式应该含 decay trace 或 contribution
    assert "consistency_score" in result.output.lower() or "decay" in result.output.lower()
```

## Step 2: 跑测试验证失败

Run: `.venv/bin/python -m pytest tests/test_cli_check.py -v`
Expected: 2 个 FAIL (`Error: No such command 'check'`)。

## Step 3: 加 `check` 命令到 cli.py

Modify `src/explain_engine/cli.py` — 在 `run` 命令后, `list_cmd` 前插入:

```python
@app.command()
def check(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
    target_id: str | None = typer.Argument(
        None,
        help="单个 variable id (c_NNN / d_NNN). 不传则 batch check 全 L1+L2.",
    ),
    trace_all: bool = typer.Option(
        False, "--trace-all", help="渲染完整 decay_trace (默认 top 8)"
    ),
) -> None:
    """Phase 6 consistency check: 数学验证 abstract/driver 能否 rollout 出 concrete.

    Pure rule-based, 0 LLM call. 适合在已 converged session 上 audit graph 质量.

    Examples:
        explain check s_f3beb777              # batch: 全 graph L1+L2
        explain check s_f3beb777 c_001        # 单 target: 只 check c_001
    """
    from explain_engine.engines.simulation import (
        check_consistency,
        check_consistency_batch,
    )

    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    try:
        if target_id is None:
            reports = check_consistency_batch(session.state)
            if not reports:
                console.print("[yellow]graph 无 L1/L2 节点可 check[/yellow]")
                return
            _render_batch_reports(reports, session)
        else:
            report = check_consistency(session.state, target_id)
            _render_single_report(report, session, trace_all=trace_all)
    except ValueError as exc:
        console.print(f"[red]invalid target: {exc}[/red]")
        raise typer.Exit(2) from exc


def _color_for(score: float) -> str:
    if score >= 0.7:  # noqa: PLR2004
        return "green"
    if score >= 0.4:  # noqa: PLR2004
        return "yellow"
    return "red"


def _render_batch_reports(reports, session) -> None:
    g = session.state.graph
    n_L0 = sum(1 for n in g.nodes.values() if n.abstraction_level == 0)
    n_L1 = sum(1 for n in g.nodes.values() if n.abstraction_level == 1)
    n_L2 = sum(1 for n in g.nodes.values() if n.abstraction_level == 2)
    console.print(
        f"\n[bold]Consistency Check: {session.meta.session_id}[/bold]"
    )
    console.print(
        f"  (graph: {n_L0} L0 + {n_L1} L1 + {n_L2} L2 = {len(g.nodes)} nodes)\n"
    )

    table = Table()
    table.add_column("ID", style="cyan")
    table.add_column("名称", style="bold")
    table.add_column("Lvl", justify="right")
    table.add_column("Consistency", justify="right")
    table.add_column("Essentialness", justify="right")
    for r in reports:
        node = g.nodes[r.target_id]
        c_color = _color_for(r.consistency_score)
        e_color = _color_for(r.essentialness_score)
        table.add_row(
            r.target_id,
            node.name,
            str(node.abstraction_level),
            f"[{c_color}]{r.consistency_score:.2f}[/{c_color}]",
            f"[{e_color}]{r.essentialness_score:.2f}[/{e_color}]",
        )
    console.print(table)

    # quick summary 底部
    if reports:
        lowest_c = min(reports, key=lambda r: r.consistency_score)
        lowest_e = min(reports, key=lambda r: r.essentialness_score)
        all_weak: set[str] = set()
        for r in reports:
            all_weak.update(r.weak_chains)
        console.print(
            f"\nLowest consistency: {lowest_c.target_id} ({lowest_c.consistency_score:.2f})"
        )
        console.print(
            f"Lowest essentialness: {lowest_e.target_id} ({lowest_e.essentialness_score:.2f})"
        )
        if all_weak:
            console.print(f"Weak chains in any: {', '.join(sorted(all_weak))}")


def _render_single_report(report, session, trace_all: bool = False) -> None:
    g = session.state.graph
    node = g.nodes[report.target_id]
    console.print(
        f"\n[bold]ConsistencyReport: {session.meta.session_id} → "
        f"{report.target_id} {node.name} (L{node.abstraction_level})[/bold]\n"
    )

    c_color = _color_for(report.consistency_score)
    e_color = _color_for(report.essentialness_score)
    console.print(
        f"  consistency_score:    "
        f"[{c_color}]{report.consistency_score:.2f}[/{c_color}]   "
        f"(mean activation over reachable L0)"
    )
    console.print(
        f"  essentialness_score:  "
        f"[{e_color}]{report.essentialness_score:.2f}[/{e_color}]   "
        f"(Σ contribution / |L0|)"
    )
    console.print(
        f"  reachable L0:         {len(report.reachable_L0)}   {report.reachable_L0}"
    )
    if report.weak_chains:
        console.print(
            f"  weak chains (<0.15):  {len(report.weak_chains)}   {report.weak_chains}"
        )

    # contribution breakdown
    if report.contribution_breakdown:
        console.print("\n[bold]Contribution Breakdown[/bold] (baseline - without_target):")
        ct = Table()
        ct.add_column("L0 ID", style="cyan")
        ct.add_column("名称", style="dim")
        ct.add_column("Contribution", justify="right")
        for lid, contrib in sorted(
            report.contribution_breakdown.items(),
            key=lambda kv: -kv[1],
        ):
            node_name = g.nodes[lid].name if lid in g.nodes else lid
            ct.add_row(lid, node_name, f"{contrib:.2f}")
        console.print(ct)

    # decay trace
    if report.decay_trace:
        steps = report.decay_trace
        if not trace_all:
            steps = sorted(steps, key=lambda s: -s.activation_after)[:8]
            console.print(
                f"\n[bold]Decay Trace[/bold] (top 8 by activation_after, --trace-all 看完整):"
            )
        else:
            console.print("\n[bold]Decay Trace[/bold] (full):")
        tt = Table()
        tt.add_column("depth", justify="right")
        tt.add_column("src")
        tt.add_column("→")
        tt.add_column("dst")
        tt.add_column("edge")
        tt.add_column("conf", justify="right")
        tt.add_column("act_before", justify="right")
        tt.add_column("→")
        tt.add_column("act_after", justify="right")
        for s in steps:
            tt.add_row(
                str(s.depth),
                s.src,
                "→",
                s.dst,
                s.edge_id,
                f"{s.edge_confidence:.2f}",
                f"{s.activation_before:.2f}",
                "→",
                f"{s.activation_after:.2f}",
            )
        console.print(tt)
```

## Step 4: 跑 happy path 测试通过

Run: `.venv/bin/python -m pytest tests/test_cli_check.py::test_check_batch_happy_path tests/test_cli_check.py::test_check_single_target_happy_path -v`
Expected: 2 PASS。

## Step 5: 写其余 5 个测试 (not-found / level=0 / empty graph / etc)

Append to `tests/test_cli_check.py`:

```python
def test_check_session_not_found_exits_1(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["check", "s_99999999"])
    assert result.exit_code == 1


def test_check_target_not_in_graph_exits_2(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["check", sid, "c_999"])
    assert result.exit_code == 2


def test_check_target_level_0_exits_2(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["check", sid, "p_001"])
    assert result.exit_code == 2


def test_check_empty_graph_no_L1_L2(tmp_path, monkeypatch) -> None:
    """graph 只有 L0 → batch 输出空提示, exit 0."""
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id="p_001", name="p1", description="d", abstraction_level=0,
        confidence=0.8, epistemic="observation",
    ))
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    meta = SessionMeta(
        session_id="s_emptyL12", question="why", stage="converged",
        created_at=1.0, updated_at=1.0,
    )
    SessionStore(directory=tmp_path).save(Session(meta=meta, state=state))

    runner = CliRunner()
    result = runner.invoke(app, ["check", "s_emptyL12"])
    assert result.exit_code == 0
    assert "无 L1/L2" in result.output or "无 L1" in result.output


def test_check_renders_color_threshold_in_batch(tmp_path, monkeypatch) -> None:
    """batch 输出应该含 'Consistency' 表头和 score 数字."""
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["check", sid])
    assert result.exit_code == 0
    assert "Consistency" in result.output
    assert "Essentialness" in result.output
```

注: session_id 必须 `^s_[0-9a-f]{8}$`. `s_emptyL12` 含 't', 'L' — 不合规. 改成 `s_eeee0000` 之类的 8 hex.

```python
# 修正:
meta = SessionMeta(
    session_id="s_eeee0000", question="why", stage="converged",
    created_at=1.0, updated_at=1.0,
)
# ...
runner.invoke(app, ["check", "s_eeee0000"])
```

(如果先写测试再发现 id 不合规, 跑 step 6 时改, 不要 step 5 加 step 7)

## Step 6: 跑全部 CLI 测试通过

Run: `.venv/bin/python -m pytest tests/test_cli_check.py -v`
Expected: 7 PASS。

如果 session_id 不合规 raise → 改成 `s_eeee0000` 之类 8 hex 字符。

## Step 7: 跑全测试 + ruff

Run: `.venv/bin/python -m pytest tests/ -q --tb=short`
Expected: 271 PASS (264 + 7 新)。

Run: `.venv/bin/python -m ruff check src tests`
Expected: 0 errors。

## Step 8: Commit

```bash
git add tests/test_cli_check.py src/explain_engine/cli.py
git commit -m "$(cat <<'EOF'
cli · explain check + rich rendering (Phase 6)

explain check <session_id> [<target_id>] [--trace-all]:
- 不带 target_id: batch check 全 graph L1+L2 (按 id 升序)
- 带 target_id: 单 target 详细 report (含 contribution breakdown + decay trace)
- --trace-all flag: 渲染完整 decay_trace (默认 top 8)
- Color threshold: ≥0.7 green / ≥0.4 yellow / <0.4 red

Exit codes:
- 0: 正常 (含 "graph 无 L1/L2")
- 1: session not found
- 2: invalid target (不在 graph / level=0)

不动 explain show (分工清晰: show=现状, check=自检).

测试: 7 PASS (batch / single / not-found / target-not-found / level-0 /
empty-graph / color-threshold). 全 271 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 6.5: Acceptance smoke on 3 sessions + tune + evidence

**目的**: 跑 `explain check` on 3 个 converged sessions (s_f3beb777 / s_705f0435 / s_7d491774), 验证算法行为, 写 acceptance evidence file。s_7d491774 (LLM hallucinated A 股 session) 作 negative control — 期望它的 consistency 平均显著低于 s_f3beb777 (cleanly-built).

**Files:**
- Read-only: `sessions/s_f3beb777.json` / `s_705f0435.json` / `s_7d491774.json` (已有)
- Create: `docs/plans/2026-05-14-cognitive-engine-phase-6-acceptance.md`
- (可能 Modify): `src/explain_engine/engines/_propagation.py` 调常量 (跟 Phase 5 GAIN_THRESHOLD 同处理)

---

## Step 1: 跑 batch check on s_f3beb777 (cleanly-built control)

```bash
.venv/bin/python -m explain_engine.cli check s_f3beb777 2>&1 | tee /tmp/check_f3beb777.txt
```

Expected:
- exit 0
- batch 表含 c_001 / c_003 / c_004 (3 L1) + d_001-d_NNN (Phase 5 加的 8-9 driver)
- 整体 consistency 大致 0.5-0.85, essentialness 大致 0.3-0.7
- 0 LLM call (秒级完成 < 100ms)

记录 average consistency / average essentialness / lowest target。

## Step 2: 跑 batch check on s_705f0435 (mixed)

```bash
.venv/bin/python -m explain_engine.cli check s_705f0435 2>&1 | tee /tmp/check_705f0435.txt
```

记录数据。期望 essentialness 区分度大 (driver 间冗余多)。

## Step 3: 跑 batch check on s_7d491774 (hallucinated control)

```bash
.venv/bin/python -m explain_engine.cli check s_7d491774 2>&1 | tee /tmp/check_7d491774.txt
```

记录数据。**关键验证**: avg consistency 应该明显低于 s_f3beb777 (假设 hallucinated graph 链条更弱)。

## Step 4: 跑 single-target 详细 trace 看几个有趣的

```bash
# s_f3beb777 c_001: 期望强 chain
.venv/bin/python -m explain_engine.cli check s_f3beb777 c_001

# s_f3beb777 d_005 (Phase 5 reasoner 给 gain=0.70): 期望中等 consistency
.venv/bin/python -m explain_engine.cli check s_f3beb777 d_005

# s_7d491774 c_001: 期望低 consistency (暴露 hallucination)
.venv/bin/python -m explain_engine.cli check s_7d491774 c_001
```

记录每个的 consistency_score / weak_chains / decay_trace 概况。

## Step 5: 分析 + tune 决策

读 3 个 check 输出, 看:

1. **MAX_ACTIVE_VARIABLES=12 是否过严**? 看 batch baseline 跑时, decay_trace 是否经常因 top-k 剪枝丢节点。如果是, 调到 16 或 20。
2. **WEAK_CHAIN_THRESHOLD=0.15 是否合理**? 看 weak_chains 输出: 是否经常 [] (太严) 或经常列全部 reachable (太宽)。如果是, 调到 0.10 或 0.20。
3. **跨 session 区分度**: s_7d491774 avg consistency < s_f3beb777 avg consistency? 如果**不是** (e.g. s_7d491774 score 反而更高), 算法可能无效, 需要重新审 algorithm 设计 — 暂停 Phase 6 跟用户对齐。

如果 tune 决策非 trivial, **在 commit message 说明**, 改 `_propagation.py` 常量, 重跑 3 个 check 验证修正后行为符合预期。

如果不 tune, skip 改动。

## Step 6: 跑全测试 + ruff 一次 (确认 tune 没破现有 test)

如果 Step 5 改了常量:

Run: `.venv/bin/python -m pytest tests/ -q --tb=short`
Expected: 271 PASS (constants 改动不会破 test, 因为 propagation test monkeypatch override 默认值)。

Run: `.venv/bin/python -m ruff check src tests`
Expected: 0 errors。

## Step 7: 写 acceptance evidence file

Create `docs/plans/2026-05-14-cognitive-engine-phase-6-acceptance.md`:

```markdown
# Phase 6 Acceptance — Simulation Consistency Check on 3 sessions

**日期**: 2026-05-14 (实际执行日期)
**Sessions**: s_f3beb777 / s_705f0435 / s_7d491774
**LLM provider**: N/A (Phase 6 pure rule-based, 0 LLM call)
**Tune**: (如改了常量, 写明; 否则: "顶层 §11.4 默认值未调")

## 跑法

```bash
explain check s_f3beb777
explain check s_705f0435
explain check s_7d491774

# 单 target 详细 trace
explain check s_f3beb777 c_001
explain check s_f3beb777 d_005
explain check s_7d491774 c_001
```

## 数据快照

| Session | Question | n L0/L1/L2 | Avg Consistency | Avg Essentialness | Lowest C target | Hallucination flag |
|---|---|---|---|---|---|---|
| s_f3beb777 | 为什么宗教战争最血腥 | 12/3/9 | ? | ? | ? | No (Phase 5 已审) |
| s_705f0435 | 特朗普访华影响 | 9/3/8 | ? | ? | ? | Mixed (方向 mismatch) |
| s_7d491774 | 2026-05-14 A 股 | 13/3/8 | ? | ? | ? | Yes (LLM 编 L0) |

## 验收 checklist (跟 design §7.3 同步)

- [ ] 算法 deterministic (跑 2 次结果一致)
- [ ] batch perf < 100ms / session
- [ ] s_7d491774 avg consistency **<** s_f3beb777 avg consistency (negative control 关键)
- [ ] MAX_ACTIVE_VARIABLES=12 不过严 (剪枝触发率 < 30%)
- [ ] WEAK_CHAIN_THRESHOLD=0.15 合理 (非全空非全满)
- [ ] essentialness 区分度 (同 session 内 跨度 ≥ 0.2)
- [ ] L0 节点 ≥ 90% reachable (说明 graph connected)
- [ ] rendering: 中文 column 对齐, color threshold 正确, trace 不溢出
- [ ] Phase 0-5 + Phase 6 全 271 PASS
- [ ] ruff check 0 errors

## 算法行为观察

(填实际跑出的细节: MAX_ACTIVE 剪枝率, weak threshold 触发情况, decay trace 长度典型值, etc)

## Tune 决策

(如有改常量, 写明改动 + 重测前后对比; 如无, "顶层默认值经验证够用")

## Phase 7 起点

Phase 6 完工后系统具备:
1. Propagation algorithm production-ready, Phase 7 forward prediction 0 重写直接复用
2. Variable-level structural quality 信号 (consistency / essentialness) 可供 Phase 7+ Reflection Engine 调度
3. 3 session 的 score 分布数据点, 给 Phase 7 tune Reflection threshold 用

Phase 7 推荐方向 (跟 design §9 对齐):
- A) Forward Prediction (intervention → propagated effects, LLM 生新 predicted L0)
- B) Counterfactual Reasoning (Remove/Substitute, 跟 A 共 80% mechanics)
- + 必要时引入 Reflection Engine (consistency_score 调度)
```

(把 `?` 替换成实际数据)

## Step 8: Commit acceptance evidence

```bash
# 如果 Step 5 改了常量, 一起加进 stage
git add docs/plans/2026-05-14-cognitive-engine-phase-6-acceptance.md
# (可选) git add src/explain_engine/engines/_propagation.py

git commit -m "$(cat <<'EOF'
acceptance · Phase 6 Simulation Consistency Check evidence (3 sessions)

跑 explain check on s_f3beb777 / s_705f0435 / s_7d491774:
- s_f3beb777 (cleanly-built control): avg consistency=X.XX, essentialness=X.XX
- s_705f0435 (mixed): avg=X.XX, essentialness=X.XX
- s_7d491774 (hallucinated control): avg=X.XX, essentialness=X.XX

Negative control 验证: s_7d491774 avg consistency 显著 < s_f3beb777
(差 X.XX), 说明 Phase 6 算法能识别 hallucinated graph 链条弱.

跨 session 区分度: 同 session 内 essentialness 跨度 X.XX-Y.YY
(满足 ≥ 0.2 要求).

Tune: (若有, 写明 e.g. "MAX_ACTIVE 12→16 剪枝率从 35% 降到 12%";
若无, "顶层 §11.4 默认值未调")

Phase 0-5 + Phase 6 全 271 PASS, ruff 0.

参考: design doc §7 acceptance plan + checklist.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 总结

Phase 6 plan: **5 task / ~35 step / +39 tests** (271 final), 单 Wave A 线性执行。

节奏:
- Task 6.1 (~5 step): graph helper, dependency for 6.2
- Task 6.2 (~16 step): 算法核心, 最大 task (13 tests)
- Task 6.3 (~12 step): API + ConsistencyReport
- Task 6.4 (~8 step): CLI + rendering
- Task 6.5 (~8 step): acceptance smoke + tune + evidence

预期总测试: 232 + 39 = **271 PASS**, ruff 0 errors。

预期 LLM cost: **$0** (Phase 6 pure rule-based)。

Acceptance 验证关键: **s_7d491774 avg consistency < s_f3beb777 avg consistency** —— 如果不满足, 算法 design 失败, 需要 stop + revisit design doc §3-§4.
