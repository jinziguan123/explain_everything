from explain_agent.graph.state import AttributionState, Citation
from explain_agent.llm import LLMClient, get_strong_llm


NARRATIVE_SYSTEM = """你是审慎的金融研究员。基于以下六维归因证据，
写一段 80-150 字的归因叙事段。

要求:
- 用客观语气总结主因
- 不要预测/推荐操作
- 没有证据的论点直接砍掉
- 不要列点,要连贯叙事
"""


async def report_builder_node(
    state: AttributionState,
    llm: LLMClient | None = None,
) -> dict:
    llm = llm or get_strong_llm()
    dim_results = state["dimension_results"]
    sub_results = state.get("subbranch_results", {})

    summaries = []
    for dim_id, r in dim_results.items():
        summaries.append(f"[{dim_id}] {r['mini_summary']}")
    for sub_name, r in sub_results.items():
        summaries.append(f"[子分支:{sub_name}] {r['mini_summary']}")

    user = (
        f"标的: {state['target']}\n"
        f"时间窗: {state['time_window'][0]} ~ {state['time_window'][1]}\n"
        f"市场锚点: {state['market_facts'].get('snippet', '')}\n"
        f"维度摘要:\n" + "\n\n".join(summaries)
    )
    narrative = llm.chat(system=NARRATIVE_SYSTEM, user=user, max_tokens=4000)

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
        "dimension_reports": dim_reports,
        "citations": citations,
        "confidence": confidence,
    }
