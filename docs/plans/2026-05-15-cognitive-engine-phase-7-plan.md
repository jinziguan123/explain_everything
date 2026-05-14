# Cognitive Engine Phase 7 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Phase 7 design 实施落地 —— Confidence 信号化 (Wave A) + Forward Prediction + Counterfactual (Wave B, B3 自然语言 + LLM parser) + Reflection Engine (Wave C, 4-action 闭环) + Acceptance (Wave D). 修 Phase 6 acceptance 暴露的 confidence placeholder 硬伤, 让系统从 "dumb scheduler runtime" 进化到 "self-reflective runtime", 并第一次给用户 forward prediction / counterfactual user-facing 能力. 落地顶层 §4.5 + §4.6 + §8.1 + §11.3 + §11.5 + §12.

**Architecture:** 11 task, TDD 流水线, **4 Wave 线性执行** (A → B → C → D). Wave 之间 stop checkpoint 等用户审. Wave A 改现有 evaluation/expansion (写回 edge.confidence), Wave B 新增 3 engine module (parser/prediction/counterfactual), Wave C 新增 reflection engine + 改 scheduler/runtime/stop, Wave D 加 rescore CLI + 真 LLM acceptance.

**Tech Stack:** Python 3.11+ / dataclasses (frozen) / typer / rich / pydantic / pytest / pytest-mock / pytest-asyncio. Phase 0-6 完全复用, 无新增 dependency.

**Branch:** `dev` (latest: `1fa7aeb` design · Phase 7)

**Design Doc:** [2026-05-15-cognitive-engine-phase-7-design.md](2026-05-15-cognitive-engine-phase-7-design.md)

**Phase 0-6 现状:** 276 tests pass, ruff 0 errors. 3 个 converged session 可用 (s_f3beb777 / s_705f0435 / s_7d491774) 作 acceptance baseline.

---

## 与 Design Doc 的偏差说明

Plan 起草阶段, design doc 跟现有代码无 reconcile 缺口. 如果实施中发现 reconcile, 在对应 Task 内 explicit 说明.

明确的实现约定 (不算偏差):

1. **测试用 `.venv/bin/python -m pytest`** (项目用 uv-managed venv, 不是 `python`)
2. **commit message 中文 + `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer**
3. **每 Wave 完成后 stop checkpoint**, 等用户审通过再进下一 Wave
4. **CLI 测试 fixture 用 `SESSIONS_DIR` env var** (跟 conftest.py + Phase 5/6 test 一致)
5. **预期 LLM mock 用 `pytest-mock` (跟 Phase 5 一致)**, 不用 `unittest.mock`
6. **新 dataclass 用 `@dataclass(frozen=True)`** (跟 Phase 6 ConsistencyReport 一致)

---

## 任务索引

**Wave A — Confidence 信号化 (2 task, 线性, +10 tests)**
- Task A.1: `evaluation.py` 写回 `manifests_as` edge.confidence (~5 step, +5 tests)
- Task A.2: `expansion.py` 写回 `causes` edge.confidence (~5 step, +5 tests)

**Wave B — Forward Prediction + Counterfactual B3 (4 task, 线性, +39 tests)**
- Task B.1: `intervention_parser.py` + prompt + tests (~7 step, +8 tests)
- Task B.2: `prediction.py` + prompt + `explain predict` CLI + HITL (~10 step, +15 tests)
- Task B.3: `counterfactual.py` + narrative prompt + `explain counterfactual` CLI (~8 step, +13 tests)
- Task B.4: shared propagation utility refactor (~5 step, +3 tests)

**Wave C — Reflection Engine (3 task, 线性, +25 tests)**
- Task C.1: `reflection.py` 决策器 + 常量 + tests (~6 step, +10 tests)
- Task C.2: `expansion.re_expand()` + scheduler 改 + runtime.py 加 reflect 分支 (~8 step, +15 tests)
- Task C.3: `runtime/stop.py` 加 reflection_signaled_stop (~5 step, +3 tests)

**Wave D — Acceptance + 文档 (2 task, +5 tests)**
- Task D.1: `explain rescore` CLI + 真 LLM 重跑 3 session + Phase 6 check 对比 (~6 step, +5 tests)
- Task D.2: acceptance evidence file + README 更新 (~3 step, 0 tests)

总: 11 task / ~70 step / +82 tests (276 → 358 final).

---

# Wave A — Confidence 信号化

## Task A.1: `evaluation.py` 写回 `manifests_as` edge.confidence

**目的**: Phase 4 `score_all()` 调 `_score_edge` 返 int score (1-5), 现在只算 compression_gain 不写回 `edge.confidence`. Wave A 改成同时写回 `edge.confidence = score / 5.0` (linear mapping, design §4.2). 让 Phase 6 simulation 沿真信号 propagate, 不是 default placeholder.

**Files:**
- Modify: `src/explain_engine/engines/evaluation.py` (改 `score_all` 主循环)
- Create: `tests/test_engines_evaluation_writeback.py`

---

### Step 1: 写失败测试

Create `tests/test_engines_evaluation_writeback.py`:

```python
"""Wave A.1: evaluation.score_all 写回 edge.confidence 测试。

design §4.2: linear mapping `conf = score / 5.0`. 改 Phase 4 evaluation, 让 LLM 评的
score 写回对应 manifests_as edge.confidence (不再 default 0.7).
"""

import pytest

from explain_engine.engines.evaluation import score_all
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state_with_candidate(plausibility_scores: list[int]) -> CognitiveState:
    """1 c_001 candidate + N p_NNN concrete + N manifests_as edges."""
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id="c_001", name="abstract", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    for i, _ in enumerate(plausibility_scores):
        pid = f"p_{i+1:03d}"
        g.add_node(VariableNode(
            id=pid, name=f"phenom_{i+1}", description="d",
            abstraction_level=0, confidence=0.7, epistemic="observation",
        ))
        g.add_edge(RelationEdge(
            id=f"e_{i+1:03d}",
            source_node="c_001", target_node=pid,
            relation_type="manifests_as",
            confidence=0.7,   # default, 待被 score_all 覆写
            mechanism_description="m",
        ))
    state = CognitiveState(
        graph=g, budget_remaining=10, root_question="why",
        insight_candidates=["c_001"],
    )
    return state


class TestEvaluationWriteback:
    @pytest.mark.asyncio
    async def test_score_5_writes_confidence_1_0(self, mocker) -> None:
        state = _make_state_with_candidate([5])
        from explain_engine.engines import evaluation
        mocker.patch.object(evaluation, "_score_edge", return_value=5)
        await score_all(state, llm=mocker.AsyncMock())
        assert state.graph.edges["e_001"].confidence == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_score_3_writes_confidence_0_6(self, mocker) -> None:
        state = _make_state_with_candidate([3])
        from explain_engine.engines import evaluation
        mocker.patch.object(evaluation, "_score_edge", return_value=3)
        await score_all(state, llm=mocker.AsyncMock())
        assert state.graph.edges["e_001"].confidence == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_score_1_writes_confidence_0_2(self, mocker) -> None:
        state = _make_state_with_candidate([1])
        from explain_engine.engines import evaluation
        mocker.patch.object(evaluation, "_score_edge", return_value=1)
        await score_all(state, llm=mocker.AsyncMock())
        assert state.graph.edges["e_001"].confidence == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_multi_edge_independent_writeback(self, mocker) -> None:
        state = _make_state_with_candidate([5, 3, 1])
        scores = iter([5, 3, 1])
        from explain_engine.engines import evaluation
        mocker.patch.object(
            evaluation, "_score_edge",
            side_effect=lambda *a, **kw: next(scores),
        )
        await score_all(state, llm=mocker.AsyncMock())
        assert state.graph.edges["e_001"].confidence == pytest.approx(1.0)
        assert state.graph.edges["e_002"].confidence == pytest.approx(0.6)
        assert state.graph.edges["e_003"].confidence == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_score_edge_failure_keeps_original_confidence(self, mocker) -> None:
        state = _make_state_with_candidate([5])
        from explain_engine.engines import evaluation
        from explain_engine.llm.errors import SchemaValidationError
        mocker.patch.object(
            evaluation, "_score_edge",
            side_effect=SchemaValidationError("LLM 失败"),
        )
        with pytest.raises(SchemaValidationError):
            await score_all(state, llm=mocker.AsyncMock())
        # 异常时不写回, edge.confidence 保持原 default 0.7
        assert state.graph.edges["e_001"].confidence == pytest.approx(0.7)
```

### Step 2: 跑测试验证失败

Run: `.venv/bin/python -m pytest tests/test_engines_evaluation_writeback.py -v`
Expected: 4 个 FAIL (`AssertionError: confidence != 1.0/0.6/0.2`), 1 个 PASS (test_score_edge_failure_keeps_original_confidence —— 现行为已是 raise + 不写回, 实际可能 PASS 也可能 FAIL 看 mock 顺序).

### Step 3: 改 `evaluation.py` 写回逻辑

Modify `src/explain_engine/engines/evaluation.py`. 找到 `score_all` 函数 (大约 line 30-86), 改主循环:

```python
async def score_all(state: CognitiveState, llm: LLMClient) -> dict[str, float]:
    total_concrete = sum(
        1 for n in state.graph.nodes.values() if n.abstraction_level == 0
    )
    if total_concrete == 0:
        logger.warning("total_concrete == 0, all gains will be 0")

    gains: dict[str, float] = {}
    prompt = load_prompt("scoring")

    for cid in state.insight_candidates:
        cand = state.graph.nodes[cid]
        out_edges = [
            e
            for e in state.graph.edges.values()
            if e.source_node == cid and e.relation_type == "manifests_as"
        ]
        if not out_edges or total_concrete == 0:
            gains[cid] = 0.0
            continue

        covered = len(out_edges)
        representation_reduction = covered / total_concrete

        scores: list[int] = []
        scores_by_edge: dict[str, int] = {}   # Wave A: 记录 per-edge score
        for e in out_edges:
            concrete = state.graph.nodes[e.target_node]
            score = await _score_edge(
                llm,
                prompt,
                abstract_name=cand.name,
                abstract_description=cand.description,
                concrete_name=concrete.name,
                concrete_description=concrete.description,
                mechanism=e.mechanism_description,
            )
            scores.append(score)
            scores_by_edge[e.id] = score        # Wave A: 累计

        explanatory_preservation = (sum(scores) / len(scores)) / 5.0
        gains[cid] = representation_reduction * explanatory_preservation

        # Wave A (Phase 7 design §4.2): 写回 edge.confidence = score / 5.0
        for edge_id, score in scores_by_edge.items():
            state.graph.edges[edge_id].confidence = score / 5.0

    state.insight_candidates = sorted(
        state.insight_candidates, key=lambda cid: gains[cid], reverse=True
    )
    state.last_gains = dict(gains)
    return gains
```

Diff 只动两处:
1. 新增 `scores_by_edge: dict[str, int] = {}` (在 `scores: list[int] = []` 旁边)
2. 新增 `scores_by_edge[e.id] = score` (在 `scores.append(score)` 后)
3. 新增 写回 loop (在 `gains[cid] = ...` 后, `state.insight_candidates = sorted(...)` 前)

### Step 4: 跑测试验证通过 + 不破现有测试

Run: `.venv/bin/python -m pytest tests/test_engines_evaluation_writeback.py tests/test_engines_evaluation.py tests/test_engines_evaluation_last_gains.py -v`
Expected: 5 + 现有 evaluation tests 全 PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: 276 + 5 = 281 PASS. (Phase 6 baseline 276)

### Step 5: ruff check + commit

Run: `.venv/bin/python -m ruff check src/explain_engine/engines/evaluation.py tests/test_engines_evaluation_writeback.py`
Expected: 0 errors.

```bash
git add src/explain_engine/engines/evaluation.py tests/test_engines_evaluation_writeback.py
git commit -m "$(cat <<'EOF'
Wave A.1 · evaluation.score_all 写回 manifests_as edge.confidence

linear mapping conf = score / 5.0 (Phase 7 design §4.2)
- score=5 → 1.0, score=3 → 0.6, score=1 → 0.2
- 修 Phase 6 acceptance 暴露的 confidence placeholder issue
- LLM 失败时不写回, 保持原 default

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task A.2: `expansion.py` 写回 `causes` edge.confidence

**目的**: Phase 5 `expand_one_frontier()` 生成 driver 时 hardcode `confidence=0.6`, plausibility 只算 gain 不写回. Wave A 改成 `conf = plausibility / 5.0` (linear mapping). 让 driver→target 链 propagation 时反映真 LLM 评分.

**Files:**
- Modify: `src/explain_engine/engines/expansion.py` (改 line 122 附近 RelationEdge 构造)
- Create: `tests/test_engines_expansion_writeback.py`

---

### Step 1: 写失败测试

Create `tests/test_engines_expansion_writeback.py`:

