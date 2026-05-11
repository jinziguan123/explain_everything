import asyncio

from rich.console import Console

from explain_agent.adapters.clickhouse_market import ClickHouseMarketAdapter, IndustryResolver
from explain_agent.adapters.mysql_fundamentals import MySQLFundamentalsAdapter
from explain_agent.adapters.akshare_capital_flow import AkshareCapitalFlowAdapter
from explain_agent.adapters.news_corpus import NewsCorpusAdapter
from explain_agent.db.clickhouse import get_client as ch_client
from explain_agent.db.mysql import get_engine
from explain_agent.db.qdrant import get_qdrant_client
from explain_agent.embedding.bge_m3 import get_embedder
from explain_agent.graph.dimension_worker import DimensionWorker
from explain_agent.graph.main_graph import build_main_graph
from explain_agent.graph.state import new_attribution_state
from explain_agent.llm import get_strong_llm, get_weak_llm


console = Console()


def main(question: str = "为什么半导体板块今天涨"):
    quant_engine = get_engine("quant")
    explain_engine = get_engine("explain")
    resolver = IndustryResolver(quant_engine)

    market = ClickHouseMarketAdapter(ch_client(), resolver)
    fund = MySQLFundamentalsAdapter(quant_engine, resolver)
    flow = AkshareCapitalFlowAdapter()
    news = NewsCorpusAdapter(
        qdrant=get_qdrant_client(), embedder=get_embedder(), engine=explain_engine,
    )
    registry = {
        "clickhouse_market": market,
        "mysql_fundamentals": fund,
        "akshare_capital_flow": flow,
        "news_corpus": news,
    }

    weak = get_weak_llm()
    strong = get_strong_llm()

    def worker_factory(dimension_config, worker_config):
        return DimensionWorker(
            dimension_config=dimension_config,
            worker_config=worker_config,
            llm=weak,
            adapter_registry=registry,
        )

    graph = build_main_graph(
        market_adapter=market,
        worker_factory=worker_factory,
        weak_llm=weak,
        strong_llm=strong,
        engine=explain_engine,
    )

    state = new_attribution_state(question)
    console.print(f"[bold cyan]Question:[/bold cyan] {question}")
    result = asyncio.run(graph.ainvoke(state))

    console.print(f"\n[bold]Target:[/bold] {result.get('target')}")
    console.print(f"[bold]Time window:[/bold] {result.get('time_window')}")
    console.print(f"[bold]Domain:[/bold] {result.get('domain_id')}")
    console.print(f"[bold]Confidence:[/bold] {result.get('confidence')}")

    console.print(f"\n[bold yellow]Narrative:[/bold yellow]\n{result.get('narrative')}")

    claims = result.get("narrative_claims", [])
    console.print(f"\n[bold]Narrative claims ({len(claims)} 条):[/bold]")
    for i, c in enumerate(claims, 1):
        console.print(f"  {i}. [cyan]{c['text']}[/cyan]  → {c['evidence_ids']}")

    drops = result.get("unverified_drops", [])
    if drops:
        console.print(f"\n[bold red]Unverified drops ({len(drops)} 条):[/bold red]")
        for d in drops:
            console.print(f"  - {d}")
    else:
        console.print("\n[dim]Unverified drops: 0 (强模型守规矩或无数字声明)[/dim]")

    console.print("\n[bold yellow]Dimension Reports:[/bold yellow]")
    for dim_id, summary in result.get("dimension_reports", {}).items():
        console.print(f"\n[bold]▎ {dim_id}[/bold]")
        console.print(summary[:500])

    if result.get("subbranch_results"):
        console.print("\n[bold yellow]Subbranches:[/bold yellow]")
        for name, r in result["subbranch_results"].items():
            console.print(f"\n[bold]▎ 子分支: {name}[/bold]")
            console.print(r["mini_summary"][:500])

    console.print(f"\n[bold]Citations:[/bold] {len(result.get('citations', []))} 条")
    console.print(f"[bold]Session ID:[/bold] {result.get('session_id')}")


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "为什么半导体板块今天涨"
    main(q)
