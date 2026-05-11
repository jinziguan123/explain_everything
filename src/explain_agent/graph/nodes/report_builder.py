import json
import re

from explain_agent.graph.state import AttributionState, Citation, NarrativeClaim
from explain_agent.llm import LLMClient, get_strong_llm


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
    raw = llm.chat(system=NARRATIVE_SYSTEM, user=user, max_tokens=4000)
    data = _extract_json(raw)

    if not data or "claims" not in data:
        narrative = raw
        narrative_claims: list[NarrativeClaim] = []
    else:
        claims_raw = data.get("claims", [])
        narrative_claims = [
            NarrativeClaim(text=c.get("text", ""), evidence_ids=c.get("evidence_ids", []))
            for c in claims_raw
            if c.get("text") and c.get("evidence_ids")
        ]
        narrative = " ".join(c["text"] for c in narrative_claims)

    dim_reports = {dim_id: r["mini_summary"] for dim_id, r in dim_results.items()}

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

    high_count = sum(1 for r in dim_results.values() if r["confidence"] == "high")
    if high_count >= 3:
        confidence = "high"
    elif high_count >= 1:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "narrative": narrative,
        "narrative_claims": narrative_claims,
        "dimension_reports": dim_reports,
        "citations": citations,
        "confidence": confidence,
    }
