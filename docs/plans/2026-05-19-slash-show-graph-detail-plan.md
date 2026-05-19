# /show + /graph Detail Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 改 `/show` 输出全展开 graph (L0/L1/L2 + edges 按 type), 新加 `/graph` slash 走 graphviz inline 渲染 (iTerm2/Kitty/chafa 检测 + 临时 PNG + atexit cleanup).

**Architecture:** Phase A 改现有 `_handle_show` + 加 3 个 helper (`_format_epi_short` / `_format_edge_brief` / 微调 `_format_node_brief`). Phase B 新加 `/graph` slash 含 4 helper (`_get_session_tmpdir` / `_build_digraph` / `_detect_inline_renderer` / `_handle_graph`). Phase C 更新 README + acceptance 文档. 18 → 19 slash.

**Tech Stack:** Python 3.11+, pytest, prompt_toolkit (已有), Rich (已有), `graphviz>=0.20` (新加), 系统 `dot` binary + 可选 `chafa` / `imgcat` / `kitty`.

**Related Design:** [docs/plans/2026-05-19-slash-show-graph-detail-design.md](2026-05-19-slash-show-graph-detail-design.md)

**Project Conventions** (MUST 遵守):
- 测试: `.venv/bin/python -m pytest` (NOT bare `python`)
- Lint: `.venv/bin/ruff check src/ tests/`
- Commit: 中文 HEREDOC + `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer (`(1M context)` 后缀必须)
- 分支 dev. NEVER push, NEVER --no-verify, NEVER amend. 新 commit only.
- No emoji unless user 要 (本任务里 marker 全 ASCII)
- Pydantic v2 风格 (本任务无新 model, 复用现有)
- TDD: 每 task 先 failing test → minimal impl → verify pass → commit

---

## Phase A: `/show` text 增强 (改现有 handler)

### Task A1: 加 `_format_epi_short` helper

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py` (helper 加在 `_format_node_brief` 上方 ~line 484)
- Create test: `tests/chat/test_format_helpers.py`

**Step 1: Create test directory if needed**

```bash
mkdir -p /Users/jinziguan/Desktop/explain_everything/tests/chat
touch /Users/jinziguan/Desktop/explain_everything/tests/chat/__init__.py
```

**Step 2: Write the failing test**

Create `tests/chat/test_format_helpers.py`:

```python
"""Phase 12 (2026-05-19): /show + /graph detail helpers test."""

import pytest


class TestFormatEpiShort:
    def test_fact(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("fact") == "fact"

    def test_observation(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("observation") == "obs"

    def test_inference(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("inference") == "inf"

    def test_insight(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("insight") == "ins"

    def test_speculation(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("speculation") == "spec"

    def test_unknown_returns_input(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        # 防御: 未知 epi 返原值 (新加 Epistemic literal 时不 crash)
        assert _format_epi_short("emerging") == "emerging"
```

**Step 3: Run test to verify FAIL**

```bash
cd /Users/jinziguan/Desktop/explain_everything
.venv/bin/python -m pytest tests/chat/test_format_helpers.py::TestFormatEpiShort -v
```

Expected: 6 ERROR/FAIL with "ImportError: cannot import name '_format_epi_short'"

**Step 4: Write minimal implementation**

Insert into `src/explain_engine/chat/slash_commands.py` immediately before `_format_node_brief` (around line 484):

```python
_EPI_SHORT_MAP = {
    "fact": "fact",
    "observation": "obs",
    "inference": "inf",
    "insight": "ins",
    "speculation": "spec",
}


def _format_epi_short(epi: str) -> str:
    """Epistemic 5 字 → 3-4 字缩写, 行格式对齐用. 未知 epi fallback 返原值."""
    return _EPI_SHORT_MAP.get(epi, epi)
```

**Step 5: Run test to verify PASS**

```bash
.venv/bin/python -m pytest tests/chat/test_format_helpers.py::TestFormatEpiShort -v
```

Expected: 6 PASS

**Step 6: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/chat/__init__.py tests/chat/test_format_helpers.py
git commit -m "$(cat <<'EOF'
chat/slash · 加 _format_epi_short helper (epistemic 缩写)

/show + /graph detail design Phase A Task 1: epistemic 5 字 (fact/
observation/inference/insight/speculation) → 3-4 字 (fact/obs/inf/ins/
spec) 行格式对齐用. 6 unit test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A2: 重构 `_format_node_brief` 加 epi_short + conf + marker

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py:485-500` (现有 `_format_node_brief`)
- Modify test: `tests/chat/test_format_helpers.py`

**Step 1: Read current `_format_node_brief`** (line 485-500 已读过, 内容):

```python
def _format_node_brief(state, nid: str, max_desc: int = 60) -> str:
    """Fix 3 (2026-05-19 smoke bug 2): ..."""
    node = state.graph.nodes.get(nid)
    if node is None:
        return f"{nid} (节点不在 graph)"
    desc = node.description[:max_desc]
    if len(node.description) > max_desc:
        desc += "..."
    return f"{nid} 「{node.name}」: {desc}"
```

**Step 2: Write the failing tests (add to test_format_helpers.py)**

Append to `tests/chat/test_format_helpers.py`:

```python
class TestFormatNodeBrief:
    """新行格式: `{id} [{epi_short} {conf:.2f}] {marker?}「{name}」: {desc[:60]}...?`"""

    def _make_state_with_node(self, **node_kwargs):
        """Helper: build minimal ChatState with 1 VariableNode."""
        from explain_engine.chat.session import ChatState
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.nodes import VariableNode
        g = ExplanationGraph(root_question="Q")
        node = VariableNode(**node_kwargs)
        g.add_node(node)
        state = ChatState(graph=g)
        return state

    def test_basic_format_includes_epi_conf_name(self):
        from explain_engine.chat.slash_commands import _format_node_brief
        state = self._make_state_with_node(
            id="p_001", name="房价上涨", description="一线城市房价持续上涨",
            abstraction_level=0, confidence=0.85, epistemic="observation",
        )
        out = _format_node_brief(state, "p_001")
        assert "p_001" in out
        assert "[obs 0.85]" in out
        assert "「房价上涨」" in out
        assert "一线城市房价持续上涨" in out

    def test_desc_truncation_at_60(self):
        from explain_engine.chat.slash_commands import _format_node_brief
        long_desc = "x" * 100
        state = self._make_state_with_node(
            id="p_002", name="n", description=long_desc,
            abstraction_level=0, confidence=0.5, epistemic="fact",
        )
        out = _format_node_brief(state, "p_002")
        assert "..." in out
        # desc 部分恰好 60 char + "..."
        assert out.endswith("x" * 60 + "...")

    def test_marker_weak(self):
        from explain_engine.chat.slash_commands import _format_node_brief
        state = self._make_state_with_node(
            id="c_001", name="n", description="d",
            abstraction_level=1, confidence=0.5, epistemic="insight",
        )
        out = _format_node_brief(state, "c_001", weak=True)
        assert "(weak)" in out

    def test_marker_stale(self):
        from explain_engine.chat.slash_commands import _format_node_brief
        state = self._make_state_with_node(
            id="c_002", name="n", description="d",
            abstraction_level=1, confidence=0.5, epistemic="insight",
            lifecycle_state="stale",
        )
        out = _format_node_brief(state, "c_002")
        assert "[stale]" in out

    def test_marker_decayed(self):
        from explain_engine.chat.slash_commands import _format_node_brief
        state = self._make_state_with_node(
            id="c_003", name="n", description="d",
            abstraction_level=1, confidence=0.5, epistemic="insight",
            lifecycle_state="decayed",
        )
        out = _format_node_brief(state, "c_003")
        assert "[decayed]" in out

    def test_marker_priority_decayed_over_weak(self):
        """lifecycle > weak — decayed + weak 同时只显 [decayed]."""
        from explain_engine.chat.slash_commands import _format_node_brief
        state = self._make_state_with_node(
            id="c_004", name="n", description="d",
            abstraction_level=1, confidence=0.5, epistemic="insight",
            lifecycle_state="decayed",
        )
        out = _format_node_brief(state, "c_004", weak=True)
        assert "[decayed]" in out
        assert "(weak)" not in out

    def test_marker_priority_stale_over_weak(self):
        from explain_engine.chat.slash_commands import _format_node_brief
        state = self._make_state_with_node(
            id="c_005", name="n", description="d",
            abstraction_level=1, confidence=0.5, epistemic="insight",
            lifecycle_state="stale",
        )
        out = _format_node_brief(state, "c_005", weak=True)
        assert "[stale]" in out
        assert "(weak)" not in out

    def test_missing_node_fallback(self):
        """Fix 3 兼容: nid 不在 graph 返原 '(节点不在 graph)' fallback."""
        from explain_engine.chat.session import ChatState
        from explain_engine.chat.slash_commands import _format_node_brief
        from explain_engine.schema.graph import ExplanationGraph
        state = ChatState(graph=ExplanationGraph(root_question="Q"))
        out = _format_node_brief(state, "p_999")
        assert "p_999" in out
        assert "节点不在 graph" in out
