# Cognitive Engine Phase 5 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Phase 5 design 实施落地 —— ExpansionEngine (上溯 driver) + PhaseScheduler + Runtime Loop + `explain run` CLI + Wave A 地基 (Provider 抽象重构 + last_gains 持久化)。让系统从 "single-shot compress" 进化到 "持续 thinking"。

**Architecture:** 10 个 task，TDD 流水线。Wave 顺序：A 地基 (schema 字段 / last_gains / frontier_nodes / Provider 重构) → B Expansion (prompt / engine) → C Loop (Scheduler / Stop / Runtime) → D CLI + acceptance。

**Tech Stack:** Python 3.11+ / pydantic / dataclasses / typer / rich / pyyaml / tenacity / pytest / anthropic SDK / openai SDK。Phase 0-4 完全复用。

**Branch:** `dev` (latest: `86bdc01` Phase 5 design)

**Design Doc:** [2026-05-13-cognitive-engine-phase-5-design.md](2026-05-13-cognitive-engine-phase-5-design.md)

**Phase 0-4 现状:** 159 tests pass, ruff 0 errors。`s_f3beb777` (12 现象 + 5 candidate / "为什么宗教战争是最血腥的战争") Phase 4 done，作 Phase 5 acceptance session。

---

## 与 Design Doc 的偏差说明

实施前发现 design 与现有代码有几处需要 reconcile，在此 explicit 列出：

1. **`CognitiveState` 是 `dataclass` 不是 pydantic BaseModel**（`schema/state.py:11`）。`tick / budget_remaining / last_gain_tick / active_frontier / insight_candidates` Phase 0 起就存在。Phase 5 加的字段照 dataclass 风格写。

2. **`graph.frontier()` 已存在但语义不同**（`schema/graph.py:105`）：现行返 `level≥1 且 out_degree==0`。Phase 5 需要 `level==1 且 没有 incoming causes edge`，**新加 `frontier_nodes()` 方法**，旧 `frontier()` 不动（保留向后兼容）。

3. **`AbstractionLevel = Literal[0, 1, 2]`**（`schema/nodes.py:18`）已支持 0/1/2。Phase 5 driver 用 level=2，但 frontier_nodes() 只返 level==1 的节点 —— 把 graph cap 在 3 层（concrete L0 / abstract L1 / driver L2），避免 d_NNN 自身又被 expand 出 super-driver（schema Literal 不支持 level=3）。Phase 6 扩 Literal 时再放开。

4. **DeepSeek 不支持 json_schema strict 模式**（旧 `deepseek.py` 用 json_object + prompt 注入 schema 描述）。Phase 5 删 3 个 client 改 2 个时，需要在 `openai_protocol.py` 同时支持两种 structured output 模式，通过 `LLM_STRUCTURED_OUTPUT_MODE=json_schema|json_object` env var 显式控制（默认 `json_schema`，DeepSeek 用户配 `json_object`）。

5. **现行 LLM 文件名 `openai_client.py` 不是 `openai.py`**（避开 SDK 重名）。Phase 5 新文件用 `openai_protocol.py` / `anthropic_protocol.py`。

6. **`TraceEntry` 用 dataclass** 跟 `CognitiveState` 风格一致（state.py 内嵌）。

7. **每 tick 落盘**（design §5.4）通过把 `SessionStore` 注入 `Runtime.run` 实现。如果嫌 Runtime 跟 persistence 耦合，可用 callback 参数（`on_tick: Callable[[Session], None]`）。本 plan 选 callback 方案 —— Runtime 只管 loop 逻辑，落盘由 CLI 层注入。

---

## 任务索引

- **Wave A (地基)**:
  - Task 5.1 Schema: Stage "converged" + CognitiveState `last_gains` + `reasoning_trace` + TraceEntry
  - Task 5.2 last_gains 持久化（EvaluationEngine + HITL 2 复用）
  - Task 5.3 ExplanationGraph.frontier_nodes() helper
  - Task 5.4 Provider 抽象重构（3 client → 2 client + factory + .env + README）
- **Wave B (Expansion)**:
  - Task 5.5 Prompts: expansion.yaml + loader test
  - Task 5.6 ExpansionEngine.expand_one_frontier
- **Wave C (Loop)**:
  - Task 5.7 Runtime: PhaseScheduler + should_stop
  - Task 5.8 Runtime: Runtime.run 主循环
- **Wave D (CLI + Acceptance)**:
  - Task 5.9 CLI: `explain run` + `explain show --trace`
  - Task 5.10 Acceptance smoke on s_f3beb777（手动 + final report）

---

# Task 5.1: Schema — Stage "converged" + CognitiveState 新字段 + TraceEntry

**目的**:
1. `Stage` Literal 加 `"converged"` 终态
2. `CognitiveState` 加 `last_gains: dict[str, float]` + `reasoning_trace: list[TraceEntry]`
3. 新建 `TraceEntry` dataclass
4. 更新 `to_dict` / `from_dict` 支持新字段

**Files:**
- Modify: `src/explain_engine/persistence/session.py:21-28` (Stage Literal + _VALID_STAGES)
- Modify: `src/explain_engine/schema/state.py` (CognitiveState 加字段 + TraceEntry + to_dict/from_dict)
- Create: `tests/test_schema_state_stage_converged.py`
- Create: `tests/test_schema_trace_entry.py`
- Create: `tests/test_schema_state_phase5_fields.py`

---

## Step 1: 写 Stage "converged" 失败测试

Create `tests/test_schema_state_stage_converged.py`:

```python
"""Phase 5 Stage Literal 加 'converged' 终态。"""

import pytest

from explain_engine.persistence.session import SessionMeta


class TestStageConverged:
    def test_converged_valid(self) -> None:
        meta = SessionMeta(
            session_id="s_abcd1234",
            question="why",
            stage="converged",
            created_at=1.0,
            updated_at=1.0,
        )
        assert meta.stage == "converged"

    def test_phase4_done_still_valid(self) -> None:
        """旧 'done' 状态依然有效（Phase 4 session 反序列化兼容）。"""
        meta = SessionMeta(
            session_id="s_abcd1234",
            question="why",
            stage="done",
            created_at=1.0,
            updated_at=1.0,
        )
        assert meta.stage == "done"

    def test_invalid_stage_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid stage"):
            SessionMeta(
                session_id="s_abcd1234",
                question="why",
                stage="running",
                created_at=1.0,
                updated_at=1.0,
            )
```

## Step 2: 跑测试验证失败

Run: `pytest tests/test_schema_state_stage_converged.py -v`
Expected: `test_converged_valid` FAIL (`ValueError: invalid stage: 'converged'`); 另两个 PASS。

## Step 3: 改 Stage Literal

Modify `src/explain_engine/persistence/session.py:21-28`:

```python
Stage = Literal[
    "bootstrap_pending",   # 等 HITL 1
    "insight_pending",     # Compression + Evaluation 完成，等 HITL 2
    "done",                # HITL 2 完成 (Phase 4 终态 → Phase 5 入口)
    "converged",           # Phase 5 reasoning loop 完成 (终态)
]

_SESSION_ID_RE = re.compile(r"^s_[0-9a-f]{8}$")
_VALID_STAGES = frozenset({"bootstrap_pending", "insight_pending", "done", "converged"})
```

## Step 4: 跑测试验证通过

Run: `pytest tests/test_schema_state_stage_converged.py -v`
Expected: 3 个 PASS。

## Step 5: 写 TraceEntry 失败测试

Create `tests/test_schema_trace_entry.py`:

```python
"""TraceEntry — Phase 5 reasoning_trace 单条记录。"""

import pytest

from explain_engine.schema.state import TraceEntry


class TestTraceEntry:
    def test_construct_minimal(self) -> None:
        entry = TraceEntry(
            tick=0,
            action="expand",
            target_node_id="c_001",
            gain_delta=0.42,
            llm_calls=1,
            timestamp="2026-05-13T10:00:00",
        )
        assert entry.tick == 0
        assert entry.action == "expand"
        assert entry.target_node_id == "c_001"
        assert entry.gain_delta == 0.42

    def test_target_node_id_optional(self) -> None:
        entry = TraceEntry(
            tick=4,
            action="evaluate",
            target_node_id=None,
            gain_delta=0.0,
            llm_calls=0,
            timestamp="2026-05-13T10:00:01",
        )
        assert entry.target_node_id is None

    def test_action_literal_validated(self) -> None:
        """非 expand/compress/evaluate 直接构造能过（dataclass 不强校验 Literal），
        但 mypy / runtime 用 _VALID_ACTIONS 兜底。"""
        # dataclass 不会拒，但 __post_init__ 校验
        with pytest.raises(ValueError, match="invalid action"):
            TraceEntry(
                tick=0,
                action="bogus",  # type: ignore[arg-type]
                target_node_id=None,
                gain_delta=0.0,
                llm_calls=0,
                timestamp="2026-05-13T10:00:00",
            )

    def test_negative_tick_rejected(self) -> None:
        with pytest.raises(ValueError, match="tick must be >= 0"):
            TraceEntry(
                tick=-1,
                action="expand",
                target_node_id="c_001",
                gain_delta=0.0,
                llm_calls=0,
                timestamp="2026-05-13T10:00:00",
            )

    def test_to_dict_from_dict_roundtrip(self) -> None:
        entry = TraceEntry(
            tick=3,
            action="expand",
            target_node_id="c_002",
            gain_delta=0.6,
            llm_calls=1,
            timestamp="2026-05-13T11:00:00",
        )
        d = entry.to_dict()
        recovered = TraceEntry.from_dict(d)
        assert recovered == entry
```

## Step 6: 跑测试验证失败

Run: `pytest tests/test_schema_trace_entry.py -v`
Expected: `ImportError: cannot import name 'TraceEntry' from 'explain_engine.schema.state'` —— 5 个全部 collect failure。

## Step 7: 加 TraceEntry dataclass

Modify `src/explain_engine/schema/state.py` —— 在 import 后、`@dataclass class CognitiveState` 前插入：

```python
from typing import Literal

Action = Literal["expand", "compress", "evaluate"]
_VALID_ACTIONS = frozenset({"expand", "compress", "evaluate"})


@dataclass
class TraceEntry:
    """Phase 5 reasoning_trace 单条记录。"""

    tick: int
    action: Action
    target_node_id: str | None
    gain_delta: float
    llm_calls: int
    timestamp: str   # iso8601

    def __post_init__(self) -> None:
        if self.action not in _VALID_ACTIONS:
            raise ValueError(
                f"invalid action: {self.action!r}, must be one of {sorted(_VALID_ACTIONS)}"
            )
        if self.tick < 0:
            raise ValueError(f"tick must be >= 0, got {self.tick}")
        if self.llm_calls < 0:
            raise ValueError(f"llm_calls must be >= 0, got {self.llm_calls}")

    def to_dict(self) -> dict:
        return {
            "tick": self.tick,
            "action": self.action,
            "target_node_id": self.target_node_id,
            "gain_delta": self.gain_delta,
            "llm_calls": self.llm_calls,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TraceEntry":
        return cls(
            tick=d["tick"],
            action=d["action"],
            target_node_id=d.get("target_node_id"),
            gain_delta=d["gain_delta"],
            llm_calls=d["llm_calls"],
            timestamp=d["timestamp"],
        )
```

## Step 8: 跑 TraceEntry 测试验证通过

Run: `pytest tests/test_schema_trace_entry.py -v`
Expected: 5 个 PASS。

## Step 9: 写 CognitiveState 新字段失败测试

Create `tests/test_schema_state_phase5_fields.py`:

```python
"""CognitiveState Phase 5 新字段: last_gains + reasoning_trace。"""

from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState, TraceEntry


class TestCognitiveStatePhase5Fields:
    def test_last_gains_defaults_empty(self) -> None:
        state = CognitiveState.bootstrap("why", budget=10)
        assert state.last_gains == {}

    def test_reasoning_trace_defaults_empty(self) -> None:
        state = CognitiveState.bootstrap("why", budget=10)
        assert state.reasoning_trace == []

    def test_last_gains_assignable(self) -> None:
        state = CognitiveState.bootstrap("why", budget=10)
        state.last_gains = {"c_001": 0.42, "c_002": 0.55}
        assert state.last_gains["c_001"] == 0.42

    def test_reasoning_trace_appendable(self) -> None:
        state = CognitiveState.bootstrap("why", budget=10)
        entry = TraceEntry(
            tick=0, action="expand", target_node_id="c_001",
            gain_delta=0.5, llm_calls=1, timestamp="2026-05-13T10:00:00",
        )
        state.reasoning_trace.append(entry)
        assert len(state.reasoning_trace) == 1

    def test_to_dict_includes_new_fields(self) -> None:
        state = CognitiveState.bootstrap("why", budget=10)
        state.last_gains = {"c_001": 0.42}
        state.reasoning_trace.append(
            TraceEntry(
                tick=0, action="expand", target_node_id="c_001",
                gain_delta=0.5, llm_calls=1, timestamp="2026-05-13T10:00:00",
            )
        )
        d = state.to_dict()
        assert d["last_gains"] == {"c_001": 0.42}
        assert len(d["reasoning_trace"]) == 1
        assert d["reasoning_trace"][0]["tick"] == 0
        assert d["reasoning_trace"][0]["action"] == "expand"

    def test_from_dict_recovers_new_fields(self) -> None:
        d = {
            "graph": ExplanationGraph(root_question="why").to_dict(),
            "budget_remaining": 10,
            "root_question": "why",
            "active_frontier": [],
            "insight_candidates": [],
            "tick": 0,
            "last_gain_tick": 0,
            "last_gains": {"c_001": 0.42},
            "reasoning_trace": [
                {
                    "tick": 0,
                    "action": "expand",
                    "target_node_id": "c_001",
                    "gain_delta": 0.5,
                    "llm_calls": 1,
                    "timestamp": "2026-05-13T10:00:00",
                }
            ],
        }
        state = CognitiveState.from_dict(d)
        assert state.last_gains == {"c_001": 0.42}
        assert len(state.reasoning_trace) == 1
        assert state.reasoning_trace[0].action == "expand"

    def test_from_dict_phase4_compat(self) -> None:
        """旧 Phase 4 session 无新字段，默认空。"""
        d = {
            "graph": ExplanationGraph(root_question="why").to_dict(),
            "budget_remaining": 10,
            "root_question": "why",
        }
        state = CognitiveState.from_dict(d)
        assert state.last_gains == {}
        assert state.reasoning_trace == []
```

