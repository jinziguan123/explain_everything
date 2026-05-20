"""Compression Engine — 把 concrete phenomena 压成 abstract candidate.

设计参考 docs/plans/2026-05-13-cognitive-engine-phase-4-design.md §3。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ValidationError, field_validator

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


class _CoverageItem(BaseModel):
    concrete_id: str
    mechanism: str

    @field_validator("mechanism")
    @classmethod
    def _strip_nonempty(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("mechanism 不能为空")
        return s


class _CompressionCandidate(BaseModel):
    name: str
    description: str
    coverage: list[_CoverageItem]


class CompressionOutput(BaseModel):
    """compression.yaml prompt 的 structured output."""

    candidates: list[_CompressionCandidate]


def _render_lexicon_topk_section(
    existing_lexicon: list[tuple[str, str, int]] | None,
) -> str:
    """Phase 13 Wave 3 Task 1: 渲染 Top-K lexicon prior section。

    用于 compression prompt 提示 LLM "若新 candidate 语义重复其中之一，
    复用 ID 而非新生"。

    Args:
        existing_lexicon: list of (global_id, canonical_mechanism, reuse_count).
            None 或空 list → 返 ""（caller 视为无 section）。

    Returns:
        多行 block: header + 一行 bullet per entry，
        canonical_mechanism 截 80 char + "..." 若超长。
    """
    if not existing_lexicon:
        return ""
    lines = ["[已知 lexicon Top-K — 若新候选语义重复其中之一, 复用 ID 而非新生]"]
    for global_id, canonical, reuse_count in existing_lexicon:
        canonical_short = canonical[:80]
        if len(canonical) > 80:
            canonical_short += "..."
        lines.append(f"- {global_id} (reused {reuse_count}x): {canonical_short}")
    return "\n".join(lines)


async def propose_candidates(
    state: CognitiveState,
    llm: LLMClient,
    min_count: int = 3,
    max_count: int = 5,
    existing_lexicon: list[tuple[str, str, int]] | None = None,
) -> None:
    """LLM 出 3-5 个 abstract 候选，灌进 state.graph，落 state.insight_candidates。

    Side effects:
        - state.graph: 新增 N 个 level=1 VariableNode (c_001..c_00N) + 若干 edges
        - state.insight_candidates: 设为 [c_001, ..., c_00N]（未排序）

    Args:
        state: 当前 cognitive state（含 graph + root_question）。
        llm: LLMClient。
        min_count: 最少候选数。
        max_count: 最多候选数。
        existing_lexicon: Phase 13 Wave 3 — 可选 Top-K lexicon 列表
            (global_id, canonical_mechanism, reuse_count)。非空时
            注入 prompt，提示 LLM 复用而非新生重复语义的 candidate。

    Raises:
        LLMError: 底层调用失败（provider 抛出，本函数不捕）
        SchemaValidationError: LLM 输出不合规（retry 1 次后仍失败，或 concrete_id 无效）
    """
    concrete_ids = {nid for nid, n in state.graph.nodes.items() if n.abstraction_level == 0}
    phenomena_table = _render_phenomena_table(state)
    prompt = load_prompt("compression")

    messages = [
        Message(role="system", content=prompt["system"]),
        Message(
            role="user",
            content=prompt["user_template"].format(
                question=state.root_question,
                phenomena_table=phenomena_table,
                existing_lexicon_section=_render_lexicon_topk_section(existing_lexicon),
                min_count=min_count,
                max_count=max_count,
            ),
        ),
    ]

    output = await _call_with_retry(llm, messages, concrete_ids)

    # 截断 + 淘汰 coverage<2
    candidates = output.candidates[:max_count]
    candidates = [c for c in candidates if len(c.coverage) >= 2]

    if len(candidates) < min_count:
        logger.warning(
            "Compression 仅产出 %d 个有效候选（min=%d），但接受",
            len(candidates),
            min_count,
        )

    # 灌进 graph
    start_c = _next_candidate_id_num(state)
    next_edge_id = _next_edge_id(state)
    state.insight_candidates = []
    for offset, cand in enumerate(candidates):
        c_id = f"c_{start_c + offset:03d}"
        state.graph.add_node(
            VariableNode(
                id=c_id,
                name=cand.name,
                description=cand.description,
                abstraction_level=1,
                confidence=0.7,
                epistemic="insight",
                source="llm",
            )
        )
        for item in cand.coverage:
            state.graph.add_edge(
                RelationEdge(
                    id=f"e_{next_edge_id:03d}",
                    source_node=c_id,
                    target_node=item.concrete_id,
                    relation_type="manifests_as",
                    confidence=0.7,
                    mechanism_description=item.mechanism,
                )
            )
            next_edge_id += 1
        state.insight_candidates.append(c_id)


def _render_phenomena_table(state: CognitiveState) -> str:
    lines = []
    for nid, n in state.graph.nodes.items():
        if n.abstraction_level == 0:
            lines.append(f"- {nid}: {n.name} — {n.description}")
    return "\n".join(lines)


def _next_edge_id(state: CognitiveState) -> int:
    existing = [int(eid.split("_")[1]) for eid in state.graph.edges if eid.startswith("e_")]
    return (max(existing) + 1) if existing else 1


def _next_candidate_id_num(state: CognitiveState) -> int:
    """扫已有 c_NNN node 取 max+1（保证重入不撞 id）。"""
    existing = [
        int(nid.split("_")[1])
        for nid in state.graph.nodes
        if nid.startswith("c_") and nid[2:].isdigit()
    ]
    return (max(existing) + 1) if existing else 1


async def _call_with_retry(
    llm: LLMClient,
    messages: list[Message],
    valid_concrete_ids: set[str],
) -> CompressionOutput:
    """调 LLM，校验 concrete_id；不合规 retry 1 次。"""
    last_exc: Exception | None = None
    for _attempt in range(2):
        resp = await llm.chat(messages, schema=CompressionOutput)
        if resp.parsed is None:
            last_exc = SchemaValidationError("LLM 未返回 structured output")
            continue
        try:
            output = CompressionOutput.model_validate(resp.parsed)
        except ValidationError as exc:
            last_exc = SchemaValidationError(f"LLM 输出 schema 不合规: {exc}")
            continue
        # 校验 concrete_id
        bad = [
            item.concrete_id
            for cand in output.candidates
            for item in cand.coverage
            if item.concrete_id not in valid_concrete_ids
        ]
        if bad:
            last_exc = SchemaValidationError(f"未知 concrete_id: {bad}")
            continue
        return output
    assert last_exc is not None
    raise last_exc
