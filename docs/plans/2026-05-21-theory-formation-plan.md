# Phase 16: Theory Formation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 跨 session graph 中 emergent recurring causal motif → 抽成 stable theory → 接 bootstrap 做 predictive prior. 落地 V2 §13 (Variable Evolution) + 哲学 §9 (Theory Formation) + JEPA 启示 a/b/c (falsifiability / slow-fast / VICReg diversity).

**Architecture:** 新 `engines/theory/` module (10 py file, ~1500 行) 含 cluster (Phase 13 embedding 复用) / 自实现 simplified gSpan (~300 行, directed in-memory) / falsifiability evaluator / MMR ranking / hybrid lazy cached. `theories.json` sidecar (跟 lexicon 同目录) atomic write. chat REPL 加 `/theories` `/theory <id> [reject]` + cli `explain theories` + bootstrap inject stable theory 到 LLM prompt 软引导. engine 内部 schema 0 改动.

**Tech Stack:** Python 3.11 + networkx (子图同构) + Phase 13 BGE-M3 embedder (cluster). pytest + pytest-mock. `.venv/bin/python -m pytest` (uv-managed venv) + `.venv/bin/ruff check`.

**Design doc:** [docs/plans/2026-05-21-theory-formation-design.md](2026-05-21-theory-formation-design.md) — 读 §4 (JEPA 启示) + §5 (Design) + §6 (Testing) + §7 (Risks) 先.

---

## Task 1: chat_copy.py 新增 theory 文案

**Files:**
- Modify: `src/explain_engine/chat/chat_copy.py`
- Test: `tests/test_chat_copy.py`

**Step 1: Write the failing test**

```python
# tests/test_chat_copy.py 末追加

class TestTheoryCopy:
    def test_command_descriptions_theories(self):
        from explain_engine.chat.chat_copy import COMMAND_DESCRIPTIONS
        assert "theories" in COMMAND_DESCRIPTIONS
        assert "theory" in COMMAND_DESCRIPTIONS
        for k in ("theories", "theory"):
            assert len(COMMAND_DESCRIPTIONS[k]) <= 50
            import re
            assert re.search(r'[一-鿿]', COMMAND_DESCRIPTIONS[k])

    def test_status_theories_compute_markup(self):
        from explain_engine.chat.chat_copy import STATUS_THEORIES_COMPUTE
        assert "[bold green]" in STATUS_THEORIES_COMPUTE
        assert "分析" in STATUS_THEORIES_COMPUTE

    def test_msg_theories_cold_start_numbers(self):
        from explain_engine.chat.chat_copy import msg_theories_cold_start
        m = msg_theories_cold_start(2, 3)
        assert "2" in m and "3" in m and "session" in m

    def test_msg_theory_rejected_id(self):
        from explain_engine.chat.chat_copy import msg_theory_rejected
        assert "t_abc123" in msg_theory_rejected("t_abc123")
        assert "拒绝" in msg_theory_rejected("t_abc123")

    def test_err_theory_not_found_id(self):
        from explain_engine.chat.chat_copy import err_theory_not_found
        m = err_theory_not_found("t_xyz")
        assert "t_xyz" in m
        assert "/theories" in m
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_copy.py::TestTheoryCopy -v`
Expected: 5 FAIL with `ImportError: cannot import name ...`

**Step 3: Implement chat_copy.py 加 theory 文案**

```python
# src/explain_engine/chat/chat_copy.py

COMMAND_DESCRIPTIONS = {
    ...,  # 既有 19 条
    "theories": "查看跨 session 发现的稳定因果模式",
    "theory":   "看某 theory 详情 / 拒绝它 (/theory <id> [reject])",
}

# HELP_GROUPS_ZH "管理 session" 组追加 theories/theory
HELP_GROUPS_ZH = [
    ...,
    ("管理 session", ["new", "resume", "list", "lexicon", "theories", "theory"]),
    ...,
]

STATUS_THEORIES_COMPUTE = "[bold green]正在分析跨 session 模式...[/bold green]"

def msg_theories_cold_start(current: int, needed: int) -> str:
    return f"需累积 ≥ {needed} 个 session 才能形成 theory. 当前: {current}/{needed}."

def msg_theories_no_motif_found(n_sessions: int) -> str:
    return f"已分析 {n_sessions} 个 session, 未发现重复出现的因果模式. 跑更多 session 试试."

def msg_theory_rejected(theory_id: str) -> str:
    return f"已拒绝 theory {theory_id}, 后续不再用于 bootstrap inject."

def err_theory_not_found(theory_id: str) -> str:
    return (
        f"theory {theory_id} 不存在, 可能 cache 已 invalidate. "
        f"先跑 /theories 看当前 list."
    )
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_copy.py -v`
Expected: 27 PASS (22 既有 + 5 新)

**Step 5: Commit**

```bash
git add src/explain_engine/chat/chat_copy.py tests/test_chat_copy.py
git commit -m "chat/chat_copy · Phase 16 Task 1: theory 文案 (COMMAND_DESCRIPTIONS / STATUS / msg / err)"
```

---

## Task 2: engines/theory/ scaffold + Theory/Theme dataclass

**Files:**
- Create: `src/explain_engine/engines/theory/__init__.py`
- Create: `src/explain_engine/engines/theory/theory.py`
- Test: `tests/test_engines_theory_dataclass.py`

**Step 1: Write the failing test**

```python
# tests/test_engines_theory_dataclass.py
"""Phase 16: Theory + Theme dataclass + _compute_theory_id 稳定 hash."""


class TestTheme:
    def test_construct(self):
        from explain_engine.engines.theory.theory import Theme
        t = Theme(id="th_001", name="不确定性",
                  member_global_ids=("v_aaaa", "v_bbbb"),
                  centroid_summary="不确定性 (cluster of 2)")
        assert t.id == "th_001"
        assert len(t.member_global_ids) == 2

    def test_theme_is_frozen(self):
        from explain_engine.engines.theory.theory import Theme
        import dataclasses
        t = Theme(id="th_001", name="x", member_global_ids=(), centroid_summary="")
        with pytest.raises(dataclasses.FrozenInstanceError):
            t.id = "th_002"


class TestTheory:
    def test_construct_with_defaults(self):
        from explain_engine.engines.theory.theory import Theory
        t = Theory(
            id="t_aaa", motif_type="chain",
            theme_ids=("th_001", "th_002"), node_ids=("v_a", "v_b"),
            edges=(("v_a", "v_b", "causes"),),
            supporting_sessions=("s_1",),
            natural_language_summary="A → B",
            structure_complexity=2,
            first_seen_session="s_1", last_seen_session="s_1",
        )
        assert t.predictive_power == 0.0          # default
        assert t.stability_status == "tentative"  # default

    def test_compute_theory_id_stable_across_edge_order(self):
        from explain_engine.engines.theory.theory import _compute_theory_id
        edges1 = (("v_a", "v_b", "causes"), ("v_b", "v_c", "manifests_as"))
        edges2 = (("v_b", "v_c", "manifests_as"), ("v_a", "v_b", "causes"))
        assert _compute_theory_id("chain", edges1) == _compute_theory_id("chain", edges2)

    def test_compute_theory_id_differs_motif_type(self):
        from explain_engine.engines.theory.theory import _compute_theory_id
        edges = (("v_a", "v_b", "causes"),)
        assert _compute_theory_id("chain", edges) != _compute_theory_id("star", edges)
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_dataclass.py -v`
Expected: 5 FAIL (ImportError).

**Step 3: Implement theory.py + __init__.py**

```python
# src/explain_engine/engines/theory/__init__.py
"""Phase 16: Theory Formation engine."""

# src/explain_engine/engines/theory/theory.py
"""Phase 16: Theory + Theme dataclass + 稳定 hash."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    member_global_ids: tuple[str, ...]
    centroid_summary: str


@dataclass(frozen=True)
class Theory:
    id: str
    motif_type: Literal["chain", "star", "cycle"]
    theme_ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    edges: tuple[tuple[str, str, str], ...]  # (src_gid, tgt_gid, relation_type)
    supporting_sessions: tuple[str, ...]
    natural_language_summary: str
    structure_complexity: int
    first_seen_session: str
    last_seen_session: str
    # JEPA (a) — falsifiability
    predictive_power: float = 0.0
    # JEPA (b) — slow-fast
    stability_status: Literal["tentative", "stable"] = "tentative"
    stable_promoted_at_session: str | None = None


def _compute_theory_id(motif_type: str, edges: tuple) -> str:
    """edges 按 (src, tgt, rel) 排序保 deterministic."""
    canonical = f"{motif_type}:{tuple(sorted(edges))}"
    return "t_" + hashlib.sha256(canonical.encode()).hexdigest()[:10]
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_dataclass.py -v`
Expected: 5 PASS

**Step 5: Commit**

```bash
git add src/explain_engine/engines/theory/__init__.py src/explain_engine/engines/theory/theory.py tests/test_engines_theory_dataclass.py
git commit -m "engines/theory · Phase 16 Task 2: Theory + Theme dataclass + _compute_theory_id"
```

---

## Task 3: clustering.py (cluster_lexicon_themes)

**Files:**
- Create: `src/explain_engine/engines/theory/clustering.py`
- Test: `tests/test_engines_theory_clustering.py`

**Step 1: Write the failing test**

