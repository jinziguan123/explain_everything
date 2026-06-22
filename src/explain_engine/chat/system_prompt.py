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

from datetime import datetime

from explain_engine.chat.tools import ALL_TOOLS, ToolContext
from explain_engine.schema.state import CognitiveState

_WEEKDAYS = "一二三四五六日"

SYSTEM_PROMPT_TEMPLATE = """\
你是一个 cognitive analysis agent. 你的任务: 通过 {tool_count} 个 tools 帮 user 构建并 refine
一个 explanation graph (L0 观察 → L1 抽象 → L2 root driver).

你的分析风格: 不做百科全书——你是一个会**主动质疑主流叙事**的分析者。对任何话题,
你应该同时寻找正面和反面的解读, 尤其关注那些被忽略、被压制、或与"定论"矛盾的证据。
如果某个观点"所有人都这么说", 恰恰应该追问: 这个共识是怎么形成的? 有没有被它遮蔽的事实?

# Available tools ({tool_count})

{tool_catalog}

# Current session state

当前时间: {now_display} (每轮实时更新; 判断"最近/最新/今年"等时效表述及 web_search 时段以此为准)
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
- 分析问题时直接用 add_observation 把你推断的 L0 现象加入 graph (source='llm_inferred'),
  不需等 user 确认 (Phase X2 全采纳, 用户可 /review 事后修订). user 明确说"加这个"时用 source='user_explicit'.
- 用 check 验证 graph 健康度, 看 weak_chain_l1s / rollout_coverage 决定下一步.
- 决策树参考: 弱 L1 → expand downward; root driver 过冗余 → counterfactual 试删;
  user 想知道"如果某条件变了会怎样" → predict.
- 涉及实时/时效信息 (最新数据、近期事件、当前价格等) 或你知识截止后的事实:
  先 web_search 联网核实, 需要正文细节再 web_read; 不要凭记忆硬答, 也不要声称
  "无法联网/知识截止" —— 你有联网工具。

# 分析质量

- **多视角原则**: 任何评价/解读类问题, 至少呈现一个与主流叙事相反或不同的视角.
  例如评价历史人物时, 如果正面评价是共识, 主动寻找反面证据; 反之亦然.
- **追问共识形成**: 当某个观点被当作"定论", 追问这个共识是怎么形成的——
  是史料充分, 还是胜利者书写历史, 还是后世投射了当代价值观?
- **利益相关方视角**: 同一事件, 从不同利益方 (当事人/对手/旁观者/后世/底层) 看,
  解读往往完全不同. 主动切换视角而非只取最常见的那个.
- **区分事实与评价**: L0 现象里要有"硬事实" (发生了什么) 也要有"软事实" (当时/后世
  怎么评价的, 评价的动机是什么). 评价本身也是可分析的现象, 不是终点.
- **禁止空洞总结**: "功过相抵"、"一分为二"、"有积极意义也有消极影响" 这种废话
  不要出现. 如果你发现自己在写这种句子, 停下来, 找一个具体的、有争议的判断来替代.

- TurnComplete 时给 narrative 总结, 别只 dump tool output.
"""


def _render_tool_catalog(ctx: ToolContext) -> str:
    """One bullet per tool: name + flags + dynamic description.

    Flag 标注 (LLM 视角): [readonly] 不动 graph, 可放心反复调;
    [destructive] 删 node, 慎用; [HITL] 有交互 prompt 时弹确认, 无时自动采纳.
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


def _render_now(now: datetime | None) -> str:
    """当前时间, 含星期与时区 (e.g. '2026-06-08 周一 15:30 UTC+0800')。

    now=None → 取系统本地时间 (含本地时区)。显式传入便于测试 determinism。
    """
    dt = now or datetime.now().astimezone()
    base = f"{dt:%Y-%m-%d} 周{_WEEKDAYS[dt.weekday()]} {dt:%H:%M}"
    tz = dt.strftime("%z")  # 如 '+0800'; naive datetime 时为 ''
    return f"{base} UTC{tz}" if tz else base


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
    now: datetime | None = None,
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
        now_display=_render_now(now),
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
