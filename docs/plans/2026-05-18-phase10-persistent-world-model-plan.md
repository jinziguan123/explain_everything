# Phase 10 Persistent World Model Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `knowledge/variables.json` 跨 session Variable Lexicon — 高 fitness L1/L2 节点累积成 reusable abstractions, 新 session bootstrap 时 LLM 看 Top-K 作 prior.

**Architecture:** 新模块 `engines/lexicon.py` 提供 CRUD + flush + render API. `engines/bootstrap.py` 加 `lexicon` 参数. cli.py 在 3 个 stage→done / aclose 触发点 flush. 全 local JSON, atomic `.tmp`+rename, 无新 dep.

**Tech Stack:** Python 3.11+ / Pydantic v2 / pytest + pytest-asyncio / Typer / Rich / uv-managed venv

**Setup pre-flight:**
- 分支: `dev` (HEAD `9c9e104` — design doc 已 commit)
- 全测基线: `.venv/bin/python -m pytest -x` 应 679 PASS
- Lint: `.venv/bin/ruff check src/ tests/` 应 0
- Design 参考: [docs/plans/2026-05-18-phase10-persistent-world-model-design.md](2026-05-18-phase10-persistent-world-model-design.md)

---

## Wave 1 — Lexicon schema + CRUD (engines/lexicon.py 基础)

### Task 1: 实装 lexicon 基础 CRUD API

**Files:**
- Create: `src/explain_engine/engines/lexicon.py`
- Create: `tests/test_engines_lexicon.py`

**Step 1.1: 写 failing test — 基础 API 全套**

Create `tests/test_engines_lexicon.py`:

```python
"""Phase 10 Wave 1: Variable Lexicon CRUD tests."""

import json
import logging

import pytest

from explain_engine.engines.lexicon import (
    _compute_global_id,
    _load_lexicon,
    _now_iso,
    _save_lexicon,
    _should_promote,
    _upsert_var,
)
from explain_engine.schema.nodes import VariableNode


def _make_node(
    nid: str = "c_001",
    name: str = "长期不确定性",
    abstraction_level: int = 2,
    activation: float = 0.8,
    lifecycle_state: str = "active",
    epistemic: str = "insight",
) -> VariableNode:
    """Helper: 建 VariableNode 用于 test."""
    return VariableNode(
        id=nid,
        name=name,
        description=f"{name} 的描述",
        abstraction_level=abstraction_level,
        confidence=0.7,
        epistemic=epistemic,
        activation=activation,
        lifecycle_state=lifecycle_state,
    )


class TestComputeGlobalId:
    def test_same_inputs_yield_same_id(self):
        a = _compute_global_id("长期不确定性", "通常 cause 风险规避")
        b = _compute_global_id("长期不确定性", "通常 cause 风险规避")
        assert a == b

    def test_different_name_yields_different_id(self):
        a = _compute_global_id("长期不确定性", "通常 cause 风险规避")
        b = _compute_global_id("身份焦虑", "通常 cause 风险规避")
        assert a != b

    def test_different_mech_yields_different_id(self):
        a = _compute_global_id("长期不确定性", "通常 cause 风险规避")
        b = _compute_global_id("长期不确定性", "通常 cause 储蓄上升")
        assert a != b

    def test_id_format_is_v_plus_8_hex(self):
        gid = _compute_global_id("x", "y")
        assert gid.startswith("v_")
        assert len(gid) == 10  # "v_" + 8 hex char
        hex_part = gid[2:]
        assert all(c in "0123456789abcdef" for c in hex_part)


class TestLoadLexicon:
    def test_load_missing_file_returns_empty_schema(self, tmp_path):
        path = tmp_path / "knowledge" / "variables.json"
        # path 不存在, 父目录也不存在
        lexicon = _load_lexicon(path)
        assert lexicon["version"] == 1
        assert lexicon["variables"] == []
        assert "updated_at" in lexicon

    def test_load_valid_file_returns_parsed(self, tmp_path):
        path = tmp_path / "variables.json"
        path.write_text(json.dumps({
            "version": 1,
            "updated_at": "2026-05-18T00:00:00Z",
            "variables": [{"global_id": "v_abc12345", "name": "x"}],
        }))
        lexicon = _load_lexicon(path)
        assert lexicon["version"] == 1
        assert len(lexicon["variables"]) == 1
        assert lexicon["variables"][0]["name"] == "x"

    def test_load_corrupt_json_raises_with_path(self, tmp_path):
        path = tmp_path / "variables.json"
        path.write_text("{ not valid json")
        with pytest.raises(json.JSONDecodeError):
            _load_lexicon(path)


class TestSaveLexicon:
    def test_save_creates_parent_dir(self, tmp_path):
        path = tmp_path / "knowledge" / "variables.json"
        _save_lexicon(path, {"version": 1, "variables": []})
        assert path.exists()
        assert path.parent.exists()

    def test_save_writes_valid_json(self, tmp_path):
        path = tmp_path / "variables.json"
        lexicon = {
            "version": 1,
            "updated_at": "2026-05-18T00:00:00Z",
            "variables": [{"global_id": "v_x", "name": "测试"}],
        }
        _save_lexicon(path, lexicon)
        loaded = json.loads(path.read_text())
        assert loaded == lexicon

    def test_save_atomic_no_tmp_left(self, tmp_path):
        path = tmp_path / "variables.json"
        _save_lexicon(path, {"version": 1, "variables": []})
        # .tmp 应已 rename 走
        assert not (tmp_path / "variables.json.tmp").exists()


class TestShouldPromote:
    def test_l0_rejected(self):
        node = _make_node(abstraction_level=0)
        assert not _should_promote(node)

    def test_decayed_rejected(self):
        node = _make_node(lifecycle_state="decayed")
        assert not _should_promote(node)

    def test_stale_rejected(self):
        node = _make_node(lifecycle_state="stale")
        assert not _should_promote(node)

    def test_low_activation_rejected(self):
        node = _make_node(activation=0.3)
        assert not _should_promote(node)

    def test_l1_active_high_activation_accepted(self):
        node = _make_node(abstraction_level=1, activation=0.7, lifecycle_state="active")
        assert _should_promote(node)

    def test_l2_active_high_activation_accepted(self):
        node = _make_node(abstraction_level=2, activation=0.9, lifecycle_state="active")
        assert _should_promote(node)


class TestUpsertVar:
    def _empty_lexicon(self):
        return {"version": 1, "updated_at": _now_iso(), "variables": []}

    def test_new_var_added_with_reuse_count_1(self):
        lex = self._empty_lexicon()
        node = _make_node()
        _upsert_var(lex, node, "通常 cause 风险规避", "s_001")
        assert len(lex["variables"]) == 1
        v = lex["variables"][0]
        assert v["name"] == "长期不确定性"
        assert v["fitness"]["reuse_count"] == 1
        assert v["source_sessions"] == ["s_001"]

    def test_existing_var_new_sid_increments_count(self):
        lex = self._empty_lexicon()
        node = _make_node()
        _upsert_var(lex, node, "mech", "s_001")
        _upsert_var(lex, node, "mech", "s_002")
        assert len(lex["variables"]) == 1
        v = lex["variables"][0]
        assert v["fitness"]["reuse_count"] == 2
        assert set(v["source_sessions"]) == {"s_001", "s_002"}

    def test_existing_var_same_sid_idempotent(self):
        """同 session 多次 flush 不 ++ count."""
        lex = self._empty_lexicon()
        node = _make_node()
        _upsert_var(lex, node, "mech", "s_001")
        _upsert_var(lex, node, "mech", "s_001")
        _upsert_var(lex, node, "mech", "s_001")
        assert len(lex["variables"]) == 1
        v = lex["variables"][0]
        assert v["fitness"]["reuse_count"] == 1
        assert v["source_sessions"] == ["s_001"]

    def test_different_name_creates_separate_entries(self):
        lex = self._empty_lexicon()
        _upsert_var(lex, _make_node(name="A"), "mech", "s_001")
        _upsert_var(lex, _make_node(name="B"), "mech", "s_001")
        assert len(lex["variables"]) == 2


class TestNowIso:
    def test_format_iso8601(self):
        ts = _now_iso()
        # 形如 "2026-05-18T15:30:00Z" 或 "2026-05-18T15:30:00.123456Z"
        assert "T" in ts
        assert ts.endswith("Z") or ts[-6] in "+-"
```

