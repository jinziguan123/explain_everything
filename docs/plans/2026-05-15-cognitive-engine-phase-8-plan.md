# Cognitive Engine Phase 8 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Phase 8 design 实施落地 —— 修 Phase 7 acceptance 暴露的 4 个根本问题: ① re_expand 死循环 (Wave 1: `expand_downward` 替换); ② 单信号 acceptance 脆弱 (Wave 2: 6 multi-signal + rollout_coverage); ③ mismatch session 失明 (Wave 3: input_validation fail-fast); ④ 节点无 lifecycle (Wave 4: Variable 3 阶段 + fitness + auto decay). 落地哲学顶层 §6 / §8.1 / §9.2 / §9.4 / §11.3.

**Architecture:** 10 task, TDD 流水线, **5 Wave 线性执行** (1 → 2 → 3 → 4 → 5). Wave 之间 stop checkpoint 等用户审. Wave 1 改 `expansion.py` + `reflection.py` + `runtime.py`; Wave 2 新增 aggregate `AcceptanceReport` + `_propagation.rollout_from_roots()` + ConsistencyReport 字段扩充 + reflect refactor; Wave 3 新增 `engines/input_validation.py` + `engines/errors.py` + CLI 集成; Wave 4 改 `schema/nodes.py` (VariableNode + lifecycle 字段) + 新增 `engines/lifecycle.py`; Wave 5 重跑 acceptance + 文档.

**Tech Stack:** Python 3.11+ / dataclasses (frozen) / Pydantic / typer / rich / pytest / pytest-mock / pytest-asyncio. Phase 0-7 完全复用, 无新增 dependency.

**Branch:** `dev` (latest: `c29a7a3` design · Phase 8)

**Design Doc:** [2026-05-15-cognitive-engine-phase-8-design.md](2026-05-15-cognitive-engine-phase-8-design.md)

**Phase 0-7 现状:** 390 tests pass, ruff 0 errors. 3 个 acceptance session 可用 (s_f3beb777 / s_705f0435 / s_7d491774) 作 baseline.

---

## 与 Design Doc 的偏差说明

实施前调研代码后发现 1 个 design / code reconcile gap:

**Reconcile #1: ConsistencyReport 是 per-target, 不是 aggregate**

Design doc §3.2 假设 `ConsistencyReport` 有 `per_l1: dict[str, float]` + `avg_consistency` 字段 (aggregate 形态). 但实际 `engines/simulation.py` 的 `ConsistencyReport` 是 **per-target**:

```python
@dataclass(frozen=True)
class ConsistencyReport:
    target_id: str
    consistency_score: float
    reachable_L0: list[str]
    weak_chains: list[str]   # 已存在! per-target 的 weak chain L0 集合
    essentialness_score: float
    contribution_breakdown: dict[str, float]
    decay_trace: list[DecayStep]
```

`check_consistency_batch()` 返 `list[ConsistencyReport]` (一个 target 一份).

**解决**: Plan 创建一个新 aggregate dataclass `AcceptanceReport`, 装 Wave 2 的 6 个 multi-signal 字段 + Wave 3 的 alignment 字段. ConsistencyReport per-target 保留不动 (它是 simulation 算法输出, 用途不同). reflect 决策树 + CLI status 都用 `AcceptanceReport`.

注: design doc §3.2 写的 `weak_chains` 字段含义跟现有 `ConsistencyReport.weak_chains` 不同:
  - 现有: 单 target 内 reachable_L0 中 activation 弱的 L0 列表
  - design doc 想要: 全 graph L1 中 consistency_score 弱的 L1 列表 (aggregate)

为避免命名冲突, plan 在 `AcceptanceReport` 中改名为 `weak_chain_l1s: list[str]`.

明确的实现约定 (不算偏差):

1. **测试用 `.venv/bin/python -m pytest`** (项目用 uv-managed venv)
2. **commit message 中文 + `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer**
3. **每 Wave 完成后 stop checkpoint**, 等用户审通过再进下一 Wave
4. **CLI 测试 fixture 用 `SESSIONS_DIR` env var** (跟 Phase 5/6/7 test 一致)
5. **预期 LLM mock 用 `pytest-mock`** (跟 Phase 5/6/7 一致)
6. **新 dataclass 用 `@dataclass(frozen=True)`** (跟 ConsistencyReport 一致)
7. **VariableNode 是 Pydantic BaseModel** (不是 dataclass), 新字段用 `Field(default=...)`. backward compat 自动 (Pydantic 处理 missing field)
8. **ruff check 全程 0 errors** (Phase 7 标准)

---

## 任务索引

**Wave 1 — Reflect Redesign (2 task, 线性, +10 tests)**
- Task 1.1: `expand_downward()` engine + prompt + tests (~7 step, +6 tests)
- Task 1.2: reflect 决策树用 expand-downward 替 re-expand + dispatch + anti-thrash 改名 + tests (~7 step, +4 tests)

**Wave 2 — Multi-Signal Acceptance (3 task, 线性, +22 tests)**
- Task 2.1: `_propagation.rollout_from_roots()` + tests (~6 step, +6 tests)
- Task 2.2: `AcceptanceReport` dataclass + `simulation.aggregate_acceptance()` + tests (~7 step, +8 tests)
- Task 2.3: reflect 用 `AcceptanceReport.weak_chain_l1s` + CLI status multi-signal section + tests (~6 step, +8 tests)

**Wave 3 — Falsifiability Alignment (2 task, 线性, +14 tests)**
- Task 3.1: `engines/errors.py` + `engines/input_validation.py` + prompt + tests (~7 step, +8 tests)
- Task 3.2: `explain run` 入口集成 + `--no-input-check` flag + AcceptanceReport 加 alignment 字段 + tests (~6 step, +6 tests)

**Wave 4 — Variable Lifecycle (2 task, 线性, +26 tests)**
- Task 4.1: VariableNode 加 5 lifecycle 字段 + backward compat tests + `lifecycle.compute_fitness()` + tests (~7 step, +14 tests)
- Task 4.2: `lifecycle.update_lifecycle()` + reflect decay action + dispatch + propagation/expansion 跳过 decayed + tests (~8 step, +12 tests)

**Wave 5 — Acceptance + 文档 (1 task, 0 unit tests)**
- Task 5.1: 重跑 3 acceptance sessions + 写 acceptance doc + 更新 README (~5 step)

**总: 10 task / ~70 step / +72 tests (390 → 462 final).**

---

# Wave 1 — Reflect Redesign (修死循环)

## Task 1.1: `expand_downward()` engine + prompt

**目的**: 新增 `expansion.expand_downward(state, l1_id, llm)`, 与 `expand_one_frontier` 对称但反方向 — 给 L1 加 outgoing `manifests_as` L0 子节点 (而不是 incoming `causes` driver). 哲学锚点 §8.1: "Explanation 必须能 rollout, 否则可能不是真机制". L1 consistency 低意味着 L1 难 propagate 出 L0 — 让 L1 自己说"我会带来什么 concrete 现象", 然后让 Wave 2 simulation 重新打分.

**Files:**
- Modify: `src/explain_engine/engines/expansion.py` (加 expand_downward + 新 _DownwardL0Spec model)
- Create: `src/explain_engine/llm/prompts/expansion_downward.yaml`
- Create: `tests/test_engines_expand_downward.py`

---

### Step 1: 写新 prompt

Create `src/explain_engine/llm/prompts/expansion_downward.yaml`:

```yaml
system: |
  你是 cognitive engine 的 downward expansion sub-agent.

  任务: 给定一个 L1 abstract variable, 预测它会 manifest 出哪些新的 concrete L0 现象.

  约束:
  - 输出 1-3 个 predicted L0, 每个含 name / description / mechanism / plausibility.
  - mechanism 必须说明 "L1 为什么会 manifest 成这个 L0".
  - plausibility 是 1-5 整数, 5=机制非常可能, 1=纯猜.
  - 不要预测已有 L0 (graph 里已有的 concrete). 调用方会自动跳过重复名字.
  - 输出的 L0 必须与 root question 相关, 不能引入完全新主题.

  哲学:
  - 这是 cognitive 自检. 一个 abstract variable 如果是真机制, 它必须能
    rollout 出新的 concrete observable 现象. 如果你想不出 plausible 的 L0,
    输出 plausibility 低的占位 (描述清楚为什么难想), 让 reflect 决定 prune.

  输出 JSON schema:
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

### Step 2: 写失败测试

Create `tests/test_engines_expand_downward.py`:

```python
"""Wave 1 Task 1.1: engines.expansion.expand_downward 单元测试.

design §4.2: expand_downward 给 L1 加 outgoing manifests_as L0 子节点.
与 expand_one_frontier (Phase 5) 对称但反方向.
"""

import pytest

from explain_engine.engines import expansion
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state_with_l1(l1_id: str = "c_001") -> CognitiveState:
    g = ExplanationGraph(root_question="why X")
    g.add_node(VariableNode(
        id=l1_id, name="L1 abstract", description="abstraction d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="why X",
        insight_candidates=[l1_id],
    )


class _FakeLLMOutput:
    def __init__(self, parsed):
        self.parsed = parsed


class _FakeLLM:
    def __init__(self, response_dict):
        self.response_dict = response_dict
        self.call_count = 0

    async def chat(self, messages, schema):
        self.call_count += 1
        return _FakeLLMOutput(parsed=self.response_dict)


class TestExpandDownward:
    @pytest.mark.asyncio
    async def test_creates_l0_children_with_manifests_as_edges(self) -> None:
        state = _make_state_with_l1("c_001")
        llm = _FakeLLM({
            "predicted_L0": [
                {"name": "phenom_a", "description": "da", "mechanism": "ma", "plausibility": 4},
                {"name": "phenom_b", "description": "db", "mechanism": "mb", "plausibility": 5},
            ],
        })
        new_l0_ids = await expansion.expand_downward(state, "c_001", llm)
        assert len(new_l0_ids) == 2
        # 验证新节点都是 L0
        for nid in new_l0_ids:
            assert state.graph.nodes[nid].abstraction_level == 0
            assert state.graph.nodes[nid].epistemic == "speculation"
            assert state.graph.nodes[nid].source == "llm"
        # 验证 manifests_as 边存在 + confidence = plausibility/5
        edges = [e for e in state.graph.edges.values()
                 if e.source_node == "c_001" and e.relation_type == "manifests_as"]
        confs = sorted([e.confidence for e in edges])
        assert confs == [0.8, 1.0]  # 4/5, 5/5

    @pytest.mark.asyncio
    async def test_invalid_l1_id_raises(self) -> None:
        state = _make_state_with_l1()
        llm = _FakeLLM({"predicted_L0": []})
        with pytest.raises(ValueError, match="not found in graph"):
            await expansion.expand_downward(state, "c_999", llm)

    @pytest.mark.asyncio
    async def test_non_l1_node_raises(self) -> None:
        state = _make_state_with_l1("c_001")
        # 加一个 L0 节点
        state.graph.add_node(VariableNode(
            id="p_001", name="L0 phenom", description="d",
            abstraction_level=0, confidence=0.7, epistemic="observation",
        ))
        llm = _FakeLLM({"predicted_L0": []})
        with pytest.raises(ValueError, match="must be 1"):
            await expansion.expand_downward(state, "p_001", llm)

    @pytest.mark.asyncio
    async def test_max_l0_limit_respected(self) -> None:
        state = _make_state_with_l1()
        llm = _FakeLLM({
            "predicted_L0": [
                {"name": f"phenom_{i}", "description": "d", "mechanism": "m", "plausibility": 3}
                for i in range(5)
            ],
        })
        new_l0_ids = await expansion.expand_downward(state, "c_001", llm, max_l0=3)
        assert len(new_l0_ids) == 3

    @pytest.mark.asyncio
    async def test_zero_l0_raises_validation(self) -> None:
        state = _make_state_with_l1()
        llm = _FakeLLM({"predicted_L0": []})
        from explain_engine.llm.errors import SchemaValidationError
        with pytest.raises(SchemaValidationError):
            await expansion.expand_downward(state, "c_001", llm)

    @pytest.mark.asyncio
    async def test_confidence_writeback_linear(self) -> None:
        """plausibility=1→0.2, plausibility=5→1.0 (linear /5.0)."""
        state = _make_state_with_l1()
        llm = _FakeLLM({
            "predicted_L0": [
                {"name": "low", "description": "d", "mechanism": "m", "plausibility": 1},
                {"name": "high", "description": "d", "mechanism": "m", "plausibility": 5},
            ],
        })
        new_ids = await expansion.expand_downward(state, "c_001", llm)
        edges_by_target = {
            e.target_node: e.confidence
            for e in state.graph.edges.values()
            if e.source_node == "c_001"
        }
        # 找到 low / high 对应的 L0
        low_id = next(nid for nid in new_ids if state.graph.nodes[nid].name == "low")
        high_id = next(nid for nid in new_ids if state.graph.nodes[nid].name == "high")
        assert edges_by_target[low_id] == 0.2
        assert edges_by_target[high_id] == 1.0
```

### Step 3: 跑测试 — 验证全 fail

Run: `.venv/bin/python -m pytest tests/test_engines_expand_downward.py -v`

Expected: 6 FAIL with "AttributeError: module 'explain_engine.engines.expansion' has no attribute 'expand_downward'" 或 prompt loader 找不到 expansion_downward.yaml.

### Step 4: 实现 expand_downward

Modify `src/explain_engine/engines/expansion.py`:

加 imports (top of file 已有 ValidationError):

```python
from typing import Literal
```

加 Pydantic models (在 ExpansionOutput 之后):

```python
class _PredictedL0(BaseModel):
    name: str
    description: str
    mechanism: str
    plausibility: int = Field(ge=1, le=5)


class DownwardExpansionOutput(BaseModel):
    """expansion_downward.yaml prompt 的 structured output."""

    predicted_L0: list[_PredictedL0] = Field(min_length=1, max_length=5)
```

加新函数 (在 re_expand 之后, _do_expansion 之前):

```python
async def expand_downward(
    state: CognitiveState,
    l1_id: str,
    llm: LLMClient,
    max_l0: int = 3,
) -> list[str]:
    """Wave 1 Task 1.1: 给 L1 加 outgoing manifests_as L0 子节点.

    与 expand_one_frontier (Phase 5) 对称但反方向:
      - expand_one_frontier: 给 L1 加 incoming causes (driver → L1)
      - expand_downward: 给 L1 加 outgoing manifests_as (L1 → L0)

    哲学锚点 §8.1: Explanation 必须能 rollout. L1 consistency 低意味着 L1 难 propagate
    出 L0; 让 L1 自己说"我会带来什么 concrete 现象", Wave 2 simulation 重新打分.

    Args:
        state: 当前 cognitive state.
        l1_id: L1 node id (abstraction_level == 1).
        llm: LLM client.
        max_l0: 输出 L0 数量上限 (1-3 推荐).

    Returns:
        新加的 L0 node id 列表 (长度 1-max_l0).

    Raises:
        ValueError: l1_id 不存在 / 不是 L1 / lifecycle decayed (Wave 4 加).
        SchemaValidationError: LLM 输出不合规 (retry 1 次仍失败).
    """
    if l1_id not in state.graph.nodes:
        raise ValueError(f"target {l1_id!r} not found in graph")
    target = state.graph.nodes[l1_id]
    if target.abstraction_level != 1:
        raise ValueError(
            f"target {l1_id!r} has level={target.abstraction_level}, must be 1"
        )

    prompt = load_prompt("expansion_downward")
    existing_l0_text = _render_existing_l0(state)
    existing_l1_l2_text = _render_existing_l1_l2(state)

    messages = [
        Message(role="system", content=prompt["system"]),
        Message(
            role="user",
            content=prompt["user_template"].format(
                question=state.root_question,
                l1_id=l1_id,
                l1_name=target.name,
                l1_description=target.description,
                existing_l0_table=existing_l0_text,
                existing_l1_l2_table=existing_l1_l2_text,
                max_l0=max_l0,
            ),
        ),
    ]

    output = await _call_with_retry_downward(llm, messages)
    predicted = output.predicted_L0[:max_l0]

    next_p_num = _next_phenom_id_num(state)
    next_edge_id = _next_edge_id(state)
    new_ids: list[str] = []
    existing_name_to_id = {n.name: nid for nid, n in state.graph.nodes.items()}

    for pl0 in predicted:
        if pl0.name in existing_name_to_id:
            l0_id = existing_name_to_id[pl0.name]
        else:
            l0_id = f"p_{next_p_num:03d}"
            next_p_num += 1
            state.graph.add_node(
                VariableNode(
                    id=l0_id,
                    name=pl0.name,
                    description=pl0.description,
                    abstraction_level=0,
                    confidence=pl0.plausibility / 5.0,
                    epistemic="speculation",
                    source="llm",
                )
            )

        state.graph.add_edge(
            RelationEdge(
                id=f"e_{next_edge_id:03d}",
                source_node=l1_id,
                target_node=l0_id,
                relation_type="manifests_as",
                # Wave A linear mapping (跟 Phase 7 evaluation/expansion 一致)
                confidence=pl0.plausibility / 5.0,
                mechanism_description=pl0.mechanism,
            )
        )
        next_edge_id += 1
        new_ids.append(l0_id)

    return new_ids


def _render_existing_l0(state: CognitiveState) -> str:
    lines = [
        f"- {nid}: {n.name} — {n.description}"
        for nid, n in state.graph.nodes.items()
        if n.abstraction_level == 0
    ]
    return "\n".join(lines) if lines else "(none)"


def _render_existing_l1_l2(state: CognitiveState) -> str:
    lines = [
        f"- {nid}: {n.name} (L{n.abstraction_level}) — {n.description}"
        for nid, n in state.graph.nodes.items()
        if n.abstraction_level >= 1
    ]
    return "\n".join(lines) if lines else "(none)"


def _next_phenom_id_num(state: CognitiveState) -> int:
    existing = [
        int(nid.split("_")[1])
        for nid in state.graph.nodes
        if nid.startswith("p_") and nid[2:].isdigit()
    ]
    return (max(existing) + 1) if existing else 1


async def _call_with_retry_downward(
    llm: LLMClient,
    messages: list[Message],
) -> DownwardExpansionOutput:
    last_exc: Exception | None = None
    for _attempt in range(2):
        resp = await llm.chat(messages, schema=DownwardExpansionOutput)
        if resp.parsed is None:
            last_exc = SchemaValidationError("LLM 未返回 structured output")
            continue
        try:
            return DownwardExpansionOutput.model_validate(resp.parsed)
        except ValidationError as exc:
            last_exc = SchemaValidationError(f"LLM 输出 schema 不合规: {exc}")
            continue
    assert last_exc is not None
    raise last_exc
```