```

**Step 3: Run tests to verify FAIL**

```bash
.venv/bin/python -m pytest tests/chat/test_format_helpers.py::TestFormatNodeBrief -v
```

Expected: 7 FAIL (新 keyword arg `weak=True` 不支持 + 行格式不含 `[obs 0.85]` 等)

**Step 4: Replace `_format_node_brief`**

Replace lines 485-500 in `src/explain_engine/chat/slash_commands.py`:

```python
def _format_node_brief(
    state,
    nid: str,
    max_desc: int = 60,
    weak: bool = False,
) -> str:
    """Phase 12 (2026-05-19): /show + /graph detail. Fix 3 升级版.

    新行格式:
      {id} [{epi_short} {conf:.2f}] {marker?} 「{name}」: {desc[:max_desc]}{...}?

    marker 优先级 (lifecycle > weak): [decayed] > [stale] > (weak) > 空.

    Args:
        state: ChatState (含 graph).
        nid: node id.
        max_desc: desc 截短 char 数, 默认 60.
        weak: caller 标记此 node 在 weak_chain_l1s 中 (multi-signal 视角).
              若 lifecycle_state 是 stale/decayed, marker 用 lifecycle 不用 weak.

    Returns:
        formatted line, 或 fallback "{nid} (节点不在 graph)".

    Used by:
        - /show (Phase 12) node tree
        - /predict (Fix 3) report
        - /counterfactual (Fix 3) report
    """
    node = state.graph.nodes.get(nid)
    if node is None:
        return f"{nid} (节点不在 graph)"

    epi_short = _format_epi_short(node.epistemic)
    conf_str = f"{node.confidence:.2f}"

    # marker 优先级: lifecycle > weak
    if node.lifecycle_state == "decayed":
        marker = "[decayed] "
    elif node.lifecycle_state == "stale":
        marker = "[stale] "
    elif weak:
        marker = "(weak) "
    else:
        marker = ""

    desc = node.description[:max_desc]
    if len(node.description) > max_desc:
        desc += "..."

    return f"{nid} [{epi_short} {conf_str}] {marker}「{node.name}」: {desc}"
```

**Step 5: Run tests to verify PASS**

```bash
.venv/bin/python -m pytest tests/chat/test_format_helpers.py -v
```

Expected: All TestFormatEpiShort (6) + TestFormatNodeBrief (8) PASS.

**Step 6: Run existing test suite — verify no break in /predict /counterfactual (Fix 3 user)**

```bash
.venv/bin/python -m pytest tests/test_chat_slash_commands.py -v -k "predict or counterfactual"
```

Expected: 全 PASS. 若 fail, /predict 或 /counterfactual 的现有 assertion 可能 match 旧行格式 `「name」: desc` 但不 match 新格式 `[epi conf] 「name」`. 这种情况 update assertion 即可 (新增字段不破坏功能).

**Step 7: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/chat/test_format_helpers.py
git commit -m "$(cat <<'EOF'
chat/slash · 重构 _format_node_brief 加 epi+conf+marker

Phase A Task 2: 行格式从 '{id} 「name」: desc' 升级为
'{id} [{epi_short} {conf:.2f}] {marker?} 「name」: desc'.
marker 优先级 lifecycle > weak. Backward 兼容 nid not-found
fallback. 8 unit test + /predict /counterfactual 现有 test 验.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A3: 加 `_format_edge_brief` helper

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py` (加在 `_format_node_list` 下方 ~line 510)
- Modify test: `tests/chat/test_format_helpers.py`

**Step 1: Write the failing tests**

Append to `tests/chat/test_format_helpers.py`:

```python
class TestFormatEdgeBrief:
    """Edge 行格式: `{source} → {target} [{conf:.2f}] {mechanism[:max_mech]}...?`"""

    def _make_edge(self, **kwargs):
        from explain_engine.schema.edges import RelationEdge
        return RelationEdge(**kwargs)

    def test_basic_format(self):
        from explain_engine.chat.slash_commands import _format_edge_brief
        edge = self._make_edge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.85,
            mechanism_description="经济不安全感在房价感受层表现为购房意愿降",
        )
        out = _format_edge_brief(edge)
        assert "c_001" in out
        assert "→" in out
        assert "p_001" in out
        assert "[0.85]" in out
        assert "经济不安全感在房价感受层表现为购房意愿降" in out

    def test_mechanism_truncation(self):
        from explain_engine.chat.slash_commands import _format_edge_brief
        long_mech = "y" * 100
        edge = self._make_edge(
            id="e_002", source_node="c_001", target_node="c_002",
            relation_type="causes", confidence=0.5,
            mechanism_description=long_mech,
        )
        out = _format_edge_brief(edge, max_mech=60)
        assert "..." in out
        assert out.endswith("y" * 60 + "...")

    def test_relation_type_not_in_line(self):
        """type 已在 section header 分组, 行内不重复显."""
        from explain_engine.chat.slash_commands import _format_edge_brief
        edge = self._make_edge(
            id="e_003", source_node="a", target_node="b",
            relation_type="amplifies", confidence=0.7,
            mechanism_description="m",
        )
        out = _format_edge_brief(edge)
        assert "amplifies" not in out
```

**Step 2: Run test to verify FAIL**

```bash
.venv/bin/python -m pytest tests/chat/test_format_helpers.py::TestFormatEdgeBrief -v
```

Expected: 3 ERROR with `ImportError: cannot import name '_format_edge_brief'`

**Step 3: Write minimal implementation**

Insert into `src/explain_engine/chat/slash_commands.py` after `_format_node_list` (around line 510):

```python
def _format_edge_brief(edge, max_mech: int = 60) -> str:
    """Phase 12: /show edge 行格式.

    格式: `{source} → {target} [{conf:.2f}] {mechanism[:max_mech]}...?`

    relation_type 不显行内 (caller 已按 type 分 section). source/target
    只显 ID, 不展开 name — 上方 node tree 可查, 避免行宽爆炸.
    """
    mech = edge.mechanism_description[:max_mech]
    if len(edge.mechanism_description) > max_mech:
        mech += "..."
    return f"{edge.source_node} → {edge.target_node} [{edge.confidence:.2f}] {mech}"
```

**Step 4: Run test to verify PASS**

```bash
.venv/bin/python -m pytest tests/chat/test_format_helpers.py -v
```

Expected: All format helpers (epi + node + edge = 17 total) PASS.

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/chat/test_format_helpers.py
git commit -m "$(cat <<'EOF'
chat/slash · 加 _format_edge_brief helper

Phase A Task 3: edge 行格式 'source → target [conf] mechanism'
(mechanism 默认截 60 char). relation_type 不显行内, 由 caller
section header 分组. 3 unit test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A4: 重写 `_handle_show` 4 section layout

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py:101-143` (现有 `_handle_show`)
- Create test: `tests/chat/test_slash_show.py`
- Modify existing test: `tests/test_chat_slash_commands.py:56-65` (`test_show_includes_question_and_graph_counts`)

**Step 1: Write the failing tests**

Create `tests/chat/test_slash_show.py`:

```python
"""Phase 12 (2026-05-19): /show 4 section layout test."""

import pytest

from explain_engine.chat.slash_commands import dispatch_slash


