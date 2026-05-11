import typer
from rich.console import Console
from rich.progress import track

from explain_agent.db.mysql import get_engine
from explain_agent.db.qdrant import get_qdrant_client
from explain_agent.embedding.bge_m3 import get_embedder
from explain_agent.ingest.news_crawler import AkshareNewsCrawler
from explain_agent.ingest.news_indexer import NewsIndexer
from explain_agent.ingest.tagger import NewsTagger

app = typer.Typer()
console = Console()


DEFAULT_KEYWORDS = [
    "半导体", "新能源", "光伏", "锂电池", "人工智能", "医药",
    "白酒", "银行", "地产", "军工", "煤炭", "钢铁", "化工",
    "电力", "通信", "传媒", "汽车", "家电",
]


@app.command()
def main(
    keywords_file: str | None = typer.Option(None, help="一行一个关键词的文件"),
    limit_per_kw: int = typer.Option(200, help="每个关键词最多取多少条"),
):
    if keywords_file:
        with open(keywords_file, encoding="utf-8") as f:
            kws = [x.strip() for x in f if x.strip()]
    else:
        kws = DEFAULT_KEYWORDS

    console.print(f"[bold]开始回填，关键词数 {len(kws)}[/bold]")

    crawler = AkshareNewsCrawler()
    tagger = NewsTagger()
    embedder = get_embedder()
    qdrant = get_qdrant_client()
    indexer = NewsIndexer(
        engine=get_engine("explain"),
        tagger=tagger,
        embedder=embedder,
        qdrant=qdrant,
    )

    total = 0
    for kw in track(kws, description="拉取中"):
        items = crawler.crawl_symbol(kw)[:limit_per_kw]
        n = indexer.index(items)
        total += n
        console.print(f"  {kw}: 新增 {n} 条")

    console.print(f"[bold green]完成，共新增 {total} 条新闻[/bold green]")


if __name__ == "__main__":
    app()
