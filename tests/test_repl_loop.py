import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
import pytest

from explain_agent.cli.repl.loop import dispatch_input, ReplEnvironment
from explain_agent.cli.repl.state import ReplState


@pytest.mark.asyncio
async def test_dispatch_slash_command_handled_locally(monkeypatch):
    state = ReplState()
    env = MagicMock()
    env.engine = MagicMock()
    env.console = MagicMock()
    handle_called = []

    def fake_handle_sessions(engine, console, limit=10):
        handle_called.append(("sessions", engine))

    monkeypatch.setattr(
        "explain_agent.cli.repl.loop.handle_sessions", fake_handle_sessions
    )
    await dispatch_input(state, env, "/sessions")
    assert handle_called and handle_called[0][0] == "sessions"


@pytest.mark.asyncio
async def test_dispatch_first_message_auto_new(monkeypatch):
    state = ReplState()
    env = MagicMock()
    env.console = MagicMock()
    env.run_main_graph = AsyncMock(
        return_value={"session_id": "s_new", "target": "半导体"}
    )
    monkeypatch.setattr(
        "explain_agent.cli.repl.loop.load_session",
        lambda engine, sid: {"session_id": sid, "target": "半导体",
                             "dimension_reports": {}, "citations": [],
                             "narrative": "", "narrative_claims": [],
                             "market_facts": {}, "time_window": []},
    )
    await dispatch_input(state, env, "为什么半导体涨")
    env.run_main_graph.assert_called_once_with("为什么半导体涨")
    assert state.current_session_id == "s_new"


@pytest.mark.asyncio
async def test_dispatch_with_session_goes_followup(monkeypatch):
    state = ReplState()
    state.current_session_id = "s_abc"
    state.current_session = {"target": "半导体", "session_id": "s_abc",
                             "dimension_reports": {}, "citations": [],
                             "narrative": "", "narrative_claims": [],
                             "market_facts": {}, "time_window": []}
    env = MagicMock()
    env.console = MagicMock()
    env.run_followup = AsyncMock(return_value={"answer": "...", "session_id": "s_abc"})
    await dispatch_input(state, env, "政策是什么")
    env.run_followup.assert_called_once()
    assert len(state.followup_history) == 1
    assert state.followup_history[0]["question"] == "政策是什么"


@pytest.mark.asyncio
async def test_dispatch_new_explicit_with_question(monkeypatch):
    state = ReplState()
    state.current_session_id = "s_old"
    env = MagicMock()
    env.console = MagicMock()
    env.run_main_graph = AsyncMock(return_value={"session_id": "s_new2", "target": "光伏"})
    monkeypatch.setattr(
        "explain_agent.cli.repl.loop.load_session",
        lambda engine, sid: {"session_id": sid, "target": "光伏",
                             "dimension_reports": {}, "citations": [],
                             "narrative": "", "narrative_claims": [],
                             "market_facts": {}, "time_window": []},
    )
    await dispatch_input(state, env, "/new 光伏怎么了")
    env.run_main_graph.assert_called_once_with("光伏怎么了")
    assert state.current_session_id == "s_new2"
    assert state.followup_history == []
