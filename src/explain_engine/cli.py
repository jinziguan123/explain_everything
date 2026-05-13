"""Explain Engine CLI 入口。

命令：
- explain new <question> — Bootstrap + HITL 1 + 落 session
- explain show <session_id> — 显示 session 内容
- explain list — 列出所有 session

Phase 3 v0.1。
"""

import asyncio
from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table

from explain_engine.config import Settings, make_client
from explain_engine.engines.bootstrap import bootstrap_phenomena
from explain_engine.hitl.cli_interactive import review_phenomena
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.state import CognitiveState

app = typer.Typer(
    help="Cognitive Engine for explanation-centric reasoning",
    no_args_is_help=True,
)
console = Console()


def _get_store() -> SessionStore:
    settings = Settings()
    return SessionStore(directory=settings.sessions_dir)


@app.command()
def new(
    question: str = typer.Argument(..., help="为什么 X 问题"),
) -> None:
    """启动新 session：Bootstrap + HITL 1 + 落 session。"""
    asyncio.run(_run_new(question))


async def _run_new(question: str) -> None:
    settings = Settings()
    llm = make_client(settings)

    console.print(
        f"\n[INFO] 调 {settings.llm_provider} ({settings.llm_model}) 生现象..."
    )
    try:
        phenomena = await bootstrap_phenomena(question, llm)
    except Exception as exc:
        console.print(f"[red]LLM 失败: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"[INFO] 生成 {len(phenomena)} 个现象，请审查。")
    final_phenomena = review_phenomena(phenomena, console=console)

    # 构造 session
    state = CognitiveState.bootstrap(question, budget=settings.default_budget)
    for p in final_phenomena:
        state.graph.add_node(p)
    meta = SessionMeta.new(question=question)
    session = Session(meta=meta, state=state)

    store = _get_store()
    store.save(session)

    console.print(f"\n[green]Session {meta.session_id} 已保存。[/green]")
    console.print(f"       下一步：explain show {meta.session_id}")


@app.command()
def show(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
) -> None:
    """显示某个 session 的 phenomena 和 metadata。"""
    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"\n[bold]Session {session.meta.session_id}[/bold]")
    console.print(f"问题: {session.meta.question}")
    console.print(f"Stage: {session.meta.stage}")
    console.print(f"现象数: {len(session.state.graph.nodes)}\n")

    table = Table(title="Phenomena")
    table.add_column("ID", style="cyan")
    table.add_column("名称", style="bold")
    table.add_column("描述", style="dim")
    table.add_column("Level", justify="right")
    table.add_column("Epistemic")
    for nid, node in session.state.graph.nodes.items():
        table.add_row(
            nid,
            node.name,
            node.description,
            str(node.abstraction_level),
            node.epistemic,
        )
    console.print(table)


@app.command(name="list")
def list_cmd() -> None:
    """列出所有 session（按创建时间降序）。"""
    store = _get_store()
    metas = store.list()

    table = Table(title="Sessions")
    table.add_column("ID", style="cyan")
    table.add_column("问题", style="bold")
    table.add_column("Stage")
    table.add_column("Created")
    for m in metas:
        ts = datetime.fromtimestamp(m.created_at).strftime("%Y-%m-%d %H:%M")
        table.add_row(m.session_id, m.question, m.stage, ts)
    console.print(table)


if __name__ == "__main__":
    app()
