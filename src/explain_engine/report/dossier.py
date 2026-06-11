"""图谱档案 (Dossier) — 叙事报告的唯一信息源。

设计预期-修正版 §九 Phase V: 叙事层是最后一步, 它只能复述图里有的东西。
本模块是纯函数 (0 LLM call): 把 CognitiveState + AcceptanceReport 渲染成
一份结构化 markdown 档案, 交给 narrative.generate_report 作为 LLM 的
全部上下文 —— LLM 不被允许引入档案之外的内容, 这是防伪深刻的执行机制。
"""

from __future__ import annotations

from explain_engine.engines.simulation import aggregate_acceptance
from explain_engine.schema.state import CognitiveState


def core_variables(state: CognitiveState, k: int = 3) -> list[str]:
    """中心度 top-k 的 L1/L2 节点 id (反直觉度 CI 的核心集, §五.2)。

    Phase V 代理: 中心度 = 无向度数 (出边 + 入边)。decayed 节点不参与。
    """
    degree: dict[str, int] = {}
    for e in state.graph.edges.values():
        degree[e.source_node] = degree.get(e.source_node, 0) + 1
        degree[e.target_node] = degree.get(e.target_node, 0) + 1
    candidates = [
        (nid, degree.get(nid, 0))
        for nid, n in state.graph.nodes.items()
        if n.abstraction_level in (1, 2) and n.lifecycle_state != "decayed"
    ]
    candidates.sort(key=lambda t: (-t[1], t[0]))
    return [nid for nid, _ in candidates[:k]]


def graph_stats(state: CognitiveState) -> dict:
    """bench manifest 用的成本/规模统计 (纯函数)。"""
    levels = {0: 0, 1: 0, 2: 0}
    for n in state.graph.nodes.values():
        levels[n.abstraction_level] += 1
    return {
        "nodes_total": len(state.graph.nodes),
        "nodes_l0": levels[0],
        "nodes_l1": levels[1],
        "nodes_l2": levels[2],
        "edges": len(state.graph.edges),
        "ticks": state.tick,
        "llm_calls_in_trace": sum(e.llm_calls for e in state.reasoning_trace),
    }


def _causal_chains(state: CognitiveState, max_chains: int = 12) -> list[str]:
    """L2 →(causes 等)→ L1 →(manifests_as)→ L0 的代表性因果链。

    每条链渲染为 "L2名 →[机制] L1名 →[机制] L0名"。按边 confidence 乘积降序截断。
    """
    out_edges: dict[str, list] = {}
    for e in state.graph.edges.values():
        out_edges.setdefault(e.source_node, []).append(e)

    nodes = state.graph.nodes
    chains: list[tuple[float, str]] = []
    l2_ids = [nid for nid, n in nodes.items()
              if n.abstraction_level == 2 and n.lifecycle_state != "decayed"]
    for l2 in l2_ids:
        for e1 in out_edges.get(l2, []):
            mid = nodes.get(e1.target_node)
            if mid is None or mid.abstraction_level != 1:
                continue
            for e2 in out_edges.get(e1.target_node, []):
                leaf = nodes.get(e2.target_node)
                if leaf is None or leaf.abstraction_level != 0:
                    continue
                strength = e1.confidence * e2.confidence
                text = (
                    f"[{l2}] {nodes[l2].name} ─{e1.relation_type}→ "
                    f"[{mid.id}] {mid.name} ─{e2.relation_type}→ "
                    f"[{leaf.id}] {leaf.name}"
                    f"  (强度 {strength:.2f}; 机制: {e1.mechanism_description})"
                )
                chains.append((strength, text))
    chains.sort(key=lambda t: -t[0])
    return [text for _, text in chains[:max_chains]]


def _render_node_line(n, tier_label: str, extra: str = "") -> str:
    decayed = " (已衰亡)" if n.lifecycle_state == "decayed" else ""
    return (
        f"- [{n.id}] **{n.name}**{decayed} — {n.description}"
        f"  (认知等级: {tier_label}; 置信 {n.confidence:.2f}{extra})"
    )


def _render_evidence_appendix(state: CognitiveState) -> list[str]:
    """证据附录: 按 target 列出已落盘的来源 (叙事报告引用来源的依据)。"""
    if not state.evidence:
        return []
    by_target: dict[str, list] = {}
    for obj_id, obj in [*state.graph.nodes.items(), *state.graph.edges.items()]:
        for ev_id in obj.evidence_ids:
            ev = state.evidence.get(ev_id)
            if ev is not None:
                by_target.setdefault(obj_id, []).append(ev)
    if not by_target:
        return []
    stance_zh = {"support": "支持", "contradict": "反驳"}
    lines = ["## 证据附录 (接地管线检索结果)"]
    for obj_id in sorted(by_target):
        lines.append(f"- 关于 [{obj_id}]:")
        for ev in by_target[obj_id]:
            lines.append(
                f"  - ({stance_zh[ev.stance]}) {ev.title} — {ev.url}"
            )
    lines.append("")
    return lines