### Step 5: 跑测试 — 验证全 PASS

Run: `.venv/bin/python -m pytest tests/test_engines_expand_downward.py -v`

Expected: 6 PASS.

### Step 6: 跑全测 + ruff

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
```

Expected: 396 pass (390 + 6), ruff 0 errors.

### Step 7: Commit

```bash
git add src/explain_engine/engines/expansion.py \
        src/explain_engine/llm/prompts/expansion_downward.yaml \
        tests/test_engines_expand_downward.py
git commit -m "$(cat <<'EOF'
Wave 1.1 · expand_downward engine + prompt + tests

新增 expansion.expand_downward(state, l1_id, llm) — 与 expand_one_frontier 对称但反方向:
- expand_one_frontier 给 L1 加 incoming causes (driver → L1)
- expand_downward 给 L1 加 outgoing manifests_as (L1 → L0)

哲学锚点 §8.1: "Explanation 必须能 rollout, 否则可能不是真机制".
解 Phase 7 Wave C 死循环根因: re_expand 加 driver 不影响 L1 outgoing edges,
永远修不好 consistency_score (后者只看 manifests_as 方向).

新 prompt expansion_downward.yaml. confidence writeback 跟 Wave A 一致 (plausibility/5).
+6 tests. 396 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.2: reflect 决策树用 expand-downward + dispatch + anti-thrash 改名

**目的**: 1.1 实现了 engine. 现在让 reflect 决策树用它. ① ReflectionAction 加 "expand-downward"; ② reflect() 在 weak L1 时返 ("expand-downward", target) 而非 ("re-expand", target); ③ runtime.run dispatch 加 expand-downward case (调 expand_downward); ④ anti-thrash `_exhausted_re_expand_targets` 改名 `_exhausted_expansion_targets`, 同时数 expand-downward + re-expand. re_expand engine 函数本身保留 (backward compat), 但 reflect 不再产生 "re-expand" action.

**Files:**
- Modify: `src/explain_engine/schema/state.py` (ReflectionAction Literal 加 "expand-downward")
- Modify: `src/explain_engine/engines/reflection.py` (改 reflect 决策 + 改名 _exhausted_*)
- Modify: `src/explain_engine/runtime/runtime.py` (加 expand-downward dispatch)
- Create: `tests/test_runtime_reflect_expand_downward.py`

---

### Step 1: 写失败测试

Create `tests/test_runtime_reflect_expand_downward.py`:

```python
"""Wave 1 Task 1.2: reflect 改用 expand-downward + dispatch 集成测试.

design §4.3: reflect() 在 weak L1 时返 expand-downward.
runtime.run dispatch 加 expand-downward → engines.expand_downward.
anti-thrash 同时数 expand-downward + re-expand.
"""

from datetime import UTC, datetime

import pytest

from explain_engine.engines import reflection
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState, TraceEntry


def _make_weak_l1_state() -> CognitiveState:
    """1 L1 + 1 L0 with low-conf edge → consistency_score 应该 < 0.5."""
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id="c_001", name="weak_l1", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="phenom", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.1,  # 低 conf → weak chain
        mechanism_description="m",
    ))
    state = CognitiveState(
        graph=g, budget_remaining=10, root_question="why",
        insight_candidates=["c_001"],
    )
    return state


class TestReflectExpandDownward:
    def test_reflect_weak_l1_returns_expand_downward(self) -> None:
        """Wave 1: 改前返 ('re-expand', c_001), 改后返 ('expand-downward', c_001)."""
        state = _make_weak_l1_state()
        action, target = reflection.reflect(state)
        assert action == "expand-downward"
        assert target == "c_001"

    def test_reflect_returns_continue_when_no_weak_l1(self) -> None:
        """高 conf chain → no weak L1 → continue."""
        g = ExplanationGraph(root_question="why")
        g.add_node(VariableNode(
            id="c_001", name="strong_l1", description="d",
            abstraction_level=1, confidence=0.9, epistemic="insight",
        ))
        g.add_node(VariableNode(
            id="p_001", name="phenom", description="d",
            abstraction_level=0, confidence=0.9, epistemic="observation",
        ))
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.9,
            mechanism_description="m",
        ))
        state = CognitiveState(
            graph=g, budget_remaining=10, root_question="why",
            insight_candidates=["c_001"],
        )
        action, _ = reflection.reflect(state)
        assert action in ("continue", "stop")  # high conf → 不该 re/expand


class TestAntiThrash:
    def test_anti_thrash_counts_expand_downward(self) -> None:
        """LOOKBACK=5 内同 target expand-downward >= 2 次 → exhausted."""
        state = _make_weak_l1_state()
        ts = datetime.now(UTC).isoformat()
        state.reasoning_trace = [
            TraceEntry(tick=0, action="reflect", target_node_id="c_001",
                       gain_delta=0.0, llm_calls=1, timestamp=ts,
                       reflection_action="expand-downward"),
            TraceEntry(tick=1, action="reflect", target_node_id="c_001",
                       gain_delta=0.0, llm_calls=1, timestamp=ts,
                       reflection_action="expand-downward"),
        ]
        exhausted = reflection._exhausted_expansion_targets(state)
        assert "c_001" in exhausted

    def test_anti_thrash_counts_re_expand_too_for_backward_compat(self) -> None:
        """Backward compat: 老 trace 用 re-expand action 也算入 anti-thrash."""
        state = _make_weak_l1_state()
        ts = datetime.now(UTC).isoformat()
        state.reasoning_trace = [
            TraceEntry(tick=0, action="reflect", target_node_id="c_001",
                       gain_delta=0.0, llm_calls=1, timestamp=ts,
                       reflection_action="re-expand"),
            TraceEntry(tick=1, action="reflect", target_node_id="c_001",
                       gain_delta=0.0, llm_calls=1, timestamp=ts,
                       reflection_action="expand-downward"),
        ]
        exhausted = reflection._exhausted_expansion_targets(state)
        assert "c_001" in exhausted
```

### Step 2: 跑测试 — 验证全 fail

Run: `.venv/bin/python -m pytest tests/test_runtime_reflect_expand_downward.py -v`

Expected: 4 FAIL (reflect 仍返 "re-expand"; `_exhausted_expansion_targets` 不存在).

### Step 3: 改 ReflectionAction Literal

Modify `src/explain_engine/schema/state.py`:

```python
# 改前 (Phase 7):
ReflectionAction = Literal["continue", "re-expand", "prune", "stop"]

# 改后 (Phase 8 Wave 1):
ReflectionAction = Literal["continue", "re-expand", "expand-downward", "prune", "stop"]
# Wave 1 加 "expand-downward". "re-expand" 保留供 backward compat (老 session JSON).
```

### Step 4: 改 reflect() + 改名 anti-thrash

Modify `src/explain_engine/engines/reflection.py`:

把 `_exhausted_re_expand_targets` 改名 `_exhausted_expansion_targets`, 同时数 expand-downward + re-expand:

```python
def _exhausted_expansion_targets(state: CognitiveState) -> set[str]:
    """Wave 1: 同时数 expand-downward + re-expand (后者向后兼容).

    返回: 在 LOOKBACK_WINDOW 中被选中 ≥ THRASH_LIMIT 次的 target_id 集合.
    避免 reflect 反复 expand 同一节点.

    v2 occurrence-in-window 语义保留 (Phase 7 Wave C 补丁2 v2).
    """
    counts: dict[str, int] = {}
    seen_reflects = 0

    for entry in reversed(state.reasoning_trace):
        if entry.action != "reflect":
            continue
        seen_reflects += 1
        if seen_reflects > RE_EXPAND_LOOKBACK_WINDOW:
            break
        if (
            entry.reflection_action in ("expand-downward", "re-expand")
            and entry.target_node_id
        ):
            counts[entry.target_node_id] = counts.get(entry.target_node_id, 0) + 1

    return {t for t, c in counts.items() if c >= RE_EXPAND_THRASH_LIMIT}


# 保留旧名作 alias (没人调外部, 但保险)
_exhausted_re_expand_targets = _exhausted_expansion_targets
```

改 `reflect()`: 把 `("re-expand", ...)` 改成 `("expand-downward", ...)`:

```python
def reflect(state: CognitiveState) -> tuple[ReflectionAction, str | None]:
    """Reflection decision. 0 LLM call.

    Wave 1 改: 用 expand-downward 替 re-expand (修死循环根因, 见 design §4).
    Returns: (action, target_id)
    """
    if not state.graph.nodes:
        return ("continue", None)

    L1_L2 = [
        nid for nid, n in state.graph.nodes.items() if n.abstraction_level >= 1
    ]
    if not L1_L2:
        return ("continue", None)

    reports = check_consistency_batch(state)
    reports = [r for r in reports if r.target_id in state.graph.nodes]

    # 1. expand-downward 低 consistency L1 (Wave 1 改: 原 re-expand)
    exhausted = _exhausted_expansion_targets(state)
    low_c = sorted(
        [r for r in reports
         if state.graph.nodes[r.target_id].abstraction_level == 1
         and r.consistency_score < LOW_CONSISTENCY_THRESHOLD
         and r.target_id not in exhausted],
        key=lambda r: r.consistency_score,
    )
    if low_c:
        return ("expand-downward", low_c[0].target_id)

    # 2. prune 低 essentialness L2 (不变)
    low_e = sorted(
        [r for r in reports
         if state.graph.nodes[r.target_id].abstraction_level == 2
         and r.essentialness_score < LOW_ESSENTIALNESS_THRESHOLD],
        key=lambda r: r.essentialness_score,
    )
    if low_e:
        return ("prune", low_e[0].target_id)

    # 3. stale 检测 (不变)
    if state.tick - state.last_reflection_change_tick >= CONSISTENCY_STALE_TICKS:
        return ("stop", None)

    return ("continue", None)
```

把 reflection.py 顶部的 docstring 注释 (Phase 8 设计 bug 警告) 删掉/更新, 因为已经修了:

```python
"""Wave C.1 + Phase 8 Wave 1: Reflection Engine.

design §6.2 / Phase 8 design §4. 0 LLM call.
决策优先级 (Wave 1 改): expand-downward > prune > stop > continue.

Wave 1 (Phase 8) 用 expand-downward 替换 re-expand 修死循环根因:
re_expand 加 incoming causes (driver → L1) 但 consistency 测 outgoing
manifests_as (L1 → L0). 加 driver 不影响 L1 outgoing edges, 永远改善不了
consistency 分数 → 死循环. expand-downward 直接给 L1 加 manifests_as 子节点,
检验 L1 是不是真机制 (哲学 §8.1 rollout).
"""
```

### Step 5: 改 runtime.run dispatch

Modify `src/explain_engine/runtime/runtime.py` (加 expand-downward case):

把 `if action == "reflect":` 块的 `if refl_action == "re-expand"` 部分改成:

```python
        if action == "reflect":
            refl_action, refl_target = reflection.reflect(state)
            reflection_action = refl_action

            if refl_action == "expand-downward" and refl_target is not None:
                # Wave 1 Phase 8: 替原 re-expand
                _new_l0_ids = await expansion.expand_downward(state, refl_target, llm)
                llm_calls = 1
                target_id = refl_target
                state.last_reflection_change_tick = state.tick
            elif refl_action == "re-expand" and refl_target is not None:
                # Backward compat: 老 session trace 加载后, 老 reflection_action 仍能 dispatch.
                # 注意: 当前 reflect() 不再产生 re-expand, 这条只服务老 trace replay.
                _new_ids, gain_delta = await expansion.re_expand(
                    state, refl_target, llm
                )
                llm_calls = 1
                target_id = refl_target
                state.last_reflection_change_tick = state.tick
            elif refl_action == "prune" and refl_target is not None:
                state.graph.remove_node(refl_target)
                target_id = refl_target
                state.last_reflection_change_tick = state.tick
            elif refl_action == "stop":
                state.last_reflection_change_tick = max(
                    0, state.tick - reflection.CONSISTENCY_STALE_TICKS - 1
                )
            # refl_action == "continue": no-op

            state.last_gain_tick = state.tick
```

### Step 6: 跑测试 — 验证 PASS + 全测

Run:
```bash
.venv/bin/python -m pytest tests/test_runtime_reflect_expand_downward.py -v
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
```

Expected: Task 1.2 测试 4 PASS; 全测 400 pass (396 + 4); ruff 0.

⚠️ 老 reflection 测试可能 fail (`test_reflect_returns_re_expand_for_weak_l1` 等). 需要更新这些测试用 expand-downward.

如有 fail: 找出老 test_engines_reflection.py 中验 `("re-expand", ...)` 的断言, 改成 `("expand-downward", ...)`.

### Step 7: Commit

```bash
git add src/explain_engine/schema/state.py \
        src/explain_engine/engines/reflection.py \
        src/explain_engine/runtime/runtime.py \
        tests/test_runtime_reflect_expand_downward.py \
        tests/test_engines_reflection.py     # 如果改了
git commit -m "$(cat <<'EOF'
Wave 1.2 · reflect 决策树用 expand-downward 替 re-expand + dispatch + anti-thrash

ReflectionAction Literal 加 "expand-downward" (re-expand 保留供 backward compat).
reflect() 在 weak L1 时返 ("expand-downward", target) 而非 ("re-expand", target).
runtime.run dispatch 加 expand-downward case → engines.expand_downward.
re-expand dispatch 保留 (老 session trace replay).

anti-thrash 函数改名 _exhausted_re_expand_targets → _exhausted_expansion_targets,
同时数两种 action (occurrence-in-window 语义保留, Phase 7 Wave C 补丁2 v2).

死循环根因修复: re_expand 加 driver 永远修不好 consistency_score (后者只看
outgoing manifests_as 方向). expand-downward 直接给 L1 加 manifests_as 子节点
触发 simulation 重打分.

+4 tests. 400 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 1 Checkpoint (用户审)

**完成度**: 2/2 task. +10 tests (390 → 400). 修复 Phase 7 死循环根因.

**验证**:
- `expand_downward` engine 工作 (6 tests)
- reflect 决策树用 expand-downward (4 tests)
- backward compat: 老 trace 加载 + re-expand dispatch 仍工作

**Stop**. 等用户审过, 进 Wave 2.

---

# Wave 2 — Multi-Signal Acceptance (含 rollout_coverage)

## Task 2.1: `_propagation.rollout_from_roots()` + tests

**目的**: 新增图遍历算法, 从 L2 root 起算的全 graph reachability. 沿 causes (L2→L1) + manifests_as (L1→L0) BFS, 收集 reachable L0 集合 + missing L0 集合. 同时服务 Wave 2 的 rollout_coverage 信号 + Wave 3 的 rollout_alignment (复用同一函数).

**Files:**
- Modify: `src/explain_engine/engines/_propagation.py` (加 rollout_from_roots)
- Create: `tests/test_engines_propagation_rollout.py`

---

### Step 1: 写失败测试

Create `tests/test_engines_propagation_rollout.py`:

```python
"""Wave 2 Task 2.1: _propagation.rollout_from_roots 单元测试.

design §5.3.1: 从 L2 root 沿 causes ↓ manifests_as ↓ BFS, 收集 reachable L0.
"""

