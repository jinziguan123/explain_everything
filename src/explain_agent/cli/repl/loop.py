import asyncio
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from explain_agent.cli.repl.commands import (
    parse_slash_command, SlashCommand, SlashCommandError, ReplExit,
    handle_sessions, handle_load, handle_clear, handle_help, handle_quit,
)
from explain_agent.cli.repl.state import ReplState, load_session


@dataclass
class ReplEnvironment:
    engine: Any
    console: Console
    run_main_graph: Any
    run_followup: Any


async def dispatch_input(state: ReplState, env: ReplEnvironment, raw: str) -> None:
    line = raw.strip()
    if not line:
        return

    if line.startswith("/"):
        try:
            cmd = parse_slash_command(line)
        except SlashCommandError as e:
            env.console.print(f"[red]{e}[/red] (/help 查看可用命令)")
            return
        await _run_slash(state, env, cmd)
        return

    if state.current_session_id is None:
        await _run_main(state, env, line)
        return

    await _run_followup(state, env, line)


async def _run_slash(state: ReplState, env: ReplEnvironment, cmd: SlashCommand) -> None:
    if cmd.name == "new":
        if not cmd.arg:
            env.console.print("[red]/new 需要带问题（例：/new 为什么半导体涨）[/red]")
            return
        state.current_session_id = None
        state.current_session = None
        state.followup_history = []
        await _run_main(state, env, cmd.arg)
    elif cmd.name == "sessions":
        handle_sessions(engine=env.engine, console=env.console)
    elif cmd.name == "load":
        handle_load(engine=env.engine, console=env.console, state=state, session_id=cmd.arg)
    elif cmd.name == "clear":
        handle_clear(console=env.console, state=state)
    elif cmd.name == "help":
        handle_help(console=env.console)
    elif cmd.name == "quit":
        handle_quit(console=env.console)
    elif cmd.name == "annotate":
        from explain_agent.cli.repl.commands import handle_annotate
        handle_annotate(engine=env.engine, console=env.console, state=state)
    elif cmd.name == "stats":
        from explain_agent.cli.repl.commands import handle_stats
        handle_stats(engine=env.engine, console=env.console)


async def _run_main(state: ReplState, env: ReplEnvironment, question: str) -> None:
    result = await env.run_main_graph(question)
    if not result:
        env.console.print("[red]main graph 未返回有效结果[/red]")
        return
    state.current_session_id = result.get("session_id")
    state.current_session = load_session(env.engine, state.current_session_id)
    state.followup_history = []

    env.console.print(f"\n[bold yellow]Narrative:[/bold yellow]\n{result.get('narrative', '')}")
    env.console.print(f"\n[bold]Confidence:[/bold] {result.get('confidence')}")
    dim_reports = result.get("dimension_reports", {})
    if dim_reports:
        env.console.print("\n[bold yellow]Dimension Reports:[/bold yellow]")
        for dim_id, report in dim_reports.items():
            env.console.print(f"\n[bold]▎ {dim_id}[/bold]")
            env.console.print(report[:600])
    if result.get("connection_section"):
        env.console.print(f"\n[bold magenta]{result['connection_section']}[/bold magenta]")


async def _run_followup(state: ReplState, env: ReplEnvironment, question: str) -> None:
    out = await env.run_followup(
        session=state.current_session,
        history=state.followup_history,
        question=question,
    )
    env.console.print(f"\n{out['answer']}\n")
    state.followup_history.append({"question": question, "answer": out["answer"]})


