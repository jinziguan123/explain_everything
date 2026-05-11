from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from explain_agent.cli.repl.state import list_recent_sessions


class SlashCommandError(ValueError):
    pass


@dataclass
class SlashCommand:
    name: str
    arg: str


_ALIASES = {"exit": "quit"}
_KNOWN = {"new", "sessions", "load", "clear", "help", "quit"}


def parse_slash_command(raw: str) -> SlashCommand:
    line = raw.strip()
    if not line.startswith("/"):
        raise SlashCommandError(f"not a slash command: {raw}")
    body = line[1:].strip()
    if not body:
        raise SlashCommandError("empty slash command")
    parts = body.split(maxsplit=1)
    name = parts[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    name = _ALIASES.get(name, name)
    if name not in _KNOWN:
        raise SlashCommandError(f"unknown command: /{name}")
    return SlashCommand(name=name, arg=arg)


def handle_sessions(engine, console: Console, limit: int = 10) -> None:
    sessions = list_recent_sessions(engine, limit=limit)
    if not sessions:
        console.print("[dim]无历史 session。输入问题即可开始新会话。[/dim]")
        return
    table = Table(title="最近 session", show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("session_id")
    table.add_column("时间")
    table.add_column("target")
    table.add_column("confidence")
    table.add_column("追问")
    for i, s in enumerate(sessions, 1):
        table.add_row(
            str(i),
            s["session_id"],
            s["created_at"].strftime("%Y-%m-%d %H:%M"),
            s["target"] or "-",
            s["confidence"] or "-",
            str(s["followup_count"]),
        )
    console.print(table)