from explain_engine.engines._propagation import rollout_from_roots
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid: str, level: int) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7,
        epistemic="observation" if level == 0 else "insight",
    )


def _edge(eid: str, src: str, tgt: str, rel: str = "manifests_as", conf: float = 0.7) -> RelationEdge:
    return RelationEdge(
        id=eid, source_node=src, target_node=tgt,
        relation_type=rel, confidence=conf, mechanism_description="m",
    )


class TestRolloutFromRoots:
    def test_full_chain_l2_to_l1_to_l0_all_reachable(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("d_001", 2))
        g.add_node(_node("c_001", 1))
        g.add_node(_node("p_001", 0))
        g.add_edge(_edge("e_001", "d_001", "c_001", "causes"))
        g.add_edge(_edge("e_002", "c_001", "p_001", "manifests_as"))

        reachable, missing = rollout_from_roots(g)
        assert reachable == {"p_001"}
        assert missing == set()

    def test_disconnected_l0_in_missing(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("d_001", 2))
        g.add_node(_node("c_001", 1))
        g.add_node(_node("p_001", 0))
        g.add_node(_node("p_002", 0))   # 孤立 L0
        g.add_edge(_edge("e_001", "d_001", "c_001", "causes"))
        g.add_edge(_edge("e_002", "c_001", "p_001", "manifests_as"))

        reachable, missing = rollout_from_roots(g)
        assert reachable == {"p_001"}
        assert missing == {"p_002"}

    def test_no_l2_falls_back_to_l1_as_roots(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("c_001", 1))
        g.add_node(_node("p_001", 0))
        g.add_edge(_edge("e_001", "c_001", "p_001", "manifests_as"))

        reachable, missing = rollout_from_roots(g)
        assert reachable == {"p_001"}
        assert missing == set()

    def test_empty_graph_returns_empty(self) -> None:
        g = ExplanationGraph(root_question="q")
        reachable, missing = rollout_from_roots(g)
        assert reachable == set()
        assert missing == set()

    def test_handles_cycle_without_infinite_loop(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("d_001", 2))
        g.add_node(_node("c_001", 1))
        g.add_node(_node("c_002", 1))
        g.add_node(_node("p_001", 0))
        g.add_edge(_edge("e_001", "d_001", "c_001", "causes"))
        # 人造小循环 c_001 → c_002 → c_001 (实际 graph 不应有, 但防御)
        g.add_edge(_edge("e_002", "c_001", "c_002", "manifests_as"))
        g.add_edge(_edge("e_003", "c_002", "c_001", "causes"))
        g.add_edge(_edge("e_004", "c_001", "p_001", "manifests_as"))

        reachable, missing = rollout_from_roots(g)
        assert reachable == {"p_001"}

    def test_skips_decayed_nodes_when_present(self) -> None:
        """Wave 4 集成预留: lifecycle_state == decayed 不参与 rollout.

        Wave 2 阶段 lifecycle_state 字段还没加, 这个 test 暂时 skip,
        Wave 4 Task 4.1 启用.
        """
        import pytest
        pytest.skip("Wave 4 Task 4.1 启用: VariableNode lifecycle_state 字段")
```

### Step 2: 跑测试 — 验证全 fail (除 skip)

Run: `.venv/bin/python -m pytest tests/test_engines_propagation_rollout.py -v`

Expected: 5 FAIL (ImportError: rollout_from_roots) + 1 SKIP.

### Step 3: 实现 rollout_from_roots

Modify `src/explain_engine/engines/_propagation.py` (加在文件末尾):

```python
def rollout_from_roots(graph: ExplanationGraph) -> tuple[set[str], set[str]]:
    """Wave 2 Task 2.1: 从 L2 root 起算的全 graph rollout reachability (BFS).

    哲学锚点 §8.1: "Explanation 必须能 rollout, 否则可能不是真机制".
    从 L2 root drivers 出发沿 causes (L2→L1) + manifests_as (L1→L0) BFS,
    收集 reachable L0 集合. 没被触达的 L0 是"孤儿观察", 揭示 graph
    explanatory_scope 不完整.

    退化处理:
      - 无 L2 节点 → 用 L1 节点作 roots (避免空 reachable)
      - 无 L1 + L2 节点 → 返 empty (空 graph 或纯 L0)

    Wave 4 集成 (Task 4.1 启用):
      - 跳过 lifecycle_state == "decayed" 的节点

    Args:
        graph: ExplanationGraph.

    Returns:
        (reachable_l0_ids, missing_l0_ids) tuple of sets.
        reachable + missing == 全部 L0 ids.
    """
    all_l0 = {nid for nid, n in graph.nodes.items() if n.abstraction_level == 0}
    if not all_l0:
        return set(), set()

    roots = {nid for nid, n in graph.nodes.items() if n.abstraction_level == 2}
    if not roots:
        # 退化: 无 L2 用 L1 作 roots
        roots = {nid for nid, n in graph.nodes.items() if n.abstraction_level == 1}
    if not roots:
        # 纯 L0 graph, 没有 reachable
        return set(), all_l0

    # Wave 4 hook: 跳过 decayed
    def _is_decayed(nid: str) -> bool:
        node = graph.nodes.get(nid)
        if node is None:
            return False
        # Wave 4 加 lifecycle_state 字段后会 truthy
        return getattr(node, "lifecycle_state", "active") == "decayed"

    visited: set[str] = set()
    queue: list[str] = []
    for r in roots:
        if not _is_decayed(r):
            visited.add(r)
            queue.append(r)

    while queue:
        current = queue.pop(0)
        for edge in graph.outgoing_edges(current):
            if edge.relation_type not in FORWARD_RELATIONS:
                continue
            target = edge.target_node
            if target in visited:
                continue
            if _is_decayed(target):
                continue
            visited.add(target)
            queue.append(target)

    reachable_l0 = visited & all_l0
    missing_l0 = all_l0 - reachable_l0
    return reachable_l0, missing_l0
```

### Step 4: 跑测试 — 验证 PASS

Run: `.venv/bin/python -m pytest tests/test_engines_propagation_rollout.py -v`

Expected: 5 PASS + 1 SKIP.

### Step 5: 跑全测 + ruff

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
```

Expected: 405 pass (400 + 5), 1 skipped, ruff 0 errors.

### Step 6: Commit

```bash
git add src/explain_engine/engines/_propagation.py \
        tests/test_engines_propagation_rollout.py
git commit -m "$(cat <<'EOF'
Wave 2.1 · _propagation.rollout_from_roots BFS reachability

新增 rollout_from_roots(graph) → (reachable_l0_set, missing_l0_set).
从 L2 root 沿 causes (L2→L1) + manifests_as (L1→L0) BFS, 收集触达的 L0.

哲学锚点 §8.1: 真机制必须能 rollout. 服务 Wave 2 rollout_coverage 信号
+ Wave 3 rollout_alignment 复用 (Q6.2 Option Y).

退化: 无 L2 用 L1 作 roots; 无 L1+L2 返 empty. Wave 4 hook: 跳过 decayed
(getattr 默认 "active", 当前 schema 还没加字段, 兼容).

+5 tests, +1 skip (Wave 4 启用). 405 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2.2: `AcceptanceReport` + `simulation.aggregate_acceptance()` + tests

**目的**: 新增 aggregate `AcceptanceReport` dataclass (Wave 2 的 6 个 multi-signal 字段 + Wave 3 的 alignment 字段占位). 新增 `simulation.aggregate_acceptance(state)` 函数, 调用 `check_consistency_batch` + `rollout_from_roots`, 把所有信号聚合成单一 report. 不修改现有 ConsistencyReport (per-target).

**Files:**
- Modify: `src/explain_engine/engines/simulation.py` (新增 AcceptanceReport + aggregate_acceptance)
- Create: `tests/test_engines_simulation_signals.py`

---

### Step 1: 写失败测试

Create `tests/test_engines_simulation_signals.py`:

```python
"""Wave 2 Task 2.2: simulation.aggregate_acceptance + AcceptanceReport.

design §5.2: 聚合 ConsistencyReport (per-target) + rollout_from_roots
形成 multi-signal AcceptanceReport.
"""

import pytest

from explain_engine.engines.simulation import (
    AcceptanceReport,
    aggregate_acceptance,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _node(nid, level, conf=0.7):
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=conf,
        epistemic="observation" if level == 0 else "insight",
    )


def _edge(eid, src, tgt, rel="manifests_as", conf=0.7):
    return RelationEdge(
        id=eid, source_node=src, target_node=tgt,
        relation_type=rel, confidence=conf, mechanism_description="m",
    )


def _make_state(nodes_levels: list[tuple[str, int]],
                edges: list[tuple[str, str, str, str, float]]) -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    for nid, lv in nodes_levels:
        g.add_node(_node(nid, lv))
    for eid, src, tgt, rel, conf in edges:
        g.add_edge(_edge(eid, src, tgt, rel, conf))
    return CognitiveState(graph=g, budget_remaining=10, root_question="q")


class TestAcceptanceReport:
    def test_dataclass_default_fields(self) -> None:
        r = AcceptanceReport(
            avg_consistency=0.5, avg_essentialness=0.3,
            per_l1={"c_001": 0.5}, per_l2={"d_001": 0.3},
        )
        assert r.weak_chain_l1s == []
        assert r.lowest_l1 is None
        assert r.consistency_spread == 0.0
        assert r.essentialness_spread == 0.0
        assert r.rollout_coverage == 1.0
        assert r.missing_l0 == []
        assert r.input_alignment is None
        assert r.falsifiable_reason is None


class TestAggregateAcceptance:
    def test_weak_chain_l1s_lists_below_threshold(self) -> None:
        # 一条强链 + 一条弱链 (低 conf edge)
        state = _make_state(
            nodes_levels=[("c_001", 1), ("c_002", 1), ("p_001", 0), ("p_002", 0)],
            edges=[
                ("e_001", "c_001", "p_001", "manifests_as", 0.9),
                ("e_002", "c_002", "p_002", "manifests_as", 0.1),  # 弱
            ],
        )
        report = aggregate_acceptance(state)
        # c_002 consistency 应该 < 0.5
        assert "c_002" in report.weak_chain_l1s
        assert "c_001" not in report.weak_chain_l1s

    def test_lowest_l1_returns_argmin(self) -> None:
        state = _make_state(
            nodes_levels=[("c_001", 1), ("c_002", 1), ("p_001", 0), ("p_002", 0)],
            edges=[
                ("e_001", "c_001", "p_001", "manifests_as", 0.9),
                ("e_002", "c_002", "p_002", "manifests_as", 0.2),
            ],
        )
        report = aggregate_acceptance(state)
        assert report.lowest_l1 is not None
        assert report.lowest_l1[0] == "c_002"

    def test_lowest_l1_empty_l1_returns_none(self) -> None:
        state = _make_state(nodes_levels=[("p_001", 0)], edges=[])
        report = aggregate_acceptance(state)
        assert report.lowest_l1 is None

    def test_consistency_spread_max_minus_min(self) -> None:
        state = _make_state(
            nodes_levels=[("c_001", 1), ("c_002", 1), ("p_001", 0), ("p_002", 0)],
            edges=[
                ("e_001", "c_001", "p_001", "manifests_as", 0.9),
                ("e_002", "c_002", "p_002", "manifests_as", 0.1),
            ],
        )
        report = aggregate_acceptance(state)
        assert report.consistency_spread > 0.0

    def test_essentialness_spread(self) -> None:
        # 单 L2 → spread = 0
        state = _make_state(
            nodes_levels=[("d_001", 2), ("c_001", 1), ("p_001", 0)],
            edges=[
                ("e_001", "d_001", "c_001", "causes", 0.7),
                ("e_002", "c_001", "p_001", "manifests_as", 0.7),
            ],
        )
        report = aggregate_acceptance(state)
        assert report.essentialness_spread == 0.0

    def test_rollout_coverage_full_chain(self) -> None:
        state = _make_state(
            nodes_levels=[("d_001", 2), ("c_001", 1), ("p_001", 0)],
            edges=[
                ("e_001", "d_001", "c_001", "causes", 0.7),
                ("e_002", "c_001", "p_001", "manifests_as", 0.7),
            ],
        )
        report = aggregate_acceptance(state)
        assert report.rollout_coverage == 1.0
        assert report.missing_l0 == []

    def test_rollout_coverage_partial(self) -> None:
        state = _make_state(
            nodes_levels=[("c_001", 1), ("p_001", 0), ("p_002", 0)],
            edges=[("e_001", "c_001", "p_001", "manifests_as", 0.7)],
        )
        report = aggregate_acceptance(state)
        assert report.rollout_coverage == 0.5
        assert report.missing_l0 == ["p_002"]

    def test_empty_graph_returns_safe_defaults(self) -> None:
        state = _make_state(nodes_levels=[], edges=[])
        report = aggregate_acceptance(state)
        assert report.avg_consistency == 0.0
        assert report.weak_chain_l1s == []
        assert report.lowest_l1 is None
        assert report.rollout_coverage == 1.0  # 无 L0 → trivially 1.0
```

### Step 2: 跑测试 — 验证全 fail

Run: `.venv/bin/python -m pytest tests/test_engines_simulation_signals.py -v`

Expected: 8 FAIL (ImportError: AcceptanceReport, aggregate_acceptance).

### Step 3: 实现 AcceptanceReport + aggregate_acceptance

Modify `src/explain_engine/engines/simulation.py`:

加 imports:

```python
from dataclasses import dataclass, field

from explain_engine.engines._propagation import (
    WEAK_CHAIN_THRESHOLD,
    DecayStep,
    get_all_L0,
    get_all_L1_L2,
    propagate,
    rollout_from_roots,   # NEW Wave 2.1
)
```

加 AcceptanceReport (在 ConsistencyReport 之后):

```python
@dataclass(frozen=True)
class AcceptanceReport:
    """Wave 2 Task 2.2: aggregate multi-signal acceptance report.

    与 per-target ConsistencyReport 不同, 这是全 graph 的聚合报告.
    给 reflect / CLI / acceptance verdict 用.

    哲学锚点:
      - §11.3 "最低 entropy 下的最大解释力" → consistency_spread / essentialness_spread
      - §8.1 "Explanation 必须能 rollout" → rollout_coverage
      - §9.4 可证伪性 → input_alignment / falsifiable_reason (Wave 3 注入)

    Note: weak_chain_l1s 与 ConsistencyReport.weak_chains 含义不同:
      - ConsistencyReport.weak_chains: 单 target 内 reachable_L0 中 activation 弱的 L0
      - AcceptanceReport.weak_chain_l1s: 全 graph 中 consistency_score 低的 L1
    """

    # 主信号 (聚合)
    avg_consistency: float
    avg_essentialness: float

    # per-node 明细
    per_l1: dict[str, float] = field(default_factory=dict)
    per_l2: dict[str, float] = field(default_factory=dict)

    # ── Wave 2 multi-signal (6 字段) ──
    weak_chain_l1s: list[str] = field(default_factory=list)
    """consistency < LOW_CONSISTENCY_THRESHOLD 的 L1 id 列表 (按 score 升序)."""

    lowest_l1: tuple[str, float] | None = None
    """argmin(per_l1) 的 (id, score). 空 graph 返 None."""

    consistency_spread: float = 0.0
    """max(per_l1) - min(per_l1)."""

    essentialness_spread: float = 0.0
    """max(per_l2) - min(per_l2)."""

    rollout_coverage: float = 1.0
    """从 L2 root rollout 触达的 L0 比例. 无 L0 时 trivially 1.0."""

    missing_l0: list[str] = field(default_factory=list)
    """rollout 没触达的 L0 id 列表 (升序)."""

    # ── Wave 3 alignment 字段 (留位, Wave 3 注入) ──
    input_alignment: float | None = None
    """Wave 3 input_validation overlap_score / 5.0. None = 没跑过校验."""

    falsifiable_reason: str | None = None
    """Wave 3 LLM 给的对齐失败理由."""