def build_dossier(state: CognitiveState, prior_causes: list[str] | None = None) -> str:
    """构建图谱档案 markdown。

    Args:
        state: 收敛后的 CognitiveState。
        prior_causes: 先验预期原因列表 (§五.2 反直觉度的 Prior 集, 可选)。
    """
    # 局部 import: grounding.ground_targets 反向引用本模块的 core_variables
    # (函数体内 deferred import), 顶层互引会循环。
    from explain_engine.engines.grounding import TIER_LABELS, compute_tiers
    from explain_engine.engines.metrics import compression_value

    report = aggregate_acceptance(state)
    tiers = compute_tiers(state)
    cv = compression_value(state)
    nodes = state.graph.nodes
    by_level: dict[int, list] = {0: [], 1: [], 2: []}
    for n in nodes.values():
        by_level[n.abstraction_level].append(n)
    for lst in by_level.values():
        lst.sort(key=lambda n: n.id)

    core = core_variables(state)

    lines: list[str] = []
    lines.append(f"# 图谱档案: {state.root_question}")
    lines.append("")
    lines.append(
        f"规模: {len(nodes)} 节点 / {len(state.graph.edges)} 边; "
        f"推理 {state.tick} tick。"
    )
    lines.append(
        f"接受度: 平均一致性 {report.avg_consistency:.2f}, "
        f"平均必要性 {report.avg_essentialness:.2f}, "
        f"现象覆盖率 {report.rollout_coverage:.0%}; "
        f"压缩值 CV {cv.cv:.2f} (覆盖 {cv.coverage:.2f} / 结构长度 {cv.length:.1f})。"
    )
    n_verified = sum(
        1 for t in tiers.values() if t == "fact"
    )
    n_contested = sum(1 for t in tiers.values() if t == "contested")
    if state.evidence:
        lines.append(
            f"证据接地: {len(state.evidence)} 条来源; "
            f"实证 {n_verified} / 争议 {n_contested} (其余为推断或假设)。"
        )
    else:
        lines.append("证据接地: 未执行 — 全部内容为 LLM 内省, 按假设/推断级措辞。")
    if report.missing_l0:
        missing_names = [
            f"[{nid}] {nodes[nid].name}" for nid in report.missing_l0 if nid in nodes
        ]
        lines.append(f"未被任何机制解释的现象 (解释残差): {'; '.join(missing_names)}")
    lines.append("")

    def _tier_label(obj_id: str) -> str:
        return TIER_LABELS[tiers[obj_id]]

    lines.append("## 一、现象层 (L0, 待解释的观察)")
    for n in by_level[0]:
        extra = f"; 证据 {len(n.evidence_ids)} 条" if n.evidence_ids else ""
        lines.append(_render_node_line(n, _tier_label(n.id), extra))
    lines.append("")

    lines.append("## 二、模式层 (L1, 归纳出的中层机制)")
    for n in by_level[1]:
        cons = report.per_l1.get(n.id)
        extra = f"; 一致性 {cons:.2f}" if cons is not None else ""
        if n.id in report.weak_chain_l1s:
            extra += "; ⚠ 弱链"
        lines.append(_render_node_line(n, _tier_label(n.id), extra))
    lines.append("")

    lines.append("## 三、驱动层 (L2, 深层驱动变量)")
    for n in by_level[2]:
        ess = report.per_l2.get(n.id)
        extra = f"; 必要性 {ess:.2f}" if ess is not None else ""
        lines.append(_render_node_line(n, _tier_label(n.id), extra))
    lines.append("")

    lines.append("## 四、关键因果链 (按强度降序)")
    chains = _causal_chains(state)
    if chains:
        lines.extend(f"- {c}" for c in chains)
    else:
        lines.append("- (无完整 L2→L1→L0 链)")
    lines.append("")

    lines.append("## 五、全部因果边")
    for e in sorted(state.graph.edges.values(), key=lambda e: e.id):
        src = nodes.get(e.source_node)
        tgt = nodes.get(e.target_node)
        if src is None or tgt is None:
            continue
        lines.append(
            f"- [{e.id}] [{src.id}] {src.name} ─{e.relation_type}"
            f"({e.confidence:.2f})→ [{tgt.id}] {tgt.name}: "
            f"{e.mechanism_description} (认知等级: {_tier_label(e.id)})"
        )
    lines.append("")

    core_names = [f"[{nid}] {nodes[nid].name}" for nid in core if nid in nodes]
    lines.append("## 六、核心变量 (中心度 top-3)")
    lines.extend(f"- {c}" for c in core_names)
    if prior_causes:
        lines.append("")
        lines.append("## 七、提问前的先验预期原因 (用于反直觉度对照)")
        lines.extend(f"- {p}" for p in prior_causes)
    lines.append("")
    lines.extend(_render_evidence_appendix(state))
    return "\n".join(lines)
