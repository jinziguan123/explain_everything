from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from explain_agent.cli.repl.state import list_recent_sessions, load_session, ReplState


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


def handle_load(engine, console: Console, state: ReplState, session_id: str) -> None:
    if not session_id:
        console.print("[red]/load 需要 session_id 参数[/red]")
        return
    session = load_session(engine, session_id)
    if session is None:
        console.print(f"[red]找不到 session: {session_id}[/red]")
        return
    state.current_session_id = session_id
    state.current_session = session
    state.followup_history = []
    console.print(
        f"[green]✓[/green] 切到 session [bold]{session_id}[/bold] "
        f"(target=[cyan]{session.get('target')}[/cyan])"
    )


class ReplExit(SystemExit):
    """正常退出 REPL 的哨兵异常"""


def handle_clear(console: Console, state: ReplState) -> None:
    state.followup_history = []
    console.print("[dim]✓ 已清空追问历史（当前 session 保留）[/dim]")


def handle_help(console: Console) -> None:
    console.print(
        """[bold]命令列表:[/bold]
  [cyan]/new[/cyan] [问题]       开启新 session, 走完整 6 维归因
  [cyan]/sessions[/cyan]         列最近 10 个 session
  [cyan]/load[/cyan] [session_id] 切换到指定 session 继续追问
  [cyan]/clear[/cyan]            清空当前 session 的追问历史
  [cyan]/help[/cyan]             显示此帮助
  [cyan]/quit[/cyan] / [cyan]/exit[/cyan]       退出 REPL (或 Ctrl+D)

直接输入文字, 默认作为当前 session 的追问;
当前无 session 时, 自动作为新问题 (/new)"""
    )


def handle_quit(console: Console) -> None:
    console.print("[dim]bye.[/dim]")
    raise ReplExit(0)
