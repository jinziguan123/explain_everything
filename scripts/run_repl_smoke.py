"""端到端 REPL smoke：模拟 4-5 个真实输入跑通 main graph + followup + /sessions。

直接运行: uv run python scripts/run_repl_smoke.py
"""
import asyncio

from rich.console import Console

from explain_agent.cli.repl.loop import _build_environment, dispatch_input
from explain_agent.cli.repl.state import ReplState


SCRIPTED_INPUTS = [
    "为什么半导体板块今天涨",
    "政策面具体是哪些政策？",
    "长电科技为什么涨停？",
    "/sessions",
    "/clear",
    "AI 算力对板块的拉动有多大？",
    "/quit",
]


async def main():
    console = Console()
    env = _build_environment()
    state = ReplState()
    for inp in SCRIPTED_INPUTS:
        console.print(f"\n[bold cyan]> {inp}[/bold cyan]")
        try:
            await dispatch_input(state, env, inp)
        except SystemExit:
            break
    console.print("\n[green]smoke done.[/green]")


if __name__ == "__main__":
    asyncio.run(main())