def _build_environment() -> ReplEnvironment:
    """实际构建生产环境的 env（adapters/llm/engines/main_graph）。"""
    from explain_agent.adapters.clickhouse_market import ClickHouseMarketAdapter, IndustryResolver
    from explain_agent.adapters.mysql_fundamentals import MySQLFundamentalsAdapter
    from explain_agent.adapters.akshare_capital_flow import AkshareCapitalFlowAdapter
    from explain_agent.adapters.news_corpus import NewsCorpusAdapter
    from explain_agent.adapters.web_search import WebSearchAdapter
    from explain_agent.adapters.research_corpus import ResearchCorpusAdapter
    from explain_agent.config import get_settings
    from explain_agent.db.clickhouse import get_client as ch_client
    from explain_agent.db.mysql import get_engine
    from explain_agent.db.qdrant import get_qdrant_client
    from explain_agent.embedding.bge_m3 import get_embedder
    from explain_agent.graph.dimension_worker import DimensionWorker
    from explain_agent.graph.main_graph import build_main_graph
    from explain_agent.graph.state import new_attribution_state
    from explain_agent.graph.followup import run_followup
    from explain_agent.ingest.news_crawler import AkshareNewsCrawler
    from explain_agent.ingest.news_indexer import NewsIndexer
    from explain_agent.ingest.tagger import NewsTagger
    from explain_agent.llm import get_strong_llm, get_weak_llm
    from explain_agent.storage.snapshot import SnapshotStore

    console = Console()
    console.print("[dim]初始化 DB / embedder ...[/dim]")
    quant_engine = get_engine("quant")
    explain_engine = get_engine("explain")
    resolver = IndustryResolver(quant_engine)
    embedder = get_embedder()
    embedder.embed(["warm up"])
    registry = {
        "clickhouse_market": ClickHouseMarketAdapter(ch_client(), resolver),
        "mysql_fundamentals": MySQLFundamentalsAdapter(quant_engine, resolver),
        "akshare_capital_flow": AkshareCapitalFlowAdapter(),
        "news_corpus": NewsCorpusAdapter(
            qdrant=get_qdrant_client(), embedder=embedder, engine=explain_engine,
        ),
    }
    registry["research_corpus"] = ResearchCorpusAdapter(
        qdrant=get_qdrant_client(), embedder=embedder, engine=explain_engine,
    )

    settings = get_settings()
    snapshot_store = SnapshotStore(base_dir=settings.snapshot_dir, engine=explain_engine)
    news_crawler = AkshareNewsCrawler()
    news_indexer = NewsIndexer(
        engine=explain_engine,
        tagger=NewsTagger(),
        embedder=embedder,
        qdrant=get_qdrant_client(),
        snapshot_store=snapshot_store,
    )
    if settings.tavily_api_key:
        from tavily import TavilyClient
        tavily = TavilyClient(api_key=settings.tavily_api_key)
        registry["web_search"] = WebSearchAdapter(tavily, snapshot_store)
        console.print("[dim]✓ web_search adapter (Tavily) 已注入[/dim]")
    else:
        console.print("[yellow]⚠ TAVILY_API_KEY 未设置，web_search 链路被跳过[/yellow]")

    weak = get_weak_llm()
    strong = get_strong_llm()

    def on_dim_done(dim_id, duration, no_data, retry_count, evidence_count):
        flag = "[red]no_data[/red]" if no_data else f"[green]ok[/green] ev={evidence_count}"
        console.print(
            f"  └─ {flag} [bold]{dim_id}[/bold] "
            f"[dim]rounds={retry_count} ({duration:.1f}s)[/dim]"
        )

    def on_dim_round(dim_id, round_idx, max_rounds, keywords, new_count,
                     total_count, duration, reason):
        kw_preview = ",".join(keywords) if keywords else "-"
        console.print(
            f"    [dim cyan]·[/dim cyan] [bold]{dim_id}[/bold] "
            f"[dim]round {round_idx}/{max_rounds}[/dim] kw=[{kw_preview}] "
            f"new={new_count} total={total_count} [dim]({duration:.1f}s, {reason})[/dim]"
        )

    def on_node_event(event, name, *args):
        if event == "start":
            console.print(f"[dim]▶[/dim] [bold]{name}[/bold] ...")
        elif event == "end":
            dur = args[0]
            console.print(f"[green]✓[/green] [bold]{name}[/bold] [dim]({dur:.1f}s)[/dim]")
        elif event == "error":
            dur, err = args
            console.print(f"[red]✗[/red] [bold]{name}[/bold] [dim]({dur:.1f}s)[/dim] {err}")

    def worker_factory(dimension_config, worker_config):
        return DimensionWorker(
            dimension_config=dimension_config, worker_config=worker_config,
            llm=weak, adapter_registry=registry,
            on_done=on_dim_done, on_round=on_dim_round,
        )
    graph = build_main_graph(
        market_adapter=registry["clickhouse_market"],
        worker_factory=worker_factory,
        weak_llm=weak, strong_llm=strong, engine=explain_engine,
        adapter_registry=registry,
        news_crawler=news_crawler,
        news_indexer=news_indexer,
        on_node_event=on_node_event,
    )

    async def _run_main_graph(question: str) -> dict:
        st = new_attribution_state(question)
        return await graph.ainvoke(st)

    async def _run_followup_wrapper(session, history, question):
        return await run_followup(
            session=session, history=history, question=question,
            llm=strong, engine=explain_engine,
        )

    console.print("[green]✓[/green] ready.")
    return ReplEnvironment(
        engine=explain_engine, console=console,
        run_main_graph=_run_main_graph, run_followup=_run_followup_wrapper,
    )


def main() -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    env = _build_environment()
    state = ReplState()

    handle_sessions(engine=env.engine, console=env.console, limit=5)

    session_input = PromptSession(history=InMemoryHistory())

    async def _loop():
        while True:
            try:
                raw = await session_input.prompt_async("explain> ")
            except (EOFError, KeyboardInterrupt):
                env.console.print("[dim]bye.[/dim]")
                return
            try:
                await dispatch_input(state, env, raw)
            except ReplExit:
                return
            except Exception as e:
                env.console.print(f"[red]Error:[/red] {e!r}")

    asyncio.run(_loop())


if __name__ == "__main__":
    main()