## Step 10: 跑测试验证失败

Run: `pytest tests/test_schema_state_phase5_fields.py -v`
Expected: 全部 FAIL（CognitiveState 无 `last_gains` / `reasoning_trace` 属性）。

## Step 11: 改 CognitiveState

Modify `src/explain_engine/schema/state.py` — 在 `class CognitiveState` 内加字段，并改 to_dict / from_dict:

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
    # Phase 5 NEW:
    last_gains: dict[str, float] = field(default_factory=dict)
    reasoning_trace: list[TraceEntry] = field(default_factory=list)

    # __post_init__ 不变，bootstrap 不变，advance_tick / record_gain 不变

    def to_dict(self) -> dict:
        return {
            "graph": self.graph.to_dict(),
            "budget_remaining": self.budget_remaining,
            "root_question": self.root_question,
            "active_frontier": list(self.active_frontier),
            "insight_candidates": list(self.insight_candidates),
            "tick": self.tick,
            "last_gain_tick": self.last_gain_tick,
            "last_gains": dict(self.last_gains),
            "reasoning_trace": [e.to_dict() for e in self.reasoning_trace],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CognitiveState":
        try:
            return cls(
                graph=ExplanationGraph.from_dict(d["graph"]),
                budget_remaining=d["budget_remaining"],
                root_question=d["root_question"],
                active_frontier=list(d.get("active_frontier", [])),
                insight_candidates=list(d.get("insight_candidates", [])),
                tick=d.get("tick", 0),
                last_gain_tick=d.get("last_gain_tick", 0),
                last_gains=dict(d.get("last_gains", {})),
                reasoning_trace=[
                    TraceEntry.from_dict(t) for t in d.get("reasoning_trace", [])
                ],
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid state dict: {exc}") from exc
```

## Step 12: 跑测试验证通过

Run: `pytest tests/test_schema_state_phase5_fields.py tests/test_schema_state_stage_converged.py tests/test_schema_trace_entry.py -v`
Expected: 14 个 PASS。

## Step 13: 跑全测试确认 Phase 0-4 不破

Run: `pytest tests/ -v --tb=short`
Expected: 159 + 14 = **173** 全 PASS。如果有 Phase 0-4 测试失败，diagnose 后修（大概率 from_dict 的兼容性 case，写法应该兼容）。

## Step 14: Commit

```bash
git add tests/test_schema_state_stage_converged.py tests/test_schema_trace_entry.py tests/test_schema_state_phase5_fields.py src/explain_engine/persistence/session.py src/explain_engine/schema/state.py
git commit -m "$(cat <<'EOF'
schema · Phase 5 字段 (Stage converged + last_gains + reasoning_trace + TraceEntry)

- Stage Literal 加 "converged" (Phase 5 终态)
- CognitiveState 加 last_gains / reasoning_trace 字段
- 新增 TraceEntry dataclass (tick / action / target_node_id /
  gain_delta / llm_calls / timestamp)
- to_dict / from_dict 兼容 Phase 4 旧 session JSON

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 5.2: last_gains 持久化（EvaluationEngine + HITL 2 复用）

**目的**: 修 Phase 4 design §11 risk #1。`EvaluationEngine.score_all` 末尾写 `state.last_gains`；`HITL 2 review_insights` 渲染 gain 列时读 `state.last_gains`（stage=insight_pending 重入不显 0.0）；drop candidate 时 pop 对应 entry。

**Files:**
- Modify: `src/explain_engine/engines/evaluation.py` (score_all 末尾写 last_gains)
- Modify: `src/explain_engine/hitl/cli_interactive.py` (review_insights 读 last_gains)
- Create: `tests/test_engines_evaluation_last_gains.py`
- Create: `tests/test_hitl_review_insights_last_gains.py`

---

## Step 1: 写 EvaluationEngine.score_all 末尾写 last_gains 失败测试

Create `tests/test_engines_evaluation_last_gains.py`:

```python
"""EvaluationEngine.score_all 末尾把 gain dict 持久化进 state.last_gains。"""

import pytest

from explain_engine.engines import evaluation
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState
from explain_engine.schema.graph import ExplanationGraph


class FakeLLM:
    """Mock client: 永远返 score=4。"""

    async def chat(self, messages, schema=None, model=None):
        from explain_engine.llm.client import Response
        return Response(
            text='{"score": 4}',
            parsed={"score": 4, "rationale": "ok"},
            model="fake",
            usage={"input_tokens": 0, "output_tokens": 0},
        )


def _make_state_with_one_candidate() -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(id="p_001", name="p1", description="d", abstraction_level=0,
                            confidence=0.8, epistemic="observation"))
    g.add_node(VariableNode(id="p_002", name="p2", description="d", abstraction_level=0,
                            confidence=0.8, epistemic="observation"))
    g.add_node(VariableNode(id="c_001", name="c1", description="d", abstraction_level=1,
                            confidence=0.7, epistemic="insight"))
    g.add_edge(RelationEdge(id="e_001", source_node="c_001", target_node="p_001",
                            relation_type="manifests_as", confidence=0.7,
                            mechanism_description="m1"))
    g.add_edge(RelationEdge(id="e_002", source_node="c_001", target_node="p_002",
                            relation_type="manifests_as", confidence=0.7,
                            mechanism_description="m2"))
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    state.insight_candidates = ["c_001"]
    return state


@pytest.mark.asyncio
async def test_score_all_writes_last_gains() -> None:
    state = _make_state_with_one_candidate()
    gains = await evaluation.score_all(state, FakeLLM())
    assert state.last_gains == gains
    assert "c_001" in state.last_gains
    # representation_reduction=1.0 (covers 2/2), explanatory_preservation=4/5=0.8
    assert abs(state.last_gains["c_001"] - 0.8) < 1e-9


@pytest.mark.asyncio
async def test_score_all_overwrites_old_last_gains() -> None:
    """重跑 score_all 应该覆盖旧 last_gains（不留陈数据）。"""
    state = _make_state_with_one_candidate()
    state.last_gains = {"c_999_stale": 0.5}   # 陈数据
    await evaluation.score_all(state, FakeLLM())
    assert "c_999_stale" not in state.last_gains
    assert "c_001" in state.last_gains
```

## Step 2: 跑测试验证失败

Run: `pytest tests/test_engines_evaluation_last_gains.py -v`
Expected: `test_score_all_writes_last_gains` FAIL (`state.last_gains` 是 `{}` 不是 `gains`)。

## Step 3: 改 EvaluationEngine.score_all

Modify `src/explain_engine/engines/evaluation.py` — 在 `score_all` 末尾 (return 前) 加：

```python
async def score_all(state: CognitiveState, llm: LLMClient) -> dict[str, float]:
    # ... 现有逻辑不变 ...

    # 降序重排
    state.insight_candidates = sorted(
        state.insight_candidates, key=lambda cid: gains[cid], reverse=True
    )
    # Phase 5: 持久化 last_gains（覆盖旧值）
    state.last_gains = dict(gains)
    return gains
```

## Step 4: 跑测试验证通过

Run: `pytest tests/test_engines_evaluation_last_gains.py -v`
Expected: 2 个 PASS。

## Step 5: 写 HITL 2 review_insights 读 last_gains 失败测试

Create `tests/test_hitl_review_insights_last_gains.py`:

```python
"""HITL 2 review_insights 渲染 gain 列时读 state.last_gains（不重算）。

stage=insight_pending 重入时复用持久化的 gain；drop candidate 时同步从 last_gains 移除。
"""

from io import StringIO

from rich.console import Console

from explain_engine.hitl.cli_interactive import review_insights
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _state_with_two_candidates() -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    for pid in ("p_001", "p_002"):
        g.add_node(VariableNode(id=pid, name=pid, description="d", abstraction_level=0,
                                confidence=0.8, epistemic="observation"))
    for cid in ("c_001", "c_002"):
        g.add_node(VariableNode(id=cid, name=cid, description="d", abstraction_level=1,
                                confidence=0.7, epistemic="insight"))
        g.add_edge(RelationEdge(id=f"e_{cid}", source_node=cid, target_node="p_001",
                                relation_type="manifests_as", confidence=0.7,
                                mechanism_description="m"))
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    state.insight_candidates = ["c_001", "c_002"]
    state.last_gains = {"c_001": 0.65, "c_002": 0.42}
    return state


def test_review_insights_table_uses_persisted_last_gains(monkeypatch) -> None:
    """HITL 2 渲染 gain 列读 state.last_gains，不是临时计算。"""
    state = _state_with_two_candidates()
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)

    # mock Prompt.ask 全 keep
    import explain_engine.hitl.cli_interactive as hitl_mod

    answers = iter(["k", "k"])
    monkeypatch.setattr(hitl_mod.Prompt, "ask", lambda *a, **kw: next(answers))

    review_insights(state, console)

    out = buf.getvalue()
    assert "0.65" in out
    assert "0.42" in out


def test_review_insights_drop_removes_from_last_gains(monkeypatch) -> None:
    state = _state_with_two_candidates()
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)

    import explain_engine.hitl.cli_interactive as hitl_mod
    answers = iter(["k", "d"])   # keep c_001, drop c_002
    monkeypatch.setattr(hitl_mod.Prompt, "ask", lambda *a, **kw: next(answers))

    review_insights(state, console)

    assert "c_002" not in state.last_gains
    assert "c_001" in state.last_gains
```

## Step 6: 跑测试验证失败

Run: `pytest tests/test_hitl_review_insights_last_gains.py -v`
Expected: 失败（review_insights 现在 gain 列读哪个数据要查代码；drop 不会动 last_gains）。先看现有 `review_insights` 实现来知道改哪。

## Step 7: 读 review_insights 现有实现

Run: `grep -n "review_insights" src/explain_engine/hitl/cli_interactive.py` 定位函数；Read 该函数（约 60-80 行），找：
- 渲染 gain 列那一行（多半是 `f"{gain:.2f}"` 或从某 dict 取）
- drop 分支（多半调 `state.graph.remove_node`）

观察后改两处：
1. 渲染 gain 列读 `state.last_gains[cid]`，不是局部 `gains_local[cid]`
2. drop 分支在 `state.graph.remove_node(cid)` 后加 `state.last_gains.pop(cid, None)`

## Step 8: 改 review_insights

Modify `src/explain_engine/hitl/cli_interactive.py` 内 `review_insights` 函数（具体行号执行时查）：

- 渲染表格 gain 列：把现有的 `gain` 取值改为 `state.last_gains.get(cid, 0.0)`
- drop 分支：在 `state.graph.remove_node(cid)` 后追加 `state.last_gains.pop(cid, None)`

## Step 9: 跑 HITL 测试验证通过

Run: `pytest tests/test_hitl_review_insights_last_gains.py tests/test_hitl_cli_interactive_insights.py -v`
Expected: 新测试 2 个 PASS；现有 HITL 2 测试不破。

## Step 10: 跑全测试

Run: `pytest tests/ -v --tb=short`
Expected: ≥175 PASS（Phase 4 + Task 5.1 + Task 5.2）。

## Step 11: Commit

```bash
git add tests/test_engines_evaluation_last_gains.py tests/test_hitl_review_insights_last_gains.py src/explain_engine/engines/evaluation.py src/explain_engine/hitl/cli_interactive.py
git commit -m "$(cat <<'EOF'
last_gains 持久化 (修 Phase 4 risk #1 reentry 限制)

- EvaluationEngine.score_all 末尾写 state.last_gains
- HITL 2 review_insights 渲染 gain 列读 state.last_gains
- drop candidate 时同步 pop state.last_gains[cid]
- stage=insight_pending 重入时显持久化 gain，不再显 0.0

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 5.3: ExplanationGraph.frontier_nodes() helper

**目的**: 加 `frontier_nodes()` 方法，返 `abstraction_level == 1 且 没有 incoming causes edge` 的节点 id list（Phase 5 语义）。不动现有 `frontier()` 方法（向后兼容）。

**Files:**
- Modify: `src/explain_engine/schema/graph.py`
- Create: `tests/test_schema_graph_frontier_nodes.py`

---

## Step 1: 写失败测试

Create `tests/test_schema_graph_frontier_nodes.py`:

```python
"""ExplanationGraph.frontier_nodes() — Phase 5 expansion 起点识别。

返 abstraction_level == 1 且没有 incoming causes edge 的节点 id list。
排序: 按 node id 字符串升序。
"""

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid: str, level: int) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7, epistemic="insight",
    )