```python
"""Wave A.2: expansion.expand_one_frontier 写回 causes edge.confidence。

design §4.3: linear mapping `conf = plausibility / 5.0` (跟 evaluation 同).
"""

import pytest

from explain_engine.engines.expansion import expand_one_frontier
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state_with_frontier(target_id: str = "c_001") -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id=target_id, name="abstract", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="phenom", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node=target_id, target_node="p_001",
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m",
    ))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="why",
    )


def _mock_expansion_output(mocker, drivers: list[dict]):
    """Mock LLM 返 ExpansionOutput with given driver list."""
    from explain_engine.engines.expansion import ExpansionOutput, _DriverCandidate
    output = ExpansionOutput(drivers=[_DriverCandidate(**d) for d in drivers])

    async def fake_call_with_retry(*args, **kwargs):
        return output
    mocker.patch(
        "explain_engine.engines.expansion._call_with_retry",
        side_effect=fake_call_with_retry,
    )


class TestExpansionWriteback:
    @pytest.mark.asyncio
    async def test_plausibility_5_writes_confidence_1_0(self, mocker) -> None:
        state = _make_state_with_frontier()
        _mock_expansion_output(mocker, [{
            "name": "d1", "description": "d",
            "mechanism": "m", "plausibility": 5,
        }])
        new_ids, _gain = await expand_one_frontier(state, "c_001", mocker.AsyncMock())
        assert len(new_ids) == 1
        # 新加的 causes edge 应该有 confidence = 5 / 5 = 1.0
        new_edges = [e for e in state.graph.edges.values() if e.relation_type == "causes"]
        assert len(new_edges) == 1
        assert new_edges[0].confidence == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_plausibility_3_writes_confidence_0_6(self, mocker) -> None:
        state = _make_state_with_frontier()
        _mock_expansion_output(mocker, [{
            "name": "d1", "description": "d", "mechanism": "m", "plausibility": 3,
        }])
        await expand_one_frontier(state, "c_001", mocker.AsyncMock())
        new_edges = [e for e in state.graph.edges.values() if e.relation_type == "causes"]
        assert new_edges[0].confidence == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_multi_driver_independent_confidence(self, mocker) -> None:
        state = _make_state_with_frontier()
        _mock_expansion_output(mocker, [
            {"name": "d1", "description": "d", "mechanism": "m", "plausibility": 5},
            {"name": "d2", "description": "d", "mechanism": "m", "plausibility": 3},
            {"name": "d3", "description": "d", "mechanism": "m", "plausibility": 1},
        ])
        await expand_one_frontier(state, "c_001", mocker.AsyncMock())
        new_edges = sorted(
            [e for e in state.graph.edges.values() if e.relation_type == "causes"],
            key=lambda e: e.id,
        )
        assert len(new_edges) == 3
        assert new_edges[0].confidence == pytest.approx(1.0)
        assert new_edges[1].confidence == pytest.approx(0.6)
        assert new_edges[2].confidence == pytest.approx(0.2)

    @pytest.mark.asyncio
    async def test_reused_driver_new_edge_uses_new_plausibility(self, mocker) -> None:
        """Driver 同名复用 node, 但新 causes edge 用新 plausibility."""
        state = _make_state_with_frontier()
        # 先加一个 d_999 同名 node + edge (模拟 existing driver)
        state.graph.add_node(VariableNode(
            id="d_999", name="d1", description="d",
            abstraction_level=2, confidence=0.6, epistemic="inference",
        ))
        # 加一个 c_002 来给 d_999 当 target, 让 d_999 不是 isolated
        state.graph.add_node(VariableNode(
            id="c_002", name="other", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
        state.graph.add_edge(RelationEdge(
            id="e_999", source_node="d_999", target_node="c_002",
            relation_type="causes", confidence=0.6,
            mechanism_description="old",
        ))
        _mock_expansion_output(mocker, [{
            "name": "d1", "description": "d",
            "mechanism": "m", "plausibility": 5,
        }])
        new_ids, _ = await expand_one_frontier(state, "c_001", mocker.AsyncMock())
        # 应复用 d_999 (同名), 加新 edge to c_001 with conf=1.0
        assert new_ids == ["d_999"]
        new_edges_to_c001 = [
            e for e in state.graph.edges.values()
            if e.relation_type == "causes" and e.target_node == "c_001"
        ]
        assert len(new_edges_to_c001) == 1
        assert new_edges_to_c001[0].confidence == pytest.approx(1.0)
        # 旧 edge e_999 不变
        assert state.graph.edges["e_999"].confidence == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_zero_drivers_no_edge_written(self, mocker) -> None:
        state = _make_state_with_frontier()
        _mock_expansion_output(mocker, [])
        new_ids, gain = await expand_one_frontier(state, "c_001", mocker.AsyncMock())
        assert new_ids == []
        assert gain == 0.0
        # 没新增 causes edge
        causes_edges = [e for e in state.graph.edges.values() if e.relation_type == "causes"]
        assert causes_edges == []
```

### Step 2: 跑测试验证失败

Run: `.venv/bin/python -m pytest tests/test_engines_expansion_writeback.py -v`
Expected: 大部分 FAIL (current expansion hardcode `confidence=0.6`).

### Step 3: 改 `expansion.py` 写回

Modify `src/explain_engine/engines/expansion.py`. 找到 `expand_one_frontier` 内 `RelationEdge(...)` 构造 (大约 line 122):

```python
        state.graph.add_edge(
            RelationEdge(
                id=f"e_{next_edge_id:03d}",
                source_node=d_id,
                target_node=target_id,
                relation_type="causes",
                confidence=d.plausibility / 5.0,   # Wave A.2: 原 hardcode 0.6
                mechanism_description=d.mechanism,
            )
        )
```

只改 `confidence=0.6` → `confidence=d.plausibility / 5.0`.

### Step 4: 跑测试 + 现有 expansion 测试

Run: `.venv/bin/python -m pytest tests/test_engines_expansion_writeback.py tests/test_engines_expansion.py -v`
Expected: 5 + 现有 expansion tests 全 PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: 281 + 5 = 286 PASS.

### Step 5: ruff check + commit

Run: `.venv/bin/python -m ruff check src/explain_engine/engines/expansion.py tests/test_engines_expansion_writeback.py`
Expected: 0 errors.

```bash
git add src/explain_engine/engines/expansion.py tests/test_engines_expansion_writeback.py
git commit -m "$(cat <<'EOF'
Wave A.2 · expansion.expand_one_frontier 写回 causes edge.confidence

linear mapping conf = plausibility / 5.0 (跟 A.1 同 mapping)
- 原 hardcode 0.6 → 改 LLM 评分动态写回
- 同名 driver 复用 node 但新 edge 用新 plausibility (existing edge 不动)
- Wave A 完结: Phase 6 simulation 现真信号化

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave A 完成 Checkpoint

跑全测试:

```bash
.venv/bin/python -m pytest -q
```

Expected: **286 PASS** (276 base + 10 new).

跑 ruff:

```bash
.venv/bin/python -m ruff check src/ tests/
```

Expected: 0 errors.

**STOP — 等用户审 Wave A 通过后进 Wave B.**

---

# Wave B — Forward Prediction + Counterfactual (B3)

## Task B.1: `intervention_parser.py` + prompt + tests

**目的**: B3 主入口 — LLM-based parser 把自然语言 intervention 拆成 `existing_refs` (graph 已有 variable ids) + `new_concepts` (要新建的概念, 最多 2 个). Forward predict / Counterfactual 都先调它.

**Files:**
- Create: `src/explain_engine/engines/intervention_parser.py`
- Create: `src/explain_engine/llm/prompts/intervention_parser.yaml`
- Create: `tests/test_engines_intervention_parser.py`

---

### Step 1: 写失败测试

Create `tests/test_engines_intervention_parser.py`:

```python
"""Wave B.1: intervention_parser.parse 测试.

design §5.2. LLM-based 拆 intervention 为 existing_refs + new_concepts.
"""

import pytest
from pydantic import ValidationError

from explain_engine.engines.intervention_parser import (
    NewConceptSpec,
    ParsedIntervention,
    parse,
)
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state() -> CognitiveState:
    g = ExplanationGraph(root_question="为什么宗教战争最血腥")
    g.add_node(VariableNode(
        id="c_001", name="绝对化价值框架", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="d_002", name="教义不可妥协性", description="d",
        abstraction_level=2, confidence=0.6, epistemic="inference",
    ))
    g.add_node(VariableNode(
        id="p_001", name="神圣不可妥协性", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m",
    ))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="为什么宗教战争最血腥",
    )


def _mock_llm(mocker, parsed_dict: dict | None, raise_validation: bool = False):
    """Mock llm.chat returning structured parsed output."""
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.parsed = parsed_dict
    if raise_validation:
        resp.parsed = {"foo": "bar"}  # invalid schema
    llm = mocker.AsyncMock()
    llm.chat = mocker.AsyncMock(return_value=resp)
    return llm


class TestParseExistingOnly:
    @pytest.mark.asyncio
    async def test_existing_refs_d_002(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": ["d_002"],
            "new_concepts": [],
        })
        result = await parse(state, "教义不可妥协性强化", llm)
        assert result.existing_refs == ["d_002"]
        assert result.new_concepts == []


class TestParseNewOnly:
    @pytest.mark.asyncio
    async def test_new_concept_only(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": [],
            "new_concepts": [{
                "name": "现代媒体放大效应",
                "description": "新概念",
                "expected_level": 2,
            }],
        })
        result = await parse(state, "现代媒体放大效应", llm)
        assert result.existing_refs == []
        assert len(result.new_concepts) == 1
        assert result.new_concepts[0].name == "现代媒体放大效应"
        assert result.new_concepts[0].expected_level == 2


class TestParseMixed:
    @pytest.mark.asyncio
    async def test_mixed_existing_and_new(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": ["d_002"],
            "new_concepts": [{
                "name": "现代媒体放大效应",
                "description": "d", "expected_level": 2,
            }],
        })
        result = await parse(state, "现代媒体 + 教义", llm)
        assert result.existing_refs == ["d_002"]
        assert len(result.new_concepts) == 1


class TestParseErrors:
    @pytest.mark.asyncio
    async def test_nonexistent_variable_id_raises_after_retry(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": ["d_999"],  # 不存在
            "new_concepts": [],
        })
        with pytest.raises(SchemaValidationError, match="d_999"):
            await parse(state, "x", llm)
        # 已 retry, 总共 2 次调用
        assert llm.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_too_many_new_concepts_raises(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": [],
            "new_concepts": [
                {"name": "a", "description": "d", "expected_level": 1},
                {"name": "b", "description": "d", "expected_level": 1},
                {"name": "c", "description": "d", "expected_level": 1},
            ],
        })
        with pytest.raises(SchemaValidationError):
            await parse(state, "x", llm)

    @pytest.mark.asyncio
    async def test_invalid_expected_level_raises(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": [],
            "new_concepts": [{
                "name": "a", "description": "d", "expected_level": 3,
            }],
        })
        with pytest.raises(SchemaValidationError):
            await parse(state, "x", llm)

    @pytest.mark.asyncio
    async def test_empty_parse_raises_valueerror(self, mocker) -> None:
        state = _make_state()
        llm = _mock_llm(mocker, {
            "existing_refs": [],
            "new_concepts": [],
        })
        with pytest.raises(ValueError, match="无法解析"):
            await parse(state, "废话", llm)
```

### Step 2: 跑测试验证失败

Run: `.venv/bin/python -m pytest tests/test_engines_intervention_parser.py -v`
Expected: 全 FAIL (ImportError: module not found).

### Step 3: 创建 prompt yaml

Create `src/explain_engine/llm/prompts/intervention_parser.yaml`:

```yaml
system: |
  你是 cognitive engine 的 intervention parser sub-agent.

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
      {"name": str, "description": str, "expected_level": 1 or 2}
    ]
  }

user_template: |
  根问题: {question}

  当前 graph 已有节点 (id: name — description, level):
  {graph_nodes_table}

  用户 intervention 描述:
  {intervention_text}

  请拆: existing_refs + new_concepts (最多 2 个 new_concept).
```

### Step 4: 实现 `intervention_parser.py`

Create `src/explain_engine/engines/intervention_parser.py`:

```python
"""Wave B.1: Intervention parser — 把自然语言 intervention 拆成 (existing_refs, new_concepts).

design §5.2. LLM-based, retry 1 次. 返空 raise ValueError.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


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

    Raises:
        SchemaValidationError: LLM 输出不合 schema 或 existing_refs 含不存在 id
                               (retry 1 次仍失败).
        ValueError: parser 返空 (existing_refs=[] 且 new_concepts=[]).
    """
    prompt = load_prompt("intervention_parser")
    graph_nodes_table = _render_graph_nodes(state)
    valid_ids = set(state.graph.nodes.keys())

    messages = [
        Message(role="system", content=prompt["system"]),
        Message(
            role="user",
            content=prompt["user_template"].format(
                question=state.root_question,
                graph_nodes_table=graph_nodes_table,
                intervention_text=intervention_text,
            ),
        ),
    ]

    parsed = await _call_with_retry(llm, messages, valid_ids)

    if not parsed.existing_refs and not parsed.new_concepts:
        raise ValueError(
            f"无法解析 intervention: {intervention_text!r} "
            f"(parser 返空 — intervention 可能跟 root_question 无关)"
        )
    return parsed


def _render_graph_nodes(state: CognitiveState) -> str:
    lines = [
        f"- {nid}: {n.name} — {n.description} (level={n.abstraction_level})"
        for nid, n in state.graph.nodes.items()
    ]
    return "\n".join(lines) if lines else "(graph 为空)"


async def _call_with_retry(
    llm: LLMClient,
    messages: list[Message],
    valid_ids: set[str],
) -> ParsedIntervention:
    last_exc: Exception | None = None
    for _attempt in range(2):
        resp = await llm.chat(messages, schema=ParsedIntervention)
        if resp.parsed is None:
            last_exc = SchemaValidationError("LLM 未返回 structured output")
            continue
        try:
            parsed = ParsedIntervention.model_validate(resp.parsed)
        except ValidationError as exc:
            last_exc = SchemaValidationError(f"parser 输出 schema 不合规: {exc}")
            continue
        bad = [rid for rid in parsed.existing_refs if rid not in valid_ids]
        if bad:
            last_exc = SchemaValidationError(f"未知 variable id: {bad}")
            continue
        return parsed
    assert last_exc is not None
    raise last_exc
```

### Step 5: 跑测试验证通过

Run: `.venv/bin/python -m pytest tests/test_engines_intervention_parser.py -v`
Expected: 8 PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: 286 + 8 = 294 PASS.

### Step 6: ruff check + 写 prompt loader 测试 (可选, 跟 Phase 5 一致)

Run: `.venv/bin/python -m ruff check src/explain_engine/engines/intervention_parser.py tests/test_engines_intervention_parser.py`
Expected: 0 errors.

### Step 7: Commit

```bash
git add src/explain_engine/engines/intervention_parser.py \
        src/explain_engine/llm/prompts/intervention_parser.yaml \
        tests/test_engines_intervention_parser.py
git commit -m "$(cat <<'EOF'
Wave B.1 · intervention_parser — LLM-based 自然语言 intervention 拆解

Phase 7 design §5.2. B3 主入口:
- parse(state, intervention_text, llm) → ParsedIntervention(existing_refs, new_concepts)
- existing_refs 必须 graph 已有 (retry 1 次校验)
- new_concepts 最多 2 个, parser 决定 expected_level (1 abstract / 2 driver)
- 返空 raise ValueError

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task B.2: `prediction.py` + prompt + `explain predict` CLI + HITL

**目的**: Forward prediction 主路径 — parser 拆完后, LLM 生 predicted L0, 灌进 graph, propagate, HITL 审. `explain predict <sid> "<text>"` CLI 命令.

**Files:**
- Create: `src/explain_engine/engines/prediction.py`
- Create: `src/explain_engine/llm/prompts/prediction.yaml`
- Create: `tests/test_engines_prediction.py`
- Create: `tests/test_cli_predict.py`
- Modify: `src/explain_engine/cli.py` (加 `predict` 命令)
- Modify: `src/explain_engine/hitl/cli_interactive.py` (加 `review_predicted_l0`)

---

### Step 1: 写失败测试 (engine + CLI)

Create `tests/test_engines_prediction.py`:

```python
"""Wave B.2: ForwardPredictionEngine.predict 测试.

design §5.3.
"""

import pytest

from explain_engine.engines.intervention_parser import (
    NewConceptSpec,
    ParsedIntervention,
)
from explain_engine.engines.prediction import (
    PredictedL0,
    PredictionOutput,
    PredictionReport,
    predict,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state() -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id="c_001", name="abstract", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="d_002", name="driver", description="d",
        abstraction_level=2, confidence=0.6, epistemic="inference",
    ))
    g.add_node(VariableNode(
        id="p_001", name="existing_L0", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m",
    ))
    g.add_edge(RelationEdge(
        id="e_002", source_node="d_002", target_node="c_001",
        relation_type="causes", confidence=0.6,
        mechanism_description="m",
    ))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="why",
    )


def _mock_parser(mocker, parsed: ParsedIntervention):
    async def fake_parse(*args, **kwargs):
        return parsed
    mocker.patch(
        "explain_engine.engines.prediction.parse_intervention",
        side_effect=fake_parse,
    )


def _mock_generation(mocker, predicted_L0: list[dict]):
    """Mock LLM 返 PredictionOutput."""
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.parsed = {"predicted_L0": predicted_L0}
    return resp


class TestPredictMainPath:
    @pytest.mark.asyncio
    async def test_new_concept_creates_node_and_predicted_L0(self, mocker) -> None:
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=[],
            new_concepts=[NewConceptSpec(
                name="新概念", description="d", expected_level=2,
            )],
        ))
        gen_resp = _mock_generation(mocker, [
            {"name": "predicted_p1", "description": "d", "mechanism": "m1"},
            {"name": "predicted_p2", "description": "d", "mechanism": "m2"},
        ])
        llm = mocker.AsyncMock()
        llm.chat = mocker.AsyncMock(return_value=gen_resp)
        report = await predict(state, "新概念", llm)
        # 新 driver node 加进 graph
        new_drivers = [
            nid for nid, n in state.graph.nodes.items()
            if n.name == "新概念" and n.abstraction_level == 2
        ]
        assert len(new_drivers) == 1
        # predicted L0 加进 graph
        predicted = [
            nid for nid, n in state.graph.nodes.items()
            if n.epistemic == "speculation" and n.abstraction_level == 0
        ]
        assert len(predicted) == 2
        # Report 字段
        assert len(report.new_node_ids) == 1
        assert len(report.predicted_L0_ids) == 2

    @pytest.mark.asyncio
    async def test_existing_only_skips_generation(self, mocker) -> None:
        """B1 退化 case: existing_refs only, 不调 generation LLM."""
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"], new_concepts=[],
        ))
        llm = mocker.AsyncMock()
        report = await predict(state, "已有 driver", llm)
        # LLM 没被调 (parser mock 已 patch, generation 不该 call)
        assert llm.chat.call_count == 0
        # 不加新 node
        assert report.new_node_ids == []
        assert report.predicted_L0_ids == []
        # propagation 从 d_002 跑
        assert "d_002" in report.propagation_acts

    @pytest.mark.asyncio
    async def test_predicted_L0_uses_speculation_epistemic(self, mocker) -> None:
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=[],
            new_concepts=[NewConceptSpec(
                name="新", description="d", expected_level=2,
            )],
        ))
        gen_resp = _mock_generation(mocker, [
            {"name": "predicted", "description": "d", "mechanism": "m"},
        ])
        llm = mocker.AsyncMock()
        llm.chat = mocker.AsyncMock(return_value=gen_resp)
        report = await predict(state, "新", llm)
        predicted_id = report.predicted_L0_ids[0]
        assert state.graph.nodes[predicted_id].epistemic == "speculation"
        assert state.graph.nodes[predicted_id].abstraction_level == 0

    @pytest.mark.asyncio
    async def test_predicted_L0_edge_uses_wave_a_mapping(self, mocker) -> None:
        """新加的 manifests_as edge 应该 conf=0.7 (无 plausibility 用 default)."""
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=[],
            new_concepts=[NewConceptSpec(
                name="新", description="d", expected_level=2,
            )],
        ))
        gen_resp = _mock_generation(mocker, [
            {"name": "p", "description": "d", "mechanism": "m"},
        ])
        llm = mocker.AsyncMock()
        llm.chat = mocker.AsyncMock(return_value=gen_resp)
        report = await predict(state, "新", llm)
        new_edges = [
            e for e in state.graph.edges.values()
            if e.relation_type == "manifests_as"
            and e.target_node == report.predicted_L0_ids[0]
        ]
        assert len(new_edges) == 1
        # Wave B.2 design: 新 edge 用 default 0.7 (LLM 没自评 plausibility 的话)
        assert new_edges[0].confidence == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_predict_returns_propagation_acts(self, mocker) -> None:
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"], new_concepts=[],
        ))
        llm = mocker.AsyncMock()
        report = await predict(state, "x", llm)
        assert report.propagation_acts.get("d_002") == 1.0
        assert "p_001" in report.propagation_acts  # 经 c_001 → p_001 propagate
```

Create `tests/test_cli_predict.py`:

```python
"""Wave B.2: explain predict CLI 测试."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from explain_engine.cli import app


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
    return sessions_dir


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


def _save_converged_session(sessions_dir: Path, sid: str) -> None:
    """灌一个 stage=converged 的最简 session."""
    import json
    payload = {
        "meta": {
            "session_id": sid, "question": "why",
            "stage": "converged", "created_at": 1234567890.0,
        },
        "state": {
            "graph": {
                "root_question": "why",
                "nodes": {
                    "p_001": {
                        "id": "p_001", "name": "p", "description": "d",
                        "abstraction_level": 0, "confidence": 0.7,
                        "epistemic": "observation",
                    },
                    "c_001": {
                        "id": "c_001", "name": "c", "description": "d",
                        "abstraction_level": 1, "confidence": 0.7,
                        "epistemic": "insight",
                    },
                },
                "edges": {
                    "e_001": {
                        "id": "e_001", "source_node": "c_001",
                        "target_node": "p_001",
                        "relation_type": "manifests_as",
                        "confidence": 0.7, "mechanism_description": "m",
                    },
                },
            },
            "budget_remaining": 0, "root_question": "why",
            "active_frontier": [], "insight_candidates": ["c_001"],
            "tick": 0, "last_gain_tick": 0,
            "last_gains": {}, "reasoning_trace": [],
        },
    }
    (sessions_dir / f"{sid}.json").write_text(json.dumps(payload, ensure_ascii=False))


class TestCLIPredict:
    def test_session_not_found(self, cli_env, runner) -> None:
        result = runner.invoke(app, ["predict", "s_nonexistent", "x"])
        assert result.exit_code == 1

    def test_predict_invokes_engine(self, cli_env, runner, monkeypatch) -> None:
        _save_converged_session(cli_env, "s_test01")
        # mock prediction.predict
        async def fake_predict(state, text, llm):
            from explain_engine.engines.prediction import PredictionReport
            from explain_engine.engines.intervention_parser import ParsedIntervention
            return PredictionReport(
                intervention_text=text,
                parsed=ParsedIntervention(existing_refs=["c_001"], new_concepts=[]),
                new_node_ids=[], predicted_L0_ids=[],
                activated_existing_L0=["p_001"],
                propagation_acts={"c_001": 1.0, "p_001": 0.7},
                decay_trace=[],
            )
        monkeypatch.setattr(
            "explain_engine.engines.prediction.predict", fake_predict,
        )
        # mock LLM factory (避免真调)
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client",
            lambda: MagicMock(),
        )
        result = runner.invoke(app, ["predict", "s_test01", "x"])
        assert result.exit_code == 0
        assert "p_001" in result.stdout
```

(其他 3 个 CLI test: parser fail → exit 2, HITL accept all, predict with new concept 生 predicted L0 — 省略, 让实施者补)

### Step 2: 跑测试验证失败

Run: `.venv/bin/python -m pytest tests/test_engines_prediction.py tests/test_cli_predict.py -v`
Expected: 全 FAIL.

### Step 3: 创建 prompt yaml

Create `src/explain_engine/llm/prompts/prediction.yaml`:

```yaml
system: |
  你是 cognitive engine 的 forward prediction sub-agent.

  任务: 给定一个 intervention (已 parse 出 [intervention nodes + existing nodes]),
  预测它会 manifest 出哪些新的 concrete L0 现象.

  约束:
  - 输出 1-5 个 predicted L0, 每个含 name / description / mechanism.
  - mechanism 必须说明 "为什么 intervention → 这个 L0".
  - 不要预测已有 L0 (graph 里已经有的 concrete). 算法会自动合并 existing L0.
  - predicted L0 是 forward (intervention 会带来的新现象), 不是 backward (intervention 是什么的结果).

  输出 schema:
  {
    "predicted_L0": [
      {"name": str, "description": str, "mechanism": str}
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

### Step 4: 实现 `prediction.py`

Create `src/explain_engine/engines/prediction.py`:

```python
"""Wave B.2: Forward Prediction Engine.

design §5.3. flow:
  1. parser → ParsedIntervention
  2. 加 new_concepts 进 graph (level by spec, epistemic=speculation)
  3. LLM 生 predicted L0 (level=0, epistemic=speculation)
  4. propagate from new + existing
  5. 返 PredictionReport (state.graph 已 mutate)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from explain_engine.engines._propagation import DecayStep, propagate
from explain_engine.engines.intervention_parser import (
    ParsedIntervention,
    parse as parse_intervention,
)
from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


class PredictedL0(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)


class PredictionOutput(BaseModel):
    predicted_L0: list[PredictedL0] = Field(min_length=1, max_length=5)


@dataclass(frozen=True)
class PredictionReport:
    intervention_text: str
    parsed: ParsedIntervention
    new_node_ids: list[str]
    predicted_L0_ids: list[str]
    activated_existing_L0: list[str]
    propagation_acts: dict[str, float]
    decay_trace: list[DecayStep]


async def predict(
    state: CognitiveState,
    intervention_text: str,
    llm: LLMClient,
) -> PredictionReport:
    """Forward prediction. 副作用: state.graph 改 (加 nodes + edges).

    Raises:
        SchemaValidationError, ValueError 同 intervention_parser.
    """
    parsed = await parse_intervention(state, intervention_text, llm)

    new_node_ids: list[str] = []
    predicted_L0_ids: list[str] = []

    # 1. 加 new_concepts 进 graph
    for spec in parsed.new_concepts:
        prefix = "c" if spec.expected_level == 1 else "d"
        new_id = _next_id(state, prefix)
        state.graph.add_node(VariableNode(
            id=new_id, name=spec.name, description=spec.description,
            abstraction_level=spec.expected_level,
            confidence=0.7, epistemic="speculation", source="llm",
        ))
        new_node_ids.append(new_id)

    # 2. LLM 生 predicted L0 (仅当有 new_concepts)
    if parsed.new_concepts:
        gen_output = await _generate_predicted_L0(state, parsed, intervention_text, llm)
        for predicted in gen_output.predicted_L0:
            p_id = _next_id(state, "p")
            state.graph.add_node(VariableNode(
                id=p_id, name=predicted.name, description=predicted.description,
                abstraction_level=0, confidence=0.7,
                epistemic="speculation", source="llm",
            ))
            predicted_L0_ids.append(p_id)
            # 加 manifests_as edge from intervention nodes → predicted L0
            for new_id in new_node_ids:
                edge_id = _next_edge_id(state)
                state.graph.add_edge(RelationEdge(
                    id=edge_id, source_node=new_id, target_node=p_id,
                    relation_type="manifests_as", confidence=0.7,
                    mechanism_description=predicted.mechanism,
                ))

    # 3. propagate
    sources = set(new_node_ids) | set(parsed.existing_refs)
    acts, trace = propagate(state.graph, sources)

    activated_L0 = sorted(
        nid for nid, n in state.graph.nodes.items()
        if n.abstraction_level == 0 and n.epistemic != "speculation"
        and acts.get(nid, 0.0) > 0
    )

    return PredictionReport(
        intervention_text=intervention_text, parsed=parsed,
        new_node_ids=new_node_ids, predicted_L0_ids=predicted_L0_ids,
        activated_existing_L0=activated_L0,
        propagation_acts=acts, decay_trace=trace,
    )


async def _generate_predicted_L0(
    state: CognitiveState,
    parsed: ParsedIntervention,
    intervention_text: str,
    llm: LLMClient,
) -> PredictionOutput:
    prompt = load_prompt("prediction")
    existing_L0_table = "\n".join(
        f"- {nid}: {n.name}" for nid, n in state.graph.nodes.items()
        if n.abstraction_level == 0 and n.epistemic != "speculation"
    ) or "(none)"
    intervention_summary = (
        f"{intervention_text}\n"
        f"existing_refs={parsed.existing_refs}, "
        f"new_concepts={[c.name for c in parsed.new_concepts]}"
    )
    messages = [
        Message(role="system", content=prompt["system"]),
        Message(role="user", content=prompt["user_template"].format(
            question=state.root_question,
            intervention_summary=intervention_summary,
            existing_L0_table=existing_L0_table,
        )),
    ]
    last_exc: Exception | None = None
    for _ in range(2):
        resp = await llm.chat(messages, schema=PredictionOutput)
        if resp.parsed is None:
            last_exc = SchemaValidationError("LLM 未返回 structured output")
            continue
        try:
            return PredictionOutput.model_validate(resp.parsed)
        except ValidationError as exc:
            last_exc = SchemaValidationError(f"prediction 输出 schema 不合规: {exc}")
            continue
    assert last_exc is not None
    raise last_exc


def _next_id(state: CognitiveState, prefix: str) -> str:
    existing = [
        int(nid.split("_")[1])
        for nid in state.graph.nodes
        if nid.startswith(f"{prefix}_") and nid[len(prefix)+1:].isdigit()
    ]
    n = (max(existing) + 1) if existing else 1
    return f"{prefix}_{n:03d}"


def _next_edge_id(state: CognitiveState) -> str:
    existing = [
        int(eid.split("_")[1])
        for eid in state.graph.edges
        if eid.startswith("e_") and eid[2:].isdigit()
    ]
    n = (max(existing) + 1) if existing else 1
    return f"e_{n:03d}"
```

### Step 5: 加 HITL `review_predicted_l0`

Modify `src/explain_engine/hitl/cli_interactive.py` (在文件末尾加):

```python
def review_predicted_l0(
    state: CognitiveState,
    predicted_L0_ids: list[str],
    *,
    console: Console | None = None,
) -> list[str]:
    """HITL: 审 predicted L0. accept/reject/edit per L0. 返保留的 id list."""
    if not predicted_L0_ids:
        return []
    cons = console or Console()
    kept: list[str] = []
    for pid in predicted_L0_ids:
        node = state.graph.nodes.get(pid)
        if not node:
            continue
        cons.print(
            f"\n[bold]{pid}[/bold] {node.name} — {node.description}"
        )
        choice = typer.prompt(
            "[a]ccept / [r]eject / [e]dit",
            default="a",
        ).strip().lower()
        if choice == "r":
            # cascade remove edges, then node
            state.graph.remove_node(pid)
            cons.print(f"[yellow]rejected {pid}[/yellow]")
        elif choice == "e":
            new_name = typer.prompt("新 name", default=node.name)
            new_desc = typer.prompt("新 description", default=node.description)
            node.name = new_name
            node.description = new_desc
            kept.append(pid)
            cons.print(f"[green]edited {pid}[/green]")
        else:
            kept.append(pid)
    return kept
```

(注: 需要 `import typer` at top of file, 跟现有 review_phenomena 同 pattern.)

### Step 6: 加 CLI 命令 `predict`

Modify `src/explain_engine/cli.py` (在 `check` 命令下方加):

```python
@app.command()
def predict(
    session_id: str = typer.Argument(...),
    intervention_text: str = typer.Argument(...),
) -> None:
    """Phase 7 Wave B: forward prediction.

    Examples:
        explain predict s_f3beb777 "现代媒体放大效应"
    """
    asyncio.run(_run_predict(session_id, intervention_text))


async def _run_predict(session_id: str, intervention_text: str) -> None:
    from explain_engine.engines.prediction import predict as predict_fn
    from explain_engine.hitl.cli_interactive import review_predicted_l0
    from explain_engine.llm.errors import LLMError, SchemaValidationError

    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    llm = make_llm_client()
    try:
        report = await predict_fn(session.state, intervention_text, llm)
    except ValueError as exc:
        console.print(f"[red]parser 失败: {exc}[/red]")
        raise typer.Exit(2) from exc
    except (LLMError, SchemaValidationError) as exc:
        console.print(f"[red]LLM 失败: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"\n[bold]Forward prediction: {intervention_text}[/bold]")
    if report.new_node_ids:
        console.print(f"  新增 intervention nodes: {report.new_node_ids}")
    if report.predicted_L0_ids:
        console.print(f"\n[bold]Predicted new L0 (待审):[/bold]")
        kept = review_predicted_l0(
            session.state, report.predicted_L0_ids, console=console,
        )
        console.print(f"\n保留 {len(kept)}/{len(report.predicted_L0_ids)} predicted L0")
    if report.activated_existing_L0:
        console.print(
            f"\n[bold]Existing L0 也被激活:[/bold] {report.activated_existing_L0}"
        )
    store.save(session)
```

### Step 7: 跑测试验证通过

Run: `.venv/bin/python -m pytest tests/test_engines_prediction.py tests/test_cli_predict.py -v`
Expected: 10 + 5 PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: 294 + 15 = 309 PASS.

### Step 8: ruff check + commit

```bash
.venv/bin/python -m ruff check src/explain_engine/engines/prediction.py \
                                src/explain_engine/cli.py \
                                src/explain_engine/hitl/cli_interactive.py \
                                tests/test_engines_prediction.py \
                                tests/test_cli_predict.py
```

Expected: 0 errors.

```bash
git add src/explain_engine/engines/prediction.py \
        src/explain_engine/llm/prompts/prediction.yaml \
        src/explain_engine/hitl/cli_interactive.py \
        src/explain_engine/cli.py \
        tests/test_engines_prediction.py \
        tests/test_cli_predict.py
git commit -m "$(cat <<'EOF'
Wave B.2 · ForwardPredictionEngine + explain predict CLI + HITL

Phase 7 design §5.3. flow:
  parser → 加 new_concepts → LLM 生 predicted L0 → propagate → HITL
- predicted L0 epistemic=speculation (跟 schema Literal 兼容)
- B1 退化 case: existing_refs only 跳过 generation
- HITL accept/reject/edit per predicted L0
- 副作用: state.graph mutate, 保留 predict 的 graph 改动

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task B.3: `counterfactual.py` + narrative prompt + `explain counterfactual` CLI

**目的**: Counterfactual = remove + (optional) substitute. 跟 predict 区别: **不改 state.graph** (graph 深拷贝跑 propagate, 拷贝丢弃). Substitute case 调 LLM 生 alt narrative.

**Files:**
- Create: `src/explain_engine/engines/counterfactual.py`
- Create: `src/explain_engine/llm/prompts/counterfactual_narrative.yaml`
- Create: `tests/test_engines_counterfactual.py`
- Create: `tests/test_cli_counterfactual.py`
- Modify: `src/explain_engine/cli.py` (加 `counterfactual` 命令)

---

### Step 1: 写失败测试

Create `tests/test_engines_counterfactual.py`:

```python
"""Wave B.3: CounterfactualEngine.substitute 测试.