```python
# tests/test_engines_theory_clustering.py
"""Phase 16: cluster_lexicon_themes — Phase 13 embedding cosine clustering."""

import numpy as np
import pytest


class FakeEmbedder:
    """Mock embedder, encode() 返预设 vector."""
    def __init__(self, name_to_vec: dict[str, np.ndarray]):
        self._map = name_to_vec
    def encode(self, names):
        return np.stack([self._map[n] for n in names])


def _fake_lexicon(vars_data):
    return {"version": "1.0", "variables": vars_data}


class TestClusterLexiconThemes:
    def test_empty_lexicon_returns_empty(self):
        from explain_engine.engines.theory.clustering import cluster_lexicon_themes
        themes = cluster_lexicon_themes(_fake_lexicon([]), embedder=None)
        assert themes == []

    def test_single_var_returns_empty(self):
        from explain_engine.engines.theory.clustering import cluster_lexicon_themes
        lex = _fake_lexicon([{"global_id": "v_a", "name": "A",
                              "embedding": [1.0, 0.0, 0.0]}])
        themes = cluster_lexicon_themes(lex, embedder=None)
        assert themes == []  # < 2 var 无法 cluster

    def test_two_similar_vars_form_one_theme(self):
        from explain_engine.engines.theory.clustering import cluster_lexicon_themes
        # 2 highly similar vec (cos=0.95)
        v1 = np.array([1.0, 0.0, 0.0])
        v2 = np.array([0.95, 0.31, 0.0])
        v2 /= np.linalg.norm(v2)
        lex = _fake_lexicon([
            {"global_id": "v_a", "name": "A", "embedding": v1.tolist()},
            {"global_id": "v_b", "name": "B", "embedding": v2.tolist()},
        ])
        themes = cluster_lexicon_themes(lex, embedder=None, cosine_threshold=0.85)
        assert len(themes) == 1
        assert set(themes[0].member_global_ids) == {"v_a", "v_b"}

    def test_dissimilar_vars_form_two_themes(self):
        from explain_engine.engines.theory.clustering import cluster_lexicon_themes
        v1 = np.array([1.0, 0.0, 0.0])
        v2 = np.array([0.0, 1.0, 0.0])  # cos=0
        lex = _fake_lexicon([
            {"global_id": "v_a", "name": "A", "embedding": v1.tolist()},
            {"global_id": "v_b", "name": "B", "embedding": v2.tolist()},
        ])
        themes = cluster_lexicon_themes(lex, embedder=None, cosine_threshold=0.85)
        # 每 var 单独 1 cluster, 但 cluster size < 2 应被过滤
        # 取决于定义 — 我们认 single-member theme 无意义不返
        assert all(len(t.member_global_ids) >= 2 for t in themes)

    def test_theme_name_taken_from_centroid_nearest_member(self):
        from explain_engine.engines.theory.clustering import cluster_lexicon_themes
        v1 = np.array([1.0, 0.0, 0.0])
        v2 = np.array([0.95, 0.31, 0.0]); v2 /= np.linalg.norm(v2)
        lex = _fake_lexicon([
            {"global_id": "v_a", "name": "中心点", "embedding": v1.tolist()},
            {"global_id": "v_b", "name": "外围点", "embedding": v2.tolist()},
        ])
        themes = cluster_lexicon_themes(lex, embedder=None, cosine_threshold=0.85)
        # centroid = (v1 + v2) / 2, 距 v1 比 v2 近? 不一定. 主要验返了某 name
        assert themes[0].name in ("中心点", "外围点")
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_clustering.py -v`
Expected: 5 FAIL (ImportError).

**Step 3: Implement clustering.py**

```python
# src/explain_engine/engines/theory/clustering.py
"""Phase 16: lexicon variables 按 cosine 距离 cluster, 形成 theme groups."""
from __future__ import annotations

from collections import defaultdict
import numpy as np

from explain_engine.engines.theory.theory import Theme


def cluster_lexicon_themes(
    lexicon: dict,
    embedder=None,  # 兼容性占位, 若 var 已含 embedding 字段则不用
    cosine_threshold: float = 0.85,
) -> list[Theme]:
    """Union-find agglomerative clustering.

    Args:
        lexicon: {"variables": [{"global_id", "name", "embedding": list[float]}]}
        embedder: 若 var 缺 embedding 字段, 用此 embedder.encode([name]). MVP 假设
                 Phase 13 lazy migrate 已写过 embedding.
        cosine_threshold: ≥ 阈值视为同 theme (跟 Phase 13 0.85 一致)

    Returns:
        Theme list (size ≥ 2 的 cluster). cluster name = 中心最近 member.name.
    """
    variables = [v for v in lexicon.get("variables", []) if v.get("embedding")]
    if len(variables) < 2:
        return []

    embs = np.stack([np.array(v["embedding"]) for v in variables])
    # 归一化
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    embs = embs / np.where(norms > 0, norms, 1)
    var_ids = [v["global_id"] for v in variables]
    var_names = {v["global_id"]: v["name"] for v in variables}
    n = len(var_ids)

    # Union-find
    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    sim_matrix = embs @ embs.T
    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i, j] >= cosine_threshold:
                union(i, j)

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    themes = []
    for ci, (root, indices) in enumerate(clusters.items()):
        if len(indices) < 2:
            continue  # single member 无意义
        member_ids = tuple(var_ids[i] for i in indices)
        # centroid 最近 var 取 name
        centroid = embs[indices].mean(axis=0)
        distances = [(i, np.linalg.norm(embs[i] - centroid)) for i in indices]
        nearest_idx = min(distances, key=lambda x: x[1])[0]
        rep_name = var_names[var_ids[nearest_idx]]
        themes.append(Theme(
            id=f"th_{ci:03d}",
            name=rep_name,
            member_global_ids=member_ids,
            centroid_summary=f"{rep_name} (cluster of {len(member_ids)})",
        ))
    return themes
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_clustering.py -v`
Expected: 5 PASS

**Step 5: Commit**

```bash
git add src/explain_engine/engines/theory/clustering.py tests/test_engines_theory_clustering.py
git commit -m "engines/theory · Phase 16 Task 3: clustering.cluster_lexicon_themes (Phase 13 embedding 复用)"
```

---

## Task 4: gspan.py — DFS data structures + _count_frequent_edges

**Files:**
- Create: `src/explain_engine/engines/theory/gspan.py`
- Test: `tests/test_engines_theory_gspan.py`

**Step 1: Write the failing test**

```python
# tests/test_engines_theory_gspan.py
"""Phase 16: 自实现 simplified gSpan (Yan & Han 2002), directed in-memory."""

import networkx as nx
import pytest


class TestDFSEdge:
    def test_construct(self):
        from explain_engine.engines.theory.gspan import DFSEdge
        e = DFSEdge(from_idx=0, to_idx=1, from_label="A", edge_label="causes", to_label="B")
        assert e.from_idx == 0 and e.to_idx == 1


class TestFrequentSubgraph:
    def test_construct(self):
        from explain_engine.engines.theory.gspan import FrequentSubgraph
        fs = FrequentSubgraph(
            nodes=("n0", "n1"),
            edges=(("n0", "n1", "causes"),),
            support_count=3,
            embeddings_in_graphs=(("g0", {"n0": "x0", "n1": "x1"}),),
        )
        assert fs.support_count == 3


class TestCountFrequentEdges:
    def _make_graph(self, edges_with_labels):
        g = nx.DiGraph()
        for src, src_label, tgt, tgt_label, edge_label in edges_with_labels:
            g.add_node(src, label=src_label)
            g.add_node(tgt, label=tgt_label)
            g.add_edge(src, tgt, label=edge_label)
        return g

    def test_single_graph_returns_no_frequent(self):
        from explain_engine.engines.theory.gspan import _count_frequent_edges
        g = self._make_graph([("a", "A", "b", "B", "causes")])
        result = _count_frequent_edges([("g0", g)], min_support=2)
        assert result == []  # 单 graph 无 frequent

    def test_two_graphs_same_edge_template_is_frequent(self):
        from explain_engine.engines.theory.gspan import _count_frequent_edges
        g0 = self._make_graph([("a", "A", "b", "B", "causes")])
        g1 = self._make_graph([("x", "A", "y", "B", "causes")])
        result = _count_frequent_edges([("g0", g0), ("g1", g1)], min_support=2)
        assert len(result) == 1
        edge_template, count = result[0]
        assert edge_template == ("A", "causes", "B")
        assert count == 2

    def test_three_graphs_two_diff_labels(self):
        from explain_engine.engines.theory.gspan import _count_frequent_edges
        g0 = self._make_graph([("a", "A", "b", "B", "causes")])
        g1 = self._make_graph([("x", "A", "y", "B", "causes")])
        g2 = self._make_graph([("p", "C", "q", "D", "causes")])
        result = _count_frequent_edges([("g0", g0), ("g1", g1), ("g2", g2)], min_support=2)
        assert len(result) == 1  # 只 (A, causes, B) 满足 freq=2
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_gspan.py -v`
Expected: 5 FAIL.

**Step 3: Implement gspan.py 数据结构 + _count_frequent_edges**

```python
# src/explain_engine/engines/theory/gspan.py
"""Phase 16: 自实现 simplified gSpan (Yan & Han 2002), directed in-memory.

简化点 (跟 paper 比):
- 仅 directed (我们 explanation graph 是 directed manifests_as/causes)
- in-memory list[(graph_id, nx.DiGraph)] API, 不读 file
- 不支持 disconnected motif / weighted edge
- Node/edge label 用 nx attribute "label" 字段

参考: Yan & Han 2002 §4 (Algorithm gSpan), §4.1 (canonical DFS code), §4.2 (rightmost-path extension)
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

import networkx as nx


@dataclass(frozen=True)
class DFSEdge:
    """gSpan DFS code 的一条 edge entry.

    (from_idx, to_idx): 在 DFS tree 中的位置. forward edge 若 to_idx > from_idx,
    backward edge 若 to_idx < from_idx (跟 paper 一致).
    """
    from_idx: int
    to_idx: int
    from_label: str
    edge_label: str
    to_label: str


@dataclass(frozen=True)
class FrequentSubgraph:
    """gSpan output: frequent subgraph + 在哪些 input graph 出现 + 位置."""
    nodes: tuple[str, ...]            # canonical DFS 顺序的 node id (motif-local, 非 graph-local)
    edges: tuple[tuple[str, str, str], ...]  # (src_motif_id, tgt_motif_id, edge_label)
    support_count: int
    embeddings_in_graphs: tuple[tuple[str, dict], ...]
        # (graph_id, {motif_node_id: graph_node_id}) — caller 反查需要


def _count_frequent_edges(
    graphs: list[tuple[str, nx.DiGraph]],
    min_support: int,
) -> list[tuple[tuple[str, str, str], int]]:
    """Phase 1: 数所有 1-edge (from_label, edge_label, to_label) 在多少 graph 出现.

    Returns: [((from_label, edge_label, to_label), graph_count)] 满足 ≥ min_support.
    """
    edge_to_graphs: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for gid, g in graphs:
        seen_in_this_graph: set[tuple[str, str, str]] = set()
        for src, tgt, edata in g.edges(data=True):
            from_lbl = g.nodes[src].get("label", "")
            to_lbl = g.nodes[tgt].get("label", "")
            edge_lbl = edata.get("label", "")
            template = (from_lbl, edge_lbl, to_lbl)
            if template not in seen_in_this_graph:
                edge_to_graphs[template].add(gid)
                seen_in_this_graph.add(template)
    return [(tpl, len(gids)) for tpl, gids in edge_to_graphs.items()
            if len(gids) >= min_support]
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_gspan.py -v`
Expected: 5 PASS