**Step 1.2: 跑测试确认 fail**

Run: `.venv/bin/python -m pytest tests/test_engines_lexicon.py -v 2>&1 | head -30`
Expected: 全 FAIL — `ImportError: cannot import name ... from 'explain_engine.engines.lexicon'`.

**Step 1.3: 实装 engines/lexicon.py**

Create `src/explain_engine/engines/lexicon.py`:

```python
"""Phase 10 Variable Lexicon — cross-session 高 fitness L1/L2 abstractions.

knowledge/variables.json schema:
{
  "version": 1,
  "updated_at": "<ISO8601>",
  "variables": [
    {
      "global_id": "v_<8hex>",
      "name": str,
      "description": str,
      "abstraction_level": int (1 or 2),
      "epistemic": str,
      "fitness": {
        "reuse_count": int,
        "avg_essentialness": float,
        "avg_consistency": float,
        "first_seen_at": str (ISO8601),
        "last_seen_at": str (ISO8601),
      },
      "canonical_mechanism": str (1-line summary),
      "source_sessions": list[str]
    }
  ]
}

设计参考 docs/plans/2026-05-18-phase10-persistent-world-model-design.md.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from explain_engine.schema.nodes import VariableNode

SCHEMA_VERSION = 1


def _now_iso() -> str:
    """ISO8601 UTC, 'Z' suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compute_global_id(name: str, canonical_mechanism: str) -> str:
    """global_id = 'v_' + sha256(name + '::' + canonical_mechanism)[:8].

    name 或 canonical_mechanism 任一变 → 新 global_id (conservative split —
    宁可重复存, 不要 wrong merge).
    """
    s = f"{name}::{canonical_mechanism}".encode("utf-8")
    return "v_" + hashlib.sha256(s).hexdigest()[:8]


def _load_lexicon(path: Path) -> dict[str, Any]:
    """Load lexicon from JSON file. Missing file → empty schema."""
    if not path.exists():
        return {
            "version": SCHEMA_VERSION,
            "updated_at": _now_iso(),
            "variables": [],
        }
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def _save_lexicon(path: Path, lexicon: dict[str, Any]) -> None:
    """Atomic write: .tmp → rename. 同 StorageV2._write_atomic pattern."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(lexicon, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _should_promote(node: VariableNode) -> bool:
    """Phase 10 第一版 fitness filter.

    - skip L0 (observations 不进 lexicon)
    - skip non-active (stale/decayed)
    - skip activation < 0.5 (conservative threshold)
    """
    return (
        node.abstraction_level >= 1
        and node.lifecycle_state == "active"
        and node.activation >= 0.5
    )


def _upsert_var(
    lexicon: dict[str, Any],
    node: VariableNode,
    canonical_mechanism: str,
    sid: str,
) -> None:
    """Insert or update var entry. Idempotent w.r.t. (global_id, sid).

    新 var: append with reuse_count=1, source_sessions=[sid].
    已有 + 新 sid: ++ reuse_count, append sid.
    已有 + 同 sid: 仅 update last_seen_at (不 ++ count).
    """
    global_id = _compute_global_id(node.name, canonical_mechanism)
    entries = lexicon["variables"]
    existing = next((v for v in entries if v["global_id"] == global_id), None)

    now = _now_iso()

    if existing is None:
        entries.append({
            "global_id": global_id,
            "name": node.name,
            "description": node.description,
            "abstraction_level": node.abstraction_level,
            "epistemic": node.epistemic,
            "fitness": {
                "reuse_count": 1,
                "avg_essentialness": node.activation,  # Phase 10 第一版 proxy
                "avg_consistency": node.stability,  # Phase 10 第一版 proxy
                "first_seen_at": now,
                "last_seen_at": now,
            },
            "canonical_mechanism": canonical_mechanism,
            "source_sessions": [sid],
        })
        return

    if sid in existing["source_sessions"]:
        # 同 session 重复 flush — 仅 update last_seen
        existing["fitness"]["last_seen_at"] = now
        return

    # 新 sid → ++ reuse_count
    existing["source_sessions"].append(sid)
    fitness = existing["fitness"]
    new_count = fitness["reuse_count"] + 1
    # Running avg: new_avg = (old_avg * old_count + new_value) / new_count
    fitness["avg_essentialness"] = (
        fitness["avg_essentialness"] * fitness["reuse_count"] + node.activation
    ) / new_count
    fitness["avg_consistency"] = (
        fitness["avg_consistency"] * fitness["reuse_count"] + node.stability
    ) / new_count
    fitness["reuse_count"] = new_count
    fitness["last_seen_at"] = now
```

**Step 1.4: 跑测试确认 pass**

Run: `.venv/bin/python -m pytest tests/test_engines_lexicon.py -v`
Expected: 全 PASS (21 test).

**Step 1.5: 全测 + ruff**

Run: `.venv/bin/python -m pytest -x --tb=no -q 2>&1 | tail -3`
Expected: `700 passed` (679 + 21).

Run: `.venv/bin/ruff check src/explain_engine/engines/lexicon.py tests/test_engines_lexicon.py`
Expected: 0.

**Step 1.6: Commit**

```bash
git add src/explain_engine/engines/lexicon.py tests/test_engines_lexicon.py
git commit -m "$(cat <<'EOF'
engines/lexicon · Phase 10 Wave 1 — Variable Lexicon CRUD 基础

新 module engines/lexicon.py: SCHEMA_VERSION=1, _now_iso, _compute_global_id
(sha256[:8] of name + canonical_mech), _load_lexicon (空 path 返空 schema),
_save_lexicon (atomic .tmp+rename, 同 StorageV2 pattern), _should_promote
(skip L0/non-active/activation<0.5), _upsert_var (新 var / 新 sid ++count /
同 sid idempotent).

avg_essentialness/avg_consistency 用 running avg, Phase 10 第一版用
node.activation/stability 作 proxy (TODO 后续从 acceptance_report 取真值).

21 unit test 覆盖.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 2 — flush_to_lexicon + canonical_mechanism

### Task 2: 实装 flush_to_lexicon 主函数 + canonical_mechanism 生成

**Files:**
- Modify: `src/explain_engine/engines/lexicon.py` (加 `_build_canonical_mechanism` + `flush_to_lexicon`)
- Modify: `tests/test_engines_lexicon.py` (加 TestBuildCanonicalMechanism + TestFlushToLexicon class)

**Step 2.1: 写 failing test**

加到 `tests/test_engines_lexicon.py` 末尾:

```python
from unittest.mock import AsyncMock, MagicMock

