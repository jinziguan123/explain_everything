# Phase 1 Hardening Plan

> **For Claude:** Use after Task 1.5 of Phase 0+1+2 plan completes. Closes systematic issues raised across Task 1.1–1.5 reviewers.

**Goal:** 一次性把 Phase 1 reviewer 们提出的 9 项 cross-cutting 问题清掉，让 schema/persistence 层在进 Phase 2 (LLM client) 之前不留模糊地带。

**Scope:** schema/{nodes, edges, graph, state}, persistence/session, 对应 tests。

**Style:** 8 个独立 step，每 step 一个 commit (TDD 风格)。

**Branch:** `cognitive-engine-mvp` (latest: `aad6a8e`)

---

## Backlog → Step 映射

| Reviewer 来源 | 问题 | Step |
|---|---|---|
| Task 1.1, 1.2 | id-like 字段缺 `Field(min_length=1)` | Step 1 |
| Task 1.1, 1.2 | 缺 boundary value tests | Step 2 |
| Task 1.3 spec | 缺 mid-level (abstraction_level=1) 在 compression 中的测试 | Step 2 |
| Task 1.3 | `ExplanationGraph.nodes / edges` 公开可变 | Step 3 |
| Task 1.3, 1.4, 1.5 | `from_dict` 出错信息不友好（裸 KeyError）| Step 4 |
| Task 1.4 | dataclass `Stage` Literal / session_id / budget 运行时无校验 | Step 5 + 6 |
| Task 1.5 | `test_update_session_overwrites` 用了非法 stage `"in_progress"` | Step 5 |
| Task 1.5 | `SessionStore.save` 非原子写入 | Step 7 |
| Task 1.5 | `SessionStore.list` 遇到坏 JSON 直接 crash | Step 8 |

---

## Step 1: 给 id-like 字段加 `Field(min_length=1)`

**Files:**
- Modify: `src/explain_engine/schema/nodes.py`
- Modify: `src/explain_engine/schema/edges.py`
- Modify: `tests/test_schema_nodes.py` (add tests)
- Modify: `tests/test_schema_edges.py` (add tests)

**Changes to nodes.py:**

`VariableNode.id` 加 `Field(min_length=1)`:

```python
class VariableNode(BaseModel):
    id: str = Field(min_length=1)
    name: str
    description: str
    ...
```

**Changes to edges.py:**

`RelationEdge.id / source_node / target_node` 都加 `Field(min_length=1)`:

```python
class RelationEdge(BaseModel):
    id: str = Field(min_length=1)
    source_node: str = Field(min_length=1)
    target_node: str = Field(min_length=1)
    relation_type: RelationType
    confidence: float = Field(ge=0.0, le=1.0)
    mechanism_description: str = Field(min_length=1)

    @model_validator(mode="after")
    ...
```

**New tests in `tests/test_schema_nodes.py`** (append at end of `TestVariableNode`):

```python
    def test_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            VariableNode(
                id="",  # 空 id 应该被拒
                name="x",
                description="x",
                abstraction_level=0,
                confidence=0.5,
                epistemic="fact",
            )
```

**New tests in `tests/test_schema_edges.py`** (append at end of `TestRelationEdge`):

```python
    def test_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            RelationEdge(
                id="",
                source_node="n_001",
                target_node="n_002",
                relation_type="causes",
                confidence=0.5,
                mechanism_description="x",
            )

    def test_empty_source_target_rejected(self):
        with pytest.raises(ValidationError):
            RelationEdge(
                id="e_001",
                source_node="",
                target_node="n_002",
                relation_type="causes",
                confidence=0.5,
                mechanism_description="x",
            )
        with pytest.raises(ValidationError):
            RelationEdge(
                id="e_001",
                source_node="n_001",
                target_node="",
                relation_type="causes",
                confidence=0.5,
                mechanism_description="x",
            )
```

**Verify:**
```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_nodes.py tests/test_schema_edges.py -v
```