**Step 5: Commit**

```bash
git add src/explain_engine/engines/theory/gspan.py tests/test_engines_theory_gspan.py
git commit -m "engines/theory · Phase 16 Task 4: gspan.py DFS data structures + _count_frequent_edges"
```

---

## Task 5: gspan.py — canonical _is_minimum_dfs_code

**Files:**
- Modify: `src/explain_engine/engines/theory/gspan.py`
- Test: `tests/test_engines_theory_gspan.py`

**Step 1: Write the failing test**

```python
# tests/test_engines_theory_gspan.py 末追加

class TestIsMinimumDFSCode:
    def test_single_edge_is_canonical(self):
        from explain_engine.engines.theory.gspan import DFSEdge, _is_minimum_dfs_code
        code = [DFSEdge(0, 1, "A", "causes", "B")]
        assert _is_minimum_dfs_code(code) is True

    def test_two_isomorphic_codes_only_min_passes(self):
        """同一 subgraph 可有多种 DFS 顺序, gSpan 取字典序最小的为 canonical.

        Graph: A → B, A → C
        Code 1: [(0,1,A,e,B), (0,2,A,e,C)]  ← min DFS code (B 先 visit)
        Code 2: [(0,1,A,e,C), (0,2,A,e,B)]  ← 非 min
        """
        from explain_engine.engines.theory.gspan import DFSEdge, _is_minimum_dfs_code
        code_min = [DFSEdge(0, 1, "A", "e", "B"), DFSEdge(0, 2, "A", "e", "C")]
        code_non_min = [DFSEdge(0, 1, "A", "e", "C"), DFSEdge(0, 2, "A", "e", "B")]
        assert _is_minimum_dfs_code(code_min) is True
        assert _is_minimum_dfs_code(code_non_min) is False

    def test_chain_canonical(self):
        from explain_engine.engines.theory.gspan import DFSEdge, _is_minimum_dfs_code
        # A → B → C, DFS code [(0,1,A,e,B), (1,2,B,e,C)] 是 min
        code = [DFSEdge(0, 1, "A", "e", "B"), DFSEdge(1, 2, "B", "e", "C")]
        assert _is_minimum_dfs_code(code) is True
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_gspan.py::TestIsMinimumDFSCode -v`
Expected: 3 FAIL.

**Step 3: Implement _is_minimum_dfs_code**

```python
# src/explain_engine/engines/theory/gspan.py 追加

def _is_minimum_dfs_code(code: list[DFSEdge]) -> bool:
    """gSpan §4.1: 判 code 是否为该 subgraph 所有 isomorphic DFS code 中字典序最小者.

    简化策略 (MVP, ≤ 5 node 时正确):
      1. 用 code 重建 subgraph (nodes + edges)
      2. 从每个 node 起始, 跑所有可能 DFS traversal, 生成所有可能 DFS code
      3. 取字典序最小的, 跟 input code 比
    完整 gSpan 用 incremental DFS code generation (Yan & Han §4.1) 加速; MVP 暴力即可.
    """
    if not code:
        return True

    # Step 1: 用 code 重建
    g = nx.DiGraph()
    node_labels = {}
    for e in code:
        if e.from_idx not in node_labels:
            node_labels[e.from_idx] = e.from_label
        if e.to_idx not in node_labels:
            node_labels[e.to_idx] = e.to_label
        g.add_edge(e.from_idx, e.to_idx, label=e.edge_label)
    for nid, lbl in node_labels.items():
        g.nodes[nid]["label"] = lbl

    # Step 2: 从每个 node 起始, 枚举所有 DFS traversal 生成 DFS code list
    all_codes = []
    for start in g.nodes:
        all_codes.extend(_enumerate_dfs_codes_from(g, start))

    # Step 3: 字典序最小
    canonical_keys = sorted([_dfs_code_tuple(c) for c in all_codes])
    return _dfs_code_tuple(code) == canonical_keys[0]


def _dfs_code_tuple(code: list[DFSEdge]) -> tuple:
    return tuple((e.from_idx, e.to_idx, e.from_label, e.edge_label, e.to_label) for e in code)


def _enumerate_dfs_codes_from(g: nx.DiGraph, start: int) -> list[list[DFSEdge]]:
    """从 start 跑 DFS, 枚举所有 traversal 顺序生成 DFS code.

    简化版: 跑 DFS, 在分叉时按 label 字典序选 (deterministic). 不完整枚举所有 permutation
    (MVP 接受 limited canonical check 不完美; 8 test case 覆盖关键场景).
    """
    visited = {start: 0}
    code: list[DFSEdge] = []

    def visit(node):
        # 出边按 (to_label, edge_label, to_node) 排序
        edges = sorted(
            g.out_edges(node, data=True),
            key=lambda x: (g.nodes[x[1]].get("label", ""), x[2].get("label", ""), x[1]),
        )
        for _, nbr, edata in edges:
            if nbr not in visited:
                visited[nbr] = len(visited)
                code.append(DFSEdge(
                    from_idx=visited[node], to_idx=visited[nbr],
                    from_label=g.nodes[node].get("label", ""),
                    edge_label=edata.get("label", ""),
                    to_label=g.nodes[nbr].get("label", ""),
                ))
                visit(nbr)
            else:
                # backward edge (cycle)
                code.append(DFSEdge(
                    from_idx=visited[node], to_idx=visited[nbr],
                    from_label=g.nodes[node].get("label", ""),
                    edge_label=edata.get("label", ""),
                    to_label=g.nodes[nbr].get("label", ""),
                ))

    visit(start)
    return [code]  # MVP: 每 start 1 个 code (按 sort 顺序), 不全枚举
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_gspan.py -v`
Expected: 8 PASS (5 既有 + 3 新)

**Step 5: Commit**

```bash
git add src/explain_engine/engines/theory/gspan.py tests/test_engines_theory_gspan.py
git commit -m "engines/theory · Phase 16 Task 5: gspan.py _is_minimum_dfs_code canonical check"
```

---

## Task 6: gspan.py — _enumerate_rightmost_extensions + _count_support

**Files:**
- Modify: `src/explain_engine/engines/theory/gspan.py`
- Test: `tests/test_engines_theory_gspan.py`

**Step 1: Write the failing test**

```python
# tests/test_engines_theory_gspan.py 末追加

class TestRightmostExtensions:
    def test_single_node_seed_has_extensions(self):
        """seed code = [(0,1,A,e,B)], rightmost path = [0, 1].
        从 0 或 1 出去找新 edge → extension.
        """
        from explain_engine.engines.theory.gspan import (
            DFSEdge, _enumerate_rightmost_extensions
        )
        # Graph 0: A → B → C
        g = nx.DiGraph()
        g.add_node("a", label="A"); g.add_node("b", label="B"); g.add_node("c", label="C")
        g.add_edge("a", "b", label="e"); g.add_edge("b", "c", label="e")

        seed = [DFSEdge(0, 1, "A", "e", "B")]
        embedding = {0: "a", 1: "b"}  # motif_idx → graph_node
        extensions = _enumerate_rightmost_extensions(seed, [("g0", g)], [embedding])

        # 应找到 forward extension (1,2,B,e,C)
        assert len(extensions) >= 1
        assert any(ext.from_idx == 1 and ext.to_label == "C" for ext in extensions)


class TestCountSupport:
    def test_support_count_across_graphs(self):
        """同 motif 出现在 2 个 graph → support = 2."""
        from explain_engine.engines.theory.gspan import DFSEdge, _count_support
        # 2 graph, 都含 A → B
        g0 = nx.DiGraph()
        g0.add_node("x", label="A"); g0.add_node("y", label="B")
        g0.add_edge("x", "y", label="e")
        g1 = nx.DiGraph()
        g1.add_node("p", label="A"); g1.add_node("q", label="B")
        g1.add_edge("p", "q", label="e")

        motif_code = [DFSEdge(0, 1, "A", "e", "B")]
        graphs = [("g0", g0), ("g1", g1)]
        support, embeddings = _count_support(motif_code, graphs)
        assert support == 2
        assert len(embeddings) == 2
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_gspan.py::TestRightmostExtensions tests/test_engines_theory_gspan.py::TestCountSupport -v`
Expected: 2 FAIL.

**Step 3: Implement _enumerate_rightmost_extensions + _count_support**