from explain_engine.engines.lexicon import (
    _build_canonical_mechanism,
    flush_to_lexicon,
)
from explain_engine.persistence.session import Session, SessionMeta
from explain_engine.persistence.storage_v2 import StorageV2
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState


def _make_state_with_l2(
    name: str = "长期不确定性",
    add_edges: bool = True,
) -> CognitiveState:
    """单 L2 (active high activation) + 1 L1 incoming + 1 L0 outgoing manifest."""
    g = ExplanationGraph(root_question="why?")
    g.add_node(VariableNode(
        id="d_001", name=name, description="root driver",
        abstraction_level=2, confidence=0.8, epistemic="insight",
        activation=0.9, lifecycle_state="active",
    ))
    g.add_node(VariableNode(
        id="c_001", name="风险规避", description="mid",
        abstraction_level=1, confidence=0.7, epistemic="inference",
        activation=0.7, lifecycle_state="active",
    ))
    g.add_node(VariableNode(
        id="p_001", name="储蓄率上升", description="L0 obs",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    if add_edges:
        g.add_edge(RelationEdge(
            id="e_001", source_node="d_001", target_node="c_001",
            relation_type="causes", confidence=0.7,
            mechanism_description="不确定性 → 风险规避",
        ))
        g.add_edge(RelationEdge(
            id="e_002", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.7,
            mechanism_description="...",
        ))
    return CognitiveState(graph=g, budget_remaining=10, root_question="why?")


class TestBuildCanonicalMechanism:
    @pytest.mark.asyncio
    async def test_with_llm_returns_llm_output(self):
        state = _make_state_with_l2()
        node = state.graph.nodes["d_001"]
        session = Session(meta=SessionMeta.new(question="why?"), state=state)

        mock_llm = AsyncMock()
        from explain_engine.llm.client import Response
        mock_llm.chat = AsyncMock(return_value=Response(
            text="通常 cause 风险规避; 由社会结构性压力 cause",
            parsed=None,
            model="mock",
            usage={"input_tokens": 0, "output_tokens": 0},
        ))

        mech = await _build_canonical_mechanism(node, session, mock_llm)
        assert "风险规避" in mech or "压力" in mech

    @pytest.mark.asyncio
    async def test_no_llm_uses_edge_fallback(self):
        state = _make_state_with_l2()
        node = state.graph.nodes["d_001"]
        session = Session(meta=SessionMeta.new(question="why?"), state=state)

        mech = await _build_canonical_mechanism(node, session, None)
        # Fallback 应含 incoming/outgoing 边的目标 name
        # d_001 outgoing causes 到 c_001 (风险规避), 无 incoming
        assert "风险规避" in mech

    @pytest.mark.asyncio
    async def test_llm_error_falls_back(self):
        from explain_engine.llm.errors import LLMError
        state = _make_state_with_l2()
        node = state.graph.nodes["d_001"]
        session = Session(meta=SessionMeta.new(question="why?"), state=state)

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(side_effect=LLMError("api down"))

        mech = await _build_canonical_mechanism(node, session, mock_llm)
        # Fallback 路径
        assert "风险规避" in mech


class TestFlushToLexicon:
    @pytest.mark.asyncio
    async def test_promotes_l1_l2_only(self, monkeypatch):
        state = _make_state_with_l2()
        meta = SessionMeta.new(question="why?")
        meta.session_id = "s_flush001"
        session = Session(meta=meta, state=state)

        promoted = await flush_to_lexicon(
            session, StorageV2(), llm=None,
        )
        # d_001 (L2 active 0.9) + c_001 (L1 active 0.7) → 2
        assert promoted == 2

        # 验 lexicon 内 L0 没进
        path = StorageV2().knowledge_dir() / "variables.json"
        lex = _load_lexicon(path)
        names = {v["name"] for v in lex["variables"]}
        assert names == {"长期不确定性", "风险规避"}

    @pytest.mark.asyncio
    async def test_skips_decayed(self):
        state = _make_state_with_l2()
        state.graph.nodes["d_001"].lifecycle_state = "decayed"
        meta = SessionMeta.new(question="why?")
        meta.session_id = "s_flush002"
        session = Session(meta=meta, state=state)

        promoted = await flush_to_lexicon(session, StorageV2(), llm=None)
        assert promoted == 1  # 仅 c_001

    @pytest.mark.asyncio
    async def test_idempotent_same_sid(self):
        state = _make_state_with_l2()
        meta = SessionMeta.new(question="why?")
        meta.session_id = "s_flush003"
        session = Session(meta=meta, state=state)

        await flush_to_lexicon(session, StorageV2(), llm=None)
        await flush_to_lexicon(session, StorageV2(), llm=None)
        await flush_to_lexicon(session, StorageV2(), llm=None)

        path = StorageV2().knowledge_dir() / "variables.json"
        lex = _load_lexicon(path)
        # 同 sid 3 次 flush, reuse_count 仍 1
        for v in lex["variables"]:
            assert v["fitness"]["reuse_count"] == 1
            assert v["source_sessions"] == ["s_flush003"]
```

**Step 2.2: 跑测试确认 fail**

Run: `.venv/bin/python -m pytest tests/test_engines_lexicon.py::TestBuildCanonicalMechanism tests/test_engines_lexicon.py::TestFlushToLexicon -v`
Expected: FAIL — `_build_canonical_mechanism` / `flush_to_lexicon` 不存在.

**Step 2.3: 实装 _build_canonical_mechanism + flush_to_lexicon**

加到 `src/explain_engine/engines/lexicon.py` 末尾 (在 `_upsert_var` 之后):

```python
async def _build_canonical_mechanism(
    node: VariableNode,
    session,  # Session, 避 circular import 不 annotate
    llm,  # LLMClient | None
) -> str:
    """生 canonical_mechanism 1-line summary.

    有 llm: 调 LLM 用 node + neighbors 信息 prompt 出 1 句话.
    无 llm 或 llm error: edge-based fallback —
      "通常 cause [outgoing target names]; 由 [incoming source names] cause".
    """
    g = session.state.graph
    nid = node.id

    # 收集 edge neighbors
    outgoing = [
        g.nodes[e.target_node].name
        for e in g.edges.values()
        if e.source_node == nid and e.target_node in g.nodes
    ]
    incoming = [
        g.nodes[e.source_node].name
        for e in g.edges.values()
        if e.target_node == nid and e.source_node in g.nodes
    ]

    def _fallback() -> str:
        parts = []
        if outgoing:
            parts.append(f"通常 cause {', '.join(outgoing[:3])}")
        if incoming:
            parts.append(f"由 {', '.join(incoming[:3])} cause")
        return "; ".join(parts) if parts else f"{node.name} (无 edge 上下文)"

    if llm is None:
        return _fallback()

    prompt = (
        f"Variable: {node.name} (L{node.abstraction_level})\n"
        f"Description: {node.description}\n"
        f"Outgoing (causes): {', '.join(outgoing) if outgoing else '(none)'}\n"
        f"Incoming (caused by): {', '.join(incoming) if incoming else '(none)'}\n\n"
        "请用 1 句中文 (<60 字) 总结它的 canonical mechanism, "
        "格式: '通常 cause X; 由 Y cause'. 仅输 1 行, 无解释."
    )
    try:
        from explain_engine.llm.client import Response  # noqa: F401
        response = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            schema=None,
        )
        text = (response.text or "").strip()
        if not text:
            return _fallback()
        # cap 1 line + 100 chars
        first_line = text.splitlines()[0][:100]
        return first_line
    except Exception:
        return _fallback()


async def flush_to_lexicon(
    session,  # Session, 避 circular import
    storage,  # StorageV2
    llm=None,  # LLMClient | None
) -> int:
    """Promote 高 fitness var 进 lexicon. 返 promoted count.

    Idempotent w.r.t. session_id (同 sid 多次调安全, 不 ++ count).
    """
    path = storage.knowledge_dir() / "variables.json"
    lexicon = _load_lexicon(path)
    promoted = 0

    for nid, node in session.state.graph.nodes.items():
        if not _should_promote(node):
            continue
        canonical_mech = await _build_canonical_mechanism(node, session, llm)
        _upsert_var(lexicon, node, canonical_mech, session.meta.session_id)
        promoted += 1

    if promoted > 0:
        lexicon["updated_at"] = _now_iso()
        _save_lexicon(path, lexicon)

    return promoted
```

**Step 2.4: 跑测试 pass**

Run: `.venv/bin/python -m pytest tests/test_engines_lexicon.py -v`
Expected: 全 PASS (22 + ~6 = ~28 test).

**Step 2.5: 全测 + ruff**

Run: `.venv/bin/python -m pytest -x --tb=no -q 2>&1 | tail -3`
Expected: ~707 PASS (679 + 28).

Run: `.venv/bin/ruff check src/explain_engine/engines/lexicon.py tests/test_engines_lexicon.py`
Expected: 0.

**Step 2.6: Commit**

```bash
git add src/explain_engine/engines/lexicon.py tests/test_engines_lexicon.py
git commit -m "$(cat <<'EOF'
engines/lexicon · Phase 10 Wave 2 — flush_to_lexicon + canonical_mechanism

flush_to_lexicon(session, storage, llm) 遍 session graph 跑 _should_promote
filter, 对每个保留 node 调 _build_canonical_mechanism 生 1-line summary,
然后 _upsert_var. 返 promoted count.

_build_canonical_mechanism: 有 llm 时 prompt LLM 用 node + incoming/outgoing
edge names 生 "通常 cause X; 由 Y cause" 格式; 无 llm 或 LLMError 时
edge-based fallback.

~6 test (mock llm 正常路径 / 无 llm fallback / LLMError fallback /
flush L1+L2 only / skip decayed / 同 sid 幂等).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 3 — bootstrap_phenomena + lexicon prior

### Task 3: bootstrap 接 lexicon + Top-K render

**Files:**
- Modify: `src/explain_engine/engines/lexicon.py` (加 `_select_top_k_vars` + `_render_lexicon_for_prompt`)
- Modify: `src/explain_engine/engines/bootstrap.py` (加 `lexicon` + `lexicon_top_k` 参数)
- Modify: `tests/test_engines_lexicon.py` (加 TestSelectTopK + TestRenderLexicon)
- Modify: `tests/test_engines_bootstrap.py` (加 ~3 test)

**Step 3.1: 验证 bootstrap_phenomena 当前签名**

Run: `grep -n "async def bootstrap_phenomena\|^def bootstrap_phenomena" /Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/bootstrap.py`
记起始行号 + 当前签名.

Read bootstrap.py 头 50 行了解 prompt 构造方式 (有 prompt template 字符串).

**Step 3.2: 写 failing test — select_top_k + render**

加到 `tests/test_engines_lexicon.py` 末尾:

```python
from explain_engine.engines.lexicon import (
    _render_lexicon_for_prompt,
    _select_top_k_vars,
)


def _make_var_entry(
    global_id: str,
    name: str,
    reuse_count: int,
    avg_essentialness: float = 0.5,
    abstraction_level: int = 2,
    description: str = "desc",
    canonical_mechanism: str = "通常 cause X",
) -> dict:
    return {
        "global_id": global_id,
        "name": name,
        "description": description,
        "abstraction_level": abstraction_level,
        "epistemic": "insight",
        "fitness": {
            "reuse_count": reuse_count,
            "avg_essentialness": avg_essentialness,
            "avg_consistency": 0.5,
            "first_seen_at": "2026-05-13T00:00:00Z",
            "last_seen_at": "2026-05-18T00:00:00Z",
        },
        "canonical_mechanism": canonical_mechanism,
        "source_sessions": ["s_001"],
    }


class TestSelectTopK:
    def test_empty_lexicon_returns_empty(self):
        lex = {"variables": []}
        assert _select_top_k_vars(lex, k=10) == []

    def test_k_zero_returns_empty(self):
        lex = {"variables": [_make_var_entry("v_001", "A", 5)]}
        assert _select_top_k_vars(lex, k=0) == []

    def test_k_larger_than_total_returns_all(self):
        lex = {"variables": [
            _make_var_entry("v_001", "A", 1),
            _make_var_entry("v_002", "B", 1),
        ]}
        assert len(_select_top_k_vars(lex, k=10)) == 2

    def test_composite_score_descending(self):
        """reuse_count × (essentialness + 0.1) 排序."""
        lex = {"variables": [
            _make_var_entry("v_low", "low", reuse_count=1, avg_essentialness=0.1),
            _make_var_entry("v_high", "high", reuse_count=5, avg_essentialness=0.9),
            _make_var_entry("v_mid", "mid", reuse_count=3, avg_essentialness=0.5),
        ]}
        result = _select_top_k_vars(lex, k=3)
        assert result[0]["name"] == "high"  # 5 × 1.0 = 5.0
        assert result[1]["name"] == "mid"   # 3 × 0.6 = 1.8
        assert result[2]["name"] == "low"   # 1 × 0.2 = 0.2

    def test_zero_essentialness_not_completely_zeroed(self):
        """+0.1 fallback 防 essentialness=0 排不上."""
        lex = {"variables": [
            _make_var_entry("v_high_e", "high_e", reuse_count=1, avg_essentialness=0.0),
            _make_var_entry("v_low_e", "low_e", reuse_count=2, avg_essentialness=0.0),
        ]}
        result = _select_top_k_vars(lex, k=2)
        assert result[0]["name"] == "low_e"  # 2 × 0.1 = 0.2 > 1 × 0.1 = 0.1


class TestRenderLexicon:
    def test_empty_returns_empty_string(self):
        assert _render_lexicon_for_prompt([]) == ""

    def test_single_var_contains_essentials(self):
        v = _make_var_entry("v_a3f2c891", "长期不确定性", reuse_count=3)
        out = _render_lexicon_for_prompt([v])
        assert "v_a3f2c891" in out
        assert "长期不确定性" in out
        assert "L2" in out
        assert "reused 3" in out

    def test_long_desc_capped(self):
        v = _make_var_entry(
            "v_x", "x", reuse_count=1,
            description="a" * 200,  # 长 desc
        )
        out = _render_lexicon_for_prompt([v])
        # 不应渲染全 200 char
        assert out.count("a") < 100

    def test_long_mech_capped(self):
        v = _make_var_entry(
            "v_x", "x", reuse_count=1,
            canonical_mechanism="b" * 200,
        )
        out = _render_lexicon_for_prompt([v])
        assert out.count("b") < 80

    def test_chinese_no_garbled(self):
        v = _make_var_entry("v_x", "中文测试", reuse_count=1)
        out = _render_lexicon_for_prompt([v])
        assert "中文测试" in out
        assert "\\u" not in out  # 不应 escape Unicode

    def test_includes_disclaimer(self):
        v = _make_var_entry("v_x", "x", reuse_count=1)
        out = _render_lexicon_for_prompt([v])
        # 应含 "不强制" 之类提示让 LLM 知道是 optional prior
        assert "不强制" in out or "仅供参考" in out
```

**Step 3.3: 写 failing test — bootstrap 接 lexicon**

加到 `tests/test_engines_bootstrap.py` 末尾 (verify 文件存在; 若不存在 grep find 之):

```python
class TestBootstrapWithLexicon:
    @pytest.mark.asyncio
    async def test_lexicon_none_backward_compat(self, mock_llm_response):
        """lexicon=None 时 prompt 不含 prior section, 行为同老版."""
        from explain_engine.engines.bootstrap import bootstrap_phenomena

        mock_llm = AsyncMock()
        captured_messages = []

        async def _capture(messages, schema=None):
            captured_messages.append(messages)
            return mock_llm_response({"phenomena": [
                {"name": "x", "description": "y"},
            ]})

        mock_llm.chat = _capture

        await bootstrap_phenomena("why?", mock_llm, lexicon=None)
        prompt_text = str(captured_messages[0])
        assert "reusable abstractions" not in prompt_text

    @pytest.mark.asyncio
    async def test_lexicon_empty_list_same_as_none(self, mock_llm_response):
        """lexicon=[] 时 prompt 也不含 prior section."""
        from explain_engine.engines.bootstrap import bootstrap_phenomena

        mock_llm = AsyncMock()
        captured = []

        async def _capture(messages, schema=None):
            captured.append(messages)
            return mock_llm_response({"phenomena": [
                {"name": "x", "description": "y"},
            ]})

        mock_llm.chat = _capture

        await bootstrap_phenomena("why?", mock_llm, lexicon=[])
        prompt_text = str(captured[0])
        assert "reusable abstractions" not in prompt_text

    @pytest.mark.asyncio
    async def test_lexicon_attached_to_prompt(self, mock_llm_response):
        """lexicon 非空时 prompt 含 prior section + var name."""
        from explain_engine.engines.bootstrap import bootstrap_phenomena

        lexicon = [{
            "global_id": "v_a3f2c891",
            "name": "长期不确定性",
            "description": "long-term uncertainty",
            "abstraction_level": 2,
            "epistemic": "insight",
            "fitness": {
                "reuse_count": 3,
                "avg_essentialness": 0.8,
                "avg_consistency": 0.7,
                "first_seen_at": "2026-05-13T00:00:00Z",
                "last_seen_at": "2026-05-18T00:00:00Z",
            },
            "canonical_mechanism": "通常 cause 风险规避",
            "source_sessions": ["s_001"],
        }]

        mock_llm = AsyncMock()
        captured = []

        async def _capture(messages, schema=None):
            captured.append(messages)
            return mock_llm_response({"phenomena": [
                {"name": "x", "description": "y"},
            ]})

        mock_llm.chat = _capture

        await bootstrap_phenomena("why?", mock_llm, lexicon=lexicon)
        prompt_text = str(captured[0])
        assert "长期不确定性" in prompt_text
        assert "v_a3f2c891" in prompt_text
```

**Step 3.4: 跑测试 fail**

Run: `.venv/bin/python -m pytest tests/test_engines_lexicon.py::TestSelectTopK tests/test_engines_lexicon.py::TestRenderLexicon tests/test_engines_bootstrap.py::TestBootstrapWithLexicon -v`
Expected: FAIL — `_select_top_k_vars` / `_render_lexicon_for_prompt` / bootstrap kwargs 不存在.

**Step 3.5: 实装 _select_top_k_vars + _render_lexicon_for_prompt**

加到 `src/explain_engine/engines/lexicon.py` 末尾:

```python
def _select_top_k_vars(lexicon: dict[str, Any], k: int = 20) -> list[dict[str, Any]]:
    """Composite fitness rank: reuse_count × (avg_essentialness + 0.1).

    +0.1 防 essentialness=0 (新 var 未跑过 acceptance) 时全 0 排不上.
    Phase 10 第一版 deterministic; Candidate E 上 embedding 后改 query-relevance.
    """
    if k <= 0:
        return []
    variables = lexicon.get("variables", [])

    def _score(v: dict[str, Any]) -> float:
        f = v["fitness"]
        return f["reuse_count"] * (f["avg_essentialness"] + 0.1)

    return sorted(variables, key=_score, reverse=True)[:k]


def _render_lexicon_for_prompt(vars: list[dict[str, Any]]) -> str:
    """Render Top-K vars as prompt prior section.

    单 var ~80 token: name + level + reuse + 1-line desc (cap 80 char) +
    1-line mech (cap 60 char).
    """
    if not vars:
        return ""

    lines = [
        "# 已知 reusable abstractions (来自历史 session, 仅供参考)",
        "",
    ]
    for v in vars:
        level = f"L{v['abstraction_level']}"
        reuse = v["fitness"]["reuse_count"]
        desc = v["description"][:80]
        mech = v["canonical_mechanism"][:60]
        lines.append(
            f"- {v['global_id']} 「{v['name']}」({level}, reused {reuse}x): "
            f"{desc} — {mech}"
        )
    lines.append("")
    lines.append("(若新问题涉及上述抽象, expand/compress 阶段可引用. 不强制使用.)")
    return "\n".join(lines)
```

**Step 3.6: 改 bootstrap_phenomena 接 lexicon**

读 `src/explain_engine/engines/bootstrap.py` 现状. 改 `bootstrap_phenomena` 签名 + prompt 拼接:

(给 implementer: 具体改法 — 在现有 prompt 构造之后 append prior section. 不破坏现有 logic.)

```python
# src/explain_engine/engines/bootstrap.py 修改 (示例)
async def bootstrap_phenomena(
    question: str,
    llm,  # LLMClient
    lexicon: list[dict] | None = None,
    lexicon_top_k: int = 20,
) -> list[VariableNode]:
    """Phase 10 加 lexicon prior 参数. lexicon=None/[] 时行为不变."""
    # Phase 10 lexicon prior section
    prior_section = ""
    if lexicon:
        from explain_engine.engines.lexicon import (
            _render_lexicon_for_prompt,
            _select_top_k_vars,
        )
        # lexicon arg 是 raw list (caller 传 lexicon["variables"]) — 包成 dict 调
        top_k = _select_top_k_vars({"variables": lexicon}, k=lexicon_top_k)
        prior_section = _render_lexicon_for_prompt(top_k)

    # 现有 prompt 构造逻辑 ... + 末尾 append prior_section
    # 假设原 prompt 是 user_message 字符串:
    user_message = _existing_prompt_template(question)
    if prior_section:
        user_message = user_message + "\n\n" + prior_section

    # rest of bootstrap ...
```

注: 具体 prompt template 改法依赖现有 bootstrap.py 结构. implementer Read 现有 code 后决定 splice 点 (一般 在 user prompt 末尾, system prompt 不动).

**Step 3.7: 跑测试 pass**

Run: `.venv/bin/python -m pytest tests/test_engines_lexicon.py tests/test_engines_bootstrap.py -v`
Expected: 全 PASS.

**Step 3.8: 全测 + ruff**

Run: `.venv/bin/python -m pytest -x --tb=no -q 2>&1 | tail -3`
Expected: ~717 PASS (707 + ~10).

Run: `.venv/bin/ruff check src/explain_engine/engines/lexicon.py src/explain_engine/engines/bootstrap.py tests/test_engines_lexicon.py tests/test_engines_bootstrap.py`
Expected: 0.

**Step 3.9: Commit**

```bash
git add src/explain_engine/engines/lexicon.py src/explain_engine/engines/bootstrap.py tests/test_engines_lexicon.py tests/test_engines_bootstrap.py
git commit -m "$(cat <<'EOF'
engines/lexicon + bootstrap · Phase 10 Wave 3 — bootstrap 接 lexicon prior

_select_top_k_vars: composite fitness rank (reuse_count × (avg_essentialness
+ 0.1)) sort desc, +0.1 防 essentialness=0 时排不上.

_render_lexicon_for_prompt: 单 var ~80 token (name + L1/L2 + reused Nx +
1-line desc cap 80 char + 1-line mech cap 60 char). 20 var ≈ 1.7k token.
含 "不强制使用" disclaimer 让 LLM 知道 prior 是 optional.

bootstrap_phenomena 加 lexicon + lexicon_top_k 参数, lexicon=None/[] 时
行为不变 (backward compat). 非空时 prompt 末尾 append prior section.

~10 new test (Top-K rank / 0-essentialness fallback / desc/mech cap /
中文不乱码 / bootstrap backward compat / prior attach).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 4 — CLI 集成 (new/compress/chat 接 flush + `explain lexicon` 命令)

### Task 4: 集成到 3 个触发点 + 新 cli 命令

**Files:**
- Modify: `src/explain_engine/cli.py`:
  - `new` 加 `--lexicon-top-k` flag
  - `_run_new` load lexicon, 传 bootstrap
  - `_run_compress` 末尾 await flush
  - `_run_chat_repl_async` finally 内 await flush
  - 新 `lexicon` cmd
- Modify: `tests/test_cli_new.py` (扩 ~2 test)
- Modify: `tests/test_cli_compress.py` (扩 ~2 test)
- Create: `tests/test_cli_lexicon.py` (~5 test)

**Step 4.1: 改 cli.py `new` 命令 + `_run_new`**

加 `--lexicon-top-k` flag:

```python
@app.command()
def new(
    question: str = typer.Argument(..., help="为什么 X 问题"),
    no_chat: bool = typer.Option(False, "--no-chat", help="..."),
    tool_budget_per_turn: int = typer.Option(10, "--tool-budget-per-turn"),
    tool_budget_per_session: int = typer.Option(50, "--tool-budget-per-session"),
    lexicon_top_k: int = typer.Option(
        20, "--lexicon-top-k",
        help="bootstrap 拉 top-K lexicon var 作 prior (默认 20, 0 跳过)",
    ),
) -> None:
    asyncio.run(_run_new(
        question, no_chat, tool_budget_per_turn,
        tool_budget_per_session, lexicon_top_k,
    ))


async def _run_new(
    question: str,
    no_chat: bool = False,
    tool_budget_per_turn: int = 10,
    tool_budget_per_session: int = 50,
    lexicon_top_k: int = 20,
) -> None:
    settings = Settings()
    llm = make_llm_client()

    # Phase 10: load lexicon prior
    from explain_engine.engines.lexicon import _load_lexicon
    from explain_engine.persistence.storage_v2 import StorageV2

    storage = StorageV2()
    lexicon_path = storage.knowledge_dir() / "variables.json"
    lexicon_data = _load_lexicon(lexicon_path)
    lexicon = lexicon_data["variables"] if lexicon_top_k > 0 else None

    # ... 现有 LLM 调用 + bootstrap_phenomena 改:
    phenomena = await bootstrap_phenomena(
        question, llm, lexicon=lexicon, lexicon_top_k=lexicon_top_k,
    )
    # rest of _run_new ...
```

**Step 4.2: 改 `_run_compress` 末尾 flush**

读 `_run_compress` 找 `session.meta.stage = "done"` 那行 (line 269). 在 `store.save(session)` 之后加:

```python
# Phase 10: flush_to_lexicon (session done 触发)
from explain_engine.engines.lexicon import flush_to_lexicon
storage = StorageV2()
try:
    n = await flush_to_lexicon(session, storage, llm=llm)
    if n > 0:
        console.print(f"[INFO] {n} var 写入 lexicon")
except Exception as exc:
    console.print(f"[yellow]lexicon flush 失败 (非关键): {exc}[/yellow]")
```

flush 抛错不该 fail 整个 compress (best-effort).

**Step 4.3: 改 `_run_chat_repl_async` finally 内加 flush**

找 finally 块. 在 `await chat_session.aclose()` 之后加:

```python
# Phase 10: chat 退出时 flush_to_lexicon (graph 可能含 chat 期间新加 var)
try:
    from explain_engine.engines.lexicon import flush_to_lexicon
    from explain_engine.persistence.storage_v2 import StorageV2
    n = await flush_to_lexicon(
        chat_session._session, StorageV2(), llm=llm,
    )
    if n > 0:
        console.print(f"[dim]{n} var 写入 lexicon[/dim]")
except Exception as exc:
    console.print(
        f"[yellow]lexicon flush 失败 (非关键): {exc}[/yellow]"
    )
```

注: aclose 已 persist session, 这里仅 flush lexicon. exception 不该 fail.

**Step 4.4: 新 `lexicon` 命令**

加到 cli.py 末尾 (`@app.command()` 集合):

```python
@app.command()
def lexicon(
    dump_json: bool = typer.Option(
        False, "--dump-json", help="raw JSON 输到 stdout"
    ),
    top_k: int = typer.Option(
        0, "--top-k", help="仅显 top-K (默认 0=全显)"
    ),
) -> None:
    """显 knowledge/variables.json 内容 (Phase 10 lexicon)."""
    from explain_engine.engines.lexicon import _load_lexicon, _select_top_k_vars
    from explain_engine.persistence.storage_v2 import StorageV2

    storage = StorageV2()
    path = storage.knowledge_dir() / "variables.json"
    lex = _load_lexicon(path)

    if dump_json:
        import json
        print(json.dumps(lex, indent=2, ensure_ascii=False))
        return

    variables = lex["variables"]
    if top_k > 0:
        variables = _select_top_k_vars(lex, k=top_k)

    if not variables:
        console.print("[dim]lexicon 暂无变量. 跑 explain compress / chat 完成后再看.[/dim]")
        return

    table = Table(title=f"Variable Lexicon ({len(variables)} vars)")
    table.add_column("global_id", style="cyan")
    table.add_column("名称", style="bold")
    table.add_column("Level", justify="right")
    table.add_column("reuse", justify="right")
    table.add_column("avg_ess", justify="right")
    table.add_column("last_seen", style="dim")
    for v in variables:
        table.add_row(
            v["global_id"],
            v["name"],
            f"L{v['abstraction_level']}",
            str(v["fitness"]["reuse_count"]),
            f"{v['fitness']['avg_essentialness']:.2f}",
            v["fitness"]["last_seen_at"][:10],
        )
    console.print(table)
```

**Step 4.5: 写 cli test**

Create `tests/test_cli_lexicon.py`:

```python
"""Phase 10 Wave 4: explain lexicon 命令 + cli flush 集成 tests."""

import json

from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.engines.lexicon import _save_lexicon, _now_iso
from explain_engine.persistence.storage_v2 import StorageV2


def _seed_lexicon(*vars_):
    path = StorageV2().knowledge_dir() / "variables.json"
    _save_lexicon(path, {
        "version": 1,
        "updated_at": _now_iso(),
        "variables": list(vars_),
    })


def _make_var(global_id, name, reuse_count=1, level=2):
    return {
        "global_id": global_id,
        "name": name,
        "description": f"{name} 描述",
        "abstraction_level": level,
        "epistemic": "insight",
        "fitness": {
            "reuse_count": reuse_count,
            "avg_essentialness": 0.7,
            "avg_consistency": 0.5,
            "first_seen_at": _now_iso(),
            "last_seen_at": _now_iso(),
        },
        "canonical_mechanism": "通常 cause X",
        "source_sessions": ["s_001"],
    }


class TestLexiconCmd:
    def test_empty_shows_hint(self):
        runner = CliRunner()
        result = runner.invoke(app, ["lexicon"])
        assert result.exit_code == 0
        assert "暂无" in result.output or "compress" in result.output

    def test_with_vars_renders_table(self):
        _seed_lexicon(_make_var("v_a1", "长期不确定性", reuse_count=3))
        runner = CliRunner()
        result = runner.invoke(app, ["lexicon"])
        assert result.exit_code == 0
        assert "v_a1" in result.output
        assert "长期不确定性" in result.output

    def test_dump_json_outputs_raw(self):
        _seed_lexicon(_make_var("v_a1", "x"))
        runner = CliRunner()
        result = runner.invoke(app, ["lexicon", "--dump-json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["variables"][0]["global_id"] == "v_a1"

    def test_top_k_limits_rendering(self):
        _seed_lexicon(
            _make_var("v_a1", "high", reuse_count=10),
            _make_var("v_a2", "mid", reuse_count=5),
            _make_var("v_a3", "low", reuse_count=1),
        )
        runner = CliRunner()
        result = runner.invoke(app, ["lexicon", "--top-k", "2"])
        assert result.exit_code == 0
        assert "high" in result.output
        assert "mid" in result.output
        assert "low" not in result.output
```

**Step 4.6: 扩 test_cli_compress.py**

加新 test class:

```python
class TestCliCompressLexiconFlush:
    """Phase 10: compress 完成后 flush_to_lexicon 触发."""

    @pytest.mark.asyncio
    async def test_compress_done_triggers_flush(
        self, runner, ...  # 复用现有 fixture
    ):
        """跑完整 compress 流程, 验 knowledge/variables.json 含 promoted var."""
        # 用现有 fixture 跑到 stage=done, 然后
        from explain_engine.engines.lexicon import _load_lexicon
        from explain_engine.persistence.storage_v2 import StorageV2

        path = StorageV2().knowledge_dir() / "variables.json"
        lex = _load_lexicon(path)
        # 至少应有 1 var (compress 后 graph 含 L1+L2)
        assert len(lex["variables"]) >= 1

    # (具体 fixture 复用看 tests/test_cli_compress.py 现状)
```

**注**: tests/test_cli_compress.py 具体 fixture 结构 implementer 看现有再决定. 可能需要 mock LLM + HITL 整链.

**Step 4.7: 扩 test_cli_new.py — 验 bootstrap 接 lexicon**

加 1 个 test:

```python
class TestCliNewLexiconIntegration:
    def test_bootstrap_receives_lexicon(
        self, runner, setup_env, mock_llm_chat, mock_review_phenomena, monkeypatch
    ):
        """有 lexicon 时 bootstrap 收到非空 lexicon 参数."""
        from explain_engine.engines.lexicon import _save_lexicon, _now_iso
        from explain_engine.persistence.storage_v2 import StorageV2

        # 先 seed lexicon
        _save_lexicon(
            StorageV2().knowledge_dir() / "variables.json",
            {"version": 1, "updated_at": _now_iso(), "variables": [{
                "global_id": "v_seeded",
                "name": "seed_var",
                "description": "x",
                "abstraction_level": 2,
                "epistemic": "insight",
                "fitness": {"reuse_count": 1, "avg_essentialness": 0.5,
                            "avg_consistency": 0.5,
                            "first_seen_at": _now_iso(),
                            "last_seen_at": _now_iso()},
                "canonical_mechanism": "通常 cause X",
                "source_sessions": ["s_001"],
            }]}
        )

        captured_lexicon = []
        from explain_engine.engines.bootstrap import (
            bootstrap_phenomena as real_bootstrap,
        )

        async def _wrapped(question, llm, lexicon=None, lexicon_top_k=20):
            captured_lexicon.append(lexicon)
            return await real_bootstrap(
                question, llm, lexicon=None, lexicon_top_k=0,  # 防递归
            )

        # 实际 monkey patch 路径根据 bootstrap import 方式调整
        monkeypatch.setattr("explain_engine.cli.bootstrap_phenomena", _wrapped)

        mock_llm_chat([{"name": "x", "description": "y"}])
        mock_review_phenomena("all")

        result = runner.invoke(app, ["new", "why?", "--no-chat"])
        assert result.exit_code == 0
        # bootstrap 收到 seeded lexicon
        assert len(captured_lexicon) == 1
        assert captured_lexicon[0] is not None
        assert len(captured_lexicon[0]) == 1
        assert captured_lexicon[0][0]["name"] == "seed_var"
```

**Step 4.8: 跑测试 全测**

Run: `.venv/bin/python -m pytest tests/test_cli_lexicon.py tests/test_cli_new.py tests/test_cli_compress.py -v`
Expected: 全 PASS.

Run: `.venv/bin/python -m pytest -x --tb=no -q 2>&1 | tail -3`
Expected: ~727 PASS.

**Step 4.9: ruff**

Run: `.venv/bin/ruff check src/explain_engine/cli.py tests/test_cli_lexicon.py tests/test_cli_new.py tests/test_cli_compress.py`
Expected: 0.

**Step 4.10: Commit**

```bash
git add src/explain_engine/cli.py tests/test_cli_lexicon.py tests/test_cli_new.py tests/test_cli_compress.py
git commit -m "$(cat <<'EOF'
cli · Phase 10 Wave 4 — new/compress/chat 接 flush + explain lexicon 命令

3 个 lexicon 触发点:
- _run_new 启动时 _load_lexicon → 传 bootstrap_phenomena (Top-K=20 默认)
- _run_compress 末尾 stage=done 后 await flush_to_lexicon (best-effort,
  exception 不 fail compress)
- _run_chat_repl_async finally 内 aclose 后 await flush (chat 期间也可能
  产新 var)

新 cli `explain lexicon`: 列 lexicon (Rich Table) / --dump-json 输 raw /
--top-k N 限数. 空 lexicon 友好提示.

new 加 --lexicon-top-k flag (默认 20, 0 跳过 prior section).

~9 new test 覆盖 (empty / vars / json dump / top-k filter / new 收 lexicon /
compress flush 触发 / chat aclose flush).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 5 — Acceptance smoke + README update

### Task 5: 手测 doc + README Phase 10 段

**Files:**
- Create: `docs/plans/2026-05-18-phase10-persistent-world-model-acceptance.md`
- Modify: `README.md` (加 Phase 10 段)

**Step 5.1: 写 acceptance doc**

Create `docs/plans/2026-05-18-phase10-persistent-world-model-acceptance.md`:

```markdown
# Phase 10 Persistent World Model — Acceptance Checklist

> Design: [2026-05-18-phase10-persistent-world-model-design.md](2026-05-18-phase10-persistent-world-model-design.md)
> Plan: [2026-05-18-phase10-persistent-world-model-plan.md](2026-05-18-phase10-persistent-world-model-plan.md)

需 LLM key + 真终端. 6 步手测验 cross-session var lexicon 端到端流程.

## Setup

1. HEAD = Wave 5 commit 或之后
2. `.venv/bin/python -m pytest -x` 应全 PASS (~727)
3. `.env` 含 LLM 配置
4. 清测试 project lexicon: `rm -rf ~/.explain/projects/<project_id>/knowledge/`

## Smoke Steps

### S1: Session 1 — bootstrap 无 lexicon (空库)

```bash
.venv/bin/python -m explain_engine new "为什么年轻人不消费" --no-chat
```

Expected: bootstrap 正常 LLM 调用 + HITL review (无 prior section, lexicon 空).

### S2: 跑 compress 触发 flush

```bash
.venv/bin/python -m explain_engine compress <sid1>
```

Expected: HITL 2 review 后 console print "N var 写入 lexicon" (N ≥ 1).

### S3: 验 lexicon 文件创建

```bash
cat ~/.explain/projects/<project_id>/knowledge/variables.json | head -30
.venv/bin/python -m explain_engine lexicon
```

Expected:
- JSON 含 1+ var entry (含 global_id / name / fitness)
- `explain lexicon` Rich Table 显示

### S4: Session 2 — bootstrap 看 lexicon prior

```bash
.venv/bin/python -m explain_engine new "为什么年轻人不结婚" --no-chat
```

Expected: console log 含 "调 LLM ... 生现象..." 然后等 LLM 返. 检查 LLM 收到的 prompt 是否含 "已知 reusable abstractions" prior section (可通过 LLM 日志或临时加 print debug 验).

### S5: 同 session 重复 compress 幂等

跑 `compress <sid1>` 第 2 次 (假设 stage 仍 done, 看 cli 怎么处理 re-compress; 或新建一个 done session 重复 flush). 验 lexicon.variables 内对应 entry 的 `reuse_count` 仍 1, `source_sessions` 仍含 1 个 sid.

### S6: 跨 session reuse_count++ 验证

跑 session 2 完整 compress 后, `explain lexicon` 看是否有重叠 var 的 `reuse_count` 变 2 (前提: session 2 graph 产生了同 name + canonical_mech 的 var).

Note: 同 name + 同 canonical_mech 才 merge (conservative). 若 LLM 生 mech 不一样, 会创建新 entry — 这是 expected (Phase 10 第一版 trade-off, Candidate E embedding 解决).

## Pass/Fail 标准

S1-S4 必过, S5/S6 best-effort (依赖 LLM 输出稳定性).

## 已知 trade-off (design 选择)

- `canonical_mechanism` 由 LLM 生, 同 var 两 session 可能 mech 文本微差 → 不 merge (conservative split). Candidate E embedding 解决.
- fitness avg_* 用 running avg (new_avg = (old_avg * old_count + new_value) / new_count), Phase 10.x 可重评.
- Top-K=20 + render cap ~1.7k token 是 hard cap; 超 lexicon 时 long-tail var 不进 prior (是 by design, 不 surface 给 LLM).

## Wave 5 后 follow-up

- 真跑 S1-S6 暴露的 bug fix
- 长期: Phase 11 Theory Formation 启动时, lexicon 是 motif detection 的输入数据层
```

**Step 5.2: 改 README**

读 README 找 Phase 9 section. 在末尾加 Phase 10 section:

```markdown
## Phase 10 (2026-05-18) — Persistent World Model (Variable Lexicon)

`knowledge/` 目录从 Phase 9 占位空目录变 **跨 session Variable Lexicon**. 高 fitness L1/L2 节点累积成 reusable abstractions, 新 session bootstrap 时 LLM 看 Top-K 作 prior.

**核心 (design Q&A 锁)**:
- 单位: Variable Lexicon (mechanism / theory 留 Phase 11)
- 写入: session done auto-flush (compress 完 + chat aclose)
- 读取: bootstrap 看 Top-K=20 by composite fitness (reuse × essentialness)
- token cap: per-var render cap, 总 ~1.7k token (deterministic, 不依赖 embedding)
- storage: local JSON (远程存 Neo4j/pgvector 留 Phase 11+ 再评估)

**新 CLI commands**:
- `explain lexicon` — 列 lexicon (Rich Table)
- `explain lexicon --dump-json` — raw JSON
- `explain lexicon --top-k N` — 仅显 top-K
- `explain new --lexicon-top-k 0` — 跳过 lexicon prior

**文档**:
- design: [docs/plans/2026-05-18-phase10-persistent-world-model-design.md](docs/plans/2026-05-18-phase10-persistent-world-model-design.md)
- plan: [docs/plans/2026-05-18-phase10-persistent-world-model-plan.md](docs/plans/2026-05-18-phase10-persistent-world-model-plan.md)
- acceptance: [docs/plans/2026-05-18-phase10-persistent-world-model-acceptance.md](docs/plans/2026-05-18-phase10-persistent-world-model-acceptance.md)
```

**Step 5.3: 更新 README 顶部 tests 数字**

`README.md:26` 当前 "678 tests pass" → 改为最新 (查跑 pytest 后实际数, 应 ~727).

**Step 5.4: Commit**

```bash
git add docs/plans/2026-05-18-phase10-persistent-world-model-acceptance.md README.md
git commit -m "$(cat <<'EOF'
docs · Phase 10 Wave 5 — acceptance smoke + README 更新

acceptance doc 6 步手测: 空库 bootstrap → compress 触发 flush → 验 JSON
文件 → 第 2 session 看 prior section → 幂等 / cross-session ++count.

README 加 Phase 10 段 (核心决策 + 新 CLI 命令 + 文档链接). tests 数字更新.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Acceptance checklist (整体)

落地完成后:

- [ ] 全测 PASS: `.venv/bin/python -m pytest -x` (~727 total: 679 + ~48 new)
- [ ] ruff 0: `.venv/bin/ruff check src/ tests/`
- [ ] `git log --oneline dev ^master` 显示 5 commit (Wave 1-5)
- [ ] `knowledge/variables.json` schema 含 6 字段 (global_id / name / desc / level / epistemic / fitness / mech / source_sessions)
- [ ] `explain lexicon` 命令 4 mode 工作 (默认 table / --dump-json / --top-k / 空友好提示)
- [ ] (Manual smoke) S1-S6 跑通

---

## Risk 回顾

- **Phase 10 fitness avg 算法** — running avg 起步, Phase 10.x 可重评. Plan 内已 TODO.
- **canonical_mechanism LLM 漂移** — 同 var 两 session 不同 mech → split. Conservative trade-off, Candidate E 解.
- **chat aclose 时 flush 慢** — flush 含 LLM call build canonical_mech. 包 try/except + LLM error fallback (edge-based) 已 mitigate.
- **lexicon load 错误** — 空 path 返 empty schema (不抛); 损坏 JSON 抛 — bootstrap 应 catch 并 fallback 空 lexicon (Wave 3 实装时确认).

---

## 参考

- Design doc: [2026-05-18-phase10-persistent-world-model-design.md](2026-05-18-phase10-persistent-world-model-design.md)
- 顶层 §5.3 + §8.2: [最终哲学以及技术实现相关设计.md](../../最终哲学以及技术实现相关设计.md)
- 当前 chat REPL: [src/explain_engine/cli.py:945-1075](../../src/explain_engine/cli.py#L945)
- VariableNode schema: [src/explain_engine/schema/nodes.py](../../src/explain_engine/schema/nodes.py)
- StorageV2 (knowledge_dir): [src/explain_engine/persistence/storage_v2.py:70](../../src/explain_engine/persistence/storage_v2.py#L70)
