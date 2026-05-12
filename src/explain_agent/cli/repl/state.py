import json
from dataclasses import dataclass, field

from sqlalchemy import text


@dataclass
class ReplState:
    current_session_id: str | None = None
    current_session: dict | None = None
    followup_history: list[dict] = field(default_factory=list)


def list_recent_sessions(engine, limit: int = 5) -> list[dict]:
    sql = """
    SELECT s.session_id, s.target, s.created_at, t.confidence,
           (CASE WHEN t.tree_json IS NULL THEN 0 ELSE 6 END) AS dim_count,
           (SELECT COUNT(*) FROM explain_agent.explain_followup_history f
             WHERE f.session_id = s.session_id) AS followup_count
    FROM explain_agent.explain_session s
    LEFT JOIN explain_agent.explain_evidence_tree t USING (session_id)
    ORDER BY s.created_at DESC
    LIMIT :limit
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"limit": limit}).fetchall()
    return [
        {
            "session_id": r[0],
            "target": r[1],
            "created_at": r[2],
            "confidence": r[3],
            "dim_count": r[4],
            "followup_count": r[5],
        }
        for r in rows
    ]


def load_session(engine, session_id: str) -> dict | None:
    sql = """
    SELECT s.session_id, s.raw_question, s.domain_id, s.target,
           t.confidence, t.tree_json
    FROM explain_agent.explain_session s
    LEFT JOIN explain_agent.explain_evidence_tree t USING (session_id)
    WHERE s.session_id = :sid
    """
    with engine.connect() as conn:
        row = conn.execute(text(sql), {"sid": session_id}).fetchone()
    if row is None:
        return None
    tree = json.loads(row[5]) if row[5] else {}
    return {
        "session_id": row[0],
        "raw_question": row[1],
        "domain_id": row[2],
        "target": row[3] or tree.get("target"),
        "confidence": row[4],
        "time_window": tree.get("time_window"),
        "narrative": tree.get("narrative", ""),
        "narrative_claims": tree.get("narrative_claims", []),
        "dimension_reports": tree.get("dimension_reports", {}),
        "citations": tree.get("citations", []),
        "market_facts": tree.get("market_facts", {}),
        "connection_threads": tree.get("connection_threads", []),
        "connection_section": tree.get("connection_section", ""),
    }