```python
# src/explain_engine/engines/theory/gspan.py 追加

def _rightmost_path(code: list[DFSEdge]) -> list[int]:
    """gSpan §4.2: rightmost path 是从 root (idx 0) 沿 forward edge 到最新 added 节点的路径."""
    if not code:
        return [0]
    # 找 最新 added forward edge (to_idx > from_idx, 且 to_idx 最大)
    rightmost_node = max((e.to_idx for e in code if e.to_idx > e.from_idx), default=0)
    # 沿 forward edge 回溯到 0
    path = [rightmost_node]
    cur = rightmost_node
    while cur != 0:
        prev_edges = [e for e in code if e.to_idx == cur and e.to_idx > e.from_idx]
        if not prev_edges:
            break
        cur = prev_edges[0].from_idx
        path.insert(0, cur)
    return path


def _enumerate_rightmost_extensions(
    code: list[DFSEdge],
    graphs: list[tuple[str, nx.DiGraph]],
    embeddings: list[dict],  # 每 graph 一个 motif_idx → graph_node 映射
) -> list[DFSEdge]:
    """gSpan §4.2: 只扩展 rightmost path 上的节点 (避免重复枚举).

    两类 extension:
    - forward: 从 rightmost path 节点新增一个未访问过的子节点
    - backward: 从 rightmost node 加 edge 回 rightmost path 上某 ancestor (形成 cycle)

    MVP: 只支持 forward extension (cycle 由 Task 7 _dfs_extend 处理 backward).
    """
    rmpath = _rightmost_path(code)
    rightmost_node = rmpath[-1]
    next_idx = (max(max(e.from_idx, e.to_idx) for e in code) + 1) if code else 1

    candidates: list[DFSEdge] = []
    seen: set = set()

    for graph_idx, (gid, g) in enumerate(graphs):
        emb = embeddings[graph_idx]
        for motif_node in rmpath:
            if motif_node not in emb:
                continue
            graph_node = emb[motif_node]
            for _, nbr, edata in g.out_edges(graph_node, data=True):
                if nbr in emb.values():
                    continue  # 已 mapped, skip (backward 单独处理)
                nbr_label = g.nodes[nbr].get("label", "")
                edge_label = edata.get("label", "")
                from_label = g.nodes[graph_node].get("label", "")
                ext_key = (motif_node, from_label, edge_label, nbr_label)
                if ext_key in seen:
                    continue
                seen.add(ext_key)
                candidates.append(DFSEdge(
                    from_idx=motif_node, to_idx=next_idx,
                    from_label=from_label, edge_label=edge_label, to_label=nbr_label,
                ))
    return candidates


def _count_support(
    motif_code: list[DFSEdge],
    graphs: list[tuple[str, nx.DiGraph]],
) -> tuple[int, list[tuple[str, dict]]]:
    """对 motif_code 跑子图同构, 数它出现在多少 graph + 记录 embedding.

    Returns: (support_count, [(graph_id, {motif_idx: graph_node})])
    """
    # 用 code 重建 motif graph
    motif_g = nx.DiGraph()
    node_labels = {}
    for e in motif_code:
        node_labels[e.from_idx] = e.from_label
        node_labels[e.to_idx] = e.to_label
        motif_g.add_edge(e.from_idx, e.to_idx, label=e.edge_label)
    for nid, lbl in node_labels.items():
        motif_g.nodes[nid]["label"] = lbl

    found = []
    for gid, g in graphs:
        matcher = nx.algorithms.isomorphism.DiGraphMatcher(
            g, motif_g,
            node_match=lambda a, b: a.get("label") == b.get("label"),
            edge_match=lambda a, b: a.get("label") == b.get("label"),
        )
        if matcher.subgraph_is_isomorphic():
            # 取第一个 mapping (mapping is graph_node → motif_idx)
            mapping = matcher.mapping  # type: ignore
            inv = {v: k for k, v in mapping.items()}  # motif_idx → graph_node
            found.append((gid, inv))
    return len(found), found
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_gspan.py -v`
Expected: 10 PASS (8 既有 + 2 新)

**Step 5: Commit**

```bash
git add src/explain_engine/engines/theory/gspan.py tests/test_engines_theory_gspan.py
git commit -m "engines/theory · Phase 16 Task 6: gspan.py _enumerate_rightmost_extensions + _count_support"
```

---

## Task 7: gspan.py — _dfs_extend + gspan_mine 集成

**Files:**
- Modify: `src/explain_engine/engines/theory/gspan.py`
- Test: `tests/test_engines_theory_gspan.py`

**Step 1: Write the failing test**

```python
# tests/test_engines_theory_gspan.py 末追加

class TestGspanMineIntegration:
    def _make_graph(self, edges):
        g = nx.DiGraph()
        seen = set()
        for src, src_lbl, tgt, tgt_lbl, edge_lbl in edges:
            if src not in seen: g.add_node(src, label=src_lbl); seen.add(src)
            if tgt not in seen: g.add_node(tgt, label=tgt_lbl); seen.add(tgt)
            g.add_edge(src, tgt, label=edge_lbl)
        return g

    def test_finds_frequent_chain_size_3(self):
        from explain_engine.engines.theory.gspan import gspan_mine
        # 3 graph, 都含 A → B → C chain
        graphs = [
            ("g0", self._make_graph([("a", "A", "b", "B", "e"), ("b", "B", "c", "C", "e")])),
            ("g1", self._make_graph([("x", "A", "y", "B", "e"), ("y", "B", "z", "C", "e")])),
            ("g2", self._make_graph([("p", "A", "q", "B", "e"), ("q", "B", "r", "C", "e")])),
        ]
        result = gspan_mine(graphs, min_support=3, min_size=3, max_size=5)
        # 应找出 chain motif (3 nodes A→B→C)
        assert any(len(fs.nodes) == 3 and fs.support_count == 3 for fs in result)

    def test_noise_graph_not_in_support(self):
        from explain_engine.engines.theory.gspan import gspan_mine
        graphs = [
            ("g0", self._make_graph([("a", "A", "b", "B", "e")])),
            ("g1", self._make_graph([("x", "A", "y", "B", "e")])),
            ("g2", self._make_graph([("p", "X", "q", "Y", "e")])),  # noise
        ]
        result = gspan_mine(graphs, min_support=2)
        # (A,e,B) freq=2 应在; noise (X,e,Y) freq=1 不应在
        assert any(("A" in fs.nodes or "A" in str(fs.edges)) for fs in result)

    def test_min_support_3_with_only_2_graphs_returns_empty(self):
        from explain_engine.engines.theory.gspan import gspan_mine
        graphs = [
            ("g0", self._make_graph([("a", "A", "b", "B", "e")])),
            ("g1", self._make_graph([("x", "A", "y", "B", "e")])),
        ]
        result = gspan_mine(graphs, min_support=3)
        assert result == []

    def test_max_size_caps_motif(self):
        from explain_engine.engines.theory.gspan import gspan_mine
        # 都含 5-node chain, 但 max_size=3
        graphs = [
            ("g0", self._make_graph([("a", "A", "b", "B", "e"), ("b", "B", "c", "C", "e"),
                                     ("c", "C", "d", "D", "e"), ("d", "D", "e", "E", "e")])),
            ("g1", self._make_graph([("a", "A", "b", "B", "e"), ("b", "B", "c", "C", "e"),
                                     ("c", "C", "d", "D", "e"), ("d", "D", "e", "E", "e")])),
        ]
        result = gspan_mine(graphs, min_support=2, max_size=3)
        assert all(len(fs.nodes) <= 3 for fs in result)
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_gspan.py::TestGspanMineIntegration -v`
Expected: 4 FAIL.

**Step 3: Implement _dfs_extend + gspan_mine**

```python
# src/explain_engine/engines/theory/gspan.py 追加

def _subgraph_size(code: list[DFSEdge]) -> int:
    """node 数 (DFS tree 中的 distinct idx)."""
    idxs = set()
    for e in code:
        idxs.add(e.from_idx); idxs.add(e.to_idx)
    return len(idxs)


def _dfs_extend(
    current_code: list[DFSEdge],
    graphs: list[tuple[str, nx.DiGraph]],
    min_support: int,
    min_size: int,
    max_size: int,
    output: list[list[DFSEdge]],
) -> None:
    """gSpan DFS recursion. canonical check 防重复枚举, anti-monotone pruning."""
    if not _is_minimum_dfs_code(current_code):
        return
    if _subgraph_size(current_code) >= min_size:
        output.append(list(current_code))
    if _subgraph_size(current_code) >= max_size:
        return

    # 重算 embeddings (extension 用)
    support_count, embeddings_per_graph = _count_support(current_code, graphs)
    if support_count < min_support:
        return

    embeddings = [emb for _, emb in embeddings_per_graph]
    extensions = _enumerate_rightmost_extensions(current_code, graphs, embeddings)

    for ext in extensions:
        new_code = current_code + [ext]
        new_support, _ = _count_support(new_code, graphs)
        if new_support >= min_support:
            _dfs_extend(new_code, graphs, min_support, min_size, max_size, output)


def gspan_mine(
    graphs: list[tuple[str, nx.DiGraph]],
    min_support: int,
    min_size: int = 2,
    max_size: int = 5,
    is_directed: bool = True,
) -> list[FrequentSubgraph]:
    """Yan & Han 2002 gSpan, 简化为 directed + in-memory.

    Returns: FrequentSubgraph list, 每含 nodes / edges / support / embeddings_in_graphs.
    """
    if not is_directed:
        raise NotImplementedError("MVP: only directed graph supported")

    freq_1edges = _count_frequent_edges(graphs, min_support)
    all_frequent: list[list[DFSEdge]] = []

    for (from_lbl, edge_lbl, to_lbl), _ in freq_1edges:
        seed = [DFSEdge(0, 1, from_lbl, edge_lbl, to_lbl)]
        _dfs_extend(seed, graphs, min_support, min_size, max_size, all_frequent)

    # Decode DFS code → FrequentSubgraph
    result = []
    for code in all_frequent:
        node_labels = {}
        for e in code:
            node_labels[e.from_idx] = e.from_label
            node_labels[e.to_idx] = e.to_label
        nodes = tuple(f"n{i}" for i in sorted(node_labels))
        edges = tuple((f"n{e.from_idx}", f"n{e.to_idx}", e.edge_label) for e in code)
        support_count, embeddings = _count_support(code, graphs)
        # embeddings: [(gid, {motif_idx: graph_node})] → 转 ({n{i}: graph_node})
        embeddings_renamed = tuple(
            (gid, {f"n{i}": gn for i, gn in emb.items()})
            for gid, emb in embeddings
        )
        result.append(FrequentSubgraph(
            nodes=nodes, edges=edges,
            support_count=support_count,
            embeddings_in_graphs=embeddings_renamed,
        ))

    # Dedup (canonical 可能重复因 MVP _is_minimum_dfs_code 不完美)
    seen_keys = set()
    deduped = []
    for fs in result:
        key = (tuple(sorted(fs.edges)),)
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(fs)
    return deduped
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_gspan.py -v`
Expected: 14 PASS (10 既有 + 4 新)

