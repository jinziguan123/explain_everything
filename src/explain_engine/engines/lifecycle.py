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
W_EXPLANATORY: float = 1.0
"""主项权重 (顶层 §11.3 '最大解释力' 占主导, 故权重 1.0 而其他项 < 1.0)."""
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

    L0 nodes:
      compute_fitness 不区分 abstraction_level — 任何节点都返回一个浮点数.
      但 L0 (concrete observations) 的 explanatory_power 信号没意义 (L0 是 ground
      truth, 不被 consistency_score 评估). L0 默认走 explanatory=0.5.

      约定: Task 4.2 update_lifecycle / pick_decay_target 应只迭代 L1+L2,
      不对 L0 做 lifecycle 决策 (L0 = facts, 不是 evolving theories).
      调用方负责过滤; engine 不在此 raise (避免上层重复检查).

    Returns:
        fitness ∈ [0.0, 1.8]. 越大 = 越健康.
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
    out_count = sum(1 for _ in state.graph.outgoing_edges(node.id))
    in_count = sum(
        1 for e in state.graph.edges.values()
        if e.target_node == node.id
    )
    degree = out_count + in_count

    if state.graph.nodes:
        max_degree = max(
            sum(1 for _ in state.graph.outgoing_edges(nid))
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
        explanatory * W_EXPLANATORY
        + reuse * W_REUSE
        + stability * W_STABILITY
        + centrality * W_CENTRALITY
        - redundancy
    )
    return max(0.0, fitness)
