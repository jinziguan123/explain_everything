"""Phase 9 Wave F.1 + 2026-05-18: 7 default slash commands (含 /new).

设计参考 Claude Code 同款 slash 模式 — 本地 intercept 不走 LLM,
廉价 inspection + exit + force compact 等管理命令; slash 不计入
transcript / turn_count (因为非真正 user→assistant 对话).

每个 SlashCommand:
- name: str (e.g. "quit")
- description: str (shown in /help)
- handler: async (chat, args: list[str]) -> list[ChatEvent]
  返 list[ChatEvent] (multiple events 可能, /new 同时 yield slash_new + slash_switch_session).

设计参考 docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave F.1
+ docs/plans/2026-05-18-chat-new-resume-slash-plan.md Wave 3.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

# module-top import: monkeypatch path `slash_commands.{bootstrap_phenomena,review_phenomena}`
# 需要这两 name 真存在于 slash_commands module namespace (函数内 from-import 不行,
# 因为 monkeypatch 改的是 *module* attribute, 而函数 from-import 每次都从 source
# module 重新查; 提到顶后 source = slash_commands 本身, monkeypatch 才生效).
from explain_engine.engines.bootstrap import bootstrap_phenomena
from explain_engine.hitl.cli_interactive import review_phenomena

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
    """Graph snapshot + multi-signal acceptance report.

    aggregate_acceptance 是 readonly (Phase 2 simulation), 不调 LLM,
    所以放 slash 里 fresh 跑一次廉价. 异常吞掉 (e.g. graph 空 / no L1)
    防 inspection 命令 crash 整 session.
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

    lines = [
        f"Session: {chat.sid}",
        f"Question: {chat._session.meta.question}",
        f"Stage: {chat._session.meta.stage}",
        f"Graph: {len(g.nodes)} nodes ({n_l0} L0 / {n_l1} L1 / {n_l2} L2)",
        f"Lifecycle: {n_decayed} decayed, {n_stale} stale",
    ]

    # Multi-signal section (run aggregate_acceptance fresh).
    # 包 try/except — graph 空 / 边角情况不应 crash inspection 命令.
    try:
        report = aggregate_acceptance(state)
        lines.append("")
        lines.append("Multi-signal acceptance:")
        lines.append(f"  avg_consistency: {report.avg_consistency:.3f}")
        lines.append(f"  avg_essentialness: {report.avg_essentialness:.3f}")
        lines.append(f"  weak_chain_l1s: {report.weak_chain_l1s}")
        lines.append(f"  rollout_coverage: {report.rollout_coverage:.3f}")
        if report.input_alignment is not None:
            lines.append(f"  input_alignment: {report.input_alignment:.3f}")
    except Exception as exc:
        lines.append("")
        lines.append(f"(aggregate_acceptance failed: {type(exc).__name__})")

    return [ChatEvent(type="slash_show", content="\n".join(lines))]


async def _handle_budget(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Display per-turn / per-session budget remaining + turn_count."""
    from explain_engine.chat.session import ChatEvent

    b = chat.budget
    content = (
        f"per-turn remaining: {b.per_turn_remaining} / {b.per_turn_limit}\n"
        f"per-session remaining: {b.per_session_remaining} / {b.per_session_limit}\n"
        f"turn_count: {chat.chat_state.turn_count}"
    )
    return [ChatEvent(type="slash_budget", content=content)]


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
    """/new <question> — 建新 session (bootstrap + HITL) + 切到它.

    完整复用 cli `new` 命令路径 (bootstrap_phenomena → review_phenomena →
    SessionStore.save). 之后 yield 两个 event:
    - slash_new: info text 给用户
    - slash_switch_session: signal REPL 切到新 sid (REPL 单 turn iter 结束后做)

    失败时只 yield slash_error, 不 yield switch → REPL 留原 session.
    """
    import asyncio

    from explain_engine.chat.session import ChatEvent
    from explain_engine.config import Settings
    from explain_engine.llm.errors import LLMError, SchemaValidationError
    from explain_engine.persistence.session import (
        Session,
        SessionMeta,
        SessionStore,
    )
    from explain_engine.schema.state import CognitiveState

    question = " ".join(args).strip()
    if not question:
        return [ChatEvent(
            type="slash_error",
            content="Usage: /new <你的问题>  (例: /new 为什么 X 现象)",
        )]

    if chat.llm is None:
        return [ChatEvent(
            type="slash_error",
            content="/new 需要 LLM client; 当前 ChatSession 启动时未传 llm "
                    "(test path or backward-compat caller).",
        )]

    # Bootstrap (调 LLM). monkeypatch 友好: 函数体里 *使用* 模块顶 import 的
    # bootstrap_phenomena, 不在函数内 re-import.
    try:
        phenomena = await bootstrap_phenomena(question, chat.llm)
    except (SchemaValidationError, LLMError) as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"/new bootstrap 失败: {type(exc).__name__}: {exc}",
        )]

    # HITL review (sync stdin via Rich Prompt — 包 to_thread 不 block event loop).
    # console=None: review_phenomena 会自建一个临时 Console, 避免 cli.py 反向依赖.
    final_phenomena = await asyncio.to_thread(
        review_phenomena, phenomena, None
    )

    # 建 session + 存
    settings = Settings()
    state = CognitiveState.bootstrap(question, budget=settings.default_budget)
    for p in final_phenomena:
        state.graph.add_node(p)
    meta = SessionMeta.new(question=question)
    sess = Session(meta=meta, state=state)

    store = SessionStore()
    try:
        store.save(sess)
    except OSError as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"/new 存盘失败: {exc}",
        )]

    return [
        ChatEvent(
            type="slash_new",
            content=f"Session {meta.session_id} 已创建 ({len(final_phenomena)} 现象).",
        ),
        ChatEvent(
            type="slash_switch_session",
            content={"sid": meta.session_id},
        ),
    ]


# Registry — 7 default slash commands.
# 顺序决定 /help 列出顺序, 按"管理 → inspection → 操作"分组.
DEFAULT_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("quit", "Exit chat session (saves first).", _handle_quit),
    SlashCommand("help", "List slash commands and available tools.", _handle_help),
    SlashCommand("show", "Show graph snapshot + multi-signal.", _handle_show),
    SlashCommand("budget", "Show per-turn / per-session budget remaining.", _handle_budget),
    SlashCommand("compact", "Force trigger sessionMemory compaction.", _handle_compact),
    SlashCommand("save", "Explicit flush of all sidecar files.", _handle_save),
    SlashCommand("new", "新建 session (bootstrap + HITL) 后自动切.", _handle_new),
)


def _command_by_name(name: str) -> SlashCommand | None:
    """Linear lookup OK — 7 commands, no hash table needed."""
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
