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

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph

# ─── 常量 ─────────────────────────────────────────
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
        candidates: list[tuple[str, str, RelationEdge, float]] = []
        for src in sorted(frontier):
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
            top_k = sorted(new_layer.items(), key=lambda x: x[1], reverse=True)[:MAX_ACTIVE_VARIABLES]
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


def get_all_L0(graph: ExplanationGraph) -> set[str]:
    """所有 abstraction_level=0 节点 ids."""
    return {nid for nid, n in graph.nodes.items() if n.abstraction_level == 0}


def get_all_L1_L2(graph: ExplanationGraph) -> set[str]:
    """所有 abstraction_level >= 1 节点 ids."""
    return {nid for nid, n in graph.nodes.items() if n.abstraction_level >= 1}


def next_id(graph: ExplanationGraph, prefix: str) -> str:
    """生成下一个未占用的 {prefix}_NNN 节点 id (3 位 zero-padded)."""
    existing = [
        int(nid.split("_")[1])
        for nid in graph.nodes
        if nid.startswith(f"{prefix}_") and nid[len(prefix)+1:].isdigit()
    ]
    n = (max(existing) + 1) if existing else 1
    return f"{prefix}_{n:03d}"


def next_edge_id(graph: ExplanationGraph) -> str:
    """生成下一个未占用的 e_NNN edge id."""
    existing = [
        int(eid.split("_")[1])
        for eid in graph.edges
        if eid.startswith("e_") and eid[2:].isdigit()
    ]
    n = (max(existing) + 1) if existing else 1
    return f"e_{n:03d}"