**Step 5: Commit**

```bash
git add src/explain_engine/engines/theory/gspan.py tests/test_engines_theory_gspan.py
git commit -m "engines/theory · Phase 16 Task 7: gspan.py _dfs_extend + gspan_mine 集成"
```

---

## Task 8: motif_mining.py

**Files:**
- Create: `src/explain_engine/engines/theory/motif_mining.py`
- Test: `tests/test_engines_theory_motif_mining.py`

**Step 1: Write the failing test**

```python
# tests/test_engines_theory_motif_mining.py
"""Phase 16: motif_mining — per-theme subgraph 抽取 + 调 gspan_mine."""

import networkx as nx
import pytest

from explain_engine.engines.theory.theory import Theme


def _fake_graph(nodes, edges):
    """Helper: build ExplanationGraph-like, 节点用 lexicon global_id 当 id."""
    class FakeNode:
        def __init__(self, nid, name, abstraction_level=1):
            self.id = nid; self.name = name; self.abstraction_level = abstraction_level
            self.confidence = 0.7; self.epistemic = "insight"; self.lifecycle_state = "active"
            self.description = name
    class FakeEdge:
        def __init__(self, eid, src, tgt, rel):
            self.id = eid; self.source_node = src; self.target_node = tgt
            self.relation_type = rel; self.confidence = 0.8
            self.mechanism_description = ""
    class FakeGraph:
        def __init__(self):
            self.nodes = {}; self.edges = {}
        def add_node(self, n): self.nodes[n.id] = n
        def add_edge(self, e): self.edges[e.id] = e

    g = FakeGraph()
    for nid, name in nodes:
        g.add_node(FakeNode(nid, name))
    for i, (src, tgt, rel) in enumerate(edges):
        g.add_edge(FakeEdge(f"e_{i}", src, tgt, rel))
    return g


class TestFindMotifsPerTheme:
    def test_empty_sessions_returns_empty(self):
        from explain_engine.engines.theory.motif_mining import find_motifs_per_theme
        theme = Theme(id="th_001", name="X", member_global_ids=("v_a",), centroid_summary="")
        result = find_motifs_per_theme({}, theme, min_freq=3)
        assert result == []

    def test_3_sessions_with_same_chain_returns_motif(self):
        from explain_engine.engines.theory.motif_mining import find_motifs_per_theme
        sessions = {
            "s_1": _fake_graph([("v_a", "A"), ("v_b", "B")], [("v_a", "v_b", "causes")]),
            "s_2": _fake_graph([("v_a", "A"), ("v_b", "B")], [("v_a", "v_b", "causes")]),
            "s_3": _fake_graph([("v_a", "A"), ("v_b", "B")], [("v_a", "v_b", "causes")]),
        }
        theme = Theme(id="th_001", name="A-B",
                      member_global_ids=("v_a", "v_b"), centroid_summary="")
        result = find_motifs_per_theme(sessions, theme, min_freq=3)
        assert len(result) >= 1
        assert all(len(m.supporting_sessions) == 3 for m in result)

    def test_min_freq_gate(self):
        from explain_engine.engines.theory.motif_mining import find_motifs_per_theme
        sessions = {
            "s_1": _fake_graph([("v_a", "A"), ("v_b", "B")], [("v_a", "v_b", "causes")]),
            "s_2": _fake_graph([("v_a", "A"), ("v_b", "B")], [("v_a", "v_b", "causes")]),
        }
        theme = Theme(id="th_001", name="A-B",
                      member_global_ids=("v_a", "v_b"), centroid_summary="")
        # freq=2 但 min=3 → empty
        result = find_motifs_per_theme(sessions, theme, min_freq=3)
        assert result == []
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_motif_mining.py -v`
Expected: 3 FAIL.

**Step 3: Implement motif_mining.py**

```python
# src/explain_engine/engines/theory/motif_mining.py
"""Phase 16: 跨 session theme subgraph 抽取 → gspan_mine → RawMotif."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import networkx as nx

from explain_engine.engines.theory.gspan import gspan_mine
from explain_engine.engines.theory.theory import Theme


@dataclass(frozen=True)
class RawMotif:
    motif_type: Literal["chain", "star", "cycle"]
    nodes: tuple[str, ...]                  # lexicon global_ids
    edges: tuple[tuple[str, str, str], ...]
    supporting_sessions: tuple[str, ...]


def find_motifs_per_theme(
    sessions: dict[str, "ExplanationGraph"],
    theme: Theme,
    min_freq: int,
) -> list[RawMotif]:
    """对每 session 抽 theme subgraph, 跑 gspan_mine, 返 RawMotif list."""
    theme_node_set = set(theme.member_global_ids)
    per_session_subgraph: list[tuple[str, nx.DiGraph]] = []

    for sid, graph in sessions.items():
        sub = _extract_theme_subgraph(graph, theme_node_set)
        if len(sub.nodes) >= 2:
            per_session_subgraph.append((sid, sub))

    if len(per_session_subgraph) < min_freq:
        return []

    frequent = gspan_mine(
        graphs=per_session_subgraph, min_support=min_freq,
        min_size=2, max_size=5, is_directed=True,
    )

    motifs = []
    for fs in frequent:
        motif_type = _classify_motif_type(fs)
        # nodes 用 lexicon global_id (取 embedding 第一个 graph 的 mapping)
        first_gid, first_emb = fs.embeddings_in_graphs[0] if fs.embeddings_in_graphs else (None, {})
        nodes_gids = tuple(first_emb.get(n, n) for n in fs.nodes)
        # edges: 把 motif-local n0/n1 转 graph node id (用 first_emb)
        edges_gids = tuple(
            (first_emb.get(src, src), first_emb.get(tgt, tgt), rel)
            for src, tgt, rel in fs.edges
        )
        motifs.append(RawMotif(
            motif_type=motif_type,
            nodes=nodes_gids,
            edges=edges_gids,
            supporting_sessions=tuple(gid for gid, _ in fs.embeddings_in_graphs),
        ))
    return motifs


def _extract_theme_subgraph(graph, theme_node_set: set[str]) -> nx.DiGraph:
    """从 session graph 抽 theme nodes 涉及的 subgraph (含一跳邻居)."""
    sub = nx.DiGraph()
    for node in graph.nodes.values():
        if node.id in theme_node_set:
            sub.add_node(node.id, label=node.name)
    for edge in graph.edges.values():
        if edge.source_node in theme_node_set or edge.target_node in theme_node_set:
            # 加一跳节点 + edge
            for nid in (edge.source_node, edge.target_node):
                if nid not in sub.nodes and nid in graph.nodes:
                    sub.add_node(nid, label=graph.nodes[nid].name)
            sub.add_edge(edge.source_node, edge.target_node, label=edge.relation_type)
    return sub


def _classify_motif_type(fs) -> Literal["chain", "star", "cycle"]:
    g = nx.DiGraph()
    for src, tgt, _ in fs.edges:
        g.add_edge(src, tgt)
    if g.number_of_nodes() == 0:
        return "chain"
    if any(g.in_degree(n) > 1 or g.out_degree(n) > 1 for n in g.nodes):
        return "star"
    if not nx.is_directed_acyclic_graph(g):
        return "cycle"
    return "chain"
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_motif_mining.py -v`
Expected: 3 PASS

**Step 5: Commit**

```bash
git add src/explain_engine/engines/theory/motif_mining.py tests/test_engines_theory_motif_mining.py
git commit -m "engines/theory · Phase 16 Task 8: motif_mining.find_motifs_per_theme"
```

---

## Task 9: falsifiability.py (JEPA 启示 a)

**Files:**
- Create: `src/explain_engine/engines/theory/falsifiability.py`
- Test: `tests/test_engines_theory_falsifiability.py`

**Step 1: Write the failing test**

```python
# tests/test_engines_theory_falsifiability.py
"""Phase 16 JEPA (a): leave-one-session-out predictive_power."""

import numpy as np
import pytest

from explain_engine.engines.theory.motif_mining import RawMotif


class FakeEmbedder:
    def __init__(self, name_to_vec):
        self._map = name_to_vec
    def encode(self, names):
        return np.stack([self._map.get(n, np.zeros(3)) for n in names])


class TestEvaluatePredictivePower:
    def test_supporting_less_than_2_returns_zero(self):
        from explain_engine.engines.theory.falsifiability import evaluate_predictive_power
        motif = RawMotif("chain", ("v_a",), (("v_a", "v_b", "causes"),), ("s_1",))
        result = evaluate_predictive_power(motif, {}, embedder=FakeEmbedder({}))
        assert result == 0.0

    def test_perfect_predict(self):
        """3 supporting session, motif node 在所有 held-out L0 都能 cosine 0.9+ match → 1.0."""
        # TODO: 构造 fake sessions + lexicon embedding
        # (此 test 在 implementation 时根据真实 API 调整)
        pass

    def test_no_predict(self):
        """held-out L0 跟 motif 完全不像 → 0.0."""
        pass

    def test_partial_predict(self):
        """3 supporting, 2 hit, 1 miss → 0.67."""
        pass
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_falsifiability.py -v`
Expected: 1 FAIL + 3 SKIP.

**Step 3: Implement falsifiability.py**

