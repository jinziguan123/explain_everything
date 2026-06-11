"""压缩值 (Compression Value) — docs/设计预期-修正版.md §五.1 / §五.4。

把"解释 = 压缩"从哲学变成判据:

    覆盖 Cov(G) = Σ_{p∈L0} act(p)     # 从 L2/L1 root 传播后的 L0 激活和
    描述长度 L(G) = |V_L1| + |V_L2| + 0.5·|E|
    压缩值 CV(G) = Cov(G) / L(G)

收敛判据 (§五.4): 连续 W=3 tick 满足 ΔCV < ε=0.01 → 收敛 (cv_converged)。

已知可博弈点 (文档 §五.1): 把多个机制塞进一个变量可压低 L —
对策是变量准入的机制单一性审查, 不在本模块。

纯函数, 0 LLM call。复用 _propagation 的带衰减传播 — 接地后 (Phase G)
edge.confidence 已是证据加权有效置信, CV 自动反映证据质量。
"""

from __future__ import annotations

from dataclasses import dataclass

from explain_engine.engines._propagation import propagate
from explain_engine.schema.state import CognitiveState

CV_EPSILON: float = 0.01
"""§五.4: ΔCV 低于此值算"无边际增益"。"""

CV_STALE_WINDOW: int = 3
"""§五.4: 连续多少个 tick 无边际增益判收敛。"""

EDGE_LENGTH_WEIGHT: float = 0.5
"""§五.1: 边在描述长度中的权重。"""


@dataclass(frozen=True)
class CompressionReport:
    """单次压缩值计算结果。"""

    coverage: float
    """Σ L0 激活 (decayed 节点不计)。"""
    length: float
    """|L1|+|L2| + 0.5·|E| (decayed 节点与其关联边不计)。"""
    cv: float
    """coverage / length; length=0 时为 0。"""


def compression_value(state: CognitiveState) -> CompressionReport:
    """计算当前图的压缩值。

    roots 策略与 rollout_from_roots 一致: active L2, 退化用 active L1。
    """
    graph = state.graph

    def _active(nid: str) -> bool:
        return graph.nodes[nid].lifecycle_state != "decayed"

    l0_ids = [
        nid for nid, n in graph.nodes.items()
        if n.abstraction_level == 0 and _active(nid)
    ]
    l1l2_ids = [
        nid for nid, n in graph.nodes.items()
        if n.abstraction_level >= 1 and _active(nid)
    ]
    l2_ids = [nid for nid in l1l2_ids if graph.nodes[nid].abstraction_level == 2]
    roots = set(l2_ids) if l2_ids else set(l1l2_ids)

    if roots and l0_ids:
        activations, _trace = propagate(graph, roots)
        coverage = sum(activations.get(nid, 0.0) for nid in l0_ids)
    else:
        coverage = 0.0

    active_ids = {nid for nid in graph.nodes if _active(nid)}
    n_edges = sum(
        1 for e in graph.edges.values()
        if e.source_node in active_ids and e.target_node in active_ids
    )
    length = len(l1l2_ids) + EDGE_LENGTH_WEIGHT * n_edges
    cv = (coverage / length) if length > 0 else 0.0
    return CompressionReport(coverage=coverage, length=length, cv=cv)


def cv_converged(cv_history: list[float]) -> bool:
    """§五.4 收敛判据: 最近 CV_STALE_WINDOW 个增量全部 < CV_EPSILON。

    需要至少 W+1 个观测 (W 个增量); 增量取绝对值 — CV 下降同样算
    "无正向边际增益" (decay/prune 导致的小幅波动不应阻止收敛)。
    """
    w = CV_STALE_WINDOW
    if len(cv_history) < w + 1:
        return False
    recent = cv_history[-(w + 1):]
    return all(
        abs(recent[i + 1] - recent[i]) < CV_EPSILON for i in range(w)
    )