**Commit:**
```bash
git add src/explain_engine/schema/nodes.py src/explain_engine/schema/edges.py tests/test_schema_nodes.py tests/test_schema_edges.py
git commit -m "$(cat <<'EOF'
hardening · id-like 字段加 min_length=1

VariableNode.id / RelationEdge.id / source_node / target_node 全部
强制非空。+3 拒绝单测。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Step 2: Boundary value tests + mid-level abstraction test

**Files:**
- Modify: `tests/test_schema_nodes.py` (add tests)
- Modify: `tests/test_schema_edges.py` (add tests)
- Modify: `tests/test_schema_graph.py` (add tests)

**New tests in `tests/test_schema_nodes.py`** (append):

```python
    def test_confidence_boundary_values_accepted(self):
        # 0.0 和 1.0 (闭区间端点) 都应该被接受
        for c in [0.0, 1.0]:
            node = VariableNode(
                id="n_x",
                name="x",
                description="x",
                abstraction_level=0,
                confidence=c,
                epistemic="fact",
            )
            assert node.confidence == c

    def test_abstraction_level_all_values_accepted(self):
        for level in [0, 1, 2]:
            node = VariableNode(
                id=f"n_{level}",
                name="x",
                description="x",
                abstraction_level=level,  # type: ignore[arg-type]
                confidence=0.5,
                epistemic="fact",
            )
            assert node.abstraction_level == level
```

**New tests in `tests/test_schema_edges.py`** (append):

```python
    def test_confidence_boundary_values_accepted(self):
        for c in [0.0, 1.0]:
            edge = RelationEdge(
                id=f"e_{c}",
                source_node="n_001",
                target_node="n_002",
                relation_type="causes",
                confidence=c,
                mechanism_description="x",
            )
            assert edge.confidence == c
```

**New test in `tests/test_schema_graph.py`** (append to `TestExplanationGraph`):

```python
    def test_compression_score_counts_mid_level_too(self):
        """abstraction_level=1 (mid) 节点也参与 compression."""
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_mid", level=1))
        g.add_node(_node("n_con_a"))
        g.add_node(_node("n_con_b"))
        g.add_edge(_edge("e_1", "n_mid", "n_con_a"))
        g.add_edge(_edge("e_2", "n_mid", "n_con_b"))
        # mid 节点应该被 compression 计入
        assert g.compression_score() == 2.0
```

**Verify:**
```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_nodes.py tests/test_schema_edges.py tests/test_schema_graph.py -v
```

**Commit:**
```bash
git add tests/test_schema_nodes.py tests/test_schema_edges.py tests/test_schema_graph.py
git commit -m "$(cat <<'EOF'
hardening · 边界值 + mid-level 测试覆盖

- confidence 端点 0.0 / 1.0 显式接受测试 (nodes / edges)
- abstraction_level 全部 3 个合法值显式测试
- ExplanationGraph.compression_score 显式覆盖 abstraction_level=1
  (防 >=1 退化成 ==2)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Step 3: `ExplanationGraph.nodes / edges` 改成只读 view

**Files:**
- Modify: `src/explain_engine/schema/graph.py`
- Modify: `tests/test_schema_graph.py`

**Changes to graph.py:**

把 `self.nodes` / `self.edges` 改成 `self._nodes` / `self._edges`，对外暴露 `@property` 返回 `types.MappingProxyType` (read-only view)。

```python
"""ExplanationGraph — networkx.DiGraph 包装。"""

from types import MappingProxyType
from typing import Mapping

import networkx as nx

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode


class ExplanationGraph:
    def __init__(self, root_question: str) -> None:
        self.root_question = root_question
        self._g: nx.DiGraph = nx.DiGraph()
        self._nodes: dict[str, VariableNode] = {}
        self._edges: dict[str, RelationEdge] = {}

    @property
    def nodes(self) -> Mapping[str, VariableNode]:
        """只读 view。修改请走 add_node()。"""
        return MappingProxyType(self._nodes)

    @property
    def edges(self) -> Mapping[str, RelationEdge]:
        """只读 view。修改请走 add_edge()。"""
        return MappingProxyType(self._edges)

    def add_node(self, node: VariableNode) -> None:
        if node.id in self._nodes:
            raise ValueError(f"node {node.id} already exists")
        self._nodes[node.id] = node
        self._g.add_node(node.id)

    def add_edge(self, edge: RelationEdge) -> None:
        if edge.source_node not in self._nodes:
            raise ValueError(f"unknown node: {edge.source_node}")
        if edge.target_node not in self._nodes:
            raise ValueError(f"unknown node: {edge.target_node}")
        if edge.id in self._edges:
            raise ValueError(f"edge {edge.id} already exists")
        self._edges[edge.id] = edge
        self._g.add_edge(edge.source_node, edge.target_node, edge_id=edge.id)

    def compression_score(self) -> float:
        return float(
            sum(
                self._g.out_degree(nid)
                for nid, node in self._nodes.items()
                if node.abstraction_level >= 1
            )
        )

    def coverage_score(self) -> float:
        concretes = [nid for nid, n in self._nodes.items() if n.abstraction_level == 0]
        if not concretes:
            return 0.0
        covered = {
            nid
            for nid in concretes
            if any(
                pred for pred in self._g.predecessors(nid)
                if self._nodes[pred].abstraction_level >= 1
            )
        }
        return len(covered) / len(concretes)

    def frontier(self) -> list[str]:
        return sorted(
            nid
            for nid, n in self._nodes.items()
            if n.abstraction_level >= 1 and self._g.out_degree(nid) == 0
        )

    def to_dict(self) -> dict:
        return {
            "root_question": self.root_question,
            "nodes": {nid: n.model_dump() for nid, n in self._nodes.items()},
            "edges": {eid: e.model_dump() for eid, e in self._edges.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExplanationGraph":
        try:
            g = cls(root_question=d["root_question"])
            for nid, n in d["nodes"].items():
                g.add_node(VariableNode.model_validate(n))
            for eid, e in d["edges"].items():
                g.add_edge(RelationEdge.model_validate(e))
            return g
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid graph dict: {exc}") from exc
```

