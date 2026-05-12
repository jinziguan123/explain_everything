import json
import re
from typing import Any

from explain_agent.core.types import AdapterQuery
from explain_agent.graph.state import AttributionState, ConnectionThread
from explain_agent.llm import LLMClient


PROPOSE_SYSTEM = """你是金融归因 agent 的延伸思考器。基于 6 维核心归因和现有证据池，
提议 ≤3 个值得探索的"延伸议题"——指反复出现但未被 6 维覆盖的实体/事件/趋势，
或与本议题强相关的跨学科 / 跨地域 / 跨时间的对照案例。

输出 JSON:
{
  "threads": [
    {
      "title": "议题标题(15-30 字)",
      "hypothesis": "为什么这个议题值得延伸",
      "need_web_search": true|false,
      "confidence": 1-5,
      "overlap_with_main_dims": true|false,
      "query_keywords": ["关键词1", "关键词2"]
    }
  ]
}

规则:
- ≤3 个，宁可少不可多
- need_web_search=true 适用于"最新政策/制裁/事件/海外信号"等需要 5 天内时效信息的议题
- need_web_search=false 适用于"产业链结构/历史类比/概念延伸"等本地新闻语料能覆盖的议题
- overlap_with_main_dims=true 表示与 6 维核心报告中已论述的内容重复，自己标 True 让上层砍掉
- confidence 是对"该议题真的能延伸出有价值信息"的自评

只输出 JSON。
"""


ANSWER_SYSTEM = """你是延伸议题的回答器。基于给定证据写一段 100-200 字的回答。

要求:
- 用 [e_xxx] 格式标注引用的 evidence_id
- 不出现未在证据中出现的数据
- 不预测/不推荐操作
- 如果证据不足以回答，直接说"现有证据不足以展开此延伸"

直接输出文本。
"""


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def connection_explorer_node(
    state: AttributionState,
    llm: LLMClient,
    adapter_registry: dict[str, Any],
) -> dict:
    # 1. 强模型提议延伸议题
    dim_reports = state.get("dimension_reports", {})
    dim_results = state.get("dimension_results", {})
    evidence_dump = []
    for dim_id, r in dim_results.items():
        for e in r["evidence"][:5]:
            evidence_dump.append({"dim": dim_id, "id": e.id, "snippet": e.snippet[:200]})

    user = (
        f"target: {state.get('target')}\n"
        f"time_window: {state.get('time_window')}\n"
        f"6 维报告: {json.dumps(dim_reports, ensure_ascii=False)}\n"
        f"证据池(节选): {json.dumps(evidence_dump, ensure_ascii=False)}"
    )
    try:
        raw = llm.chat(system=PROPOSE_SYSTEM, user=user, max_tokens=2000)
    except Exception:
        return {"connection_threads": []}
    data = _extract_json(raw)
    if not data or "threads" not in data:
        return {"connection_threads": []}

    # 2. 过滤 + 截断
    proposals = [
        p for p in data["threads"]
        if isinstance(p, dict)
        and not p.get("overlap_with_main_dims", False)
        and int(p.get("confidence", 0)) >= 3
    ]
    proposals.sort(key=lambda p: int(p.get("confidence", 0)), reverse=True)
    proposals = proposals[:3]

    # 3. 每个 proposal 走检索 + 回答
    threads: list[ConnectionThread] = []
    for p in proposals:
        keywords = list(p.get("query_keywords") or [])
        need_web = bool(p.get("need_web_search", False))
        source: str = "local"
        evidences = []
        if need_web and "web_search" in adapter_registry:
            try:
                evidences = await adapter_registry["web_search"].query(
                    AdapterQuery(
                        keywords=keywords,
                        time_window=state["time_window"],
                        target=state["target"],
                        limit=5,
                    )
                )
                source = "web"
            except Exception:
                evidences = []
        if not evidences and "news_corpus" in adapter_registry:
            try:
                evidences = await adapter_registry["news_corpus"].query(
                    AdapterQuery(
                        keywords=keywords,
                        time_window=state["time_window"],
                        target=state["target"],
                        limit=5,
                    )
                )
                if source == "web":
                    source = "mixed"
                else:
                    source = "local"
            except Exception:
                evidences = []
        if not evidences:
            continue

        ev_dump = [
            {"id": e.id, "source_type": e.source_type, "snippet": (e.snippet or "")[:300]}
            for e in evidences
        ]
        ans_user = (
            f"议题: {p.get('title')}\n"
            f"提议理由: {p.get('hypothesis')}\n"
            f"证据池: {json.dumps(ev_dump, ensure_ascii=False)}"
        )
        try:
            content = llm.chat(system=ANSWER_SYSTEM, user=ans_user, max_tokens=2000)
        except Exception:
            continue

        threads.append(
            ConnectionThread(
                title=p.get("title", ""),
                hypothesis=p.get("hypothesis", ""),
                content=content,
                evidence_ids=[e.id for e in evidences],
                source=source,
                confidence=int(p.get("confidence", 0)),
            )
        )

    return {"connection_threads": threads}
