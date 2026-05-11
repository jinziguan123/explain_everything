import json
from datetime import datetime
from unittest.mock import MagicMock

from explain_agent.cli.repl.state import (
    ReplState, list_recent_sessions, load_session,
)


def test_repl_state_defaults():
    s = ReplState()
    assert s.current_session_id is None
    assert s.current_session is None
    assert s.followup_history == []


def test_list_recent_sessions_returns_summaries():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.fetchall.return_value = [
        ("s_abc", "半导体", datetime(2026, 5, 11, 19, 10), "low", 6, 0),
        ("s_def", "光伏",   datetime(2026, 5, 10, 11, 0),  "high", 6, 5),
    ]
    out = list_recent_sessions(mock_engine, limit=5)
    assert len(out) == 2
    assert out[0]["session_id"] == "s_abc"
    assert out[0]["target"] == "半导体"
    assert out[0]["confidence"] == "low"
    assert out[0]["followup_count"] == 0


def test_load_session_assembles_full_state():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    tree = {
        "target": "半导体",
        "time_window": ["2026-05-06", "2026-05-11"],
        "narrative": "测试叙事",
        "narrative_claims": [{"text": "claim1", "evidence_ids": ["e1"]}],
        "dimension_reports": {"policy": "政策报告"},
        "citations": [{"evidence_id": "e1", "url": "http://a", "source_type": "news"}],
    }
    mock_conn.execute.return_value.fetchone.return_value = (
        "s_abc", "为什么半导体涨", "cn_equity_sector_attribution",
        "半导体", "low", json.dumps(tree, ensure_ascii=False),
    )
    s = load_session(mock_engine, "s_abc")
    assert s["session_id"] == "s_abc"
    assert s["target"] == "半导体"
    assert s["narrative"] == "测试叙事"
    assert s["dimension_reports"]["policy"] == "政策报告"


def test_load_session_returns_none_when_missing():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.fetchone.return_value = None
    s = load_session(mock_engine, "s_nope")
    assert s is None
