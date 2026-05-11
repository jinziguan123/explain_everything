import asyncio
from typing import Callable

from explain_agent.graph.state import AttributionState, DimensionResult
from explain_agent.graph.dimension_worker import DimensionWorker


async def fan_out_dimensions_node(
    state: AttributionState,
    worker_factory: Callable[..., DimensionWorker],
) -> dict:
    framework = state["framework"]
    dims = framework["dimensions"]
    worker_cfg = framework["worker_config"]
    sem = asyncio.Semaphore(worker_cfg.get("max_concurrency", 3))

    async def run_one(dim_cfg: dict) -> tuple[str, DimensionResult]:
        async with sem:
            worker = worker_factory(dimension_config=dim_cfg, worker_config=worker_cfg)
            r = await worker.run(
                target=state["target"],
                time_window=state["time_window"],
                market_facts=state["market_facts"],
            )
            return dim_cfg["id"], r

    results = await asyncio.gather(*[run_one(d) for d in dims], return_exceptions=False)
    return {"dimension_results": dict(results)}
