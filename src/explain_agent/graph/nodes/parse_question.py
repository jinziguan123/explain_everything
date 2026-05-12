import json
import re
from datetime import date, timedelta

from explain_agent.graph.state import AttributionState
from explain_agent.llm import LLMClient, get_weak_llm


SYSTEM = """你是金融归因 agent 的问题解析器。读完用户输入，输出 JSON：
{
  "target": "标的（板块/行业/主题名）",
  "time_window_start": "YYYY-MM-DD",
  "time_window_end": "YYYY-MM-DD",
  "intent": "up|down|volatile|general"
}

规则：
- target 提取板块/主题词（如"半导体"、"光伏"、"白酒"），无明确则用整句话
- 时间窗：默认 end=今天，start=今天-7天；若用户提"上周"则推 7-14 天前
- "今天"语义：end=今天，start=今天-5天（覆盖最近 5 个自然日，包含上一个交易日），避免单日窗口导致行情库查空
- 若用户给出明确日期则严格按用户给定
- intent：涨/涨停=up，跌/大跌=down，波动/异动=volatile，其它=general
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


async def parse_question_node(
    state: AttributionState,
    llm: LLMClient | None = None,
) -> dict:
    llm = llm or get_weak_llm()
    today = state["asked_at"].date()
    user = f"今天: {today}\n用户输入: {state['raw_question']}"

    raw = await llm.achat(system=SYSTEM, user=user, max_tokens=2000)
    data = _extract_json(raw)
    if data is None:
        return {
            "target": state["raw_question"][:50],
            "time_window": (today - timedelta(days=7), today),
            "intent": "general",
        }

    try:
        start = date.fromisoformat(data["time_window_start"])
        end = date.fromisoformat(data["time_window_end"])
    except (KeyError, ValueError):
        start, end = today - timedelta(days=7), today

    return {
        "target": data.get("target", state["raw_question"][:50]),
        "time_window": (start, end),
        "intent": data.get("intent", "general"),
    }
