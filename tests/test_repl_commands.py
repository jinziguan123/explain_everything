import io
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from explain_agent.cli.repl.commands import (
    parse_slash_command, SlashCommand, SlashCommandError,
)


def test_parse_new_with_question():
    cmd = parse_slash_command("/new 为什么半导体涨")
    assert cmd.name == "new"
    assert cmd.arg == "为什么半导体涨"


def test_parse_load_with_session_id():
    cmd = parse_slash_command("/load s_abc123")
    assert cmd.name == "load"
    assert cmd.arg == "s_abc123"


def test_parse_sessions_no_arg():
    cmd = parse_slash_command("/sessions")
    assert cmd.name == "sessions"
    assert cmd.arg == ""


def test_parse_clear_help_quit_exit():
    assert parse_slash_command("/clear").name == "clear"
    assert parse_slash_command("/help").name == "help"
    assert parse_slash_command("/quit").name == "quit"
    assert parse_slash_command("/exit").name == "quit"


def test_parse_unknown_command_raises():
    with pytest.raises(SlashCommandError):
        parse_slash_command("/foobar")


def test_parse_trims_whitespace():
    cmd = parse_slash_command("  /new   半导体  ")
    assert cmd.name == "new"
    assert cmd.arg == "半导体"


def test_handle_sessions_prints_table_when_some_exist(monkeypatch):
    from explain_agent.cli.repl.commands import handle_sessions

    fake_sessions = [
        {
            "session_id": "s_abc", "target": "半导体",
            "created_at": datetime(2026, 5, 11, 19, 10),
            "confidence": "low", "dim_count": 6, "followup_count": 0,
        },
    ]
    monkeypatch.setattr(
        "explain_agent.cli.repl.commands.list_recent_sessions",
        lambda engine, limit=10: fake_sessions,
    )
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    handle_sessions(engine=MagicMock(), console=console)
    out = buf.getvalue()
    assert "s_abc" in out
    assert "半导体" in out
    assert "low" in out


def test_handle_sessions_prints_empty_message_when_none(monkeypatch):
    from explain_agent.cli.repl.commands import handle_sessions
    monkeypatch.setattr(
        "explain_agent.cli.repl.commands.list_recent_sessions",
        lambda engine, limit=10: [],
    )
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    handle_sessions(engine=MagicMock(), console=console)
    out = buf.getvalue()
    assert "无历史 session" in out or "no session" in out.lower()