NOTE: 已经在 `from_dict` 加了 try/except 包装（属于 Step 4 一部分，提前到这里一起改，避免来回打 graph.py）。

**Changes to test_schema_graph.py:**

旧的 `g.nodes == {}` 比较改成 `dict(g.nodes) == {}` 或 `len(g.nodes) == 0`。注意 MappingProxyType 跟 dict 比较：`MappingProxyType({}) == {}` 是 True (Python 行为)，但显式 `dict(...)` 包一层最稳。

实际上 MappingProxyType 的 `__eq__` 委托给 underlying dict，所以 `g.nodes == {}` 仍然 work。无需改测试。

但要加 2 个新测试验证只读:

```python
    def test_nodes_is_read_only(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_001"))
        with pytest.raises(TypeError):
            g.nodes["n_002"] = _node("n_002")  # type: ignore[index]

    def test_edges_is_read_only(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_abs", level=2))
        g.add_node(_node("n_con"))
        g.add_edge(_edge("e_1", "n_abs", "n_con"))
        with pytest.raises(TypeError):
            g.edges["e_2"] = _edge("e_2", "n_abs", "n_con")  # type: ignore[index]
```

**Verify:**
```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_graph.py -v
```

**Commit:**
```bash
git add src/explain_engine/schema/graph.py tests/test_schema_graph.py
git commit -m "$(cat <<'EOF'
hardening · ExplanationGraph nodes/edges 改只读 view

- self.nodes / self.edges → self._nodes / self._edges (私有)
- @property + MappingProxyType 返回只读 view
- add_node / add_edge 是唯一写入入口
- from_dict 加 try/except 包成 ValueError("invalid graph dict")
- +2 单测验证只读行为

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Step 4: `from_dict` 友好错误信息 (CognitiveState + Session)

**Files:**
- Modify: `src/explain_engine/schema/state.py`
- Modify: `src/explain_engine/persistence/session.py`
- Modify: `tests/test_schema_state.py`
- Modify: `tests/test_persistence_session.py`

**Changes to state.py — `from_dict`:**

```python
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
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid state dict: {exc}") from exc
```

**Changes to session.py — `from_dict`:**

```python
    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        try:
            return cls(
                meta=SessionMeta(**d["meta"]),
                state=CognitiveState.from_dict(d["state"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"invalid session dict: {exc}") from exc
```

**New test in `tests/test_schema_state.py`** (append):

```python
    def test_from_dict_invalid_raises(self):
        with pytest.raises(ValueError, match="invalid state dict"):
            CognitiveState.from_dict({"graph": {}})  # 缺多个必需 key
```

**New test in `tests/test_persistence_session.py`** (append to TestSessionStore):

```python
    def test_load_invalid_json_raises(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        # 写一个非法 session 文件
        bad_path = tmp_sessions_dir / "s_00000000.json"
        bad_path.write_text('{"meta": {"session_id": "s_00000000"}, "state": "not a dict"}')
        with pytest.raises(ValueError, match="invalid session dict"):
            store.load("s_00000000")
```

**Verify:**
```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_state.py tests/test_persistence_session.py -v
```

**Commit:**
```bash
git add src/explain_engine/schema/state.py src/explain_engine/persistence/session.py tests/test_schema_state.py tests/test_persistence_session.py
git commit -m "$(cat <<'EOF'
hardening · from_dict 友好错误信息

CognitiveState / Session 的 from_dict 包 try/except，把裸 KeyError /
TypeError 包成 ValueError("invalid {state,session} dict: ...")，
便于调试损坏快照。+2 拒绝单测。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Step 5: `SessionMeta` `__post_init__` 校验 + 修测试

**Files:**
- Modify: `src/explain_engine/persistence/session.py`
- Modify: `tests/test_persistence_session.py`

**Changes to session.py:**

加 `__post_init__` 校验 `stage` 是合法值 + `session_id` 匹配 `s_<8hex>` 格式:

```python
import re

_SESSION_ID_RE = re.compile(r"^s_[0-9a-f]{8}$")
_VALID_STAGES = frozenset({"bootstrap_pending", "running", "finalize_pending", "done"})


@dataclass
class SessionMeta:
    session_id: str
    question: str
    stage: Stage
    created_at: float
    updated_at: float

    def __post_init__(self) -> None:
        if not _SESSION_ID_RE.match(self.session_id):
            raise ValueError(f"invalid session_id format: {self.session_id!r}")
        if self.stage not in _VALID_STAGES:
            raise ValueError(
                f"invalid stage: {self.stage!r}, must be one of {sorted(_VALID_STAGES)}"
            )

    @classmethod
    def new(cls, question: str) -> "SessionMeta":
        ...
```

**Changes to test_persistence_session.py:**

1. Fix `test_update_session_overwrites`: 把 `"in_progress"` 改成 `"running"` (合法 Stage 值)
2. 加新测试:

```python
    def test_invalid_stage_rejected(self):
        with pytest.raises(ValueError, match="invalid stage"):
            SessionMeta(
                session_id="s_00000000",
                question="why?",
                stage="bogus",  # type: ignore[arg-type]
                created_at=0.0,
                updated_at=0.0,
            )

    def test_invalid_session_id_format_rejected(self):
        with pytest.raises(ValueError, match="invalid session_id"):
            SessionMeta(
                session_id="bad-id",
                question="why?",
                stage="bootstrap_pending",
                created_at=0.0,
                updated_at=0.0,
            )
```

(放在 `TestSessionMeta` 类里)

**Verify:**
```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_persistence_session.py -v
```

**Commit:**
```bash
git add src/explain_engine/persistence/session.py tests/test_persistence_session.py
git commit -m "$(cat <<'EOF'
hardening · SessionMeta __post_init__ 校验

- stage 必须是 4 个合法 Literal 之一 (运行时强制)
- session_id 必须匹配 ^s_[0-9a-f]{8}$
- 修复旧测试 test_update_session_overwrites 中 stage="in_progress"
  (非法值) → "running"
- +2 拒绝单测

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Step 6: `CognitiveState` `__post_init__` 校验

**Files:**
- Modify: `src/explain_engine/schema/state.py`
- Modify: `tests/test_schema_state.py`

**Changes to state.py:**

```python
@dataclass
class CognitiveState:
    ...

    def __post_init__(self) -> None:
        if self.budget_remaining < 0:
            raise ValueError(f"budget_remaining must be >= 0, got {self.budget_remaining}")
        if self.tick < 0:
            raise ValueError(f"tick must be >= 0, got {self.tick}")
        if self.last_gain_tick < 0:
            raise ValueError(f"last_gain_tick must be >= 0, got {self.last_gain_tick}")
```

NOTE: `bootstrap()` 不需要改 (输入 `budget > 0`，其他默认 0)。

**New tests in `tests/test_schema_state.py`** (append):

```python
    def test_negative_budget_rejected(self):
        with pytest.raises(ValueError, match="budget_remaining"):
            CognitiveState(
                graph=ExplanationGraph(root_question="why?"),
                budget_remaining=-1,
                root_question="why?",
            )

    def test_negative_tick_rejected(self):
        with pytest.raises(ValueError, match="tick"):
            CognitiveState(
                graph=ExplanationGraph(root_question="why?"),
                budget_remaining=5,
                root_question="why?",
                tick=-1,
            )
```

**Verify:**
```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_state.py -v
```

**Commit:**
```bash
git add src/explain_engine/schema/state.py tests/test_schema_state.py
git commit -m "$(cat <<'EOF'
hardening · CognitiveState __post_init__ 校验

budget_remaining / tick / last_gain_tick 必须 >= 0。+2 拒绝单测。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Step 7: SessionStore.save 改原子写

**Files:**
- Modify: `src/explain_engine/persistence/session.py`
- Modify: `tests/test_persistence_session.py`

**Changes to session.py — `save`:**

```python
import os

class SessionStore:
    ...

    def save(self, session: Session) -> None:
        session.meta.updated_at = time.time()
        p = self._path(session.meta.session_id)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2))
        os.replace(tmp, p)
```

**New test in `tests/test_persistence_session.py`** (append to TestSessionStore):

```python
    def test_save_is_atomic_no_tmp_left(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        session = Session(
            meta=SessionMeta.new(question="why?"),
            state=CognitiveState.bootstrap("why?", budget=10),
        )
        store.save(session)
        # 不应该有 .tmp 文件残留
        tmps = list(tmp_sessions_dir.glob("*.tmp"))
        assert tmps == [], f"residual tmp files: {tmps}"
```

**Verify:**
```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_persistence_session.py -v
```

**Commit:**
```bash
git add src/explain_engine/persistence/session.py tests/test_persistence_session.py
git commit -m "$(cat <<'EOF'
hardening · SessionStore.save 改原子写

写到 {session_id}.json.tmp 再 os.replace。崩溃 / 中断不会留下半截
JSON。+1 单测验证写完无 .tmp 残留。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Step 8: SessionStore.list 跳过坏 JSON

**Files:**
- Modify: `src/explain_engine/persistence/session.py`
- Modify: `tests/test_persistence_session.py`

**Changes to session.py — `list`:**

```python
import logging

logger = logging.getLogger(__name__)


class SessionStore:
    ...

    def list(self) -> list[SessionMeta]:
        metas: list[SessionMeta] = []
        for p in self.directory.glob("s_*.json"):
            try:
                d = json.loads(p.read_text())
                metas.append(SessionMeta(**d["meta"]))
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                logger.warning("skipping unreadable session %s: %s", p.name, exc)
                continue
        metas.sort(key=lambda m: m.created_at, reverse=True)
        return metas
```

**New test in `tests/test_persistence_session.py`** (append to TestSessionStore):

```python
    def test_list_skips_corrupted_files(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        # 写一个好 session
        good = Session(
            meta=SessionMeta.new(question="good"),
            state=CognitiveState.bootstrap("good", budget=5),
        )
        store.save(good)
        # 写一个 corrupted 文件 (合法文件名但 JSON 损坏)
        bad = tmp_sessions_dir / "s_deadbeef.json"
        bad.write_text("{ corrupted")
        # list 不应该崩，只返回 good
        metas = store.list()
        assert len(metas) == 1
        assert metas[0].session_id == good.meta.session_id
```

**Verify (final full Phase 0+1 + hardening suite):**
```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest -v
```

**Commit:**
```bash
git add src/explain_engine/persistence/session.py tests/test_persistence_session.py
git commit -m "$(cat <<'EOF'
hardening · SessionStore.list 跳过损坏文件

遇到 json 解析失败 / 缺 meta / 非法 SessionMeta 字段时记 warning
跳过，不影响其他 session 的列出。+1 单测覆盖。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Hardening 完工验收

满足以下条件才算 hardening 完成:

- [ ] 8 commits added on `cognitive-engine-mvp` (1 per step)
- [ ] `uv run pytest -v` 全 PASS (Phase 0+1 32 个 + hardening 新增 ~13 个 ≈ 45+ tests)
- [ ] 不破坏任何已有测试 (Phase 0+1 32 tests 全部仍 pass)
- [ ] `uv run ruff check .` 0 error
- [ ] `git log --oneline | head -10` 顺序合理

---

## 不在本 hardening 范围

以下 reviewer 提的事项**不做**:

- ❌ 升级所有 dataclass 为 pydantic (大改，不必要 — `__post_init__` 已经够保护核心 invariant)
- ❌ Stage 状态机迁移强制 (e.g. `bootstrap_pending → running` 必须经 method) — 推到 Phase 3 引入 reasoning loop 时一起做
- ❌ `model_config = {"frozen": False}` 删除（reviewer 标记冗余但无害，留着）
- ❌ `record_gain` 改名 / `bootstrap` docstring (可读性 minor，不影响行为)
- ❌ 序列化 nodes/edges 时按 id 排序 (确定性 nice-to-have，YAGNI)