class TestFrontierNodes:
    def test_empty_graph(self) -> None:
        g = ExplanationGraph(root_question="why")
        assert g.frontier_nodes() == []

    def test_only_concrete(self) -> None:
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("p_001", 0))
        g.add_node(_node("p_002", 0))
        # level 0 不是 frontier
        assert g.frontier_nodes() == []

    def test_abstract_no_incoming(self) -> None:
        """c_001 是 abstract 且没有 incoming causes → 是 frontier。"""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("p_001", 0))
        g.add_node(_node("c_001", 1))
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.7, mechanism_description="m",
        ))
        # c_001 有 outgoing manifests_as 但没 incoming causes
        assert g.frontier_nodes() == ["c_001"]

    def test_abstract_with_incoming_causes_excluded(self) -> None:
        """c_001 已经被 d_001 通过 causes 解释 → 不是 frontier。"""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001", 1))
        g.add_node(_node("d_001", 2))
        g.add_edge(RelationEdge(
            id="e_001", source_node="d_001", target_node="c_001",
            relation_type="causes", confidence=0.7, mechanism_description="m",
        ))
        # c_001 有 incoming causes，d_001 是 level=2（Phase 5 cap，不算 frontier）
        assert g.frontier_nodes() == []

    def test_only_level_1_returned(self) -> None:
        """Phase 5 cap: 即使 d_NNN (level=2) 没 incoming causes，也不算 frontier。"""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001", 1))
        g.add_node(_node("d_001", 2))
        # d_001 没有 incoming causes 但 level=2 → 不算 frontier
        assert g.frontier_nodes() == ["c_001"]

    def test_multiple_frontiers_sorted(self) -> None:
        g = ExplanationGraph(root_question="why")
        for cid in ("c_003", "c_001", "c_002"):
            g.add_node(_node(cid, 1))
        # 3 个 abstract 都没 incoming causes，按 id 升序
        assert g.frontier_nodes() == ["c_001", "c_002", "c_003"]

    def test_incoming_manifests_as_not_excluding(self) -> None:
        """abstract 有 incoming manifests_as 不影响 frontier 判定（manifests_as 不算 cause）。"""
        g = ExplanationGraph(root_question="why")
        g.add_node(_node("c_001", 1))
        g.add_node(_node("c_002", 1))
        # 极端 case: 两个 abstract 之间有 manifests_as 边
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="c_002",
            relation_type="manifests_as", confidence=0.7, mechanism_description="m",
        ))
        # c_002 有 incoming manifests_as 但不是 causes → 仍 frontier
        assert g.frontier_nodes() == ["c_001", "c_002"]
```

## Step 2: 跑测试验证失败

Run: `pytest tests/test_schema_graph_frontier_nodes.py -v`
Expected: 全 FAIL (`AttributeError: 'ExplanationGraph' object has no attribute 'frontier_nodes'`)。

## Step 3: 加 frontier_nodes 方法

Modify `src/explain_engine/schema/graph.py` — 在 `frontier()` 方法下方加：

```python
def frontier_nodes(self) -> list[str]:
    """Phase 5 expansion 起点: abstraction_level == 1 且没有 incoming causes edge。

    限制 level == 1 是为了让 graph 最多长到 3 层 (concrete L0 / abstract L1 / driver L2),
    避免 d_NNN 自身又被 expand 出 super-driver (schema AbstractionLevel Literal 不支持 3)。
    Phase 6 扩 Literal 时改 condition 为 level >= 1。

    Returns:
        按 node id 字符串升序的 list。
    """
    incoming_causes_targets = {
        e.target_node
        for e in self._edges.values()
        if e.relation_type == "causes"
    }
    return sorted(
        nid
        for nid, n in self._nodes.items()
        if n.abstraction_level == 1 and nid not in incoming_causes_targets
    )
```

## Step 4: 跑测试验证通过

Run: `pytest tests/test_schema_graph_frontier_nodes.py -v`
Expected: 7 个 PASS。

## Step 5: 跑全测试

Run: `pytest tests/ --tb=short`
Expected: ≥182 PASS。

## Step 6: Commit

```bash
git add tests/test_schema_graph_frontier_nodes.py src/explain_engine/schema/graph.py
git commit -m "$(cat <<'EOF'
schema · ExplanationGraph.frontier_nodes() (Phase 5 expansion 起点)

返 abstraction_level == 1 且没有 incoming causes edge 的节点 id list,
Phase 5 ExpansionEngine 取 frontier 用。

Cap level == 1 把 graph 限在 3 层 (concrete L0 / abstract L1 / driver L2),
不动现有 frontier() 方法 (向后兼容)。Phase 6 扩 AbstractionLevel
Literal 后改 condition >= 1。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 5.4: Provider 抽象重构（3 client → 2 client + factory）

**目的**: 解 Phase 4 design §11 risk #3。`LLM_PROVIDER ∈ {claude, openai, deepseek}` → `LLM_PROTOCOL ∈ {anthropic, openai} + LLM_BASE_URL + LLM_API_KEY`。3 个 client (`claude.py` / `openai_client.py` / `deepseek.py`) → 2 个 (`anthropic_protocol.py` / `openai_protocol.py`)。DeepSeek 通过切 base_url 实现，json_object fallback 通过 `LLM_STRUCTURED_OUTPUT_MODE` env var 控制。

**Files:**
- Create: `src/explain_engine/llm/anthropic_protocol.py`
- Create: `src/explain_engine/llm/openai_protocol.py`
- Modify: `src/explain_engine/llm/client.py` 不动（保留 LLMClient Protocol）
- Modify: `src/explain_engine/config.py` (新 env var 解析)
- Delete: `src/explain_engine/llm/claude.py`
- Delete: `src/explain_engine/llm/openai_client.py`
- Delete: `src/explain_engine/llm/deepseek.py`
- Modify: `.env.example`
- Modify: `README.md` (mapping 表)
- Modify: `tests/conftest.py` (fixture env vars)
- Modify: 已存在 LLM 测试文件名 (`test_llm_claude.py` / `test_llm_openai.py` / `test_llm_deepseek.py` → `test_llm_anthropic_protocol.py` / `test_llm_openai_protocol.py`)
- Create: `tests/test_llm_client_factory.py`

---

## Step 1: 写 anthropic_protocol.py 失败测试

Rename `tests/test_llm_claude.py` → `tests/test_llm_anthropic_protocol.py`，整体替换 import + class name。新内容（保留原 test 逻辑）：

```python
"""AnthropicProtocolClient 测试（旧 ClaudeClient 改名 + 加 base_url）。"""

import pytest
from pydantic import BaseModel

from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient
from explain_engine.llm.client import Message
from explain_engine.llm.errors import LLMError, SchemaValidationError


class _Demo(BaseModel):
    name: str


@pytest.mark.asyncio
async def test_construct_with_default_base_url() -> None:
    """没传 base_url 时用 anthropic SDK 默认 base_url。"""
    c = AnthropicProtocolClient(
        api_key="sk-ant-fake",
        default_model="claude-opus-4-7",
    )
    # SDK 内部 _client.base_url 取默认值，不报错
    assert c._default_model == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_construct_with_explicit_base_url() -> None:
    """传 base_url 时透传给 anthropic SDK（用于 DeepSeek anthropic endpoint）。"""
    c = AnthropicProtocolClient(
        api_key="sk-fake",
        default_model="deepseek-chat",
        base_url="https://api.deepseek.com/anthropic",
    )
    assert str(c._client.base_url).startswith("https://api.deepseek.com/anthropic")


# 复用旧 test_llm_claude.py 的所有 mock-based test，把 ClaudeClient 改 AnthropicProtocolClient
# (e.g. test_chat_no_schema / test_chat_with_schema / test_api_error_wrapped / 
#  test_validation_error_wrapped) —— 行为不变，名字变。
```

(详细逻辑参考 `tests/test_llm_claude.py` 内有的 case，整体复制 + sed s/ClaudeClient/AnthropicProtocolClient/g)

## Step 2: 跑测试验证失败

Run: `pytest tests/test_llm_anthropic_protocol.py -v`
Expected: 全部 collect FAIL (`ImportError: cannot import name 'AnthropicProtocolClient'`)。

## Step 3: 新建 anthropic_protocol.py

Create `src/explain_engine/llm/anthropic_protocol.py` —— 基于现有 `claude.py`，加 `base_url` 参数：

```python
"""Anthropic 协议 client (跨 vendor: Anthropic 官方 / DeepSeek anthropic / Bedrock / Vertex)。

Phase 5 起取代 ClaudeClient,通过 base_url 解耦协议与供应商。
Structured output 走 tools API。
"""

from typing import Any

from anthropic import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncAnthropic,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from explain_engine.llm.client import Message, Response
from explain_engine.llm.errors import LLMError, SchemaValidationError


class AnthropicProtocolClient:
    def __init__(
        self,
        api_key: str,
        default_model: str,
        base_url: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**kwargs)
        self._default_model = default_model

    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str | None = None,
    ) -> Response:
        try:
            # 拆 system message
            system_text: str | None = None
            chat_messages: list[dict[str, Any]] = []
            for m in messages:
                if m.role == "system":
                    system_text = (
                        (system_text + "\n\n" if system_text else "") + m.content
                    )
                else:
                    chat_messages.append({"role": m.role, "content": m.content})

            call_kwargs: dict[str, Any] = {
                "model": model or self._default_model,
                "max_tokens": 4096,
                "messages": chat_messages,
            }
            if system_text:
                call_kwargs["system"] = system_text

            if schema is not None:
                tool_name = schema.__name__
                call_kwargs["tools"] = [
                    {
                        "name": tool_name,
                        "description": schema.__doc__ or f"Structured output: {tool_name}",
                        "input_schema": schema.model_json_schema(),
                    }
                ]
                call_kwargs["tool_choice"] = {"type": "tool", "name": tool_name}

            api_resp = await self._client.messages.create(**call_kwargs)

            text = ""
            parsed: dict[str, Any] | None = None
            for block in api_resp.content:
                if block.type == "tool_use":
                    parsed = dict(block.input)
                elif block.type == "text":
                    text += block.text

            return Response(
                text=text,
                parsed=parsed,
                model=api_resp.model,
                usage={
                    "input_tokens": api_resp.usage.input_tokens,
                    "output_tokens": api_resp.usage.output_tokens,
                },
            )
        except (APIConnectionError, APITimeoutError, RateLimitError, APIError) as exc:
            raise LLMError(str(exc)) from exc
        except ValidationError as exc:
            raise SchemaValidationError(str(exc)) from exc
```

## Step 4: 跑 anthropic_protocol 测试验证通过

Run: `pytest tests/test_llm_anthropic_protocol.py -v`
Expected: 全 PASS。

## Step 5: 写 openai_protocol.py 失败测试

Rename `tests/test_llm_openai.py` → `tests/test_llm_openai_protocol.py`。再 merge `tests/test_llm_deepseek.py` 的 json_object case 进来：

```python
"""OpenAIProtocolClient 测试 (含 json_schema / json_object 双 mode)。"""

import pytest
from pydantic import BaseModel

from explain_engine.llm.openai_protocol import OpenAIProtocolClient


class _Demo(BaseModel):
    name: str


@pytest.mark.asyncio
async def test_json_schema_mode_default(monkeypatch) -> None:
    """默认 mode='json_schema'，走 response_format json_schema。"""
    c = OpenAIProtocolClient(
        api_key="sk-fake",
        default_model="gpt-4o",
        base_url="https://api.openai.com/v1",
        mode="json_schema",
    )
    # 验证 mode 字段保存
    assert c._mode == "json_schema"


@pytest.mark.asyncio
async def test_json_object_mode_for_deepseek(monkeypatch) -> None:
    """mode='json_object'，走 prompt 注入 schema (DeepSeek 等)。"""
    c = OpenAIProtocolClient(
        api_key="sk-fake",
        default_model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        mode="json_object",
    )
    assert c._mode == "json_object"


# 复用旧 test_llm_openai.py + test_llm_deepseek.py 的 mock-based test
# (e.g. test_chat_with_schema_json_schema / test_chat_with_schema_json_object /
#  test_api_error_wrapped) —— mode 路径分别测两套。
```

## Step 6: 新建 openai_protocol.py

Create `src/explain_engine/llm/openai_protocol.py`:

```python
"""OpenAI 协议 client (跨 vendor: OpenAI / DeepSeek openai / Azure / Together / Groq)。

Phase 5 起取代 OpenAIClient + DeepSeekClient,通过 base_url + mode 解耦。

Structured output mode:
- 'json_schema': 用 response_format={"type":"json_schema", ...} strict (OpenAI 官方等)
- 'json_object': 用 response_format={"type":"json_object"} + prompt 注入 schema (DeepSeek 等
   不支持 json_schema strict 的)
"""

import json
from typing import Any, Literal

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from explain_engine.llm.client import Message, Response
from explain_engine.llm.errors import LLMError, SchemaValidationError

Mode = Literal["json_schema", "json_object"]


def _schema_instructions(schema: type[BaseModel]) -> str:
    json_schema = schema.model_json_schema()
    return (
        f"You MUST respond with a single JSON object matching schema "
        f"{schema.__name__}:\n```json\n{json.dumps(json_schema, indent=2)}\n```\n"
        f"Do not include any explanation outside the JSON."
    )


class OpenAIProtocolClient:
    def __init__(
        self,
        api_key: str,
        default_model: str,
        base_url: str | None = None,
        mode: Mode = "json_schema",
    ) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)
        self._default_model = default_model
        self._mode = mode

    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str | None = None,
    ) -> Response:
        try:
            api_messages: list[dict[str, str]] = [
                {"role": m.role, "content": m.content} for m in messages
            ]
            call_kwargs: dict[str, Any] = {
                "model": model or self._default_model,
                "messages": api_messages,
            }

            if schema is not None:
                if self._mode == "json_schema":
                    call_kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema.__name__,
                            "schema": schema.model_json_schema(),
                            "strict": True,
                        },
                    }
                else:  # json_object
                    schema_text = _schema_instructions(schema)
                    if api_messages and api_messages[0]["role"] == "system":
                        api_messages[0] = {
                            "role": "system",
                            "content": schema_text + "\n\n" + api_messages[0]["content"],
                        }
                    else:
                        api_messages.insert(0, {"role": "system", "content": schema_text})
                    call_kwargs["messages"] = api_messages
                    call_kwargs["response_format"] = {"type": "json_object"}

            api_resp = await self._client.chat.completions.create(**call_kwargs)

            text = api_resp.choices[0].message.content or ""
            parsed: dict[str, Any] | None = None
            if schema is not None and text:
                parsed = json.loads(text)

            return Response(
                text=text,
                parsed=parsed,
                model=api_resp.model,
                usage={
                    "input_tokens": api_resp.usage.prompt_tokens,
                    "output_tokens": api_resp.usage.completion_tokens,
                },
            )
        except (APIConnectionError, APITimeoutError, RateLimitError, APIError) as exc:
            raise LLMError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"invalid JSON in response: {exc}") from exc
        except ValidationError as exc:
            raise SchemaValidationError(str(exc)) from exc
```