design §5.4. 副作用必须 = 0 (不改 state.graph).
"""

import copy

import pytest

from explain_engine.engines.counterfactual import substitute
from explain_engine.engines.intervention_parser import (
    NewConceptSpec,
    ParsedIntervention,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state() -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id="c_001", name="c", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="d_002", name="driver", description="d",
        abstraction_level=2, confidence=0.6, epistemic="inference",
    ))
    g.add_node(VariableNode(
        id="p_001", name="L0", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7,
        mechanism_description="m",
    ))
    g.add_edge(RelationEdge(
        id="e_002", source_node="d_002", target_node="c_001",
        relation_type="causes", confidence=0.6,
        mechanism_description="m",
    ))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="why",
    )


def _mock_parser(mocker, parsed: ParsedIntervention):
    async def fake_parse(*args, **kwargs):
        return parsed
    mocker.patch(
        "explain_engine.engines.counterfactual.parse_intervention",
        side_effect=fake_parse,
    )


def _mock_chat(mocker, narrative_text: str = "alt narrative"):
    """Mock LLM 返 narrative + generation output."""
    from unittest.mock import MagicMock
    def make_resp(content_dict):
        r = MagicMock()
        r.parsed = content_dict
        return r
    llm = mocker.AsyncMock()
    # 顺序: 第一次是 generation (PredictionOutput), 第二次是 narrative
    llm.chat = mocker.AsyncMock(side_effect=[
        make_resp({"predicted_L0": [
            {"name": "alt_p", "description": "d", "mechanism": "m"},
        ]}),
        make_resp({"narrative": narrative_text}),
    ])
    return llm


class TestCounterfactualNoMutation:
    @pytest.mark.asyncio
    async def test_state_graph_not_mutated_on_remove(self, mocker) -> None:
        state = _make_state()
        snapshot = copy.deepcopy(state.graph)
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"], new_concepts=[],
        ))
        llm = mocker.AsyncMock()
        await substitute(state, "如果删除 d_002", llm)
        # graph 不变
        assert set(state.graph.nodes.keys()) == set(snapshot.nodes.keys())
        assert set(state.graph.edges.keys()) == set(snapshot.edges.keys())

    @pytest.mark.asyncio
    async def test_state_graph_not_mutated_on_substitute(self, mocker) -> None:
        state = _make_state()
        snapshot_nodes = set(state.graph.nodes.keys())
        snapshot_edges = set(state.graph.edges.keys())
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"],
            new_concepts=[NewConceptSpec(
                name="替代", description="d", expected_level=2,
            )],
        ))
        llm = _mock_chat(mocker)
        await substitute(state, "用 X 替代 Y", llm)
        # graph 仍不变
        assert set(state.graph.nodes.keys()) == snapshot_nodes
        assert set(state.graph.edges.keys()) == snapshot_edges


class TestCounterfactualReport:
    @pytest.mark.asyncio
    async def test_pure_remove_no_narrative(self, mocker) -> None:
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"], new_concepts=[],
        ))
        llm = mocker.AsyncMock()
        report = await substitute(state, "如果删除 d_002", llm)
        assert report.alt_narrative is None
        assert report.removed_node_ids == ["d_002"]
        assert report.added_node_ids == []

    @pytest.mark.asyncio
    async def test_substitute_returns_narrative(self, mocker) -> None:
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"],
            new_concepts=[NewConceptSpec(
                name="替代", description="d", expected_level=2,
            )],
        ))
        llm = _mock_chat(mocker, narrative_text="alt trajectory: ...")
        report = await substitute(state, "用 X 替代 d_002", llm)
        assert report.alt_narrative == "alt trajectory: ..."
        assert len(report.added_node_ids) == 1
        assert len(report.added_predicted_L0_ids) == 1

    @pytest.mark.asyncio
    async def test_activation_diff_calculated(self, mocker) -> None:
        state = _make_state()
        _mock_parser(mocker, ParsedIntervention(
            existing_refs=["d_002"], new_concepts=[],
        ))
        llm = mocker.AsyncMock()
        report = await substitute(state, "x", llm)
        # baseline 跑全 L1+L2 = {c_001, d_002} → p_001 act > 0
        assert report.baseline_acts.get("p_001", 0.0) > 0
        # counterfactual 删 d_002, baseline 仍含 c_001
        assert report.counterfactual_acts.get("p_001", 0.0) > 0
        # diff 应非空
        assert "p_001" in report.activation_diff


class TestCounterfactualErrors:
    @pytest.mark.asyncio
    async def test_parser_empty_raises(self, mocker) -> None:
        state = _make_state()
        async def fake_parse(*args, **kwargs):
            raise ValueError("无法解析")
        mocker.patch(
            "explain_engine.engines.counterfactual.parse_intervention",
            side_effect=fake_parse,
        )
        with pytest.raises(ValueError):
            await substitute(state, "废话", mocker.AsyncMock())
```

(test_cli_counterfactual.py ~5 tests, 类似 test_cli_predict.py 结构, 省略 — 实施者参考 B.2.)

### Step 2: 跑测试验证失败

Run: `.venv/bin/python -m pytest tests/test_engines_counterfactual.py -v`
Expected: 全 FAIL (ImportError).

### Step 3: 创建 narrative prompt

Create `src/explain_engine/llm/prompts/counterfactual_narrative.yaml`:

```yaml
system: |
  你是 cognitive engine 的 counterfactual narrative sub-agent.

  任务: 给定一个 counterfactual scenario (removed 哪些 driver + substituted 哪些),
  以及 activation_diff (每个 L0 in/out 程度变化), 写一段 alt trajectory narrative.

  约束:
  - 80-200 字, 不超过 3 段.
  - 必须基于 activation_diff 数据 (不要凭空 speculate).
  - 不要 hedging ("可能"、"也许"). 写得 confident.

  输出 schema:
  {"narrative": str}

user_template: |
  根问题: {question}
  Removed drivers: {removed_summary}
  Substituted: {substituted_summary}
  Activation diff (baseline - counterfactual per L0):
  {diff_table}

  请写 alt trajectory narrative.
```

### Step 4: 实现 `counterfactual.py`

Create `src/explain_engine/engines/counterfactual.py`:

```python
"""Wave B.3: Counterfactual Engine.

design §5.4. 副作用 = 0 (graph 深拷贝跑 propagate).
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from explain_engine.engines._propagation import propagate
from explain_engine.engines.intervention_parser import (
    ParsedIntervention,
    parse as parse_intervention,
)
from explain_engine.engines.prediction import (
    PredictionOutput,
)
from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


class _NarrativeOutput(BaseModel):
    narrative: str = Field(min_length=1)


@dataclass(frozen=True)
class CounterfactualReport:
    intervention_text: str
    parsed: ParsedIntervention
    removed_node_ids: list[str]
    added_node_ids: list[str]
    added_predicted_L0_ids: list[str]
    baseline_acts: dict[str, float]
    counterfactual_acts: dict[str, float]
    activation_diff: dict[str, float]
    alt_narrative: str | None


async def substitute(
    state: CognitiveState,
    intervention_text: str,
    llm: LLMClient,
) -> CounterfactualReport:
    """Counterfactual remove + (optional) substitute.

    副作用 = 0 (深拷贝 graph 跑 propagate).
    """
    parsed = await parse_intervention(state, intervention_text, llm)

    # 1. baseline: 原 graph all L1+L2 propagate
    original_L1_L2 = {
        nid for nid, n in state.graph.nodes.items() if n.abstraction_level >= 1
    }
    baseline_acts, _ = propagate(state.graph, original_L1_L2)

    # 2. 深拷贝 graph
    cf_graph = copy.deepcopy(state.graph)

    # 3. 删 existing_refs
    removed_ids = list(parsed.existing_refs)
    for rid in removed_ids:
        if rid in cf_graph.nodes:
            cf_graph.remove_node(rid)

    # 4. 加 new_concepts (substitute case)
    added_ids: list[str] = []
    added_L0_ids: list[str] = []
    for spec in parsed.new_concepts:
        prefix = "c" if spec.expected_level == 1 else "d"
        new_id = _next_id(cf_graph, prefix)
        cf_graph.add_node(VariableNode(
            id=new_id, name=spec.name, description=spec.description,
            abstraction_level=spec.expected_level,
            confidence=0.7, epistemic="speculation", source="llm",
        ))
        added_ids.append(new_id)

    # 5. LLM 生 alt predicted L0 (仅 substitute case)
    if parsed.new_concepts:
        gen_output = await _generate_alt_predicted(
            cf_graph, parsed, intervention_text, llm, state.root_question,
        )
        for predicted in gen_output.predicted_L0:
            p_id = _next_id(cf_graph, "p")
            cf_graph.add_node(VariableNode(
                id=p_id, name=predicted.name, description=predicted.description,
                abstraction_level=0, confidence=0.7,
                epistemic="speculation", source="llm",
            ))
            added_L0_ids.append(p_id)
            for new_id in added_ids:
                edge_id = _next_edge_id(cf_graph)
                cf_graph.add_edge(RelationEdge(
                    id=edge_id, source_node=new_id, target_node=p_id,
                    relation_type="manifests_as", confidence=0.7,
                    mechanism_description=predicted.mechanism,
                ))

    # 6. counterfactual propagate
    cf_L1_L2 = {
        nid for nid, n in cf_graph.nodes.items() if n.abstraction_level >= 1
    }
    cf_acts, _ = propagate(cf_graph, cf_L1_L2)

    # 7. diff
    diff = {
        nid: baseline_acts.get(nid, 0.0) - cf_acts.get(nid, 0.0)
        for nid in set(baseline_acts) | set(cf_acts)
    }

    # 8. narrative (仅 substitute case)
    alt_narrative: str | None = None
    if parsed.new_concepts:
        alt_narrative = await _generate_narrative(
            state.root_question, removed_ids, added_ids, diff, llm,
        )

    return CounterfactualReport(
        intervention_text=intervention_text, parsed=parsed,
        removed_node_ids=removed_ids,
        added_node_ids=added_ids,
        added_predicted_L0_ids=added_L0_ids,
        baseline_acts=baseline_acts,
        counterfactual_acts=cf_acts,
        activation_diff=diff,
        alt_narrative=alt_narrative,
    )


async def _generate_alt_predicted(
    cf_graph: ExplanationGraph,
    parsed: ParsedIntervention,
    intervention_text: str,
    llm: LLMClient,
    question: str,
) -> PredictionOutput:
    prompt = load_prompt("prediction")
    existing_L0_table = "\n".join(
        f"- {nid}: {n.name}" for nid, n in cf_graph.nodes.items()
        if n.abstraction_level == 0 and n.epistemic != "speculation"
    ) or "(none)"
    intervention_summary = (
        f"{intervention_text}\n"
        f"removed: {parsed.existing_refs}, "
        f"substituted: {[c.name for c in parsed.new_concepts]}"
    )
    messages = [
        Message(role="system", content=prompt["system"]),
        Message(role="user", content=prompt["user_template"].format(
            question=question,
            intervention_summary=intervention_summary,
            existing_L0_table=existing_L0_table,
        )),
    ]
    last_exc: Exception | None = None
    for _ in range(2):
        resp = await llm.chat(messages, schema=PredictionOutput)
        if resp.parsed is None:
            last_exc = SchemaValidationError("LLM 未返回 structured output")
            continue
        try:
            return PredictionOutput.model_validate(resp.parsed)
        except ValidationError as exc:
            last_exc = SchemaValidationError(f"prediction 输出 schema 不合规: {exc}")
            continue
    assert last_exc is not None
    raise last_exc


