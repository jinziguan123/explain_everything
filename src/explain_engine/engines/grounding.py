"""Phase G 证据接地引擎 — docs/设计预期-修正版.md §五.3 / §六。

三块职责:
1. compute_tiers: 由存储级 evidence_state + 图结构计算展示级认知等级
   (实证 fact / 推断 inference / 假设 hypothesis / 争议 contested)
2. ground_state: 接地管线 — 声明抽取 (light LLM) → web 检索 (≥2 独立来源)
   → 逐来源立场判定 (light LLM) → evidence 落盘 + evidence_state 更新
3. apply_grounded_confidence: §五.3 置信度公式
   confidence(edge) = base(tier) × llm_confidence
   废除"LLM 给自己打分即终值" — 无证据的边永远到不了高置信。

范围控制 (§六.1): 只对 (a) 全部 L0 现象 (b) 中心度 top-k 核心变量的入边
强制接地; 其余对象保持假设级, 由置信天花板压低其影响力。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from explain_engine.engines._llm_retry import call_with_retry
from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.evidence import Evidence, EvidenceState
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)

Tier = Literal["fact", "inference", "hypothesis", "contested"]

TIER_BASE: dict[Tier, float] = {
    "fact": 1.0,
    "inference": 0.7,
    "hypothesis": 0.4,
    "contested": 0.5,
}
"""§五.3: LLM 合理性评分只能在证据等级允许的天花板内浮动。
contested 介于假设与推断之间 — 有证据但互相冲突, 比无证据略可信。"""

TIER_LABELS: dict[Tier, str] = {
    "fact": "实证",
    "inference": "推断",
    "hypothesis": "假设",
    "contested": "争议",
}

MIN_SUPPORT_DOMAINS: int = 2
"""§六.1: verified 要求 ≥2 个独立域名的来源支持且无冲突。"""

GROUND_TOP_K: int = 3
"""默认对中心度 top-3 核心变量的入边接地。"""


# ─── 1. 认知等级计算 (§六.2 状态机的展示层) ──────────────────────


def compute_tiers(state: CognitiveState) -> dict[str, Tier]:
    """计算全图节点与边的认知等级。返回 {node_id|edge_id: tier}。

    判定规则 (§六.2):
    - verified  → fact (实证)
    - contested → contested (争议)
    - unverified 节点 → inference (推断), 若有 ≥1 入边且全部入边的
      source 节点为 fact/inference; 否则 hypothesis (假设)
    - unverified 边 → inference, 若 source 节点为 fact/inference; 否则 hypothesis

    推断沿生成方向 (L2→L1→L0) 传递: 被已实证机制生成的对象可信度继承。
    DAG 假设下递归带 memo; 防御性 cycle guard (在环上的按 hypothesis 处理)。
    """
    graph = state.graph
    in_edges: dict[str, list] = {}
    for e in graph.edges.values():
        in_edges.setdefault(e.target_node, []).append(e)

    memo: dict[str, Tier] = {}
    visiting: set[str] = set()

    def node_tier(nid: str) -> Tier:
        if nid in memo:
            return memo[nid]
        node = graph.nodes[nid]
        if node.evidence_state == "verified":
            memo[nid] = "fact"
        elif node.evidence_state == "contested":
            memo[nid] = "contested"
        elif nid in visiting:  # cycle guard
            return "hypothesis"
        else:
            visiting.add(nid)
            preds = in_edges.get(nid, [])
            if preds and all(
                node_tier(e.source_node) in ("fact", "inference") for e in preds
            ):
                memo[nid] = "inference"
            else:
                memo[nid] = "hypothesis"
            visiting.discard(nid)
        return memo[nid]

    tiers: dict[str, Tier] = {}
    for nid in graph.nodes:
        tiers[nid] = node_tier(nid)
    for eid, e in graph.edges.items():
        if e.evidence_state == "verified":
            tiers[eid] = "fact"
        elif e.evidence_state == "contested":
            tiers[eid] = "contested"
        elif tiers[e.source_node] in ("fact", "inference"):
            tiers[eid] = "inference"
        else:
            tiers[eid] = "hypothesis"
    return tiers


# ─── 2. 置信度公式 (§五.3) ───────────────────────────────────────


def apply_grounded_confidence(state: CognitiveState) -> int:
    """对全部边应用 confidence = base(tier) × llm_confidence。幂等。

    首次应用时把当前 confidence 存入 llm_confidence (LLM 原始值);
    之后重复调用始终从 llm_confidence 重算, 不会复利衰减。

    Returns:
        confidence 发生变化的边数。
    """
    tiers = compute_tiers(state)
    changed = 0
    for eid, edge in list(state.graph.edges.items()):
        raw = edge.llm_confidence if edge.llm_confidence is not None else edge.confidence
        new_conf = round(min(1.0, TIER_BASE[tiers[eid]] * raw), 4)
        if edge.llm_confidence is None or abs(new_conf - edge.confidence) > 1e-9:
            state.graph.replace_edge(
                eid, edge.model_copy(
                    update={"llm_confidence": raw, "confidence": new_conf}
                ),
            )
            if abs(new_conf - edge.confidence) > 1e-9:
                changed += 1
    return changed


# ─── 3. 接地管线 (§六.1) ─────────────────────────────────────────

# search_fn 签名与 chat.web_search.web_search 一致, 可注入 (测试 / 换源)。
SearchFn = Callable[[str], Awaitable[Sequence]]


class _ClaimItem(BaseModel):
    target_id: str
    claim: str = Field(min_length=1)


class _ClaimsOutput(BaseModel):
    claims: list[_ClaimItem]


class _SourceStance(BaseModel):
    index: int = Field(ge=0)
    stance: Literal["support", "contradict", "irrelevant"]


class _StanceOutput(BaseModel):
    verdicts: list[_SourceStance]


@dataclass
class GroundingSummary:
    """ground_state 的执行摘要 (CLI / bench manifest 用)。"""

    targets_total: int = 0
    verified: int = 0
    contested: int = 0
    unverified: int = 0
    evidence_added: int = 0
    edges_reweighted: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "targets_total": self.targets_total,
            "verified": self.verified,
            "contested": self.contested,
            "unverified": self.unverified,
            "evidence_added": self.evidence_added,
            "edges_reweighted": self.edges_reweighted,
            "errors": list(self.errors),
        }


def ground_targets(
    state: CognitiveState, top_k: int = GROUND_TOP_K,
) -> tuple[list[str], list[str]]:
    """选择接地对象 (§六.1 成本控制): (全部 L0 节点 id, 核心变量入边 id)。"""
    from explain_engine.report.dossier import core_variables

    l0_ids = sorted(
        nid for nid, n in state.graph.nodes.items()
        if n.abstraction_level == 0 and n.lifecycle_state != "decayed"
    )
    core = set(core_variables(state, k=top_k))
    edge_ids = sorted(
        eid for eid, e in state.graph.edges.items() if e.target_node in core
    )
    return l0_ids, edge_ids


def _domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc.removeprefix("www.")


def _verdict_from_evidence(evs: list[Evidence]) -> EvidenceState:
    """聚合判定 (§六.1): ≥2 独立域名支持且 0 冲突 → verified;
    支持与冲突并存 → contested; 否则 unverified。"""
    support_domains = {_domain(e.url) for e in evs if e.stance == "support"}
    contradict_domains = {_domain(e.url) for e in evs if e.stance == "contradict"}
    if support_domains and contradict_domains:
        return "contested"
    if len(support_domains) >= MIN_SUPPORT_DOMAINS:
        return "verified"
    return "unverified"


def _next_evidence_id(state: CognitiveState) -> str:
    existing = [
        int(eid.split("_")[1])
        for eid in state.evidence
        if eid.startswith("ev_") and eid[3:].isdigit()
    ]
    n = (max(existing) + 1) if existing else 1
    return f"ev_{n:03d}"


def _render_targets_table(state: CognitiveState, l0_ids: list[str], edge_ids: list[str]) -> str:
    lines = []
    for nid in l0_ids:
        n = state.graph.nodes[nid]
        lines.append(f"- [{nid}] 节点: {n.name} — {n.description}")
    for eid in edge_ids:
        e = state.graph.edges[eid]
        src = state.graph.nodes[e.source_node]
        tgt = state.graph.nodes[e.target_node]
        lines.append(
            f"- [{eid}] 因果断言: {src.name} ─{e.relation_type}→ {tgt.name}"
            f" (机制: {e.mechanism_description})"
        )
    return "\n".join(lines)


async def _extract_claims(
    state: CognitiveState, l0_ids: list[str], edge_ids: list[str], llm: LLMClient,
) -> dict[str, str]:
    """声明抽取 (1 次 LLM call, 批量)。返回 {target_id: claim}。"""
    prompt = load_prompt("grounding_claim")
    messages = [
        Message(role="system", content=prompt["system"]),
        Message(role="user", content=prompt["user_template"].format(
            question=state.root_question,
            targets_table=_render_targets_table(state, l0_ids, edge_ids),
        )),
    ]
    out: _ClaimsOutput = await call_with_retry(
        llm, messages, _ClaimsOutput,
        error_prefix="grounding 声明抽取输出不合规",
    )
    wanted = set(l0_ids) | set(edge_ids)
    return {c.target_id: c.claim for c in out.claims if c.target_id in wanted}


async def _judge_stances(
    claim: str, results: Sequence, llm: LLMClient,
) -> list[tuple[int, str]]:
    """逐来源立场判定 (1 次 LLM call)。返回 [(result_index, stance)]。"""
    prompt = load_prompt("grounding_stance")
    sources_table = "\n".join(
        f"[{i}] {r.title}\n    {r.snippet}" for i, r in enumerate(results)
    )
    messages = [
        Message(role="system", content=prompt["system"]),
        Message(role="user", content=prompt["user_template"].format(
            claim=claim, sources_table=sources_table,
        )),
    ]
    out: _StanceOutput = await call_with_retry(
        llm, messages, _StanceOutput,
        error_prefix="grounding 立场判定输出不合规",
    )
    return [
        (v.index, v.stance) for v in out.verdicts
        if 0 <= v.index < len(results)
    ]


async def ground_state(
    state: CognitiveState,
    llm: LLMClient,
    search_fn: SearchFn | None = None,
    top_k: int = GROUND_TOP_K,
    max_results: int = 5,
    concurrency: int = 4,
) -> GroundingSummary:
    """接地管线主入口 (§六.1)。原地修改 state, 返回执行摘要。

    流程: 选 targets → 声明抽取 (1 LLM call) → 每 target 检索 + 立场判定
    (1 search + 1 light-LLM call, Semaphore 限 concurrency 路并发)
    → evidence 落盘 + evidence_state 更新 (按 target 顺序串行,
    保证 evidence id 确定性) → 全图应用置信度公式 (§五.3)。

    单 target 失败 (限速/解析) 不中断: 记入 summary.errors, 该 target
    维持 unverified — 接地是 best-effort 增强, 不是硬依赖。

    Args:
        llm: 建议传 light model (分类型任务)。
        search_fn: 默认 chat.web_search.web_search (DuckDuckGo 免费端点)。
        concurrency: 检索+判定的并发路数 (默认 4; 1 = 串行, 防限速可调低)。
    """
    import asyncio

    if search_fn is None:
        from explain_engine.chat.web_search import web_search as search_fn

    summary = GroundingSummary()
    l0_ids, edge_ids = ground_targets(state, top_k=top_k)
    summary.targets_total = len(l0_ids) + len(edge_ids)
    if summary.targets_total == 0:
        return summary

    try:
        claims = await _extract_claims(state, l0_ids, edge_ids, llm)
    except Exception as exc:
        # 声明抽取整体失败 → 全部维持 unverified, 但置信公式仍要应用
        summary.errors.append(f"claim_extraction: {type(exc).__name__}: {exc}")
        summary.unverified = summary.targets_total
        summary.edges_reweighted = apply_grounded_confidence(state)
        return summary

    # ── 并发段: 只做 I/O (检索 + 判定), 不碰 state ──
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _fetch(target_id: str, claim: str):
        async with sem:
            try:
                results = list(await search_fn(claim))[:max_results]
                stances = (
                    await _judge_stances(claim, results, llm) if results else []
                )
                return target_id, results, stances, None
            except Exception as exc:
                return target_id, [], [], exc

    ordered = [*l0_ids, *edge_ids]
    with_claims = [(t, claims[t]) for t in ordered if claims.get(t)]
    fetched = await asyncio.gather(*[_fetch(t, c) for t, c in with_claims])
    by_target = {t: (results, stances, err) for t, results, stances, err in fetched}

    # ── 串行段: 按原顺序落盘 (evidence id 确定性 + 无并发写 state) ──
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for target_id in ordered:
        if target_id not in by_target:
            summary.unverified += 1  # 声明抽取没给 claim
            continue
        results, stances, err = by_target[target_id]
        claim = claims[target_id]
        if err is not None:
            summary.errors.append(f"{target_id}: {type(err).__name__}: {err}")
            summary.unverified += 1
            continue

        evs: list[Evidence] = []
        for idx, stance in stances:
            if stance == "irrelevant":
                continue
            r = results[idx]
            ev = Evidence(
                id=_next_evidence_id(state), claim=claim,
                url=r.url, title=r.title, snippet=r.snippet,
                stance=stance, retrieved_at=now,
            )
            state.evidence[ev.id] = ev
            evs.append(ev)

        verdict = _verdict_from_evidence(evs)
        ev_ids = [e.id for e in evs]
        if target_id in state.graph.nodes:
            node = state.graph.nodes[target_id]
            state.graph.replace_node(target_id, node.model_copy(update={
                "evidence_state": verdict,
                "evidence_ids": [*node.evidence_ids, *ev_ids],
                # verified 现象升级 epistemic → fact (措辞分级用)
                **({"epistemic": "fact"} if verdict == "verified"
                   and node.abstraction_level == 0 else {}),
            }))
        else:
            edge = state.graph.edges[target_id]
            state.graph.replace_edge(target_id, edge.model_copy(update={
                "evidence_state": verdict,
                "evidence_ids": [*edge.evidence_ids, *ev_ids],
            }))

        summary.evidence_added += len(evs)
        if verdict == "verified":
            summary.verified += 1
        elif verdict == "contested":
            summary.contested += 1
        else:
            summary.unverified += 1
        logger.info("grounding: %s → %s (%d 证据)", target_id, verdict, len(evs))

    summary.edges_reweighted = apply_grounded_confidence(state)
    return summary
