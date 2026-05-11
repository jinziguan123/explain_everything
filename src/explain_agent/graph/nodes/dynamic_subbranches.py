import asyncio
from typing import Callable

from explain_agent.graph.state import AttributionState, DimensionResult
from explain_agent.graph.dimension_worker import DimensionWorker


async def dynamic_subbranches_node(
    state: AttributionState,
    worker_factory: Callable[..., DimensionWorker],
) -> dict:
    if not state.get("needs_subbranch") or not state.get("subbranches"):
        return {"subbranch_results": {}}

    worker_cfg = state["framework"].get("worker_config", {})
    sem = asyncio.Semaphore(worker_cfg.get("max_concurrency", 2))

    async def run_one(spec: dict) -> tuple[str, DimensionResult]:
        async with sem:
            dim_cfg = {
                "id": f"sub_{spec['name']}",
                "name": spec["name"],
                "data_sources": ["news_corpus"],
                "query_template": f"{spec['name']} " + " ".join(spec.get("query_hints", [])),
            }
            worker = worker_factory(dimension_config=dim_cfg, worker_config=worker_cfg)
            r = await worker.run(
                target=state["target"],
                time_window=state["time_window"],
                market_facts=state["market_facts"],
            )
            return spec["name"], r

    results = await asyncio.gather(*[run_one(s) for s in state["subbranches"]])
    return {"subbranch_results": dict(results)}
