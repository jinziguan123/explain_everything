"""Expansion Engine — 给一个 abstract candidate 上溯 driver。

Phase 5 设计参考 docs/plans/2026-05-13-cognitive-engine-phase-5-design.md §3。

输入: state (含 graph) + target_id (frontier 节点) + llm
输出: (list[str], float) — (新建/复用 driver id list, expansion_gain ∈ [0,1])
  gain = mean(plausibility) / 5.0  (plausibility in-memory only, 不落盘)
副作用:
  - state.graph 新增 1-3 d_NNN VariableNode (level=2, source="llm",
    epistemic="inference", confidence=0.6)
  - state.graph 新增 1-3 causes RelationEdge (driver_id → target_id)
  - 同名 driver 复用现有 node id, 只加 edge
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field, ValidationError

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


class _DriverCandidate(BaseModel):
    name: str
    description: str
    mechanism: str
    plausibility: int = Field(ge=1, le=5)


class ExpansionOutput(BaseModel):
    """expansion.yaml prompt 的 structured output."""

    drivers: list[_DriverCandidate]


async def expand_one_frontier(
    state: CognitiveState,
    target_id: str,
    llm: LLMClient,
    max_drivers: int = 3,
) -> tuple[list[str], float]:
    """LLM 出 1-3 driver,灌进 state.graph,返回 (new_driver_ids, expansion_gain)。

    gain = mean(plausibility) / 5.0, plausibility 不持久化到 graph
    (Phase 6 重新设计 expansion_gain 时再考虑加 meta 字段)。

    Raises:
        ValueError: target_id 不在 graph / target 不是 level==1 / target 已有 incoming causes
        LLMError: LLM 调用失败 (provider 抛出，本函数不捕)
        SchemaValidationError: LLM 输出 schema / plausibility 不合规 (retry 1 次后仍失败)
    """
    if target_id not in state.graph.nodes:
        raise ValueError(f"target {target_id!r} not found in graph")
    target = state.graph.nodes[target_id]
    if target.abstraction_level != 1:
        raise ValueError(
            f"target {target_id!r} has level={target.abstraction_level}, must be 1"
        )
    if any(
        e.relation_type == "causes" and e.target_node == target_id
        for e in state.graph.edges.values()
    ):
        raise ValueError(
            f"target {target_id!r} already has incoming causes edge (not a frontier)"
        )

    return await _do_expansion(state, target_id, llm, max_drivers=max_drivers)


async def re_expand(
    state: CognitiveState,
    target_id: str,
    llm: LLMClient,
    max_drivers: int = 2,
) -> tuple[list[str], float]:
    """Wave C.2: 绕过 frontier check 给已 covered L1 加 driver (reflection 用)。

    与 expand_one_frontier 唯一差别: 不要求 target 没有 incoming causes edge。
    default max_drivers=2 (re-expand 比首次 expand 更保守,避免膨胀)。

    Raises:
        ValueError: target_id 不在 graph / target 不是 level==1
        LLMError: LLM 调用失败 (provider 抛出,本函数不捕)
        SchemaValidationError: LLM 输出 schema 不合规 (retry 1 次后仍失败)
    """
    if target_id not in state.graph.nodes:
        raise ValueError(f"target {target_id!r} not found in graph")
    target = state.graph.nodes[target_id]
    if target.abstraction_level != 1:
        raise ValueError(
            f"target {target_id!r} has level={target.abstraction_level}, must be 1"
        )

    return await _do_expansion(state, target_id, llm, max_drivers=max_drivers)


async def _do_expansion(
    state: CognitiveState,
    target_id: str,
    llm: LLMClient,
    max_drivers: int,
) -> tuple[list[str], float]:
    """LLM 调用 + driver/edge 写入的共享 helper (frontier check 由 caller 做)。"""
    target = state.graph.nodes[target_id]
    prompt = load_prompt("expansion")
    outgoing_edges_text = _render_target_outgoing_edges(state, target_id)
    existing_drivers_text = _render_existing_drivers(state)

    messages = [
        Message(role="system", content=prompt["system"]),
        Message(
            role="user",
            content=prompt["user_template"].format(
                question=state.root_question,
                target_node=f"{target.id}: {target.name} — {target.description}",
                target_outgoing_edges=outgoing_edges_text,
                existing_drivers=existing_drivers_text,
            ),
        ),
    ]

    output = await _call_with_retry(llm, messages)
    drivers = output.drivers[:max_drivers]

    if not drivers:
        logger.warning("Expansion 0 driver for target %s (skip, no aboard)", target_id)
        return [], 0.0

    next_d_num = _next_driver_id_num(state)
    next_edge_id = _next_edge_id(state)
    new_ids: list[str] = []
    existing_name_to_id = {n.name: nid for nid, n in state.graph.nodes.items()}

    for d in drivers:
        if d.name in existing_name_to_id:
            d_id = existing_name_to_id[d.name]
        else:
            d_id = f"d_{next_d_num:03d}"
            next_d_num += 1
            state.graph.add_node(
                VariableNode(
                    id=d_id,
                    name=d.name,
                    description=d.description,
                    abstraction_level=2,
                    confidence=0.6,
                    epistemic="inference",
                    source="llm",
                )
            )

        state.graph.add_edge(
            RelationEdge(
                id=f"e_{next_edge_id:03d}",
                source_node=d_id,
                target_node=target_id,
                relation_type="causes",
                # Wave A (Phase 7 design §4.3): linear mapping plausibility→confidence
                confidence=d.plausibility / 5.0,
                mechanism_description=d.mechanism,
            )
        )
        next_edge_id += 1
        new_ids.append(d_id)

    gain = sum(d.plausibility for d in drivers) / (5.0 * len(drivers))
    return new_ids, gain


def _render_target_outgoing_edges(state: CognitiveState, target_id: str) -> str:
    lines: list[str] = []
    for e in state.graph.edges.values():
        if e.source_node == target_id and e.relation_type == "manifests_as":
            concrete = state.graph.nodes.get(e.target_node)
            name = concrete.name if concrete else e.target_node
            lines.append(f"- {e.target_node}: {name} (mechanism: {e.mechanism_description})")
    return "\n".join(lines) if lines else "(none)"


def _render_existing_drivers(state: CognitiveState) -> str:
    lines = [
        f"- {nid}: {n.name} — {n.description}"
        for nid, n in state.graph.nodes.items()
        if nid.startswith("d_") and n.abstraction_level == 2
    ]
    return "\n".join(lines) if lines else "(none)"


def _next_driver_id_num(state: CognitiveState) -> int:
    existing = [
        int(nid.split("_")[1])
        for nid in state.graph.nodes
        if nid.startswith("d_") and nid[2:].isdigit()
    ]
    return (max(existing) + 1) if existing else 1


def _next_edge_id(state: CognitiveState) -> int:
    existing = [
        int(eid.split("_")[1])
        for eid in state.graph.edges
        if eid.startswith("e_") and eid[2:].isdigit()
    ]
    return (max(existing) + 1) if existing else 1


async def _call_with_retry(
    llm: LLMClient,
    messages: list[Message],
) -> ExpansionOutput:
    last_exc: Exception | None = None
    for _attempt in range(2):
        resp = await llm.chat(messages, schema=ExpansionOutput)
        if resp.parsed is None:
            last_exc = SchemaValidationError("LLM 未返回 structured output")
            continue
        try:
            return ExpansionOutput.model_validate(resp.parsed)
        except ValidationError as exc:
            last_exc = SchemaValidationError(f"LLM 输出 schema 不合规: {exc}")
            continue
    assert last_exc is not None
    raise last_exc
