from dataclasses import dataclass
from uuid import uuid4

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
_KNOWN = {"new", "sessions", "load", "clear", "help", "quit", "annotate", "stats"}


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


_LABEL_MAP = {"g": "green", "y": "yellow", "r": "red"}


def handle_annotate(engine, console: Console, state: ReplState, prompt_fn=input) -> None:
    if state.current_session is None:
        console.print("[red]当前没有 active session, 用 /load <id> 或先跑一个新问题[/red]")
        return
    threads = state.current_session.get("connection_threads") or []
    if not threads:
        console.print("[yellow]当前 session 无 connection_threads, 无需标注[/yellow]")
        return

    session_id = state.current_session_id
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT thread_index FROM explain_agent.explain_annotation WHERE session_id = %s",
            (session_id,),
        ).fetchall()
        annotated_indices = {r[0] for r in rows}

    console.print(f"\n▎ 当前 session: [bold]{session_id}[/bold]")
    console.print(f"▎ Connection threads ({len(threads)} 条):\n")

    counts = {"green": 0, "yellow": 0, "red": 0}

    for idx, t in enumerate(threads):
        if idx in annotated_indices:
            console.print(f"[{idx+1}/{len(threads)}] {t.get('title', '')}  [dim](已标注, 跳过)[/dim]")
            continue
        console.print(f"\n[{idx+1}/{len(threads)}] [cyan]{t.get('title', '')}[/cyan]")
        console.print(f"      source={t.get('source')}, confidence={t.get('confidence')}")
        console.print(f"      内容: {(t.get('content') or '')[:200]}")
        label_raw = prompt_fn("      标签? (g=🟢真知灼见 / y=🟡合理但平庸 / r=🔴漂移 / s=skip): ").strip().lower()
        if label_raw == "s" or label_raw == "":
            console.print("      [dim]skip[/dim]")
            continue
        label = _LABEL_MAP.get(label_raw)
        if label is None:
            console.print(f"      [red]非法标签 {label_raw!r}, skip[/red]")
            continue
        note = prompt_fn("      备注 (回车跳过): ").strip() or None

        annotation_id = f"ann_{uuid4().hex[:16]}"
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    """
                    INSERT INTO explain_agent.explain_annotation
                      (annotation_id, session_id, thread_index, thread_title, label, note)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (annotation_id, session_id, idx, t.get("title", ""), label, note),
                )
            counts[label] += 1
            console.print("      [green]✓ 已标记[/green]")
        except Exception as e:
            console.print(f"      [red]标注落库失败: {e!r}[/red]")

    summary = f"完成。session {session_id}: {counts['green']} 🟢 / {counts['yellow']} 🟡 / {counts['red']} 🔴"
    console.print(f"\n{summary}")
