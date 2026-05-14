"""Explain Engine CLI 入口。

命令：
- explain new <question> — Bootstrap + HITL 1 + 落 session
- explain show <session_id> — 显示 session 内容
- explain list — 列出所有 session
- explain compress <session_id> — Compression + Evaluation + HITL 2

Phase 3 v0.1 / Phase 4。
"""

import asyncio
from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table

from explain_engine.config import Settings, make_llm_client
from explain_engine.engines.bootstrap import bootstrap_phenomena
from explain_engine.engines.compression import propose_candidates
from explain_engine.engines.evaluation import score_all
from explain_engine.hitl.cli_interactive import review_insights, review_phenomena
from explain_engine.llm.errors import LLMError, SchemaValidationError
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
    llm = make_llm_client()

    import os
    proto = os.environ.get("LLM_PROTOCOL", "?")
    model = os.environ.get("LLM_MODEL", "?")
    console.print(f"\n[INFO] 调 LLM ({proto} / {model}) 生现象...")
    try:
        phenomena = await bootstrap_phenomena(question, llm)
    except SchemaValidationError as exc:
        console.print(f"[red]LLM 输出不合规: {exc}[/red]")
        raise typer.Exit(2) from exc
    except LLMError as exc:
        console.print(f"[red]LLM 调用失败: {exc}[/red]")
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
    try:
        store.save(session)
    except OSError as exc:
        console.print(f"[red]session 保存失败: {exc}[/red]")
        raise typer.Exit(3) from exc

    console.print(f"\n[green]Session {meta.session_id} 已保存。[/green]")
    console.print(f"       下一步：explain show {meta.session_id}")


@app.command()
def show(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
    trace: bool = typer.Option(False, "--trace", help="渲染 reasoning_trace 表 (Phase 5)"),
) -> None:
    """显示某个 session 的 phenomena 和 metadata。

    `--trace` 额外渲染 reasoning_trace (Phase 5 reasoning loop 每 tick 记录)。
    """
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

    if trace:
        if not session.state.reasoning_trace:
            console.print("\n[dim](reasoning_trace 为空)[/dim]")
        else:
            t = Table(title="Reasoning Trace")
            t.add_column("tick", justify="right")
            t.add_column("action")
            t.add_column("target")
            t.add_column("gain", justify="right")
            t.add_column("llm calls", justify="right")
            t.add_column("timestamp", style="dim")
            for e in session.state.reasoning_trace:
                t.add_row(
                    str(e.tick), e.action, e.target_node_id or "-",
                    f"{e.gain_delta:.2f}", str(e.llm_calls), e.timestamp,
                )
            console.print(t)


@app.command()
def compress(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
) -> None:
    """对已 bootstrap 的 session 跑 Compression + Evaluation + HITL 2。"""
    asyncio.run(_run_compress(session_id))


async def _run_compress(session_id: str) -> None:
    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    stage = session.meta.stage
    if stage == "done":
        console.print(f"[red]session {session_id} 已 done，不可重跑。[/red]")
        raise typer.Exit(4)
    if stage not in ("bootstrap_pending", "insight_pending"):
        console.print(
            f"[red]session {session_id} stage={stage}，不支持 compress。[/red]"
        )
        raise typer.Exit(4)

    llm = make_llm_client()

    if stage == "bootstrap_pending":
        console.print("[INFO] 调 LLM 生成 abstract 候选...")
        try:
            await propose_candidates(session.state, llm)
        except SchemaValidationError as exc:
            console.print(f"[red]LLM 输出不合规: {exc}[/red]")
            raise typer.Exit(2) from exc
        except LLMError as exc:
            console.print(f"[red]LLM 调用失败: {exc}[/red]")
            raise typer.Exit(1) from exc

        console.print(
            f"[INFO] 生成 {len(session.state.insight_candidates)} 个候选，开始评分..."
        )
        try:
            await score_all(session.state, llm)
        except SchemaValidationError as exc:
            console.print(f"[red]评分输出不合规: {exc}[/red]")
            raise typer.Exit(2) from exc
        except LLMError as exc:
            console.print(f"[red]评分 LLM 调用失败: {exc}[/red]")
            raise typer.Exit(1) from exc

        session.meta.stage = "insight_pending"
        try:
            store.save(session)
            console.print("[INFO] 中间状态已保存 (stage=insight_pending)。")
        except OSError as exc:
            console.print(
                f"[red]中间状态保存失败（LLM 调用已消耗，重跑会再次计费）: {exc}[/red]"
            )
            raise typer.Exit(3) from exc
    else:  # stage == "insight_pending"
        console.print("[INFO] 检测到 stage=insight_pending，跳过 LLM 直接进入审查。")
        # Phase 5: gain 已持久化进 state.last_gains，重入也能正确显示

    # HITL 2 — Phase 5 起从 state.last_gains 读
    review_insights(session.state, console=console)

    session.meta.stage = "done"
    try:
        store.save(session)
    except OSError as exc:
        console.print(f"[red]保存失败: {exc}[/red]")
        raise typer.Exit(3) from exc

    console.print(f"\n[green]Session {session_id} 已完成。[/green]")


@app.command()
def run(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
    budget: int = typer.Option(15, "--budget", help="reasoning loop tick 上限"),
) -> None:
    """Phase 5 reasoning loop: 上溯 driver,自动收敛。

    session 必须 stage=done (Phase 4 HITL 2 完成后)。
    跑完 stage 变 converged。
    """
    from explain_engine.runtime.runtime import run as runtime_run

    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if session.meta.stage != "done":
        console.print(
            f"[red]session stage={session.meta.stage!r}, must be 'done' to run "
            f"(先跑 explain compress)。[/red]"
        )
        raise typer.Exit(4)

    llm = make_llm_client()

    def on_tick(_state: CognitiveState) -> None:
        store.save(session)

    try:
        reason = asyncio.run(runtime_run(session.state, llm, budget=budget, on_tick=on_tick))
    except (LLMError, SchemaValidationError) as exc:
        console.print(f"[red]runtime failed: {exc}[/red]")
        raise typer.Exit(1) from exc

    session.meta.stage = "converged"
    store.save(session)

    drivers = [nid for nid, n in session.state.graph.nodes.items() if n.abstraction_level == 2]
    console.print(
        f"\n[green]Phase 5 run complete[/green] "
        f"(reason={reason}, tick={session.state.tick})"
    )
    console.print(
        f"graph: {len(session.state.graph.nodes)} nodes / "
        f"{len(session.state.graph.edges)} edges"
    )
    console.print(f"driver layer: {len(drivers)} drivers added")


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