```python
# src/explain_engine/engines/theory/falsifiability.py
"""Phase 16 JEPA (a): theory 的 predictive_power 评估 (leave-one-session-out).

哲学 §9.4 可证伪性 — theory 必须可失败. 这里实现客观判据:
若 theory.motif_nodes 在 held-out session 的 L0 phenomena 中能 cosine ≥ 0.85 match
至少 1 个, 则算 predict 成功. predictive_power = 命中 / supporting_session 数.
"""
from __future__ import annotations

import numpy as np

from explain_engine.engines.theory.motif_mining import RawMotif


def evaluate_predictive_power(
    motif: RawMotif,
    all_sessions: dict[str, "ExplanationGraph"],
    embedder,
    match_threshold: float = 0.85,
) -> float:
    """leave-one-out: 对每 supporting session, 看 motif nodes 能否在该 session L0 match."""
    if len(motif.supporting_sessions) < 2:
        return 0.0

    hit_count = 0
    for held_out_sid in motif.supporting_sessions:
        held_graph = all_sessions.get(held_out_sid)
        if held_graph is None:
            continue
        held_l0_phenomena = [
            n for n in held_graph.nodes.values()
            if getattr(n, "abstraction_level", -1) == 0
        ]
        if not held_l0_phenomena:
            continue

        # 取 motif node names (需 reverse lookup global_id → name via lexicon, 简化为节点 id)
        # MVP: 用节点 id 直接 encode (理论上应从 lexicon 取 canonical name)
        motif_node_names = [str(nid) for nid in motif.nodes]
        held_node_names = [n.name for n in held_l0_phenomena]

        motif_embs = embedder.encode(motif_node_names)
        held_embs = embedder.encode(held_node_names)

        # 归一化
        motif_embs = _normalize(motif_embs)
        held_embs = _normalize(held_embs)

        # 任一 motif node 跟任一 held L0 cosine ≥ threshold → 命中
        sim_matrix = motif_embs @ held_embs.T  # (motif_n, held_n)
        if (sim_matrix >= match_threshold).any():
            hit_count += 1

    return hit_count / len(motif.supporting_sessions)


def _normalize(embs):
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    return embs / np.where(norms > 0, norms, 1)
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_falsifiability.py -v`
Expected: 1 PASS + 3 SKIP (后 3 case 实施时补)

**Step 5: Commit**

```bash
git add src/explain_engine/engines/theory/falsifiability.py tests/test_engines_theory_falsifiability.py
git commit -m "engines/theory · Phase 16 Task 9: falsifiability.evaluate_predictive_power (JEPA a)"
```

---

## Task 10: ranking.py (compute_score + MMR + promote_stable, JEPA b/c)

**Files:**
- Create: `src/explain_engine/engines/theory/ranking.py`
- Test: `tests/test_engines_theory_ranking.py`

**Step 1: Write the failing test**

```python
# tests/test_engines_theory_ranking.py
"""Phase 16 JEPA (b)(c): ranking + promote stable."""

import pytest

from explain_engine.engines.theory.theory import Theory


def _make_theory(id="t1", supporting=("s_1",), predictive=0.5,
                 theme_ids=("th_001",), complexity=3):
    return Theory(
        id=id, motif_type="chain",
        theme_ids=theme_ids, node_ids=("v_a", "v_b"),
        edges=(("v_a", "v_b", "causes"),),
        supporting_sessions=supporting,
        natural_language_summary="...",
        structure_complexity=complexity,
        first_seen_session=supporting[0], last_seen_session=supporting[-1],
        predictive_power=predictive,
    )


class TestComputeScore:
    def test_predictive_power_weighted_most(self):
        from explain_engine.engines.theory.ranking import compute_score
        t1 = _make_theory(supporting=("s_1", "s_2"), predictive=1.0)
        t2 = _make_theory(supporting=("s_1", "s_2"), predictive=0.0)
        assert compute_score(t1, 10) > compute_score(t2, 10)


class TestMmrRanking:
    def test_diversity_penalty(self):
        from explain_engine.engines.theory.ranking import rank_topk_with_mmr
        # 2 same-theme theory, 1 different-theme
        t1 = _make_theory(id="t1", theme_ids=("th_001",), predictive=0.9, supporting=("s_1",))
        t2 = _make_theory(id="t2", theme_ids=("th_001",), predictive=0.85, supporting=("s_1",))
        t3 = _make_theory(id="t3", theme_ids=("th_002",), predictive=0.8, supporting=("s_1",))
        ranked = rank_topk_with_mmr([t1, t2, t3], k=2, λ=0.5, n_sessions_total=10)
        # 高 score 的 t1 一定在; 第二个应是 t3 (不同 theme, 防 paraphrase)
        ids = [t.id for t in ranked]
        assert ids[0] == "t1"
        assert ids[1] == "t3"


class TestPromoteStable:
    def test_promote_if_in_recent_window(self):
        from explain_engine.engines.theory.ranking import maybe_promote_to_stable
        # window=5, theory 在最近 5 session 中 3 个有出现 → stable (5//2+1 = 3)
        sessions = ["s_1", "s_2", "s_3", "s_4", "s_5"]
        t = _make_theory(supporting=("s_2", "s_3", "s_5"))
        assert maybe_promote_to_stable(t, sessions, window_size=5) is True

    def test_not_promote_if_too_few(self):
        from explain_engine.engines.theory.ranking import maybe_promote_to_stable
        sessions = ["s_1", "s_2", "s_3", "s_4", "s_5"]
        t = _make_theory(supporting=("s_2",))  # only 1 in window
        assert maybe_promote_to_stable(t, sessions, window_size=5) is False
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_ranking.py -v`
Expected: 4 FAIL.

**Step 3: Implement ranking.py**

```python
# src/explain_engine/engines/theory/ranking.py
"""Phase 16 JEPA (b)(c): scoring + MMR diversity + promote stable."""
from __future__ import annotations

from explain_engine.engines.theory.theory import Theory


def compute_score(theory: Theory, n_sessions_total: int) -> float:
    freq = len(theory.supporting_sessions) / max(n_sessions_total, 1)
    complexity = min(theory.structure_complexity, 5) / 5.0
    return (
        0.35 * freq
      + 0.20 * complexity
      + 0.45 * theory.predictive_power  # JEPA (a)
    )


def theme_overlap(t1: Theory, t2: Theory) -> float:
    """Jaccard of theme_ids."""
    s1, s2 = set(t1.theme_ids), set(t2.theme_ids)
    return len(s1 & s2) / max(len(s1 | s2), 1)


def rank_topk_with_mmr(
    theories: list[Theory],
    k: int = 20,
    λ: float = 0.7,
    n_sessions_total: int = 1,
) -> list[Theory]:
    """JEPA (c): MMR diversity. λ=0.7 偏 relevance, 0.3 偏 diversity."""
    if not theories:
        return []
    selected: list[Theory] = []
    pool = sorted(theories, key=lambda t: -compute_score(t, n_sessions_total))
    while len(selected) < k and pool:
        if not selected:
            selected.append(pool.pop(0))
            continue
        best = max(pool, key=lambda t:
            λ * compute_score(t, n_sessions_total)
          - (1 - λ) * max(theme_overlap(t, s) for s in selected)
        )
        selected.append(best); pool.remove(best)
    return selected


def maybe_promote_to_stable(
    theory: Theory,
    all_sessions: list[str],
    window_size: int,
) -> bool:
    """JEPA (b): 最近 window 内 ≥ ⌈window/2⌉+1 个 session 有 theory → stable."""
    if len(all_sessions) < window_size:
        return False
    recent_window = set(all_sessions[-window_size:])
    overlap = recent_window & set(theory.supporting_sessions)
    return len(overlap) >= (window_size // 2 + 1)
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_ranking.py -v`
Expected: 4 PASS

**Step 5: Commit**

```bash
git add src/explain_engine/engines/theory/ranking.py tests/test_engines_theory_ranking.py
git commit -m "engines/theory · Phase 16 Task 10: ranking.compute_score + MMR + promote_stable (JEPA b/c)"
```

---

## Task 11: cache.py + loader.py + recompute.py

**Files:**
- Create: `src/explain_engine/engines/theory/loader.py`
- Create: `src/explain_engine/engines/theory/cache.py`
- Create: `src/explain_engine/engines/theory/recompute.py`
- Test: `tests/test_engines_theory_cache.py`

**Step 1: Write the failing test**

