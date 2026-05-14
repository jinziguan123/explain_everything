"""Wave B.1: Intervention parser — 把自然语言 intervention 拆成 (existing_refs, new_concepts).

design §5.2. LLM-based, retry 1 次. 返空 raise ValueError.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


class NewConceptSpec(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    expected_level: Literal[1, 2]


class ParsedIntervention(BaseModel):
    existing_refs: list[str] = Field(default_factory=list)
    new_concepts: list[NewConceptSpec] = Field(default_factory=list, max_length=2)


async def parse(
    state: CognitiveState,
    intervention_text: str,
    llm: LLMClient,
) -> ParsedIntervention:
    """LLM-based intervention parser.

    Raises:
        SchemaValidationError: LLM 输出不合 schema 或 existing_refs 含不存在 id
                               (retry 1 次仍失败).
        ValueError: parser 返空 (existing_refs=[] 且 new_concepts=[]).
    """
    prompt = load_prompt("intervention_parser")
    graph_nodes_table = _render_graph_nodes(state)
    valid_ids_with_level: dict[str, int] = {
        nid: n.abstraction_level for nid, n in state.graph.nodes.items()
    }

    messages = [
        Message(role="system", content=prompt["system"]),
        Message(
            role="user",
            content=prompt["user_template"].format(
                question=state.root_question,
                graph_nodes_table=graph_nodes_table,
                intervention_text=intervention_text,
            ),
        ),
    ]

    parsed = await _call_with_retry(llm, messages, valid_ids_with_level)

    if not parsed.existing_refs and not parsed.new_concepts:
        raise ValueError(
            f"无法解析 intervention: {intervention_text!r} "
            f"(parser 返空 — intervention 可能跟 root_question 无关)"
        )
    return parsed


def _render_graph_nodes(state: CognitiveState) -> str:
    lines = [
        f"- {nid}: {n.name} — {n.description} (level={n.abstraction_level})"
        for nid, n in state.graph.nodes.items()
    ]
    return "\n".join(lines) if lines else "(graph 为空)"


async def _call_with_retry(
    llm: LLMClient,
    messages: list[Message],
    valid_ids_with_level: dict[str, int],
) -> ParsedIntervention:
    last_exc: Exception | None = None
    for _attempt in range(2):
        resp = await llm.chat(messages, schema=ParsedIntervention)
        if resp.parsed is None:
            last_exc = SchemaValidationError("LLM 未返回 structured output")
            continue
        try:
            parsed = ParsedIntervention.model_validate(resp.parsed)
        except ValidationError as exc:
            last_exc = SchemaValidationError(f"parser 输出 schema 不合规: {exc}")
            continue
        bad_unknown = [
            rid for rid in parsed.existing_refs if rid not in valid_ids_with_level
        ]
        if bad_unknown:
            last_exc = SchemaValidationError(f"未知 variable id: {bad_unknown}")
            continue
        bad_l0 = [
            rid for rid in parsed.existing_refs
            if valid_ids_with_level[rid] == 0
        ]
        if bad_l0:
            last_exc = SchemaValidationError(
                f"L0 节点不可作 intervention target "
                f"(intervention 应 target L1/L2 mechanism): {bad_l0}"
            )
            continue
        return parsed
    assert last_exc is not None
    raise last_exc
