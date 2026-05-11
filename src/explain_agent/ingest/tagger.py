import json
import re

from explain_agent.llm import LLMClient, get_weak_llm


SYSTEM_PROMPT = """你是金融新闻分类标注器。读完新闻后，**只输出 JSON**（不要任何解释/前后缀/markdown）：
{
  "industries": ["..."],
  "concepts": ["..."],
  "policy_type": "..."|null,
  "event_type": "..."|null
}

字段说明：
- industries: 申万一级行业名（如"电子"、"医药生物"），无相关填 []
- concepts: 主题/概念（如"国产替代"、"HBM"、"减肥药"），无相关填 []
- policy_type: 货币/财政/产业/监管/外交，无相关填 null
- event_type: 产业链/事件/数据/公司公告/海外/无关
"""


_EMPTY = {"industries": [], "concepts": [], "policy_type": None, "event_type": None}


def _extract_json(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    return json.loads(text)


class NewsTagger:
    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or get_weak_llm()

    def tag(self, title: str, content: str) -> dict:
        user = f"标题: {title}\n正文: {content[:1500]}"
        try:
            raw = self.llm.chat(system=SYSTEM_PROMPT, user=user, max_tokens=2000)
            data = _extract_json(raw.strip())
        except Exception:
            return dict(_EMPTY)
        return {
            "industries": data.get("industries", []) or [],
            "concepts": data.get("concepts", []) or [],
            "policy_type": data.get("policy_type"),
            "event_type": data.get("event_type"),
        }
