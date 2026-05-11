import time
from typing import Awaitable, Callable

from langgraph.graph import StateGraph, END

from explain_agent.graph.state import AttributionState
from explain_agent.graph.nodes.parse_question import parse_question_node
from explain_agent.graph.nodes.domain_router import domain_router_node
from explain_agent.graph.nodes.fetch_market_facts import fetch_market_facts_node
from explain_agent.graph.nodes.fan_out_dimensions import fan_out_dimensions_node
from explain_agent.graph.nodes.synthesizer import synthesizer_node
from explain_agent.graph.nodes.dynamic_subbranches import dynamic_subbranches_node
from explain_agent.graph.nodes.report_builder import report_builder_node
from explain_agent.graph.nodes.persist import persist_node
from explain_agent.graph.framework_loader import load_framework


NodeEvent = Callable[..., None]


def build_main_graph(
    market_adapter,
    worker_factory,
    weak_llm,
    strong_llm,
    engine,
    on_node_event: NodeEvent | None = None,
):
    g = StateGraph(AttributionState)

    def _trace(name: str, fn: Callable[..., Awaitable[dict]]):
        if on_node_event is None:
            return fn

        async def traced(state):
            on_node_event("start", name)
            t0 = time.perf_counter()
            try:
                out = await fn(state)
                on_node_event("end", name, time.perf_counter() - t0)
                return out
            except Exception as e:
                on_node_event("error", name, time.perf_counter() - t0, repr(e))
                raise

        return traced

    async def _parse(state):
        return await parse_question_node(state, llm=weak_llm)

    async def _router(state):
        return await domain_router_node(state)

    async def _load_fw(state):
        return {"framework": load_framework(state["domain_id"])}

    async def _facts(state):
        return await fetch_market_facts_node(state, market_adapter=market_adapter)

    async def _fan_out(state):
        return await fan_out_dimensions_node(state, worker_factory=worker_factory)

    async def _synth(state):
        return await synthesizer_node(state, llm=strong_llm)

    async def _sub(state):
        return await dynamic_subbranches_node(state, worker_factory=worker_factory)

    async def _report(state):
        return await report_builder_node(state, llm=strong_llm)

    async def _persist(state):
        return await persist_node(state, engine=engine)

    g.add_node("parse", _trace("parse", _parse))
    g.add_node("router", _trace("router", _router))
    g.add_node("load_framework", _trace("load_framework", _load_fw))
    g.add_node("market_facts", _trace("market_facts", _facts))
    g.add_node("fan_out", _trace("fan_out", _fan_out))
    g.add_node("synth", _trace("synth", _synth))
    g.add_node("dynamic_sub", _trace("dynamic_sub", _sub))
    g.add_node("report", _trace("report", _report))
    g.add_node("persist", _trace("persist", _persist))

    g.set_entry_point("parse")
    g.add_edge("parse", "router")
    g.add_edge("router", "load_framework")
    g.add_edge("load_framework", "market_facts")
    g.add_edge("market_facts", "fan_out")
    g.add_edge("fan_out", "synth")
    g.add_edge("synth", "dynamic_sub")
    g.add_edge("dynamic_sub", "report")
    g.add_edge("report", "persist")
    g.add_edge("persist", END)

    return g.compile()
