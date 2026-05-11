import json
import re
import time

from explain_agent.graph.state import AttributionState, Citation, NarrativeClaim
from explain_agent.llm import LLMClient, get_strong_llm


def _call_with_retry(
    llm: LLMClient, system: str, user: str, max_tokens: int = 4000,
    attempts: int = 3, backoff: float = 2.0,
) -> str:
    """对 LLM 调用做 attempts 次重试（指数退避），全部失败时返回 ''。"""
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            return llm.chat(system=system, user=user, max_tokens=max_tokens)
        except Exception as e:
            last_err = e
            if i < attempts - 1:
                time.sleep(backoff ** i)
    return ""


DIM_REPORT_SYSTEM = """你是该维度的资深分析师。基于给定证据池，写一段 200-400 字的维度内归因报告。

要求:
- 每个关键论点后用 [e_xxx] 标注证据 id (例如: "存储芯片涨价 [e_001] [e_002]")
- evidence_id 必须来自给定证据池
- 不出现未在证据中出现的数字或具体描述
- 论述结构清晰、可读
- 不预测,不推荐操作

直接输出维度报告文本(不要 JSON, 不要标题, 不要前缀)。
"""


NARRATIVE_SYSTEM = """你是审慎的金融研究员。基于以下六维证据池，写一段 80-150 字的归因叙事。

输出格式 (JSON):
{
  "claims": [
    {"text": "一句话(15-40 字)", "evidence_ids": ["e_xxx", ...]}
  ]
}

要求:
- 每个 claim 必须挂 ≥1 个 evidence_id, evidence_id 必须来自给定证据池
- claim 之间逻辑连贯,可读为一段完整叙事
- 不出现数据(涨跌幅/金额/百分比)若该数据未在引用证据中出现
- 不预测/推荐操作
- 整体长度 80-150 字
只输出 JSON。
"""


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


_NUM_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:%|亿|万|千|百|次|倍|个|台|条)?")


def _extract_numbers(text: str) -> list[str]:
    return _NUM_PATTERN.findall(text)


def _normalize(token: str) -> str:
    return token.replace(" ", "")


def _estimate_overall_confidence(
    dim_results: dict,
    narrative_claims: list[NarrativeClaim],
) -> str:
    cited_ids: set[str] = set()
    for c in narrative_claims:
        cited_ids.update(c["evidence_ids"])

    source_types: set[str] = set()
    for r in dim_results.values():
        for e in r["evidence"]:
            if e.id in cited_ids:
                source_types.add(e.source_type)

    cited_count = len(cited_ids)
    type_count = len(source_types)

    if cited_count >= 8 and type_count >= 3:
        return "high"
    if cited_count >= 4 and type_count >= 2:
        return "medium"
    return "low"


def _rewrite_dim_report(
    dim_id: str,
    dim_result,
    target: str,
    market_facts: dict,
    llm: LLMClient,
) -> str:
    """单个维度的强模型重写。no_data 维度直接返回原 mini_summary。"""
    if dim_result["no_data"] or not dim_result["evidence"]:
        return dim_result["mini_summary"]

    evidence_dump = [
        {"id": e.id, "source_type": e.source_type, "snippet": e.snippet[:300]}
        for e in dim_result["evidence"]
    ]
    user = (
        f"维度: {dim_id}\n"
        f"标的: {target}\n"
        f"市场锚点: {market_facts.get('snippet', '')}\n"
        f"该维度证据池:\n{json.dumps(evidence_dump, ensure_ascii=False)}"
    )
    out = _call_with_retry(llm, DIM_REPORT_SYSTEM, user, max_tokens=4000)
    return out if out else dim_result["mini_summary"]


def _strip_unverified_numbers(
    claims: list[NarrativeClaim],
    evidence_pool: list,
) -> tuple[list[NarrativeClaim], list[str]]:
    """对每个 claim 检查其包含的数字 token 是否能在引用证据中找到精确匹配。
    找不到 → 整 claim 删除并加入 unverified_drops。
    无数字的 claim 直接保留。
    """
    evidence_by_id = {e.id: e for e in evidence_pool}
    kept: list[NarrativeClaim] = []
    dropped: list[str] = []

    for claim in claims:
        numbers = _extract_numbers(claim["text"])
        if not numbers:
            kept.append(claim)
            continue

        haystacks: list[str] = []
        for eid in claim["evidence_ids"]:
            e = evidence_by_id.get(eid)
            if e is None:
                continue
            haystacks.append(e.snippet or "")
            if e.raw_payload is not None:
                haystacks.append(json.dumps(e.raw_payload, ensure_ascii=False, default=str))
        haystack = _normalize("\n".join(haystacks))

        all_found = all(_normalize(n) in haystack for n in numbers)
        if all_found:
            kept.append(claim)
        else:
            dropped.append(claim["text"])

    return kept, dropped


async def report_builder_node(
    state: AttributionState,
    llm: LLMClient | None = None,
) -> dict:
    llm = llm or get_strong_llm()
    dim_results = state["dimension_results"]
    sub_results = state.get("subbranch_results", {})

    all_evidence: list = []
    for r in list(dim_results.values()) + list(sub_results.values()):
        all_evidence.extend(r["evidence"])

    evidence_dump = [
        {"id": e.id, "source_type": e.source_type, "snippet": e.snippet[:300]}
        for e in all_evidence
    ]

    user = (
        f"标的: {state['target']}\n"
        f"时间窗: {state['time_window'][0]} ~ {state['time_window'][1]}\n"
        f"市场锚点: {state['market_facts'].get('snippet', '')}\n"
        f"证据池:\n{json.dumps(evidence_dump, ensure_ascii=False)}"
    )
    raw = _call_with_retry(llm, NARRATIVE_SYSTEM, user, max_tokens=4000)
    data = _extract_json(raw)

    if not data or "claims" not in data:
        narrative = raw
        narrative_claims: list[NarrativeClaim] = []
        unverified_drops: list[str] = []
    else:
        claims_raw = data.get("claims", [])
        narrative_claims = [
            NarrativeClaim(text=c.get("text", ""), evidence_ids=c.get("evidence_ids", []))
            for c in claims_raw
            if c.get("text") and c.get("evidence_ids")
        ]
        narrative_claims, unverified_drops = _strip_unverified_numbers(narrative_claims, all_evidence)
        narrative = " ".join(c["text"] for c in narrative_claims)

    dim_reports = {
        dim_id: _rewrite_dim_report(dim_id, r, state["target"], state["market_facts"], llm)
        for dim_id, r in dim_results.items()
    }

    citations: list[Citation] = []
    seen_ids: set[str] = set()
    for r in list(dim_results.values()) + list(sub_results.values()):
        for e in r["evidence"]:
            if e.id in seen_ids:
                continue
            seen_ids.add(e.id)
            citations.append(Citation(
                evidence_id=e.id, url=e.url,
                snapshot_id=e.snapshot_id, source_type=e.source_type,
            ))

    confidence = _estimate_overall_confidence(dim_results, narrative_claims)

    return {
        "narrative": narrative,
        "narrative_claims": narrative_claims,
        "unverified_drops": unverified_drops,
        "dimension_reports": dim_reports,
        "citations": citations,
        "confidence": confidence,
    }
