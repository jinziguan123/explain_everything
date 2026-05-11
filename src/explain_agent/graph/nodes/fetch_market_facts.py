from explain_agent.core.types import AdapterQuery
from explain_agent.graph.state import AttributionState


async def fetch_market_facts_node(
    state: AttributionState,
    market_adapter,
) -> dict:
    q = AdapterQuery(
        keywords=[],
        time_window=state["time_window"],
        target=state["target"],
    )
    evidences = await market_adapter.query(q)
    if not evidences:
        return {
            "market_facts": {
                "target": state["target"],
                "time_window": list(state["time_window"]),
                "snippet": "",
                "raw_payload": None,
            }
        }
    e = evidences[0]
    return {
        "market_facts": {
            "target": state["target"],
            "time_window": list(state["time_window"]),
            "snippet": e.snippet,
            "raw_payload": e.raw_payload,
        }
    }