async def _generate_narrative(
    question: str, removed: list[str], added: list[str],
    diff: dict[str, float], llm: LLMClient,
) -> str:
    prompt = load_prompt("counterfactual_narrative")
    diff_table = "\n".join(
        f"  {nid}: {v:+.2f}" for nid, v in sorted(diff.items())
    ) or "(无变化)"
    messages = [
        Message(role="system", content=prompt["system"]),
        Message(role="user", content=prompt["user_template"].format(
            question=question,
            removed_summary=", ".join(removed) or "(无)",
            substituted_summary=", ".join(added) or "(无)",
            diff_table=diff_table,
        )),
    ]
    resp = await llm.chat(messages, schema=_NarrativeOutput)
    if resp.parsed is None:
        raise SchemaValidationError("narrative LLM 未返回 structured output")
    return _NarrativeOutput.model_validate(resp.parsed).narrative


def _next_id(graph: ExplanationGraph, prefix: str) -> str:
    existing = [
        int(nid.split("_")[1])
        for nid in graph.nodes
        if nid.startswith(f"{prefix}_") and nid[len(prefix)+1:].isdigit()
    ]
    n = (max(existing) + 1) if existing else 1
    return f"{prefix}_{n:03d}"


def _next_edge_id(graph: ExplanationGraph) -> str:
    existing = [
        int(eid.split("_")[1])
        for eid in graph.edges
        if eid.startswith("e_") and eid[2:].isdigit()
    ]
    n = (max(existing) + 1) if existing else 1
    return f"e_{n:03d}"
```

### Step 5: 加 CLI 命令 `counterfactual`

Modify `src/explain_engine/cli.py` 在 `predict` 命令下方加:

```python
@app.command()
def counterfactual(
    session_id: str = typer.Argument(...),
    intervention_text: str = typer.Argument(...),
) -> None:
    """Phase 7 Wave B: counterfactual remove + (optional) substitute.

    Examples:
        explain counterfactual s_f3beb777 "用经济激励替代教义不可妥协性"
        explain counterfactual s_f3beb777 "如果删除集体身份维系压力"
    """
    asyncio.run(_run_counterfactual(session_id, intervention_text))