## Step 7: 跑 openai_protocol 测试验证通过

Run: `pytest tests/test_llm_openai_protocol.py -v`
Expected: 全 PASS。

## Step 8: 写 factory 失败测试

Create `tests/test_llm_client_factory.py`:

```python
"""LLM client factory: 按 LLM_PROTOCOL 路由 anthropic / openai。"""

import pytest


def test_factory_anthropic(monkeypatch) -> None:
    from explain_engine.config import make_llm_client
    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-7")
    client = make_llm_client()
    from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient
    assert isinstance(client, AnthropicProtocolClient)


def test_factory_openai_json_schema(monkeypatch) -> None:
    from explain_engine.config import make_llm_client
    monkeypatch.setenv("LLM_PROTOCOL", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    monkeypatch.delenv("LLM_STRUCTURED_OUTPUT_MODE", raising=False)
    client = make_llm_client()
    from explain_engine.llm.openai_protocol import OpenAIProtocolClient
    assert isinstance(client, OpenAIProtocolClient)
    assert client._mode == "json_schema"   # 默认


def test_factory_openai_json_object(monkeypatch) -> None:
    """DeepSeek 等用 json_object mode。"""
    from explain_engine.config import make_llm_client
    monkeypatch.setenv("LLM_PROTOCOL", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-fake")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("LLM_STRUCTURED_OUTPUT_MODE", "json_object")
    client = make_llm_client()
    from explain_engine.llm.openai_protocol import OpenAIProtocolClient
    assert isinstance(client, OpenAIProtocolClient)
    assert client._mode == "json_object"


def test_factory_unknown_protocol_rejected(monkeypatch) -> None:
    from explain_engine.config import make_llm_client
    monkeypatch.setenv("LLM_PROTOCOL", "bogus")
    monkeypatch.setenv("LLM_BASE_URL", "x")
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setenv("LLM_MODEL", "x")
    with pytest.raises(ValueError, match="LLM_PROTOCOL"):
        make_llm_client()


def test_factory_missing_env_var_rejected(monkeypatch) -> None:
    from explain_engine.config import make_llm_client
    monkeypatch.delenv("LLM_PROTOCOL", raising=False)
    with pytest.raises(KeyError, match="LLM_PROTOCOL"):
        make_llm_client()
```

## Step 9: 改 config.py 加 make_llm_client factory

Read `src/explain_engine/config.py` 看现有结构（多半已有 `get_llm_client()` 或 类似 factory 函数，按 LLM_PROVIDER 路由）。

替换现有 factory 函数，新加 `make_llm_client()`:

```python
import os

from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient
from explain_engine.llm.openai_protocol import OpenAIProtocolClient


def make_llm_client():
    proto = os.environ["LLM_PROTOCOL"]
    base_url = os.environ["LLM_BASE_URL"]
    api_key = os.environ["LLM_API_KEY"]
    model = os.environ["LLM_MODEL"]

    if proto == "anthropic":
        return AnthropicProtocolClient(
            api_key=api_key,
            default_model=model,
            base_url=base_url,
        )
    elif proto == "openai":
        mode = os.environ.get("LLM_STRUCTURED_OUTPUT_MODE", "json_schema")
        return OpenAIProtocolClient(
            api_key=api_key,
            default_model=model,
            base_url=base_url,
            mode=mode,   # type: ignore[arg-type]
        )
    else:
        raise ValueError(
            f"Unknown LLM_PROTOCOL: {proto!r}, must be 'anthropic' or 'openai'"
        )
```

如果 config.py 还有别的 LLM_PROVIDER 相关代码 (`get_provider()` etc.)，一并删除。

## Step 10: 跑 factory 测试验证通过

Run: `pytest tests/test_llm_client_factory.py -v`
Expected: 5 个 PASS。

## Step 11: 改 conftest.py fixture

Read `tests/conftest.py`，找 set LLM env var 的 fixture，改用新 env vars：

```python
# 旧：
# os.environ["LLM_PROVIDER"] = "claude"
# os.environ["CLAUDE_API_KEY"] = "sk-fake"
# 新：
os.environ["LLM_PROTOCOL"] = "anthropic"
os.environ["LLM_BASE_URL"] = "https://api.anthropic.com"
os.environ["LLM_API_KEY"] = "sk-fake"
os.environ["LLM_MODEL"] = "claude-opus-4-7"
```

## Step 12: 改 .env.example

Modify `.env.example` (整体替换):

```env
# Phase 5+ 配置：协议 / base_url / api_key / model 解耦

# 必填：协议（anthropic 或 openai）
LLM_PROTOCOL=anthropic

# 必填：API base url
# - Anthropic 官方:        https://api.anthropic.com
# - DeepSeek anthropic 端点: https://api.deepseek.com/anthropic
# - OpenAI 官方:           https://api.openai.com/v1
# - DeepSeek openai 端点:   https://api.deepseek.com/v1
LLM_BASE_URL=https://api.anthropic.com

# 必填：API key
LLM_API_KEY=

# 必填：模型
LLM_MODEL=claude-opus-4-7

# 可选：OpenAI 协议下 structured output 模式
# - json_schema (默认): OpenAI 官方 / Azure / 多数 vendor 支持
# - json_object       : DeepSeek 等不支持 json_schema strict 的 vendor 用
# LLM_STRUCTURED_OUTPUT_MODE=json_schema
```

## Step 13: 改 README.md 加 Phase 4 → Phase 5 mapping 表

Read `README.md` 找 LLM env var 说明部分，整体替换 + 加 mapping 表：

```markdown
### LLM 配置 (Phase 5+)

Phase 5 起协议跟供应商解耦，配 4 个 env var:

- `LLM_PROTOCOL`: `anthropic` 或 `openai`
- `LLM_BASE_URL`: API 入口（详见 `.env.example`）
- `LLM_API_KEY`: API key
- `LLM_MODEL`: 模型名

可选: `LLM_STRUCTURED_OUTPUT_MODE` (openai 协议下 `json_schema` (默认) / `json_object`)

#### Phase 4 → Phase 5 配置迁移

| Phase 4                              | Phase 5 等价                                                                |
|---|---|
| `LLM_PROVIDER=claude`                | `LLM_PROTOCOL=anthropic` + `LLM_BASE_URL=https://api.anthropic.com`         |
| `LLM_PROVIDER=openai`                | `LLM_PROTOCOL=openai` + `LLM_BASE_URL=https://api.openai.com/v1`            |
| `LLM_PROVIDER=deepseek` (openai)     | `LLM_PROTOCOL=openai` + `LLM_BASE_URL=https://api.deepseek.com/v1` + `LLM_STRUCTURED_OUTPUT_MODE=json_object` |
| `LLM_PROVIDER=deepseek` (anthropic)  | `LLM_PROTOCOL=anthropic` + `LLM_BASE_URL=https://api.deepseek.com/anthropic` |
| `CLAUDE_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY` | `LLM_API_KEY` (统一)                                |
```

## Step 14: 删旧 client 文件 + 旧测试

```bash
git rm src/explain_engine/llm/claude.py
git rm src/explain_engine/llm/openai_client.py
git rm src/explain_engine/llm/deepseek.py
git rm tests/test_llm_claude.py   # 已 rename 为 test_llm_anthropic_protocol.py
git rm tests/test_llm_openai.py   # 已 rename 为 test_llm_openai_protocol.py
git rm tests/test_llm_deepseek.py # 部分逻辑 merge 进 test_llm_openai_protocol.py
```

## Step 15: 全测试 + ruff

Run: `pytest tests/ --tb=short`
Expected: 仍 ≥182 PASS（旧 LLM 测试改名后保留逻辑，加了 5 个 factory test）。

如果有 import error（其他模块还 import 旧 claude.py / openai_client.py / deepseek.py），diagnose 后改 import:
- `from explain_engine.llm.claude import ClaudeClient` → `from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient`
- 等等

Run: `ruff check src tests`
Expected: 0 errors。

## Step 16: Commit

```bash
git add -A
git commit -m "$(cat <<'EOF'
llm · Provider 抽象重构 (LLM_PROTOCOL + BASE_URL 三元组)

解 Phase 4 design §11 risk #3: 把"协议"和"供应商"解耦。
3 个 client → 2 个:

- claude.py + 删       → anthropic_protocol.py (加 base_url 参数)
- openai_client.py + 删 + deepseek.py + 删
  → openai_protocol.py (加 base_url + json_schema/json_object 双 mode)

新 env vars: LLM_PROTOCOL / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL,
+ 可选 LLM_STRUCTURED_OUTPUT_MODE (json_schema 默认 / json_object for
DeepSeek 等不支持 json_schema strict 的 vendor)。

config.make_llm_client() factory 按 LLM_PROTOCOL 路由。

.env.example + README mapping 表覆盖 Phase 4 → Phase 5 迁移路径。

旧 LLM 测试改名 + merge: test_llm_claude.py → test_llm_anthropic_protocol.py;
test_llm_openai.py + test_llm_deepseek.py → test_llm_openai_protocol.py。
新增 test_llm_client_factory.py (5 case)。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 5.5: Prompts — expansion.yaml + loader test

**目的**: 新增 `expansion.yaml` prompt，让 LLM 给定一个 abstract candidate 出 1-3 个 driver candidate（含 plausibility 自评 1-5）。

**Files:**
- Create: `src/explain_engine/llm/prompts/expansion.yaml`
- Create: `tests/test_llm_prompts_expansion_loader.py`

---

## Step 1: 写 loader 失败测试

Create `tests/test_llm_prompts_expansion_loader.py`:

```python
"""expansion.yaml prompt loader 测试。"""

from explain_engine.llm.prompts._loader import load_prompt


def test_expansion_yaml_loads() -> None:
    p = load_prompt("expansion")
    assert "system" in p
    assert "user_template" in p


def test_expansion_yaml_has_required_placeholders() -> None:
    p = load_prompt("expansion")
    tpl = p["user_template"]
    # 必需 placeholder
    for ph in ("{question}", "{target_node}", "{target_outgoing_edges}", "{existing_drivers}"):
        assert ph in tpl, f"missing placeholder {ph!r} in expansion user_template"


def test_expansion_system_mentions_driver() -> None:
    p = load_prompt("expansion")
    sys = p["system"]
    # 哲学锚: driver 必须是可检验机制变量，不是 cosmic 哲学名词
    assert "driver" in sys.lower()
```

## Step 2: 跑测试验证失败

Run: `pytest tests/test_llm_prompts_expansion_loader.py -v`
Expected: 全 FAIL (`FileNotFoundError` 或 prompt 不存在)。

## Step 3: 写 expansion.yaml

Create `src/explain_engine/llm/prompts/expansion.yaml`:

```yaml
system: |
  你是一个 cognitive engine 的 expansion sub-agent。
  你的任务是给定一个 abstract variable（已被建立的 explanation candidate），
  找出**它的 driver**（更上游的 cause）—— 即什么生成了这个 abstract。

  约束:
  - Driver 必须是**可被进一步检验**的机制变量,例如"集体身份维系压力"、
    "教义不可妥协性"、"代际记忆传递机制"。
  - Driver **不能**是 cosmic 哲学名词,例如 "熵增"、"进化"、"宇宙真理"、
    "人性"。这类抽象到无法 falsify 的概念会让 reasoning 退化为神学。
  - 每个 driver 必须能给出 mechanism: "为什么这个 driver 会生成 target abstract"。
  - 每个 driver 必须自评 plausibility (1-5 整数): driver→target 的 mechanism
    可信度。1=投机, 3=合理推断, 5=机制清晰且可与已知现象交叉验证。
  - 输出 1-3 个 driver。多角度互不冗余优于单一最强。
  - 不要重复 existing_drivers 列出的已有 driver。

  输出 schema (JSON):
  {
    "drivers": [
      {"name": str, "description": str, "mechanism": str, "plausibility": 1..5},
      ...
    ]
  }

user_template: |
  根问题: {question}

  当前要 expand 的 abstract variable:
  {target_node}

  这个 abstract 已经解释了以下 concrete 现象 (通过 manifests_as edge):
  {target_outgoing_edges}

  已知 driver (不要重复，建议互补):
  {existing_drivers}

  请输出 1-3 个 driver,每个含 name / description / mechanism / plausibility。
```

