"""Wave B.2: Forward Prediction Engine.

design §5.3. flow:
  1. parser → ParsedIntervention
  2. 加 new_concepts 进 graph (level by spec, epistemic=speculation)
  3. LLM 生 predicted L0 (level=0, epistemic=speculation), 仅当 new_concepts 非空
  4. propagate from new + existing
  5. 返 PredictionReport (state.graph 已 mutate)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from explain_engine.engines._propagation import DecayStep, propagate
from explain_engine.engines.intervention_parser import (
    ParsedIntervention,
)
from explain_engine.engines.intervention_parser import (
    parse as parse_intervention,
)
from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


class PredictedL0(BaseModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)


class PredictionOutput(BaseModel):
    predicted_L0: list[PredictedL0] = Field(min_length=1, max_length=5)


@dataclass(frozen=True)
class PredictionReport:
    intervention_text: str
    parsed: ParsedIntervention
    new_node_ids: list[str]
    predicted_L0_ids: list[str]
    activated_existing_L0: list[str]
    propagation_acts: dict[str, float]
    decay_trace: list[DecayStep]


async def predict(
    state: CognitiveState,
    intervention_text: str,
    llm: LLMClient,
) -> PredictionReport:
    """Forward prediction. 副作用: state.graph 改 (加 nodes + edges).

    Raises:
        LLMError: provider 调用失败 (本函数不捕).
        SchemaValidationError: parser 或 generation LLM 输出不合规.
        ValueError: parser 返空 (无法解析 intervention).
    """
    parsed = await parse_intervention(state, intervention_text, llm)

    new_node_ids: list[str] = []
    predicted_L0_ids: list[str] = []

    # 1. 加 new_concepts 进 graph
    for spec in parsed.new_concepts:
        prefix = "c" if spec.expected_level == 1 else "d"
        new_id = _next_id(state, prefix)
        state.graph.add_node(VariableNode(
            id=new_id, name=spec.name, description=spec.description,
            abstraction_level=spec.expected_level,
            confidence=0.7, epistemic="speculation", source="llm",
        ))
        new_node_ids.append(new_id)

    # 2. LLM 生 predicted L0 (仅当有 new_concepts)
    if parsed.new_concepts:
        gen_output = await _generate_predicted_L0(state, parsed, intervention_text, llm)
        for predicted in gen_output.predicted_L0:
            p_id = _next_id(state, "p")
            state.graph.add_node(VariableNode(
                id=p_id, name=predicted.name, description=predicted.description,
                abstraction_level=0, confidence=0.7,
                epistemic="speculation", source="llm",
            ))
            predicted_L0_ids.append(p_id)
            # I1 fix: 仅 link 第一个 new_concept (避免 noisy-OR 过度激活,
            # 因为 fan-out 把同一 intervention 的效应当独立 parent).
            if new_node_ids:
                edge_id = _next_edge_id(state)
                state.graph.add_edge(RelationEdge(
                    id=edge_id, source_node=new_node_ids[0], target_node=p_id,
                    relation_type="manifests_as", confidence=0.7,
                    mechanism_description=predicted.mechanism,
                ))

    # 3. propagate
    sources = set(new_node_ids) | set(parsed.existing_refs)
    acts, trace = propagate(state.graph, sources)

    activated_L0 = sorted(
        nid for nid, n in state.graph.nodes.items()
        if n.abstraction_level == 0 and n.epistemic != "speculation"
        and acts.get(nid, 0.0) > 0
    )

    return PredictionReport(
        intervention_text=intervention_text, parsed=parsed,
        new_node_ids=new_node_ids, predicted_L0_ids=predicted_L0_ids,
        activated_existing_L0=activated_L0,
        propagation_acts=acts, decay_trace=trace,
    )


async def _generate_predicted_L0(
    state: CognitiveState,
    parsed: ParsedIntervention,
    intervention_text: str,
    llm: LLMClient,
) -> PredictionOutput:
    prompt = load_prompt("prediction")
    existing_L0_table = "\n".join(
        f"- {nid}: {n.name}" for nid, n in state.graph.nodes.items()
        if n.abstraction_level == 0 and n.epistemic != "speculation"
    ) or "(none)"
    intervention_summary = (
        f"{intervention_text}\n"
        f"existing_refs={parsed.existing_refs}, "
        f"new_concepts={[c.name for c in parsed.new_concepts]}"
    )
    messages = [
        Message(role="system", content=prompt["system"]),
        Message(role="user", content=prompt["user_template"].format(
            question=state.root_question,
            intervention_summary=intervention_summary,
            existing_L0_table=existing_L0_table,
        )),
    ]
    last_exc: Exception | None = None
    for _ in range(2):
        resp = await llm.chat(messages, schema=PredictionOutput)
        if resp.parsed is None:
            last_exc = SchemaValidationError("LLM 未返回 structured output")
            continue
        try:
            return PredictionOutput.model_validate(resp.parsed)
        except ValidationError as exc:
            last_exc = SchemaValidationError(f"prediction 输出 schema 不合规: {exc}")
            continue
    assert last_exc is not None
    raise last_exc


def _next_id(state: CognitiveState, prefix: str) -> str:
    existing = [
        int(nid.split("_")[1])
        for nid in state.graph.nodes
        if nid.startswith(f"{prefix}_") and nid[len(prefix)+1:].isdigit()
    ]
    n = (max(existing) + 1) if existing else 1
    return f"{prefix}_{n:03d}"


def _next_edge_id(state: CognitiveState) -> str:
    existing = [
        int(eid.split("_")[1])
        for eid in state.graph.edges
        if eid.startswith("e_") and eid[2:].isdigit()
    ]
    n = (max(existing) + 1) if existing else 1
    return f"e_{n:03d}"