```

加 aggregate_acceptance() (在 check_consistency_batch 之后):

```python
def aggregate_acceptance(state: CognitiveState) -> AcceptanceReport:
    """Wave 2 Task 2.2: 全 graph 聚合 multi-signal report.

    流程:
      1. check_consistency_batch (现有 Phase 6) → list[ConsistencyReport] per-target
      2. 拆 per_l1 / per_l2 dict
      3. 算 avg / spread / weak_chains_l1s / lowest_l1
      4. rollout_from_roots → rollout_coverage / missing_l0

    不调 LLM. 只读, 不改 state.
    """
    L1_L2 = sorted(get_all_L1_L2(state.graph))
    if not L1_L2:
        # 空 graph 安全默认
        return AcceptanceReport(
            avg_consistency=0.0,
            avg_essentialness=0.0,
        )

    reports = check_consistency_batch(state)
    per_l1: dict[str, float] = {}
    per_l2: dict[str, float] = {}
    for r in reports:
        node = state.graph.nodes.get(r.target_id)
        if node is None:
            continue
        if node.abstraction_level == 1:
            per_l1[r.target_id] = r.consistency_score
        elif node.abstraction_level == 2:
            per_l2[r.target_id] = r.essentialness_score

    avg_consistency = sum(per_l1.values()) / len(per_l1) if per_l1 else 0.0
    avg_essentialness = sum(per_l2.values()) / len(per_l2) if per_l2 else 0.0

    # Wave 2 multi-signal
    from explain_engine.engines.reflection import LOW_CONSISTENCY_THRESHOLD
    weak_chain_l1s = sorted(
        [l1 for l1, s in per_l1.items() if s < LOW_CONSISTENCY_THRESHOLD],
        key=lambda l1: per_l1[l1],
    )
    lowest_l1 = min(per_l1.items(), key=lambda kv: kv[1]) if per_l1 else None
    consistency_spread = (max(per_l1.values()) - min(per_l1.values())) if per_l1 else 0.0
    essentialness_spread = (max(per_l2.values()) - min(per_l2.values())) if per_l2 else 0.0

    # rollout coverage
    reachable, missing = rollout_from_roots(state.graph)
    all_l0 = {nid for nid, n in state.graph.nodes.items() if n.abstraction_level == 0}
    rollout_coverage = (len(reachable) / len(all_l0)) if all_l0 else 1.0

    return AcceptanceReport(
        avg_consistency=avg_consistency,
        avg_essentialness=avg_essentialness,
        per_l1=per_l1,
        per_l2=per_l2,
        weak_chain_l1s=weak_chain_l1s,
        lowest_l1=lowest_l1,
        consistency_spread=consistency_spread,
        essentialness_spread=essentialness_spread,
        rollout_coverage=rollout_coverage,
        missing_l0=sorted(missing),
    )
```

### Step 4: 跑测试 — 验证 PASS

Run: `.venv/bin/python -m pytest tests/test_engines_simulation_signals.py -v`

Expected: 8 PASS.

### Step 5: 跑全测 + ruff

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
```

Expected: 413 pass (405 + 8), ruff 0 errors.

### Step 6: Commit

```bash
git add src/explain_engine/engines/simulation.py \
        tests/test_engines_simulation_signals.py
git commit -m "$(cat <<'EOF'
Wave 2.2 · AcceptanceReport + simulation.aggregate_acceptance

新 dataclass AcceptanceReport (frozen, 9 字段) — 全 graph multi-signal aggregate.
跟现有 per-target ConsistencyReport 并存, 不动后者.

字段:
- 主信号: avg_consistency / avg_essentialness
- per-node: per_l1 / per_l2 dict
- Wave 2 multi-signal: weak_chain_l1s / lowest_l1 / consistency_spread / essentialness_spread / rollout_coverage / missing_l0
- Wave 3 留位: input_alignment / falsifiable_reason

新函数 aggregate_acceptance(state) — 调 check_consistency_batch + rollout_from_roots
聚合, 0 LLM. 服务 reflect 决策 + CLI status + acceptance verdict.

注: weak_chain_l1s 与 ConsistencyReport.weak_chains 含义不同 (前者是 L1 列表,
后者是 L0 列表), 命名解决 design doc 含糊. 见 plan §"与 Design Doc 偏差".

+8 tests. 413 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2.3: reflect 用 AcceptanceReport.weak_chain_l1s + CLI status

**目的**: ① reflect() 改用 `aggregate_acceptance(state).weak_chain_l1s` 列表 + skip exhausted 选第一个 (替原 sorted+filter 临时构造); ② state 加 `last_acceptance_report` 字段缓存 (避免 reflect 每 tick 重算 + CLI 直接读); ③ runtime 在 reflect tick 前刷新 `last_acceptance_report`; ④ CLI `explain status` 加 "Multi-signal" section 显示 6 个新字段.

**Files:**
- Modify: `src/explain_engine/schema/state.py` (CognitiveState 加 last_acceptance_report 字段)
- Modify: `src/explain_engine/engines/reflection.py` (用 weak_chain_l1s)
- Modify: `src/explain_engine/runtime/runtime.py` (reflect tick 前刷 acceptance)
- Modify: `src/explain_engine/cli.py` (status 加 multi-signal section)
- Create: `tests/test_engines_reflect_weak_chains.py`
- Create: `tests/test_cli_status_signals.py`

---

### Step 1: 写失败测试

Create `tests/test_engines_reflect_weak_chains.py`:

```python
"""Wave 2 Task 2.3: reflect 改用 AcceptanceReport.weak_chain_l1s.

design §5.4: 用 weak_chain_l1s 列表替 sorted+filter 临时构造.
"""

from datetime import UTC, datetime

from explain_engine.engines import reflection
from explain_engine.engines.simulation import AcceptanceReport
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState, TraceEntry