## Step 4: 跑测试验证通过

Run: `pytest tests/test_llm_prompts_expansion_loader.py -v`
Expected: 3 个 PASS。

## Step 5: Commit

```bash
git add tests/test_llm_prompts_expansion_loader.py src/explain_engine/llm/prompts/expansion.yaml
git commit -m "$(cat <<'EOF'
prompts · expansion.yaml (Phase 5 上溯 driver)

LLM 给定 abstract candidate 出 1-3 个 driver,含 mechanism + plausibility
自评 1-5。约束: driver 必须可检验机制变量,不能是 cosmic 哲学名词
(熵增 / 进化 / 宇宙真理) 否则 reasoning 退化为神学。

placeholder: {question} / {target_node} / {target_outgoing_edges} / {existing_drivers}

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 5.6: ExpansionEngine.expand_one_frontier

**目的**: 实现 `expand_one_frontier(state, target_id, llm)` 主函数：取 frontier 节点 → 调 LLM → 出 1-3 driver → 灌 graph (新 `d_NNN` VariableNode + `causes` RelationEdge) → 返回新 driver id list。

**Files:**
- Create: `src/explain_engine/engines/expansion.py`
- Create: `tests/test_engines_expansion.py`

---

## Step 1: 写 ExpansionEngine 失败测试 (mock LLM 返 2 driver)

Create `tests/test_engines_expansion.py`:

```python
"""ExpansionEngine.expand_one_frontier — Phase 5 上溯 driver。"""

from typing import Any

import pytest

from explain_engine.engines import expansion
from explain_engine.llm.client import Response
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


class FakeLLM:
    """Mock LLM 返预设 driver list。"""

    def __init__(self, drivers_data: list[dict[str, Any]], parsed: dict | None = None) -> None:
        self._drivers_data = drivers_data
        self._explicit_parsed = parsed
        self.call_count = 0
        self.last_messages: list = []

    async def chat(self, messages, schema=None, model=None):
        self.call_count += 1
        self.last_messages = messages
        parsed = self._explicit_parsed if self._explicit_parsed is not None else {
            "drivers": self._drivers_data
        }
        return Response(
            text="ignored",
            parsed=parsed,
            model="fake",
            usage={"input_tokens": 0, "output_tokens": 0},
        )


def _state_with_frontier() -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(id="p_001", name="p1", description="d", abstraction_level=0,
                            confidence=0.8, epistemic="observation"))
    g.add_node(VariableNode(id="p_002", name="p2", description="d", abstraction_level=0,
                            confidence=0.8, epistemic="observation"))
    g.add_node(VariableNode(id="c_001", name="abstract", description="abs",
                            abstraction_level=1, confidence=0.7, epistemic="insight"))
    g.add_edge(RelationEdge(id="e_001", source_node="c_001", target_node="p_001",
                            relation_type="manifests_as", confidence=0.7,
                            mechanism_description="m1"))
    g.add_edge(RelationEdge(id="e_002", source_node="c_001", target_node="p_002",
                            relation_type="manifests_as", confidence=0.7,
                            mechanism_description="m2"))
    state = CognitiveState(graph=g, budget_remaining=10, root_question="why")
    return state


@pytest.mark.asyncio
async def test_happy_path_creates_drivers() -> None:
    state = _state_with_frontier()
    llm = FakeLLM([
        {"name": "driver A", "description": "da", "mechanism": "ma", "plausibility": 4},
        {"name": "driver B", "description": "db", "mechanism": "mb", "plausibility": 3},
    ])
    new_ids = await expansion.expand_one_frontier(state, "c_001", llm)

    assert new_ids == ["d_001", "d_002"]
    assert state.graph.nodes["d_001"].abstraction_level == 2
    assert state.graph.nodes["d_001"].epistemic == "inference"
    assert state.graph.nodes["d_001"].source == "llm"
    assert state.graph.nodes["d_001"].name == "driver A"
    # causes edge
    causes_edges = [e for e in state.graph.edges.values() if e.relation_type == "causes"]
    assert len(causes_edges) == 2
    assert causes_edges[0].source_node == "d_001"
    assert causes_edges[0].target_node == "c_001"


@pytest.mark.asyncio
async def test_truncate_to_3_drivers() -> None:
    state = _state_with_frontier()
    llm = FakeLLM([
        {"name": f"d{i}", "description": "x", "mechanism": "m", "plausibility": 3}
        for i in range(5)
    ])
    new_ids = await expansion.expand_one_frontier(state, "c_001", llm)
    assert len(new_ids) == 3


@pytest.mark.asyncio
async def test_zero_drivers_returns_empty_no_raise(caplog) -> None:
    state = _state_with_frontier()
    llm = FakeLLM([])
    new_ids = await expansion.expand_one_frontier(state, "c_001", llm)
    assert new_ids == []
    assert "0 driver" in caplog.text.lower() or "no driver" in caplog.text.lower()


@pytest.mark.asyncio
async def test_target_not_in_graph_raises() -> None:
    state = _state_with_frontier()
    llm = FakeLLM([])
    with pytest.raises(ValueError, match="not.*found|不存在"):
        await expansion.expand_one_frontier(state, "c_999", llm)


@pytest.mark.asyncio
async def test_target_not_level_1_raises() -> None:
    """expand 一个 concrete (level=0) 应该报错。"""
    state = _state_with_frontier()
    llm = FakeLLM([])
    with pytest.raises(ValueError, match="level"):
        await expansion.expand_one_frontier(state, "p_001", llm)


@pytest.mark.asyncio
async def test_target_already_has_causes_raises() -> None:
    """expand 一个已经有 incoming causes 的 abstract 应该报错。"""
    state = _state_with_frontier()
    state.graph.add_node(VariableNode(
        id="d_existing", name="existing", description="d", abstraction_level=2,
        confidence=0.6, epistemic="inference",
    ))
    state.graph.add_edge(RelationEdge(
        id="e_existing", source_node="d_existing", target_node="c_001",
        relation_type="causes", confidence=0.7, mechanism_description="m",
    ))
    llm = FakeLLM([])
    with pytest.raises(ValueError, match="frontier|causes"):
        await expansion.expand_one_frontier(state, "c_001", llm)


@pytest.mark.asyncio
async def test_id_continuation() -> None:
    """已有 d_001/d_002 时新建从 d_003 开始。"""
    state = _state_with_frontier()
    state.graph.add_node(VariableNode(
        id="d_001", name="old1", description="d", abstraction_level=2,
        confidence=0.6, epistemic="inference",
    ))
    state.graph.add_node(VariableNode(
        id="d_002", name="old2", description="d", abstraction_level=2,
        confidence=0.6, epistemic="inference",
    ))
    llm = FakeLLM([
        {"name": "new", "description": "d", "mechanism": "m", "plausibility": 4},
    ])
    new_ids = await expansion.expand_one_frontier(state, "c_001", llm)
    assert new_ids == ["d_003"]


@pytest.mark.asyncio
async def test_invalid_schema_retry_then_raise() -> None:
    """LLM 第一次返非法 schema → retry 1 次仍失败 → 抛 SchemaValidationError。"""
    state = _state_with_frontier()

    class FailingLLM:
        async def chat(self, messages, schema=None, model=None):
            return Response(
                text="bad",
                parsed={"wrong_key": "x"},
                model="fake",
                usage={"input_tokens": 0, "output_tokens": 0},
            )

    with pytest.raises(SchemaValidationError):
        await expansion.expand_one_frontier(state, "c_001", FailingLLM())


@pytest.mark.asyncio
async def test_invalid_plausibility_retry_then_raise() -> None:
    state = _state_with_frontier()
    llm = FakeLLM(
        [],
        parsed={"drivers": [
            {"name": "d", "description": "d", "mechanism": "m", "plausibility": 7},
        ]},
    )
    with pytest.raises(SchemaValidationError):
        await expansion.expand_one_frontier(state, "c_001", llm)


@pytest.mark.asyncio
async def test_existing_driver_name_match_reused() -> None:
    """LLM 出的 driver name 跟现有 node 完全相同 → 复用现有 id，只加 edge。"""
    state = _state_with_frontier()
    state.graph.add_node(VariableNode(
        id="d_existing", name="重复 driver", description="d", abstraction_level=2,
        confidence=0.6, epistemic="inference",
    ))
    llm = FakeLLM([
        {"name": "重复 driver", "description": "d2", "mechanism": "m", "plausibility": 4},
    ])
    new_ids = await expansion.expand_one_frontier(state, "c_001", llm)
    assert new_ids == ["d_existing"]
    # 没有新建 d_NNN
    d_nodes = [nid for nid in state.graph.nodes if nid.startswith("d_")]
    assert len(d_nodes) == 1
    # 但加了一条 edge d_existing → c_001
    causes = [e for e in state.graph.edges.values() if e.relation_type == "causes"]
    assert any(e.source_node == "d_existing" and e.target_node == "c_001" for e in causes)


@pytest.mark.asyncio
async def test_prompt_passes_existing_drivers() -> None:
    state = _state_with_frontier()
    state.graph.add_node(VariableNode(
        id="d_001", name="prior driver", description="d", abstraction_level=2,
        confidence=0.6, epistemic="inference",
    ))
    llm = FakeLLM([
        {"name": "new", "description": "d", "mechanism": "m", "plausibility": 4},
    ])
    await expansion.expand_one_frontier(state, "c_001", llm)
    # 验证 prompt 里 existing_drivers placeholder 含 "prior driver"
    user_msg = next(m for m in llm.last_messages if m.role == "user")
    assert "prior driver" in user_msg.content
