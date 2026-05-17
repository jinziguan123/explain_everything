"""Phase 9 Wave C.2: dynamic system prompt assembly.

每 turn 把 system prompt 重新拼装一遍, 这样:
- tool 目录的 description 能反映 latest graph 状态 (e.g. "current: 5 L0, 2 L1, 1 L2")
- LLM 看到的 graph summary / multi-signal / budget 都是最新值
- memory.md 内容变化 (Wave E micro-compact 后) 会立刻 reflected

参考 Claude Code 的 fetchSystemPromptParts pattern, 把 prompt 拆成 6 part
(role / tools / state / budget / memory / guidelines), 模板 format 出最终字符串.

参考 docs/plans/2026-05-17-conversational-cognitive-engine-design.md.
参考 docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave C.2.
"""

from __future__ import annotations

from explain_engine.chat.tools import ALL_TOOLS, ToolContext
from explain_engine.schema.state import CognitiveState

SYSTEM_PROMPT_TEMPLATE = """\
你是一个 cognitive analysis agent. 你的任务: 通过 {tool_count} 个 tools 帮 user 构建并 refine
一个 explanation graph (L0 观察 → L1 抽象 → L2 root driver).

# Available tools ({tool_count})

{tool_catalog}

# Current session state

Question: {question}
{graph_summary}
{multi_signal_summary}

# Budget

per-turn remaining: {per_turn_remaining} / {per_turn_limit}
per-session remaining: {per_session_remaining} / {per_session_limit}

# Memory hint
{memory_hint}
{hint_section}
# Guidelines

- 优先用 read_node 看节点完整 description, 别要求 user 重复信息.
- 加 observation 时区分 source: "user_explicit" (user 明确说加) vs "llm_inferred" (你自己推断).
- 用 check 验证 graph 健康度, 看 weak_chain_l1s / rollout_coverage 决定下一步.
- 决策树参考: 弱 L1 → expand downward; root driver 过冗余 → counterfactual 试删;
  user 想知道"如果加 X" → predict.
- TurnComplete 时给 narrative 总结, 别只 dump tool output.
"""


def _render_tool_catalog(ctx: ToolContext) -> str:
    """One bullet per tool: name + flags + dynamic description.

    Flag 标注 (LLM 视角): [readonly] 不动 graph, 可放心反复调;
    [destructive] 删 node, 慎用; [HITL] 调前等 user 确认.
    """
    lines = []
    for tool in ALL_TOOLS:
        desc = tool.description(ctx)
        flags = []
        if tool.is_readonly:
            flags.append("readonly")
        if tool.is_destructive:
            flags.append("destructive")
        if tool.requires_hitl:
            flags.append("HITL")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"- `{tool.name}`{flag_str}: {desc}")
    return "\n".join(lines)


def _render_graph_summary(state: CognitiveState) -> str:
    """Compact graph stats: counts by abstraction_level + lifecycle stats."""
    g = state.graph
    n_l0 = sum(1 for n in g.nodes.values() if n.abstraction_level == 0)
    n_l1 = sum(1 for n in g.nodes.values() if n.abstraction_level == 1)
    n_l2 = sum(1 for n in g.nodes.values() if n.abstraction_level == 2)
    n_decayed = sum(
        1 for n in g.nodes.values()
        if getattr(n, "lifecycle_state", "active") == "decayed"
    )
    n_stale = sum(
        1 for n in g.nodes.values()
        if getattr(n, "lifecycle_state", "active") == "stale"
    )
    return (
        f"Graph: {len(g.nodes)} nodes ({n_l0} L0 / {n_l1} L1 / {n_l2} L2), "
        f"lifecycle: {n_decayed} decayed, {n_stale} stale"
    )


def _render_multi_signal(state: CognitiveState) -> str:
    """Multi-signal acceptance summary if available; hint else."""
    report = state.last_acceptance_report
    if report is None:
        return "Multi-signal: not yet computed (run `check` to populate)"
    return (
        f"Multi-signal: avg_consistency={report.avg_consistency:.3f}, "
        f"weak_chain_l1s={report.weak_chain_l1s}, "
        f"rollout_coverage={report.rollout_coverage:.3f}"
    )


def assemble_system_prompt(
    state: CognitiveState,
    question: str,
    memory_md: str,
    budget: dict,
    hint: str | None = None,
) -> str:
    """Build per-turn system prompt (called from query_loop each iteration).

    Args:
        state: 当前 CognitiveState (for graph snapshot + tool dynamic desc)
        question: root question (从 SessionMeta.question 来)
        memory_md: session_memory.md 内容 (Wave E micro-compact 产出, 暂时空字符串)
        budget: dict with 4 keys: per_turn_remaining/limit + per_session_remaining/limit
        hint: Wave D.2 — 上一 turn reflect_post_turn 留的 hint (e.g. "reflect 建议:
            expand-downward (target=c_002)"). None → 不渲染 hint section. caller
            (query_loop) 用完后清 chat.next_turn_hint = None (one-shot consumption).

    Returns:
        完整 system prompt 字符串, 直接喂给 LLM API 的 system param.
    """
    ctx = ToolContext(state=state)
    memory_hint = (
        f"(session_memory.md 含 {len(memory_md)} chars, 已 splice 到对话历史前置)"
        if memory_md
        else "(无 session_memory yet)"
    )
    hint_section = f"\n# Last reflect hint\n{hint}\n" if hint else ""
    return SYSTEM_PROMPT_TEMPLATE.format(
        tool_count=len(ALL_TOOLS),
        tool_catalog=_render_tool_catalog(ctx),
        question=question,
        graph_summary=_render_graph_summary(state),
        multi_signal_summary=_render_multi_signal(state),
        per_turn_remaining=budget["per_turn_remaining"],
        per_turn_limit=budget["per_turn_limit"],
        per_session_remaining=budget["per_session_remaining"],
        per_session_limit=budget["per_session_limit"],
        memory_hint=memory_hint,
        hint_section=hint_section,
    )