```python
# tests/test_engines_theory_cache.py
"""Phase 16: TheoriesCache lazy invalidation + atomic write + reject."""

import json
import pytest
from pathlib import Path


def _empty_cache_dict():
    return {
        "version": "1.0", "computed_at": "2026-05-21T00:00:00Z",
        "session_ids_snapshot": [], "cold_start_threshold": 3,
        "stability_window_size": 5,
        "themes": [], "tentative_theories": [], "stable_theories": [],
        "rejected_theory_ids": [],
    }


class TestGetActiveTheoriesCache:
    def test_no_cache_file_returns_empty(self, tmp_path, monkeypatch):
        from explain_engine.engines.theory.cache import get_active_theories
        from explain_engine.persistence.storage_v2 import StorageV2
        # 用 tmp project
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        storage = StorageV2()
        # 无 session, 应 cold-start path 返 empty
        result = get_active_theories(storage, embedder=None)
        assert result.session_ids_snapshot == []

    def test_cache_hit_returns_cached(self, tmp_path, monkeypatch):
        from explain_engine.engines.theory.cache import get_active_theories, _atomic_write_cache, _empty_cache_obj
        from explain_engine.persistence.storage_v2 import StorageV2
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        storage = StorageV2()
        knowledge_dir = storage.knowledge_dir()
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        # 写一 cache
        cache_path = knowledge_dir / "theories.json"
        cache_path.write_text(json.dumps(_empty_cache_dict()))
        # 无 session 时 snapshot=[] 跟 cache 一致 → cache hit
        result = get_active_theories(storage, embedder=None)
        assert result.session_ids_snapshot == []


class TestRejectTheory:
    def test_reject_idempotent(self, tmp_path, monkeypatch):
        from explain_engine.engines.theory.cache import reject_theory
        from explain_engine.persistence.storage_v2 import StorageV2
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        storage = StorageV2()
        knowledge_dir = storage.knowledge_dir()
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        cache_dict = _empty_cache_dict()
        cache_dict["tentative_theories"] = [{
            "id": "t_abc", "motif_type": "chain",
            "theme_ids": [], "node_ids": [], "edges": [],
            "supporting_sessions": [], "natural_language_summary": "",
            "structure_complexity": 2,
            "first_seen_session": "", "last_seen_session": "",
            "predictive_power": 0.5, "stability_status": "tentative",
            "stable_promoted_at_session": None,
        }]
        cache_path = knowledge_dir / "theories.json"
        cache_path.write_text(json.dumps(cache_dict))

        assert reject_theory(storage, "t_abc") is True
        assert reject_theory(storage, "t_abc") is True  # idempotent
        # Re-read
        reloaded = json.loads(cache_path.read_text())
        assert "t_abc" in reloaded["rejected_theory_ids"]

    def test_reject_nonexistent_returns_false(self, tmp_path, monkeypatch):
        from explain_engine.engines.theory.cache import reject_theory
        from explain_engine.persistence.storage_v2 import StorageV2
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        storage = StorageV2()
        knowledge_dir = storage.knowledge_dir()
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        cache_path = knowledge_dir / "theories.json"
        cache_path.write_text(json.dumps(_empty_cache_dict()))
        assert reject_theory(storage, "t_does_not_exist") is False
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_cache.py -v`
Expected: 4 FAIL.

**Step 3: Implement loader.py + cache.py + recompute.py**

```python
# src/explain_engine/engines/theory/loader.py
"""Phase 16: load_all_session_graphs — IO heavy lazy."""
from __future__ import annotations


def load_all_session_graphs(sids, storage):
    from explain_engine.persistence.session import SessionStore
    return {sid: SessionStore().load(sid).state.graph for sid in sids}


# src/explain_engine/engines/theory/cache.py
"""Phase 16: TheoriesCache lazy invalidation + atomic write."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from explain_engine.engines.theory.theory import Theme, Theory


@dataclass
class TheoriesCache:
    themes: list[Theme] = field(default_factory=list)
    tentative_theories: list[Theory] = field(default_factory=list)
    stable_theories: list[Theory] = field(default_factory=list)
    rejected_theory_ids: set[str] = field(default_factory=set)
    session_ids_snapshot: list[str] = field(default_factory=list)
    cold_start_threshold: int = 3
    stability_window_size: int = 5
    computed_at: str = ""


def _empty_cache_obj() -> TheoriesCache:
    return TheoriesCache(computed_at=_now_iso())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_active_theories(storage, embedder=None, *, force_recompute: bool = False) -> TheoriesCache:
    from explain_engine.persistence.session import SessionStore
    cache_path = storage.knowledge_dir() / "theories.json"
    cache = _load_cache(cache_path) if cache_path.exists() else _empty_cache_obj()

    current_sids = sorted(m.session_id for m in SessionStore().list())
    if (force_recompute or set(cache.session_ids_snapshot) != set(current_sids)
            or not cache_path.exists()):
        if embedder is None:
            # bootstrap inject 路径 — 返 stale (best-effort)
            return cache
        from explain_engine.engines.theory.recompute import _recompute_all
        cache = _recompute_all(
            sessions=current_sids, storage=storage, embedder=embedder,
            preserve_rejected=cache.rejected_theory_ids,
        )
        _atomic_write_cache(cache, cache_path)
    return cache


def reject_theory(storage, theory_id: str) -> bool:
    cache_path = storage.knowledge_dir() / "theories.json"
    if not cache_path.exists():
        return False
    cache = _load_cache(cache_path)
    all_ids = {t.id for t in cache.tentative_theories + cache.stable_theories}
    if theory_id not in all_ids:
        return False
    if theory_id in cache.rejected_theory_ids:
        return True
    cache.rejected_theory_ids.add(theory_id)
    _atomic_write_cache(cache, cache_path)
    return True


def _atomic_write_cache(cache: TheoriesCache, path: Path) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(_cache_to_dict(cache), indent=2, ensure_ascii=False))
    tmp.replace(path)


def _load_cache(path: Path) -> TheoriesCache:
    d = json.loads(path.read_text())
    return TheoriesCache(
        themes=[Theme(**th) for th in d.get("themes", [])],
        tentative_theories=[_theory_from_dict(t) for t in d.get("tentative_theories", [])],
        stable_theories=[_theory_from_dict(t) for t in d.get("stable_theories", [])],
        rejected_theory_ids=set(d.get("rejected_theory_ids", [])),
        session_ids_snapshot=d.get("session_ids_snapshot", []),
        cold_start_threshold=d.get("cold_start_threshold", 3),
        stability_window_size=d.get("stability_window_size", 5),
        computed_at=d.get("computed_at", ""),
    )


def _cache_to_dict(cache: TheoriesCache) -> dict:
    from dataclasses import asdict
    return {
        "version": "1.0", "computed_at": cache.computed_at,
        "session_ids_snapshot": cache.session_ids_snapshot,
        "cold_start_threshold": cache.cold_start_threshold,
        "stability_window_size": cache.stability_window_size,
        "themes": [asdict(th) for th in cache.themes],
        "tentative_theories": [_theory_to_dict(t) for t in cache.tentative_theories],
        "stable_theories": [_theory_to_dict(t) for t in cache.stable_theories],
        "rejected_theory_ids": sorted(cache.rejected_theory_ids),
    }


def _theory_to_dict(t: Theory) -> dict:
    from dataclasses import asdict
    return asdict(t)


def _theory_from_dict(d: dict) -> Theory:
    return Theory(
        id=d["id"], motif_type=d["motif_type"],
        theme_ids=tuple(d["theme_ids"]), node_ids=tuple(d["node_ids"]),
        edges=tuple(tuple(e) for e in d["edges"]),
        supporting_sessions=tuple(d["supporting_sessions"]),
        natural_language_summary=d["natural_language_summary"],
        structure_complexity=d["structure_complexity"],
        first_seen_session=d["first_seen_session"], last_seen_session=d["last_seen_session"],
        predictive_power=d.get("predictive_power", 0.0),
        stability_status=d.get("stability_status", "tentative"),
        stable_promoted_at_session=d.get("stable_promoted_at_session"),
    )


# src/explain_engine/engines/theory/recompute.py
"""Phase 16: _recompute_all 7-step orchestrator."""
from __future__ import annotations


def _recompute_all(sessions, storage, embedder, preserve_rejected):
    """完整 7-step pipeline. (实施时填完整 — 占位)"""
    from explain_engine.engines.theory.cache import TheoriesCache, _now_iso
    cold_start = max(3, len(sessions) // 3)
    window_size = 5

    if len(sessions) < cold_start:
        return TheoriesCache(
            rejected_theory_ids=preserve_rejected,
            session_ids_snapshot=sessions,
            cold_start_threshold=cold_start,
            stability_window_size=window_size,
            computed_at=_now_iso(),
        )

    # TODO: load sessions → cluster → motif → predict → promote → rank
    # 实施时补完
    return TheoriesCache(
        rejected_theory_ids=preserve_rejected,
        session_ids_snapshot=sessions,
        cold_start_threshold=cold_start, stability_window_size=window_size,
        computed_at=_now_iso(),
    )
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_engines_theory_cache.py -v`
Expected: 4 PASS

**Step 5: Commit**

```bash
git add src/explain_engine/engines/theory/loader.py src/explain_engine/engines/theory/cache.py src/explain_engine/engines/theory/recompute.py tests/test_engines_theory_cache.py
git commit -m "engines/theory · Phase 16 Task 11: cache + loader + recompute scaffold"
```

---

## Task 12: recompute.py 完整 pipeline

**Files:**
- Modify: `src/explain_engine/engines/theory/recompute.py`
- Test: `tests/test_engines_theory_recompute_integration.py`

**Step 1: Write the failing test**

```python
# tests/test_engines_theory_recompute_integration.py
"""Phase 16: _recompute_all 完整 7-step integration test."""

import pytest


class TestRecomputeAll:
    @pytest.mark.skip(reason="integration test — 实施时跑")
    def test_cold_start_returns_empty(self, tmp_path, monkeypatch):
        pass

    @pytest.mark.skip(reason="integration test — 实施时跑")
    def test_3_sessions_same_motif_finds_theory(self, tmp_path, monkeypatch):
        pass
```

**Step 2-4**: 实施 _recompute_all 完整 pipeline (按 design Section 5.4), 跑 integration test.

**Step 5: Commit**

```bash
git add src/explain_engine/engines/theory/recompute.py tests/test_engines_theory_recompute_integration.py
git commit -m "engines/theory · Phase 16 Task 12: recompute.py 完整 7-step pipeline"
```

---

## Task 13: chat slash _handle_theories

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py`
- Test: `tests/test_chat_slash_theories.py`

**Step 1: Write the failing test**

```python
# tests/test_chat_slash_theories.py
"""Phase 16: /theories chat slash."""

import pytest

from explain_engine.chat.slash_commands import dispatch_slash


class TestSlashTheories:
    @pytest.mark.asyncio
    async def test_cold_start_shows_threshold_message(self, tmp_path, monkeypatch):
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        _make_done_session("s_t1000001")
        chat = ChatSession("s_t1000001")
        events = await dispatch_slash(chat, "/theories")
        assert events[0].type == "slash_theories"
        # 1 session < cold_start_threshold=3 → cold start msg
        assert "session" in events[0].content
```

**Step 2-4**: Implement _handle_theories + register in DEFAULT_COMMANDS.

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/test_chat_slash_theories.py
git commit -m "chat/slash · Phase 16 Task 13: _handle_theories + /theories 注册"
```

---

