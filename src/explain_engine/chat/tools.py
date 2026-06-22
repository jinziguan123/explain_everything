"""Phase 9 Wave B Task B.1: Tool dataclass + 5 engine tool wrappers.

每个 Tool 包装一个 Phase 0-8 engine, 通过 Anthropic native tool_use API
(Wave C.2) 暴露给 LLM agent. ToolContext 携带 per-call state + llm client.

设计参考 docs/plans/2026-05-14-cognitive-engine-phase-9-design.md.
设计参考 docs/plans/2026-05-14-cognitive-engine-phase-9-plan.md Wave B Task B.1.

API 备忘 (engine 实际签名, spec 不一定准):
- expansion.expand_downward(state, l1_id, llm, max_l0=3) → list[str]
- expansion.expand_one_frontier(state, target_id, llm, max_drivers=3) → (list[str], float)
  注: target_id 必填, 没有 auto-pick; tool 层在 auto 模式自己挑 frontier L1.
- compression.propose_candidates(state, llm, min_count=3, max_count=5) → None
  注: 副作用写 state.insight_candidates, 没有 compress() 这个名字.
- simulation.aggregate_acceptance(state) → AcceptanceReport (multi-signal)
- simulation.check_consistency(state, target_id) → ConsistencyReport
- prediction.predict(state, intervention_text, llm) → PredictionReport
  字段: new_node_ids / predicted_L0_ids / activated_existing_L0 / propagation_acts / ...
- counterfactual.substitute(state, intervention_text, llm) → CounterfactualReport
  字段: baseline_acts / counterfactual_acts / activation_diff / alt_narrative / ...
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from explain_engine.engines import compression, counterfactual, expansion, prediction
from explain_engine.engines.simulation import aggregate_acceptance, check_consistency
from explain_engine.llm.client import LLMClient
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


@dataclass
class ToolContext:
    """Per-call ctx passed to every Tool.call().

    state: 当前 CognitiveState, tool 可读可写 (e.g. expand/compress mutate graph).
    llm: 可选 LLM client; readonly tool (e.g. check) 不需要, 传 None 即可.
    """

    state: CognitiveState
    llm: LLMClient | None = None


@dataclass
class Tool:
    """Phase 9 Wave B: capability wrap for LLM tool_use API.

    name: tool 名 (LLM 看到的, 与 ALL_TOOLS 注册名一致)
    input_schema: Pydantic v2 model class, 用于校验 LLM 传入的 input
    description: callable(ctx) → str, 动态生成 (可含 graph 状态 hint)
    call: async (input_instance, ctx) → str, 返回给 LLM 的纯文本结果
    is_readonly: True = 不 mutate state (Wave C.2 之后用于跳过 graph snapshot)
    is_destructive: True = 删 node / 改大 graph (Wave 9.x HITL gate)
    requires_hitl: True = 调前必须用户确认 (Wave 9.x)
    """

    name: str
    input_schema: type[BaseModel]
    description: Callable[[ToolContext], str]
    call: Callable[[BaseModel, ToolContext], Awaitable[str]]
    is_readonly: bool = False
    is_destructive: bool = False
    requires_hitl: bool = False


# ───────────────────────── expand_tool ─────────────────────────

class _ExpandInput(BaseModel):
    l1_id: str | None = Field(
        default=None,
        description="L1 node id; None=auto-pick frontier (lowest consistency).",
    )
    direction: Literal["downward", "upward", "auto"] = Field(
        default="auto",
        description=(
            "downward 加 manifests_as L0 子节点 (requires l1_id); "
            "upward 加 incoming causes driver (frontier only — l1_id 必须是 frontier L1 "
            "无 incoming causes); "
            "auto: 有 l1_id → downward; 无 l1_id → upward auto-pick frontier."
        ),
    )


def _expand_description(ctx: ToolContext) -> str:
    g = ctx.state.graph
    n_l0 = sum(1 for n in g.nodes.values() if n.abstraction_level == 0)
    n_l1 = sum(1 for n in g.nodes.values() if n.abstraction_level == 1)
    n_l2 = sum(1 for n in g.nodes.values() if n.abstraction_level == 2)
    return (
        "扩展 L1 节点. direction=downward 加 manifests_as L0 子节点; "
        "upward 加 incoming causes driver (L2); "
        "auto 由 tool 决定 (l1_id 指定 → downward, 否则 upward + auto-pick frontier). "
        f"Current graph: {n_l0} L0, {n_l1} L1, {n_l2} L2."
    )


def _pick_frontier_l1(state: CognitiveState) -> str | None:
    """Auto-pick frontier L1 (no incoming causes, not decayed).

    Priority: low consistency_score first (if cache available), fallback insertion order.
    cache 来自 state.last_acceptance_report (Wave 2 reflect 写入); 没跑过 → None,
    所有候选 score=1.0, sort 退化为稳定的 insertion order.
    """
    has_incoming_causes: set[str] = {
        e.target_node
        for e in state.graph.edges.values()
        if e.relation_type == "causes"
    }
    cache = state.last_acceptance_report  # may be None (not yet checked)
    candidates: list[tuple[str, float]] = []
    for nid, n in state.graph.nodes.items():
        if (
            n.abstraction_level == 1
            and n.lifecycle_state != "decayed"
            and nid not in has_incoming_causes
        ):
            score = cache.per_l1.get(nid, 1.0) if cache else 1.0
            candidates.append((nid, score))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1])  # low consistency first
    return candidates[0][0]


async def _expand_call(input: BaseModel, ctx: ToolContext) -> str:
    assert isinstance(input, _ExpandInput)
    if ctx.llm is None:
        return "expand failed: no LLM client in context"

    direction = input.direction
    l1_id = input.l1_id

    # auto 模式: 有 l1_id → downward; 无 → upward (auto-pick frontier)
    if direction == "auto":
        direction = "downward" if l1_id else "upward"

    if direction == "downward":
        if not l1_id:
            return "expand downward 需要指定 l1_id (没有 auto-pick downward 语义)."
        try:
            new_ids = await expansion.expand_downward(ctx.state, l1_id, ctx.llm)
        except ValueError as exc:
            return f"expand downward 失败: {exc}"
        return f"expanded {l1_id} downward, added L0: {new_ids}"

    # direction == "upward"
    if not l1_id:
        l1_id = _pick_frontier_l1(ctx.state)
        if l1_id is None:
            return "expand upward: 没有可用 frontier L1 (所有 L1 都已有 incoming causes 或 decayed)."
    try:
        new_ids, gain = await expansion.expand_one_frontier(ctx.state, l1_id, ctx.llm)
    except ValueError as exc:
        return f"expand upward 失败: {exc}"
    return f"expanded {l1_id} upward, added drivers: {new_ids} (gain={gain:.2f})"


expand_tool = Tool(
    name="expand",
    input_schema=_ExpandInput,
    description=_expand_description,
    call=_expand_call,
)


# ───────────────────────── compress_tool ─────────────────────────

class _CompressInput(BaseModel):
    """compress 不需任何参数 (扫全 graph L0 → 出 L1 候选)."""

    pass


def _compress_description(ctx: ToolContext) -> str:
    g = ctx.state.graph
    n_l0 = sum(1 for n in g.nodes.values() if n.abstraction_level == 0)
    return (
        f"把所有 L0 observations 抽象成 L1 candidates (Phase 4 compression). "
        f"副作用: 加 L1 节点到 graph + 写 state.insight_candidates. Current: {n_l0} L0."
    )


async def _compress_call(input: BaseModel, ctx: ToolContext) -> str:
    assert isinstance(input, _CompressInput)
    if ctx.llm is None:
        return "compress failed: no LLM client in context"
    from explain_engine.engines.lexicon import get_lexicon_top_k_for_compress
    from explain_engine.persistence.storage_v2 import StorageV2
    top_k = get_lexicon_top_k_for_compress(StorageV2(), k=20)
    await compression.propose_candidates(ctx.state, ctx.llm, existing_lexicon=top_k)
    candidates = ctx.state.insight_candidates
    return f"compressed, {len(candidates)} L1 candidates: {candidates}"


compress_tool = Tool(
    name="compress",
    input_schema=_CompressInput,
    description=_compress_description,
    call=_compress_call,
)


# ───────────────────────── check_tool ─────────────────────────

class _CheckInput(BaseModel):
    target_id: str | None = Field(
        default=None,
        description="None = 跑全 graph aggregate_acceptance; 指定 id = 跑单 target consistency check.",
    )


def _check_description(ctx: ToolContext) -> str:
    return (
        "跑 multi-signal acceptance (target_id=None, 全 graph 聚合) "
        "或单 target consistency check (指定 L1/L2 id). Read-only, 不调 LLM."
    )


async def _check_call(input: BaseModel, ctx: ToolContext) -> str:
    assert isinstance(input, _CheckInput)
    if input.target_id is None:
        report = aggregate_acceptance(ctx.state)
        return (
            f"acceptance: avg_consistency={report.avg_consistency:.3f}, "
            f"avg_essentialness={report.avg_essentialness:.3f}, "
            f"weak_chain_l1s={report.weak_chain_l1s}, "
            f"rollout_coverage={report.rollout_coverage:.3f}, "
            f"missing_l0={report.missing_l0}"
        )
    try:
        r = check_consistency(ctx.state, input.target_id)
    except ValueError as exc:
        return f"check 失败: {exc}"
    return (
        f"target {input.target_id}: consistency={r.consistency_score:.3f}, "
        f"essentialness={r.essentialness_score:.3f}, "
        f"weak_chains={r.weak_chains}, reachable_L0={r.reachable_L0}"
    )


check_tool = Tool(
    name="check",
    input_schema=_CheckInput,
    description=_check_description,
    call=_check_call,
    is_readonly=True,
)


# ───────────────────────── predict_tool ─────────────────────────

class _PredictInput(BaseModel):
    intervention_text: str = Field(
        min_length=1,
        description="自然语言 intervention, e.g. '如果加入 X 因素' '假设 Y 突然增加'.",
    )


def _predict_description(ctx: ToolContext) -> str:
    return (
        "Forward prediction: 给自然语言 intervention 加新 concept 进 graph, "
        "LLM 生 predicted L0 manifest, 跑 propagate. 副作用: 改 state.graph."
    )


async def _predict_call(input: BaseModel, ctx: ToolContext) -> str:
    assert isinstance(input, _PredictInput)
    if ctx.llm is None:
        return "predict failed: no LLM client in context"
    try:
        report = await prediction.predict(ctx.state, input.intervention_text, ctx.llm)
    except ValueError as exc:
        return f"predict 失败: {exc}"
    # propagation_acts 可能很大, 只显示 top-5 by activation
    top_acts = dict(
        sorted(report.propagation_acts.items(), key=lambda kv: -kv[1])[:5]
    )
    return (
        f"prediction: new_nodes={report.new_node_ids}, "
        f"predicted_L0={report.predicted_L0_ids}, "
        f"activated_existing_L0={report.activated_existing_L0}, "
        f"top_propagation_acts={top_acts}"
    )


predict_tool = Tool(
    name="predict",
    input_schema=_PredictInput,
    description=_predict_description,
    call=_predict_call,
)


# ───────────────────────── counterfactual_tool ─────────────────────────

class _CounterfactualInput(BaseModel):
    intervention_text: str = Field(
        min_length=1,
        description="自然语言 counterfactual, e.g. '如果移除 Y' 或 '用 Z 替代 Y'.",
    )


def _counterfactual_description(ctx: ToolContext) -> str:
    return (
        "Counterfactual: 自然语言 intervention 描述 remove / substitute, "
        "深拷贝 graph 跑 propagate (无副作用), 返回 baseline vs counterfactual diff."
    )


async def _counterfactual_call(input: BaseModel, ctx: ToolContext) -> str:
    assert isinstance(input, _CounterfactualInput)
    if ctx.llm is None:
        return "counterfactual failed: no LLM client in context"
    try:
        report = await counterfactual.substitute(
            ctx.state, input.intervention_text, ctx.llm,
        )
    except ValueError as exc:
        return f"counterfactual 失败: {exc}"
    # activation_diff 可能很大, 只显示 top-5 by abs diff
    top_diff = dict(
        sorted(
            report.activation_diff.items(),
            key=lambda kv: -abs(kv[1]),
        )[:5]
    )
    summary = (
        f"counterfactual: removed={report.removed_node_ids}, "
        f"added={report.added_node_ids}, "
        f"added_predicted_L0={report.added_predicted_L0_ids}, "
        f"top_activation_diff={top_diff}"
    )
    if report.alt_narrative:
        summary += f", narrative={report.alt_narrative!r}"
    return summary


counterfactual_tool = Tool(
    name="counterfactual",
    input_schema=_CounterfactualInput,
    description=_counterfactual_description,
    call=_counterfactual_call,
)


# ─── add_observation tool (Phase 9 Wave B.2 mutation) ───

def _next_p_id(state: CognitiveState) -> str:
    """Compute next p_NNN id by incrementing max existing p_<digits> id."""
    existing = [
        int(nid.split("_")[1]) for nid in state.graph.nodes
        if nid.startswith("p_") and nid[2:].isdigit()
    ]
    return f"p_{(max(existing) + 1) if existing else 1:03d}"


class _AddObservationInput(BaseModel):
    name: str = Field(min_length=1, description="新观察简短名字")
    description: str = Field(min_length=1, description="详细描述")
    source: Literal["user_explicit", "llm_inferred"] = Field(
        description=(
            "user_explicit = user 明确说加; "
            "llm_inferred = 你自己分析推断. "
            "两者均直接执行 (Phase X2 全采纳)."
        )
    )


async def _add_observation_call(input: BaseModel, ctx: ToolContext) -> str:
    """Add L0 node to graph. HITL gate (Task D.1) intercepts before this for llm_inferred."""
    assert isinstance(input, _AddObservationInput)
    nid = _next_p_id(ctx.state)
    # source field reflects authoring agent: user_explicit → user-typed it;
    # llm_inferred → LLM proposed (may be HITL-confirmed by Task D.1, but author remains LLM)
    node_source = "user" if input.source == "user_explicit" else "llm"
    ctx.state.graph.add_node(VariableNode(
        id=nid, name=input.name, description=input.description,
        abstraction_level=0, confidence=0.7, epistemic="observation",
        source=node_source,
    ))
    return (
        f"added L0 observation {nid}: {input.name} "
        f"(source={input.source}, node_source={node_source})"
    )


add_observation_tool = Tool(
    name="add_observation",
    input_schema=_AddObservationInput,
    description=lambda ctx: (
        "添加新 L0 observation 到 graph. "
        "若 user 明确说'加这个观察' → source='user_explicit'; "
        "若你自己分析推断该加 → source='llm_inferred'. "
        "两者均会直接执行 (Phase X2 全采纳), 用户可 /review 事后修订."
    ),
    call=_add_observation_call,
    requires_hitl=True,  # Conditional gate; chat/hitl.py checks source
)


# ─── read_node tool (Phase 9 Wave B.2 lazy load) ───

class _ReadNodeInput(BaseModel):
    node_id: str = Field(min_length=1)


async def _read_node_call(input: BaseModel, ctx: ToolContext) -> str:
    assert isinstance(input, _ReadNodeInput)
    node = ctx.state.graph.nodes.get(input.node_id)
    if node is None:
        return f"node {input.node_id!r} not found in graph"
    # Soft cap description to prevent context bloat (LLM chain reads via this tool).
    # Task E.1 microCompact will GC stale tool results, but per-call safety helps too.
    desc = node.description
    MAX_DESC = 2000
    if len(desc) > MAX_DESC:
        desc = (
            desc[:MAX_DESC]
            + f"... [truncated {len(node.description) - MAX_DESC} chars; "
            f"full content {len(node.description)} chars total]"
        )
    return (
        f"{node.id} | {node.name} | level={node.abstraction_level} | "
        f"confidence={node.confidence:.2f} | epistemic={node.epistemic} | "
        f"lifecycle_state={node.lifecycle_state}\n"
        f"description: {desc}"
    )


read_node_tool = Tool(
    name="read_node",
    input_schema=_ReadNodeInput,
    description=lambda ctx: "读节点完整 description (lean context mode lazy load 用).",
    call=_read_node_call,
    is_readonly=True,
)


# ─── web_search tool (联网搜索, 免费无 key DuckDuckGo) ───

class _WebSearchInput(BaseModel):
    query: str = Field(min_length=1, description="搜索关键词或问题 (自然语言均可)")
    max_results: int = Field(
        default=5, ge=1, le=10, description="返回结果条数 (1-10), 默认 5"
    )


async def _web_search_call(input: BaseModel, ctx: ToolContext) -> str:
    assert isinstance(input, _WebSearchInput)
    from explain_engine.chat.web_search import web_search
    try:
        results = await web_search(input.query, input.max_results)
    except Exception as exc:
        return f"web_search 失败: {type(exc).__name__}: {exc}"
    if not results:
        return f"web_search '{input.query}': 无结果"
    lines = [f"web_search '{input.query}' — {len(results)} 条结果:"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.title}\n    {r.url}\n    {r.snippet}")
    return "\n".join(lines)


web_search_tool = Tool(
    name="web_search",
    input_schema=_WebSearchInput,
    description=lambda ctx: (
        "联网搜索实时/事实信息 (DuckDuckGo, 免费无 key). 适用于模型知识截止后的"
        "新事件、最新数据 (经济数据/股价/新闻等). 返回 标题+URL+摘要; 摘要不够时"
        "再用 web_read 取具体页面正文."
    ),
    call=_web_search_call,
    is_readonly=True,
)


# ─── web_read tool (抓取网页正文, 免费) ───

class _WebReadInput(BaseModel):
    url: str = Field(min_length=1, description="要抓取正文的网页 URL (通常来自 web_search)")


async def _web_read_call(input: BaseModel, ctx: ToolContext) -> str:
    assert isinstance(input, _WebReadInput)
    from explain_engine.chat.web_search import web_read
    try:
        text = await web_read(input.url)
    except Exception as exc:
        return f"web_read 失败: {type(exc).__name__}: {exc}"
    return f"网页正文 {input.url}:\n{text}"


web_read_tool = Tool(
    name="web_read",
    input_schema=_WebReadInput,
    description=lambda ctx: (
        "抓取指定 URL 的网页正文纯文本 (免费, 去脚本/导航, 截断 ~4000 字). "
        "一般先 web_search 拿 URL, 再用本工具读全文细节."
    ),
    call=_web_read_call,
    is_readonly=True,
)


# ───────────────────────── master registry ─────────────────────────

ALL_TOOLS: tuple[Tool, ...] = (
    expand_tool,
    compress_tool,
    check_tool,
    predict_tool,
    counterfactual_tool,
    add_observation_tool,
    read_node_tool,
    web_search_tool,
    web_read_tool,
)
