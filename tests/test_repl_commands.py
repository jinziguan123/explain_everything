import pytest
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
