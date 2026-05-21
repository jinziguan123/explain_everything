"""Phase 9 Wave F.1 + Phase 11 Wave 3/4: 17 default slash commands + 1 alias.

设计参考 Claude Code 同款 slash 模式 — 本地 intercept 不走 LLM,
廉价 inspection + exit + force compact 等管理命令; slash 不计入
transcript / turn_count (因为非真正 user→assistant 对话).

每个 SlashCommand:
- name: str (e.g. "quit")
- description: str (shown in /help)
- handler: async (chat, args: list[str]) -> list[ChatEvent]
  返 list[ChatEvent] (multiple events 可能, /new + /resume 同时 yield
  slash_{new,resume} + slash_switch_session).

设计参考 docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave F.1
+ docs/plans/2026-05-18-chat-new-resume-slash-plan.md Wave 3 + Wave 4
+ docs/plans/2026-05-18-phase11-repl-unification-plan.md Wave 3 + Wave 4
  (cli subcommand → slash: /compress /run /check /predict /counterfactual /rescore + /cf
   Wave 4: /list /lexicon /migrate — cross-session, ephemeral 也 work).
"""

from __future__ import annotations

# Phase 12 /graph: tmpdir lifecycle. aliased _atexit/_shutil/_tempfile 让
# test 可 monkeypatch sc._atexit.register (验 atexit 注册).
import atexit as _atexit
import shutil as _shutil
import tempfile as _tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from explain_engine.chat.slash_stage_rules import with_stage_gate

if TYPE_CHECKING:
    from explain_engine.chat.session import ChatEvent, ChatSession


@dataclass
class SlashCommand:
    """Single slash command registry entry.

    name: 命令名 (不带 / 前缀)
    description: /help 时展示给用户
    handler: async (chat, args) → list[ChatEvent]
        当前所有 handler 都 single event; list 形式留给未来扩展
        (e.g. /show 可能想分多 event 流式输出).
    """

    name: str
    description: str
    handler: Callable[[ChatSession, list[str]], Awaitable[list[ChatEvent]]]


def _render_table_to_string(table) -> str:
    """Phase 11 Wave 4: Rich Table → str (no terminal) DRY helper.

    用 force_terminal=False + Console(file=StringIO) 让 Rich 渲染到内存,
    供 slash_{list,lexicon} 把 table 内容塞进 ChatEvent.content (str).
    width 固 120 避终端宽度变化导致 test flake.
    """
    from io import StringIO

    from rich.console import Console as _Console

    buf = StringIO()
    _Console(file=buf, force_terminal=False, width=120).print(table)
    return buf.getvalue()


