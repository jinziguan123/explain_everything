"""Evaluation Engine — 给 abstract candidate 算 compression_gain。

compression_gain = representation_reduction × explanatory_preservation
  representation_reduction = covered_concrete / total_concrete  (Python)
  explanatory_preservation = mean(mechanism_plausibility 1-5) / 5  (LLM)

设计参考 docs/plans/2026-05-13-cognitive-engine-phase-4-design.md §4。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


class _ScoringOutput(BaseModel):
    score: int = Field(ge=1, le=5)
    rationale: str = ""


async def score_all(state: CognitiveState, llm: LLMClient) -> dict[str, float]:
    """对 state.insight_candidates 每个 candidate 算 compression_gain，
    按 gain 降序重排 state.insight_candidates。

    Returns:
        dict[candidate_id, gain]（供 HITL 2 渲染表格用）

    Raises:
        SchemaValidationError: LLM 评分返回非 1-5 整数
    """
    total_concrete = sum(
        1 for n in state.graph.nodes.values() if n.abstraction_level == 0
    )
    if total_concrete == 0:
        logger.warning("total_concrete == 0, all gains will be 0")

    gains: dict[str, float] = {}
    prompt = load_prompt("scoring")

    for cid in state.insight_candidates:
        cand = state.graph.nodes[cid]
        out_edges = [
            e
            for e in state.graph.edges.values()
            if e.source_node == cid and e.relation_type == "manifests_as"
        ]
        if not out_edges or total_concrete == 0:
            gains[cid] = 0.0
            continue

        covered = len(out_edges)
        representation_reduction = covered / total_concrete

        scores: list[int] = []
        scores_by_edge: dict[str, int] = {}   # Wave A: 记录 per-edge score
        for e in out_edges:
            concrete = state.graph.nodes[e.target_node]
            score = await _score_edge(
                llm,
                prompt,
                abstract_name=cand.name,
                abstract_description=cand.description,
                concrete_name=concrete.name,
                concrete_description=concrete.description,
                mechanism=e.mechanism_description,
            )
            scores.append(score)
            scores_by_edge[e.id] = score        # Wave A: 累计

        explanatory_preservation = (sum(scores) / len(scores)) / 5.0
        gains[cid] = representation_reduction * explanatory_preservation

        # Wave A (Phase 7 design §4.2): 写回 edge.confidence = score / 5.0
        for edge_id, score in scores_by_edge.items():
            state.graph.edges[edge_id].confidence = score / 5.0

    # 降序重排
    state.insight_candidates = sorted(
        state.insight_candidates, key=lambda cid: gains[cid], reverse=True
    )
    # Phase 5: 持久化 last_gains（覆盖旧值），让 HITL 2 重入 + Runtime loop 读得到
    state.last_gains = dict(gains)
    return gains


async def _score_edge(
    llm: LLMClient,
    prompt: dict[str, Any],
    *,
    abstract_name: str,
    abstract_description: str,
    concrete_name: str,
    concrete_description: str,
    mechanism: str,
) -> int:
    """调 scoring.yaml prompt 评单条 edge mechanism 分数，retry 1 次。

    注：retry 内联在本函数（不像 compression.py 抽 _call_with_retry）—— 因为
    每条 edge 独立 retry，retry 粒度是 inner-loop 级别，抽函数反而绕。
    """
    messages = [
        Message(role="system", content=prompt["system"]),
        Message(
            role="user",
            content=prompt["user_template"].format(
                abstract_name=abstract_name,
                abstract_description=abstract_description,
                concrete_name=concrete_name,
                concrete_description=concrete_description,
                mechanism=mechanism,
            ),
        ),
    ]
    last_exc: Exception | None = None
    for _ in range(2):
        resp = await llm.chat(messages, schema=_ScoringOutput)
        if resp.parsed is None:
            last_exc = SchemaValidationError("LLM 未返回 structured output")
            continue
        try:
            return _ScoringOutput.model_validate(resp.parsed).score
        except ValidationError as exc:
            last_exc = SchemaValidationError(f"scoring 输出 schema 不合规: {exc}")
            continue
    raise SchemaValidationError(
        f"scoring 返非 1-5 整数 for {abstract_name} → {concrete_name}: {last_exc}"
    )
