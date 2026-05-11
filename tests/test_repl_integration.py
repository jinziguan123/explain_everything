import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest

from explain_agent.cli.repl.loop import dispatch_input, ReplEnvironment
from explain_agent.cli.repl.state import ReplState


@pytest.mark.asyncio
async def test_full_session_flow(monkeypatch):
    """模拟: 启动 -> 问新问题 -> 追问 -> /sessions -> /clear -> /quit"""
    state = ReplState()
    env = ReplEnvironment(
        engine=MagicMock(),
        console=MagicMock(),
        run_main_graph=AsyncMock(return_value={"session_id": "s_new", "target": "半导体"}),
        run_followup=AsyncMock(return_value={"answer": "政策面 ...", "session_id": "s_new"}),
    )

    monkeypatch.setattr(
        "explain_agent.cli.repl.loop.load_session",
        lambda engine, sid: {"session_id": sid, "target": "半导体", "dimension_reports": {},
                             "citations": [], "narrative": "", "narrative_claims": [],
                             "market_facts": {}, "time_window": []},
    )
    await dispatch_input(state, env, "为什么半导体涨")
    env.run_main_graph.assert_called_once()
    assert state.current_session_id == "s_new"

    await dispatch_input(state, env, "政策面具体是什么")
    env.run_followup.assert_called_once()
    assert len(state.followup_history) == 1

    monkeypatch.setattr(
        "explain_agent.cli.repl.commands.list_recent_sessions",
        lambda engine, limit=10: [{
            "session_id": "s_new", "target": "半导体",
            "created_at": datetime(2026, 5, 11, 19, 10),
            "confidence": "medium", "dim_count": 6, "followup_count": 1,
        }],
    )
    await dispatch_input(state, env, "/sessions")

    await dispatch_input(state, env, "/clear")
    assert state.current_session_id == "s_new"
    assert state.followup_history == []

    from explain_agent.cli.repl.commands import ReplExit
    with pytest.raises(ReplExit):
        await dispatch_input(state, env, "/quit")