async def _handle_quit(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Signal CLI to exit. Actual exit handled by F.2 CLI wrapper.

    本 handler 只 yield slash_quit event; CLI loop (F.2) 收到该 event
    后跳出 input loop, 调 chat.aclose() flush 后退出.
    """
    from explain_engine.chat.session import ChatEvent
    return [ChatEvent(type="slash_quit", content="Goodbye. Session saved.")]


async def _handle_help(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """List slash commands + tools (含 readonly / HITL flags)."""
    from explain_engine.chat.session import ChatEvent
    from explain_engine.chat.tools import ALL_TOOLS

    lines = ["Available slash commands (local, bypass LLM):"]
    for cmd in DEFAULT_COMMANDS:
        lines.append(f"  /{cmd.name} — {cmd.description}")
    lines.append("")
    lines.append("Available tools (LLM-callable):")
    for tool in ALL_TOOLS:
        flags = []
        if tool.is_readonly:
            flags.append("readonly")
        if tool.requires_hitl:
            flags.append("HITL")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"  {tool.name}{flag_str}")
    return [ChatEvent(type="slash_help", content="\n".join(lines))]


async def _handle_show(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 12 (2026-05-19): /show 全展开 graph + multi-signal acceptance.

    输出 4 个 section (Session → Graph → Edges → Multi-signal). 详见
    docs/plans/2026-05-19-slash-show-graph-detail-design.md.

    aggregate_acceptance 是 readonly (Phase 2 simulation), 不调 LLM, 廉价.
    包 try/except — graph 空 / no L1 / 其他 edge case 不应 crash inspection 命令.
    """
    from explain_engine.chat.session import ChatEvent
    from explain_engine.engines.simulation import aggregate_acceptance

    state = chat.state
    g = state.graph
    n_l0 = sum(1 for n in g.nodes.values() if n.abstraction_level == 0)
    n_l1 = sum(1 for n in g.nodes.values() if n.abstraction_level == 1)
    n_l2 = sum(1 for n in g.nodes.values() if n.abstraction_level == 2)
    n_decayed = sum(1 for n in g.nodes.values() if n.lifecycle_state == "decayed")
    n_stale = sum(1 for n in g.nodes.values() if n.lifecycle_state == "stale")

    # ─── Multi-signal 提前算 (weak_chain_l1s 要传给 L1 section 给 (weak) marker)
    weak_l1_set: set[str] = set()
    report = None
    agg_err: str | None = None
    try:
        report = aggregate_acceptance(state)
        weak_l1_set = set(report.weak_chain_l1s or [])
    except Exception as exc:
        agg_err = type(exc).__name__

    lines: list[str] = []

    # ═══ Section 1: Session ═══
    lines.append("=== Session ===")
    lines.append(f"SID:      {chat.sid}")
    lines.append(f"Question: {chat._session.meta.question}")
    lines.append(f"Stage:    {chat._session.meta.stage}")
    lines.append("")

    # ═══ Section 2: Graph (node tree by L) ═══
    lines.append(
        f"=== Graph ({len(g.nodes)} nodes: {n_l0} L0 / {n_l1} L1 / {n_l2} L2; "
        f"{n_decayed} decayed, {n_stale} stale) ==="
    )
    lines.append("")

    if len(g.nodes) == 0:
        lines.append("(empty)")
        lines.append("")
    else:
        # L0 section
        if n_l0 > 0:
            lines.append(f"[L0 Observations] ({n_l0})")
            for nid in sorted(n.id for n in g.nodes.values() if n.abstraction_level == 0):
                lines.append(f"  {_format_node_brief(state, nid, weak=nid in weak_l1_set)}")
            lines.append("")

        # L1 section (with weak chain header inline)
        if n_l1 > 0:
            l1_header = f"[L1 Concepts] ({n_l1})"
            l1_weak = [n.id for n in g.nodes.values()
                       if n.abstraction_level == 1 and n.id in weak_l1_set]
            if l1_weak:
                l1_header += f" — weak chain: {' '.join(sorted(l1_weak))}"
            lines.append(l1_header)
            for nid in sorted(n.id for n in g.nodes.values() if n.abstraction_level == 1):
                lines.append(f"  {_format_node_brief(state, nid, weak=nid in weak_l1_set)}")
            lines.append("")

        # L2 section (always shown — explicit "(none)" when zero)
        lines.append(f"[L2 Drivers] ({n_l2})")
        if n_l2 == 0:
            lines.append("  (none — 尚未 expand 出 root driver)")
        else:
            for nid in sorted(n.id for n in g.nodes.values() if n.abstraction_level == 2):
                lines.append(f"  {_format_node_brief(state, nid)}")
        lines.append("")

    # ═══ Section 3: Edges (group by relation_type) ═══
    lines.append(f"=== Edges ({len(g.edges)}) ===")
    lines.append("")
    if len(g.edges) == 0:
        lines.append("(no edges)")
        lines.append("")
    else:
        by_type: dict[str, list] = {}
        for e in g.edges.values():
            by_type.setdefault(e.relation_type, []).append(e)
        for rtype in sorted(by_type):
            edges = sorted(by_type[rtype], key=lambda e: (e.source_node, e.target_node))
            lines.append(f"{rtype} ({len(edges)}):")
            for edge in edges:
                lines.append(f"  {_format_edge_brief(edge)}")
            lines.append("")

    # ═══ Section 4: Multi-signal verdict ═══
    lines.append("=== Multi-signal acceptance ===")
    if report is not None:
        lines.append(f"avg_consistency:    {report.avg_consistency:.3f}")
        lines.append(f"avg_essentialness:  {report.avg_essentialness:.3f}")
        lines.append(f"rollout_coverage:   {report.rollout_coverage:.3f}")
        weak_ids = sorted(report.weak_chain_l1s or [])
        if weak_ids:
            lines.append(f"weak_chain_l1s ({len(weak_ids)}): {' '.join(weak_ids)}")
        else:
            lines.append("weak_chain_l1s: (none)")
        if report.input_alignment is not None:
            lines.append(f"input_alignment:    {report.input_alignment:.3f}")
    else:
        lines.append(f"(aggregate_acceptance failed: {agg_err})")

    return [ChatEvent(type="slash_show", content="\n".join(lines))]


async def _handle_graph(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 12 (2026-05-19): /graph — visual rendering via graphviz inline.

    Pipeline:
      1. Empty graph → return warning, no graphviz call.
      2. Check dot binary present, friendly error if missing.
      3. Build graphviz.Digraph from chat.state.graph (含 weak_l1 marker).
      4. Render PNG to session tmpdir.
      5. Detect terminal capability (iTerm/Kitty/chafa), inline display.
         若无 inline renderer 可用, 输 PNG path + install hint.
      6. Footer: PNG path + multi-signal verdict 4 行.

    设计详见 docs/plans/2026-05-19-slash-show-graph-detail-design.md §4.2.
    """
    import os
    import shutil
    import subprocess

    from explain_engine.chat.session import ChatEvent
    from explain_engine.engines.simulation import aggregate_acceptance

    state = chat.state
    g = state.graph

    # Edge: empty graph
    if len(g.nodes) == 0:
        return [ChatEvent(
            type="slash_graph",
            content="(empty graph, nothing to render)",
        )]

    # Edge: dot binary missing
    if shutil.which("dot") is None:
        return [ChatEvent(
            type="slash_graph",
            content=(
                "dot binary not found.\n"
                "Install: brew install graphviz"
            ),
        )]

    # Compute weak_l1_ids (gracefully handle agg failure — render still works)
    weak_l1_ids: set[str] = set()
    report = None
    try:
        report = aggregate_acceptance(state)
        weak_l1_ids = set(report.weak_chain_l1s or [])
    except Exception:
        pass

    # Build + render — dg.render() shells out to `dot`; even with dot binary
    # present (shutil.which check above), the subprocess can fail (segfault,
    # permission, disk full, dot version mismatch). Catch + report friendly
    # error instead of crashing the REPL turn.
    dg = _build_digraph(state, weak_l1_ids=weak_l1_ids)
    tmpdir = _get_session_tmpdir()
    tick = getattr(state, "tick", 0)
    base = os.path.join(tmpdir, f"graph_{chat.sid}_{tick}")
    try:
        png_path = dg.render(filename=base, cleanup=True)
    except Exception as exc:
        return [ChatEvent(
            type="slash_graph",
            content=(
                f"dot render 失败: {type(exc).__name__}: {exc}\n"
                "请检查 graphviz 安装 (brew reinstall graphviz) 或磁盘空间."
            ),
        )]

    # Header
    n_l0 = sum(1 for n in g.nodes.values() if n.abstraction_level == 0)
    n_l1 = sum(1 for n in g.nodes.values() if n.abstraction_level == 1)
    n_l2 = sum(1 for n in g.nodes.values() if n.abstraction_level == 2)
    header = (
        f"/graph tick={tick} · {len(g.nodes)} nodes "
        f"({n_l0} L0 / {n_l1} L1 / {n_l2} L2), {len(g.edges)} edges"
    )

    # Inline display
    cmd, renderer = _detect_inline_renderer(png_path)
    if cmd is not None:
        try:
            # stderr=DEVNULL: chafa/imgcat 任何 warning 不污染用户 terminal.
            # stdout=None: 让 chafa/imgcat 把 image bytes 写到 terminal (inline 显示必需).
            subprocess.run(cmd, check=False, stderr=subprocess.DEVNULL)
            inline_msg = f"(rendered inline via {renderer})"
        except Exception as exc:
            inline_msg = f"(inline render via {renderer} failed: {type(exc).__name__})"
    else:
        inline_msg = "(install chafa for inline preview: brew install chafa)"

    # Footer
    footer_lines = [
        "",
        inline_msg,
        f"PNG: {png_path}",
        "",
    ]
    if report is not None:
        footer_lines.append(
            f"Multi-signal: consistency={report.avg_consistency:.3f} "
            f"essentialness={report.avg_essentialness:.3f} "
            f"coverage={report.rollout_coverage:.3f}"
        )
        weak_ids = sorted(report.weak_chain_l1s or [])
        if weak_ids:
            footer_lines.append(f"weak L1: {' '.join(weak_ids)}")
    else:
        footer_lines.append("Multi-signal: (aggregate_acceptance failed)")

    content = header + "\n" + "\n".join(footer_lines)
    return [ChatEvent(type="slash_graph", content=content)]


def _format_budget_value(limit: int, remaining: int) -> str:
    """渲单轴 budget: '无限 (已用 K)' for unlimited, '{limit} (剩余 {remaining})' for finite.

    unlimited (limit==0) 时 remaining 走负值 tracking (BudgetCounter.consume 也扣),
    display 用 -remaining = 已用次数.
    """
    if limit == 0:
        used = max(0, -remaining)
        return f"无限 (已用 {used})"
    return f"{limit}  (剩余 {remaining})"


async def _handle_budget(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 11 Wave 2.5 + 2026-05-20 hotfix: interactive budget config.

    Display 当前 per-turn / per-session limit + remaining, 然后 sequential
    prompt 收 2 个新 limit. 用 chat.chat_state 直接读写 (而非 chat.budget
    BudgetCounter property), 让 EphemeralChatSession 也支持.

    2026-05-20 hotfix 行为变化:
    - 默 limit=0 = unlimited (新 session). Display "无限 (已用 K)".
    - 输 0 = 设 unlimited (有效输入, 不再报 < 1 error).
    - Commit 时 remaining = limit (full refill, 不是 min cap). 修 bug:
      用户 100k 已用尽再设 100k limit, 老逻辑 min(0, 100000)=0 还是 0;
      新逻辑直接 remaining=new_limit, "用尽后重设 = 重新授权".

    UX:
    - chat.input_provider is None: display-only (test / 非 REPL).
    - 输 'q' / 'quit': 取消, 不改.
    - 空输入: 保持原 limit.
    - 非数字 / < 0: 返 slash_error, 不改.
    """
    from rich.console import Console

    from explain_engine.chat.session import ChatEvent

    cs = chat.chat_state

    console = Console()
    console.print(
        f"\n[bold]Current budget[/bold]\n"
        f"  per-turn limit:    {_format_budget_value(cs.budget_per_turn_limit, cs.budget_per_turn_remaining)}\n"
        f"  per-session limit: {_format_budget_value(cs.budget_per_session_limit, cs.budget_per_session_remaining)}\n"
    )

    if chat.input_provider is None:
        return [ChatEvent(
            type="slash_budget",
            content="(no input_provider; display-only — test/non-REPL 路径)",
        )]

    # 收 per-turn
    try:
        new_turn_str = await chat.input_provider(
            f"新 per-turn limit (回车保持 {cs.budget_per_turn_limit}, 0=无限, q 取消): "
        )
    except (EOFError, KeyboardInterrupt):
        return [ChatEvent(type="slash_budget", content="已取消.")]

    new_turn_str = new_turn_str.strip()
    if new_turn_str.lower() in ("q", "quit"):
        return [ChatEvent(type="slash_budget", content="已取消.")]

    new_turn = cs.budget_per_turn_limit
    if new_turn_str:
        try:
            new_turn = int(new_turn_str)
            if new_turn < 0:
                return [ChatEvent(
                    type="slash_error",
                    content="per-turn limit 需 >= 0 (0=无限); 已取消.",
                )]
        except ValueError:
            return [ChatEvent(
                type="slash_error",
                content=f"输入非数字 {new_turn_str!r}; 已取消.",
            )]

    # 收 per-session
    try:
        new_session_str = await chat.input_provider(
            f"新 per-session limit (回车保持 {cs.budget_per_session_limit}, 0=无限, q 取消): "
        )
    except (EOFError, KeyboardInterrupt):
        return [ChatEvent(type="slash_budget", content="已取消.")]

    new_session_str = new_session_str.strip()
    if new_session_str.lower() in ("q", "quit"):
        return [ChatEvent(type="slash_budget", content="已取消.")]

    new_session = cs.budget_per_session_limit
    if new_session_str:
        try:
            new_session = int(new_session_str)
            if new_session < 0:
                return [ChatEvent(
                    type="slash_error",
                    content="per-session limit 需 >= 0 (0=无限); 已取消.",
                )]
        except ValueError:
            return [ChatEvent(
                type="slash_error",
                content=f"输入非数字 {new_session_str!r}; 已取消.",
            )]

    # Apply + refill remaining (2026-05-20 hotfix bug 2: 老逻辑 min(remaining,
    # new_limit) 只 cap 不 refill, 用尽后无法重设有效 budget).
    old_turn = cs.budget_per_turn_limit
    old_session = cs.budget_per_session_limit
    cs.budget_per_turn_limit = new_turn
    cs.budget_per_session_limit = new_session
    cs.budget_per_turn_remaining = new_turn  # refill (0 if unlimited)
    cs.budget_per_session_remaining = new_session  # refill (0 if unlimited)

    # Wave 2.5 review I-A: slash 改 chat_state 后立即 persist, 防进程中断丢
    # 配置. ephemeral 无 persist (没 sid), 跳过 — 改动在 in-memory chat_state,
    # promote_to_persistent 时拷过去.
    if hasattr(chat, "persist") and not getattr(chat, "is_ephemeral", False):
        try:
            chat.persist()
        except Exception:
            pass  # persist 失败不该 block /budget 返回

    def _fmt_limit(v: int) -> str:
        return "无限" if v == 0 else str(v)

    return [ChatEvent(
        type="slash_budget",
        content=(
            f"[已更新]\n"
            f"  per-turn: {_fmt_limit(old_turn)} → {_fmt_limit(new_turn)}\n"
            f"  per-session: {_fmt_limit(old_session)} → {_fmt_limit(new_session)}"
        ),
    )]


async def _handle_compact(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Force trigger sessionMemory compaction (bypass 5-turn interval).

    NOTE: 本 handler 不直接调 sessionMemory writer — 那需要 LLM client
    (slash dispatcher 没拿到). F.2 CLI wrapper 应该 intercept slash_compact
    event, 自己用 llm 调 session_memory_writer. 当前只 signal request.
    """
    from explain_engine.chat.session import ChatEvent
    return [ChatEvent(
        type="slash_compact",
        content="Force compaction requested. Will run on next reflect tick or via CLI handler.",
    )]


async def _handle_save(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Explicit flush sidecars (chat_state.json + graph)."""
    from explain_engine.chat.session import ChatEvent
    chat.persist()
    return [ChatEvent(
        type="slash_save",
        content=f"Saved session {chat.sid} to disk (graph + chat_state + transcript).",
    )]


async def _handle_new(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """/new — 重置 chat 到 ephemeral REPL 启动态.

    2026-05-20 重构 (跟 Phase 13 hotfix 同期): /new 不再 bootstrap 新 session.
    之前 contract `/new <question>` 跟 ephemeral REPL 启动后自然语言输入 promote
    走的是相同 bootstrap+HITL 路径, 等于双入口. 简化为: /new 重置回 ephemeral,
    用户接着输自然语言 → 走 promote 建新 session.

    Event yield: 单 ChatEvent(type='slash_reset_to_ephemeral'). Args ignored
    (向前兼容老用户输 `/new <question>` 不抛错, 仅静默忽略). Content 留 None —
    REPL consumer 看到 type 即触发 clear+ephemeral, 不需 payload.

    Consumer:
    - explain_engine.chat.repl_entry.enter_repl_async (主 REPL): aclose 当前
      持久 chat (if any) → console.clear() → chat = EphemeralChatSession →
      reprint banner. 视觉+逻辑等同 uv run explain 刚跑完.
    - explain_engine.cli._run_chat_repl_async (explain chat <sid> 路径):
      session-bound 模式, 不支持 ephemeral. /new 在此模式仅退出 chat REPL +
      提示用户输 `explain` 重启 (见 cli.py).
    """
    from explain_engine.chat.session import ChatEvent

    del chat, args  # 静默忽略; 历史 `/new <question>` 不抛错.
    return [ChatEvent(type="slash_reset_to_ephemeral", content=None)]


async def _handle_resume(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """/resume — numbered picker 列当前 project 所有 session, 用户选号后切.

    无参数. 列 + 弹 input 收 # → yield slash_switch_session.
    输无效 / out-of-range → slash_error 取消 (无 retry, 保持简单).
    输 q / empty → slash_resume 取消.
    选当前 sid → slash_resume info 'already there', 不 yield switch.

    Event 契约:
    - slash_resume: info content (str)
    - slash_switch_session: content={"sid": str} — REPL 据此切
      (见 ChatEvent docstring 完整 contract).
    """
    import asyncio
    from datetime import datetime

    from rich.console import Console
    from rich.table import Table

    from explain_engine.chat.session import ChatEvent
    from explain_engine.persistence.session import SessionStore

    if args:
        return [ChatEvent(
            type="slash_error",
            content="Usage: /resume  (无参数, 弹列表后选号)",
        )]

    # SessionStore.list() 自动 sort by created_at desc + log warning 跳过坏 session;
    # 只读 metadata.json, 不读 graph (避免 O(N) 大 graph IO).
    # 替代了之前手写循环 + 静默 except (Wave 4 code review I-1).
    metas = SessionStore().list()
    if not metas:
        return [ChatEvent(
            type="slash_resume",
            content="当前 project 无 session.",
        )]

    # 渲染表 — 用临时 Console (跟 /new 同款, 避免 from cli import console 反向依赖)
    console = Console()
    table = Table(title=f"Sessions ({len(metas)})")
    table.add_column("#", style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("问题", style="bold")
    table.add_column("Stage")
    table.add_column("Created")
    for i, m in enumerate(metas, start=1):
        is_current = "* " if m.session_id == chat.sid else "  "
        ts = datetime.fromtimestamp(m.created_at).strftime("%Y-%m-%d %H:%M")
        table.add_row(f"{is_current}{i}", m.session_id, m.question, m.stage, ts)
    console.print(table)

    # 收 user input. F-1 (2026-05-18): chat.input_provider set 时 (REPL 启动
    # 时挂上 read_input wrapper), 走 prompt_toolkit 享 bottom toolbar / ctrl+o /
    # 中文 backspace fix; 否则 fallback bare input (test / 非 chat REPL 路径).
    prompt_msg = "选 # (q 取消): "
    try:
        if chat.input_provider is not None:
            choice = await chat.input_provider(prompt_msg)
        else:
            choice = await asyncio.to_thread(input, prompt_msg)
    except (EOFError, KeyboardInterrupt):
        return [ChatEvent(type="slash_resume", content="已取消.")]

    choice = choice.strip().lower()
    if choice in ("", "q", "quit"):
        return [ChatEvent(type="slash_resume", content="已取消.")]

    if not choice.isdigit():
        return [ChatEvent(
            type="slash_error",
            content=f"输入需为数字 1-{len(metas)}; 已取消.",
        )]

    idx = int(choice)
    if not (1 <= idx <= len(metas)):
        return [ChatEvent(
            type="slash_error",
            content=f"# {idx} 超范围 (1-{len(metas)}); 已取消.",
        )]

    target_sid = metas[idx - 1].session_id
    if target_sid == chat.sid:
        return [ChatEvent(
            type="slash_resume",
            content=f"已在 session {target_sid}, 不切换.",
        )]

    return [
        ChatEvent(
            type="slash_resume",
            content=f"切换到 session {target_sid}...",
        ),
        ChatEvent(
            type="slash_switch_session",
            content={"sid": target_sid},
        ),
    ]


# ─────────────────────────────────────────────────────────────────────────
# Phase 11 Wave 3: 6 single-session slash + /cf alias.
# Cover cli subcommand: compress / run / check / predict / counterfactual / rescore.
# 复用 engines layer logic (不复用 cli command body, 因 cli body 含 typer 装饰).
#
# 设计:
# - 单 session 操作, ephemeral 时 reject (没 real session 操作 graph)
# - /predict /counterfactual 走 input_provider 收 intervention text
# - /compress 走 review_insights_async + flush_to_lexicon (chat 用 async, cli 用 sync)
# - /run /rescore 走 chat.persist() (mutate state)
# - /check read-only, 不 persist
# ─────────────────────────────────────────────────────────────────────────


_EPI_SHORT_MAP = {
    "fact": "fact",
    "observation": "obs",
    "inference": "inf",
    "insight": "ins",
    "speculation": "spec",
}


def _format_epi_short(epi: str) -> str:
    """Epistemic 5 字 → 3-4 字缩写, 行格式对齐用. 未知 epi fallback 返原值."""
    return _EPI_SHORT_MAP.get(epi, epi)


def _format_node_brief(
    state,
    nid: str,
    max_desc: int = 60,
    weak: bool = False,
) -> str:
    """Phase 12 (2026-05-19): /show + /graph detail. Fix 3 升级版.

    新行格式:
      {id} [{epi_short} {conf:.2f}] {marker?} 「{name}」: {desc[:max_desc]}{...}?

    marker 优先级 (lifecycle > weak): [decayed] > [stale] > (weak) > 空.

    Args:
        state: ChatState (含 graph).
        nid: node id.
        max_desc: desc 截短 char 数, 默认 60.
        weak: caller 标记此 node 在 weak_chain_l1s 中 (multi-signal 视角).
              若 lifecycle_state 是 stale/decayed, marker 用 lifecycle 不用 weak.

    Returns:
        formatted line, 或 fallback "{nid} (节点不在 graph)".

    Used by:
        - /show (Phase 12) node tree
        - /predict (Fix 3) report
        - /counterfactual (Fix 3) report
    """
    node = state.graph.nodes.get(nid)
    if node is None:
        return f"{nid} (节点不在 graph)"

    epi_short = _format_epi_short(node.epistemic)
    conf_str = f"{node.confidence:.2f}"

    # marker 优先级: lifecycle > weak
    if node.lifecycle_state == "decayed":
        marker = "[decayed] "
    elif node.lifecycle_state == "stale":
        marker = "[stale] "
    elif weak:
        marker = "(weak) "
    else:
        marker = ""

    desc = node.description[:max_desc]
    if len(node.description) > max_desc:
        desc += "..."

    return f"{nid} [{epi_short} {conf_str}] {marker}「{node.name}」: {desc}"


def _format_node_list(state, nids: list[str], indent: str = "    ") -> str:
    """格式化 node ID 列表为 multi-line, 每行 1 个 node brief."""
    if not nids:
        return f"{indent}(none)"
    return "\n".join(f"{indent}{_format_node_brief(state, nid)}" for nid in nids)


def _format_edge_brief(edge, max_mech: int = 60) -> str:
    """Phase 12: /show edge 行格式.

    格式: `{source} → {target} [{conf:.2f}] {mechanism[:max_mech]}...?`

    relation_type 不显行内 (caller 已按 type 分 section). source/target
    只显 ID, 不展开 name — 上方 node tree 可查, 避免行宽爆炸.
    """
    mech = edge.mechanism_description[:max_mech]
    if len(edge.mechanism_description) > max_mech:
        mech += "..."
    return f"{edge.source_node} → {edge.target_node} [{edge.confidence:.2f}] {mech}"


_SESSION_TMPDIR: str | None = None
"""Phase 12: lazy session-scoped tmpdir for /graph PNG output.

进程级 (非 session 级), 同一 REPL 内 /new /resume 多 session 共享,
filename 含 sid 区分 (graph_<sid>_<tick>.png). atexit 进程退出清.
退出后路径失效 — 符合用户预期 '磁盘干净'.
"""


def _get_session_tmpdir() -> str:
    """Lazy init + atexit cleanup. 不用 /graph 的 session 完全不创目录."""
    global _SESSION_TMPDIR
    if _SESSION_TMPDIR is None:
        _SESSION_TMPDIR = _tempfile.mkdtemp(prefix="explain_graph_")
        _atexit.register(_shutil.rmtree, _SESSION_TMPDIR, ignore_errors=True)
    return _SESSION_TMPDIR


_EDGE_TYPE_SHORT = {
    "causes": "cau",
    "amplifies": "amp",
    "suppresses": "sup",
    "constrains": "con",
    "manifests_as": "man",
}

_L_SHAPE = {0: "box", 1: "ellipse", 2: "doubleoctagon"}
_L_FILL = {0: "lightblue", 1: "lightyellow", 2: "lightcoral"}


def _build_digraph(state, weak_l1_ids: set[str]):
    """Phase 12 /graph: build graphviz.Digraph from ChatState.graph.

    Visual encoding 见 docs/plans/2026-05-19-slash-show-graph-detail-design.md §4.2.

    Args:
        state: ChatState (含 graph).
        weak_l1_ids: 来自 aggregate_acceptance().weak_chain_l1s, 用于 weak L1 红边框.

    Returns:
        graphviz.Digraph (caller render to PNG).
    """
    import graphviz

    dg = graphviz.Digraph(format="png")
    dg.attr(rankdir="TB")
    dg.attr("node", style="filled", fontname="Helvetica")

    for node in state.graph.nodes.values():
        shape = _L_SHAPE.get(node.abstraction_level, "box")
        fill = _L_FILL.get(node.abstraction_level, "white")
        label = f"{node.id}\n「{node.name}」\n[{node.confidence:.2f}]"

        attrs: dict[str, str] = {
            "shape": shape,
            "fillcolor": fill,
            "label": label,
        }

        # lifecycle 优先 (decayed > stale > default)
        if node.lifecycle_state == "decayed":
            attrs["style"] = "dashed,filled"
            attrs["fillcolor"] = "gray80"
        elif node.lifecycle_state == "stale":
            attrs["style"] = "dotted,filled"

        # weak L1: 红边框 (与 lifecycle 视觉叠加)
        if node.id in weak_l1_ids:
            attrs["color"] = "red"
            attrs["penwidth"] = "2"

        dg.node(node.id, **attrs)

    for edge in state.graph.edges.values():
        short = _EDGE_TYPE_SHORT.get(edge.relation_type, edge.relation_type[:3])
        label = f"{short} {edge.confidence:.2f}"

        edge_attrs: dict[str, str] = {"label": label}

        if edge.relation_type == "amplifies":
            edge_attrs["penwidth"] = "2.5"
        elif edge.relation_type == "suppresses":
            edge_attrs["color"] = "red"
        elif edge.relation_type == "constrains":
            edge_attrs["color"] = "blue"
        elif edge.relation_type == "manifests_as":
            edge_attrs["style"] = "dashed"

        dg.edge(edge.source_node, edge.target_node, **edge_attrs)

    return dg


def _detect_inline_renderer(png_path: str) -> tuple[list[str] | None, str]:
    """Phase 12 /graph: detect terminal capability, return (cmd, renderer_name).

    检测顺序 (按优先级): iTerm2 → Kitty/Ghostty → chafa → None.

    Returns:
        (cmd_list, name) — cmd_list None 表示无 inline renderer 可用.
        name ∈ {"iterm", "kitty", "chafa", "none"}.

    Note:
        iTerm 检 imgcat 在 PATH (iTerm2 自带 utilities, 但用户可能没装).
        若 iTerm 检测到但 imgcat 不在 → fall through 下一档 (Kitty/chafa).
    """
    import os
    import shutil

    term_program = os.environ.get("TERM_PROGRAM", "")
    kitty_window = os.environ.get("KITTY_WINDOW_ID", "")

    # 1. iTerm2 + imgcat
    if term_program == "iTerm.app" and shutil.which("imgcat"):
        return ["imgcat", png_path], "iterm"

    # 2. Kitty / Ghostty (kitty graphics protocol)
    if (kitty_window or term_program == "ghostty") and shutil.which("kitty"):
        return ["kitty", "+kitten", "icat", png_path], "kitty"

    # 3. chafa (通用 Unicode block art)
    if shutil.which("chafa"):
        return ["chafa", "--size", "100x40", png_path], "chafa"

    # 4. None
    return None, "none"


def _ephemeral_reject(name: str) -> list[ChatEvent]:
    """Phase 11 Wave 3: ephemeral 时统一 reject 模板.

    单 session slash 都要求真 session — graph 是空的, 跑没意义.
    友好提示用户先 promote_to_persistent.
    """
    from explain_engine.chat.session import ChatEvent
    return [ChatEvent(
        type="slash_error",
        content=(
            f"/{name} 需要真 session, 当前 ephemeral (尚未 /new). "
            f"输自然语言新建 session 或 /resume 选历史 session."
        ),
    )]


@with_stage_gate(
    allowed=["bootstrap_pending", "insight_pending"],
    success_stage="done",
    fail_hint_key="need_promote_first",
    success_hint_key="after_compress",
)
async def _handle_compress(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 11 Wave 3: 当前 session compress + HITL review_insights + flush_to_lexicon.

    走 async path (review_insights_async + chat.input_provider), 跟 cli
    `explain compress <sid>` 用 sync review_insights 不同 — chat REPL
    要走 prompt_toolkit input_provider 才能享 ctrl+o / bottom toolbar.

    Side effects:
    - state.graph: 加 N 个 L1 候选 + edges (propose_candidates)
    - state.insight_candidates: 被 review_insights_async 改 (drop / keep / edit)
    - lexicon: best-effort flush (异常吞)
    - sidecar: chat.persist() 落盘

    LLM 失败 / lexicon flush 失败都返 slash_error 不 raise.
    """
    from explain_engine.chat.session import ChatEvent

    if getattr(chat, "is_ephemeral", False):
        return _ephemeral_reject("compress")

    if chat.llm is None:
        return [ChatEvent(
            type="slash_error",
            content="/compress 需要 LLM client; 当前 chat session 启动时未绑定 llm.",
        )]

    from rich.console import Console

    from explain_engine.engines.compression import propose_candidates
    from explain_engine.engines.evaluation import score_all
    from explain_engine.engines.lexicon import flush_to_lexicon
    from explain_engine.hitl.cli_interactive import review_insights_async

    _console = Console()

    # Phase 14 Task 15: ip 入口短路 — 上次 propose+score 完 (mid-stage Task 14),
    # 但 review 被取消 → 重入跳过 LLM 直接进 review.
    if chat._session.meta.stage == "insight_pending":
        _console.print(
            "[dim](检测到 stage=insight_pending, 跳过 LLM 直接进入审查)[/dim]"
        )
        # ip 入口: 直接 fallback dedup_stats — 上次 propose 已落 candidates,
        # 但 embedding stats 缺. Display "0 near-dup / N new" 占位.
        dedup_stats = {"reused": 0, "new": len(chat.state.insight_candidates)}
    else:
        # bp 入口 (default): propose + score + mid-stage persist
        try:
            from explain_engine.engines.lexicon import get_lexicon_top_k_for_compress
            top_k = get_lexicon_top_k_for_compress(chat.storage, k=20)
            with _console.status("[bold green]调 LLM 提候选 (compress)...[/bold green]"):
                await propose_candidates(chat.state, chat.llm, existing_lexicon=top_k)
        except Exception as exc:
            return [ChatEvent(
                type="slash_error",
                content=f"/compress propose_candidates 失败: {type(exc).__name__}: {exc}",
            )]

        # Phase 13 Wave 3 Task 4: compute dedup stats for UI display (observational,
        # doesn't mutate state). Display threshold 0.75 (lower than 0.85 merge
        # threshold) accounts for proxy-text format mismatch with lexicon canonical.
        import logging

        from explain_engine.engines.compress_dedup import compute_compress_dedup_stats
        try:
            dedup_stats = compute_compress_dedup_stats(
                chat.state,
                chat.storage,
                list(chat.state.insight_candidates),
                display_threshold=0.75,
            )
        except Exception as exc:
            logging.warning(
                f"compute_compress_dedup_stats failed in /compress: "
                f"{type(exc).__name__}: {exc}. Showing all-new fallback."
            )
            dedup_stats = {"reused": 0, "new": len(chat.state.insight_candidates)}

        # Fix 1 (2026-05-19 smoke bug): 加 score_all 让 state.last_gains 非空
        # (跟 cli `_run_compress` 一致). Phase 11 Wave 3 漏 score_all 导致 HITL
        # 看 gain 全 0.00 — review_insights_async 实际从 state.last_gains 读.
        try:
            with _console.status(
                "[bold green]调 LLM 评每 L1 候选 (score_all)...[/bold green]"
            ):
                await score_all(chat.state, chat.llm)
        except Exception as exc:
            return [ChatEvent(
                type="slash_error",
                content=f"/compress score_all 失败: {type(exc).__name__}: {exc}",
            )]

        # Phase 14 Task 14: mid-stage persist (中断恢复). propose+score 完后,
        # review 之前, 推 stage → insight_pending + 立刻落盘. 即便 review 取消
        # (KeyboardInterrupt) 也能下次重入跳过 LLM (Task 15 短路).
        chat._session.meta.stage = "insight_pending"
        chat.persist()
        _console.print(
            "[dim](中间状态已保存, 即便 review 取消也能下次重入跳过 LLM)[/dim]"
        )

    # HITL async review (走 chat.input_provider, None 时 accept-all). 不包 spinner
    # — HITL 期间 prompt 显式 wait user, spinner 会撞.
    await review_insights_async(chat.state, chat.input_provider)

    # persist sidecar + flush lexicon (best-effort — lexicon 失败不该 fail compress)
    chat.persist()
    n = 0
    try:
        with _console.status(
            "[bold green]写入 lexicon (LLM 生 canonical mechanism)...[/bold green]"
        ):
            n = await flush_to_lexicon(chat._session, chat.storage, llm=chat.llm)
    except Exception:
        n = 0

    total = dedup_stats["reused"] + dedup_stats["new"]
    return [ChatEvent(
        type="slash_compress",
        content=(
            f"compress 完成. {len(chat.state.insight_candidates)} 候选保留. "
            f"{n} var 写入 lexicon.\n"
            f"compress dedup: {total} candidates → {dedup_stats['reused']} near-dup "
            f"(cos≥0.75) / {dedup_stats['new']} new (embedding pre-check; "
            f"actual merge happens at flush_to_lexicon with cos≥0.85)"
        ),
    )]


@with_stage_gate(
    allowed=["done"],
    success_stage="converged",
    fail_hint_key="need_compress_first",
    success_hint_key="after_run",
)
async def _handle_run(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 11 Wave 3: 当前 session reasoning loop (Phase 5/7 runtime).

    封装 `runtime.runtime.run(state, llm, budget, on_tick)` —— 用
    chat.state.budget_remaining 作 budget (跟 cli 一致, 不强制额外指定).

    Side effects:
    - state.tick / budget / trace / graph (expansion/reflection 改)
    - sidecar persist after loop ends

    返 stop_reason. 失败 (LLMError / SchemaValidationError) 返 slash_error.
    """
    from explain_engine.chat.session import ChatEvent

    if getattr(chat, "is_ephemeral", False):
        return _ephemeral_reject("run")

    if chat.llm is None:
        return [ChatEvent(
            type="slash_error",
            content="/run 需要 LLM client; 当前 chat session 启动时未绑定 llm.",
        )]

    from rich.console import Console

    from explain_engine.runtime.runtime import run as runtime_run

    budget = max(chat.state.budget_remaining, 1)
    try:
        # 2026-05-19 polish: Rich Status spinner — runtime.run 跑 reasoning loop
        # (多次 LLM 调用, 总耗时几十秒到几分钟取决于 budget)
        with Console().status(
            "[bold green]调 LLM 跑 reasoning loop (含 expand/reflect/decay)...[/bold green]"
        ):
            reason = await runtime_run(chat.state, chat.llm, budget=budget)
    except Exception as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"/run 失败: {type(exc).__name__}: {exc}",
        )]

    chat.persist()
    return [ChatEvent(
        type="slash_run",
        content=f"reasoning loop 完成: stop_reason={reason}, tick={chat.state.tick}",
    )]


async def _handle_check(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 11 Wave 3: multi-signal acceptance read-only.

    跑 aggregate_acceptance 输出汇总数字. 跟 cli `explain check` 不同 —
    cli 还跑 check_consistency_batch / 渲染 per-target table, slash 只
    出 aggregate summary (chat 模式不适合大 table). 用户想看 per-target
    可用 /show.

    无 mutate, 不 persist.
    """
    from explain_engine.chat.session import ChatEvent

    if getattr(chat, "is_ephemeral", False):
        return _ephemeral_reject("check")

    from explain_engine.engines.simulation import aggregate_acceptance

    try:
        report = aggregate_acceptance(chat.state)
    except Exception as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"/check 失败: {type(exc).__name__}: {exc}",
        )]

    weak_str = ", ".join(report.weak_chain_l1s) or "(none)"
    missing_str = ", ".join(report.missing_l0) or "(none)"
    return [ChatEvent(
        type="slash_check",
        content=(
            f"Multi-signal acceptance:\n"
            f"  avg_consistency:   {report.avg_consistency:.3f}\n"
            f"  avg_essentialness: {report.avg_essentialness:.3f}\n"
            f"  rollout_coverage:  {report.rollout_coverage:.3f}\n"
            f"  weak_chain_l1s:    {weak_str}\n"
            f"  missing_l0:        {missing_str}"
        ),
    )]


@with_stage_gate(
    allowed=["done", "converged"],
    success_stage=None,
    fail_hint_key="need_compress_first",
    success_hint_key="after_inference",
)
async def _handle_predict(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 11 Wave 3: forward prediction. Interactive prompt 收 intervention text.

    跟 cli `explain predict <sid> <text>` 区别: cli 把 text 当 typer
    positional arg, slash 走 input_provider 收 (用户 /predict 之后
    sub-prompt 输描述). 无 args (输 args 会被忽略).

    Side effects: state.graph + nodes (加 new_concepts + predicted L0).
    持久化通过 chat.persist().
    """
    from explain_engine.chat.session import ChatEvent

    if getattr(chat, "is_ephemeral", False):
        return _ephemeral_reject("predict")

    if chat.llm is None:
        return [ChatEvent(
            type="slash_error",
            content="/predict 需要 LLM client; 当前 chat session 启动时未绑定 llm.",
        )]

    if chat.input_provider is None:
        return [ChatEvent(
            type="slash_error",
            content="/predict 需 input_provider (REPL 模式), 当前 None.",
        )]

    try:
        intervention = (await chat.input_provider(
            "intervention 描述 (e.g. '如果 X 增加', q 取消): "
        )).strip()
    except (EOFError, KeyboardInterrupt):
        return [ChatEvent(type="slash_predict", content="已取消.")]

    if not intervention or intervention.lower() in ("q", "quit"):
        return [ChatEvent(type="slash_predict", content="已取消.")]

    from rich.console import Console

    from explain_engine.engines.prediction import predict as prediction_predict
    try:
        # 2026-05-19 polish: Rich Status spinner — prediction LLM 调用 (~5-15s)
        with Console().status(
            "[bold green]调 LLM 跑 prediction...[/bold green]"
        ):
            report = await prediction_predict(chat.state, intervention, chat.llm)
    except Exception as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"/predict 失败: {type(exc).__name__}: {exc}",
        )]

    chat.persist()

    # Fix 3 (2026-05-19 smoke bug 2): 显 node.name + description 而非裸 ID.
    # _format_node_brief 从 chat.state.graph.nodes 读 — predict 已经把新 node
    # add 到 graph (predict 副作用), 所以 c_005 / p_xxx 都能查到.
    new_nodes_block = _format_node_list(chat.state, report.new_node_ids)
    predicted_block = _format_node_list(chat.state, report.predicted_L0_ids)
    activated_block = _format_node_list(chat.state, report.activated_existing_L0)

    # Fix 2 (2026-05-19 smoke bug): 加 top-3 propagation_acts display.
    # PredictionReport.propagation_acts 是核心信息 (新 concept 通过 edge
    # 影响现 graph mid-level node 的 activation map). 同样显 name+desc.
    prop_acts = getattr(report, "propagation_acts", {})
    if prop_acts:
        top_acts = sorted(prop_acts.items(), key=lambda kv: -abs(kv[1]))[:3]
        prop_lines = "\n".join(
            f"    {act:+.2f}  {_format_node_brief(chat.state, nid)}"
            for nid, act in top_acts
        )
    else:
        prop_lines = "    (无 propagation, intervention 跟现 graph 无 edge 关联)"

    return [ChatEvent(
        type="slash_predict",
        content=(
            f"prediction (intervention={intervention!r}):\n"
            f"  new_nodes:\n{new_nodes_block}\n"
            f"  predicted_L0:\n{predicted_block}\n"
            f"  activated_existing_L0:\n{activated_block}\n"
            f"  top propagation (现 graph mid-level 受影响, sign=activation delta):\n"
            f"{prop_lines}"
        ),
    )]


@with_stage_gate(
    allowed=["done", "converged"],
    success_stage=None,
    fail_hint_key="need_compress_first",
    success_hint_key="after_inference",
)
async def _handle_counterfactual(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 11 Wave 3: counterfactual remove + (optional) substitute.

    副作用 = 0 (engines.counterfactual.substitute 用 deepcopy). 不 persist.

    /cf 是同函数 alias (DEFAULT_COMMANDS 注册同 handler 实例).
    """
    from explain_engine.chat.session import ChatEvent

    if getattr(chat, "is_ephemeral", False):
        return _ephemeral_reject("counterfactual")

    if chat.llm is None:
        return [ChatEvent(
            type="slash_error",
            content="/counterfactual 需要 LLM client; 当前 chat session 启动时未绑定 llm.",
        )]

    if chat.input_provider is None:
        return [ChatEvent(
            type="slash_error",
            content="/counterfactual 需 input_provider (REPL 模式), 当前 None.",
        )]

    try:
        intervention = (await chat.input_provider(
            "counterfactual 描述 (e.g. '若用 X 替代 Y', q 取消): "
        )).strip()
    except (EOFError, KeyboardInterrupt):
        return [ChatEvent(type="slash_counterfactual", content="已取消.")]

    if not intervention or intervention.lower() in ("q", "quit"):
        return [ChatEvent(type="slash_counterfactual", content="已取消.")]

    from rich.console import Console

    from explain_engine.engines.counterfactual import substitute
    try:
        # 2026-05-19 polish: Rich Status spinner — counterfactual LLM 调用 (~5-15s)
        with Console().status(
            "[bold green]调 LLM 跑 counterfactual...[/bold green]"
        ):
            report = await substitute(chat.state, intervention, chat.llm)
    except Exception as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"/counterfactual 失败: {type(exc).__name__}: {exc}",
        )]

    # 副作用 = 0, 不 persist.
    removed_str = ", ".join(report.removed_node_ids) or "(none)"
    added_str = ", ".join(report.added_node_ids) or "(none)"
    # Top |diff| > 0.05 (跟 cli 同 cutoff)
    sig_diff = sorted(
        [(nid, v) for nid, v in report.activation_diff.items() if abs(v) > 0.05],
        key=lambda kv: -abs(kv[1]),
    )[:5]
    diff_lines = (
        "\n".join(f"    {nid}: {v:+.2f}" for nid, v in sig_diff)
        if sig_diff else "    (无明显 diff)"
    )

    content_lines = [
        f"counterfactual (intervention={intervention!r}):",
        f"  removed:       {removed_str}",
        f"  substituted:   {added_str}",
        "  top diff (baseline - cf):",
        diff_lines,
    ]
    if report.alt_narrative:
        content_lines.append(f"  narrative: {report.alt_narrative}")

    return [ChatEvent(
        type="slash_counterfactual",
        content="\n".join(content_lines),
    )]


@with_stage_gate(
    allowed=None,
    success_stage=None,
    fail_hint_key=None,
    success_hint_key="after_rescore",
)
async def _handle_rescore(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 11 Wave 3: 重评 edge.confidence (manifests_as + causes edges).

    无 HITL. 跑完 persist. 跟 cli `explain rescore <sid>` 同 engines API
    (rescore_session). LLM cost: ~25 calls per session (typical).
    """
    from explain_engine.chat.session import ChatEvent

    if getattr(chat, "is_ephemeral", False):
        return _ephemeral_reject("rescore")

    if chat.llm is None:
        return [ChatEvent(
            type="slash_error",
            content="/rescore 需要 LLM client; 当前 chat session 启动时未绑定 llm.",
        )]

    from rich.console import Console

    from explain_engine.engines.rescore import rescore_session

    try:
        # 2026-05-19 polish: Rich Status spinner — rescore LLM 多调用 (~25 LLM call)
        with Console().status(
            "[bold green]调 LLM 重评 edge confidence (典型 ~25 LLM call)...[/bold green]"
        ):
            new_confs = await rescore_session(chat.state, chat.llm)
    except Exception as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"/rescore 失败: {type(exc).__name__}: {exc}",
        )]

    chat.persist()
    if not new_confs:
        return [ChatEvent(
            type="slash_rescore",
            content="rescore 完成: 无 manifests_as/causes edges 可 rescore.",
        )]

    avg = sum(new_confs.values()) / len(new_confs)
    return [ChatEvent(
        type="slash_rescore",
        content=f"rescore 完成: {len(new_confs)} edges, avg conf={avg:.2f}. 已 persist.",
    )]


# ─────────────────────────────────────────────────────────────────────────
# Phase 11 Wave 4: 3 cross-session slash — /list /lexicon /migrate.
# 不依赖单 session graph, ephemeral 也 work (不 reject). 复用 cli 同名
# subcommand 的 render 逻辑, table 渲染到内存 (StringIO + force_terminal=False)
# 后塞进 ChatEvent.content.
# ─────────────────────────────────────────────────────────────────────────


async def _handle_list(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 11 Wave 4: 列当前 project 所有 session (cross-session inspect).

    ephemeral 也 work (不依赖 chat.state). 直接 SessionStore().list() 取
    metadata.json, Rich Table 渲染到 string 塞进 slash_list event.
    """
    from datetime import datetime

    from rich.table import Table

    from explain_engine.chat.session import ChatEvent
    from explain_engine.persistence.session import SessionStore

    metas = SessionStore().list()
    if not metas:
        return [ChatEvent(
            type="slash_list",
            content="当前 project 无 session.",
        )]

    table = Table(title=f"Sessions ({len(metas)})")
    table.add_column("ID", style="cyan")
    table.add_column("问题", style="bold")
    table.add_column("Stage")
    table.add_column("Created")
    for m in metas:
        ts = datetime.fromtimestamp(m.created_at).strftime("%Y-%m-%d %H:%M")
        table.add_row(m.session_id, m.question, m.stage, ts)

    return [ChatEvent(
        type="slash_list",
        content=_render_table_to_string(table),
    )]


async def _handle_lexicon(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 11 Wave 4: 列 cross-session lexicon variables (Phase 10).

    读 ~/.explain/projects/<proj>/knowledge/variables.json. 空时给 hint
    引导跑 /compress 或退出 chat (chat aclose 会 flush lexicon).
    """
    from rich.table import Table

    from explain_engine.chat.session import ChatEvent
    from explain_engine.engines.lexicon import _load_lexicon
    from explain_engine.persistence.storage_v2 import StorageV2

    storage = StorageV2()
    lexicon_path = storage.knowledge_dir() / "variables.json"
    lex = _load_lexicon(lexicon_path)

    variables = lex["variables"]
    if not variables:
        return [ChatEvent(
            type="slash_lexicon",
            content="lexicon 暂无变量. 跑 /compress 或退出 chat 让 aclose flush.",
        )]

    table = Table(title=f"Variable Lexicon ({len(variables)} vars)")
    table.add_column("global_id", style="cyan")
    table.add_column("名称", style="bold")
    table.add_column("Level", justify="right")
    table.add_column("reuse", justify="right")
    table.add_column("avg_ess", justify="right")
    table.add_column("last_seen", style="dim")
    for v in variables:
        table.add_row(
            v["global_id"],
            v["name"],
            f"L{v['abstraction_level']}",
            str(v["fitness"]["reuse_count"]),
            f"{v['fitness']['avg_essentialness']:.2f}",
            v["fitness"]["last_seen_at"][:10],
        )

    return [ChatEvent(
        type="slash_lexicon",
        content=_render_table_to_string(table),
    )]


async def _handle_migrate(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 11 Wave 4: 一次性迁老 sessions/*.json → storage_v2 layout.

    流程:
    1. detect_legacy_sessions() — 扫当前 cwd sessions/ 找 legacy
    2. 无 legacy → 直接 info, 返
    3. 有 legacy + 无 input_provider → display info (test / 非 REPL)
    4. 有 legacy + 有 provider → 弹 confirm prompt; 'y' 跑 migrate_all
       (dry_run=False), 否则取消

    Migration API: explain_engine.persistence.migration —
    detect_legacy_sessions() + migrate_all(dry_run=). 失败吞 → slash_error.
    """
    import asyncio

    from explain_engine.chat.session import ChatEvent
    from explain_engine.persistence.migration import (
        detect_legacy_sessions,
        migrate_all,
    )

    try:
        sids = await asyncio.to_thread(detect_legacy_sessions)
    except Exception as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"/migrate detect 失败: {type(exc).__name__}: {exc}",
        )]

    if not sids:
        return [ChatEvent(
            type="slash_migrate",
            content="无老 sessions/*.json 需迁 (或目录不存在).",
        )]

    n = len(sids)
    if chat.input_provider is None:
        return [ChatEvent(
            type="slash_migrate",
            content=(
                f"检测到 {n} legacy session(s): {sids}. "
                f"需在 REPL (input_provider 已挂) 中调用 /migrate 确认; "
                f"当前无 provider, 跳过."
            ),
        )]

    try:
        confirm = (await chat.input_provider(
            f"将迁 {n} session 到 ~/.explain/projects/<proj>/sessions/. "
            f"确认 (y/n)? "
        )).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return [ChatEvent(type="slash_migrate", content="已取消.")]

    if confirm not in ("y", "yes"):
        return [ChatEvent(type="slash_migrate", content="已取消.")]

    try:
        results = await asyncio.to_thread(migrate_all, dry_run=False)
    except Exception as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"/migrate 失败: {type(exc).__name__}: {exc}",
        )]

    migrated = [r["sid"] for r in results if r["migrated"]]
    skipped = [(r["sid"], r["reason"]) for r in results if not r["migrated"]]

    lines = [f"成功迁 {len(migrated)}/{len(results)} session."]
    if migrated:
        head = migrated[:5]
        tail = "..." if len(migrated) > 5 else ""
        lines.append(f"  migrated: {head}{tail}")
    if skipped:
        lines.append(f"  skipped ({len(skipped)}):")
        for sid, reason in skipped[:5]:
            lines.append(f"    {sid}: {reason}")

    return [ChatEvent(
        type="slash_migrate",
        content="\n".join(lines),
    )]


# Registry — 17 default slash commands + 1 alias (/cf → counterfactual).
# 顺序决定 /help 列出顺序, 按"管理 → inspection → 操作 → engines → cross-session"分组.
DEFAULT_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("quit", "Exit chat session (saves first).", _handle_quit),
    SlashCommand("help", "List slash commands and available tools.", _handle_help),
    SlashCommand("show", "Show graph snapshot + multi-signal.", _handle_show),
    SlashCommand("graph", "渲染 graph 可视化 (graphviz inline via iTerm/Kitty/chafa).", _handle_graph),
    SlashCommand("budget", "Show budget + interactive config per-turn / per-session limit.", _handle_budget),
    SlashCommand("compact", "Force trigger sessionMemory compaction.", _handle_compact),
    SlashCommand("save", "Explicit flush of all sidecar files.", _handle_save),
    SlashCommand("new", "重置 chat: 清屏 + 关当前 session + 回 ephemeral REPL.", _handle_new),
    SlashCommand("resume", "列历史 session, 选号后切.", _handle_resume),
    # Phase 11 Wave 3: 6 single-session engines slash + /cf alias.
    SlashCommand("compress", "Compress 当前 session (propose_candidates + HITL + lexicon).", _handle_compress),
    SlashCommand("run", "跑 reasoning loop (expansion + reflection).", _handle_run),
    SlashCommand("check", "Multi-signal acceptance report (read-only).", _handle_check),
    SlashCommand("predict", "Forward prediction: 收 intervention text 后跑.", _handle_predict),
    SlashCommand("counterfactual", "Counterfactual: 收 intervention text 后跑 (副作用 0).", _handle_counterfactual),
    SlashCommand("cf", "(alias of /counterfactual)", _handle_counterfactual),
    SlashCommand("rescore", "重评 edge.confidence (manifests_as + causes).", _handle_rescore),
    # Phase 11 Wave 4: 3 cross-session slash (不依赖 single session, ephemeral 也 work).
    SlashCommand("list", "列当前 project 所有 session (cross-session).", _handle_list),
    SlashCommand("lexicon", "列 cross-session lexicon variables.", _handle_lexicon),
    SlashCommand("migrate", "一次性迁老 sessions/*.json → storage_v2 layout.", _handle_migrate),
)


def _command_by_name(name: str) -> SlashCommand | None:
    """Linear lookup OK — small N (~20 commands), no hash table needed."""
    for cmd in DEFAULT_COMMANDS:
        if cmd.name == name:
            return cmd
    return None


async def dispatch_slash(chat: ChatSession, raw_input: str) -> list[ChatEvent]:
    """Parse `/cmd args` and dispatch to handler.

    Returns list of ChatEvent (handler may yield multiple).
    Unknown command → single slash_unknown event with /help hint.
    Empty slash ("/") → slash_error.
    Non-slash input → slash_error (caller should check is_slash_command first).
    """
    from explain_engine.chat.session import ChatEvent

    text = raw_input.strip()
    if not text.startswith("/"):
        # Defensive — caller should have checked is_slash_command
        return [ChatEvent(type="slash_error", content="not a slash command")]

    # Strip leading /, split on whitespace.
    parts = text[1:].split()
    if not parts:
        return [ChatEvent(
            type="slash_error",
            content="empty slash command. type /help for list.",
        )]

    name = parts[0]
    args = parts[1:]
    cmd = _command_by_name(name)
    if cmd is None:
        return [ChatEvent(
            type="slash_unknown",
            content=f"unknown slash command: /{name}. type /help for list.",
        )]

    return await cmd.handler(chat, args)
