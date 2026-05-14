"""Wave B.3: Counterfactual Engine.

design §5.4. 副作用 = 0 (graph 深拷贝跑 propagate, 拷贝丢弃).
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from explain_engine.engines._propagation import next_edge_id, next_id, propagate
from explain_engine.engines.intervention_parser import (
    ParsedIntervention,
)
from explain_engine.engines.intervention_parser import (
    parse as parse_intervention,
)
from explain_engine.engines.prediction import PredictionOutput
from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


class _NarrativeOutput(BaseModel):
    narrative: str = Field(min_length=1)


@dataclass(frozen=True)
class CounterfactualReport:
    intervention_text: str
    parsed: ParsedIntervention
    removed_node_ids: list[str]
    added_node_ids: list[str]
    added_predicted_L0_ids: list[str]
    baseline_acts: dict[str, float]
    counterfactual_acts: dict[str, float]
    activation_diff: dict[str, float]
    alt_narrative: str | None


async def substitute(
    state: CognitiveState,
    intervention_text: str,
    llm: LLMClient,
) -> CounterfactualReport:
    """Counterfactual: remove + (optional) substitute.

    副作用 = 0: 不改 state.graph. 深拷贝跑 propagate, 拷贝丢弃.

    Pure remove (no new_concepts): 0 LLM call (just propagation diff).
    Substitute (with new_concepts): 2 LLM call (generation + narrative).

    Raises:
        LLMError: provider 调用失败 (本函数不捕).
        SchemaValidationError: parser 或 generation LLM 输出不合规.
        ValueError: parser 返空.
    """
    parsed = await parse_intervention(state, intervention_text, llm)

    # 1. baseline: 原 graph 所有 L1+L2 propagate
    original_L1_L2 = {
        nid for nid, n in state.graph.nodes.items() if n.abstraction_level >= 1
    }
    baseline_acts, _ = propagate(state.graph, original_L1_L2) if original_L1_L2 else ({}, [])

    # B.3 I3 fix: 在 mutation 之前从 state.graph (原图) 解 removed 节点的 name,
    # 因为 cf_graph 在 step 3 会删掉这些节点, 之后无法解.
    existing_refs_summary = ", ".join(
        f"{rid}({state.graph.nodes[rid].name})"
        for rid in parsed.existing_refs
        if rid in state.graph.nodes
    ) or "(none)"

    # 2. 深拷贝 graph (副作用隔离)
    cf_graph = copy.deepcopy(state.graph)

    # 3. 删 existing_refs (cascade edges via remove_node)
    removed_ids: list[str] = []
    for rid in parsed.existing_refs:
        if rid in cf_graph.nodes:
            cf_graph.remove_node(rid)
            removed_ids.append(rid)

    # 4. 加 new_concepts (substitute case)
    added_ids: list[str] = []
    added_L0_ids: list[str] = []
    for spec in parsed.new_concepts:
        prefix = "c" if spec.expected_level == 1 else "d"
        new_id = next_id(cf_graph, prefix)
        cf_graph.add_node(VariableNode(
            id=new_id, name=spec.name, description=spec.description,
            abstraction_level=spec.expected_level,
            confidence=0.7, epistemic="speculation", source="llm",
        ))
        added_ids.append(new_id)

    # 5. LLM 生 alt predicted L0 (仅 substitute case)
    if parsed.new_concepts:
        gen_output = await _generate_alt_predicted(
            cf_graph, parsed, intervention_text, existing_refs_summary,
            llm, state.root_question,
        )
        for predicted in gen_output.predicted_L0:
            p_id = next_id(cf_graph, "p")
            cf_graph.add_node(VariableNode(
                id=p_id, name=predicted.name, description=predicted.description,
                abstraction_level=0, confidence=0.7,
                epistemic="speculation", source="llm",
            ))
            added_L0_ids.append(p_id)
            # I1 fix from B.2: 只跟第一个 new_concept 建 edge
            if added_ids:
                edge_id = next_edge_id(cf_graph)
                cf_graph.add_edge(RelationEdge(
                    id=edge_id, source_node=added_ids[0], target_node=p_id,
                    relation_type="manifests_as", confidence=0.7,
                    mechanism_description=predicted.mechanism,
                ))

    # 6. counterfactual propagate
    cf_L1_L2 = {
        nid for nid, n in cf_graph.nodes.items() if n.abstraction_level >= 1
    }
    cf_acts, _ = propagate(cf_graph, cf_L1_L2) if cf_L1_L2 else ({}, [])

    # 7. diff
    diff = {
        nid: baseline_acts.get(nid, 0.0) - cf_acts.get(nid, 0.0)
        for nid in set(baseline_acts) | set(cf_acts)
    }

    # 8. narrative (仅 substitute case)
    alt_narrative: str | None = None
    if parsed.new_concepts:
        # B.3 I3 fix: pre-resolve name 给 narrative prompt, 让 LLM 有上下文.
        removed_summary_str = ", ".join(
            f"{rid}({state.graph.nodes[rid].name})"
            for rid in removed_ids
            if rid in state.graph.nodes
        )
        added_summary_str = ", ".join(
            f"{aid}({cf_graph.nodes[aid].name})"
            for aid in added_ids
            if aid in cf_graph.nodes
        )

        def _name(nid: str) -> str:
            # diff 含 baseline 跟 cf 两边的 id. cf_graph 是 superset
            # (含 predicted L0); 失败 fallback 到原图 (含已删的).
            if nid in cf_graph.nodes:
                return cf_graph.nodes[nid].name
            if nid in state.graph.nodes:
                return state.graph.nodes[nid].name
            return "?"

        diff_with_names = sorted(
            [(nid, _name(nid), v) for nid, v in diff.items()],
            key=lambda x: x[0],
        )
        alt_narrative = await _generate_narrative(
            state.root_question, removed_summary_str, added_summary_str,
            diff_with_names, llm,
        )

    return CounterfactualReport(
        intervention_text=intervention_text, parsed=parsed,
        removed_node_ids=removed_ids,
        added_node_ids=added_ids,
        added_predicted_L0_ids=added_L0_ids,
        baseline_acts=baseline_acts,
        counterfactual_acts=cf_acts,
        activation_diff=diff,
        alt_narrative=alt_narrative,
    )


async def _generate_alt_predicted(
    cf_graph: ExplanationGraph,
    parsed: ParsedIntervention,
    intervention_text: str,
    existing_refs_summary: str,
    llm: LLMClient,
    question: str,
) -> PredictionOutput:
    prompt = load_prompt("prediction")
    existing_L0_table = "\n".join(
        f"- {nid}: {n.name}" for nid, n in cf_graph.nodes.items()
        if n.abstraction_level == 0 and n.epistemic != "speculation"
    ) or "(none)"
    # B.3 I3 fix: 用 pre-resolved {id}(name) 而非 raw id list, 给 LLM 上下文
    intervention_summary = (
        f"{intervention_text}\n"
        f"removed: {existing_refs_summary}, "
        f"substituted: {[c.name for c in parsed.new_concepts]}"
    )
    messages = [
        Message(role="system", content=prompt["system"]),
        Message(role="user", content=prompt["user_template"].format(
            question=question,
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


async def _generate_narrative(
    question: str,
    removed_summary: str,
    added_summary: str,
    diff_with_names: list[tuple[str, str, float]],
    llm: LLMClient,
) -> str:
    """生成 counterfactual narrative.

    B.3 I3 fix: removed/added/diff 全用 pre-resolved {id}(name) 而非 raw id,
    让 LLM 有足够上下文写出可读的 narrative.
    """
    prompt = load_prompt("counterfactual_narrative")
    diff_table = "\n".join(
        f"  {nid}({name}): {v:+.2f}" for nid, name, v in diff_with_names
    ) or "(无变化)"
    messages = [
        Message(role="system", content=prompt["system"]),
        Message(role="user", content=prompt["user_template"].format(
            question=question,
            removed_summary=removed_summary or "(无)",
            substituted_summary=added_summary or "(无)",
            diff_table=diff_table,
        )),
    ]
    resp = await llm.chat(messages, schema=_NarrativeOutput)
    if resp.parsed is None:
        raise SchemaValidationError("narrative LLM 未返回 structured output")
    return _NarrativeOutput.model_validate(resp.parsed).narrative
