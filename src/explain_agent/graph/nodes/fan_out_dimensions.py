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
    sem = asyncio.Semaphore(worker_cfg.get("max_concurrency", 6))

    async def run_one(dim_cfg: dict) -> tuple[str, DimensionResult]:
        async with sem:
            worker = worker_factory(dimension_config=dim_cfg, worker_config=worker_cfg)
            r = await worker.run(
                target=state["target"],
                time_window=state["time_window"],
                market_facts=state["market_facts"],
            )
            return dim_cfg["id"], r

    results_or_errors = await asyncio.gather(
        *[run_one(d) for d in dims], return_exceptions=True
    )

    results: list[tuple[str, DimensionResult]] = []
    for dim_cfg, r in zip(dims, results_or_errors):
        if isinstance(r, BaseException):
            results.append((dim_cfg["id"], DimensionResult(
                evidence=[], mini_summary=f"维度 worker 失败: {r!r}",
                retry_count=0, no_data=True, confidence="low",
            )))
        else:
            results.append(r)
    return {"dimension_results": dict(results)}