```

## Step 2: 跑测试验证失败

Run: `pytest tests/test_engines_expansion.py -v`
Expected: 全 FAIL (`ImportError: cannot import name 'expansion'`)。

## Step 3: 写 ExpansionEngine

Create `src/explain_engine/engines/expansion.py`:

```python
"""Expansion Engine — 给一个 abstract candidate 上溯 driver。

Phase 5 设计参考 docs/plans/2026-05-13-cognitive-engine-phase-5-design.md §3。

输入: state (含 graph + insight_candidates) + target_id (frontier 节点)
输出: list[str] (新建 d_NNN id list)
副作用:
  - state.graph 新增 1-3 d_NNN VariableNode (level=2, source="llm",
    epistemic="inference", confidence=0.6)
  - state.graph 新增 1-3 causes RelationEdge (driver_id → target_id)
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field, ValidationError

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


class _DriverCandidate(BaseModel):
    name: str
    description: str
    mechanism: str
    plausibility: int = Field(ge=1, le=5)


class ExpansionOutput(BaseModel):
    """expansion.yaml prompt 的 structured output."""

    drivers: list[_DriverCandidate]


async def expand_one_frontier(
    state: CognitiveState,
    target_id: str,
    llm: LLMClient,
    max_drivers: int = 3,
) -> list[str]:
    """LLM 出 1-3 driver,灌进 state.graph,返回新 driver id list。

    Raises:
        ValueError: target_id 不在 graph / target 不是 level==1 / target 已有 incoming causes
        LLMError: LLM 调用失败 (provider 抛出，本函数不捕)
        SchemaValidationError: LLM 输出 schema / plausibility 不合规 (retry 1 次后仍失败)
    """
    # 校验 target
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

    # 准备 prompt
    prompt = load_prompt("expansion")
    outgoing_edges_text = _render_target_outgoing_edges(state, target_id)
    existing_drivers_text = _render_existing_drivers(state)

    messages = [
        Message(role="system", content=prompt["system"]),
        Message(
            role="user",
            content=prompt["user_template"].format(
                question=state.root_question,
                target_node=f"{target.id}: {target.name} — {target.description}",
                target_outgoing_edges=outgoing_edges_text,
                existing_drivers=existing_drivers_text,
            ),
        ),
    ]

    output = await _call_with_retry(llm, messages)
    drivers = output.drivers[:max_drivers]

    if not drivers:
        logger.warning("Expansion 0 driver for target %s (skip, no aboard)", target_id)
        return []

    # 灌进 graph
    next_d_num = _next_driver_id_num(state)
    next_edge_id = _next_edge_id(state)
    new_ids: list[str] = []
    existing_name_to_id = {n.name: nid for nid, n in state.graph.nodes.items()}

    for d in drivers:
        if d.name in existing_name_to_id:
            # 复用现有 node id（防重）
            d_id = existing_name_to_id[d.name]
        else:
            d_id = f"d_{next_d_num:03d}"
            next_d_num += 1
            state.graph.add_node(
                VariableNode(
                    id=d_id,
                    name=d.name,
                    description=d.description,
                    abstraction_level=2,
                    confidence=0.6,
                    epistemic="inference",
                    source="llm",
                )
            )

        # 加 causes edge
        state.graph.add_edge(
            RelationEdge(
                id=f"e_{next_edge_id:03d}",
                source_node=d_id,
                target_node=target_id,
                relation_type="causes",
                confidence=0.6,
                mechanism_description=d.mechanism,
            )
        )
        next_edge_id += 1
        new_ids.append(d_id)

    return new_ids


def _render_target_outgoing_edges(state: CognitiveState, target_id: str) -> str:
    lines: list[str] = []
    for e in state.graph.edges.values():
        if e.source_node == target_id and e.relation_type == "manifests_as":
            concrete = state.graph.nodes.get(e.target_node)
            name = concrete.name if concrete else e.target_node
            lines.append(f"- {e.target_node}: {name} (mechanism: {e.mechanism_description})")
    return "\n".join(lines) if lines else "(none)"


def _render_existing_drivers(state: CognitiveState) -> str:
    lines = [
        f"- {nid}: {n.name} — {n.description}"
        for nid, n in state.graph.nodes.items()
        if nid.startswith("d_") and n.abstraction_level == 2
    ]
    return "\n".join(lines) if lines else "(none)"


def _next_driver_id_num(state: CognitiveState) -> int:
    existing = [
        int(nid.split("_")[1])
        for nid in state.graph.nodes
        if nid.startswith("d_") and nid[2:].isdigit()
    ]
    return (max(existing) + 1) if existing else 1


def _next_edge_id(state: CognitiveState) -> int:
    existing = [
        int(eid.split("_")[1])
        for eid in state.graph.edges
        if eid.startswith("e_") and eid[2:].isdigit()
    ]
    return (max(existing) + 1) if existing else 1


async def _call_with_retry(
    llm: LLMClient,
    messages: list[Message],
) -> ExpansionOutput:
    last_exc: Exception | None = None
    for _attempt in range(2):
        resp = await llm.chat(messages, schema=ExpansionOutput)
        if resp.parsed is None:
            last_exc = SchemaValidationError("LLM 未返回 structured output")
            continue
        try:
            return ExpansionOutput.model_validate(resp.parsed)
        except ValidationError as exc:
            last_exc = SchemaValidationError(f"LLM 输出 schema 不合规: {exc}")
            continue
    assert last_exc is not None
    raise last_exc


def mean_plausibility(state: CognitiveState, driver_ids: list[str]) -> float:
    """计算一组 driver 的 expansion_gain = mean(plausibility) / 5.0。

    plausibility 在 LLM 输出时已写进 causes edge 的 mechanism_description 之外的地方？
    Phase 5 简化: 把 plausibility 隐含进 confidence (0.6 + 0.08*(plausibility-3))？
    或者另存储。这里选不存 plausibility (Phase 6 重新设计 expansion_gain) -- 改用
    confidence 反推:

    返回 expansion_gain ∈ [0, 1]。
    """
    if not driver_ids:
        return 0.0
    confs = [state.graph.nodes[did].confidence for did in driver_ids if did in state.graph.nodes]
    return sum(confs) / len(confs) if confs else 0.0
```

> **注**: Phase 5 简化设计里 plausibility 通过 confidence 间接持久化 —— 因为 schema 里没单独 plausibility 字段。Runtime 算 gain 时调 `mean_plausibility(state, new_driver_ids)`。如果 Phase 5 跑通后发现 plausibility 信息丢失太多，Phase 6 给 VariableNode 加 `meta: dict` 或直接加 `plausibility: int | None`。

> **修正测试**: 测试里 plausibility=4 → confidence 应该 0.6 + 0.08*(4-3) = 0.68？这有点 hacky。**简化方案**: ExpansionEngine 不直接持久化 plausibility，**plausibility 信息只用于 Runtime 当 tick 算 gain（in-memory，不落盘）**。Runtime.run 调 `expand_one_frontier` 返 `(new_ids, gain)` 二元组。

让我改返回签名:

```python
async def expand_one_frontier(
    state: CognitiveState,
    target_id: str,
    llm: LLMClient,
    max_drivers: int = 3,
) -> tuple[list[str], float]:
    """... 返 (new_driver_ids, expansion_gain) -- gain = mean(plausibility) / 5.0。"""
    # ... 现有逻辑 ...
    plausibilities = [d.plausibility for d in drivers]
    gain = sum(plausibilities) / (5.0 * len(plausibilities)) if plausibilities else 0.0
    return new_ids, gain
```

测试 assertion 改为：

```python
new_ids, gain = await expansion.expand_one_frontier(state, "c_001", llm)
assert new_ids == ["d_001", "d_002"]
assert abs(gain - (4+3) / (5.0*2)) < 1e-9
```

并删 `mean_plausibility` 函数（用不上）。

## Step 4: 修正测试 + impl，跑测试验证通过

(更新所有测试 + 新 impl 返二元组。删 `mean_plausibility` 函数。)

Run: `pytest tests/test_engines_expansion.py -v`
Expected: 11 个 PASS。

## Step 5: 跑全测试

Run: `pytest tests/ --tb=short`
Expected: ≥193 PASS。

Run: `ruff check src tests`
Expected: 0 errors。

## Step 6: Commit

```bash
git add tests/test_engines_expansion.py src/explain_engine/engines/expansion.py
git commit -m "$(cat <<'EOF'
engines · ExpansionEngine.expand_one_frontier (Phase 5 上溯 driver)

给一个 frontier (abstract level=1 且无 incoming causes) 调 LLM 出 1-3
driver,灌 graph (d_NNN level=2 + causes edge),返 (new_ids, gain)。

gain = mean(plausibility) / 5.0  (plausibility in-memory only, 不落盘)

校验:
- target 不存在 → ValueError
- target level != 1 → ValueError
- target 已有 incoming causes → ValueError (not a frontier)
- LLM 返 0 driver → warn + return ([], 0.0)
- LLM 返 >3 → 截断
- 同名 driver → 复用现有 id，只加 edge
- LLM 输出 schema / plausibility 不合规 → retry 1 次后抛 SchemaValidationError

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 5.7: Runtime — PhaseScheduler + should_stop

**目的**: 实现 `PhaseScheduler` (K=4 phase-based) + `should_stop` (3 signal: budget_exhausted / no_gain_for_3_ticks / no_frontier_remaining)。

**Files:**
- Modify: `src/explain_engine/runtime/__init__.py` (export 必要符号)
- Create: `src/explain_engine/runtime/scheduler.py`
- Create: `src/explain_engine/runtime/stop.py`
- Create: `tests/test_runtime_scheduler.py`
- Create: `tests/test_runtime_stop.py`

---

## Step 1: 写 Scheduler 失败测试

Create `tests/test_runtime_scheduler.py`:

```python
"""PhaseScheduler — Phase 5 phase-based scheduler。

1 round = K expand + 1 evaluate (K+1 tick)。
"""

from explain_engine.runtime.scheduler import PhaseScheduler
from explain_engine.schema.state import CognitiveState


def _state(tick: int) -> CognitiveState:
    s = CognitiveState.bootstrap("why", budget=10)
    s.tick = tick
    return s


class TestPhaseScheduler:
    def test_k4_round_layout(self) -> None:
        """K=4: tick 0-3 = expand, tick 4 = evaluate, tick 5-8 = expand, tick 9 = evaluate, ..."""
        sched = PhaseScheduler(K=4)
        assert sched.pick(_state(0)) == "expand"
        assert sched.pick(_state(1)) == "expand"
        assert sched.pick(_state(2)) == "expand"
        assert sched.pick(_state(3)) == "expand"
        assert sched.pick(_state(4)) == "evaluate"
        assert sched.pick(_state(5)) == "expand"
        assert sched.pick(_state(9)) == "evaluate"
        assert sched.pick(_state(10)) == "expand"

    def test_k3_round_layout(self) -> None:
        sched = PhaseScheduler(K=3)
        assert sched.pick(_state(0)) == "expand"
        assert sched.pick(_state(2)) == "expand"
        assert sched.pick(_state(3)) == "evaluate"
        assert sched.pick(_state(4)) == "expand"

    def test_k5_round_layout(self) -> None:
        sched = PhaseScheduler(K=5)
        assert sched.pick(_state(4)) == "expand"
        assert sched.pick(_state(5)) == "evaluate"

    def test_default_k_is_4(self) -> None:
        sched = PhaseScheduler()
        assert sched.K == 4
```

## Step 2: 跑测试验证失败

Run: `pytest tests/test_runtime_scheduler.py -v`
Expected: 全 FAIL (`ImportError`)。

## Step 3: 写 scheduler.py

Create `src/explain_engine/runtime/scheduler.py`:

```python
"""PhaseScheduler — Phase 5 phase-based scheduler。

1 round = K expand + 1 evaluate = (K+1) tick。
budget=15, K=4 → 3 round = 12 expand + 3 evaluate。
"""

from dataclasses import dataclass
from typing import Literal

from explain_engine.schema.state import CognitiveState

Action = Literal["expand", "evaluate"]


@dataclass
class PhaseScheduler:
    K: int = 4

    def pick(self, state: CognitiveState) -> Action:
        """按 (K+1)-modulo 决定下一个 action。

        Phase 5 不出新 compression candidate,所以 round 内只有 expand / evaluate。
        compress 在 Phase 5 不入 round (新 candidate 推 Phase 6 + batch scoring)。
        """
        if state.tick % (self.K + 1) < self.K:
            return "expand"
        return "evaluate"
```

## Step 4: 跑 scheduler 测试验证通过

Run: `pytest tests/test_runtime_scheduler.py -v`
Expected: 4 个 PASS。

## Step 5: 写 should_stop 失败测试

Create `tests/test_runtime_stop.py`:

```python
"""should_stop — Phase 5 3 个 stop signal:
  - budget_exhausted
  - no_gain_for_3_ticks
  - no_frontier_remaining
"""

from explain_engine.runtime.stop import GAIN_THRESHOLD, should_stop
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _state_with_frontier(tick: int, budget: int, last_gain_tick: int) -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(id="c_001", name="x", description="d", abstraction_level=1,
                            confidence=0.7, epistemic="insight"))
    s = CognitiveState(graph=g, budget_remaining=budget, root_question="why")
    s.tick = tick
    s.last_gain_tick = last_gain_tick
    return s


def _state_no_frontier(tick: int, budget: int, last_gain_tick: int) -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    # 无 level==1 节点 → 无 frontier
    s = CognitiveState(graph=g, budget_remaining=budget, root_question="why")
    s.tick = tick
    s.last_gain_tick = last_gain_tick
    return s


class TestShouldStop:
    def test_budget_exhausted(self) -> None:
        s = _state_with_frontier(tick=5, budget=0, last_gain_tick=4)
        stop, reason = should_stop(s)
        assert stop is True
        assert reason == "budget_exhausted"

    def test_no_gain_for_3_ticks(self) -> None:
        s = _state_with_frontier(tick=5, budget=10, last_gain_tick=2)
        # tick - last_gain_tick = 3 → trigger
        stop, reason = should_stop(s)
        assert stop is True
        assert reason == "no_gain_for_3_ticks"

    def test_no_gain_2_ticks_no_stop(self) -> None:
        s = _state_with_frontier(tick=4, budget=10, last_gain_tick=2)
        # diff = 2 → no stop
        stop, reason = should_stop(s)
        assert stop is False
        assert reason is None

    def test_no_frontier_remaining(self) -> None:
        s = _state_no_frontier(tick=3, budget=10, last_gain_tick=3)
        stop, reason = should_stop(s)
        assert stop is True
        assert reason == "no_frontier_remaining"

    def test_budget_first_priority(self) -> None:
        """多 signal 同时触发,budget_exhausted 优先返回。"""
        s = _state_no_frontier(tick=10, budget=0, last_gain_tick=2)
        stop, reason = should_stop(s)
        assert reason == "budget_exhausted"

    def test_no_gain_before_no_frontier(self) -> None:
        s = _state_no_frontier(tick=5, budget=10, last_gain_tick=2)
        stop, reason = should_stop(s)
        # budget 没空, no_gain 先于 no_frontier 检查
        assert reason == "no_gain_for_3_ticks"

    def test_running_no_stop(self) -> None:
        s = _state_with_frontier(tick=1, budget=10, last_gain_tick=0)
        stop, reason = should_stop(s)
        assert stop is False
        assert reason is None

    def test_gain_threshold_exists(self) -> None:
        assert isinstance(GAIN_THRESHOLD, float)
        assert 0.0 < GAIN_THRESHOLD < 1.0
```

## Step 6: 跑测试验证失败

Run: `pytest tests/test_runtime_stop.py -v`
Expected: 全 FAIL。

## Step 7: 写 stop.py

Create `src/explain_engine/runtime/stop.py`:

```python
"""Phase 5 stop signals + GAIN_THRESHOLD。

3 个 signal,按优先级检查:
  1. budget_exhausted: budget_remaining <= 0
  2. no_gain_for_3_ticks: tick - last_gain_tick >= 3
  3. no_frontier_remaining: graph.frontier_nodes() == []
"""

from explain_engine.schema.state import CognitiveState

GAIN_THRESHOLD: float = 0.1
"""Phase 5 阈值: expansion_gain >= 0.1 (plausibility >= 0.5/5) 算"有 gain"。

