import json
import re

from explain_agent.graph.state import AttributionState
from explain_agent.llm import LLMClient, get_strong_llm


SYSTEM = """你是金融归因 agent 的证据合成器。

任务：浏览 6 维证据，判断是否有"反复出现但未被框架维度覆盖"的实体/事件/政策值得开动态子分支。

判断标准：
- 该实体/事件至少在 3 条证据中出现
- 它代表一个独立主题（不是某一维的细分）
- 进一步检索能带来增量信息

输出 JSON:
{
  "needs_subbranch": true|false,
  "subbranches": [
    {"name": "...", "query_hints": ["关键词1", "关键词2"]}
  ]
}

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


async def synthesizer_node(
    state: AttributionState,
    llm: LLMClient | None = None,
) -> dict:
    llm = llm or get_strong_llm()
    evidence_dump = []
    for dim_id, r in state["dimension_results"].items():
        for e in r["evidence"][:10]:
            evidence_dump.append({"dim": dim_id, "id": e.id, "snippet": e.snippet[:300]})

    user = f"target: {state['target']}\nevidence:\n{json.dumps(evidence_dump, ensure_ascii=False)}"
    raw = llm.chat(system=SYSTEM, user=user, max_tokens=4000)
    data = _extract_json(raw)
    if not data:
        return {"needs_subbranch": False, "subbranches": []}
    needs = bool(data.get("needs_subbranch", False))
    branches = data.get("subbranches", [])[:2]
    return {"needs_subbranch": needs and bool(branches), "subbranches": branches}