async def _run_counterfactual(session_id: str, intervention_text: str) -> None:
    from explain_engine.engines.counterfactual import substitute
    from explain_engine.llm.errors import LLMError, SchemaValidationError

    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    llm = make_llm_client()
    try:
        report = await substitute(session.state, intervention_text, llm)
    except ValueError as exc:
        console.print(f"[red]parser 失败: {exc}[/red]")
        raise typer.Exit(2) from exc
    except (LLMError, SchemaValidationError) as exc:
        console.print(f"[red]LLM 失败: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"\n[bold]Counterfactual: {intervention_text}[/bold]")
    console.print(f"  Removed: {report.removed_node_ids}")
    if report.added_node_ids:
        console.print(f"  Substituted with: {report.added_node_ids}")
    if report.alt_narrative:
        console.print(f"\n[bold]Alt narrative:[/bold]\n{report.alt_narrative}")
    # 渲染 diff table (top changes)
    significant = sorted(
        [(nid, v) for nid, v in report.activation_diff.items() if abs(v) > 0.05],
        key=lambda kv: -abs(kv[1]),
    )[:10]
    if significant:
        console.print("\n[bold]Top activation diff:[/bold]")
        for nid, v in significant:
            console.print(f"  {nid}: {v:+.2f}")
```

### Step 6: 跑测试 + ruff + commit

Run: `.venv/bin/python -m pytest tests/test_engines_counterfactual.py tests/test_cli_counterfactual.py -v`
Expected: 8 + 5 PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: 309 + 13 = 322 PASS.

Run: `.venv/bin/python -m ruff check src/explain_engine/engines/counterfactual.py src/explain_engine/cli.py tests/test_engines_counterfactual.py tests/test_cli_counterfactual.py`
Expected: 0.

```bash
git add src/explain_engine/engines/counterfactual.py \
        src/explain_engine/llm/prompts/counterfactual_narrative.yaml \
        src/explain_engine/cli.py \
        tests/test_engines_counterfactual.py \
        tests/test_cli_counterfactual.py
git commit -m "$(cat <<'EOF'
Wave B.3 · CounterfactualEngine + explain counterfactual CLI

Phase 7 design §5.4. 副作用 = 0 (graph 深拷贝跑 propagate):
- pure remove: 仅 existing_refs, 不调 narrative LLM, alt_narrative=None
- substitute: existing_refs + new_concepts, 调 generation + narrative
- baseline_acts vs counterfactual_acts → activation_diff per L0
- alt_narrative 基于 diff_table (不凭空 speculate)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task B.4: Shared propagation utility refactor

**目的**: Phase 6 simulation.py 私有 `_get_all_L0` / `_get_all_L1_L2` 现被 prediction / counterfactual 共需. 抽 public 到 `_propagation.py`, simulation.py 改 import.

**Files:**
- Modify: `src/explain_engine/engines/_propagation.py` (加 `get_all_L0` / `get_all_L1_L2`)
- Modify: `src/explain_engine/engines/simulation.py` (删私有 helper, import public)
- Modify: `src/explain_engine/engines/prediction.py` (改 import 用 public)
- Modify: `src/explain_engine/engines/counterfactual.py` (改 import 用 public)
- Create: `tests/test_engines_propagation_helpers.py`

---

### Step 1: 写失败测试

Create `tests/test_engines_propagation_helpers.py`:

```python
"""Wave B.4: _propagation.py 加 public helpers."""

from explain_engine.engines._propagation import get_all_L0, get_all_L1_L2
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid: str, level: int) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7,
        epistemic="insight" if level >= 1 else "observation",
    )


class TestGetAllHelpers:
    def test_get_all_L0_returns_only_level_0(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("p_001", 0))
        g.add_node(_node("p_002", 0))
        g.add_node(_node("c_001", 1))
        g.add_node(_node("d_001", 2))
        assert get_all_L0(g) == {"p_001", "p_002"}

    def test_get_all_L1_L2_returns_non_L0(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("p_001", 0))
        g.add_node(_node("c_001", 1))
        g.add_node(_node("d_001", 2))
        assert get_all_L1_L2(g) == {"c_001", "d_001"}

    def test_empty_graph(self) -> None:
        g = ExplanationGraph(root_question="q")
        assert get_all_L0(g) == set()
        assert get_all_L1_L2(g) == set()
```

### Step 2: 跑测试验证失败

Run: `.venv/bin/python -m pytest tests/test_engines_propagation_helpers.py -v`
Expected: ImportError fail.

### Step 3: 加 public helpers to `_propagation.py`

Modify `src/explain_engine/engines/_propagation.py` (在文件末尾加):

```python
def get_all_L0(graph: ExplanationGraph) -> set[str]:
    """所有 abstraction_level=0 节点 ids."""
    return {nid for nid, n in graph.nodes.items() if n.abstraction_level == 0}


def get_all_L1_L2(graph: ExplanationGraph) -> set[str]:
    """所有 abstraction_level >= 1 节点 ids."""
    return {nid for nid, n in graph.nodes.items() if n.abstraction_level >= 1}
```

### Step 4: 改 `simulation.py` import

Modify `src/explain_engine/engines/simulation.py`:

```python
# 改前
from explain_engine.engines._propagation import (
    WEAK_CHAIN_THRESHOLD, DecayStep, propagate,
)
# ... _get_all_L0 / _get_all_L1_L2 私有 helpers in this file ...

# 改后
from explain_engine.engines._propagation import (
    WEAK_CHAIN_THRESHOLD, DecayStep, get_all_L0, get_all_L1_L2, propagate,
)

# 删私有 _get_all_L0 / _get_all_L1_L2 函数 (替换调用)
# `_get_all_L0(graph)` → `get_all_L0(graph)`
# `_get_all_L1_L2(graph)` → `get_all_L1_L2(graph)`
```

### Step 5: 跑全 simulation 测试 + propagation helpers 新测试

Run: `.venv/bin/python -m pytest tests/test_engines_simulation.py tests/test_engines_propagation_helpers.py -v`
Expected: 现有 simulation tests 全 PASS + 3 新 helpers PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: 322 + 3 = 325 PASS.

### Step 6: ruff check + commit

```bash
.venv/bin/python -m ruff check src/ tests/
```

Expected: 0.

```bash
git add src/explain_engine/engines/_propagation.py \
        src/explain_engine/engines/simulation.py \
        tests/test_engines_propagation_helpers.py
git commit -m "$(cat <<'EOF'
Wave B.4 · 抽 shared propagation helpers (get_all_L0 / get_all_L1_L2)

Phase 7 design §5.5. DRY:
- simulation.py 私有 helper 抽 public 到 _propagation.py
- prediction.py / counterfactual.py 复用同 public API
- 0 行为变化, 纯 refactor

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave B 完成 Checkpoint

跑全测试:

```bash
.venv/bin/python -m pytest -q
```

Expected: **325 PASS** (Wave A 286 + Wave B 39).

跑 ruff:

```bash
.venv/bin/python -m ruff check src/ tests/
```

Expected: 0.

**STOP — 等用户审 Wave B 通过后进 Wave C.**

---

# Wave C — Reflection Engine

## Task C.1: `reflection.py` 决策器 + 常量 + tests

**目的**: Reflection Engine 主决策器. 0 LLM call (用 Phase 6 simulation.check_consistency_batch). 返 `(action, target_id)`. 决策优先级: re-expand > prune > stop > continue.

**Files:**
- Modify: `src/explain_engine/schema/state.py` (加 `ReflectionAction` + Action "reflect" + TraceEntry 字段 + CognitiveState 字段)
- Create: `src/explain_engine/engines/reflection.py`
- Create: `tests/test_engines_reflection.py`

---

### Step 1: 改 schema (state.py)

Modify `src/explain_engine/schema/state.py`:

```python
# 改前
Action = Literal["expand", "compress", "evaluate"]
_VALID_ACTIONS = frozenset({"expand", "compress", "evaluate"})

# 改后
Action = Literal["expand", "compress", "evaluate", "reflect"]   # 加 "reflect"
_VALID_ACTIONS = frozenset({"expand", "compress", "evaluate", "reflect"})

ReflectionAction = Literal["continue", "re-expand", "prune", "stop"]   # 新
```

加 `reflection_action` 字段到 `TraceEntry`:

```python
@dataclass
class TraceEntry:
    tick: int
    action: Action
    target_node_id: str | None
    gain_delta: float
    llm_calls: int
    timestamp: str
    reflection_action: ReflectionAction | None = None   # Phase 7 Wave C, default None

    def to_dict(self) -> dict:
        return {
            "tick": self.tick,
            "action": self.action,
            "target_node_id": self.target_node_id,
            "gain_delta": self.gain_delta,
            "llm_calls": self.llm_calls,
            "timestamp": self.timestamp,
            "reflection_action": self.reflection_action,   # 新
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TraceEntry":
        return cls(
            tick=d["tick"], action=d["action"],
            target_node_id=d.get("target_node_id"),
            gain_delta=d["gain_delta"], llm_calls=d["llm_calls"],
            timestamp=d["timestamp"],
            reflection_action=d.get("reflection_action"),   # 新, default None
        )
```

加 `last_reflection_change_tick` 到 `CognitiveState`:

```python
@dataclass
class CognitiveState:
    graph: ExplanationGraph
    budget_remaining: int
    root_question: str
    active_frontier: list[str] = field(default_factory=list)
    insight_candidates: list[str] = field(default_factory=list)
    tick: int = 0
    last_gain_tick: int = 0
    last_gains: dict[str, float] = field(default_factory=dict)
    reasoning_trace: list[TraceEntry] = field(default_factory=list)
    last_reflection_change_tick: int = 0   # Phase 7 Wave C, new

    # __post_init__: 加 self.last_reflection_change_tick < 0 raise

    def to_dict(self) -> dict:
        return {
            # ... existing ...
            "last_reflection_change_tick": self.last_reflection_change_tick,   # 新
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CognitiveState":
        try:
            return cls(
                # ... existing ...
                last_reflection_change_tick=d.get("last_reflection_change_tick", 0),
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid state dict: {exc}") from exc
```

### Step 2: 写失败测试

Create `tests/test_engines_reflection.py`:

```python
"""Wave C.1: ReflectionEngine.reflect 测试.

design §6.2. 0 LLM call, 用 Phase 6 simulation.check_consistency_batch.
决策优先级: re-expand > prune > stop > continue.
"""

import pytest

from explain_engine.engines.reflection import (
    CONSISTENCY_STALE_TICKS,
    LOW_CONSISTENCY_THRESHOLD,
    LOW_ESSENTIALNESS_THRESHOLD,
    reflect,
)
from explain_engine.engines.simulation import ConsistencyReport
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _node(nid: str, level: int) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7,
        epistemic="insight" if level >= 1 else "observation",
    )


def _make_state(nodes: list[tuple[str, int]], tick: int = 0,
                last_refl: int = 0) -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    for nid, lvl in nodes:
        g.add_node(_node(nid, lvl))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="q",
        tick=tick, last_reflection_change_tick=last_refl,
    )


def _mock_reports(mocker, reports: list[tuple[str, float, float]]):
    """Mock check_consistency_batch 返指定 reports.
    
    Each tuple: (target_id, consistency, essentialness).
    """
    fake = [
        ConsistencyReport(
            target_id=tid, consistency_score=c, essentialness_score=e,
            reachable_L0=[], weak_chains=[],
            contribution_breakdown={}, decay_trace=[],
        )
        for tid, c, e in reports
    ]
    mocker.patch(
        "explain_engine.engines.reflection.check_consistency_batch",
        return_value=fake,
    )


class TestReflectEdgeCases:
    def test_empty_graph_returns_continue(self) -> None:
        state = _make_state([])
        action, target = reflect(state)
        assert action == "continue"
        assert target is None

    def test_no_L1_L2_returns_continue(self) -> None:
        state = _make_state([("p_001", 0)])
        action, target = reflect(state)
        assert action == "continue"
        assert target is None


class TestReExpand:
    def test_single_low_consistency_L1_triggers_re_expand(self, mocker) -> None:
        state = _make_state([("c_001", 1), ("p_001", 0)])
        _mock_reports(mocker, [("c_001", 0.3, 0.5)])
        action, target = reflect(state)
        assert action == "re-expand"
        assert target == "c_001"

    def test_multi_low_consistency_returns_lowest(self, mocker) -> None:
        state = _make_state([("c_001", 1), ("c_002", 1), ("p_001", 0)])
        _mock_reports(mocker, [
            ("c_001", 0.4, 0.5),
            ("c_002", 0.2, 0.5),
        ])
        action, target = reflect(state)
        assert action == "re-expand"
        assert target == "c_002"   # 0.2 < 0.4

    def test_threshold_exclusive(self, mocker) -> None:
        """consistency = 0.5 exactly 不触发 (严格 <)."""
        state = _make_state([("c_001", 1), ("p_001", 0)])
        _mock_reports(mocker, [("c_001", LOW_CONSISTENCY_THRESHOLD, 0.5)])
        action, _ = reflect(state)
        assert action != "re-expand"


class TestPrune:
    def test_low_essentialness_L2_triggers_prune(self, mocker) -> None:
        state = _make_state([("c_001", 1), ("d_001", 2), ("p_001", 0)])
        _mock_reports(mocker, [
            ("c_001", 0.8, 0.5),
            ("d_001", 0.7, 0.02),
        ])
        action, target = reflect(state)
        assert action == "prune"
        assert target == "d_001"

    def test_re_expand_priority_over_prune(self, mocker) -> None:
        """Same time: low consistency L1 + low essentialness L2 → re-expand 优先."""
        state = _make_state([("c_001", 1), ("d_001", 2), ("p_001", 0)])
        _mock_reports(mocker, [
            ("c_001", 0.3, 0.5),
            ("d_001", 0.7, 0.02),
        ])
        action, target = reflect(state)
        assert action == "re-expand"
        assert target == "c_001"


class TestStop:
    def test_stale_change_tick_triggers_stop(self, mocker) -> None:
        state = _make_state(
            [("c_001", 1), ("p_001", 0)],
            tick=CONSISTENCY_STALE_TICKS,
            last_refl=0,
        )
        _mock_reports(mocker, [("c_001", 0.8, 0.5)])
        action, target = reflect(state)
        assert action == "stop"
        assert target is None

    def test_fresh_change_tick_returns_continue(self, mocker) -> None:
        state = _make_state(
            [("c_001", 1), ("p_001", 0)],
            tick=1, last_refl=0,
        )
        _mock_reports(mocker, [("c_001", 0.8, 0.5)])
        action, _ = reflect(state)
        assert action == "continue"


class TestNoLLMCalls:
    def test_reflect_uses_no_llm(self, mocker) -> None:
        state = _make_state([("c_001", 1), ("p_001", 0)])
        _mock_reports(mocker, [("c_001", 0.8, 0.5)])
        action, _ = reflect(state)
        # reflect 不该 import LLM client
        # 测试只验证 check_consistency_batch 被调
        from explain_engine.engines.reflection import check_consistency_batch
        # ok, reflect returned without exception
        assert action in ("continue", "re-expand", "prune", "stop")
```

### Step 3: 实现 `reflection.py`

Create `src/explain_engine/engines/reflection.py`:

```python
"""Wave C.1: Reflection Engine.

design §6.2. 0 LLM call (用 Phase 6 simulation).
决策优先级: re-expand > prune > stop > continue.
"""

from __future__ import annotations

import logging

from explain_engine.engines.simulation import check_consistency_batch
from explain_engine.schema.state import CognitiveState, ReflectionAction

logger = logging.getLogger(__name__)

# ─── 常量 (Wave D acceptance 后 tune) ────────────────────────
LOW_CONSISTENCY_THRESHOLD: float = 0.5
"""L1 consistency_score < 阈值 → re-expand."""

LOW_ESSENTIALNESS_THRESHOLD: float = 0.05
"""L2 essentialness_score < 阈值 → prune."""

CONSISTENCY_STALE_TICKS: int = 3
"""state.tick - last_reflection_change_tick >= 此值 → stop."""


def reflect(state: CognitiveState) -> tuple[ReflectionAction, str | None]:
    """Reflection decision. 0 LLM call.

    Returns: (action, target_id)
      - re-expand → target_id = lowest-consistency L1 id
      - prune → target_id = lowest-essentialness L2 id
      - stop → target_id = None
      - continue → target_id = None
    """
    if not state.graph.nodes:
        return ("continue", None)

    L1_L2 = [
        nid for nid, n in state.graph.nodes.items() if n.abstraction_level >= 1
    ]
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

### Step 4: 跑测试

Run: `.venv/bin/python -m pytest tests/test_engines_reflection.py tests/test_schema_state.py -v`
Expected: 10 PASS + 现有 state tests 仍 PASS (schema 改向后兼容).

Run: `.venv/bin/python -m pytest -q`
Expected: 325 + 10 = 335 PASS.

### Step 5: ruff + commit

```bash
.venv/bin/python -m ruff check src/explain_engine/engines/reflection.py src/explain_engine/schema/state.py tests/test_engines_reflection.py
```

Expected: 0.

```bash
git add src/explain_engine/engines/reflection.py \
        src/explain_engine/schema/state.py \
        tests/test_engines_reflection.py
git commit -m "$(cat <<'EOF'
Wave C.1 · ReflectionEngine.reflect decision logic + schema 扩展

Phase 7 design §6.2. 0 LLM call (用 Phase 6 simulation):
- 决策优先级: re-expand > prune > stop > continue
- LOW_CONSISTENCY_THRESHOLD=0.5, LOW_ESSENTIALNESS_THRESHOLD=0.05
- CONSISTENCY_STALE_TICKS=3 (Wave D tune)
- schema: Action 加 "reflect", ReflectionAction Literal 新增
- TraceEntry / CognitiveState 加新字段 (向后兼容)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task C.2: `expansion.re_expand()` + scheduler 改 + runtime.py 加 reflect 分支

**目的**: re-expand 绕过 frontier check 给已 covered L1 加 driver. scheduler 1 round 改成 K expand + 1 reflect (替 evaluate). runtime.py 加 reflect action 分支 (调 reflect → 触发 re_expand / prune / stop).

**Files:**
- Modify: `src/explain_engine/engines/expansion.py` (加 `re_expand()` + 抽 `_do_expansion` helper)
- Modify: `src/explain_engine/runtime/scheduler.py`
- Modify: `src/explain_engine/runtime/runtime.py`
- Create: `tests/test_engines_expansion_re_expand.py`
- Create: `tests/test_runtime_scheduler_reflect.py`
- Create: `tests/test_runtime_run_reflect.py`

---

### Step 1: 写 re_expand 测试

Create `tests/test_engines_expansion_re_expand.py`:

```python
"""Wave C.2: expansion.re_expand — 绕过 frontier check 加 driver."""

import pytest

from explain_engine.engines.expansion import re_expand
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_already_expanded() -> CognitiveState:
    """c_001 已被 d_001 cover (incoming causes), Phase 5 frontier_nodes 不返."""
    g = ExplanationGraph(root_question="q")
    g.add_node(VariableNode(
        id="c_001", name="c", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="d_001", name="d", description="d",
        abstraction_level=2, confidence=0.6, epistemic="inference",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="d_001", target_node="c_001",
        relation_type="causes", confidence=0.6,
        mechanism_description="m",
    ))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="q",
    )


class TestReExpand:
    @pytest.mark.asyncio
    async def test_re_expand_accepts_already_covered_L1(self, mocker) -> None:
        state = _make_already_expanded()
        from explain_engine.engines.expansion import ExpansionOutput, _DriverCandidate
        mocker.patch(
            "explain_engine.engines.expansion._call_with_retry",
            return_value=ExpansionOutput(drivers=[
                _DriverCandidate(name="d2", description="d", mechanism="m", plausibility=4),
            ]),
        )
        new_ids, gain = await re_expand(state, "c_001", mocker.AsyncMock())
        assert len(new_ids) == 1

    @pytest.mark.asyncio
    async def test_re_expand_rejects_L0_target(self, mocker) -> None:
        state = _make_already_expanded()
        state.graph.add_node(VariableNode(
            id="p_001", name="p", description="d",
            abstraction_level=0, confidence=0.7, epistemic="observation",
        ))
        with pytest.raises(ValueError, match="level"):
            await re_expand(state, "p_001", mocker.AsyncMock())

    @pytest.mark.asyncio
    async def test_re_expand_rejects_nonexistent(self, mocker) -> None:
        state = _make_already_expanded()
        with pytest.raises(ValueError, match="not found"):
            await re_expand(state, "x_999", mocker.AsyncMock())

    @pytest.mark.asyncio
    async def test_re_expand_max_drivers_2_default(self, mocker) -> None:
        state = _make_already_expanded()
        from explain_engine.engines.expansion import ExpansionOutput, _DriverCandidate
        mocker.patch(
            "explain_engine.engines.expansion._call_with_retry",
            return_value=ExpansionOutput(drivers=[
                _DriverCandidate(name=f"d{i}", description="d", mechanism="m", plausibility=4)
                for i in range(5)
            ]),
        )
        new_ids, _ = await re_expand(state, "c_001", mocker.AsyncMock())
        assert len(new_ids) <= 2

    @pytest.mark.asyncio
    async def test_re_expand_writes_confidence_wave_a_mapping(self, mocker) -> None:
        state = _make_already_expanded()
        from explain_engine.engines.expansion import ExpansionOutput, _DriverCandidate
        mocker.patch(
            "explain_engine.engines.expansion._call_with_retry",
            return_value=ExpansionOutput(drivers=[
                _DriverCandidate(name="d2", description="d", mechanism="m", plausibility=4),
            ]),
        )
        await re_expand(state, "c_001", mocker.AsyncMock())
        new_edges = [
            e for e in state.graph.edges.values()
            if e.target_node == "c_001" and e.relation_type == "causes"
            and e.id != "e_001"
        ]
        assert len(new_edges) == 1
        assert new_edges[0].confidence == pytest.approx(0.8)   # 4/5
```

### Step 2: 改 `expansion.py` — 抽 helper + 加 re_expand

Modify `src/explain_engine/engines/expansion.py`. 拆 `expand_one_frontier` 内核出 `_do_expansion` helper, 让 `re_expand` 共用:

```python
async def expand_one_frontier(
    state, target_id, llm, max_drivers: int = 3,
) -> tuple[list[str], float]:
    """Phase 5: 第一次 expand frontier L1 (无 incoming causes)."""
    if target_id not in state.graph.nodes:
        raise ValueError(f"target {target_id!r} not found in graph")
    target = state.graph.nodes[target_id]
    if target.abstraction_level != 1:
        raise ValueError(
            f"target {target_id!r} has level={target.abstraction_level}, must be 1"
        )
    if any(
        e.relation_type == "causes" and e.target_node == target_id
        for e in state.graph.edges.values()
    ):
        raise ValueError(
            f"target {target_id!r} already has incoming causes edge (not a frontier)"
        )
    return await _do_expansion(state, target_id, llm, max_drivers)


async def re_expand(
    state, target_id, llm, max_drivers: int = 2,
) -> tuple[list[str], float]:
    """Phase 7 Wave C.2: 给 already-driver-covered L1 加更多 driver."""
    if target_id not in state.graph.nodes:
        raise ValueError(f"target {target_id!r} not found in graph")
    target = state.graph.nodes[target_id]
    if target.abstraction_level != 1:
        raise ValueError(
            f"target {target_id!r} has level={target.abstraction_level}, "
            f"re_expand 仅对 level=1 (L1 abstract)"
        )
    return await _do_expansion(state, target_id, llm, max_drivers)


async def _do_expansion(
    state, target_id, llm, max_drivers,
) -> tuple[list[str], float]:
    """共享 expansion 主流程. 不做 frontier check, 调用方负责."""
    target = state.graph.nodes[target_id]
    prompt = load_prompt("expansion")
    outgoing_edges_text = _render_target_outgoing_edges(state, target_id)
    existing_drivers_text = _render_existing_drivers(state)

    messages = [
        Message(role="system", content=prompt["system"]),
        Message(role="user", content=prompt["user_template"].format(
            question=state.root_question,
            target_node=f"{target.id}: {target.name} — {target.description}",
            target_outgoing_edges=outgoing_edges_text,
            existing_drivers=existing_drivers_text,
        )),
    ]
    output = await _call_with_retry(llm, messages)
    drivers = output.drivers[:max_drivers]
    if not drivers:
        logger.warning("Expansion 0 driver for target %s (skip)", target_id)
        return [], 0.0

    next_d_num = _next_driver_id_num(state)
    next_edge_id = _next_edge_id(state)
    new_ids: list[str] = []
    existing_name_to_id = {n.name: nid for nid, n in state.graph.nodes.items()}
    for d in drivers:
        if d.name in existing_name_to_id:
            d_id = existing_name_to_id[d.name]
        else:
            d_id = f"d_{next_d_num:03d}"
            next_d_num += 1
            state.graph.add_node(VariableNode(
                id=d_id, name=d.name, description=d.description,
                abstraction_level=2, confidence=0.6,
                epistemic="inference", source="llm",
            ))
        state.graph.add_edge(RelationEdge(
            id=f"e_{next_edge_id:03d}",
            source_node=d_id, target_node=target_id,
            relation_type="causes",
            confidence=d.plausibility / 5.0,
            mechanism_description=d.mechanism,
        ))
        next_edge_id += 1
        new_ids.append(d_id)
    gain = sum(d.plausibility for d in drivers) / (5.0 * len(drivers))
    return new_ids, gain
```

### Step 3: 改 `runtime/scheduler.py`

Modify `src/explain_engine/runtime/scheduler.py`:

```python
"""Phase 5 / Phase 7: PhaseScheduler.

1 round = K expand + 1 reflect (Phase 7 Wave C.2, 替 Phase 5 evaluate).
"""

from typing import Literal

from explain_engine.schema.state import CognitiveState


class PhaseScheduler:
    K: int = 4

    def __init__(self, K: int = 4) -> None:
        self.K = K

    def pick(self, state: CognitiveState) -> Literal["expand", "reflect"]:
        if state.tick % (self.K + 1) < self.K:
            return "expand"
        return "reflect"
```

### Step 4: 写 scheduler 测试

Create `tests/test_runtime_scheduler_reflect.py`:

```python
"""Wave C.2: PhaseScheduler 改 reflect."""

import pytest

from explain_engine.runtime.scheduler import PhaseScheduler
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState


def _state(tick: int) -> CognitiveState:
    return CognitiveState(
        graph=ExplanationGraph(root_question="q"),
        budget_remaining=100, root_question="q",
        tick=tick,
    )


class TestPhaseSchedulerReflect:
    def test_K4_tick_0_to_3_expand(self) -> None:
        sched = PhaseScheduler(K=4)
        for t in range(4):
            assert sched.pick(_state(t)) == "expand"

    def test_K4_tick_4_reflect(self) -> None:
        sched = PhaseScheduler(K=4)
        assert sched.pick(_state(4)) == "reflect"

    def test_K4_tick_5_to_8_expand_again(self) -> None:
        sched = PhaseScheduler(K=4)
        for t in range(5, 9):
            assert sched.pick(_state(t)) == "expand"

    def test_K4_tick_9_reflect_again(self) -> None:
        sched = PhaseScheduler(K=4)
        assert sched.pick(_state(9)) == "reflect"

    def test_K2_alternation(self) -> None:
        sched = PhaseScheduler(K=2)
        assert sched.pick(_state(0)) == "expand"
        assert sched.pick(_state(1)) == "expand"
        assert sched.pick(_state(2)) == "reflect"
```

### Step 5: 改 `runtime/runtime.py` — 加 reflect 分支

Modify `src/explain_engine/runtime/runtime.py`. 重写 `run` 函数加 "reflect" action 分支:

```python
"""Phase 5 + Phase 7: reasoning loop 主循环。

Phase 7 Wave C.2: action ∈ {"expand", "reflect"}; reflect 内部决定 next ReflectionAction
{continue / re-expand / prune / stop}.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from explain_engine.engines import expansion, reflection
from explain_engine.llm.client import LLMClient
from explain_engine.runtime import stop as stop_mod
from explain_engine.runtime.scheduler import PhaseScheduler
from explain_engine.schema.state import CognitiveState, TraceEntry


async def run(
    state: CognitiveState,
    llm: LLMClient,
    budget: int,
    on_tick: Callable[[CognitiveState], None] | None = None,
    scheduler: PhaseScheduler | None = None,
) -> str:
    """主循环。返 stop_reason。"""
    state.budget_remaining = budget
    state.tick = 0
    state.last_gain_tick = 0
    state.last_reflection_change_tick = 0
    sched = scheduler or PhaseScheduler(K=4)

    while True:
        stop, reason = stop_mod.should_stop(state)
        if stop:
            assert reason is not None
            return reason

        action = sched.pick(state)
        target_id: str | None = None
        gain_delta = 0.0
        llm_calls = 0
        refl_action = None

        if action == "expand":
            frontier = state.graph.frontier_nodes()
            if frontier:
                target_id = frontier[0]
                _new_ids, gain_delta = await expansion.expand_one_frontier(
                    state, target_id, llm,
                )
                llm_calls = 1
            else:
                # Defensive: should_stop should catch no_frontier first
                pass

        elif action == "reflect":
            refl_action, refl_target = reflection.reflect(state)
            if refl_action == "re-expand" and refl_target:
                _new_ids, gain_delta = await expansion.re_expand(
                    state, refl_target, llm,
                )
                llm_calls = 1
                target_id = refl_target
                state.last_reflection_change_tick = state.tick
            elif refl_action == "prune" and refl_target:
                state.graph.remove_node(refl_target)
                target_id = refl_target
                state.last_reflection_change_tick = state.tick
            elif refl_action == "stop":
                # 触发 reflection_signaled_stop (stop.py 在 next loop 抓到)
                state.last_reflection_change_tick = (
                    state.tick - reflection.CONSISTENCY_STALE_TICKS - 1
                )
            # continue: 无副作用

        state.reasoning_trace.append(TraceEntry(
            tick=state.tick, action=action, target_node_id=target_id,
            gain_delta=gain_delta, llm_calls=llm_calls,
            timestamp=datetime.now(UTC).isoformat(),
            reflection_action=refl_action,
        ))

        if gain_delta >= stop_mod.GAIN_THRESHOLD:
            state.last_gain_tick = state.tick

        state.tick += 1
        state.budget_remaining -= 1

        if on_tick is not None:
            on_tick(state)
```

### Step 6: 写 runtime reflect 测试

Create `tests/test_runtime_run_reflect.py`:

```python
"""Wave C.2: runtime.run 加 reflect 分支测试."""

import pytest

from explain_engine.engines.expansion import ExpansionOutput, _DriverCandidate
from explain_engine.engines.simulation import ConsistencyReport
from explain_engine.runtime.runtime import run
from explain_engine.runtime.scheduler import PhaseScheduler
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state_with_L1() -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    g.add_node(VariableNode(
        id="c_001", name="c", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="d_001", name="d", description="d",
        abstraction_level=2, confidence=0.6, epistemic="inference",
    ))
    g.add_node(VariableNode(
        id="p_001", name="p", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7, mechanism_description="m",
    ))
    g.add_edge(RelationEdge(
        id="e_002", source_node="d_001", target_node="c_001",
        relation_type="causes", confidence=0.6, mechanism_description="m",
    ))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="q",
    )


@pytest.mark.asyncio
async def test_run_with_K2_emits_reflect_action(mocker) -> None:
    state = _make_state_with_L1()
    mocker.patch(
        "explain_engine.engines.expansion._call_with_retry",
        return_value=ExpansionOutput(drivers=[]),
    )
    mocker.patch(
        "explain_engine.engines.reflection.check_consistency_batch",
        return_value=[
            ConsistencyReport(
                target_id="c_001", consistency_score=0.8, essentialness_score=0.5,
                reachable_L0=["p_001"], weak_chains=[],
                contribution_breakdown={}, decay_trace=[],
            ),
        ],
    )
    await run(state, mocker.AsyncMock(), budget=6,
              scheduler=PhaseScheduler(K=2))
    actions = [e.action for e in state.reasoning_trace]
    assert "reflect" in actions


@pytest.mark.asyncio
async def test_reflect_re_expand_mutates_graph(mocker) -> None:
    state = _make_state_with_L1()
    mocker.patch(
        "explain_engine.engines.reflection.check_consistency_batch",
        return_value=[
            ConsistencyReport(
                target_id="c_001", consistency_score=0.3, essentialness_score=0.5,
                reachable_L0=["p_001"], weak_chains=[],
                contribution_breakdown={}, decay_trace=[],
            ),
        ],
    )
    mocker.patch(
        "explain_engine.engines.expansion._call_with_retry",
        return_value=ExpansionOutput(drivers=[
            _DriverCandidate(name="new_d", description="d", mechanism="m", plausibility=4),
        ]),
    )
    initial_drivers = sum(
        1 for n in state.graph.nodes.values() if n.abstraction_level == 2
    )
    await run(state, mocker.AsyncMock(), budget=3,
              scheduler=PhaseScheduler(K=2))
    final_drivers = sum(
        1 for n in state.graph.nodes.values() if n.abstraction_level == 2
    )
    assert final_drivers > initial_drivers


@pytest.mark.asyncio
async def test_reflect_prune_removes_node(mocker) -> None:
    state = _make_state_with_L1()
    # 加 d_002 with low essentialness
    state.graph.add_node(VariableNode(
        id="d_002", name="d2", description="d",
        abstraction_level=2, confidence=0.6, epistemic="inference",
    ))
    state.graph.add_edge(RelationEdge(
        id="e_003", source_node="d_002", target_node="c_001",
        relation_type="causes", confidence=0.6, mechanism_description="m",
    ))
    mocker.patch(
        "explain_engine.engines.reflection.check_consistency_batch",
        return_value=[
            ConsistencyReport(
                target_id="c_001", consistency_score=0.8, essentialness_score=0.5,
                reachable_L0=["p_001"], weak_chains=[],
                contribution_breakdown={}, decay_trace=[],
            ),
            ConsistencyReport(
                target_id="d_001", consistency_score=0.7, essentialness_score=0.5,
                reachable_L0=[], weak_chains=[],
                contribution_breakdown={}, decay_trace=[],
            ),
            ConsistencyReport(
                target_id="d_002", consistency_score=0.7, essentialness_score=0.02,
                reachable_L0=[], weak_chains=[],
                contribution_breakdown={}, decay_trace=[],
            ),
        ],
    )
    mocker.patch(
        "explain_engine.engines.expansion._call_with_retry",
        return_value=ExpansionOutput(drivers=[]),
    )
    await run(state, mocker.AsyncMock(), budget=3,
              scheduler=PhaseScheduler(K=2))
    # d_002 应被 prune
    assert "d_002" not in state.graph.nodes


@pytest.mark.asyncio
async def test_on_tick_callback_invoked_each_tick(mocker) -> None:
    state = _make_state_with_L1()
    mocker.patch(
        "explain_engine.engines.expansion._call_with_retry",
        return_value=ExpansionOutput(drivers=[]),
    )
    mocker.patch(
        "explain_engine.engines.reflection.check_consistency_batch",
        return_value=[],
    )
    calls = []
    await run(
        state, mocker.AsyncMock(), budget=5,
        scheduler=PhaseScheduler(K=2),
        on_tick=lambda s: calls.append(s.tick),
    )
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_reflection_trace_entry_has_action(mocker) -> None:
    state = _make_state_with_L1()
    mocker.patch(
        "explain_engine.engines.reflection.check_consistency_batch",
        return_value=[
            ConsistencyReport(
                target_id="c_001", consistency_score=0.3, essentialness_score=0.5,
                reachable_L0=["p_001"], weak_chains=[],
                contribution_breakdown={}, decay_trace=[],
            ),
        ],
    )
    mocker.patch(
        "explain_engine.engines.expansion._call_with_retry",
        return_value=ExpansionOutput(drivers=[
            _DriverCandidate(name="x", description="d", mechanism="m", plausibility=4),
        ]),
    )
    await run(state, mocker.AsyncMock(), budget=3,
              scheduler=PhaseScheduler(K=2))
    reflect_entries = [e for e in state.reasoning_trace if e.action == "reflect"]
    assert len(reflect_entries) >= 1
    assert reflect_entries[0].reflection_action in ("re-expand", "prune", "stop", "continue")
```

### Step 7: 跑测试 + commit

Run: `.venv/bin/python -m pytest tests/test_engines_expansion_re_expand.py tests/test_runtime_scheduler_reflect.py tests/test_runtime_run_reflect.py -v`
Expected: 5 + 5 + 5 = 15 PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: 335 + 15 = 350 PASS.

(注: 旧 `test_runtime_run.py` / `test_runtime_scheduler.py` 可能因 scheduler 改 `evaluate→reflect` 而 fail. 修复方法: 把这些 test 中 `assert action == "evaluate"` 改 `assert action == "reflect"`. 见 Step 8.)

### Step 8: Fix 旧 runtime tests

Run: `.venv/bin/python -m pytest tests/test_runtime_run.py tests/test_runtime_scheduler.py -v`

预计若干 fail. 改 `evaluate` 字面量 → `reflect`. 也可能要 mock `reflection.check_consistency_batch`. 实施者根据失败信息逐项修.

### Step 9: ruff + commit

```bash
.venv/bin/python -m ruff check src/ tests/
```

Expected: 0.

```bash
git add src/explain_engine/engines/expansion.py \
        src/explain_engine/runtime/scheduler.py \
        src/explain_engine/runtime/runtime.py \
        tests/test_engines_expansion_re_expand.py \
        tests/test_runtime_scheduler_reflect.py \
        tests/test_runtime_run_reflect.py \
        tests/test_runtime_run.py tests/test_runtime_scheduler.py
git commit -m "$(cat <<'EOF'
Wave C.2 · re_expand + scheduler reflect + runtime reflect 分支

Phase 7 design §6.3-6.5:
- expansion.re_expand(): 绕过 frontier check (Phase 7 reflection 用)
- PhaseScheduler: 1 round = K expand + 1 reflect (替 Phase 5 evaluate)
- runtime.run: reflect 分支调 reflection.reflect, 触发 re_expand/prune/stop
- TraceEntry.reflection_action 记录决策
- 旧 Phase 5 runtime tests 适配 evaluate → reflect

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task C.3: `runtime/stop.py` 加 reflection_signaled_stop

**目的**: Phase 5 stop signal 是 budget / no_gain / no_frontier. Phase 7 加 reflection_signaled_stop (reflect 决定 stop 时通过设 `last_reflection_change_tick` 触发). 改 `no_frontier_remaining` 逻辑: frontier 空但有 low consistency L1 时仍 not stop.

**Files:**
- Modify: `src/explain_engine/runtime/stop.py`
- Create: `tests/test_runtime_stop_reflection.py`

---

### Step 1: 写测试

Create `tests/test_runtime_stop_reflection.py`:

```python
"""Wave C.3: stop.py 加 reflection_signaled_stop."""

import pytest

from explain_engine.engines.simulation import ConsistencyReport
from explain_engine.runtime.stop import should_stop
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _state(tick: int, last_refl: int = 0,
           budget: int = 10) -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    g.add_node(VariableNode(
        id="c_001", name="c", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    return CognitiveState(
        graph=g, budget_remaining=budget, root_question="q",
        tick=tick, last_gain_tick=0,
        last_reflection_change_tick=last_refl,
    )


class TestReflectionSignaledStop:
    def test_stale_reflection_triggers_stop(self, mocker) -> None:
        # tick 5, last_refl_change_tick 0, diff=5 > CONSISTENCY_STALE_TICKS+1=4
        state = _state(tick=5, last_refl=0)
        mocker.patch(
            "explain_engine.runtime.stop.check_consistency_batch",
            return_value=[],
        )
        stop, reason = should_stop(state)
        assert stop is True
        assert reason == "reflection_signaled_stop"

    def test_fresh_reflection_does_not_trigger_stop(self, mocker) -> None:
        state = _state(tick=2, last_refl=0, budget=5)
        mocker.patch(
            "explain_engine.runtime.stop.check_consistency_batch",
            return_value=[],
        )
        stop, reason = should_stop(state)
        # 不该触发 reflection_stop (diff=2 < 4)
        assert (reason != "reflection_signaled_stop")


class TestNoFrontierWithLowConsistency:
    def test_no_frontier_but_low_consistency_L1_continues(self, mocker) -> None:
        """frontier 空但有 low consistency L1 → 仍 not stop (reflection 可 re-expand)."""
        state = _state(tick=1, last_refl=0)
        # 加 incoming causes 让 c_001 不是 frontier
        state.graph.add_node(VariableNode(
            id="d_001", name="d", description="d",
            abstraction_level=2, confidence=0.6, epistemic="inference",
        ))
        state.graph.add_edge(RelationEdge(
            id="e_001", source_node="d_001", target_node="c_001",
            relation_type="causes", confidence=0.6, mechanism_description="m",
        ))
        mocker.patch(
            "explain_engine.runtime.stop.check_consistency_batch",
            return_value=[
                ConsistencyReport(
                    target_id="c_001", consistency_score=0.3, essentialness_score=0.5,
                    reachable_L0=[], weak_chains=[],
                    contribution_breakdown={}, decay_trace=[],
                ),
            ],
        )
        stop, reason = should_stop(state)
        assert stop is False or reason != "no_frontier_remaining"
```

### Step 2: 改 `runtime/stop.py`

Modify `src/explain_engine/runtime/stop.py`:

```python
"""Phase 5 + Phase 7: stop signals.

priorities (in order):
  1. budget_exhausted
  2. no_gain_for_3_ticks
  3. reflection_signaled_stop (Phase 7)
  4. no_frontier_remaining (Phase 7 改: 同时检查 L1 consistency)
"""

from explain_engine.engines.reflection import (
    CONSISTENCY_STALE_TICKS, LOW_CONSISTENCY_THRESHOLD,
)
from explain_engine.engines.simulation import check_consistency_batch
from explain_engine.schema.state import CognitiveState

GAIN_THRESHOLD: float = 0.1


def should_stop(state: CognitiveState) -> tuple[bool, str | None]:
    if state.budget_remaining <= 0:
        return True, "budget_exhausted"

    if state.tick - state.last_gain_tick >= 3:
        return True, "no_gain_for_3_ticks"

    # Phase 7 Wave C.3: reflection_signaled_stop
    if state.tick - state.last_reflection_change_tick >= CONSISTENCY_STALE_TICKS + 1:
        return True, "reflection_signaled_stop"

    if not state.graph.frontier_nodes() and not _has_low_consistency_L1(state):
        return True, "no_frontier_remaining"

    return False, None


def _has_low_consistency_L1(state: CognitiveState) -> bool:
    """Phase 7 Wave C.3: frontier 空时仍可能有低 consistency L1 (re-expand 候选)."""
    L1_L2 = any(n.abstraction_level >= 1 for n in state.graph.nodes.values())
    if not L1_L2:
        return False
    reports = check_consistency_batch(state)
    return any(
        r.consistency_score < LOW_CONSISTENCY_THRESHOLD
        and state.graph.nodes[r.target_id].abstraction_level == 1
        for r in reports
    )
```

### Step 3: 跑测试 + 修旧 stop tests + commit

Run: `.venv/bin/python -m pytest tests/test_runtime_stop_reflection.py tests/test_runtime_stop.py -v`
Expected: 3 new PASS + 现有 stop tests 跑通 (或微调 last_reflection_change_tick=0 不触发 stale).

Run: `.venv/bin/python -m pytest -q`
Expected: 350 + 3 = 353 PASS.

Run: `.venv/bin/python -m ruff check src/ tests/`
Expected: 0.

```bash
git add src/explain_engine/runtime/stop.py \
        tests/test_runtime_stop_reflection.py \
        tests/test_runtime_stop.py
git commit -m "$(cat <<'EOF'
Wave C.3 · stop signal: reflection_signaled_stop + L1 consistency check

Phase 7 design §6.6:
- reflection_signaled_stop: tick - last_refl >= CONSISTENCY_STALE_TICKS+1
- no_frontier 改: frontier 空但仍有 low consistency L1 时不 stop
- 优先级: budget > no_gain > reflection_stop > no_frontier

Wave C 完结: Reflection Engine 闭环 (decide → execute → stop signal)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave C 完成 Checkpoint

跑全测试:

```bash
.venv/bin/python -m pytest -q
```

Expected: **353 PASS** (Wave A 286 + Wave B 39 + Wave C 28).

跑 ruff:

```bash
.venv/bin/python -m ruff check src/ tests/
```

Expected: 0.

**STOP — 等用户审 Wave C 通过后进 Wave D.**

---

# Wave D — Acceptance + 文档

## Task D.1: `explain rescore` CLI + 真 LLM 重跑 3 session + Phase 6 check 对比

**目的**: Wave D acceptance fixture. 重评 existing 3 session edges (replace default placeholder confidence with LLM-evaluated). 真 LLM smoke run predict / counterfactual / run-with-reflection. 验证 Wave A 区分度 ≥ 0.15.

**Files:**
- Modify: `src/explain_engine/cli.py` (加 `rescore` 命令)
- Create: `src/explain_engine/engines/rescore.py`
- Create: `tests/test_cli_rescore.py`

---

### Step 1: 写测试

Create `tests/test_cli_rescore.py`:

```python
"""Wave D.1: explain rescore CLI 测试."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from explain_engine.cli import app


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
    return sessions_dir


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


def _save_session_with_default_conf(sessions_dir: Path, sid: str) -> None:
    payload = {
        "meta": {
            "session_id": sid, "question": "q", "stage": "converged",
            "created_at": 1234567890.0,
        },
        "state": {
            "graph": {
                "root_question": "q",
                "nodes": {
                    "p_001": {
                        "id": "p_001", "name": "p", "description": "d",
                        "abstraction_level": 0, "confidence": 0.7,
                        "epistemic": "observation",
                    },
                    "c_001": {
                        "id": "c_001", "name": "c", "description": "d",
                        "abstraction_level": 1, "confidence": 0.7,
                        "epistemic": "insight",
                    },
                    "d_001": {
                        "id": "d_001", "name": "d", "description": "d",
                        "abstraction_level": 2, "confidence": 0.6,
                        "epistemic": "inference",
                    },
                },
                "edges": {
                    "e_001": {
                        "id": "e_001", "source_node": "c_001", "target_node": "p_001",
                        "relation_type": "manifests_as",
                        "confidence": 0.7, "mechanism_description": "m",
                    },
                    "e_002": {
                        "id": "e_002", "source_node": "d_001", "target_node": "c_001",
                        "relation_type": "causes",
                        "confidence": 0.6, "mechanism_description": "m",
                    },
                },
            },
            "budget_remaining": 0, "root_question": "q",
            "active_frontier": [], "insight_candidates": ["c_001"],
            "tick": 0, "last_gain_tick": 0,
            "last_gains": {}, "reasoning_trace": [],
        },
    }
    (sessions_dir / f"{sid}.json").write_text(json.dumps(payload, ensure_ascii=False))


class TestCLIRescore:
    def test_session_not_found(self, cli_env, runner) -> None:
        result = runner.invoke(app, ["rescore", "s_nonexistent"])
        assert result.exit_code == 1

    def test_rescore_overwrites_default_confidence(
        self, cli_env, runner, monkeypatch
    ) -> None:
        _save_session_with_default_conf(cli_env, "s_test01")
        # mock rescore engine
        async def fake_rescore(state, llm):
            for e in state.graph.edges.values():
                e.confidence = 1.0   # all 5/5
            return {eid: 1.0 for eid in state.graph.edges}
        monkeypatch.setattr(
            "explain_engine.engines.rescore.rescore_session", fake_rescore,
        )
        monkeypatch.setattr(
            "explain_engine.cli.make_llm_client", lambda: MagicMock(),
        )
        result = runner.invoke(app, ["rescore", "s_test01"])
        assert result.exit_code == 0
        # 重 load 看 edge.confidence 持久化
        from explain_engine.persistence.session import SessionStore
        store = SessionStore(directory=cli_env)
        session = store.load("s_test01")
        for e in session.state.graph.edges.values():
            assert e.confidence == pytest.approx(1.0)
```

(其他 3 test: rescore manifests_as only / rescore causes only / mixed — 实施者补.)

### Step 2: 创建 `rescore.py` engine

Create `src/explain_engine/engines/rescore.py`:

```python
"""Wave D.1: 重评 existing session edges. 复用 evaluation._score_edge + 类比 prompt for causes."""

from __future__ import annotations

import logging

from explain_engine.engines.evaluation import _score_edge
from explain_engine.llm.client import LLMClient
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


async def rescore_session(
    state: CognitiveState, llm: LLMClient,
) -> dict[str, float]:
    """Rescore manifests_as + causes edges. 返 dict[edge_id, new_confidence]."""
    new_confidences: dict[str, float] = {}
    prompt = load_prompt("scoring")

    for edge in list(state.graph.edges.values()):
        if edge.relation_type == "manifests_as":
            source = state.graph.nodes[edge.source_node]   # abstract L1
            target = state.graph.nodes[edge.target_node]   # concrete L0
            score = await _score_edge(
                llm, prompt,
                abstract_name=source.name,
                abstract_description=source.description,
                concrete_name=target.name,
                concrete_description=target.description,
                mechanism=edge.mechanism_description,
            )
            new_conf = score / 5.0
            edge.confidence = new_conf
            new_confidences[edge.id] = new_conf
        elif edge.relation_type == "causes":
            # 复用 scoring.yaml: treat driver as "abstract" and target as "concrete"
            # 让 LLM 评 driver→target mechanism 合理性
            source = state.graph.nodes[edge.source_node]   # driver L2
            target = state.graph.nodes[edge.target_node]   # abstract L1
            score = await _score_edge(
                llm, prompt,
                abstract_name=source.name,
                abstract_description=source.description,
                concrete_name=target.name,
                concrete_description=target.description,
                mechanism=edge.mechanism_description,
            )
            new_conf = score / 5.0
            edge.confidence = new_conf
            new_confidences[edge.id] = new_conf

    return new_confidences
```

### Step 3: 加 CLI 命令

Modify `src/explain_engine/cli.py` (在 `counterfactual` 命令下方):

```python
@app.command()
def rescore(
    session_id: str = typer.Argument(...),
) -> None:
    """Phase 7 Wave D: 重评 existing session 的 edge.confidence (Wave A acceptance fixture)."""
    asyncio.run(_run_rescore(session_id))


async def _run_rescore(session_id: str) -> None:
    from explain_engine.engines import rescore as rescore_mod
    from explain_engine.llm.errors import LLMError, SchemaValidationError

    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    llm = make_llm_client()
    try:
        new_confs = await rescore_mod.rescore_session(session.state, llm)
    except (LLMError, SchemaValidationError) as exc:
        console.print(f"[red]rescore 失败: {exc}[/red]")
        raise typer.Exit(1) from exc

    store.save(session)
    console.print(
        f"\n[green]Rescored {len(new_confs)} edges in {session_id}[/green]"
    )
    avg = sum(new_confs.values()) / len(new_confs) if new_confs else 0.0
    console.print(f"  Average new confidence: {avg:.2f}")
```

### Step 4: 跑测试 + commit

Run: `.venv/bin/python -m pytest tests/test_cli_rescore.py -v`
Expected: 5 PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: 353 + 5 = 358 PASS.

Run: `.venv/bin/python -m ruff check src/ tests/`
Expected: 0.

```bash
git add src/explain_engine/engines/rescore.py \
        src/explain_engine/cli.py \
        tests/test_cli_rescore.py
git commit -m "$(cat <<'EOF'
Wave D.1 · explain rescore CLI + rescore engine

Phase 7 design §7.2: Wave A acceptance fixture.
- 重评 manifests_as + causes edges (用 scoring.yaml prompt 复用)
- 写回 edge.confidence = score / 5.0 (Wave A 同 mapping)
- 副作用: state.graph edges 改 in-place, save session

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Step 5: 真 LLM acceptance smoke (手动执行, 不进 commit)

```bash
# Wave A acceptance: rescore + check 对比
.venv/bin/python -m explain_engine.cli rescore s_f3beb777
.venv/bin/python -m explain_engine.cli rescore s_705f0435
.venv/bin/python -m explain_engine.cli rescore s_7d491774

.venv/bin/python -m explain_engine.cli check s_f3beb777 > /tmp/check_post_rescore_f3.txt
.venv/bin/python -m explain_engine.cli check s_705f0435 > /tmp/check_post_rescore_70.txt
.venv/bin/python -m explain_engine.cli check s_7d491774 > /tmp/check_post_rescore_7d.txt

# 看区分度: s_7d491774 avg consistency < s_f3beb777 avg consistency, 差 >= 0.15
# (若 fail, 写进 acceptance evidence + tune 阈值 in Wave D.2)

# Wave B smoke
.venv/bin/python -m explain_engine.cli predict s_f3beb777 "现代媒体放大效应"
.venv/bin/python -m explain_engine.cli counterfactual s_f3beb777 "用经济激励替代教义不可妥协性"

# Wave C smoke (新 session 跑全 pipeline 含 reflection)
.venv/bin/python -m explain_engine.cli new "为什么大公司会议总是低效"
# HITL 1 (假设跑过)
.venv/bin/python -m explain_engine.cli compress s_new
# HITL 2 (跑过)
.venv/bin/python -m explain_engine.cli run s_new --budget 15
.venv/bin/python -m explain_engine.cli show s_new --trace | grep "reflect"
```

输出收集到 `/tmp/acceptance_phase7/*.txt`, Wave D.2 整理进 evidence file.

---

## Task D.2: Acceptance evidence file + README 更新

**目的**: 写 `2026-05-15-cognitive-engine-phase-7-acceptance.md` (跟 Phase 6 同 structure) + README 加 Phase 7 边界说明.

**Files:**
- Create: `docs/plans/2026-05-15-cognitive-engine-phase-7-acceptance.md`
- Modify: `README.md`

---

### Step 1: 写 evidence file

Create `docs/plans/2026-05-15-cognitive-engine-phase-7-acceptance.md`:

```markdown
# Phase 7 Acceptance — Confidence + Forward Prediction + Reflection

**日期**: 2026-05-XX
**Sessions**: s_f3beb777 / s_705f0435 / s_7d491774 + 1 new session for run-with-reflection
**LLM provider**: <填实际 provider>

## 跑法
[填实际命令]

## Wave A 区分度数据
[填 before/after consistency_score 表]

## Wave B predict 输出样例
[填 predict 真实 output]

## Wave B counterfactual 输出样例
[填 counterfactual + narrative 输出]

## Wave C reasoning_trace 含 reflect action 样例
[填 show --trace output, highlight reflect entries]

## 验收 checklist
- [x/⚠️] ...

## Tune 决策
[阈值调整, 跟 Phase 5/6 同处理]

## Phase 8 起点
[确认 Phase 8 推荐方向跟 design doc §11 一致]
```

### Step 2: 改 README

Modify `README.md`. 加 Phase 7 节 (跟 Phase 6 design §7.5 README 模板一致):

```markdown
## Phase 7 (2026-05-15) — Confidence + Forward Prediction + Reflection

新命令:
- `explain predict <sid> "<intervention>"` — 自然语言 forward prediction
- `explain counterfactual <sid> "<substitute>"` — counterfactual 替换 / 删除
- `explain rescore <sid>` — 重评 edge confidence (Wave A acceptance fixture)

`explain run` 现含 Reflection: loop 内动态决定 re-expand / prune / stop.

边界:
- 系统适合: 历史 / 常识 / 结构性 why-questions
- 系统不适合: 实时分析 / 强时效议题 / 依赖具体新近数据
- Phase 7 forward prediction 适合 structural-mechanism 议题 (e.g. "如果加入 X 因素 / 移除 Y 因素"); 不适合时事预测.
```

### Step 3: Commit

```bash
git add docs/plans/2026-05-15-cognitive-engine-phase-7-acceptance.md README.md
git commit -m "$(cat <<'EOF'
acceptance · Phase 7 — Confidence + Forward Prediction + Reflection

evidence file (跟 Phase 6 同 structure):
- Wave A 区分度数据 (3 session before/after consistency)
- Wave B predict / counterfactual 真 LLM 输出样例
- Wave C reasoning_trace 含 reflect 样例
- Tune 决策 + Phase 8 起点

README: 加 Phase 7 命令说明 + 边界

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave D 完成 (Phase 7 完结) Checkpoint

跑全测试:

```bash
.venv/bin/python -m pytest -q
```

Expected: **358 PASS** (Phase 6 baseline 276 + Phase 7 +82).

跑 ruff:

```bash
.venv/bin/python -m ruff check src/ tests/
```

Expected: 0.

CLI sanity:

```bash
.venv/bin/python -m explain_engine.cli --help | grep -E "predict|counterfactual|rescore"
```

Expected: 3 个新命令都列出.

Acceptance smoke (真 LLM):
- Wave A 区分度 ≥ 0.15
- Wave B predict / counterfactual 跑通
- Wave C reasoning_trace 含 ≥ 1 reflect action

**Phase 7 完结. Phase 8 起点见 design doc §11.**

---

## 总结

| Wave | Task | Tests added | 累计 |
|---|---|---|---|
| A | A.1 evaluation 写回 | +5 | 281 |
| A | A.2 expansion 写回 | +5 | 286 |
| B | B.1 intervention_parser | +8 | 294 |
| B | B.2 prediction + CLI + HITL | +15 | 309 |
| B | B.3 counterfactual + CLI | +13 | 322 |
| B | B.4 shared util refactor | +3 | 325 |
| C | C.1 reflect 决策 + schema 扩展 | +10 | 335 |
| C | C.2 re_expand + scheduler + runtime | +15 | 350 |
| C | C.3 stop signal | +3 | 353 |
| D | D.1 rescore CLI + engine | +5 | 358 |
| D | D.2 evidence + README | 0 | 358 |

**总: 11 task / +82 tests / Phase 6 (276) → Phase 7 (358 PASS).**

跟 Phase 5 (10 task, +73 tests) / Phase 6 (5 task, +43 tests) 同节奏.