Phase 5 跑 ≥1 真实 session 后 tune。
"""


def should_stop(state: CognitiveState) -> tuple[bool, str | None]:
    if state.budget_remaining <= 0:
        return True, "budget_exhausted"
    if state.tick - state.last_gain_tick >= 3:
        return True, "no_gain_for_3_ticks"
    if not state.graph.frontier_nodes():
        return True, "no_frontier_remaining"
    return False, None
```

## Step 8: 跑测试验证通过

Run: `pytest tests/test_runtime_stop.py -v`
Expected: 8 个 PASS。

## Step 9: 改 runtime/__init__.py

Modify `src/explain_engine/runtime/__init__.py`:

```python
"""Phase 5 reasoning runtime."""

from explain_engine.runtime.scheduler import PhaseScheduler
from explain_engine.runtime.stop import GAIN_THRESHOLD, should_stop

__all__ = ["PhaseScheduler", "should_stop", "GAIN_THRESHOLD"]
```

## Step 10: 跑全测试

Run: `pytest tests/ --tb=short`
Expected: ≥205 PASS。

## Step 11: Commit

```bash
git add tests/test_runtime_scheduler.py tests/test_runtime_stop.py src/explain_engine/runtime/scheduler.py src/explain_engine/runtime/stop.py src/explain_engine/runtime/__init__.py
git commit -m "$(cat <<'EOF'
runtime · PhaseScheduler + should_stop (Phase 5)

PhaseScheduler: 1 round = K expand + 1 evaluate (K=4 default).
budget=15 → 3 round = 12 expand + 3 evaluate.

should_stop 3 signal (按优先级):
  1. budget_exhausted (budget_remaining <= 0)
  2. no_gain_for_3_ticks (tick - last_gain_tick >= 3)
  3. no_frontier_remaining (graph.frontier_nodes() == [])

GAIN_THRESHOLD = 0.1 (plausibility >= 0.5/5), Phase 5 跑 ≥1 真实 session 后 tune.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 5.8: Runtime — Runtime.run 主循环

**目的**: 实现 `Runtime.run(state, llm, budget, on_tick=None)` 主循环：

```
while True:
    stop, reason = should_stop(state)
    if stop: break
    action = scheduler.pick(state)
    if action == "expand":
        frontier = state.graph.frontier_nodes()
        target = frontier[0] if frontier else None
        if target: new_ids, gain = await expand_one_frontier(state, target, llm)
    else: action == "evaluate" → no-op
    state.reasoning_trace.append(TraceEntry(...))
    if gain >= GAIN_THRESHOLD: state.last_gain_tick = state.tick
    state.tick += 1, state.budget_remaining -= 1
    if on_tick: on_tick(state)
state.meta.stage = "converged"  # 由 caller 写
return stop_reason
```

**Files:**
- Create: `src/explain_engine/runtime/runtime.py`
- Create: `tests/test_runtime_run.py`

---

## Step 1: 写 Runtime.run 失败测试 (mock LLM 完整 budget=3 path)

Create `tests/test_runtime_run.py`:

```python
"""Runtime.run — Phase 5 主循环 integration test (mock LLM)。"""

from typing import Any

import pytest

from explain_engine.llm.client import Response
from explain_engine.runtime.runtime import run
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


class FakeLLM:
    """Mock LLM: 每次 chat 返一个 driver。"""

    def __init__(self, plausibility: int = 4) -> None:
        self.plausibility = plausibility
        self.call_count = 0

    async def chat(self, messages, schema=None, model=None):
        self.call_count += 1
        return Response(
            text="ignored",
            parsed={"drivers": [{
                "name": f"driver-{self.call_count}",
                "description": "d",
                "mechanism": "m",
                "plausibility": self.plausibility,
            }]},
            model="fake",
            usage={"input_tokens": 0, "output_tokens": 0},
        )


def _state_with_2_frontiers() -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(id="p_001", name="p1", description="d", abstraction_level=0,
                            confidence=0.8, epistemic="observation"))
    for cid in ("c_001", "c_002"):
        g.add_node(VariableNode(id=cid, name=cid, description="d", abstraction_level=1,
                                confidence=0.7, epistemic="insight"))
        g.add_edge(RelationEdge(id=f"e_{cid}", source_node=cid, target_node="p_001",
                                relation_type="manifests_as", confidence=0.7,
                                mechanism_description="m"))
    return CognitiveState(graph=g, budget_remaining=0, root_question="why")


@pytest.mark.asyncio
async def test_run_budget_5_completes() -> None:
    state = _state_with_2_frontiers()
    llm = FakeLLM(plausibility=4)
    reason = await run(state, llm, budget=5)
    assert reason in {"budget_exhausted", "no_gain_for_3_ticks", "no_frontier_remaining"}
    assert state.budget_remaining == 0   # 用完
    assert state.tick >= 2
    assert len(state.reasoning_trace) == state.tick


@pytest.mark.asyncio
async def test_run_writes_reasoning_trace() -> None:
    state = _state_with_2_frontiers()
    llm = FakeLLM(plausibility=4)
    await run(state, llm, budget=5)
    # 每 tick 1 entry
    assert all(e.action in {"expand", "evaluate"} for e in state.reasoning_trace)
    # 第一个 tick 是 expand (K=4 modulo)
    assert state.reasoning_trace[0].action == "expand"


@pytest.mark.asyncio
async def test_run_updates_last_gain_tick() -> None:
    state = _state_with_2_frontiers()
    llm = FakeLLM(plausibility=4)   # gain = 4/5 = 0.8 >= 0.1
    await run(state, llm, budget=5)
    # 至少 1 个 tick 触发 gain 更新
    assert state.last_gain_tick > 0


@pytest.mark.asyncio
async def test_run_stops_on_low_gain() -> None:
    """plausibility=0 (well, 1 是 min) -> gain=1/5=0.2 也 >= 0.1。
    用 plausibility=1 + 一个软 trick: 改 GAIN_THRESHOLD via patch。"""
    from explain_engine.runtime import stop as stop_mod

    state = _state_with_2_frontiers()
    llm = FakeLLM(plausibility=1)   # gain = 0.2
    # patch threshold 高于 0.2
    original = stop_mod.GAIN_THRESHOLD
    stop_mod.GAIN_THRESHOLD = 0.5
    try:
        reason = await run(state, llm, budget=20)
        # 3 tick 没 gain → no_gain_for_3_ticks
        assert reason == "no_gain_for_3_ticks"
    finally:
        stop_mod.GAIN_THRESHOLD = original


@pytest.mark.asyncio
async def test_run_no_frontier_stops_immediately() -> None:
    g = ExplanationGraph(root_question="why")
    # 没有任何 level=1 节点
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    llm = FakeLLM()
    reason = await run(state, llm, budget=10)
    assert reason == "no_frontier_remaining"
    assert state.tick == 0


@pytest.mark.asyncio
async def test_run_calls_on_tick_callback() -> None:
    state = _state_with_2_frontiers()
    llm = FakeLLM(plausibility=4)
    seen: list[int] = []

    def on_tick(s: CognitiveState) -> None:
        seen.append(s.tick)

    await run(state, llm, budget=5, on_tick=on_tick)
    assert len(seen) == state.tick   # 每 tick 调一次


@pytest.mark.asyncio
async def test_run_does_not_set_converged() -> None:
    """Runtime.run 自身不改 stage (由 CLI 层负责)。"""
    state = _state_with_2_frontiers()
    llm = FakeLLM(plausibility=4)
    await run(state, llm, budget=5)
    # state 里没有 meta，stage 是 SessionMeta 的事
    # 这个 test 只是断言 run 返字符串 reason，不动 session meta
    assert isinstance(state.reasoning_trace, list)
```

## Step 2: 跑测试验证失败

Run: `pytest tests/test_runtime_run.py -v`
Expected: 全 FAIL (`ImportError: cannot import name 'run'`)。

## Step 3: 写 runtime.py

Create `src/explain_engine/runtime/runtime.py`:

```python
"""Phase 5 reasoning loop 主循环。

参考 docs/plans/2026-05-13-cognitive-engine-phase-5-design.md §5。

输入: state, llm, budget, on_tick (optional)
输出: stop_reason (str)
副作用:
  - state.tick / budget_remaining / last_gain_tick / reasoning_trace 更新
  - state.graph 通过 expand_one_frontier 长出新 d_NNN + causes edges
  - on_tick(state) 每 tick 调用 (可用作 SessionStore.save callback)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from explain_engine.engines import expansion