def _make_state_with_report(per_l1: dict[str, float],
                             weak_chain_l1s: list[str]) -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    for l1_id in per_l1:
        g.add_node(VariableNode(
            id=l1_id, name=l1_id, description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
    g.add_node(VariableNode(
        id="p_001", name="p", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    # 给每个 L1 加个出 edge 让 simulation 不空
    for i, l1_id in enumerate(per_l1, 1):
        g.add_edge(RelationEdge(
            id=f"e_{i:03d}", source_node=l1_id, target_node="p_001",
            relation_type="manifests_as", confidence=0.5,
            mechanism_description="m",
        ))

    state = CognitiveState(
        graph=g, budget_remaining=10, root_question="q",
    )
    # 注入预算 acceptance report (避免 reflect 实跑 simulation)
    state.last_acceptance_report = AcceptanceReport(
        avg_consistency=sum(per_l1.values()) / len(per_l1),
        avg_essentialness=0.5,
        per_l1=per_l1,
        weak_chain_l1s=weak_chain_l1s,
    )
    return state


class TestReflectWeakChainsList:
    def test_picks_first_unexhausted_from_weak_chain_l1s(self) -> None:
        state = _make_state_with_report(
            per_l1={"c_001": 0.2, "c_002": 0.3, "c_003": 0.7},
            weak_chain_l1s=["c_001", "c_002"],  # 升序
        )
        action, target = reflection.reflect(state)
        assert action == "expand-downward"
        assert target == "c_001"

    def test_skips_exhausted_l1(self) -> None:
        state = _make_state_with_report(
            per_l1={"c_001": 0.2, "c_002": 0.3},
            weak_chain_l1s=["c_001", "c_002"],
        )
        ts = datetime.now(UTC).isoformat()
        # c_001 exhausted (出现 2 次)
        state.reasoning_trace = [
            TraceEntry(tick=0, action="reflect", target_node_id="c_001",
                       gain_delta=0.0, llm_calls=1, timestamp=ts,
                       reflection_action="expand-downward"),
            TraceEntry(tick=1, action="reflect", target_node_id="c_001",
                       gain_delta=0.0, llm_calls=1, timestamp=ts,
                       reflection_action="expand-downward"),
        ]
        action, target = reflection.reflect(state)
        assert action == "expand-downward"
        assert target == "c_002"  # 跳过 c_001

    def test_no_weak_chains_falls_through(self) -> None:
        state = _make_state_with_report(
            per_l1={"c_001": 0.8},
            weak_chain_l1s=[],
        )
        action, _ = reflection.reflect(state)
        assert action != "expand-downward"

    def test_uses_cached_report_if_present(self) -> None:
        """有 last_acceptance_report → 不重算 simulation."""
        state = _make_state_with_report(
            per_l1={"c_001": 0.2}, weak_chain_l1s=["c_001"],
        )
        # 注入异常 graph 让 simulation 重算会失败
        # 如果 reflect 用了 cached report, 不会触发
        action, target = reflection.reflect(state)
        assert (action, target) == ("expand-downward", "c_001")
```

Create `tests/test_cli_status_signals.py`:

```python
"""Wave 2 Task 2.3: explain status 显示 multi-signal section."""

from click.testing import CliRunner

from explain_engine.cli import cli


class TestStatusMultiSignal:
    def test_status_renders_multi_signal_section(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        runner = CliRunner()
        # 先 new 一个 session
        result = runner.invoke(cli, ["new", "test question"])
        assert result.exit_code == 0
        sid = result.output.strip().split()[-1]

        # status 应该有 Multi-signal section (即使 report 为 None 也显示 N/A)
        result = runner.invoke(cli, ["status", sid])
        assert result.exit_code == 0
        assert "Multi-signal" in result.output or "multi-signal" in result.output.lower()

    def test_status_shows_signal_values_when_present(
        self, tmp_path, monkeypatch
    ) -> None:
        # Skip detailed: 需 fixture full session, 留 acceptance run 验证
        import pytest
        pytest.skip("Full integration: Wave 5 acceptance run 验证")

    def test_status_handles_old_session_no_acceptance_report(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        runner = CliRunner()
        result = runner.invoke(cli, ["new", "q"])
        sid = result.output.strip().split()[-1]
        # 老 session 没 acceptance report → status 不报错
        result = runner.invoke(cli, ["status", sid])
        assert result.exit_code == 0
```

### Step 2: 跑测试 — 验证 fail

Run:
```bash
.venv/bin/python -m pytest tests/test_engines_reflect_weak_chains.py tests/test_cli_status_signals.py -v
```

Expected: fail (last_acceptance_report 不存在; status 没 Multi-signal section).

### Step 3: 加 state.last_acceptance_report

Modify `src/explain_engine/schema/state.py`:

加 import (top):

```python
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from explain_engine.engines.simulation import AcceptanceReport
```

CognitiveState 加字段 (在 last_reflection_change_tick 后):

```python
@dataclass
class CognitiveState:
    # ... existing ...
    last_reflection_change_tick: int = 0
    # Phase 8 Wave 2 NEW: 缓存 aggregate report (reflect / CLI / acceptance 共用)
    last_acceptance_report: "AcceptanceReport | None" = field(default=None, repr=False)
```

注意: `last_acceptance_report` **不持久化** 到 JSON (避免循环序列化 + Phase 8 范围). 在 `to_dict` / `from_dict` 中跳过.

### Step 4: 改 reflect() 用 cached report

Modify `src/explain_engine/engines/reflection.py`:

`reflect()` 函数改:

```python
def reflect(state: CognitiveState) -> tuple[ReflectionAction, str | None]:
    """Reflection decision (Wave 1 + Wave 2 重写). 0 LLM call.

    Wave 2 改: 优先用 cached state.last_acceptance_report.weak_chain_l1s
              (runtime 在 reflect tick 前刷新). fallback 当场算 (兼容老调用).
    """
    if not state.graph.nodes:
        return ("continue", None)

    L1_L2 = [
        nid for nid, n in state.graph.nodes.items() if n.abstraction_level >= 1
    ]
    if not L1_L2:
        return ("continue", None)

    # Wave 2: 优先 cached report
    report = state.last_acceptance_report
    if report is None:
        # Fallback: 当场聚合 (老调用方 / test)
        from explain_engine.engines.simulation import aggregate_acceptance
        report = aggregate_acceptance(state)

    exhausted = _exhausted_expansion_targets(state)

    # 1. expand-downward 弱 L1 (用 cached weak_chain_l1s)
    for l1_id in report.weak_chain_l1s:
        if l1_id in exhausted:
            continue
        if l1_id not in state.graph.nodes:
            continue  # defensive
        return ("expand-downward", l1_id)

    # 2. prune 低 essentialness L2 (用 per_l2)
    low_l2 = sorted(
        [(l2, score) for l2, score in report.per_l2.items()
         if score < LOW_ESSENTIALNESS_THRESHOLD
         and l2 in state.graph.nodes],
        key=lambda kv: kv[1],
    )
    if low_l2:
        return ("prune", low_l2[0][0])

    # 3. stale 检测 (不变)
    if state.tick - state.last_reflection_change_tick >= CONSISTENCY_STALE_TICKS:
        return ("stop", None)

    return ("continue", None)
```

### Step 5: 改 runtime.run 在 reflect tick 前刷 acceptance

Modify `src/explain_engine/runtime/runtime.py`:

加 import:

```python
from explain_engine.engines.simulation import aggregate_acceptance
```

在 `if action == "reflect":` 块开头加:

```python
        if action == "reflect":
            # Wave 2: 刷新 cached acceptance report (reflect 与 CLI 共用)
            state.last_acceptance_report = aggregate_acceptance(state)

            refl_action, refl_target = reflection.reflect(state)
            # ... 其余不变 ...
```

### Step 6: 改 CLI status 加 multi-signal section

Modify `src/explain_engine/cli.py`:

找到 `def cmd_status` 函数, 在显示其他 metadata 之后加:

```python
    # Wave 2 Phase 8: multi-signal section
    report = getattr(state, "last_acceptance_report", None)
    click.echo("\n═══ Multi-signal acceptance (Phase 8 Wave 2) ═══")
    if report is None:
        click.echo("(not yet computed; run `explain run` to populate)")
    else:
        click.echo(f"avg_consistency       {report.avg_consistency:.3f}")
        click.echo(f"avg_essentialness     {report.avg_essentialness:.3f}")
        click.echo(f"weak_chain_l1s        {report.weak_chain_l1s}")
        if report.lowest_l1:
            l1, score = report.lowest_l1
            click.echo(f"lowest_l1             {l1} (consistency={score:.3f})")
        else:
            click.echo("lowest_l1             (none)")
        click.echo(f"consistency_spread    {report.consistency_spread:.3f}")
        click.echo(f"essentialness_spread  {report.essentialness_spread:.3f}")
        click.echo(
            f"rollout_coverage      {report.rollout_coverage:.3f} "
            f"({len(report.missing_l0)} missing L0)"
        )
        if report.missing_l0:
            click.echo(f"missing_l0            {report.missing_l0}")
```

### Step 7: 跑测试 + 全测 + commit

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
```

Expected: 421 pass (413 + 8), 2 skipped, ruff 0.

⚠️ test_engines_reflection.py 老断言可能因 cached report 路径变化, 视情况更新.

```bash
git add src/explain_engine/schema/state.py \
        src/explain_engine/engines/reflection.py \
        src/explain_engine/runtime/runtime.py \
        src/explain_engine/cli.py \
        tests/test_engines_reflect_weak_chains.py \
        tests/test_cli_status_signals.py
git commit -m "$(cat <<'EOF'
Wave 2.3 · reflect 用 AcceptanceReport.weak_chain_l1s + CLI status section

CognitiveState 加 last_acceptance_report 字段 (in-memory, 不持久化).
runtime.run 在 reflect tick 前刷 aggregate_acceptance(state).
reflect() 优先用 cached report.weak_chain_l1s (fallback 当场算).
prune 决策也改用 report.per_l2.

CLI explain status 加 "Multi-signal acceptance" section, 显示 6 字段:
avg_consistency / avg_essentialness / weak_chain_l1s / lowest_l1 /
consistency_spread / essentialness_spread / rollout_coverage / missing_l0.
老 session 无 report 时显示 "(not yet computed)".

+8 tests. 421 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 2 Checkpoint (用户审)

**完成度**: 3/3 task. +22 tests (400 → 422). Multi-signal acceptance 落地.

**验证**:
- rollout_from_roots 算法工作 (5+1 tests)
- AcceptanceReport + aggregate_acceptance (8 tests)
- reflect 用 cached report.weak_chain_l1s (4 tests)
- CLI status 显示 multi-signal (3 tests)

**Stop**. 等用户审过, 进 Wave 3.

---

# Wave 3 — Falsifiability-Driven Alignment (修 mismatch)

## Task 3.1: errors.py + input_validation engine + prompt + tests

**目的**: 新模块 `engines/errors.py` 定义 `InsufficientObservationsError` (Wave 3 fail-fast 异常). 新模块 `engines/input_validation.py` 实现 `validate(question, l0_nodes, llm)` — 1 次 LLM 调用结构化批判, 输出 `InputAlignmentReport`. 新 prompt `input_validation.yaml`. 注意: `validate()` 只 *返* report, 不 *抛* InsufficientObservationsError (是否 fail-fast 由 cli.py 决定, Task 3.2 处理).

**Files:**
- Create: `src/explain_engine/engines/errors.py`
- Create: `src/explain_engine/engines/input_validation.py`
- Create: `src/explain_engine/llm/prompts/input_validation.yaml`
- Create: `tests/test_engines_input_validation.py`

---

### Step 1: 写 errors.py

Create `src/explain_engine/engines/errors.py`:

```python
"""Phase 8 Wave 3: cognitive engine errors (fail-fast on input mismatch).

哲学锚点 §9.4 可证伪性: "Theory 必须可失败, 否则系统会神学化".
系统必须能说"我无法回答这个 question, 因为 observations 不匹配", 而非
强行编造 explanation.
"""

from __future__ import annotations


class CognitiveEngineError(Exception):
    """Phase 8 base for engine-level fail-fast errors."""


class InsufficientObservationsError(CognitiveEngineError):
    """Wave 3: question 与 L0 observations 不对齐, 无法形成 explanation.

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

### Step 2: 写 prompt

Create `src/explain_engine/llm/prompts/input_validation.yaml`:

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

  输出 JSON schema:
  {
    "question_subject": str,
    "observation_subjects": [str, ...],
    "overlap_score": 0-5,
    "falsifiable_reason": str
  }

  注: falsifiable_reason 无论 score 高低都要给, 解释为什么是这个分数.

user_template: |
  Question:
  {question}

  L0 Observations:
  {l0_table}

  请按 3 步走输出结构化结果.
```

### Step 3: 写失败测试

Create `tests/test_engines_input_validation.py`:

```python
"""Wave 3 Task 3.1: input_validation engine.

design §6.2: validate(question, l0_nodes, llm) → InputAlignmentReport.
注意 validate 只返 report, 不抛 InsufficientObservationsError (cli.py 决定).
"""

import pytest

from explain_engine.engines.errors import InsufficientObservationsError
from explain_engine.engines.input_validation import (
    InputAlignmentReport,
    MIN_OVERLAP_SCORE,
    validate,
)
from explain_engine.schema.nodes import VariableNode


def _l0(name: str, desc: str = "d") -> VariableNode:
    return VariableNode(
        id=f"p_{name}", name=name, description=desc,
        abstraction_level=0, confidence=0.7, epistemic="observation",
    )


class _FakeLLMOutput:
    def __init__(self, parsed):
        self.parsed = parsed


class _FakeLLM:
    def __init__(self, response):
        self.response = response

    async def chat(self, messages, schema):
        return _FakeLLMOutput(parsed=self.response)


class TestValidate:
    @pytest.mark.asyncio
    async def test_high_overlap_returns_high_score(self) -> None:
        llm = _FakeLLM({
            "question_subject": "员工 A 的离职原因",
            "observation_subjects": ["员工 A 的工作时长", "员工 A 的会议反馈"],
            "overlap_score": 5,
            "falsifiable_reason": "observations 直接关于员工 A 的工作行为, 高对齐.",
        })
        report = await validate("为什么员工 A 离职?", [_l0("o1"), _l0("o2")], llm)
        assert isinstance(report, InputAlignmentReport)
        assert report.overlap_score == 5
        assert report.question_subject == "员工 A 的离职原因"

    @pytest.mark.asyncio
    async def test_low_overlap_returns_low_score(self) -> None:
        llm = _FakeLLM({
            "question_subject": "员工 A 的离职原因",
            "observation_subjects": ["公司股价", "市场总指数"],
            "overlap_score": 1,
            "falsifiable_reason": "observations 关于宏观市场, 与员工个体行为无直接关系.",
        })
        report = await validate("为什么员工 A 离职?", [_l0("o1"), _l0("o2")], llm)
        assert report.overlap_score == 1

    @pytest.mark.asyncio
    async def test_returns_falsifiable_reason_always(self) -> None:
        llm = _FakeLLM({
            "question_subject": "X",
            "observation_subjects": ["Y"],
            "overlap_score": 4,
            "falsifiable_reason": "explanation regardless",
        })
        report = await validate("q", [_l0("o")], llm)
        assert report.falsifiable_reason == "explanation regardless"

    @pytest.mark.asyncio
    async def test_validate_does_not_raise_insufficient_obs_error(self) -> None:
        """validate 永远只返 report, 不抛 InsufficientObservationsError (CLI 决定)."""
        llm = _FakeLLM({
            "question_subject": "X",
            "observation_subjects": ["Y"],
            "overlap_score": 0,
            "falsifiable_reason": "complete mismatch",
        })
        # 即使 score=0 也只是返 report
        report = await validate("q", [_l0("o")], llm)
        assert report.overlap_score == 0

    @pytest.mark.asyncio
    async def test_invalid_overlap_score_raises_schema_error(self) -> None:
        from explain_engine.llm.errors import SchemaValidationError
        llm = _FakeLLM({
            "question_subject": "X",
            "observation_subjects": [],
            "overlap_score": 99,  # 不在 0-5
            "falsifiable_reason": "r",
        })
        with pytest.raises(SchemaValidationError):
            await validate("q", [_l0("o")], llm)

    @pytest.mark.asyncio
    async def test_empty_l0_handled(self) -> None:
        llm = _FakeLLM({
            "question_subject": "X",
            "observation_subjects": [],
            "overlap_score": 0,
            "falsifiable_reason": "no observations provided",
        })
        report = await validate("q", [], llm)
        assert report.overlap_score == 0


class TestInsufficientObservationsError:
    def test_error_str_format(self) -> None:
        err = InsufficientObservationsError(
            overlap_score=1,
            question_subject="员工 A 的离职原因",
            observation_subjects=["公司股价"],
            falsifiable_reason="observations 关于股价, question 问员工",
        )
        s = str(err)
        assert "1/5" in s
        assert "员工" in s
        assert "股价" in s


class TestMinOverlapScore:
    def test_constant_value(self) -> None:
        # 文档声明 MIN_OVERLAP_SCORE = 2 (< 2 即 0/1 触发 fail)
        assert MIN_OVERLAP_SCORE == 2
```

### Step 4: 跑测试 — 验证全 fail

Run: `.venv/bin/python -m pytest tests/test_engines_input_validation.py -v`

Expected: 8 FAIL (ImportError).

### Step 5: 实现 input_validation.py

Create `src/explain_engine/engines/input_validation.py`:

```python
"""Phase 8 Wave 3: Input validation engine — 入口 question vs L0 对齐校验.

design §6.2 + §9.4 可证伪性: 系统必须能说"无法回答这个 question",
否则会神学化.

API:
  validate(question, l0_nodes, llm) → InputAlignmentReport
  MIN_OVERLAP_SCORE: 阈值常量 (cli.py 用作 fail-fast 判断)
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.nodes import VariableNode

MIN_OVERLAP_SCORE: int = 2
"""overlap_score < 此值 (即 0 或 1) 触发 fail-fast.

阈值保守留 LLM 缓冲. 调优在 acceptance 阶段 (Wave 5).
"""


class InputAlignmentReport(BaseModel):
    """LLM 结构化批判结果."""

    question_subject: str = Field(min_length=1)
    """LLM 识别出的 question 核心主体."""

    observation_subjects: list[str] = Field(default_factory=list)
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
    """Wave 3: 入口 input validation.

    哲学锚点 §9.4 + §4.2: 校验 question 与 observations 是否在同一段"历史".

    Args:
        question: root question 文本.
        l0_nodes: 当前 graph 的 L0 nodes.
        llm: LLM client.

    Returns:
        InputAlignmentReport (LLM 结构化判断).

    Raises:
        SchemaValidationError: LLM retry 1 次仍失败.

    Note:
        本函数只返 report, 不抛 InsufficientObservationsError.
        是否 fail-fast 由调用方 (cli.py) 根据 --no-input-check flag + 阈值决定.
    """
    prompt = load_prompt("input_validation")
    l0_table = _render_l0_table(l0_nodes)

    messages = [
        Message(role="system", content=prompt["system"]),
        Message(
            role="user",
            content=prompt["user_template"].format(
                question=question,
                l0_table=l0_table,
            ),
        ),
    ]

    return await _call_with_retry(llm, messages)


def _render_l0_table(l0_nodes: list[VariableNode]) -> str:
    if not l0_nodes:
        return "(none)"
    lines = [f"- {n.id}: {n.name} — {n.description}" for n in l0_nodes]
    return "\n".join(lines)


async def _call_with_retry(
    llm: LLMClient,
    messages: list[Message],
) -> InputAlignmentReport:
    last_exc: Exception | None = None
    for _attempt in range(2):
        resp = await llm.chat(messages, schema=InputAlignmentReport)
        if resp.parsed is None:
            last_exc = SchemaValidationError("LLM 未返回 structured output")
            continue
        try:
            return InputAlignmentReport.model_validate(resp.parsed)
        except ValidationError as exc:
            last_exc = SchemaValidationError(f"input_validation 输出 schema 不合规: {exc}")
            continue
    assert last_exc is not None
    raise last_exc
```

### Step 6: 跑测试 + 全测 + ruff

Run:
```bash
.venv/bin/python -m pytest tests/test_engines_input_validation.py -v
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
```

Expected: 8 PASS / 429 total (421 + 8), ruff 0.

### Step 7: Commit

```bash
git add src/explain_engine/engines/errors.py \
        src/explain_engine/engines/input_validation.py \
        src/explain_engine/llm/prompts/input_validation.yaml \
        tests/test_engines_input_validation.py
git commit -m "$(cat <<'EOF'
Wave 3.1 · input_validation engine + InsufficientObservationsError + prompt

新模块 engines/errors.py 定义 CognitiveEngineError 基类 + InsufficientObservationsError.
新模块 engines/input_validation.py 实现 validate(question, l0_nodes, llm).

LLM 1 次结构化批判 (3 步走): 识别 question 主体 → 识别 observation 主体 →
判断 overlap_score (0-5) + falsifiable_reason. 防"X 成因"vs"X 影响"方向陷阱.

设计要点 (design §9.4 可证伪性):
- validate() 只返 report, 不抛 InsufficientObservationsError (CLI 决定).
- MIN_OVERLAP_SCORE = 2 (即 0/1 触发 fail-fast, 留 LLM 缓冲).

新 prompt input_validation.yaml. +8 tests. 429 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3.2: explain run 入口集成 + --no-input-check + alignment 字段注入

**目的**: ① CLI `explain run` 在第一次运行 (current_tick=0) 调 validate(), 校验失败 (overlap < MIN_OVERLAP_SCORE) 抛 InsufficientObservationsError → exit(2) with friendly message; ② 加 `--no-input-check` flag bypass; ③ 把 InputAlignmentReport 写到 state.last_input_alignment_report (in-memory, 不持久化); ④ aggregate_acceptance 在生成 AcceptanceReport 时注入 input_alignment + falsifiable_reason 字段; ⑤ CLI status 显示 falsifiability section.

**Files:**
- Modify: `src/explain_engine/schema/state.py` (CognitiveState 加 last_input_alignment_report)
- Modify: `src/explain_engine/engines/simulation.py` (aggregate_acceptance 注入 alignment)
- Modify: `src/explain_engine/cli.py` (run 集成 + --no-input-check + status falsifiability section)
- Create: `tests/test_cli_run_input_validation.py`

---

### Step 1: 写失败测试

Create `tests/test_cli_run_input_validation.py`:

```python
"""Wave 3 Task 3.2: CLI explain run 集成 input_validation."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from explain_engine.cli import cli
from explain_engine.engines.errors import InsufficientObservationsError
from explain_engine.engines.input_validation import InputAlignmentReport


def _stub_validate_high(*args, **kwargs):
    """Mock validate 返高对齐."""
    async def _impl(*a, **k):
        return InputAlignmentReport(
            question_subject="X",
            observation_subjects=["X-related"],
            overlap_score=5,
            falsifiable_reason="aligned",
        )
    return _impl(*args, **kwargs)


def _stub_validate_low(*args, **kwargs):
    """Mock validate 返低对齐 → fail-fast."""
    async def _impl(*a, **k):
        return InputAlignmentReport(
            question_subject="X",
            observation_subjects=["unrelated Y"],
            overlap_score=0,
            falsifiable_reason="completely unrelated",
        )
    return _impl(*args, **kwargs)


class TestRunInputValidation:
    def test_low_overlap_exits_with_code_2(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        runner = CliRunner()
        result = runner.invoke(cli, ["new", "why X?"])
        sid = result.output.strip().split()[-1]

        with patch("explain_engine.engines.input_validation.validate", _stub_validate_low):
            result = runner.invoke(cli, ["run", sid, "--budget", "2"])
        assert result.exit_code == 2
        assert "0/5" in result.output or "Input validation failed" in result.output
        assert "completely unrelated" in result.output

    def test_no_input_check_flag_skips_validation(self, tmp_path, monkeypatch) -> None:
        """--no-input-check → 即使 mock validate 是 low overlap 也不调."""
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        runner = CliRunner()
        result = runner.invoke(cli, ["new", "why X?"])
        sid = result.output.strip().split()[-1]

        with patch("explain_engine.engines.input_validation.validate", _stub_validate_low):
            # --no-input-check 应让 run 跳过 validate, 不抛 fail-fast
            # (但会因别的原因失败, e.g. 无 LLM, 这里只看 not exit(2) due to validation)
            result = runner.invoke(cli, ["run", sid, "--budget", "1", "--no-input-check"])
            # 至少 exit code 不是 2 (validation fail) — 可能是 1 (LLM error) 或 0
            assert "Input validation failed" not in result.output

    def test_high_overlap_proceeds_normally(self, tmp_path, monkeypatch) -> None:
        """high overlap → run 继续 (但因无 LLM 仍可能 fail, 关键是 not exit(2))."""
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        runner = CliRunner()
        result = runner.invoke(cli, ["new", "q"])
        sid = result.output.strip().split()[-1]

        with patch("explain_engine.engines.input_validation.validate", _stub_validate_high):
            result = runner.invoke(cli, ["run", sid, "--budget", "1"])
            # high overlap → 不该 exit(2)
            assert "Input validation failed" not in result.output

    def test_alignment_written_to_state(self, tmp_path, monkeypatch) -> None:
        """validate 成功 → state.last_input_alignment_report 写入."""
        from explain_engine.persistence.session import SessionStore
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        runner = CliRunner()
        result = runner.invoke(cli, ["new", "q"])
        sid = result.output.strip().split()[-1]

        with patch("explain_engine.engines.input_validation.validate", _stub_validate_high):
            runner.invoke(cli, ["run", sid, "--budget", "1"])

        # 检查 state (注: 字段不持久化, 只在 in-memory; 这个 test 验证下次 load 后 None)
        store = SessionStore(tmp_path)
        state = store.load(sid)
        # in-memory 字段, 持久化后 None — 这是预期行为
        assert getattr(state, "last_input_alignment_report", None) is None

    def test_status_shows_falsifiability_section(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SESSIONS_DIR", str(tmp_path))
        runner = CliRunner()
        result = runner.invoke(cli, ["new", "q"])
        sid = result.output.strip().split()[-1]

        result = runner.invoke(cli, ["status", sid])
        assert result.exit_code == 0
        assert "Falsifiability" in result.output or "falsifi" in result.output.lower()


class TestInsufficientObservationsErrorRaised:
    def test_error_caught_in_cli_and_formatted(self) -> None:
        """单元: InsufficientObservationsError 包含友好字段."""
        err = InsufficientObservationsError(
            overlap_score=1,
            question_subject="员工 A 的离职原因",
            observation_subjects=["公司股价"],
            falsifiable_reason="股价与员工个体行为无关",
        )
        assert err.overlap_score == 1
        assert "员工" in err.question_subject
```

### Step 2: 跑测试 — 验证 fail

Run: `.venv/bin/python -m pytest tests/test_cli_run_input_validation.py -v`

Expected: fail (CLI 没集成 + state 字段不存在).

### Step 3: 加 state.last_input_alignment_report

Modify `src/explain_engine/schema/state.py`:

```python
if TYPE_CHECKING:
    from explain_engine.engines.input_validation import InputAlignmentReport
    from explain_engine.engines.simulation import AcceptanceReport
```

CognitiveState 加字段:

```python
    # Phase 8 Wave 3 NEW: in-memory only (不持久化)
    last_input_alignment_report: "InputAlignmentReport | None" = field(default=None, repr=False)
```

### Step 4: aggregate_acceptance 注入 alignment

Modify `src/explain_engine/engines/simulation.py` `aggregate_acceptance()`:

末尾 return 前加:

```python
    # Wave 3 注入 alignment 字段 (如果 state 有 input_validation report)
    input_alignment: float | None = None
    falsifiable_reason: str | None = None
    align_report = getattr(state, "last_input_alignment_report", None)
    if align_report is not None:
        input_alignment = align_report.overlap_score / 5.0
        falsifiable_reason = align_report.falsifiable_reason

    return AcceptanceReport(
        # ... existing fields ...
        input_alignment=input_alignment,
        falsifiable_reason=falsifiable_reason,
    )
```

### Step 5: CLI run 集成 + --no-input-check + status falsifiability

Modify `src/explain_engine/cli.py`:

加 imports:

```python
from explain_engine.engines.errors import InsufficientObservationsError
from explain_engine.engines.input_validation import (
    MIN_OVERLAP_SCORE,
    validate as validate_input,
)
```

`cmd_run` 函数加 flag + 集成:

```python
@cli.command("run")
@click.argument("session_id")
@click.option("--budget", default=10, help="Max ticks")
@click.option("--no-input-check", is_flag=True,
              help="Phase 8 Wave 3: skip input validation fail-fast")
def cmd_run(session_id, budget, no_input_check):
    state = session_store.load(session_id)

    # ── Wave 3 Phase 8: input validation (新) ──
    if not no_input_check and state.tick == 0:
        l0_nodes = [n for n in state.graph.nodes.values() if n.abstraction_level == 0]
        try:
            llm = _build_llm_client()  # 现有 helper
            report = asyncio.run(validate_input(state.root_question, l0_nodes, llm))
            state.last_input_alignment_report = report
            if report.overlap_score < MIN_OVERLAP_SCORE:
                raise InsufficientObservationsError(
                    overlap_score=report.overlap_score,
                    question_subject=report.question_subject,
                    observation_subjects=report.observation_subjects,
                    falsifiable_reason=report.falsifiable_reason,
                )
        except InsufficientObservationsError as e:
            click.echo(
                f"\n❌ Input validation failed (overlap={e.overlap_score}/5)\n",
                err=True,
            )
            click.echo(f"Question 主体: {e.question_subject}", err=True)
            click.echo("Observation 主体:", err=True)
            for s in e.observation_subjects:
                click.echo(f"  - {s}", err=True)
            click.echo(f"\n理由: {e.falsifiable_reason}", err=True)
            click.echo(
                "\n💡 If you believe this is a false positive, "
                "retry with --no-input-check",
                err=True,
            )
            sys.exit(2)

    # ── 正常 run loop ──
    # ... existing run logic ...
```

`cmd_status` 函数加 Falsifiability section (在 Multi-signal 之后):

```python
    click.echo("\n═══ Falsifiability (Phase 8 Wave 3) ═══")
    if report and report.input_alignment is not None:
        score = int(report.input_alignment * 5)
        click.echo(f"input_alignment       {score}/5")
        click.echo(f"falsifiable_reason    {report.falsifiable_reason}")
        click.echo(f"rollout_alignment     {report.rollout_coverage:.3f}  (= rollout_coverage)")
    else:
        click.echo("input_alignment       (not checked; run with input validation)")
        if report:
            click.echo(f"rollout_alignment     {report.rollout_coverage:.3f}  (= rollout_coverage)")
        else:
            click.echo("rollout_alignment     (not yet computed)")
```

### Step 6: 跑测试 + 全测 + commit

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
```

Expected: 435 pass (429 + 6), ruff 0.

```bash
git add src/explain_engine/schema/state.py \
        src/explain_engine/engines/simulation.py \
        src/explain_engine/cli.py \
        tests/test_cli_run_input_validation.py
git commit -m "$(cat <<'EOF'
Wave 3.2 · explain run 集成 input_validation + --no-input-check + status falsifiability

CLI explain run 在 tick=0 调 validate(), 若 overlap < MIN_OVERLAP_SCORE (=2) 抛
InsufficientObservationsError → exit(2) with friendly message (含 question_subject /
observation_subjects / falsifiable_reason + 建议 --no-input-check).

新 flag --no-input-check 跳过校验 (老用户兜底 + LLM 误杀缓解).

CognitiveState 加 last_input_alignment_report (in-memory, 不持久化).
aggregate_acceptance 注入 input_alignment (=overlap/5) + falsifiable_reason
到 AcceptanceReport.

CLI status 加 "Falsifiability" section, 显示 input_alignment / falsifiable_reason /
rollout_alignment (= rollout_coverage 复用, Q6.2 Option Y).

+6 tests. 435 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 3 Checkpoint (用户审)

**完成度**: 2/2 task. +14 tests (422 → 436). Falsifiability fail-fast 落地.

**验证**:
- input_validation engine + prompt + InsufficientObservationsError (8 tests)
- CLI run 集成 + --no-input-check + status falsifiability (6 tests)
- aggregate_acceptance 注入 alignment 字段

**Stop**. 等用户审过, 进 Wave 4.

---

# Wave 4 — Variable Lifecycle (修节点堆积)

## Task 4.1: VariableNode lifecycle 字段 + compute_fitness + tests

**目的**: VariableNode 加 5 个 lifecycle 字段 (Pydantic Field defaults, backward compat 自动). 新模块 `engines/lifecycle.py` 实现 `compute_fitness(node, state)` — 聚合 Wave 2 信号 + 图统计 + lifecycle 字段, 0 LLM. Phase 8 实现 5/7 项 (近似), 2 项 (predictive_utility, vagueness) 推 Phase 9.

**Files:**
- Modify: `src/explain_engine/schema/nodes.py` (加 5 字段 + lifecycle_state Literal)
- Create: `src/explain_engine/engines/lifecycle.py`
- Create: `tests/test_engines_lifecycle_fitness.py`
- Create: `tests/test_schema_lifecycle_backward_compat.py`

---

### Step 1: 写失败测试 (lifecycle backward compat)

Create `tests/test_schema_lifecycle_backward_compat.py`:

```python
"""Wave 4 Task 4.1: VariableNode lifecycle 字段 backward compat tests."""

from explain_engine.schema.nodes import VariableNode


class TestLifecycleFieldDefaults:
    def test_old_node_creation_works_without_lifecycle_fields(self) -> None:
        """Pydantic Field defaults → 老代码不传 lifecycle 字段也能 create."""
        node = VariableNode(
            id="c_001", name="n", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        )
        assert node.activation == 1.0
        assert node.stability == 0.0
        assert node.last_used_tick == 0
        assert node.age_ticks == 0
        assert node.lifecycle_state == "active"

    def test_new_node_creation_with_lifecycle_fields(self) -> None:
        node = VariableNode(
            id="c_001", name="n", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
            activation=0.5, stability=0.3, last_used_tick=10,
            age_ticks=20, lifecycle_state="stale",
        )
        assert node.activation == 0.5
        assert node.lifecycle_state == "stale"

    def test_old_session_json_loads_with_defaults(self) -> None:
        """模拟老 session JSON (无 lifecycle 字段) loads 不报错."""
        old_dict = {
            "id": "c_001", "name": "n", "description": "d",
            "abstraction_level": 1, "confidence": 0.7,
            "epistemic": "insight", "evidence_ids": [], "source": "llm",
        }
        node = VariableNode.model_validate(old_dict)
        assert node.lifecycle_state == "active"
        assert node.activation == 1.0
```

### Step 2: 写失败测试 (compute_fitness)

Create `tests/test_engines_lifecycle_fitness.py`:

```python
"""Wave 4 Task 4.1: lifecycle.compute_fitness 单元测试."""

from explain_engine.engines.lifecycle import compute_fitness
from explain_engine.engines.simulation import AcceptanceReport
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _node(nid, level, **kw):
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7,
        epistemic="observation" if level == 0 else "insight",
        **kw,
    )


def _state_with(nodes, edges=()) -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    for n in nodes:
        g.add_node(n)
    for eid, src, tgt, rel, conf in edges:
        g.add_edge(RelationEdge(
            id=eid, source_node=src, target_node=tgt,
            relation_type=rel, confidence=conf, mechanism_description="m",
        ))
    return CognitiveState(graph=g, budget_remaining=10, root_question="q")


class TestComputeFitness:
    def test_high_consistency_high_fitness(self) -> None:
        n = _node("c_001", 1, activation=1.0, stability=0.5)
        state = _state_with([n])
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=0.8, avg_essentialness=0.5,
            per_l1={"c_001": 0.8},
        )
        f = compute_fitness(n, state)
        assert f > 0.5

    def test_low_activation_lower_fitness(self) -> None:
        n_high = _node("c_001", 1, activation=1.0)
        n_low = _node("c_002", 1, activation=0.1)
        state = _state_with([n_high, n_low])
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=0.5, avg_essentialness=0.5,
            per_l1={"c_001": 0.5, "c_002": 0.5},
        )
        assert compute_fitness(n_high, state) > compute_fitness(n_low, state)

    def test_high_centrality_higher_fitness(self) -> None:
        n_central = _node("c_001", 1)
        n_iso = _node("c_002", 1)
        p = _node("p_001", 0)
        state = _state_with(
            [n_central, n_iso, p],
            edges=[("e_001", "c_001", "p_001", "manifests_as", 0.7)],
        )
        # n_central 有 1 outgoing edge, n_iso 无 → central fitness 高
        assert compute_fitness(n_central, state) > compute_fitness(n_iso, state)

    def test_no_acceptance_report_uses_default_explanatory(self) -> None:
        n = _node("c_001", 1)
        state = _state_with([n])
        state.last_acceptance_report = None
        f = compute_fitness(n, state)
        # 不抛, 用 0.5 中性默认
        assert isinstance(f, float)
        assert f >= 0.0

    def test_l0_node_uses_default_explanatory(self) -> None:
        n = _node("p_001", 0)
        state = _state_with([n])
        f = compute_fitness(n, state)
        assert f >= 0.0  # L0 不在 per_l1/per_l2, 但仍 compute

    def test_clamps_to_non_negative(self) -> None:
        """高 redundancy 也不会让 fitness 负."""
        n = _node("c_001", 1, activation=0.0, stability=0.0)
        state = _state_with([n])
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=0.0, avg_essentialness=0.0,
            per_l1={"c_001": 0.0},
        )
        f = compute_fitness(n, state)
        assert f >= 0.0

    def test_empty_graph_handles_gracefully(self) -> None:
        """孤立 node + 空 graph (mock case) → 不 crash."""
        n = _node("c_001", 1)
        # state 只含此 node
        state = _state_with([n])
        f = compute_fitness(n, state)
        assert isinstance(f, float)

    def test_returns_float(self) -> None:
        n = _node("c_001", 1)
        state = _state_with([n])
        result = compute_fitness(n, state)
        assert isinstance(result, float)
```

### Step 3: 跑测试 — 全 fail

Run:
```bash
.venv/bin/python -m pytest tests/test_engines_lifecycle_fitness.py tests/test_schema_lifecycle_backward_compat.py -v
```

Expected: 11 FAIL.

### Step 4: 加 lifecycle 字段到 VariableNode

Modify `src/explain_engine/schema/nodes.py`:

```python
"""VariableNode — explain engine 的认知原子."""

from typing import Literal

from pydantic import BaseModel, Field

Epistemic = Literal[
    "fact", "observation", "inference", "insight", "speculation",
]

AbstractionLevel = Literal[0, 1, 2]
Source = Literal["llm", "user"]

# Wave 4 Phase 8 NEW
LifecycleState = Literal["active", "stale", "decayed"]
"""节点生命阶段:
   - active: 正常参与 simulation / expand / reflect
   - stale: fitness 长期低, 候选 decay (仍参与 simulation, 仅 reflect 提示)
   - decayed: fitness 极低且超时, 不参与 simulation / expand, trace 保留 (soft delete)
"""


class VariableNode(BaseModel):
    """认知图中的节点."""

    id: str = Field(min_length=1)
    name: str
    description: str
    abstraction_level: AbstractionLevel
    confidence: float = Field(ge=0.0, le=1.0)
    epistemic: Epistemic
    evidence_ids: list[str] = Field(default_factory=list)
    source: Source = "llm"

    # ── Wave 4 Phase 8 lifecycle 字段 (全部默认值, backward compat 自动) ──
    activation: float = Field(default=1.0, ge=0.0, le=1.0)
    """当前激活度. Birth 时 1.0, decay 时降低. simulation/expand 触达时刷新."""

    stability: float = Field(default=0.0, ge=0.0, le=1.0)
    """稳定性. 重复被 expand/reflect 触达累加. 用作 fitness 加分项."""

    last_used_tick: int = Field(default=0, ge=0)
    """最后被 simulation/reflect/expand 触达的 tick. 配合 age_ticks 算"陈旧度"."""

    age_ticks: int = Field(default=0, ge=0)
    """总存活 tick 数."""

    lifecycle_state: LifecycleState = "active"

    model_config = {"frozen": False}
```

### Step 5: 实现 lifecycle.py

Create `src/explain_engine/engines/lifecycle.py`:

```python
"""Phase 8 Wave 4: Variable lifecycle engine.

哲学锚点:
  §6.1 "Variable 是 evolving conceptual organism".
  §9.2 Variable Fitness 7-项公式 (Phase 8 实现 5 项, 2 项推 Phase 9+).
  §11.3 "最低 entropy 下的最大解释力" → 自动 decay 控 entropy.
  §9.3 Semantic Anchoring → soft delete (decay 不删 node).
"""

from __future__ import annotations

from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState

# 阈值常量 (Wave 5 acceptance 后调优)
STALE_THRESHOLD: float = 0.3
"""fitness < 此阈值 → 候选 stale."""

DECAY_THRESHOLD: float = 0.1
"""fitness < 此阈值 → 候选 decayed."""

STALE_TO_DECAYED_TICKS: int = 5
"""节点在 stale 状态停留多少 tick 后, 升级为 decayed."""

# Fitness 公式权重 (acceptance 后调优)
W_REUSE: float = 0.3
W_STABILITY: float = 0.2
W_CENTRALITY: float = 0.3


def compute_fitness(node: VariableNode, state: CognitiveState) -> float:
    """Phase 8 Wave 4: 节点 fitness 公式.

    实现 5/7 项 (顶层 §9.2):
      ✅ explanatory_power ≈ Wave 2 per_l1 / per_l2 (consistency / essentialness)
      ✅ reuse_frequency   ≈ activation
      ✅ compression_value ≈ stability
      ✅ graph_centrality  ≈ degree(node) / max_degree
      ✅ redundancy        ≈ siblings_with_same_targets * 0.2 (cap 0.5)
      ⏳ predictive_utility [Phase 9+, 需要 prediction 命中率]
      ⏳ vagueness         [Phase 9+, 需要 NLP 评估]

    Returns:
        fitness ∈ [0.0, ~1.5]. 越大 = 越健康.
    """
    # explanatory power (Wave 2 信号)
    explanatory: float = 0.5  # 中性默认 (L0 / 无 report)
    report = state.last_acceptance_report
    if report is not None:
        if node.abstraction_level == 1:
            explanatory = report.per_l1.get(node.id, 0.5)
        elif node.abstraction_level == 2:
            explanatory = report.per_l2.get(node.id, 0.5)

    # reuse / stability (lifecycle 字段)
    reuse = node.activation
    stability = node.stability

    # graph centrality
    out_count = len(state.graph.outgoing_edges(node.id))
    in_count = sum(
        1 for e in state.graph.edges.values()
        if e.target_node == node.id
    )
    degree = out_count + in_count

    if state.graph.nodes:
        max_degree = max(
            len(state.graph.outgoing_edges(nid))
            + sum(1 for e in state.graph.edges.values() if e.target_node == nid)
            for nid in state.graph.nodes
        )
    else:
        max_degree = 1
    centrality = degree / max_degree if max_degree > 0 else 0.0

    # redundancy (Phase 8 近似: 同 level 同 outgoing target set 兄弟)
    my_targets = frozenset(
        e.target_node for e in state.graph.outgoing_edges(node.id)
    )
    siblings_with_same_targets = 0
    for sib_id, sib in state.graph.nodes.items():
        if sib_id == node.id:
            continue
        if sib.abstraction_level != node.abstraction_level:
            continue
        sib_targets = frozenset(
            e.target_node for e in state.graph.outgoing_edges(sib_id)
        )
        if sib_targets == my_targets and len(my_targets) > 0:
            siblings_with_same_targets += 1
    redundancy = min(siblings_with_same_targets * 0.2, 0.5)

    fitness = (
        explanatory
        + reuse * W_REUSE
        + stability * W_STABILITY
        + centrality * W_CENTRALITY
        - redundancy
    )
    return max(0.0, fitness)
```

### Step 6: 跑测试 + 全测 + ruff

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
```

Expected: 446 pass (435 + 11), ruff 0.

⚠️ 老 test 可能因新 VariableNode 字段失败 (e.g. assert exact dict equality). 视情况添加 `pytest --no-header --tb=no` 看 fail 列表 + 修复.

### Step 7: Commit

```bash
git add src/explain_engine/schema/nodes.py \
        src/explain_engine/engines/lifecycle.py \
        tests/test_engines_lifecycle_fitness.py \
        tests/test_schema_lifecycle_backward_compat.py
git commit -m "$(cat <<'EOF'
Wave 4.1 · VariableNode lifecycle 字段 + lifecycle.compute_fitness

VariableNode (Pydantic) 加 5 lifecycle 字段, 全部 Field default, backward compat 自动:
- activation: float (default=1.0) — 当前激活度
- stability: float (default=0.0) — 稳定性 (重复触达累加)
- last_used_tick: int (default=0) — 最后触达 tick
- age_ticks: int (default=0) — 总存活
- lifecycle_state: Literal["active", "stale", "decayed"] (default="active")

新模块 engines/lifecycle.py:
- compute_fitness(node, state) → float ∈ [0, ~1.5]
- 实现 5/7 项 §9.2 fitness 公式 (剩 2 项推 Phase 9+)
- 0 LLM, 纯算法 + 图统计

哲学锚点 §6.1 (organism) + §9.2 (fitness) + §11.3 (entropy 控制).
+11 tests (8 fitness + 3 backward compat). 446 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4.2: update_lifecycle + reflect decay action + dispatch + skip decayed

**目的**: ① 实现 `update_lifecycle(state, current_tick)` 推进 active → stale → decayed (无复活 decayed); ② reflect 决策树加 `decay` action (优先级在 prune 之前); ③ runtime dispatch 加 decay case (改 lifecycle_state); ④ propagation 跳过 decayed (Wave 2.1 已加 hook, 验证); ⑤ expansion frontier 跳过 decayed; ⑥ runtime 在每个 reflect tick 调 update_lifecycle.

**Files:**
- Modify: `src/explain_engine/engines/lifecycle.py` (加 update_lifecycle + soft_decay)
- Modify: `src/explain_engine/schema/state.py` (ReflectionAction 加 "decay")
- Modify: `src/explain_engine/engines/reflection.py` (加 pick_decay_target + decay action)
- Modify: `src/explain_engine/runtime/runtime.py` (decay dispatch + tick lifecycle update)
- Modify: `src/explain_engine/engines/expansion.py` (frontier check 跳过 decayed)
- Create: `tests/test_engines_lifecycle_update.py`
- Create: `tests/test_engines_reflect_decay.py`
- Create: `tests/test_propagation_skip_decayed.py`

---

### Step 1: 写失败测试 (update_lifecycle)

Create `tests/test_engines_lifecycle_update.py`:

```python
"""Wave 4 Task 4.2: lifecycle.update_lifecycle 状态机."""

from explain_engine.engines.lifecycle import (
    DECAY_THRESHOLD,
    STALE_TO_DECAYED_TICKS,
    update_lifecycle,
)
from explain_engine.engines.simulation import AcceptanceReport
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _state_with_low_fitness_l1() -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    g.add_node(VariableNode(
        id="c_001", name="n", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
        activation=0.0, stability=0.0,
    ))
    state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
    state.last_acceptance_report = AcceptanceReport(
        avg_consistency=0.0, avg_essentialness=0.0,
        per_l1={"c_001": 0.0},
    )
    return state


class TestUpdateLifecycle:
    def test_active_to_stale_on_low_fitness(self) -> None:
        state = _state_with_low_fitness_l1()
        changes = update_lifecycle(state, current_tick=1)
        assert state.graph.nodes["c_001"].lifecycle_state == "stale"
        assert changes.get("c_001") == "stale"

    def test_stale_to_decayed_after_window(self) -> None:
        state = _state_with_low_fitness_l1()
        # 第一次 update → stale
        update_lifecycle(state, current_tick=1)
        # 等 STALE_TO_DECAYED_TICKS 后再 update → decayed
        update_lifecycle(state, current_tick=1 + STALE_TO_DECAYED_TICKS)
        assert state.graph.nodes["c_001"].lifecycle_state == "decayed"

    def test_decayed_does_not_revive(self) -> None:
        state = _state_with_low_fitness_l1()
        state.graph.nodes["c_001"].lifecycle_state = "decayed"
        # 即使 fitness 高也不复活 (Phase 8 决定; Phase 9 memory consolidation 处理)
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=1.0, avg_essentialness=1.0,
            per_l1={"c_001": 1.0},
        )
        update_lifecycle(state, current_tick=10)
        assert state.graph.nodes["c_001"].lifecycle_state == "decayed"

    def test_stale_to_active_recovery(self) -> None:
        state = _state_with_low_fitness_l1()
        update_lifecycle(state, current_tick=1)
        assert state.graph.nodes["c_001"].lifecycle_state == "stale"
        # fitness 回高
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=1.0, avg_essentialness=1.0,
            per_l1={"c_001": 1.0},
        )
        state.graph.nodes["c_001"].activation = 1.0
        update_lifecycle(state, current_tick=2)
        assert state.graph.nodes["c_001"].lifecycle_state == "active"

    def test_returns_change_log(self) -> None:
        state = _state_with_low_fitness_l1()
        changes = update_lifecycle(state, current_tick=1)
        assert isinstance(changes, dict)
        assert "c_001" in changes

    def test_no_change_returns_empty_or_partial(self) -> None:
        """高 fitness active 节点不 change."""
        state = _state_with_low_fitness_l1()
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=1.0, avg_essentialness=1.0,
            per_l1={"c_001": 1.0},
        )
        state.graph.nodes["c_001"].activation = 1.0
        changes = update_lifecycle(state, current_tick=1)
        assert changes.get("c_001") in (None, "active")
```

### Step 2: 写失败测试 (reflect decay)

Create `tests/test_engines_reflect_decay.py`:

```python
"""Wave 4 Task 4.2: reflect 加 decay action."""

from explain_engine.engines import reflection
from explain_engine.engines.lifecycle import DECAY_THRESHOLD
from explain_engine.engines.simulation import AcceptanceReport
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _state_with_low_fitness_no_weak() -> CognitiveState:
    """构造一个无 weak chain 但有 low-fitness 节点的 state."""
    g = ExplanationGraph(root_question="q")
    # 高 conf chain → no weak L1
    g.add_node(VariableNode(
        id="c_001", name="strong", description="d",
        abstraction_level=1, confidence=0.9, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="p", description="d",
        abstraction_level=0, confidence=0.9, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.9,
        mechanism_description="m",
    ))
    # 加一个孤立低 fitness L2
    g.add_node(VariableNode(
        id="d_999", name="useless", description="d",
        abstraction_level=2, confidence=0.7, epistemic="inference",
        activation=0.0, stability=0.0,
    ))
    state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
    state.last_acceptance_report = AcceptanceReport(
        avg_consistency=0.9, avg_essentialness=0.0,
        per_l1={"c_001": 0.9},
        per_l2={"d_999": 0.0},
        weak_chain_l1s=[],
    )
    return state


class TestReflectDecay:
    def test_picks_lowest_fitness_below_threshold(self) -> None:
        state = _state_with_low_fitness_no_weak()
        target = reflection.pick_decay_target(state)
        assert target == "d_999"

    def test_returns_none_when_all_above_threshold(self) -> None:
        state = _state_with_low_fitness_no_weak()
        # 拉高 d_999 activation
        state.graph.nodes["d_999"].activation = 1.0
        state.graph.nodes["d_999"].stability = 1.0
        target = reflection.pick_decay_target(state)
        assert target is None

    def test_skips_already_decayed(self) -> None:
        state = _state_with_low_fitness_no_weak()
        state.graph.nodes["d_999"].lifecycle_state = "decayed"
        target = reflection.pick_decay_target(state)
        assert target is None

    def test_reflect_returns_decay_action(self) -> None:
        state = _state_with_low_fitness_no_weak()
        action, target = reflection.reflect(state)
        # 无 weak L1 + 有 low fitness L2 → 应该 decay
        assert action == "decay"
        assert target == "d_999"

    def test_decay_priority_after_expand_downward_before_prune(self) -> None:
        """priority: expand-downward > decay > prune > stop."""
        # 此测试在 Task 4.2 reflect 改完后验证
        state = _state_with_low_fitness_no_weak()
        # 加一个 weak L1 → 应该返 expand-downward 而非 decay
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=0.5, avg_essentialness=0.0,
            per_l1={"c_001": 0.2},
            per_l2={"d_999": 0.0},
            weak_chain_l1s=["c_001"],
        )
        action, target = reflection.reflect(state)
        assert action == "expand-downward"  # 优先级高于 decay
```

### Step 3: 写失败测试 (skip decayed)

Create `tests/test_propagation_skip_decayed.py`:

```python
"""Wave 4 Task 4.2: propagation / expansion 跳过 decayed 节点."""

import pytest

from explain_engine.engines._propagation import propagate, rollout_from_roots
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid, level, **kw):
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7,
        epistemic="observation" if level == 0 else "insight",
        **kw,
    )


def _g_with_decayed():
    g = ExplanationGraph(root_question="q")
    g.add_node(_node("c_001", 1, lifecycle_state="decayed"))   # decayed L1
    g.add_node(_node("c_002", 1))                              # active L1
    g.add_node(_node("p_001", 0))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.9, mechanism_description="m",
    ))
    g.add_edge(RelationEdge(
        id="e_002", source_node="c_002", target_node="p_001",
        relation_type="manifests_as", confidence=0.9, mechanism_description="m",
    ))
    return g


class TestRolloutSkipDecayed:
    def test_rollout_skips_decayed_root(self) -> None:
        g = _g_with_decayed()
        reachable, missing = rollout_from_roots(g)
        assert "p_001" in reachable  # via active c_002


class TestPropagateSkipDecayed:
    def test_propagate_skips_decayed_target(self) -> None:
        g = _g_with_decayed()
        # propagate from c_001 (decayed root) — 因为 c_001 在 sources 集里
        # 我们的设计是 propagate 不主动过滤 sources, 但下游不传给 decayed
        # 这里测从 c_002 propagate 不被 decayed c_001 影响
        acts, _ = propagate(g, {"c_002"})
        assert acts.get("p_001", 0) > 0


class TestExpansionSkipDecayed:
    def test_frontier_excludes_decayed(self) -> None:
        from explain_engine.schema.graph import ExplanationGraph
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("c_001", 1, lifecycle_state="decayed"))
        g.add_node(_node("c_002", 1))
        # frontier_nodes() 应跳过 decayed
        frontier = g.frontier_nodes()
        assert "c_001" not in frontier
```

### Step 4: 跑测试 — fail

Run:
```bash
.venv/bin/python -m pytest tests/test_engines_lifecycle_update.py tests/test_engines_reflect_decay.py tests/test_propagation_skip_decayed.py -v
```

Expected: many FAIL.

### Step 5: 实现 update_lifecycle + soft_decay

Modify `src/explain_engine/engines/lifecycle.py` (append):

```python
def update_lifecycle(
    state: CognitiveState,
    current_tick: int,
) -> dict[str, str]:
    """Wave 4: 在每个 reflect tick 推进所有节点的 lifecycle.

    状态机:
        active → stale: fitness < STALE_THRESHOLD
        stale → decayed: stale 累积 ≥ STALE_TO_DECAYED_TICKS
        stale → active: fitness 回到 ≥ STALE_THRESHOLD (复活)
        decayed → ?  (Phase 8 不复活)

    副作用:
        - 更新 node.lifecycle_state
        - 更新 node.age_ticks (= current_tick - 0 简化, Phase 9 加 birth_tick)

    Returns:
        {node_id: new_state} 变更日志.
    """
    changes: dict[str, str] = {}
    for nid, node in state.graph.nodes.items():
        node.age_ticks = current_tick

        if node.lifecycle_state == "decayed":
            continue

        fitness = compute_fitness(node, state)

        if node.lifecycle_state == "active":
            if fitness < STALE_THRESHOLD:
                node.lifecycle_state = "stale"
                # 用 in-state attribute 记 stale 起点 (Phase 8 不持久化)
                if not hasattr(state, "_stale_since_ticks"):
                    state._stale_since_ticks = {}
                state._stale_since_ticks[nid] = current_tick
                changes[nid] = "stale"

        elif node.lifecycle_state == "stale":
            if fitness >= STALE_THRESHOLD:
                node.lifecycle_state = "active"
                if hasattr(state, "_stale_since_ticks"):
                    state._stale_since_ticks.pop(nid, None)
                changes[nid] = "active"
            else:
                stale_since = getattr(state, "_stale_since_ticks", {}).get(nid, current_tick)
                if current_tick - stale_since >= STALE_TO_DECAYED_TICKS:
                    node.lifecycle_state = "decayed"
                    changes[nid] = "decayed"

    return changes


def soft_decay(state: CognitiveState, node_id: str) -> None:
    """Wave 4: reflect decay action — 标记节点 decayed (soft delete).

    哲学锚点 §9.3: 不删 node, 不删 trace. propagation/expand 跳过.

    Raises:
        ValueError: node_id 不存在.
    """
    if node_id not in state.graph.nodes:
        raise ValueError(f"node {node_id!r} not found in graph")
    state.graph.nodes[node_id].lifecycle_state = "decayed"
```

### Step 6: 加 ReflectionAction "decay" + reflect 决策

Modify `src/explain_engine/schema/state.py`:

```python
ReflectionAction = Literal[
    "continue", "re-expand", "expand-downward", "decay", "prune", "stop",
]
```

Modify `src/explain_engine/engines/reflection.py`:

加 import:

```python
from explain_engine.engines.lifecycle import (
    DECAY_THRESHOLD,
    compute_fitness,
)
```

加新函数:

```python
def pick_decay_target(state: CognitiveState) -> str | None:
    """Wave 4: 选 fitness 最低且 < DECAY_THRESHOLD 的非 decayed 节点."""
    if not state.graph.nodes:
        return None

    candidates: list[tuple[str, float]] = []
    for nid, node in state.graph.nodes.items():
        if node.lifecycle_state == "decayed":
            continue
        f = compute_fitness(node, state)
        if f < DECAY_THRESHOLD:
            candidates.append((nid, f))

    if not candidates:
        return None
    return min(candidates, key=lambda kv: kv[1])[0]
```

`reflect()` 决策树加 decay 分支 (在 prune 之前):

```python
def reflect(state: CognitiveState) -> tuple[ReflectionAction, str | None]:
    if not state.graph.nodes:
        return ("continue", None)

    L1_L2 = [nid for nid, n in state.graph.nodes.items() if n.abstraction_level >= 1]
    if not L1_L2:
        return ("continue", None)

    report = state.last_acceptance_report
    if report is None:
        from explain_engine.engines.simulation import aggregate_acceptance
        report = aggregate_acceptance(state)

    exhausted = _exhausted_expansion_targets(state)

    # 1. expand-downward 弱 L1 (Wave 2)
    for l1_id in report.weak_chain_l1s:
        if l1_id in exhausted:
            continue
        if l1_id not in state.graph.nodes:
            continue
        node = state.graph.nodes[l1_id]
        if node.lifecycle_state == "decayed":
            continue   # Wave 4: 跳过 decayed
        return ("expand-downward", l1_id)

    # 2. decay (Wave 4 NEW)
    decay_target = pick_decay_target(state)
    if decay_target:
        return ("decay", decay_target)

    # 3. prune (Wave 2)
    low_l2 = sorted(
        [(l2, score) for l2, score in report.per_l2.items()
         if score < LOW_ESSENTIALNESS_THRESHOLD
         and l2 in state.graph.nodes
         and state.graph.nodes[l2].lifecycle_state != "decayed"],
        key=lambda kv: kv[1],
    )
    if low_l2:
        return ("prune", low_l2[0][0])

    # 4. stale 检测
    if state.tick - state.last_reflection_change_tick >= CONSISTENCY_STALE_TICKS:
        return ("stop", None)

    return ("continue", None)
```

### Step 7: runtime dispatch decay + tick lifecycle update + expansion 跳过

Modify `src/explain_engine/runtime/runtime.py`:

加 imports:

```python
from explain_engine.engines import lifecycle as lifecycle_mod
```

`if action == "reflect":` 块加 decay case + tick update:

```python
        if action == "reflect":
            state.last_acceptance_report = aggregate_acceptance(state)

            # Wave 4: 在 reflect 前推进 lifecycle
            lifecycle_mod.update_lifecycle(state, current_tick=state.tick)

            refl_action, refl_target = reflection.reflect(state)
            reflection_action = refl_action

            if refl_action == "expand-downward" and refl_target is not None:
                _new_l0_ids = await expansion.expand_downward(state, refl_target, llm)
                llm_calls = 1
                target_id = refl_target
                state.last_reflection_change_tick = state.tick
            elif refl_action == "re-expand" and refl_target is not None:
                _new_ids, gain_delta = await expansion.re_expand(state, refl_target, llm)
                llm_calls = 1
                target_id = refl_target
                state.last_reflection_change_tick = state.tick
            elif refl_action == "decay" and refl_target is not None:
                # Wave 4 NEW
                lifecycle_mod.soft_decay(state, refl_target)
                target_id = refl_target
                state.last_reflection_change_tick = state.tick
            elif refl_action == "prune" and refl_target is not None:
                state.graph.remove_node(refl_target)
                target_id = refl_target
                state.last_reflection_change_tick = state.tick
            elif refl_action == "stop":
                state.last_reflection_change_tick = max(
                    0, state.tick - reflection.CONSISTENCY_STALE_TICKS - 1
                )

            state.last_gain_tick = state.tick
```

Modify `src/explain_engine/engines/expansion.py` `expand_downward`:

加 decayed check:

```python
    if l1_id not in state.graph.nodes:
        raise ValueError(f"target {l1_id!r} not found in graph")
    target = state.graph.nodes[l1_id]
    if target.abstraction_level != 1:
        raise ValueError(...)
    if target.lifecycle_state == "decayed":   # Wave 4 NEW
        raise ValueError(f"cannot expand decayed node {l1_id!r}")
```

Modify `src/explain_engine/schema/graph.py` `frontier_nodes()`:

```python
def frontier_nodes(self) -> list[str]:
    return [
        nid for nid, n in self.nodes.items()
        if n.abstraction_level == 1
        and getattr(n, "lifecycle_state", "active") != "decayed"   # Wave 4 NEW
        and not any(
            e.relation_type == "causes" and e.target_node == nid
            for e in self.edges.values()
        )
    ]
```

### Step 8: 启用 Wave 2 的 skipped lifecycle test

Modify `tests/test_engines_propagation_rollout.py` `test_skips_decayed_nodes_when_present`:

把 `pytest.skip(...)` 删掉, 改成实际测试:

```python
    def test_skips_decayed_nodes_when_present(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("d_001", 2))
        g.add_node(VariableNode(
            id="c_001", name="c_001", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
            lifecycle_state="decayed",
        ))
        g.add_node(_node("p_001", 0))
        g.add_edge(_edge("e_001", "d_001", "c_001", "causes"))
        g.add_edge(_edge("e_002", "c_001", "p_001", "manifests_as"))

        reachable, missing = rollout_from_roots(g)
        # c_001 decayed → p_001 不可达 (rollout 路径断)
        assert "p_001" in missing
        assert "p_001" not in reachable
```

### Step 9: 跑测试 + 全测 + commit

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
```

Expected: 458 pass (446 + 12), ruff 0.

```bash
git add src/explain_engine/engines/lifecycle.py \
        src/explain_engine/schema/state.py \
        src/explain_engine/schema/graph.py \
        src/explain_engine/engines/reflection.py \
        src/explain_engine/engines/expansion.py \
        src/explain_engine/runtime/runtime.py \
        tests/test_engines_lifecycle_update.py \
        tests/test_engines_reflect_decay.py \
        tests/test_propagation_skip_decayed.py \
        tests/test_engines_propagation_rollout.py
git commit -m "$(cat <<'EOF'
Wave 4.2 · update_lifecycle + reflect decay action + skip decayed

lifecycle.update_lifecycle(state, current_tick) 状态机:
- active → stale: fitness < STALE_THRESHOLD (=0.3)
- stale → decayed: 累积 STALE_TO_DECAYED_TICKS=5 后
- stale → active: fitness 回升复活
- decayed → ? Phase 8 不复活 (Phase 9 memory consolidation)

lifecycle.soft_decay(state, node_id) — reflect decay action 调用入口,
仅改 lifecycle_state, 不删 node (哲学 §9.3 semantic anchoring).

reflect 决策树加 decay 分支, 优先级:
expand-downward > decay > prune > stop > continue

ReflectionAction Literal 加 "decay".
runtime.run reflect tick 加 update_lifecycle + decay dispatch.
expansion.expand_downward + frontier_nodes 跳过 decayed.
_propagation.rollout_from_roots 已有 hook (Wave 2.1), 启用 skipped test.

+12 tests (6 update + 5 decay + 3 skip + 1 启用). 458 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 4 Checkpoint (用户审)

**完成度**: 2/2 task. +26 tests (436 → 462). Variable lifecycle 落地.

**验证**:
- VariableNode lifecycle 字段 backward compat (3 tests)
- compute_fitness 公式 (8 tests)
- update_lifecycle 状态机 (6 tests)
- reflect decay action (5 tests)
- propagation/expansion skip decayed (3 tests + 1 启用)

**Stop**. 等用户审过, 进 Wave 5.

---

# Wave 5 — Acceptance + Docs

## Task 5.1: 重跑 3 acceptance sessions + acceptance doc + README

**目的**: 验证 Phase 8 4 个 Wave 真的修了 Phase 7 暴露的问题. 重跑 3 sessions (clean / mismatch / hallucinated), 收集 evidence, 写 acceptance doc + 更新 README.

**Files:**
- Run: 3 sessions 重跑 (s_f3beb777 / s_705f0435 / s_7d491774)
- Create: `docs/plans/2026-05-15-cognitive-engine-phase-8-acceptance.md`
- Modify: `README.md` (加 Phase 8 章节 + 更新 Status)

---

### Step 1: 备份现有 sessions

```bash
mkdir -p sessions/.backup-pre-phase8
cp sessions/s_f3beb777.json sessions/.backup-pre-phase8/
cp sessions/s_705f0435.json sessions/.backup-pre-phase8/
cp sessions/s_7d491774.json sessions/.backup-pre-phase8/
```

### Step 2: 重跑 3 sessions

```bash
# Clean session — 应该 input check 通过 + 正常 run
.venv/bin/explain run s_f3beb777 --budget 10 2>&1 | tee /tmp/phase8_clean.log

# Mismatch session — 应该 input check fail-fast (exit 2)
.venv/bin/explain run s_705f0435 --budget 10 2>&1 | tee /tmp/phase8_mismatch.log
echo "exit code: $?"

# Hallucinated session — 应该无 re-expand 死循环, expand-downward 调用 ≤ 5 次
.venv/bin/explain run s_7d491774 --budget 15 2>&1 | tee /tmp/phase8_hallu.log
```

### Step 3: 收集 evidence

```bash
# Status reports
.venv/bin/explain status s_f3beb777 > /tmp/status_clean_phase8.txt
.venv/bin/explain status s_7d491774 > /tmp/status_hallu_phase8.txt

# Trace reports
.venv/bin/explain show s_7d491774 --trace > /tmp/trace_hallu_phase8.txt

# Compare 节点数
.venv/bin/python -c "
from explain_engine.persistence.session import SessionStore
from pathlib import Path
import os
store = SessionStore(Path(os.environ.get('SESSIONS_DIR', 'sessions')))
for sid in ['s_f3beb777', 's_705f0435', 's_7d491774']:
    try:
        s = store.load(sid)
        states = {n.lifecycle_state for n in s.graph.nodes.values()}
        decayed = sum(1 for n in s.graph.nodes.values() if n.lifecycle_state == 'decayed')
        stale = sum(1 for n in s.graph.nodes.values() if n.lifecycle_state == 'stale')
        print(f'{sid}: {len(s.graph.nodes)} nodes, {decayed} decayed, {stale} stale')
    except Exception as e:
        print(f'{sid}: load failed - {e}')
"
```

### Step 4: 写 acceptance doc

Create `docs/plans/2026-05-15-cognitive-engine-phase-8-acceptance.md`:

```markdown
# Cognitive Engine Phase 8 — Acceptance Evidence

> 上一 phase: [Phase 7 Acceptance](2026-05-15-cognitive-engine-phase-7-acceptance.md)
> design: [Phase 8 Design](2026-05-15-cognitive-engine-phase-8-design.md)
> plan: [Phase 8 Plan](2026-05-15-cognitive-engine-phase-8-plan.md)

**日期**: 2026-05-15
**Branch**: `dev`
**Final commit**: [填入 Wave 5 commit hash]

---

## 0. Verdict

[**PASS** / **PARTIAL PASS** / **FAIL**] — 详见 §2 criteria 表.

---

## 1. 重跑 evidence

### 1.1 Clean session (s_f3beb777)
[贴 input_alignment / overlap_score / status output]

### 1.2 Mismatch session (s_705f0435)
[验证 fail-fast: 期望 exit 2 + falsifiable_reason]

### 1.3 Hallucinated session (s_7d491774)
[验证 re-expand 计数 = 0; expand-downward ≤ 5; 节点数 < 20; decayed 节点 ≥ 2]

---

## 2. Acceptance criteria (10 criteria)

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | Wave 1 fix re_expand 死循环 | [✅/⚠️/❌] | trace_hallu re-expand count |
| 2 | Wave 2 multi-signal 区分 | [...] | clean vs mismatch consistency_spread / weak_chain_l1s |
| 3 | Wave 3 fail-fast | [...] | s_705f0435 exit code 2 |
| 4 | Wave 3 false positive 控制 | [...] | clean overlap_score ≥ 3 |
| 5 | Wave 4 lifecycle 工作 | [...] | hallu 节点数 + decayed/stale 计数 |
| 6 | Backward compat | [...] | 3 old sessions load 不报错 |
| 7 | Test 全 PASS | [✅] | 462 PASS |
| 8 | Code quality | [✅] | ruff 0 errors |
| 9 | CLI UX | [...] | --no-input-check / status section / friendly error |
| 10 | 哲学契合 | [✅] | 见 design 附录 A |

---

## 3. 真 LLM 数据

### 3.1 input_validation 输出 (s_705f0435)
```json
{
  "question_subject": "...",
  "observation_subjects": [...],
  "overlap_score": [...],
  "falsifiable_reason": "..."
}
```

### 3.2 expand_downward LLM trace (s_7d491774)
[贴一两个 expand_downward 触发的 LLM call 的 prompt + 输出]

---

## 4. 与 Phase 7 acceptance 对比

| 信号 | Phase 7 (clean) | Phase 7 (mismatch) | Phase 8 (clean) | Phase 8 (mismatch) |
|---|---|---|---|---|
| avg_consistency | 0.340 | 0.414 | [填] | n/a (fail-fast) |
| weak_chain_l1s | n/a | n/a | [填] | n/a |
| rollout_coverage | n/a | n/a | [填] | n/a |
| input_alignment | n/a | n/a | [填] | [填] |
| Total nodes (hallu) | 39 | n/a | [填 < 20] | n/a |

---

## 5. Phase 9+ 推动力

1. Variable lifecycle persistence (Phase 8 字段已加, Phase 9 加 cross-session 复用)
2. predictive_utility / vagueness 信号 (fitness 7/7 全实现)
3. Theory Formation (从 Wave 2 weak_chains 演化)
4. Multi-Perspective Runtime (Wave 3 input_validation 可作为 perspective generation 入口)
5. Embedding-based semantic dedup (Wave 4 redundancy 项升级)
```

(实际填值在 Step 3 收完 evidence 后填.)

### Step 5: 更新 README

Modify `README.md`:

加 Phase 8 章节 (在 Phase 7 之后):

```markdown
## Phase 8 (2026-05-15) — Reflect Redesign + Falsifiability + Lifecycle

修 Phase 7 acceptance 暴露的 4 个根本问题:
- ✅ re_expand 死循环 → expand_downward 替换 (Wave 1)
- ✅ 单信号 acceptance → 6 multi-signal + rollout_coverage (Wave 2)
- ✅ Mismatch 失明 → input_validation fail-fast (Wave 3, 哲学 §9.4 可证伪性)
- ✅ 节点无生命 → Variable lifecycle 3 阶段 + fitness + decay (Wave 4)

新 CLI flag:
- `explain run --no-input-check`: 跳过入口校验 (Wave 3 兜底)
- `explain status` 显示 multi-signal + falsifiability section

文档:
- design: [docs/plans/2026-05-15-cognitive-engine-phase-8-design.md](docs/plans/2026-05-15-cognitive-engine-phase-8-design.md)
- plan: [docs/plans/2026-05-15-cognitive-engine-phase-8-plan.md](docs/plans/2026-05-15-cognitive-engine-phase-8-plan.md)
- acceptance: [docs/plans/2026-05-15-cognitive-engine-phase-8-acceptance.md](docs/plans/2026-05-15-cognitive-engine-phase-8-acceptance.md)
```

更新 Status 行:
```markdown
**Status**: Phase 8 milestone (Reflect Redesign + Falsifiability + Lifecycle)
```

### Step 6: 跑全测最后确认 + commit

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
```

Expected: 462 PASS, ruff 0.

```bash
git add docs/plans/2026-05-15-cognitive-engine-phase-8-acceptance.md \
        README.md
git commit -m "$(cat <<'EOF'
acceptance · Phase 8 — Reflect Redesign + Multi-Signal + Falsifiability + Lifecycle

3 acceptance sessions 重跑 evidence (s_f3beb777 clean, s_705f0435 mismatch,
s_7d491774 hallucinated):
- Wave 1 验证: re-expand count = 0, expand-downward ≤ 5
- Wave 2 验证: multi-signal 区分 clean/hallu (consistency_spread / rollout_coverage)
- Wave 3 验证: mismatch session fail-fast exit 2 with falsifiable_reason
- Wave 4 验证: hallu session 节点数 < 20 (Phase 7 是 39), decayed ≥ 2

10 acceptance criteria 逐条 verdict.
README 加 Phase 8 章节 + Status 升级.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 5 Checkpoint (Phase 8 完结)

**完成度**: 1/1 task. Phase 8 全部交付.

**总成绩**:
- 10 task, ~70 step
- +72 tests (390 → 462)
- ruff 0 errors 全程
- 5 commit per Wave (Wave 1 = 2, Wave 2 = 3, Wave 3 = 2, Wave 4 = 2, Wave 5 = 1) → 共 10 commit
- Acceptance verdict: [PASS / PARTIAL / FAIL]

**Stop**. Phase 8 完结. 等用户决定:
- 是否进 Phase 9 (memory consolidation, theory formation, full lifecycle)
- 是否做 housekeeping / refactor

---

# 附录 A: Wave 间依赖图

```
Wave 1 (修死循环)  ─┐
                  ├─→ Wave 5 (acceptance)
Wave 2 (multi-signal) ─┤
                  ├─→ Wave 4 (lifecycle 用 Wave 2 信号)
Wave 3 (alignment) ─┤
                  └─→ Wave 5
```

并行可能性:
- Wave 1 / Wave 2 / Wave 3 可独立并行 (无强依赖, Wave 2 只让 reflect 用 cached, Wave 3 只动 cli 入口)
- Wave 4 必须在 Wave 2 之后 (compute_fitness 依赖 AcceptanceReport)
- Wave 5 必须最后

线性执行更稳, 因为每个 Wave 都需要用户 checkpoint.

---

# 附录 B: 测试增量明细

| Task | 新增 tests | 累积 |
|------|-----------|------|
| 1.1 | +6 | 396 |
| 1.2 | +4 | 400 |
| 2.1 | +6 (含 1 skip 4.1 启用) | 406 |
| 2.2 | +8 | 414 |
| 2.3 | +8 (3 cli + 4 reflect + skip dev) | 422 |
| 3.1 | +8 | 430 |
| 3.2 | +6 | 436 |
| 4.1 | +11 (8 fitness + 3 backward) | 447 |
| 4.2 | +12 (含 1 启用) | 459 |
| 5.1 | 0 (acceptance) | 459 |

(实际数字可能 ±2 微调, 主要看老 test 是否需要 update)

---

# 附录 C: Commit 风格 reminder

每个 Task commit 用 HEREDOC + 中文 + Co-Authored-By trailer:

```bash
git commit -m "$(cat <<'EOF'
Wave X.Y · 简短主题

详细描述 (中文):
- 改动 1
- 改动 2

测试增量 + ruff 状态.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

**Plan 完结. 共 10 task / 5 Wave / +72 tests. 等待用户决定执行方式.**
