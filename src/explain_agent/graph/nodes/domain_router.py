import re

from explain_agent.graph.state import AttributionState
from explain_agent.graph.framework_loader import load_framework


_KNOWN_DOMAINS = ["cn_equity_sector_attribution"]


async def domain_router_node(state: AttributionState) -> dict:
    raw = state["raw_question"]
    for domain_id in _KNOWN_DOMAINS:
        fw = load_framework(domain_id)
        for pattern in fw.get("match_patterns", []):
            if re.search(pattern, raw):
                return {"domain_id": domain_id}
    return {"domain_id": _KNOWN_DOMAINS[0]}
