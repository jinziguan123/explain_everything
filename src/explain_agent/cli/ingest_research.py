from datetime import date, timedelta

import typer
from rich.console import Console

from explain_agent.db.mysql import get_engine
from explain_agent.db.qdrant import get_qdrant_client
from explain_agent.embedding.bge_m3 import get_embedder
from explain_agent.ingest.research_crawler import EastmoneyResearchCrawler
from explain_agent.ingest.research_indexer import ResearchIndexer


app = typer.Typer()
console = Console()


@app.command()
def main(
    days: int = typer.Option(7, help="回溯天数"),
    q_types: list[int] = typer.Option([1, 2], help="qType: 1=行业, 2=宏观"),
    max_pages_per_type: int = typer.Option(5, help="每种 qType 最多翻多少页"),
    page_size: int = typer.Option(50, help="单页条数"),
):
    console.print(f"[bold]开始拉研报, 回溯 {days} 天, qType={q_types}[/bold]")

    crawler = EastmoneyResearchCrawler()
    embedder = get_embedder()
    embedder.embed(["warm up"])
    qdrant = get_qdrant_client()
    engine = get_engine("explain")

    indexer = ResearchIndexer(engine=engine, embedder=embedder, qdrant=qdrant)

    end = date.today()
    begin = end - timedelta(days=days)

    total = 0
    for qt in q_types:
        console.print(f"[dim]拉 qType={qt} ...[/dim]")
        items = crawler.crawl(
            q_type=qt, begin=begin, end=end,
            page_size=page_size, max_pages=max_pages_per_type,
        )
        n = indexer.index(items)
        console.print(f"  qType={qt}: 抓到 {len(items)} 条, 新增 {n} 条")
        total += n

    console.print(f"[bold green]完成, 共新增 {total} 条研报[/bold green]")


if __name__ == "__main__":
    app()
