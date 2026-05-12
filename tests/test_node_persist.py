from datetime import date
from unittest.mock import MagicMock
import pytest
from explain_agent.graph.state import new_attribution_state
from explain_agent.graph.nodes.persist import persist_node


@pytest.mark.asyncio
async def test_persist_writes_session_and_tree():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    state = new_attribution_state("为什么半导体涨", session_id="s_test_123")
    state["domain_id"] = "cn_equity_sector_attribution"
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["narrative"] = "半导体涨停因 ..."
    state["dimension_reports"] = {"policy": "...", "industry_chain": "..."}
    state["citations"] = []
    state["confidence"] = "high"
    state["total_cost"] = 1.5

    out = await persist_node(state, engine=mock_engine)
    assert out["session_id"] == "s_test_123"
    assert mock_conn.exec_driver_sql.call_count >= 2


@pytest.mark.asyncio
async def test_persist_writes_phase2b_fields():
    """tree_json 必须包含 narrative_claims 与 unverified_drops（Phase 2.B 引入）。"""
    import json as _json
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    state = new_attribution_state("test", session_id="s_2b")
    state["domain_id"] = "cn_equity_sector_attribution"
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["narrative"] = "测试叙事"
    state["narrative_claims"] = [
        {"text": "claim 1", "evidence_ids": ["e1"]},
    ]
    state["unverified_drops"] = ["被删除的句子"]
    state["dimension_reports"] = {}
    state["citations"] = []
    state["confidence"] = "medium"
    state["total_cost"] = 0.0

    await persist_node(state, engine=mock_engine)

    tree_json_arg = None
    for call in mock_conn.exec_driver_sql.call_args_list:
        sql = call.args[0]
        if "explain_evidence_tree" in sql:
            tree_json_arg = call.args[1][1]
            break
    assert tree_json_arg is not None
    tree = _json.loads(tree_json_arg)
    assert "narrative_claims" in tree
    assert "unverified_drops" in tree
    assert tree["narrative_claims"][0]["evidence_ids"] == ["e1"]
    assert tree["unverified_drops"] == ["被删除的句子"]


@pytest.mark.asyncio
async def test_persist_writes_connection_threads():
    """tree_json 必须包含 connection_threads 与 connection_section。"""
    import json as _json
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    state = new_attribution_state("test", session_id="s_2d1")
    state["domain_id"] = "cn_equity_sector_attribution"
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["narrative"] = "..."
    state["narrative_claims"] = []
    state["unverified_drops"] = []
    state["dimension_reports"] = {}
    state["citations"] = []
    state["confidence"] = "medium"
    state["total_cost"] = 0.0
    state["connection_threads"] = [
        {"title": "T1", "hypothesis": "h", "content": "c",
         "evidence_ids": ["e1"], "source": "local", "confidence": 4},
    ]
    state["connection_section"] = "## 延伸思考\n\n▎ T1\nc"

    await persist_node(state, engine=mock_engine)

    tree_json_arg = None
    for call in mock_conn.exec_driver_sql.call_args_list:
        sql = call.args[0]
        if "explain_evidence_tree" in sql:
            tree_json_arg = call.args[1][1]
            break
    assert tree_json_arg is not None
    tree = _json.loads(tree_json_arg)
    assert "connection_threads" in tree
    assert tree["connection_threads"][0]["title"] == "T1"
    assert "connection_section" in tree
    assert "T1" in tree["connection_section"]
