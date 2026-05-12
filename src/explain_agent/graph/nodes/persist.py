import json

from explain_agent.core.types import Evidence
from explain_agent.graph.state import AttributionState


def _evidence_to_dict(e: Evidence) -> dict:
    d = e.model_dump()
    if d.get("timestamp"):
        d["timestamp"] = d["timestamp"].isoformat()
    return d


async def persist_node(state: AttributionState, engine) -> dict:
    session_id = state["session_id"]

    tree = {
        "target": state.get("target"),
        "time_window": [state["time_window"][0].isoformat(), state["time_window"][1].isoformat()]
            if "time_window" in state else None,
        "market_facts": state.get("market_facts", {}),
        "dimension_results": {
            dim_id: {
                "mini_summary": r["mini_summary"],
                "confidence": r["confidence"],
                "no_data": r["no_data"],
                "retry_count": r["retry_count"],
                "evidence": [_evidence_to_dict(e) for e in r["evidence"]],
            }
            for dim_id, r in state.get("dimension_results", {}).items()
        },
        "subbranch_results": {
            name: {
                "mini_summary": r["mini_summary"],
                "confidence": r["confidence"],
                "no_data": r["no_data"],
                "evidence": [_evidence_to_dict(e) for e in r["evidence"]],
            }
            for name, r in state.get("subbranch_results", {}).items()
        },
        "dimension_reports": state.get("dimension_reports", {}),
        "citations": state.get("citations", []),
        "narrative": state.get("narrative", ""),
        "narrative_claims": state.get("narrative_claims", []),
        "unverified_drops": state.get("unverified_drops", []),
        "connection_threads": state.get("connection_threads", []),
        "connection_section": state.get("connection_section", ""),
    }

    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            INSERT INTO explain_agent.explain_session
              (session_id, raw_question, domain_id, target,
               time_window_start, time_window_end, status, total_cost,
               created_at, finished_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              status='completed', total_cost=VALUES(total_cost),
              finished_at=VALUES(finished_at)
            """,
            (
                session_id, state["raw_question"], state.get("domain_id"),
                state.get("target"),
                state["time_window"][0] if "time_window" in state else None,
                state["time_window"][1] if "time_window" in state else None,
                "completed", state.get("total_cost", 0.0),
                state["asked_at"], state["asked_at"],
            ),
        )
        conn.exec_driver_sql(
            """
            INSERT INTO explain_agent.explain_evidence_tree
              (session_id, tree_json, narrative, confidence, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              tree_json=VALUES(tree_json),
              narrative=VALUES(narrative),
              confidence=VALUES(confidence)
            """,
            (
                session_id,
                json.dumps(tree, ensure_ascii=False, default=str),
                state.get("narrative", ""),
                state.get("confidence", "medium"),
                state["asked_at"],
            ),
        )

    return {"session_id": session_id}
