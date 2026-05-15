"""Wave D.1: 重评 existing session 的 edge.confidence (Wave A acceptance fixture).

design §7.2: 复用 scoring.yaml prompt for both manifests_as + causes edges.
对 causes edges treat driver 作 "abstract" target 作 "concrete" — LLM 评 driver→target
mechanism plausibility, 写回 edge.confidence = score / 5.0.

并发用 Wave C 补丁 1 的 LLM_MAX_CONCURRENCY (避免 LLM provider rate limit).
"""

from __future__ import annotations

import asyncio
import logging

from explain_engine.engines.evaluation import LLM_MAX_CONCURRENCY, _score_edge
from explain_engine.llm.client import LLMClient
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


async def rescore_session(
    state: CognitiveState, llm: LLMClient,
) -> dict[str, float]:
    """Rescore all manifests_as + causes edges in graph.

    Returns: dict[edge_id, new_confidence] (供 CLI render 用).

    Side effects:
        state.graph.edges[edge_id].confidence = score / 5.0 in-place.
    """
    prompt = load_prompt("scoring")
    edges_to_score = [
        e for e in state.graph.edges.values()
        if e.relation_type in ("manifests_as", "causes")
    ]
    if not edges_to_score:
        logger.info("[rescore] no manifests_as/causes edges to rescore")
        return {}

    logger.info(
        "[rescore] rescoring %d edges (concurrency=%d)...",
        len(edges_to_score), LLM_MAX_CONCURRENCY,
    )

    sem = asyncio.Semaphore(LLM_MAX_CONCURRENCY)
    completed = 0
    total = len(edges_to_score)

    async def _rescore_one(edge):
        nonlocal completed
        source = state.graph.nodes[edge.source_node]
        target = state.graph.nodes[edge.target_node]
        async with sem:
            score = await _score_edge(
                llm, prompt,
                abstract_name=source.name,
                abstract_description=source.description,
                concrete_name=target.name,
                concrete_description=target.description,
                mechanism=edge.mechanism_description,
            )
        completed += 1
        new_conf = score / 5.0
        edge.confidence = new_conf
        logger.info(
            "[rescore] edge %d/%d done: %s (%s→%s) conf=%.2f (score=%d)",
            completed, total, edge.id,
            edge.source_node, edge.target_node, new_conf, score,
        )
        return edge.id, new_conf

    results = await asyncio.gather(*[_rescore_one(e) for e in edges_to_score])
    return dict(results)