def _make_session_with_graph(sid: str, *, l0: int = 3, l1: int = 2, l2: int = 0):
    """Helper: 建 ChatSession + 含 L0/L1/L2 node 的 graph + 几条 edge.

    用 _make_done_session pattern (复用 tests/test_chat_session.py).
    若需精细控制 graph 结构, 直接 mutate chat.state.graph 即可.
    """
    from tests.test_chat_session import _make_done_session
    from explain_engine.chat.session import ChatSession
    from explain_engine.schema.edges import RelationEdge
    from explain_engine.schema.nodes import VariableNode

    _make_done_session(sid)
    chat = ChatSession(sid)
    g = chat.state.graph

    # Clear default fixture graph 重建
    for nid in list(g.nodes):
        g.remove_node(nid)

    for i in range(l0):
        g.add_node(VariableNode(
            id=f"p_{i+1:03d}", name=f"observation_{i+1}",
            description=f"obs desc {i+1}",
            abstraction_level=0, confidence=0.8, epistemic="observation",
        ))
    for i in range(l1):
        g.add_node(VariableNode(
            id=f"c_{i+1:03d}", name=f"concept_{i+1}",
            description=f"concept desc {i+1}",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
    for i in range(l2):
        g.add_node(VariableNode(
            id=f"d_{i+1:03d}", name=f"driver_{i+1}",
            description=f"driver desc {i+1}",
            abstraction_level=2, confidence=0.6, epistemic="inference",
        ))
    # 1 manifests_as edge per L1-L0 pair (up to min)
    if l1 > 0 and l0 > 0:
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.85,
            mechanism_description="c_001 manifests as p_001",
        ))
    return chat


class TestShowLayout:
    @pytest.mark.asyncio
    async def test_four_section_headers(self):
        chat = _make_session_with_graph("s_show_001")
        events = await dispatch_slash(chat, "/show")
        content = events[0].content
        assert "=== Session ===" in content
        assert "=== Graph" in content  # `=== Graph (3 nodes: ...) ===`
        assert "=== Edges" in content  # `=== Edges (1) ===`
        assert "=== Multi-signal acceptance ===" in content

    @pytest.mark.asyncio
    async def test_node_tree_grouped_by_level(self):
        chat = _make_session_with_graph("s_show_002", l0=2, l1=1, l2=1)
        events = await dispatch_slash(chat, "/show")
        content = events[0].content
        assert "[L0 Observations] (2)" in content
        assert "[L1 Concepts] (1)" in content
        assert "[L2 Drivers] (1)" in content

    @pytest.mark.asyncio
    async def test_node_lines_show_epi_and_conf(self):
        chat = _make_session_with_graph("s_show_003")
        events = await dispatch_slash(chat, "/show")
        content = events[0].content
        # p_001 epi=observation conf=0.8 → "[obs 0.80]"
        assert "[obs 0.80]" in content
        # c_001 epi=insight conf=0.7 → "[ins 0.70]"
        assert "[ins 0.70]" in content

    @pytest.mark.asyncio
    async def test_edge_section_grouped_by_type(self):
        chat = _make_session_with_graph("s_show_004")
        events = await dispatch_slash(chat, "/show")
        content = events[0].content
        # 1 manifests_as edge → "manifests_as (1):" header
        assert "manifests_as (1):" in content

    @pytest.mark.asyncio
    async def test_empty_graph(self):
        """0 nodes → Graph section '(empty)', Edges section '(no edges)'."""
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        _make_done_session("s_show_005")
        chat = ChatSession("s_show_005")
        # Clear all nodes/edges
        for nid in list(chat.state.graph.nodes):
            chat.state.graph.remove_node(nid)
        events = await dispatch_slash(chat, "/show")
        content = events[0].content
        assert "(empty)" in content or "(no edges)" in content

    @pytest.mark.asyncio
    async def test_weak_marker_priority_lifecycle_over_weak(self):
        """同节点 lifecycle=stale 且在 weak_chain_l1s → 显 [stale] 不显 (weak)."""
        chat = _make_session_with_graph("s_show_006", l1=1)
        # Make c_001 stale + 加 weak_chain_l1s monkeypatch
        chat.state.graph.nodes["c_001"].lifecycle_state = "stale"

        import explain_engine.chat.slash_commands as sc
        original_agg = sc.aggregate_acceptance if hasattr(sc, "aggregate_acceptance") else None

        def fake_aggregate(state):
            from explain_engine.engines.simulation import AcceptanceReport
            return AcceptanceReport(
                avg_consistency=0.5, avg_essentialness=0.0,
                weak_chain_l1s=["c_001"], rollout_coverage=1.0,
            )

        # patch 路径: _handle_show import 时 `from explain_engine.engines.simulation import aggregate_acceptance`
        import explain_engine.engines.simulation as sim
        original = sim.aggregate_acceptance
        sim.aggregate_acceptance = fake_aggregate
        try:
            events = await dispatch_slash(chat, "/show")
            content = events[0].content
            # c_001 显 [stale], 不显 (weak)
            # 在 L1 section 找 c_001 line
            l1_lines = [ln for ln in content.split("\n") if "c_001" in ln and "「" in ln]
            assert l1_lines
            assert "[stale]" in l1_lines[0]
            assert "(weak)" not in l1_lines[0]
        finally:
            sim.aggregate_acceptance = original

    @pytest.mark.asyncio
    async def test_multisignal_at_bottom(self):
        chat = _make_session_with_graph("s_show_007")
        events = await dispatch_slash(chat, "/show")
        content = events[0].content
        # Multi-signal section 在 Edges section 之后
        ms_idx = content.find("=== Multi-signal acceptance ===")
        eg_idx = content.find("=== Edges")
        assert ms_idx > eg_idx > 0

    @pytest.mark.asyncio
    async def test_aggregate_failure_does_not_crash(self):
        """aggregate_acceptance raise → multi-signal section fallback, /show 不 crash."""
        chat = _make_session_with_graph("s_show_008")
        import explain_engine.engines.simulation as sim
        original = sim.aggregate_acceptance
        sim.aggregate_acceptance = lambda state: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            events = await dispatch_slash(chat, "/show")
            content = events[0].content
            assert "aggregate_acceptance failed" in content
            # session/graph section 仍输出
            assert "=== Session ===" in content
        finally:
            sim.aggregate_acceptance = original