from explain_engine.llm.client import LLMClient
from explain_engine.runtime.scheduler import PhaseScheduler
from explain_engine.runtime.stop import GAIN_THRESHOLD, should_stop
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
    sched = scheduler or PhaseScheduler(K=4)

    while True:
        stop, reason = should_stop(state)
        if stop:
            assert reason is not None
            return reason

        action = sched.pick(state)
        target_id: str | None = None
        gain_delta = 0.0
        llm_calls = 0

        if action == "expand":
            frontier = state.graph.frontier_nodes()
            if frontier:
                target_id = frontier[0]
                _new_ids, gain_delta = await expansion.expand_one_frontier(
                    state, target_id, llm
                )
                llm_calls = 1
            else:
                # 不该到这: should_stop 会先触发 no_frontier。defensive：
                action = "evaluate"
        # action == "evaluate": no-op, snapshot only

        state.reasoning_trace.append(TraceEntry(
            tick=state.tick,
            action=action,
            target_node_id=target_id,
            gain_delta=gain_delta,
            llm_calls=llm_calls,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

        if gain_delta >= GAIN_THRESHOLD:
            state.last_gain_tick = state.tick

        state.tick += 1
        state.budget_remaining -= 1

        if on_tick is not None:
            on_tick(state)
```

## Step 4: 跑测试验证通过

Run: `pytest tests/test_runtime_run.py -v`
Expected: 7 个 PASS。

## Step 5: 跑全测试

Run: `pytest tests/ --tb=short`
Expected: ≥212 PASS。

Run: `ruff check src tests`
Expected: 0 errors。

## Step 6: Commit

```bash
git add tests/test_runtime_run.py src/explain_engine/runtime/runtime.py
git commit -m "$(cat <<'EOF'
runtime · Runtime.run 主循环 (Phase 5 reasoning loop)

while not should_stop(state):
  action = scheduler.pick(state)
  if expand: target = frontier[0]; (new_ids, gain) = expand_one_frontier(...)
  if evaluate: no-op snapshot
  state.reasoning_trace.append(TraceEntry(...))
  if gain >= GAIN_THRESHOLD: state.last_gain_tick = state.tick
  state.tick += 1; state.budget_remaining -= 1
  on_tick(state)   # 用于 CLI 层每 tick 落盘

returns stop_reason (str)

Runtime 不写 session.stage (留 CLI 层),不耦合 persistence (通过 on_tick callback)。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 5.9: CLI — `explain run` + `explain show --trace`

**目的**:
1. 新增 `explain run <session_id> [--budget 15]` 命令
2. 增强 `explain show <session_id> [--trace]` 渲染 reasoning_trace 表

**Files:**
- Modify: `src/explain_engine/cli.py` (加 `run` 命令 + 改 `show` 加 --trace flag)
- Create: `tests/test_cli_run.py`
- Create: `tests/test_cli_show_trace.py`

---

## Step 1: 写 `explain run` 失败测试

Create `tests/test_cli_run.py`:

```python
"""explain run <session_id> CLI 测试 (mock LLM)。"""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.llm.client import Response
from explain_engine.persistence.session import SessionStore


class FakeLLM:
    async def chat(self, messages, schema=None, model=None):
        return Response(
            text="x",
            parsed={"drivers": [
                {"name": "drv", "description": "d", "mechanism": "m", "plausibility": 4}
            ]},
            model="fake",
            usage={"input_tokens": 0, "output_tokens": 0},
        )


def _prepare_done_session(tmp_path: Path) -> str:
    """构造一个 stage=done 的 session 落盘,返 session_id。"""
    from explain_engine.persistence.session import Session, SessionMeta
    from explain_engine.schema.edges import RelationEdge
    from explain_engine.schema.graph import ExplanationGraph
    from explain_engine.schema.nodes import VariableNode
    from explain_engine.schema.state import CognitiveState

    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(id="p_001", name="p", description="d", abstraction_level=0,
                            confidence=0.8, epistemic="observation"))
    g.add_node(VariableNode(id="c_001", name="c", description="d", abstraction_level=1,
                            confidence=0.7, epistemic="insight"))
    g.add_edge(RelationEdge(id="e_001", source_node="c_001", target_node="p_001",
                            relation_type="manifests_as", confidence=0.7,
                            mechanism_description="m"))

    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    meta = SessionMeta(
        session_id="s_abcd1234", question="why", stage="done",
        created_at=1.0, updated_at=1.0,
    )
    store = SessionStore(tmp_path)
    store.save(Session(meta=meta, state=state))
    return "s_abcd1234"


def test_run_happy_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXPLAIN_SESSIONS_DIR", str(tmp_path))
    sid = _prepare_done_session(tmp_path)
    with patch("explain_engine.cli.make_llm_client", return_value=FakeLLM()):
        runner = CliRunner()
        result = runner.invoke(app, ["run", sid, "--budget", "3"])
    assert result.exit_code == 0, result.output
    # 重新 load 看 stage
    store = SessionStore(tmp_path)
    session = store.load(sid)
    assert session.meta.stage == "converged"
    assert session.state.tick >= 1
    assert len(session.state.reasoning_trace) >= 1


def test_run_session_not_found(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXPLAIN_SESSIONS_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["run", "s_nonexist"])
    assert result.exit_code == 1


def test_run_stage_not_done(tmp_path, monkeypatch) -> None:
    """stage=bootstrap_pending 重跑 run → exit 4。"""
    from explain_engine.persistence.session import Session, SessionMeta
    from explain_engine.schema.graph import ExplanationGraph
    from explain_engine.schema.state import CognitiveState

    monkeypatch.setenv("EXPLAIN_SESSIONS_DIR", str(tmp_path))
    g = ExplanationGraph(root_question="why")
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    meta = SessionMeta(
        session_id="s_aaaabbbb", question="why", stage="bootstrap_pending",
        created_at=1.0, updated_at=1.0,
    )
    store = SessionStore(tmp_path)
    store.save(Session(meta=meta, state=state))

    runner = CliRunner()
    result = runner.invoke(app, ["run", "s_aaaabbbb"])
    assert result.exit_code == 4


def test_run_already_converged(tmp_path, monkeypatch) -> None:
    """stage=converged 重跑 → exit 4。"""
    from explain_engine.persistence.session import Session, SessionMeta
    from explain_engine.schema.graph import ExplanationGraph
    from explain_engine.schema.state import CognitiveState

    monkeypatch.setenv("EXPLAIN_SESSIONS_DIR", str(tmp_path))
    g = ExplanationGraph(root_question="why")
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    meta = SessionMeta(
        session_id="s_aaaabbbb", question="why", stage="converged",
        created_at=1.0, updated_at=1.0,
    )
    store = SessionStore(tmp_path)
    store.save(Session(meta=meta, state=state))

    runner = CliRunner()
    result = runner.invoke(app, ["run", "s_aaaabbbb"])
    assert result.exit_code == 4
```

## Step 2: 跑测试验证失败

Run: `pytest tests/test_cli_run.py -v`
Expected: 全 FAIL (`No command "run"`)。

## Step 3: 在 cli.py 加 `run` 命令

Modify `src/explain_engine/cli.py` — 在 `compress` 命令旁加新命令。Read 现有 `compress` / `show` 命令找 pattern 参考。

新增命令骨架 (具体行号执行时定位):

```python
@app.command()
def run(
    session_id: str = typer.Argument(...),
    budget: int = typer.Option(15, "--budget", help="reasoning loop tick 上限"),
) -> None:
    """Phase 5 reasoning loop: 上溯 driver,自动收敛。

    session 必须 stage=done (Phase 4 HITL 2 完成后)。
    跑完 stage 变 converged。
    """
    import asyncio
    from explain_engine.config import make_llm_client
    from explain_engine.persistence.session import SessionStore
    from explain_engine.runtime.runtime import run as runtime_run

    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError:
        typer.echo(f"session {session_id!r} not found", err=True)
        raise typer.Exit(code=1)

    if session.meta.stage != "done":
        typer.echo(
            f"session stage={session.meta.stage!r}, must be 'done' to run "
            f"(先跑 explain compress)",
            err=True,
        )
        raise typer.Exit(code=4)

    llm = make_llm_client()

    def on_tick(_state) -> None:
        store.save(session)

    try:
        reason = asyncio.run(runtime_run(session.state, llm, budget=budget, on_tick=on_tick))
    except Exception as exc:
        typer.echo(f"runtime failed: {exc}", err=True)
        raise typer.Exit(code=1)

    session.meta.stage = "converged"
    store.save(session)

    typer.echo(f"Phase 5 run complete (reason={reason}, tick={session.state.tick})")
    typer.echo(f"graph: {len(session.state.graph.nodes)} nodes / {len(session.state.graph.edges)} edges")
    drivers = [nid for nid, n in session.state.graph.nodes.items() if n.abstraction_level == 2]
    typer.echo(f"driver layer: {len(drivers)} drivers added")
```

`_get_store()` 在 cli.py 现有 helper (复用 EXPLAIN_SESSIONS_DIR env var)。

## Step 4: 跑测试验证通过

Run: `pytest tests/test_cli_run.py -v`
Expected: 4 个 PASS。

## Step 5: 写 `explain show --trace` 失败测试

Create `tests/test_cli_show_trace.py`:

```python
"""explain show <session_id> --trace 渲染 reasoning_trace 表。"""

from pathlib import Path

from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState, TraceEntry


def _prepare_session_with_trace(tmp_path: Path) -> str:
    g = ExplanationGraph(root_question="why")
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    state.reasoning_trace.extend([
        TraceEntry(tick=0, action="expand", target_node_id="c_001",
                   gain_delta=0.8, llm_calls=1, timestamp="2026-05-13T10:00:00"),
        TraceEntry(tick=1, action="expand", target_node_id="c_002",
                   gain_delta=0.4, llm_calls=1, timestamp="2026-05-13T10:00:01"),
        TraceEntry(tick=2, action="evaluate", target_node_id=None,
                   gain_delta=0.0, llm_calls=0, timestamp="2026-05-13T10:00:02"),
    ])
    meta = SessionMeta(session_id="s_traced01", question="why", stage="converged",
                       created_at=1.0, updated_at=1.0)
    SessionStore(tmp_path).save(Session(meta=meta, state=state))
    return "s_traced01"


def test_show_without_trace_no_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXPLAIN_SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session_with_trace(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["show", sid])
    assert result.exit_code == 0
    # 不带 --trace 不渲染 tick 表
    assert "expand" not in result.output or "tick" not in result.output.lower()


def test_show_with_trace_renders_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXPLAIN_SESSIONS_DIR", str(tmp_path))
    sid = _prepare_session_with_trace(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["show", sid, "--trace"])
    assert result.exit_code == 0
    # 渲染 3 个 tick
    assert "expand" in result.output
    assert "evaluate" in result.output
    assert "c_001" in result.output
```

## Step 6: 跑测试验证失败

Run: `pytest tests/test_cli_show_trace.py -v`
Expected: `test_show_with_trace_renders_table` FAIL (no `--trace` flag)。

## Step 7: 改 `show` 命令加 --trace

Modify `src/explain_engine/cli.py` — `show` 命令加 `--trace` option:

```python
@app.command()
def show(
    session_id: str = typer.Argument(...),
    trace: bool = typer.Option(False, "--trace", help="渲染 reasoning_trace 表"),
) -> None:
    """显示 session 当前状态。--trace 增渲染 reasoning_trace。"""
    # ... 现有 show 逻辑 ...

    if trace:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        if not session.state.reasoning_trace:
            console.print("[dim](reasoning_trace 为空)[/dim]")
        else:
            t = Table(title="Reasoning Trace")
            t.add_column("tick"); t.add_column("action")
            t.add_column("target"); t.add_column("gain")
            t.add_column("llm calls"); t.add_column("timestamp")
            for e in session.state.reasoning_trace:
                t.add_row(
                    str(e.tick), e.action, e.target_node_id or "-",
                    f"{e.gain_delta:.2f}", str(e.llm_calls), e.timestamp,
                )
            console.print(t)
```

## Step 8: 跑测试验证通过

Run: `pytest tests/test_cli_show_trace.py -v`
Expected: 2 个 PASS。

## Step 9: 跑全测试 + ruff

Run: `pytest tests/ --tb=short`
Expected: ≥218 PASS。

Run: `ruff check src tests`
Expected: 0 errors。

## Step 10: Commit

```bash
git add tests/test_cli_run.py tests/test_cli_show_trace.py src/explain_engine/cli.py
git commit -m "$(cat <<'EOF'
cli · explain run + show --trace (Phase 5)

explain run <session_id> [--budget 15]:
- stage=done → 跑 Runtime.run → stage=converged
- 每 tick 通过 on_tick callback 落盘 (Ctrl-C 不丢)
- exit 4 if stage 不对; exit 1 if session 找不到 / runtime 异常

explain show <session_id> --trace:
- 加 --trace flag 渲染 reasoning_trace rich table
- 不加 --trace 时行为不变 (向后兼容)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 5.10: Acceptance smoke on s_f3beb777（手动）

**目的**: 跑 Phase 5 真实 LLM acceptance，验证:
1. `explain run s_f3beb777 --budget 15` 完整跑通
2. stage 变 `converged`
3. graph 长出 ≥3 d_NNN driver 节点
4. reasoning_trace 完整 (≥8 entry)
5. stop reason 合理
6. driver 名字定性扎实（人评，跟 Phase 4 wow demo 同标准）

写 acceptance evidence file + final commit。

**Files:**
- Run: `explain run s_f3beb777 --budget 15`
- Inspect: `explain show s_f3beb777 --trace`
- Create: `docs/plans/2026-05-13-cognitive-engine-phase-5-acceptance.md` (final report)

---

## Step 1: 备份 s_f3beb777

```bash
cp sessions/s_f3beb777.json sessions/s_f3beb777.phase4-snapshot.json
```

保留 Phase 4 终态 snapshot,便于对比 / 复跑。

## Step 2: 检查 .env 配置

```bash
cat .env
```

确认 `LLM_PROTOCOL / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL` 全配齐。建议先跑 cheap 模型 (deepseek-chat 通过 anthropic endpoint) 一遍 smoke,再决定要不要换 claude-opus-4-7。

## Step 3: 跑 Phase 5 reasoning loop

```bash
explain run s_f3beb777 --budget 15
```

Expected output:
```
Phase 5 run complete (reason=<budget_exhausted|no_gain_for_3_ticks|no_frontier_remaining>, tick=N)
graph: M nodes / E edges
driver layer: K drivers added
```

记录 N / M / E / K 数值,以及 stop reason。

## Step 4: 查看 trace + graph

```bash
explain show s_f3beb777 --trace
```

人工验证:
- [ ] reasoning_trace 完整 (N entry)
- [ ] expand / evaluate 节奏符合 K=4 (4 expand + 1 evaluate / round)
- [ ] driver 名字定性扎实 (e.g. 对 c_001 "绝对化价值框架", driver 可能是 "集体身份维系压力" / "传统继承机制" / "生存威胁知觉" 这类语义清晰的机制变量)
- [ ] 没有 "熵增" / "宇宙真理" 这种 cosmic 名词
- [ ] stop reason 合理 (不是因为 LLM error / crash)

## Step 5: 写 acceptance evidence file

Create `docs/plans/2026-05-13-cognitive-engine-phase-5-acceptance.md`:

```markdown
# Phase 5 Acceptance — s_f3beb777

**日期**: 2026-05-13 (Phase 5 完工后填实际日期)
**Session**: s_f3beb777 — "为什么宗教战争是最血腥的战争"
**Phase 4 入口**: stage=done, 5 candidate (c_001-c_005)
**Phase 5 出口**: stage=converged, K driver 节点, N tick reasoning_trace

## 跑法

```bash
explain run s_f3beb777 --budget 15
explain show s_f3beb777 --trace
```

LLM provider: <e.g. LLM_PROTOCOL=anthropic / LLM_MODEL=claude-opus-4-7 / LLM_BASE_URL=https://api.anthropic.com>

## 数据快照

- tick: N
- budget_remaining (终态): 0 / X
- stop reason: <budget_exhausted | no_gain_for_3_ticks | no_frontier_remaining>
- graph 终态: 12 concrete + 5 abstract + K driver = ?? nodes
- edges 终态: ?? manifests_as + ?? causes
- LLM call total: ≈ ??

## Driver candidates

| frontier (c_NNN) | driver (d_NNN)    | mechanism                      | plausibility |
|------------------|-------------------|--------------------------------|--------------|
| c_001 绝对化价值框架  | d_001 集体身份维系压力 | ...                            | 4            |
| c_001            | d_002 ...         | ...                            | 3            |
| c_002 超越性激励系统  | d_003 ...         | ...                            | 4            |
| ...              | ...               | ...                            | ...          |

## 验收点

- [ ] stage = "converged"
- [ ] ≥3 d_NNN driver
- [ ] reasoning_trace ≥8 entry, 每 tick 1 entry
- [ ] stop reason ∈ {budget_exhausted, no_gain_for_3_ticks, no_frontier_remaining}
- [ ] driver 名字定性扎实 (informal 人评)
- [ ] 没有 cosmic 哲学名词
- [ ] L1+L2+L3 测试 ≥30 PASS
- [ ] Phase 0-4 测试 (159) 不破
- [ ] ruff check 0 error
- [ ] Provider 重构后 LLM_PROTOCOL=anthropic + DeepSeek base_url 也能跑通

## 观察 (人评)

(填: Phase 5 driver 比 Phase 4 abstract "更深一层" 的语义体现; gain 变化趋势;
stop signal 触发时点; 任何意外行为)

## Phase 6 起点 (Q7=B observe-then-act 决策)

(填: Compression coverage 是否随 driver 加入提升; 是否需要 Phase 5 末尾
prompt iteration; K=4 / GAIN_THRESHOLD=0.1 是否合理)
```

## Step 6: 跑全测试 + ruff 一次

Run: `pytest tests/ --tb=short -q`
Expected: ≥218 PASS, Phase 0-4 不破。

Run: `ruff check src tests`
Expected: 0 errors。

## Step 7: 验证最终成绩单

Phase 5 design §10 验收 checklist 全部打勾。如有 fail，单独修 + commit。

## Step 8: Commit acceptance evidence

```bash
git add docs/plans/2026-05-13-cognitive-engine-phase-5-acceptance.md sessions/s_f3beb777.json sessions/s_f3beb777.phase4-snapshot.json
git commit -m "$(cat <<'EOF'
acceptance · Phase 5 wow demo evidence (s_f3beb777)

跑 explain run s_f3beb777 --budget 15:
- stage: done → converged
- graph: 12 concrete + 5 abstract + K driver
- reasoning_trace: N entry
- stop reason: <...>

driver candidates 名字定性扎实 (无 cosmic 名词):
- ...

Phase 4 → Phase 5 wow 跃迁: single-shot compress → 持续 thinking (multi-tick
expansion + auto convergence)。

Phase 4 snapshot 备份 s_f3beb777.phase4-snapshot.json。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 总结

Phase 5 plan 10 task, ~100 step. 节奏:
- Wave A (5.1-5.4): 4 task, 地基 (schema 字段 / last_gains / frontier_nodes / Provider 重构)
- Wave B (5.5-5.6): 2 task, ExpansionEngine (prompt + engine)
- Wave C (5.7-5.8): 2 task, Loop (Scheduler + Stop + Runtime)
- Wave D (5.9-5.10): 2 task, CLI + acceptance

预期总测试: 159 + 35 ≈ **194 PASS** (略低于 design §6.6 预估的 193, 因为 task 5.4 Provider 重构 rename 部分旧 test 而非新增)。

预期 LLM cost:
- Task 5.10 真 LLM run --budget 15: ≈ 12-15 LLM call (每 expand 1 call, evaluate 0 call)
- 比 Phase 4 acceptance (≈ 36 call) 便宜，因为 plausibility 自评合并在 expansion 内
