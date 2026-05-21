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

per-turn: {per_turn_display}
per-session: {per_session_display}

# Session memory

{memory_section}
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


def _render_id_list(ids: list[str], max_inline: int = 8) -> str:
    """Render a list of node IDs. If <= max_inline, show all; else show first 3 + last 1 + count."""
    if not ids:
        return "[]"
    if len(ids) <= max_inline:
        return "[" + ", ".join(ids) + "]"
    # Long list — show prefix + suffix + ellipsis
    return f"[{ids[0]}, {ids[1]}, {ids[2]}, ..., {ids[-1]} ({len(ids)} total)]"


def _render_graph_summary(state: CognitiveState) -> str:
    """Compact graph stats: counts + actual node ID lists + lifecycle stats.

    Bug 1 fix: previously only counts ("12 L0, 4 L1") were exposed, forcing LLM to
    guess ID format (it would invent 'L0_1', 'L0_2' etc instead of real 'p_001').
    Now includes truncated ID list so LLM can call read_node / expand etc with
    correct IDs without guessing.
    """
    g = state.graph
    l0_ids = sorted(
        nid for nid, n in g.nodes.items() if n.abstraction_level == 0
    )
    l1_ids = sorted(
        nid for nid, n in g.nodes.items() if n.abstraction_level == 1
    )
    l2_ids = sorted(
        nid for nid, n in g.nodes.items() if n.abstraction_level == 2
    )
    n_decayed = sum(
        1 for n in g.nodes.values()
        if getattr(n, "lifecycle_state", "active") == "decayed"
    )
    n_stale = sum(
        1 for n in g.nodes.values()
        if getattr(n, "lifecycle_state", "active") == "stale"
    )
    return (
        f"Graph: {len(g.nodes)} nodes\n"
        f"  L0 ({len(l0_ids)}): {_render_id_list(l0_ids)}\n"
        f"  L1 ({len(l1_ids)}): {_render_id_list(l1_ids)}\n"
        f"  L2 ({len(l2_ids)}): {_render_id_list(l2_ids)}\n"
        f"  lifecycle: {n_decayed} decayed, {n_stale} stale\n"
        f"  ID format: L0=p_NNN, L1=c_NNN, L2=d_NNN (zero-padded 3 digits)"
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


def _render_memory_section(memory_md: str) -> str:
    """Render session memory section.

    E.1 fix: 原来只显示 'session_memory.md 含 N chars, 已 splice 到对话历史前置',
    但 splice 实际产 {role:'system'} msg 会被 loop._transcript_to_messages
    过滤, memory 内容从未到 LLM. 改: 这里直接把 memory_md 全文内联进
    sys_prompt (走 Anthropic native system 参数, 不受 messages 数组 role
    限制), 让 LLM 真正看到 memory 决策上下文.
    """
    if not memory_md:
        return (
            "(no session memory yet; this is a fresh session or no "
            "compactions have run)"
        )
    return memory_md


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
        memory_md: session_memory.md 内容 (Wave E D.2 hook 产出). E.1 fix:
            非空时全文内联进 sys_prompt (而非仅显示 chars 计数). 因为原
            splice 方案产 {role:'system'} msg 会被 _transcript_to_messages
            过滤掉 → memory 从未到 LLM. 走 Anthropic native system 参数
            才能保证 memory 到达.
        budget: dict with 4 keys: per_turn_remaining/limit + per_session_remaining/limit
        hint: Wave D.2 — 上一 turn reflect_post_turn 留的 hint (e.g. "reflect 建议:
            expand-downward (target=c_002)"). None → 不渲染 hint section. caller
            (query_loop) 用完后清 chat.next_turn_hint = None (one-shot consumption).

    Returns:
        完整 system prompt 字符串, 直接喂给 LLM API 的 system param.
    """
    ctx = ToolContext(state=state)
    hint_section = f"\n# Last reflect hint\n{hint}\n" if hint else ""

    # 2026-05-20 hotfix: 渲 budget — limit=0 (unlimited) 显 "unlimited (used K)",
    # finite 显 "{remaining}/{limit} remaining". 不直接喂 LLM 原始负数 (会 confuse).
    def _budget_display(remaining: int, limit: int) -> str:
        if limit == 0:
            used = max(0, -remaining)
            return f"unlimited (used {used})"
        return f"{remaining}/{limit} remaining"

    return SYSTEM_PROMPT_TEMPLATE.format(
        tool_count=len(ALL_TOOLS),
        tool_catalog=_render_tool_catalog(ctx),
        question=question,
        graph_summary=_render_graph_summary(state),
        multi_signal_summary=_render_multi_signal(state),
        per_turn_display=_budget_display(
            budget["per_turn_remaining"], budget["per_turn_limit"],
        ),
        per_session_display=_budget_display(
            budget["per_session_remaining"], budget["per_session_limit"],
        ),
        memory_section=_render_memory_section(memory_md),
        hint_section=hint_section,
    )