```

**Step 2: Run tests to verify FAIL**

```bash
.venv/bin/python -m pytest tests/chat/test_slash_show.py -v
```

Expected: 多个 FAIL — section header `=== ... ===` 不在; node tree group header 不在; multi-signal 不在末尾 etc.

**Step 3: Replace `_handle_show` (line 101-143)**

Replace the entire `_handle_show` function:

```python
async def _handle_show(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 12 (2026-05-19): /show 全展开 graph + multi-signal acceptance.

    输出 4 个 section (Session → Graph → Edges → Multi-signal). 详见
    docs/plans/2026-05-19-slash-show-graph-detail-design.md.

    aggregate_acceptance 是 readonly (Phase 2 simulation), 不调 LLM, 廉价.
    包 try/except — graph 空 / no L1 / 其他 edge case 不应 crash inspection 命令.
    """
    from explain_engine.chat.session import ChatEvent
    from explain_engine.engines.simulation import aggregate_acceptance

    state = chat.state
    g = state.graph
    n_l0 = sum(1 for n in g.nodes.values() if n.abstraction_level == 0)
    n_l1 = sum(1 for n in g.nodes.values() if n.abstraction_level == 1)
    n_l2 = sum(1 for n in g.nodes.values() if n.abstraction_level == 2)
    n_decayed = sum(1 for n in g.nodes.values() if n.lifecycle_state == "decayed")
    n_stale = sum(1 for n in g.nodes.values() if n.lifecycle_state == "stale")

    # ─── Multi-signal 提前算 (weak_chain_l1s 要传给 L1 section 给 (weak) marker)
    weak_l1_set: set[str] = set()
    report = None
    agg_err: str | None = None
    try:
        report = aggregate_acceptance(state)
        weak_l1_set = set(report.weak_chain_l1s or [])
    except Exception as exc:
        agg_err = type(exc).__name__

    lines: list[str] = []

    # ═══ Section 1: Session ═══
    lines.append("=== Session ===")
    lines.append(f"SID:      {chat.sid}")
    lines.append(f"Question: {chat._session.meta.question}")
    lines.append(f"Stage:    {chat._session.meta.stage}")
    lines.append("")

    # ═══ Section 2: Graph (node tree by L) ═══
    lines.append(
        f"=== Graph ({len(g.nodes)} nodes: {n_l0} L0 / {n_l1} L1 / {n_l2} L2; "
        f"{n_decayed} decayed, {n_stale} stale) ==="
    )
    lines.append("")

    if len(g.nodes) == 0:
        lines.append("(empty)")
    else:
        # L0 section
        if n_l0 > 0:
            lines.append(f"[L0 Observations] ({n_l0})")
            for nid in sorted(n.id for n in g.nodes.values() if n.abstraction_level == 0):
                lines.append(f"  {_format_node_brief(state, nid, weak=nid in weak_l1_set)}")
            lines.append("")

        # L1 section (with weak chain header)
        if n_l1 > 0:
            l1_header = f"[L1 Concepts] ({n_l1})"
            l1_weak = [n.id for n in g.nodes.values()
                       if n.abstraction_level == 1 and n.id in weak_l1_set]
            if l1_weak:
                l1_header += f" — weak chain: {' '.join(sorted(l1_weak))}"
            lines.append(l1_header)
            for nid in sorted(n.id for n in g.nodes.values() if n.abstraction_level == 1):
                lines.append(f"  {_format_node_brief(state, nid, weak=nid in weak_l1_set)}")
            lines.append("")

        # L2 section
        lines.append(f"[L2 Drivers] ({n_l2})")
        if n_l2 == 0:
            lines.append("  (none — 尚未 expand 出 root driver)")
        else:
            for nid in sorted(n.id for n in g.nodes.values() if n.abstraction_level == 2):
                lines.append(f"  {_format_node_brief(state, nid)}")
        lines.append("")

    # ═══ Section 3: Edges (group by relation_type) ═══
    lines.append(f"=== Edges ({len(g.edges)}) ===")
    lines.append("")
    if len(g.edges) == 0:
        lines.append("(no edges)")
    else:
        # Group by type (deterministic: 按 type 字母序; 内部按 source/target 升序)
        by_type: dict[str, list] = {}
        for e in g.edges.values():
            by_type.setdefault(e.relation_type, []).append(e)
        for rtype in sorted(by_type):
            edges = sorted(by_type[rtype], key=lambda e: (e.source_node, e.target_node))
            lines.append(f"{rtype} ({len(edges)}):")
            for edge in edges:
                lines.append(f"  {_format_edge_brief(edge)}")
            lines.append("")

    # ═══ Section 4: Multi-signal verdict ═══
    lines.append("=== Multi-signal acceptance ===")
    if report is not None:
        lines.append(f"avg_consistency:    {report.avg_consistency:.3f}")
        lines.append(f"avg_essentialness:  {report.avg_essentialness:.3f}")
        lines.append(f"rollout_coverage:   {report.rollout_coverage:.3f}")
        weak_ids = sorted(report.weak_chain_l1s or [])
        if weak_ids:
            lines.append(f"weak_chain_l1s ({len(weak_ids)}): {' '.join(weak_ids)}")
        else:
            lines.append("weak_chain_l1s: (none)")
        if report.input_alignment is not None:
            lines.append(f"input_alignment:    {report.input_alignment:.3f}")
    else:
        lines.append(f"(aggregate_acceptance failed: {agg_err})")

    return [ChatEvent(type="slash_show", content="\n".join(lines))]
```

**Step 4: Update existing test in `tests/test_chat_slash_commands.py:56-65`**

The assertion `"Question:"` + `"Graph:"` + `"L0"` are all preserved in new layout. Verify pass:

```bash
.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestDispatchSlash::test_show_includes_question_and_graph_counts -v
```

Expected: PASS (no change needed — `Question:`, `Graph`, `L0` 子串都在新输出中).

If fail, update the assertion to:
```python
assert "Question:" in content
assert "=== Graph" in content
assert "[L0 Observations]" in content
```

**Step 5: Run all new tests**

```bash
.venv/bin/python -m pytest tests/chat/test_slash_show.py -v
```

Expected: 8 PASS.

**Step 6: Run /show smoke (full suite) — verify no regression**

```bash
.venv/bin/python -m pytest tests/test_chat_slash_commands.py -v -k "show"
```

Expected: 全 PASS.

**Step 7: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/chat/test_slash_show.py
# 若现有 test 需 update assertion:
git add tests/test_chat_slash_commands.py
git commit -m "$(cat <<'EOF'
chat/slash · /show 重写 4 section layout (Phase A 核心)

Session → Graph (L0/L1/L2 group, weak 双重曝光) → Edges (按 type 分组)
→ Multi-signal verdict 末尾. 复用 _format_node_brief + _format_edge_brief.
weak/lifecycle marker 优先级 lifecycle > weak. Empty graph / agg failure
fallback. 8 layout test + 现有 /show smoke 兼容.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A5: Phase A 全 suite + lint + 验证

**Step 1: Full test suite**

```bash
.venv/bin/python -m pytest -x --tb=short
```

Expected: 全 PASS (812+ → 812+ + 新加 ~20 个).

**Step 2: Lint**

```bash
.venv/bin/ruff check src/ tests/
```

Expected: 0 issues.

**Step 3: Manual smoke (optional)**

```bash
.venv/bin/python -m explain_engine.cli  # 进 REPL
> /resume   # 选一个含 graph 的旧 session
> /show     # 验输出 layout 对
> /quit
```

Expected: 4 section 都在, node 一行 `id [epi conf] 「name」: desc`, edge 按 type 分组.

**Step 4: 若全 pass, Phase A 完成. 不另开 commit (前 4 task 已 commit).**

---

## Phase B: `/graph` 新 slash

### Task B1: 加 `graphviz` Python dep

**Files:**
- Modify: `pyproject.toml`

**Step 1: Edit pyproject.toml dependencies**

Insert `"graphviz>=0.20",` into `[project] dependencies` (在现有 `"rich>=15.0.0",` 后):

```toml
dependencies = [
    "anthropic>=0.100.0",
    "graphviz>=0.20",      # Phase 12 /graph slash, dot binary 轻 wrapper
    "networkx>=3.5",
    "openai>=2.36.0",
    "prompt-toolkit>=3.0.52",
    "pydantic>=2.13.4",
    "pydantic-settings>=2.14.1",
    "python-dotenv>=1.2.2",
    "pyyaml>=6.0.3",
    "rich>=15.0.0",
    "tenacity>=9.1.4",
    "typer>=0.25.1",
]
```

**Step 2: Sync deps**

```bash
cd /Users/jinziguan/Desktop/explain_everything
uv sync
```

Expected: `graphviz` package 装入 `.venv`.

**Step 3: Verify dot binary exists (system dep)**

```bash
which dot
```

If not found:
```bash
brew install graphviz
```

Then re-check `which dot` — should print `/opt/homebrew/bin/dot` or `/usr/local/bin/dot`.

**Step 4: Smoke import**

```bash
.venv/bin/python -c "import graphviz; g = graphviz.Digraph(); g.node('a'); print(g.source)"
```

Expected: prints `digraph { a }` (or similar).

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
deps · 加 graphviz>=0.20 (Phase B /graph 渲染依赖)

Phase B Task 1: dot binary 轻 wrapper, 用于 /graph slash 生 PNG.
系统 dep brew install graphviz 必备. uv sync 落地.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B2: 加 `_get_session_tmpdir()` lazy init + atexit cleanup

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py` (加在文件末尾 helpers 区域)
- Modify test: `tests/chat/test_format_helpers.py` (或新建 `tests/chat/test_slash_graph_helpers.py`)

**Step 1: Write the failing tests**

Create `tests/chat/test_slash_graph_helpers.py`:

```python
"""Phase 12: /graph helper tests (tmpdir / digraph builder / renderer detect)."""

import os
import shutil

import pytest


class TestGetSessionTmpdir:
    def setup_method(self):
        """Reset module-global tmpdir before each test."""
        import explain_engine.chat.slash_commands as sc
        if sc._SESSION_TMPDIR is not None:
            shutil.rmtree(sc._SESSION_TMPDIR, ignore_errors=True)
        sc._SESSION_TMPDIR = None

    def test_lazy_init_first_call_creates(self):
        from explain_engine.chat.slash_commands import _get_session_tmpdir
        tmpdir = _get_session_tmpdir()
        assert os.path.isdir(tmpdir)
        assert "explain_graph_" in tmpdir

    def test_second_call_reuses(self):
        from explain_engine.chat.slash_commands import _get_session_tmpdir
        tmpdir1 = _get_session_tmpdir()
        tmpdir2 = _get_session_tmpdir()
        assert tmpdir1 == tmpdir2

    def test_atexit_registered(self, monkeypatch):
        """First call 应 atexit.register(shutil.rmtree, tmpdir, ignore_errors=True)."""
        import explain_engine.chat.slash_commands as sc

        captured: list[tuple] = []

        def fake_register(func, *args, **kwargs):
            captured.append((func, args, kwargs))

        monkeypatch.setattr("atexit.register", fake_register)
        sc._SESSION_TMPDIR = None  # force re-init

        from explain_engine.chat.slash_commands import _get_session_tmpdir
        tmpdir = _get_session_tmpdir()

        assert len(captured) == 1
        func, args, kwargs = captured[0]
        assert func is shutil.rmtree
        assert args == (tmpdir,)
        assert kwargs == {"ignore_errors": True}
```

**Step 2: Run test FAIL**

```bash
.venv/bin/python -m pytest tests/chat/test_slash_graph_helpers.py::TestGetSessionTmpdir -v
```

Expected: 3 ERROR `ImportError: cannot import name '_get_session_tmpdir'`.

**Step 3: Write impl**

Append to `src/explain_engine/chat/slash_commands.py` (after existing format helpers, near line 510):

```python
import atexit as _atexit
import shutil as _shutil
import tempfile as _tempfile

_SESSION_TMPDIR: str | None = None
"""Phase 12: lazy session-scoped tmpdir for /graph PNG output.

进程级 (非 session 级), 同一 REPL 内 /new /resume 多 session 共享,
filename 含 sid 区分 (graph_<sid>_<tick>.png). atexit 进程退出清.
退出后路径失效 — 符合用户预期 '磁盘干净'.
"""


def _get_session_tmpdir() -> str:
    """Lazy init + atexit cleanup. 不用 /graph 的 session 完全不创目录."""
    global _SESSION_TMPDIR
    if _SESSION_TMPDIR is None:
        _SESSION_TMPDIR = _tempfile.mkdtemp(prefix="explain_graph_")
        _atexit.register(_shutil.rmtree, _SESSION_TMPDIR, ignore_errors=True)
    return _SESSION_TMPDIR
```

**Step 4: Run test PASS**

```bash
.venv/bin/python -m pytest tests/chat/test_slash_graph_helpers.py::TestGetSessionTmpdir -v
```

Expected: 3 PASS.

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/chat/test_slash_graph_helpers.py
git commit -m "$(cat <<'EOF'
chat/slash · 加 _get_session_tmpdir lazy init + atexit cleanup

Phase B Task 2: /graph PNG 输出临时目录, lazy 首调创建, atexit
进程退出 rmtree (覆盖 /quit, Ctrl-C, SystemExit). 进程级 share,
filename 含 sid 区分. 3 unit test (lazy init / reuse / atexit register).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B3: 加 `_build_digraph` graphviz builder

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py`
- Modify test: `tests/chat/test_slash_graph_helpers.py`

**Step 1: Write failing tests**

Append to `tests/chat/test_slash_graph_helpers.py`:

```python
class TestBuildDigraph:
    def _make_state(self, *, l0=1, l1=1, l2=0):
        from explain_engine.chat.session import ChatState
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.nodes import VariableNode
        g = ExplanationGraph(root_question="Q")
        for i in range(l0):
            g.add_node(VariableNode(
                id=f"p_{i+1:03d}", name=f"obs{i+1}", description="d",
                abstraction_level=0, confidence=0.85, epistemic="observation",
            ))
        for i in range(l1):
            g.add_node(VariableNode(
                id=f"c_{i+1:03d}", name=f"concept{i+1}", description="d",
                abstraction_level=1, confidence=0.78, epistemic="insight",
            ))
        for i in range(l2):
            g.add_node(VariableNode(
                id=f"d_{i+1:03d}", name=f"driver{i+1}", description="d",
                abstraction_level=2, confidence=0.60, epistemic="inference",
            ))
        return ChatState(graph=g)

    def test_empty_returns_digraph_with_zero_nodes(self):
        from explain_engine.chat.session import ChatState
        from explain_engine.chat.slash_commands import _build_digraph
        from explain_engine.schema.graph import ExplanationGraph
        state = ChatState(graph=ExplanationGraph(root_question="Q"))
        dg = _build_digraph(state, weak_l1_ids=set())
        # graphviz.Digraph.source 含 'digraph' header
        assert "digraph" in dg.source.lower()

    def test_l0_node_box_lightblue(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=1, l1=0)
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "p_001" in src
        assert "shape=box" in src or "shape=\"box\"" in src
        assert "lightblue" in src

    def test_l1_node_ellipse_lightyellow(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=0, l1=1)
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "c_001" in src
        assert "ellipse" in src
        assert "lightyellow" in src

    def test_l2_node_doubleoctagon_lightcoral(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=0, l1=0, l2=1)
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "d_001" in src
        assert "doubleoctagon" in src
        assert "lightcoral" in src

    def test_node_label_contains_id_name_conf(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=1, l1=0)
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        # label = "p_001\n「obs1」\n[0.85]"
        assert "obs1" in src
        assert "0.85" in src

    def test_weak_l1_red_border(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=0, l1=1)
        dg = _build_digraph(state, weak_l1_ids={"c_001"})
        src = dg.source
        assert "color=red" in src or "color=\"red\"" in src
        assert "penwidth=2" in src or "penwidth=\"2\"" in src

    def test_decayed_node_dashed_gray(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=0, l1=1)
        state.graph.nodes["c_001"].lifecycle_state = "decayed"
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "dashed" in src
        assert "gray80" in src

    def test_stale_node_dotted(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=0, l1=1)
        state.graph.nodes["c_001"].lifecycle_state = "stale"
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "dotted" in src

    def test_edge_manifests_as_dashed(self):
        from explain_engine.chat.slash_commands import _build_digraph
        from explain_engine.schema.edges import RelationEdge
        state = self._make_state(l0=1, l1=1)
        state.graph.add_edge(RelationEdge(
            id="e1", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.85,
            mechanism_description="m",
        ))
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "dashed" in src

    def test_edge_amplifies_thick(self):
        from explain_engine.chat.slash_commands import _build_digraph
        from explain_engine.schema.edges import RelationEdge
        state = self._make_state(l0=0, l1=2)
        state.graph.add_edge(RelationEdge(
            id="e1", source_node="c_001", target_node="c_002",
            relation_type="amplifies", confidence=0.7,
            mechanism_description="m",
        ))
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "penwidth=2.5" in src or "penwidth=\"2.5\"" in src

    def test_edge_suppresses_red(self):
        from explain_engine.chat.slash_commands import _build_digraph
        from explain_engine.schema.edges import RelationEdge
        state = self._make_state(l0=0, l1=2)
        state.graph.add_edge(RelationEdge(
            id="e1", source_node="c_001", target_node="c_002",
            relation_type="suppresses", confidence=0.7,
            mechanism_description="m",
        ))
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        # edge red 而不是 weak node red (weak 不在此 test)
        assert "red" in src

    def test_edge_label_format(self):
        from explain_engine.chat.slash_commands import _build_digraph
        from explain_engine.schema.edges import RelationEdge
        state = self._make_state(l0=1, l1=1)
        state.graph.add_edge(RelationEdge(
            id="e1", source_node="c_001", target_node="p_001",
            relation_type="causes", confidence=0.80,
            mechanism_description="m",
        ))
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        # label "cau 0.80"
        assert "cau" in src
        assert "0.80" in src

    def test_rankdir_tb(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state()
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "rankdir=TB" in src or "rankdir=\"TB\"" in src
```

**Step 2: Run test FAIL**

```bash
.venv/bin/python -m pytest tests/chat/test_slash_graph_helpers.py::TestBuildDigraph -v
```

Expected: 13 ERROR `ImportError: cannot import name '_build_digraph'`.

**Step 3: Write impl**

Append to `src/explain_engine/chat/slash_commands.py`:

```python
_EDGE_TYPE_SHORT = {
    "causes": "cau",
    "amplifies": "amp",
    "suppresses": "sup",
    "constrains": "con",
    "manifests_as": "man",
}

_L_SHAPE = {0: "box", 1: "ellipse", 2: "doubleoctagon"}
_L_FILL = {0: "lightblue", 1: "lightyellow", 2: "lightcoral"}


def _build_digraph(state, weak_l1_ids: set[str]):
    """Phase 12 /graph: build graphviz.Digraph from ChatState.graph.

    Visual encoding 见 docs/plans/2026-05-19-slash-show-graph-detail-design.md
    §4.2 表格.

    Args:
        state: ChatState (含 graph).
        weak_l1_ids: 来自 aggregate_acceptance().weak_chain_l1s, 用于 weak L1 红边框.

    Returns:
        graphviz.Digraph (caller render to PNG).
    """
    import graphviz

    dg = graphviz.Digraph(format="png")
    dg.attr(rankdir="TB")
    dg.attr("node", style="filled", fontname="Helvetica")

    for node in state.graph.nodes.values():
        shape = _L_SHAPE.get(node.abstraction_level, "box")
        fill = _L_FILL.get(node.abstraction_level, "white")
        label = f"{node.id}\n「{node.name}」\n[{node.confidence:.2f}]"

        attrs: dict[str, str] = {
            "shape": shape,
            "fillcolor": fill,
            "label": label,
        }

        # lifecycle 优先 (decayed > stale > default)
        if node.lifecycle_state == "decayed":
            attrs["style"] = "dashed,filled"
            attrs["fillcolor"] = "gray80"
        elif node.lifecycle_state == "stale":
            attrs["style"] = "dotted,filled"

        # weak L1: 红边框 (与 lifecycle 视觉叠加)
        if node.id in weak_l1_ids:
            attrs["color"] = "red"
            attrs["penwidth"] = "2"

        dg.node(node.id, **attrs)

    for edge in state.graph.edges.values():
        short = _EDGE_TYPE_SHORT.get(edge.relation_type, edge.relation_type[:3])
        label = f"{short} {edge.confidence:.2f}"

        attrs: dict[str, str] = {"label": label}

        if edge.relation_type == "amplifies":
            attrs["penwidth"] = "2.5"
        elif edge.relation_type == "suppresses":
            attrs["color"] = "red"
        elif edge.relation_type == "constrains":
            attrs["color"] = "blue"
        elif edge.relation_type == "manifests_as":
            attrs["style"] = "dashed"

        dg.edge(edge.source_node, edge.target_node, **attrs)

    return dg
```

**Step 4: Run test PASS**

```bash
.venv/bin/python -m pytest tests/chat/test_slash_graph_helpers.py::TestBuildDigraph -v
```

Expected: 13 PASS.

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/chat/test_slash_graph_helpers.py
git commit -m "$(cat <<'EOF'
chat/slash · 加 _build_digraph (Phase B 核心 visual encoder)

Phase B Task 3: ChatState.graph → graphviz.Digraph. L0/L1/L2 shape+
fillcolor 编码 + lifecycle decayed/stale 视觉 + weak L1 红边框 +
edge type style (manifests_as dashed, amplifies 粗, suppresses red,
constrains blue, causes default). edge label = type 3 字 + conf.
rankdir TB. 13 unit test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B4: 加 `_detect_inline_renderer` env+which 检测

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py`
- Modify test: `tests/chat/test_slash_graph_helpers.py`

**Step 1: Write failing tests**

Append to `tests/chat/test_slash_graph_helpers.py`:

```python
class TestDetectInlineRenderer:
    """检测顺序: iTerm2 → Kitty/Ghostty → chafa → None."""

    def test_iterm_detected(self, monkeypatch):
        from explain_engine.chat.slash_commands import _detect_inline_renderer
        monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/imgcat" if x == "imgcat" else None)
        cmd, name = _detect_inline_renderer("/tmp/foo.png")
        assert name == "iterm"
        assert cmd[0] == "imgcat"
        assert "/tmp/foo.png" in cmd

    def test_kitty_window_id_detected(self, monkeypatch):
        from explain_engine.chat.slash_commands import _detect_inline_renderer
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.setenv("KITTY_WINDOW_ID", "1")
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/kitty" if x == "kitty" else None)
        cmd, name = _detect_inline_renderer("/tmp/foo.png")
        assert name == "kitty"
        assert cmd[:3] == ["kitty", "+kitten", "icat"]

    def test_ghostty_detected(self, monkeypatch):
        from explain_engine.chat.slash_commands import _detect_inline_renderer
        monkeypatch.setenv("TERM_PROGRAM", "ghostty")
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/kitty" if x == "kitty" else None)
        cmd, name = _detect_inline_renderer("/tmp/foo.png")
        assert name == "kitty"

    def test_chafa_fallback(self, monkeypatch):
        from explain_engine.chat.slash_commands import _detect_inline_renderer
        monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")  # 非 iterm/ghostty
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/chafa" if x == "chafa" else None)
        cmd, name = _detect_inline_renderer("/tmp/foo.png")
        assert name == "chafa"
        assert cmd[0] == "chafa"
        assert "--size" in cmd

    def test_none_when_all_unavailable(self, monkeypatch):
        from explain_engine.chat.slash_commands import _detect_inline_renderer
        monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.setattr("shutil.which", lambda x: None)
        cmd, name = _detect_inline_renderer("/tmp/foo.png")
        assert cmd is None
        assert name == "none"

    def test_iterm_missing_imgcat_falls_to_chafa(self, monkeypatch):
        """iTerm 但 imgcat 不在 PATH → 试下一档 chafa."""
        from explain_engine.chat.slash_commands import _detect_inline_renderer
        monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/chafa" if x == "chafa" else None)
        cmd, name = _detect_inline_renderer("/tmp/foo.png")
        assert name == "chafa"
```

**Step 2: Run test FAIL**

```bash
.venv/bin/python -m pytest tests/chat/test_slash_graph_helpers.py::TestDetectInlineRenderer -v
```

Expected: 6 ERROR ImportError.

**Step 3: Write impl**

Append to `src/explain_engine/chat/slash_commands.py`:

```python
def _detect_inline_renderer(png_path: str) -> tuple[list[str] | None, str]:
    """Phase 12 /graph: detect terminal capability, return (cmd, renderer_name).

    检测顺序 (按优先级): iTerm2 → Kitty/Ghostty → chafa → None.

    Returns:
        (cmd_list, name) — cmd_list None 表示无 inline renderer 可用.
        name ∈ {"iterm", "kitty", "chafa", "none"}.

    Note:
        iTerm 检 imgcat 在 PATH (iTerm2 自带 utilities, 但用户可能没装).
        若 iTerm 检测到但 imgcat 不在 → fall through 下一档 (Kitty/chafa).
    """
    import os
    import shutil

    term_program = os.environ.get("TERM_PROGRAM", "")
    kitty_window = os.environ.get("KITTY_WINDOW_ID", "")

    # 1. iTerm2 + imgcat
    if term_program == "iTerm.app" and shutil.which("imgcat"):
        return ["imgcat", png_path], "iterm"

    # 2. Kitty / Ghostty (kitty graphics protocol)
    if (kitty_window or term_program == "ghostty") and shutil.which("kitty"):
        return ["kitty", "+kitten", "icat", png_path], "kitty"

    # 3. chafa (通用 Unicode block art)
    if shutil.which("chafa"):
        return ["chafa", "--size", "100x40", png_path], "chafa"

    # 4. None
    return None, "none"
```

**Step 4: Run test PASS**

```bash
.venv/bin/python -m pytest tests/chat/test_slash_graph_helpers.py::TestDetectInlineRenderer -v
```

Expected: 6 PASS.

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/chat/test_slash_graph_helpers.py
git commit -m "$(cat <<'EOF'
chat/slash · 加 _detect_inline_renderer (iTerm/Kitty/chafa)

Phase B Task 4: 检测终端 inline image capability 顺序 iTerm2 →
Kitty/Ghostty → chafa → none. 返 (cmd_list, name). iTerm 检 imgcat
在 PATH, fall-through 下一档兜底. 6 unit test (含 fall-through).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B5: 加 `_handle_graph` 主 handler

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py`
- Create test: `tests/chat/test_slash_graph.py`

**Step 1: Write failing tests**

Create `tests/chat/test_slash_graph.py`:

```python
"""Phase 12: /graph slash handler test."""

import os
import shutil

import pytest

from explain_engine.chat.slash_commands import dispatch_slash


def _make_session_with_graph(sid: str, *, l0=1, l1=1):
    from tests.test_chat_session import _make_done_session
    from explain_engine.chat.session import ChatSession
    from explain_engine.schema.nodes import VariableNode

    _make_done_session(sid)
    chat = ChatSession(sid)
    g = chat.state.graph
    for nid in list(g.nodes):
        g.remove_node(nid)
    for i in range(l0):
        g.add_node(VariableNode(
            id=f"p_{i+1:03d}", name=f"obs{i+1}", description="d",
            abstraction_level=0, confidence=0.85, epistemic="observation",
        ))
    for i in range(l1):
        g.add_node(VariableNode(
            id=f"c_{i+1:03d}", name=f"concept{i+1}", description="d",
            abstraction_level=1, confidence=0.78, epistemic="insight",
        ))
    return chat


class TestSlashGraph:
    @pytest.mark.asyncio
    async def test_empty_graph_returns_warning(self):
        """0 nodes → 不调 graphviz, 输 '(empty graph, nothing to render)'."""
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        _make_done_session("s_graph_001")
        chat = ChatSession("s_graph_001")
        for nid in list(chat.state.graph.nodes):
            chat.state.graph.remove_node(nid)
        events = await dispatch_slash(chat, "/graph")
        assert events[0].type == "slash_graph"
        assert "empty graph" in events[0].content.lower()

    @pytest.mark.asyncio
    async def test_dot_missing_returns_friendly_error(self, monkeypatch):
        """dot binary 缺 → 友好 error + brew install 提示, 不 crash."""
        chat = _make_session_with_graph("s_graph_002")
        monkeypatch.setattr("shutil.which", lambda x: None if x == "dot" else "/fake")
        events = await dispatch_slash(chat, "/graph")
        assert events[0].type == "slash_graph"
        content = events[0].content.lower()
        assert "dot" in content
        assert "brew install graphviz" in content

    @pytest.mark.asyncio
    async def test_renders_via_chafa(self, monkeypatch, tmp_path):
        """Non-empty graph + chafa available → subprocess called with chafa."""
        chat = _make_session_with_graph("s_graph_003")

        # Mock dot binary present
        original_which = shutil.which
        def fake_which(x):
            if x == "dot":
                return "/usr/local/bin/dot"
            if x == "chafa":
                return "/usr/local/bin/chafa"
            return None
        monkeypatch.setattr("shutil.which", fake_which)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)

        # Mock graphviz.Digraph.render (don't actually shell out to dot)
        rendered: list[str] = []
        import graphviz
        original_render = graphviz.Digraph.render

        def fake_render(self, filename, **kwargs):
            png_path = str(filename) + "." + kwargs.get("format", "png")
            # write a dummy file so subprocess "succeeds" reading it
            with open(png_path, "wb") as f:
                f.write(b"\x89PNG\r\n")
            rendered.append(png_path)
            return png_path

        monkeypatch.setattr("graphviz.Digraph.render", fake_render)

        # Mock subprocess.run to capture inline display call
        called: list[list[str]] = []
        import subprocess

        def fake_run(cmd, **kwargs):
            called.append(cmd)
            class Result:
                returncode = 0
                stdout = b""
                stderr = b""
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)

        events = await dispatch_slash(chat, "/graph")
        assert events[0].type == "slash_graph"
        # chafa called with png path
        assert any("chafa" in c[0] for c in called if c)

    @pytest.mark.asyncio
    async def test_no_renderer_outputs_path(self, monkeypatch):
        """No iTerm/Kitty/chafa → output PNG path + install hint, 不 crash."""
        chat = _make_session_with_graph("s_graph_004")
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/dot" if x == "dot" else None)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)

        import graphviz
        def fake_render(self, filename, **kwargs):
            png_path = str(filename) + ".png"
            with open(png_path, "wb") as f:
                f.write(b"\x89PNG")
            return png_path
        monkeypatch.setattr("graphviz.Digraph.render", fake_render)

        events = await dispatch_slash(chat, "/graph")
        content = events[0].content
        assert "PNG saved" in content
        assert "chafa" in content  # install hint

    @pytest.mark.asyncio
    async def test_output_contains_multisignal_footer(self, monkeypatch):
        """Output 末尾含 multi-signal verdict (consistency / essentialness / coverage)."""
        chat = _make_session_with_graph("s_graph_005")
        monkeypatch.setattr("shutil.which", lambda x: "/usr/local/bin/dot" if x == "dot" else None)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)

        import graphviz
        monkeypatch.setattr(
            "graphviz.Digraph.render",
            lambda self, filename, **kw: open(str(filename) + ".png", "wb").write(b"\x89PNG") or str(filename) + ".png",
        )

        events = await dispatch_slash(chat, "/graph")
        content = events[0].content
        assert "Multi-signal" in content or "consistency" in content.lower()
```

**Step 2: Run test FAIL**

```bash
.venv/bin/python -m pytest tests/chat/test_slash_graph.py -v
```

Expected: 5 FAIL — `_handle_graph` undefined (also /graph not registered yet, dispatch returns error).

**Step 3: Write `_handle_graph` impl**

Insert into `src/explain_engine/chat/slash_commands.py` (after `_handle_show`, around line 144):

```python
async def _handle_graph(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 12 (2026-05-19): /graph — visual rendering via graphviz inline.

    Pipeline:
      1. Empty graph → return warning, no graphviz call.
      2. Check dot binary present, friendly error if missing.
      3. Build graphviz.Digraph from chat.state.graph (含 weak_l1 marker).
      4. Render PNG to session tmpdir.
      5. Detect terminal capability (iTerm/Kitty/chafa), inline display.
         若无 inline renderer 可用, 输 PNG path + install hint.
      6. Footer: PNG path + multi-signal verdict 4 行.

    设计详见 docs/plans/2026-05-19-slash-show-graph-detail-design.md §4.2.
    """
    import os
    import shutil
    import subprocess

    from explain_engine.chat.session import ChatEvent
    from explain_engine.engines.simulation import aggregate_acceptance

    state = chat.state
    g = state.graph

    # Edge: empty graph
    if len(g.nodes) == 0:
        return [ChatEvent(
            type="slash_graph",
            content="(empty graph, nothing to render)",
        )]

    # Edge: dot binary missing
    if shutil.which("dot") is None:
        return [ChatEvent(
            type="slash_graph",
            content=(
                "dot binary not found.\n"
                "Install: brew install graphviz"
            ),
        )]

    # Compute weak_l1_ids (gracefully handle agg failure — render still works)
    weak_l1_ids: set[str] = set()
    report = None
    try:
        report = aggregate_acceptance(state)
        weak_l1_ids = set(report.weak_chain_l1s or [])
    except Exception:
        pass  # render without weak marker; verdict section will say "(failed)"

    # Build + render
    dg = _build_digraph(state, weak_l1_ids=weak_l1_ids)
    tmpdir = _get_session_tmpdir()
    tick = state.tick if hasattr(state, "tick") else 0
    base = os.path.join(tmpdir, f"graph_{chat.sid}_{tick}")
    png_path = dg.render(filename=base, cleanup=True)
    # graphviz.render returns the output file path (e.g. base.png)

    # Header
    n_l0 = sum(1 for n in g.nodes.values() if n.abstraction_level == 0)
    n_l1 = sum(1 for n in g.nodes.values() if n.abstraction_level == 1)
    n_l2 = sum(1 for n in g.nodes.values() if n.abstraction_level == 2)
    header = (
        f"/graph tick={tick} · {len(g.nodes)} nodes "
        f"({n_l0} L0 / {n_l1} L1 / {n_l2} L2), {len(g.edges)} edges"
    )

    # Inline display
    cmd, renderer = _detect_inline_renderer(png_path)
    inline_msg = ""
    if cmd is not None:
        try:
            subprocess.run(cmd, check=False)
            inline_msg = f"(rendered inline via {renderer})"
        except Exception as exc:
            inline_msg = f"(inline render via {renderer} failed: {type(exc).__name__})"
    else:
        inline_msg = "(install chafa for inline preview: brew install chafa)"

    # Footer: PNG path + multi-signal verdict
    footer_lines = [
        "",
        inline_msg,
        f"PNG: {png_path}",
        "",
    ]
    if report is not None:
        footer_lines.append(
            f"Multi-signal: consistency={report.avg_consistency:.3f} "
            f"essentialness={report.avg_essentialness:.3f} "
            f"coverage={report.rollout_coverage:.3f}"
        )
        weak_ids = sorted(report.weak_chain_l1s or [])
        if weak_ids:
            footer_lines.append(f"weak L1: {' '.join(weak_ids)}")
    else:
        footer_lines.append("Multi-signal: (aggregate_acceptance failed)")

    content = header + "\n" + "\n".join(footer_lines)
    return [ChatEvent(type="slash_graph", content=content)]
```

**Step 4: Register /graph (tweak DEFAULT_COMMANDS for tests to pass)**

Insert into `DEFAULT_COMMANDS` tuple right after `SlashCommand("show", ...)` (around line 1087):

```python
SlashCommand("graph", "渲染 graph 可视化 (graphviz inline via iTerm/Kitty/chafa).", _handle_graph),
```

**Step 5: Run test PASS**

```bash
.venv/bin/python -m pytest tests/chat/test_slash_graph.py -v
```

Expected: 5 PASS.

**Step 6: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/chat/test_slash_graph.py
git commit -m "$(cat <<'EOF'
chat/slash · 加 /graph slash + _handle_graph (Phase B 主 handler)

Phase B Task 5: /graph 注册 (18 → 19 slash). 主 pipeline: empty
graph 短路, dot binary missing 友好 error, 否则 _build_digraph →
render PNG 进 _get_session_tmpdir, _detect_inline_renderer 后
subprocess 显图. footer 输 PNG path + multi-signal verdict. 5 e2e
test (empty/dot-missing/chafa-render/no-renderer/multi-signal).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B6: 更新 /help + slash registry test 验 19

**Files:**
- Modify test: `tests/test_chat_slash_commands.py` (`TestWave4Registry` 或类似 count assertion)
- Modify: `src/explain_engine/chat/slash_commands.py` (`_handle_help` 已自动列 DEFAULT_COMMANDS, 无需改)

**Step 1: 找现有 registry test 验当前 slash 数**

```bash
grep -n "len(DEFAULT_COMMANDS)\|18 \|len.*== 18" /Users/jinziguan/Desktop/explain_everything/tests/test_chat_slash_commands.py
```

**Step 2: Update assertion 18 → 19**

Find the test (likely in `TestWave4Registry` or `TestSlashRegistry`):
- Update `len(DEFAULT_COMMANDS) == 18` → `== 19`
- Or update the `required` set to include `"graph"`

Concrete edit will depend on which test exists; locate via grep above.

**Step 3: Verify /help test mentions /graph**

If `test_help_lists_commands_and_tools` exists with explicit name list, add `"graph"` to it. Otherwise /help iterates DEFAULT_COMMANDS so it auto-includes.

**Step 4: Run all slash tests**

```bash
.venv/bin/python -m pytest tests/test_chat_slash_commands.py -v
```

Expected: 全 PASS.

**Step 5: Commit**

```bash
git add tests/test_chat_slash_commands.py
git commit -m "$(cat <<'EOF'
test · slash registry 18 → 19 (新加 /graph)

Phase B Task 6: update registry assertion 反映新 /graph slash.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B7: Phase B 全 suite + lint + manual smoke

**Step 1: Full test suite**

```bash
.venv/bin/python -m pytest -x --tb=short
```

Expected: 全 PASS (812 + ~40 new = ~850+).

**Step 2: Lint**

```bash
.venv/bin/ruff check src/ tests/
```

Expected: 0 issues. If `_atexit` / `_shutil` / `_tempfile` 命名被 ruff B 系列 flag (e.g. naming convention), 改名或加 noqa.

**Step 3: Manual smoke**

```bash
.venv/bin/python -m explain_engine.cli
> /resume   # 选含 graph 的旧 session
> /graph    # 验渲染
```

Expected:
- iTerm2 用户: 内联显图
- 其他终端: 内联渲染 (chafa) 或 输 PNG path + install hint
- 无 crash

**Step 4: 若全 pass, Phase B 完成. 不另开 commit.**

---

## Phase C: README + acceptance 文档 update

### Task C1: 更新 README + acceptance

**Files:**
- Modify: `README.md` (Phase 11 milestone 表 + slash 命令拓扑)
- Modify: `docs/plans/2026-05-18-phase11-repl-unification-acceptance.md` (新 /graph 加入 acceptance)

**Step 1: Update README slash 列表 18 → 19**

```bash
grep -n "18 slash\|18 default\|18 total" /Users/jinziguan/Desktop/explain_everything/README.md
```

Update mentions to "19 slash" (or "18 default + 1 alias = 19" — verify current phrasing). Add `/graph` to slash command listing if present.

**Step 2: Add manual acceptance step**

Add to `docs/plans/2026-05-18-phase11-repl-unification-acceptance.md`:

```markdown
### Step 11: /graph 可视化 (Phase 12)

Steps:
1. `/resume` 选含 graph 的旧 session (e.g. >= 5 nodes)
2. `/graph`
3. 验证:
   - 输出 header `/graph tick=N · X nodes (...)` 1 行
   - 渲染图 inline (iTerm/Kitty/chafa) 或 显 PNG path + install hint
   - footer 含 `PNG: /tmp/explain_graph_.../...png`
   - 末尾 multi-signal 1 行
4. `/quit`
5. 验证: `ls /tmp/explain_graph_*` 应不存在 (atexit cleanup 已清)
```

**Step 3: Commit**

```bash
git add README.md docs/plans/2026-05-18-phase11-repl-unification-acceptance.md
git commit -m "$(cat <<'EOF'
docs · README + acceptance 加 /graph (Phase C)

slash 18 → 19. acceptance 加 Step 11 验 /graph 渲染 + 退出
PNG 清理.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final Verification

```bash
# Full suite
.venv/bin/python -m pytest -x --tb=short

# Lint
.venv/bin/ruff check src/ tests/

# Git log review
git log --oneline -15

# Status clean
git status
```

Expected:
- All tests pass (~850+)
- Ruff 0 issues
- 12-ish commits Phase A1-A4 + B1-B7 + C1 (skip A5/B7 if no extra commit needed)
- Git status: clean except for known unstaged (`.env.bak`, `obsidian/`)

---

## Risk & Rollback

**Risk 1: graphviz Python pkg incompatible with current uv lock**
- Mitigation: `uv add graphviz>=0.20` 显式装, 若 lock 冲突手动 resolve

**Risk 2: dot binary 缺导致 acceptance 失败**
- Mitigation: handler 已检测, 输友好 error 不 crash. User 装 graphviz 系统包后即可

**Risk 3: chafa output 在某些 terminal 显示乱码 (unicode block 不支持)**
- Mitigation: 不影响 /graph 命令本身, user 可改用 iTerm2 (有 imgcat) 或直接 open PNG path

**Risk 4: atexit 在 pytest 进程结束时 cleanup race condition**
- Mitigation: 已用 `ignore_errors=True`, 失败也不 crash

**Rollback**: 每 task 独立 commit. 任何 step 出问题:
```bash
git revert <commit_sha>  # 或多个连续
```
完全回滚: revert C1 → B6 → B5 → ... → A1, 或 `git reset --hard 8d4b8f2` (Phase 11 末尾 HEAD).