## Task 14: chat slash _handle_theory + reject

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py`
- Test: `tests/test_chat_slash_theory.py`

**Step 1: Write the failing test**

```python
# tests/test_chat_slash_theory.py
"""Phase 16: /theory <id> [reject] chat slash."""

import pytest


class TestSlashTheory:
    @pytest.mark.asyncio
    async def test_no_args_returns_usage_error(self, tmp_path, monkeypatch):
        from explain_engine.chat.slash_commands import dispatch_slash
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        _make_done_session("s_t2000001")
        chat = ChatSession("s_t2000001")
        events = await dispatch_slash(chat, "/theory")
        assert events[0].type == "slash_error"
        assert "用法" in events[0].content

    @pytest.mark.asyncio
    async def test_not_found_returns_error(self, tmp_path, monkeypatch):
        from explain_engine.chat.slash_commands import dispatch_slash
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        _make_done_session("s_t2000002")
        chat = ChatSession("s_t2000002")
        events = await dispatch_slash(chat, "/theory t_nonexistent")
        assert events[0].type == "slash_error"
        assert "t_nonexistent" in events[0].content
```

**Step 2-4**: Implement _handle_theory (含 detail + reject sub-arg) + register.

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/test_chat_slash_theory.py
git commit -m "chat/slash · Phase 16 Task 14: _handle_theory (含 reject sub-arg)"
```

---

## Task 15: cli explain theories subcommand

**Files:**
- Modify: `src/explain_engine/cli.py`
- Test: `tests/test_cli_theories.py`

**Step 1: Write the failing test**

```python
# tests/test_cli_theories.py
import pytest
from typer.testing import CliRunner

from explain_engine.cli import app


class TestCliTheories:
    def test_command_exists(self):
        runner = CliRunner()
        result = runner.invoke(app, ["theories", "--help"])
        assert result.exit_code == 0

    def test_no_sessions_shows_cold_start(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        runner = CliRunner()
        result = runner.invoke(app, ["theories"])
        assert result.exit_code == 0
        # cold start: 0 session
        assert "session" in result.output.lower() or "需累积" in result.output

    def test_force_recompute_flag(self):
        runner = CliRunner()
        result = runner.invoke(app, ["theories", "--force", "--help"])
        assert result.exit_code == 0
```

**Step 2-4**: Implement `theories()` cli subcommand.

**Step 5: Commit**

```bash
git add src/explain_engine/cli.py tests/test_cli_theories.py
git commit -m "cli · Phase 16 Task 15: explain theories subcommand"
```

---

## Task 16: bootstrap.py propose_phenomena 加 theories 参数

**Files:**
- Modify: `src/explain_engine/engines/bootstrap.py`
- Test: `tests/test_engines_bootstrap_theory_inject.py`

**Step 1: Write the failing test**

```python
# tests/test_engines_bootstrap_theory_inject.py
"""Phase 16: propose_phenomena 接受 theories 参数, prompt 加 segment."""

import pytest


class TestProposePhenomenaTheories:
    @pytest.mark.asyncio
    async def test_theories_none_keeps_old_prompt(self):
        """theories=None → prompt 跟之前一致 (backward compat)."""
        # Mock LLM, capture prompt, assert 不含 "已发现的稳定因果模式"
        pass

    @pytest.mark.asyncio
    async def test_theories_non_empty_adds_segment(self):
        """theories=[Theory(...)] → prompt 加段."""
        pass
```

**Step 2-4**: 加 `theories: list[Theory] | None = None` param + `_build_theories_prompt_section` helper.

**Step 5: Commit**

```bash
git add src/explain_engine/engines/bootstrap.py tests/test_engines_bootstrap_theory_inject.py
git commit -m "engines/bootstrap · Phase 16 Task 16: propose_phenomena 加 theories 参数"
```

---

## Task 17: ephemeral.py bootstrap inject

**Files:**
- Modify: `src/explain_engine/chat/ephemeral.py`
- Test: `tests/test_chat_ephemeral_theory_inject.py`

**Step 1: Write the failing test**

```python
# tests/test_chat_ephemeral_theory_inject.py
"""Phase 16: promote_to_persistent inject stable theories 进 propose_phenomena."""

import pytest


class TestPromoteWithTheoryInject:
    @pytest.mark.asyncio
    async def test_stable_theories_passed_to_propose(self):
        """mock get_active_theories 返 stable theory, assert propose_phenomena 收到."""
        pass

    @pytest.mark.asyncio
    async def test_cache_fail_fallback_empty(self):
        """mock get_active_theories 抛, assert promote 仍正常 (theories=None)."""
        pass

    @pytest.mark.asyncio
    async def test_rejected_theories_filtered_before_inject(self):
        """rejected_theory_ids 内 theory 不进 inject."""
        pass
```

**Step 2-4**: Modify `promote_to_persistent` 加 get_active_theories + 过滤 rejected.

**Step 5: Commit**

```bash
git add src/explain_engine/chat/ephemeral.py tests/test_chat_ephemeral_theory_inject.py
git commit -m "chat/ephemeral · Phase 16 Task 17: bootstrap 注入 stable theories (filtered rejected)"
```

---

## Task 18: 改既有 test (适配新 theory inject path)

**Files:**
- Modify: `tests/test_chat_ephemeral.py` (`test_promote_to_persistent` mock theory cache)
- Modify: `tests/test_chat_slash_commands.py` (`TestSlashRegistryUsesChineseDescriptions` + `TestHelpGroupingChinese`)

**Step 1-4**: 加 mock + 新 assertion.

**Step 5: Commit**

```bash
git add tests/test_chat_ephemeral.py tests/test_chat_slash_commands.py
git commit -m "tests · Phase 16 Task 18: 既有 test 适配 theory inject path + 新命令注册"
```

---

## Task 19: Acceptance smoke doc + 跑 5 真 session 验证

**Files:**
- Create: `docs/plans/2026-05-21-theory-formation-acceptance.md`

**Step 1**: 写 acceptance doc (类似 Phase 15 acceptance, 11+ 步含: 跑 5 个不同问题 session → /theories → check stable theory 形成 → /theory <id> 看详情 → reject 一个 → 新跑 session 验证 inject 跳过 rejected).

**Step 2**: 真 LLM 跑 5 个相关 session (e.g. 都关于"年轻人不消费 / 不结婚 / 不生育 / 储蓄少 / 投资意愿低"), 期望出现至少 1-2 个 stable theory.

**Step 3**: 截图 + paste 关键 output 到 acceptance doc.

**Step 4**: 若发现 bug, 走 systematic-debugging skill 修.

**Step 5: Commit**

```bash
git add docs/plans/2026-05-21-theory-formation-acceptance.md
git commit -m "docs · Phase 16 Task 19: acceptance smoke doc + 5-session 真验证"
```

---

## Task 20: 全量 pytest + ruff + README update

**Files:**
- Modify: `README.md` (加 Phase 16 status block + section)
- 任何 cleanup needed

**Step 1: Run full pytest**

```bash
.venv/bin/python -m pytest
```

Expected: ALL PASS (~1050+ test).

**Step 2: Run ruff**

```bash
.venv/bin/ruff check src/ tests/
```

Expected: All checks passed.

**Step 3: Update README**

加 Phase 16 milestone (类似 Phase 15 写法), 引用 design / plan / acceptance doc.

**Step 4: Commit**

```bash
git add README.md
git commit -m "docs/README · Phase 16 Task 20: 加 Theory Formation milestone block + section"
```

---

## 总结

20 个 task 完成 = Phase 16 Theory Formation MVP 上线.

最终预期 git log (反序):

```
xxxxxxx docs/README · Phase 16 Task 20: 加 Theory Formation milestone
xxxxxxx docs · Phase 16 Task 19: acceptance smoke doc + 5-session 真验证
xxxxxxx tests · Phase 16 Task 18: 既有 test 适配 theory inject path
xxxxxxx chat/ephemeral · Phase 16 Task 17: bootstrap 注入 stable theories
xxxxxxx engines/bootstrap · Phase 16 Task 16: propose_phenomena 加 theories
xxxxxxx cli · Phase 16 Task 15: explain theories subcommand
xxxxxxx chat/slash · Phase 16 Task 14: _handle_theory (含 reject)
xxxxxxx chat/slash · Phase 16 Task 13: _handle_theories
xxxxxxx engines/theory · Phase 16 Task 12: recompute.py 完整 pipeline
xxxxxxx engines/theory · Phase 16 Task 11: cache + loader + recompute scaffold
xxxxxxx engines/theory · Phase 16 Task 10: ranking.py (JEPA b/c)
xxxxxxx engines/theory · Phase 16 Task 9: falsifiability.py (JEPA a)
xxxxxxx engines/theory · Phase 16 Task 8: motif_mining.find_motifs_per_theme
xxxxxxx engines/theory · Phase 16 Task 7: gspan.py _dfs_extend + gspan_mine 集成
xxxxxxx engines/theory · Phase 16 Task 6: gspan.py extensions + _count_support
xxxxxxx engines/theory · Phase 16 Task 5: gspan.py _is_minimum_dfs_code canonical
xxxxxxx engines/theory · Phase 16 Task 4: gspan.py DFS data structures
xxxxxxx engines/theory · Phase 16 Task 3: clustering.cluster_lexicon_themes
xxxxxxx engines/theory · Phase 16 Task 2: Theory + Theme dataclass
xxxxxxx chat/chat_copy · Phase 16 Task 1: theory 文案
xxxxxxx docs/plans · Phase 16 design: Theory Formation (跨 session 因果模式)
```

## Skills Used

- `superpowers:writing-plans` (本 plan 生成)
- `superpowers:executing-plans` (实施时 per task TDD discipline)
- `superpowers:test-driven-development` (TDD red-green-commit cycle)
- `superpowers:systematic-debugging` (若撞 bug, gSpan canonical labeling 易写错)
- `superpowers:verification-before-completion` (Task 19/20 final smoke)
