"""Phase 9 Wave F.1 + Phase 11 Wave 3/4 + Phase 19 Wave 4: 25 default slash commands + 1 alias (/cf → counterfactual).

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
import logging
import shutil as _shutil
import tempfile as _tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING

from explain_engine.chat.chat_copy import (
    COMMAND_DESCRIPTIONS,
    HELP_GROUPS_ZH,
    INFO_INSIGHT_PENDING_RESUME,
    INFO_MID_STAGE_SAVED,
    STATUS_COMPRESS_PROPOSE,
    STATUS_COMPRESS_SCORE,
    STATUS_COUNTERFACTUAL,
    STATUS_DEEPEN_CLASSIFY,
    STATUS_LEXICON_FLUSH,
    STATUS_PREDICT,
    STATUS_RESCORE,
    STATUS_RUN,
    STATUS_THEORIES_COMPUTE,
    err_ephemeral_reject,
    err_failed,
    err_no_llm,
    err_theory_not_found,
    msg_compress_done,
    msg_rescore_done,
    msg_resume_already,
    msg_resume_switching,
    msg_run_done,
    msg_theories_cold_start,
    msg_theories_no_motif_found,
    msg_theory_rejected,
    zh,
)
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


# ── Phase 19 真终端 Bug C: long-task spinner helper (textual / Rich dispatch) ──


class _SpinnerContext:
    """async context manager — 长 LLM task spinner (textual TUI / Rich CLI 都支持).

    用法:
        async with _spinner(chat, STATUS_COMPRESS_PROPOSE):
            await propose_candidates(...)

    Dispatch 路径:
    - chat.tui_app 非 None (textual TUI 模式) → 走 tui_app._mount_status(label) /
      _unmount_status(), 在 textual VerticalScroll 内 mount LoadingIndicator + label
      static, 用户真终端可见 spinner.
    - chat.tui_app=None (batch / test / 老 CLI) → 走 Rich `Console().status(label)`
      上下文管理器, stderr 写 spinner ANSI (老行为保留).

    真因 (第一性原理):
    - Rich Console.status 写 stderr 在 textual alt screen 模式下被 capture,
      用户**看不到** spinner — 5-15s LLM 调用期间屏幕静止 = 假死.
    - textual `_mount_status` mount LoadingIndicator widget 到主输出容器,
      跟 thinking Collapsible / status_start/end event 协议同款机制.

    try/finally: LLM call 抛异常时也保证 spinner 清掉 (跟 status_start/end
    event 协议契约一致 — error path 也 yield status_end).
    """

    def __init__(self, chat: object, label: str) -> None:
        self._chat = chat
        self._label = label
        # 选 dispatch: textual tui_app 优先, fallback Rich Console.status
        self._tui_app = getattr(chat, "tui_app", None)
        # Rich fallback path 用. _rich_ctx 是 Rich Console.status return value (cm).
        self._rich_ctx: object | None = None

    async def __aenter__(self) -> _SpinnerContext:
        if self._tui_app is not None and hasattr(self._tui_app, "_mount_status"):
            # textual path: await async mount
            await self._tui_app._mount_status(self._label)
        else:
            # Rich fallback path: 进 Console.status sync context manager
            from rich.console import Console as _Console
            self._rich_ctx = _Console().status(self._label)
            self._rich_ctx.__enter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # try/finally semantics: 即使 LLM 抛异常也清 spinner
        if self._tui_app is not None and hasattr(self._tui_app, "_unmount_status"):
            try:
                await self._tui_app._unmount_status()
            except Exception:
                # 防御: spinner cleanup 失败不该掩盖原 LLM 异常
                pass
        elif self._rich_ctx is not None:
            try:
                self._rich_ctx.__exit__(exc_type, exc, tb)
            except Exception:
                pass


def _spinner(chat: object, label: str) -> _SpinnerContext:
    """factory — 给 slash handler 用的简写, 等价 _SpinnerContext(chat, label)."""
    return _SpinnerContext(chat, label)


# ── Phase 16.2: REPL history snapshot/delta helpers (Wave 2) ──

_history_logger = logging.getLogger(__name__)


def _snapshot_graph(state) -> dict[str, int]:
    """Wave 2: 取 graph 4-count (l0/l1/l2/edges), 给 history wrapper 算 delta 用."""
    g = state.graph
    return {
        "l0": sum(1 for n in g.nodes.values() if n.abstraction_level == 0),
        "l1": sum(1 for n in g.nodes.values() if n.abstraction_level == 1),
        "l2": sum(1 for n in g.nodes.values() if n.abstraction_level == 2),
        "edges": len(g.edges),
    }


def _snapshot_graph_safe(state) -> dict[str, int] | None:
    """Wave 2: snapshot 容错版 — 异常返 None, log debug. 让 wrapper 不被 graph 异常打断."""
    try:
        return _snapshot_graph(state)
    except Exception as exc:
        _history_logger.debug(f"snapshot failed: {type(exc).__name__}: {exc}")
        return None


def _compute_delta(before: dict | None, after: dict | None) -> str:
    """Wave 2: 算 before/after diff → 人话 '+1 L1 / +5 现象 / +12 边'. None 输入返 '(变化未知)'."""
    if before is None or after is None:
        return "(变化未知)"
    parts = []
    if (d := after["l1"] - before["l1"]):
        parts.append(f"{d:+d} L1")
    if (d := after["l0"] - before["l0"]):
        parts.append(f"{d:+d} 现象")
    if (d := after["l2"] - before["l2"]):
        parts.append(f"{d:+d} L2")
    if (d := after["edges"] - before["edges"]):
        parts.append(f"{d:+d} 边")
    return " / ".join(parts) if parts else "无变化"


# ── Phase 16.2 Wave 3: dispatcher wrapper (history sidecar) ──


def _extract_intervention(events) -> str | None:
    """Wave 3: 从 handler 返回的 list[ChatEvent] 反解 intervention text.

    仅 /predict /counterfactual 在 return 时往 event 塞 metadata={"intervention": ...}
    (Wave 4 落地). 此处用 getattr 兼容老 ChatEvent (无 metadata 属性).
    """
    if not events:
        return None
    for evt in events:
        meta = getattr(evt, "metadata", None)
        if meta and "intervention" in meta:
            return meta["intervention"]
    return None


def _build_history_entry(
    name: str,
    args: list[str],
    before: dict | None,
    after: dict | None,
    intervention: str | None,
    error: str | None,
) -> dict:
    """Wave 3: 拼 repl_history.jsonl entry dict (design §5.2 type=slash schema).

    error 非 None 时 summary 退化为 '(执行失败: ErrType)', error 字段塞 entry.
    intervention 非 None 时塞 entry (仅 predict/counterfactual). 否则字段省略 (不写 None).
    """
    from datetime import datetime

    ts = datetime.now(UTC).astimezone().isoformat()
    if error is None:
        summary = _compute_delta(before, after)
    else:
        summary = f"(执行失败: {error.split(':', 1)[0]})"
    entry: dict = {
        "ts": ts,
        "type": "slash",
        "cmd": name,
        "args": list(args),
        "summary": summary,
    }
    if intervention is not None:
        entry["intervention"] = intervention
    if error is not None:
        entry["error"] = error
    return entry


def _safe_append_history(chat, entry: dict) -> None:
    """Wave 3: storage.append_repl_history 失败 silent + log warn (设计 §7.1 降级).

    任何 IO/permission/disk full 错都不该 propagate, history 是 nice-to-have.
    """
    try:
        chat.storage.append_repl_history(chat.sid, entry)
    except Exception as exc:
        _history_logger.warning(
            f"append_repl_history failed for /{entry.get('cmd')}: "
            f"{type(exc).__name__}: {exc}"
        )


def _wrap_handler(name: str, handler):
    """Wave 3: 中央 wrapper — snapshot before → handler → snapshot after → 写 entry.

    设计 §4.1 + §4.4 + §7:
    - 所有 handler 零感知 wrapper 存在 (DEFAULT_COMMANDS 注册时一次套).
    - ephemeral / sid is None 时跳 history 写入 (无 sidecar 可写), 但仍正常调 handler.
    - KeyboardInterrupt 直 propagate, 不写 history (用户主动放弃, design §7.5).
    - 其他 Exception: 先写 error entry, 再 raise (用户重启后 banner 可见失败记录).
    - storage 写失败 silent + log (_safe_append_history).
    """
    async def wrapped(chat, args):
        # ephemeral 没 sid / storage, 跳 history (但 handler 仍调).
        if getattr(chat, "is_ephemeral", False) or getattr(chat, "sid", None) is None:
            return await handler(chat, args)

        before = _snapshot_graph_safe(chat.state)
        try:
            result = await handler(chat, args)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            after = _snapshot_graph_safe(chat.state)
            error_repr = f"{type(exc).__name__}: {exc}"
            entry = _build_history_entry(name, args, before, after, None, error_repr)
            _safe_append_history(chat, entry)
            raise

        after = _snapshot_graph_safe(chat.state)
        intervention = _extract_intervention(result)
        entry = _build_history_entry(name, args, before, after, intervention, None)
        _safe_append_history(chat, entry)
        return result

    # 暴露原 handler 给 test/inspection (e.g. /cf alias 验跟 /counterfactual 同
    # underlying handler 而非同 wrapped instance).
    wrapped.__wrapped__ = handler
    return wrapped


# ── Phase 16.2 Wave 5: /history slash 命令 ──

_HISTORY_VALID_TYPES = {"slash", "llm_turn"}
_HISTORY_LIMIT_MAX = 200
_HISTORY_LIMIT_DEFAULT = 30


def _parse_history_args(
    args: list[str],
) -> tuple[int, set[str] | None, str | None]:
    """Wave 5: 解析 /history 参数, 返 (limit, types_filter_or_None, err_msg_or_None).

    支持:
    - `--limit N` (1-200 整数)
    - `--all` (= --limit 200 快捷)
    - `--type T1 [T2 ...]` (slash / llm_turn, 可多选, 多选等价无过滤)
    - 不接位置参数

    types_filter:
    - None: 不过滤 (全显)
    - set: 只显含此 type 的 entry
    - 多选 dedup 后等价全集 → 也视作 None (无过滤)
    """
    from explain_engine.chat.chat_copy import (
        err_history_limit_range,
        err_history_limit_type,
        err_history_positional,
        err_history_type_invalid,
    )

    limit = _HISTORY_LIMIT_DEFAULT
    types: set[str] | None = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--all":
            limit = _HISTORY_LIMIT_MAX
            i += 1
        elif a == "--limit":
            if i + 1 >= len(args):
                return (0, None, err_history_limit_type())
            try:
                limit = int(args[i + 1])
            except ValueError:
                return (0, None, err_history_limit_type())
            if limit < 1:
                return (0, None, err_history_limit_type())
            if limit > _HISTORY_LIMIT_MAX:
                return (0, None, err_history_limit_range(limit))
            i += 2
        elif a == "--type":
            # 收一个或多个后续 token 直到下一个 -- 开头或末尾
            types_collected: list[str] = []
            j = i + 1
            while j < len(args) and not args[j].startswith("--"):
                types_collected.append(args[j])
                j += 1
            if not types_collected:
                return (0, None, err_history_type_invalid("(空)"))
            for t in types_collected:
                if t not in _HISTORY_VALID_TYPES:
                    return (0, None, err_history_type_invalid(t))
            types = set(types_collected)
            # 多选 dedup 后若覆盖全集 → 等价无过滤
            if types == _HISTORY_VALID_TYPES:
                types = None
            i = j
        else:
            return (0, None, err_history_positional())
    return (limit, types, None)


def _render_history_entry_full(entry: dict) -> str:
    """Wave 5: 渲染单 entry, 不截断 (用于 /history 输出, 跟 banner 截断的短版区分)."""
    from explain_engine.chat.chat_copy import (
        HISTORY_INTERVENTION_PREFIX,
        HISTORY_SUMMARY_PREFIX,
        HISTORY_TYPE_PREFIX_ASSISTANT,
        HISTORY_TYPE_PREFIX_USER,
    )

    raw_ts = entry.get("ts", "?")
    # ISO ts "2026-05-25T14:00:00+08:00" → "2026-05-25 14:00:00"
    ts = raw_ts[:19].replace("T", " ") if isinstance(raw_ts, str) else "?"
    typ = entry.get("type")
    if typ == "slash":
        lines = [f"[{ts}] /{entry.get('cmd', '?')}"]
        if "intervention" in entry:
            lines.append(f"  {HISTORY_INTERVENTION_PREFIX}{entry['intervention']}")
        lines.append(f"  {HISTORY_SUMMARY_PREFIX}{entry.get('summary', '?')}")
        return "\n".join(lines)
    if typ == "llm_turn":
        return (
            f"[{ts}] {HISTORY_TYPE_PREFIX_USER}{entry.get('user_input', '')}\n"
            f"  {HISTORY_TYPE_PREFIX_ASSISTANT}{entry.get('assistant_text', '')}"
        )
    return f"[{ts}] (unknown entry type: {typ})"


async def _handle_history(
    chat: ChatSession, args: list[str]
) -> list[ChatEvent]:
    """Wave 5: `/history` — 查看本 session 操作历史 (默认最近 30 条, 上限 200).

    设计 §6.2 用户接触面 + §7.6 参数边界 / §7.7 老 session 兼容.

    Args:
        --limit N (1-200): 指定显示条数
        --all: = --limit 200 快捷
        --type slash|llm_turn (可多选): 过滤 entry type
        无位置参数 (位置参数 reject)

    路径:
    - ephemeral / sid 缺失 → 友好提示, 不查 storage
    - load 失败 → slash_error event
    - 空 history → BANNER_HISTORY_EMPTY 友好提示
    - 正常 → header + 渲染 (旧→新) + footer
    """
    from explain_engine.chat.chat_copy import (
        BANNER_HISTORY_EMPTY,
        HISTORY_FOOTER,
        HISTORY_HEADER,
    )
    from explain_engine.chat.session import ChatEvent

    limit, types, err = _parse_history_args(args)
    if err is not None:
        return [ChatEvent(type="slash_error", content=err)]

    if getattr(chat, "is_ephemeral", False) or getattr(chat, "sid", None) is None:
        return [ChatEvent(
            type="slash_history", content="(ephemeral 会话, 无历史可查)"
        )]

    try:
        all_entries = chat.storage.load_repl_history(chat.sid)
    except Exception as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"读取历史失败: {type(exc).__name__}: {exc}",
        )]

    if types is not None:
        filtered = [e for e in all_entries if e.get("type") in types]
    else:
        filtered = all_entries

    total = len(filtered)
    shown_entries = filtered[-limit:] if total > limit else filtered

    if not shown_entries:
        return [ChatEvent(type="slash_history", content=BANNER_HISTORY_EMPTY)]

    body = "\n\n".join(
        _render_history_entry_full(e) for e in shown_entries
    )
    out = (
        HISTORY_HEADER.format(total=total, shown=len(shown_entries))
        + "\n\n" + body + "\n\n" + HISTORY_FOOTER
    )
    return [ChatEvent(type="slash_history", content=out)]


async def _handle_quit(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """通知 CLI 退出. 真正退出由 F.2 CLI wrapper 处理.

    本 handler 只 yield slash_quit event; CLI loop (F.2) 收到该 event
    后跳出 input loop, 调 chat.aclose() flush 后退出.
    """
    from explain_engine.chat.session import ChatEvent
    return [ChatEvent(type="slash_quit", content="再见, session 已存盘.")]


async def _handle_help(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """所有 slash 命令 6 中文分组 + tools (含 readonly / HITL flag).

    Phase 15: group name + 标题 全中文; HELP_GROUPS_ZH 引自 chat_copy.
    """
    from explain_engine.chat.session import ChatEvent
    from explain_engine.chat.tools import ALL_TOOLS

    cmd_by_name = {c.name: c for c in DEFAULT_COMMANDS}

    lines = ["所有 slash 命令 (本地处理, 不走 LLM):", ""]
    for group_name, cmd_names in HELP_GROUPS_ZH:
        lines.append(f"  {group_name}:")
        for n in cmd_names:
            c = cmd_by_name.get(n)
            if c is not None:
                lines.append(f"    /{c.name} — {c.description}")
        lines.append("")

    if "cf" in cmd_by_name:
        lines.append("  别名: /cf → /counterfactual")
        lines.append("")

    lines.append("可被 LLM 调用的工具:")
    for tool in ALL_TOOLS:
        flags = []
        if tool.is_readonly:
            flags.append("只读")
        if tool.requires_hitl:
            flags.append("需人工审查")
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
    lines.append("=== 当前 session ===")
    lines.append(f"SID:    {chat.sid}")
    lines.append(f"问题:   {chat._session.meta.question}")
    lines.append(f"阶段:   {zh(chat._session.meta.stage)}")
    lines.append("")

    # ═══ Section 2: Graph (node tree by L) ═══
    lines.append(
        f"=== 因果图 ({len(g.nodes)} 节点: {n_l0} 现象 / {n_l1} 模式 / "
        f"{n_l2} 深层原因; {n_decayed} 衰减, {n_stale} 陈旧) ==="
    )
    lines.append("")

    if len(g.nodes) == 0:
        lines.append("(空)")
        lines.append("")
    else:
        # 现象 section (L0)
        if n_l0 > 0:
            lines.append(f"[现象] ({n_l0})")
            for nid in sorted(n.id for n in g.nodes.values() if n.abstraction_level == 0):
                lines.append(f"  {_format_node_brief(state, nid, weak=nid in weak_l1_set)}")
            lines.append("")

        # 归纳出的模式 section (L1)
        if n_l1 > 0:
            l1_header = f"[归纳出的模式] ({n_l1})"
            l1_weak = [n.id for n in g.nodes.values()
                       if n.abstraction_level == 1 and n.id in weak_l1_set]
            if l1_weak:
                l1_header += f" — 薄弱因果链: {' '.join(sorted(l1_weak))}"
            lines.append(l1_header)
            for nid in sorted(n.id for n in g.nodes.values() if n.abstraction_level == 1):
                lines.append(f"  {_format_node_brief(state, nid, weak=nid in weak_l1_set)}")
            lines.append("")

        # 深层原因 section (L2) — always shown, 显 "(无)" when zero
        lines.append(f"[深层原因] ({n_l2})")
        if n_l2 == 0:
            lines.append("  (无 — 尚未推出深层原因)")
        else:
            for nid in sorted(n.id for n in g.nodes.values() if n.abstraction_level == 2):
                lines.append(f"  {_format_node_brief(state, nid)}")
        lines.append("")

    # ═══ Section 3: Edges (group by relation_type) ═══
    lines.append(f"=== 因果关系 ({len(g.edges)} 条) ===")
    lines.append("")
    if len(g.edges) == 0:
        lines.append("(无因果关系)")
        lines.append("")
    else:
        by_type: dict[str, list] = {}
        for e in g.edges.values():
            by_type.setdefault(e.relation_type, []).append(e)
        for rtype in sorted(by_type):
            edges = sorted(by_type[rtype], key=lambda e: (e.source_node, e.target_node))
            # rtype 同时显英文 raw key + 中文注释 (manifests_as「体现为」)
            rtype_zh = zh(rtype)
            if rtype_zh != rtype:
                lines.append(f"{rtype}「{rtype_zh}」({len(edges)} 条):")
            else:
                lines.append(f"{rtype} ({len(edges)} 条):")
            for edge in edges:
                lines.append(f"  {_format_edge_brief(edge)}")
            lines.append("")

    # ═══ Section 4: Multi-signal verdict ═══
    lines.append("=== 接受度评估 ===")
    if report is not None:
        lines.append(f"一致性:         {report.avg_consistency:.3f}")
        lines.append(f"本质重要性:     {report.avg_essentialness:.3f}")
        lines.append(f"覆盖率:         {report.rollout_coverage:.3f}")
        weak_ids = sorted(report.weak_chain_l1s or [])
        if weak_ids:
            lines.append(f"薄弱因果链 ({len(weak_ids)}): {' '.join(weak_ids)}")
        else:
            lines.append("薄弱因果链:     (无)")
        if report.input_alignment is not None:
            lines.append(f"输入对齐度:     {report.input_alignment:.3f}")
    else:
        lines.append(f"(接受度评估失败: {agg_err})")

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
            content="(因果图为空, 无内容可渲染)",
        )]

    # Edge: dot binary missing
    if shutil.which("dot") is None:
        return [ChatEvent(
            type="slash_graph",
            content=(
                "找不到 graphviz 的 dot 可执行文件.\n"
                "安装方式: brew install graphviz"
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
                f"渲染失败: {type(exc).__name__}: {exc}\n"
                "请检查 graphviz 是否完整 (brew reinstall graphviz) 及磁盘空间."
            ),
        )]

    # Header
    n_l0 = sum(1 for n in g.nodes.values() if n.abstraction_level == 0)
    n_l1 = sum(1 for n in g.nodes.values() if n.abstraction_level == 1)
    n_l2 = sum(1 for n in g.nodes.values() if n.abstraction_level == 2)
    header = (
        f"/graph 推理步={tick} · {len(g.nodes)} 节点 "
        f"({n_l0} 现象 / {n_l1} 模式 / {n_l2} 深层原因), {len(g.edges)} 条因果关系"
    )

    # Inline display
    #
    # 崩溃修 (BlockingIOError EAGAIN): Textual TUI 模式 (chat.tui_app 非 None) 下
    # 绝不能跑终端内联渲染器 (imgcat / kitty icat / chafa). 它们 (a) 直写 alt-screen,
    # 被 textual 立刻覆盖成乱码; (b) 查询终端能力时把 stdin 设 O_NONBLOCK 且退出
    # 不还原 → textual 输入线程的阻塞 read() 变非阻塞返 EAGAIN, 整个 app 崩溃.
    # 改用完全脱离终端的 OS 看图器 (start_new_session + 三标准流 DEVNULL, 构造上
    # 不碰 textual stdin). 非 Textual (CLI / test, tui_app=None) 保持原内联行为.
    if getattr(chat, "tui_app", None) is not None:
        inline_msg = _open_png_detached(png_path)
    else:
        cmd, renderer = _detect_inline_renderer(png_path)
        if cmd is not None:
            try:
                # stderr=DEVNULL: chafa/imgcat 任何 warning 不污染用户 terminal.
                # stdout=None: 让 chafa/imgcat 把 image bytes 写到 terminal (inline 显示必需).
                subprocess.run(cmd, check=False, stderr=subprocess.DEVNULL)
                inline_msg = f"(已通过 {renderer} 内联渲染)"
            except Exception as exc:
                inline_msg = f"(内联渲染失败 via {renderer}: {type(exc).__name__})"
        else:
            inline_msg = "(装 chafa 可内联预览: brew install chafa)"

    # Footer
    footer_lines = [
        "",
        inline_msg,
        f"PNG: {png_path}",
        "",
    ]
    if report is not None:
        footer_lines.append(
            f"接受度评估: 一致性={report.avg_consistency:.3f} "
            f"本质重要性={report.avg_essentialness:.3f} "
            f"覆盖率={report.rollout_coverage:.3f}"
        )
        weak_ids = sorted(report.weak_chain_l1s or [])
        if weak_ids:
            footer_lines.append(f"薄弱模式: {' '.join(weak_ids)}")
    else:
        footer_lines.append("接受度评估: (失败)")

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
        f"\n[bold]当前预算[/bold]\n"
        f"  本轮预算上限:     {_format_budget_value(cs.budget_per_turn_limit, cs.budget_per_turn_remaining)}\n"
        f"  本 session 上限:  {_format_budget_value(cs.budget_per_session_limit, cs.budget_per_session_remaining)}\n"
    )

    if chat.input_provider is None:
        return [ChatEvent(
            type="slash_budget",
            content="(无输入通道, 仅展示 — test/非交互模式)",
        )]

    # 收 per-turn
    try:
        new_turn_str = await chat.input_provider(
            f"新本轮上限 (回车保持 {cs.budget_per_turn_limit}, 0=无限, q 取消): "
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
                    content="本轮上限需 >= 0 (0=无限); 已取消.",
                )]
        except ValueError:
            return [ChatEvent(
                type="slash_error",
                content=f"输入非数字 {new_turn_str!r}; 已取消.",
            )]

    # 收 per-session
    try:
        new_session_str = await chat.input_provider(
            f"新 session 上限 (回车保持 {cs.budget_per_session_limit}, 0=无限, q 取消): "
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
                    content="session 上限需 >= 0 (0=无限); 已取消.",
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
            f"  本轮:    {_fmt_limit(old_turn)} → {_fmt_limit(new_turn)}\n"
            f"  session: {_fmt_limit(old_session)} → {_fmt_limit(new_session)}"
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
        content="已请求强制压缩对话记忆. 将在下次反思 tick 或经 CLI handler 执行.",
    )]


async def _handle_save(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """显式存盘 sidecar (chat_state.json + graph)."""
    from explain_engine.chat.chat_copy import msg_save_done
    from explain_engine.chat.session import ChatEvent
    chat.persist()
    return [ChatEvent(type="slash_save", content=msg_save_done(chat.sid))]


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
    """/resume [sid] — 切换到指定 session, 或弹 textual modal picker 交互式选.

    Phase 19 Wave 7 hotfix (9838507): 老版无参 picker 用 `asyncio.to_thread(input)`
    收选号, textual 已 hold stdin → 死锁无法 Ctrl+C 退. 简化为必须带 sid arg.

    Phase 19 Wave 7 follow-up (本 commit): user 反馈期望真 picker. textual
    ModalScreen + OptionList 是 native widget, 走 textual message pump,
    跟 asyncio.to_thread(input) 完全不同路径 — 不抢 stdin, 不死锁. 故无 sid
    时改为 yield `slash_open_session_picker` event, tui_app._render_event 接
    到 push SessionPickerScreen.

    用法:
        /resume <sid>     切到指定 session
        /resume           弹 textual modal picker (有 session 时); 空时 slash_error

    Event 契约:
    - slash_error: 用法错误 / sid 非法 / sid 不存在 / 空 session list (无 args)
    - slash_resume: info content (str) — 切到当前 sid 或正常 switching msg
    - slash_switch_session: content={"sid": str} — REPL 据此切
    - slash_open_session_picker: content="选择 session..." str,
      metadata={"sessions": [{"sid", "question", "stage", "created_at"}, ...],
                "current_sid": str} — REPL push SessionPickerScreen modal.
      Producer: 本 handler 无 args 路径.
      Consumer: tui_app._render_event push ModalScreen.
    """
    from explain_engine.chat.session import ChatEvent
    from explain_engine.persistence.session import SessionStore

    # 无 args → 弹 textual modal picker (有 session 时) / slash_error (空时)
    if not args:
        metas = SessionStore().list()
        if not metas:
            return [ChatEvent(
                type="slash_error",
                content=(
                    "暂无可 resume 的 session — 当前 project 还没创过 session. "
                    "可以先 /deepen 触发深度建模, 或退出用 `explain new <question>` 新建."
                ),
            )]
        # Phase 20.1 #8: sort by created_at desc — 最近创的 session 在顶
        # (跟 macOS Finder Recent / Chrome 最近标签同语义). SessionStore.list()
        # 本身已 sort, 但 handler defensive 自己再 sort 一遍 — 解耦 handler 跟
        # caller 的排序契约, picker 的 sessions[0] 永远是最新 session.
        metas_sorted = sorted(metas, key=lambda m: m.created_at, reverse=True)
        # picker 协议: 把 metas → list[dict], REPL 不引 SessionMeta 类型耦合
        sessions_payload = [
            {
                "sid": m.session_id,
                "question": m.question,
                "stage": m.stage,
                "created_at": m.created_at,
            }
            for m in metas_sorted
        ]
        return [ChatEvent(
            type="slash_open_session_picker",
            content="选择要切到的 session (↑↓ 选, Enter 确认, Esc 取消)",
            metadata={
                "sessions": sessions_payload,
                "current_sid": chat.sid,
            },
        )]
    if len(args) > 1:
        return [ChatEvent(
            type="slash_error",
            content=(
                f"/resume 只接一个 sid 参数 (got {len(args)}: {args!r}). "
                f"用法: /resume <sid>"
            ),
        )]

    target_sid = args[0].strip()
    if not target_sid:
        return [ChatEvent(
            type="slash_error",
            content="用法: /resume <sid> (sid 不能为空)",
        )]

    # 当前 sid → 已在该 session, 不 yield switch
    if target_sid == chat.sid:
        return [ChatEvent(
            type="slash_resume", content=msg_resume_already(target_sid)
        )]

    # 验 sid 存在 — 通过 SessionStore.list 拿现 metadata (跟 picker 老 path
    # 一致, 用 metadata 不读 graph 避大 IO)
    metas = SessionStore().list()
    known_sids = {m.session_id for m in metas}
    if target_sid not in known_sids:
        return [ChatEvent(
            type="slash_error",
            content=(
                f"session {target_sid!r} 不存在. 用 /list 看当前 project 所有 sid."
            ),
        )]

    return [
        ChatEvent(type="slash_resume", content=msg_resume_switching(target_sid)),
        ChatEvent(type="slash_switch_session", content={"sid": target_sid}),
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


def _open_png_detached(png_path: str) -> str:
    """Textual 模式打开 /graph PNG: 用 OS 默认看图器, 完全脱离终端.

    start_new_session=True (脱离 controlling terminal / 进程组) + 三标准流全
    DEVNULL → 子进程构造上不可能碰 textual 的 stdin (即不会复现 BlockingIOError
    崩溃). Popen 不 wait, 立刻返回 (open/xdg-open 异步起看图器).

    best-effort: 无 opener (非 mac/linux 或 PATH 缺) / spawn 失败 → 退化为仅
    提示看下方 PNG 路径 (footer 已打印 `PNG: {path}`).

    返 user-visible inline_msg 文案.
    """
    import shutil
    import subprocess
    import sys

    opener: str | None = None
    if sys.platform == "darwin":
        opener = "open"
    elif sys.platform.startswith("linux"):
        opener = "xdg-open"

    if opener is not None and shutil.which(opener) is not None:
        try:
            subprocess.Popen(
                [opener, png_path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return "(TUI 内无法内联预览, 已用系统看图器打开 PNG)"
        except Exception:
            pass
    return "(TUI 内无法内联预览, 见下方 PNG 路径)"


def _ephemeral_reject(name: str) -> list[ChatEvent]:
    """Phase 11 Wave 3: ephemeral 时统一 reject 模板.

    单 session slash 都要求真 session — graph 是空的, 跑没意义.
    Phase 15: 引 err_ephemeral_reject (chat_copy single source).
    """
    from explain_engine.chat.session import ChatEvent
    return [ChatEvent(type="slash_error", content=err_ephemeral_reject(name))]


@with_stage_gate(
    # Phase 17.1 Wave 8: +done/converged 允许重入 — /predict 加新 L0 后用户可
    # 再次 /compress 把新 L0 归 L1. 重复 L1 由 lexicon dedup (cosine > 0.85) 兜底.
    allowed=["bootstrap_pending", "insight_pending", "done", "converged"],
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
        return [ChatEvent(type="slash_error", content=err_no_llm("compress"))]

    from rich.console import Console

    from explain_engine.engines.compression import propose_candidates
    from explain_engine.engines.evaluation import score_all
    from explain_engine.engines.lexicon import flush_to_lexicon
    from explain_engine.hitl.cli_interactive import review_insights_async

    _console = Console()

    # Phase 14 Task 15: ip 入口短路 — 上次 propose+score 完 (mid-stage Task 14),
    # 但 review 被取消 → 重入跳过 LLM 直接进 review.
    if chat._session.meta.stage == "insight_pending":
        _console.print(INFO_INSIGHT_PENDING_RESUME)
        # ip 入口: 直接 fallback dedup_stats — 上次 propose 已落 candidates,
        # 但 embedding stats 缺. Display "0 near-dup / N new" 占位.
        dedup_stats = {"reused": 0, "new": len(chat.state.insight_candidates)}
    else:
        # bp 入口 (default): propose + score + mid-stage persist
        try:
            import asyncio

            from explain_engine.engines.lexicon import get_lexicon_top_k_for_compress
            # 同步 (PG 查询 / JSON 读) 卸到线程池 — 防 PG 网络 IO 阻塞 event loop.
            top_k = await asyncio.to_thread(get_lexicon_top_k_for_compress, chat.storage, k=20)
            # Phase 19 真终端 Bug C: _spinner helper 走 textual tui_app 或 Rich fallback.
            async with _spinner(chat, STATUS_COMPRESS_PROPOSE):
                await propose_candidates(chat.state, chat.llm, existing_lexicon=top_k)
        except Exception as exc:
            return [ChatEvent(type="slash_error", content=err_failed("compress", exc))]

        # Phase 13 Wave 3 Task 4: compute dedup stats for UI display (observational,
        # doesn't mutate state). Display threshold 0.75 (lower than 0.85 merge
        # threshold) accounts for proxy-text format mismatch with lexicon canonical.
        import asyncio
        import logging

        from explain_engine.engines.compress_dedup import compute_compress_dedup_stats
        try:
            # compute_compress_dedup_stats 同步跑 BGE-M3 torch embedding (秒级,
            # 占 GIL). 在 chat REPL event loop 上直接调会冻结整个 TUI (这是
            # /compress 卡死 + 无法滚动/输入的根因). 卸到线程池让 loop 空闲,
            # 配合 slash 后台 task 让 escape 可中断.
            dedup_stats = await asyncio.to_thread(
                compute_compress_dedup_stats,
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
            # Phase 19 真终端 Bug C: _spinner helper.
            async with _spinner(chat, STATUS_COMPRESS_SCORE):
                await score_all(chat.state, chat.llm)
        except Exception as exc:
            return [ChatEvent(type="slash_error", content=err_failed("compress", exc))]

        # Phase 14 Task 14: mid-stage persist (中断恢复). propose+score 完后,
        # review 之前, 推 stage → insight_pending + 立刻落盘. 即便 review 取消
        # (KeyboardInterrupt) 也能下次重入跳过 LLM (Task 15 短路).
        chat._session.meta.stage = "insight_pending"
        chat.persist()
        _console.print(INFO_MID_STAGE_SAVED)

    # Phase X2: 候选默认全采纳, 不再逐条阻塞审查 (兑现交互成本预算). 用户随时
    # /review 回看修订. 传 input_provider=None 走 review_insights_async 的
    # accept-all 路径 (全 keep + 清空 insight_candidates).
    await review_insights_async(chat.state, None)

    # persist sidecar + flush lexicon (best-effort — lexicon 失败不该 fail compress)
    chat.persist()
    n = 0
    # Phase 17.2 Task 7 (final review fix): light_llm 给 flush_to_lexicon 走 cheap
    # model 生 canonical_mechanism. lazy 调 make_light_llm_client (跟 ephemeral.py
    # 同 pattern); LLM_LIGHT_* 未配则 fallback 主 LLM.
    from explain_engine.config import make_light_llm_client
    try:
        light_llm = make_light_llm_client()
    except Exception:
        light_llm = None
    try:
        # Phase 19 真终端 Bug C: _spinner helper.
        async with _spinner(chat, STATUS_LEXICON_FLUSH):
            n = await flush_to_lexicon(
                chat._session, chat.storage,
                llm=chat.llm, light_llm=light_llm,
            )
    except Exception:
        n = 0

    n_candidates = len(chat.state.insight_candidates)
    return [ChatEvent(
        type="slash_compress",
        content=msg_compress_done(
            n_candidates=n_candidates,
            n_to_lexicon=n,
            dedup_reused=dedup_stats["reused"],
            dedup_new=dedup_stats["new"],
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
        return [ChatEvent(type="slash_error", content=err_no_llm("run"))]

    from explain_engine.runtime.runtime import run as runtime_run

    # Phase 15.1 hotfix: 若 chat_state.budget_per_session_limit == 0 (用户 /budget
    # 设无限), /run 也应不受 state.budget_remaining 限制 — chat 用户的 mental
    # model 是"无限"一处设全场动. 否则保留老行为 max(state.budget_remaining, 1).
    # 1e9 实际等价无限 (跑不到这么多 tick).
    if chat.chat_state.budget_per_session_limit == 0:
        budget = 10**9
    else:
        budget = max(chat.state.budget_remaining, 1)
    try:
        # Phase 19 真终端 Bug C: _spinner helper — textual tui_app spinner 真显;
        # batch/test 模式 fallback Rich Console.status (老行为). runtime.run 跑推理
        # 循环 (多次 LLM 调用, 总耗时几十秒到几分钟取决于 budget).
        async with _spinner(chat, STATUS_RUN):
            reason = await runtime_run(chat.state, chat.llm, budget=budget)
    except Exception as exc:
        return [ChatEvent(type="slash_error", content=err_failed("run", exc))]

    chat.persist()

    # ── Phase A 机器提案: 收敛后自动增量接地 (best-effort, 失败不阻断) ──
    ground_note = ""
    try:
        from explain_engine.config import make_light_llm_client
        from explain_engine.engines.grounding import ground_state

        async with _spinner(chat, "自动接地新对象 (增量)"):
            gsum = await ground_state(chat.state, make_light_llm_client())
        chat.persist()
        if gsum.targets_total:
            ground_note = (
                f"\n已自动接地 {gsum.targets_total} 个新对象: "
                f"实证 {gsum.verified} / 争议 {gsum.contested} / "
                f"未验证 {gsum.unverified}; "
                f"{gsum.edges_reweighted} 条边按证据等级重新加权。"
            )
    except Exception as exc:
        ground_note = (
            f"\n(自动接地失败, 可稍后用 explain ground 重试: "
            f"{type(exc).__name__})"
        )

    return [ChatEvent(
        type="slash_run",
        content=msg_run_done(stop_reason=reason, tick=chat.state.tick) + ground_note,
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
        return [ChatEvent(type="slash_error", content=err_failed("check", exc))]

    weak_str = ", ".join(report.weak_chain_l1s) or "(无)"
    missing_str = ", ".join(report.missing_l0) or "(无)"
    return [ChatEvent(
        type="slash_check",
        content=(
            f"接受度评估:\n"
            f"  一致性:         {report.avg_consistency:.3f}\n"
            f"  本质重要性:     {report.avg_essentialness:.3f}\n"
            f"  覆盖率:         {report.rollout_coverage:.3f}\n"
            f"  薄弱因果链:     {weak_str}\n"
            f"  缺失现象:       {missing_str}"
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
        return [ChatEvent(type="slash_error", content=err_no_llm("predict"))]

    if chat.input_provider is None:
        return [ChatEvent(
            type="slash_error",
            content="/predict 需要在交互模式下运行 (当前无 input 通道).",
        )]

    try:
        intervention = (await chat.input_provider(
            "干预描述 (e.g. '如果 X 增加', q 取消): "
        )).strip()
    except (EOFError, KeyboardInterrupt):
        return [ChatEvent(type="slash_predict", content="已取消.")]

    if not intervention or intervention.lower() in ("q", "quit"):
        return [ChatEvent(type="slash_predict", content="已取消.")]

    from explain_engine.engines.prediction import predict as prediction_predict
    try:
        # Phase 19 真终端 Bug C: _spinner helper — prediction LLM 调用 (~5-15s).
        async with _spinner(chat, STATUS_PREDICT):
            report = await prediction_predict(chat.state, intervention, chat.llm)
    except Exception as exc:
        return [ChatEvent(type="slash_error", content=err_failed("predict", exc))]

    chat.persist()

    # Fix 3 (2026-05-19 smoke bug 2): 显 node.name + description 而非裸 ID.
    new_nodes_block = _format_node_list(chat.state, report.new_node_ids)
    predicted_block = _format_node_list(chat.state, report.predicted_L0_ids)
    activated_block = _format_node_list(chat.state, report.activated_existing_L0)

    # Fix 2: top-3 propagation_acts display (新 concept 经因果关系影响现因果图节点).
    prop_acts = getattr(report, "propagation_acts", {})
    if prop_acts:
        top_acts = sorted(prop_acts.items(), key=lambda kv: -abs(kv[1]))[:3]
        prop_lines = "\n".join(
            f"    {act:+.2f}  {_format_node_brief(chat.state, nid)}"
            for nid, act in top_acts
        )
    else:
        prop_lines = "    (无影响传导, 干预跟现因果图无关联)"

    return [ChatEvent(
        type="slash_predict",
        content=(
            f"预测结果 (干预: {intervention!r}):\n"
            f"  新增节点:\n{new_nodes_block}\n"
            f"  预测的现象:\n{predicted_block}\n"
            f"  激活的已有现象:\n{activated_block}\n"
            f"  影响最大的 3 个中间节点 (正负号 = 激活变化方向):\n"
            f"{prop_lines}"
        ),
        metadata={"intervention": intervention},
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
        return [ChatEvent(type="slash_error", content=err_no_llm("counterfactual"))]

    if chat.input_provider is None:
        return [ChatEvent(
            type="slash_error",
            content="/counterfactual 需要在交互模式下运行 (当前无 input 通道).",
        )]

    try:
        intervention = (await chat.input_provider(
            "反事实假设 (e.g. '若用 X 替代 Y', q 取消): "
        )).strip()
    except (EOFError, KeyboardInterrupt):
        return [ChatEvent(type="slash_counterfactual", content="已取消.")]

    if not intervention or intervention.lower() in ("q", "quit"):
        return [ChatEvent(type="slash_counterfactual", content="已取消.")]

    from explain_engine.engines.counterfactual import substitute
    try:
        # Phase 19 真终端 Bug C: _spinner helper — counterfactual LLM 调用 (~5-15s).
        async with _spinner(chat, STATUS_COUNTERFACTUAL):
            report = await substitute(chat.state, intervention, chat.llm)
    except Exception as exc:
        return [ChatEvent(type="slash_error", content=err_failed("counterfactual", exc))]

    # 副作用 = 0, 不 persist.
    removed_str = ", ".join(report.removed_node_ids) or "(无)"
    added_str = ", ".join(report.added_node_ids) or "(无)"
    # Top |diff| > 0.05 (跟 cli 同 cutoff)
    sig_diff = sorted(
        [(nid, v) for nid, v in report.activation_diff.items() if abs(v) > 0.05],
        key=lambda kv: -abs(kv[1]),
    )[:5]
    diff_lines = (
        "\n".join(f"    {nid}: {v:+.2f}" for nid, v in sig_diff)
        if sig_diff else "    (无明显差异)"
    )

    content_lines = [
        f"反事实分析 (假设: {intervention!r}):",
        f"  移除:    {removed_str}",
        f"  替代后:  {added_str}",
        "  影响最大的差异 (原因果图 - 反事实):",
        diff_lines,
    ]
    if report.alt_narrative:
        content_lines.append(f"  替代叙事: {report.alt_narrative}")

    return [ChatEvent(
        type="slash_counterfactual",
        content="\n".join(content_lines),
        metadata={"intervention": intervention},
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
        return [ChatEvent(type="slash_error", content=err_no_llm("rescore"))]

    from explain_engine.engines.rescore import rescore_session

    try:
        # Phase 19 真终端 Bug C: _spinner helper — rescore LLM 多调用 (~25 LLM call).
        async with _spinner(chat, STATUS_RESCORE):
            new_confs = await rescore_session(chat.state, chat.llm)
    except Exception as exc:
        return [ChatEvent(type="slash_error", content=err_failed("rescore", exc))]

    chat.persist()
    if not new_confs:
        return [ChatEvent(
            type="slash_rescore",
            content="重评完成: 无可 rescore 的因果关系 (体现为 / 导致 类型).",
        )]

    avg = sum(new_confs.values()) / len(new_confs)
    return [ChatEvent(
        type="slash_rescore",
        content=msg_rescore_done(n_edges=len(new_confs), avg_conf=avg),
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
            content="当前项目无任何 session.",
        )]

    table = Table(title=f"Session 列表 ({len(metas)})")
    table.add_column("ID", style="cyan")
    table.add_column("问题", style="bold")
    table.add_column("阶段")
    table.add_column("创建时间")
    for m in metas:
        ts = datetime.fromtimestamp(m.created_at).strftime("%Y-%m-%d %H:%M")
        table.add_row(m.session_id, m.question, zh(m.stage), ts)

    return [ChatEvent(
        type="slash_list",
        content=_render_table_to_string(table),
    )]


async def _handle_delete(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 17.2 Feature C: 删某个 session.

    用法:
      /delete <sid> --force    # 必须 --force, 简化版无真二次 confirm
                                # (chat REPL 内嵌 confirm I/O 复杂, 此 phase 跳过)

    边界:
    - 无参数 / 仅 --force → slash_error 提示用法
    - 不带 --force → slash_error 提示加 --force
    - 删当前活动 session (chat.sid == sid) → 拒绝, 提示 /resume 或 /new
    - 不存在 sid → err_delete_not_found
    - 删成功 → slash_delete event (msg_delete_done)

    Lexicon 不动 — PG var.source_sessions 可保留 dangling sid, 长期跨 session
    累积价值仍在 (设计决定, 见 docs/plans/2026-05-26-phase-17.2-design.md
    Feature C "不动 lexicon" 段).
    """
    from explain_engine.chat.chat_copy import (
        err_delete_active,
        err_delete_not_found,
        msg_delete_done,
    )
    from explain_engine.chat.session import ChatEvent
    from explain_engine.persistence.session import SessionStore

    force = "--force" in args
    pos_args = [a for a in args if a != "--force"]
    if not pos_args:
        return [ChatEvent(
            type="slash_error",
            content="用法: /delete <sid> --force",
        )]
    sid = pos_args[0]

    # 拒绝删自身 — 否则 chat REPL 状态会指向已删 session, 行为不可恢复.
    if chat.sid == sid:
        return [ChatEvent(
            type="slash_error",
            content=err_delete_active(sid),
        )]

    if not force:
        return [ChatEvent(
            type="slash_error",
            content=(
                f"/delete <sid> 需加 --force (chat 内简化为强制 flag, "
                f"误删风险大). 用法: /delete {sid} --force"
            ),
        )]

    store = SessionStore()
    try:
        store.delete(sid)
    except FileNotFoundError:
        return [ChatEvent(
            type="slash_error",
            content=err_delete_not_found(sid),
        )]
    except ValueError as exc:
        # Phase 17.2 Wave 3 review fix (C-1): StorageV2 拒绝非法 sid 格式 →
        # 友好 slash_error, 不抛 raw exception.
        return [ChatEvent(
            type="slash_error",
            content=f"非法 session id: {exc}",
        )]
    except OSError as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"删除失败 (IO/权限): {exc}",
        )]

    return [ChatEvent(
        type="slash_delete",
        content=msg_delete_done(sid),
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
            content="概念库暂无变量. 跑 /compress 或退出 chat 即可写入概念库.",
        )]

    # abstraction_level 0/1/2 翻成中文短语
    level_zh_map = {0: "现象", 1: "模式", 2: "深层原因"}

    table = Table(title=f"概念库 ({len(variables)} 项)")
    table.add_column("全局 ID", style="cyan")
    table.add_column("名称", style="bold")
    table.add_column("层级", justify="right")
    table.add_column("被复用次数", justify="right")
    table.add_column("平均本质重要性", justify="right")
    table.add_column("最近使用", style="dim")
    for v in variables:
        lvl = v["abstraction_level"]
        table.add_row(
            v["global_id"],
            v["name"],
            level_zh_map.get(lvl, f"L{lvl}"),
            str(v["fitness"]["reuse_count"]),
            f"{v['fitness']['avg_essentialness']:.2f}",
            v["fitness"]["last_seen_at"][:10],
        )

    return [ChatEvent(
        type="slash_lexicon",
        content=_render_table_to_string(table),
    )]


async def _handle_theories(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 16 Task 13: /theories — 显跨 session 因果模式 (theory list).

    Read-only cross-session inspect (跟 /list /lexicon 一致, ephemeral 也 work).
    无 mutate, 不 persist (recompute 自动 atomic write theories.json).

    Args:
      --all      : 同时显 tentative (default 只 stable)
      --limit=N  : top-N (default 10)
    """
    from rich.table import Table

    from explain_engine.chat.session import ChatEvent
    from explain_engine.engines.theory.cache import get_active_theories
    from explain_engine.persistence.storage_v2 import StorageV2

    show_all = "--all" in args
    limit_arg = 10
    for a in args:
        if a.startswith("--limit="):
            try:
                limit_arg = int(a.split("=", 1)[1])
            except (ValueError, IndexError):
                pass

    storage = StorageV2()
    # Phase 16 design hotfix: ChatSession 无 .embedder attr 但 BGE-M3 是 process
    # singleton — 用 get_embedder() 拿. EXPLAIN_EMBEDDING_DISABLED=1 (test env)
    # 或 load 失败 → fallback None (走 stale cache, plan §5.4 degraded mode).
    embedder: object | None
    try:
        from explain_engine.embedding.bge_m3 import get_embedder
        embedder = get_embedder()
    except Exception:
        embedder = None

    try:
        # Phase 19 真终端 Bug C: _spinner helper.
        async with _spinner(chat, STATUS_THEORIES_COMPUTE):
            cache = get_active_theories(storage, embedder=embedder)
    except Exception as exc:
        return [ChatEvent(type="slash_error", content=err_failed("theories", exc))]

    n_sessions = len(cache.session_ids_snapshot)
    if n_sessions < cache.cold_start_threshold:
        return [ChatEvent(
            type="slash_theories",
            content=msg_theories_cold_start(n_sessions, cache.cold_start_threshold),
        )]

    all_theories = list(cache.stable_theories)
    if show_all:
        all_theories.extend(cache.tentative_theories)
    visible = [t for t in all_theories if t.id not in cache.rejected_theory_ids][:limit_arg]

    if not visible:
        return [ChatEvent(
            type="slash_theories",
            content=msg_theories_no_motif_found(n_sessions),
        )]

    type_zh_map = {"chain": "因果链", "star": "星型", "cycle": "环路"}
    table = Table(
        title=(
            f"跨 session 因果模式 (stable: {len(cache.stable_theories)}, "
            f"tentative: {len(cache.tentative_theories)}, "
            f"rejected: {len(cache.rejected_theory_ids)})"
        )
    )
    table.add_column("ID", style="cyan")
    table.add_column("类型")
    table.add_column("模式", style="bold", max_width=50)
    table.add_column("覆盖 session", justify="right")
    table.add_column("预测准确度", justify="right")
    table.add_column("状态")

    for t in visible:
        type_zh = type_zh_map.get(t.motif_type, t.motif_type)
        status_zh = "已稳定" if t.stability_status == "stable" else "待观察"
        rejected_mark = " (已拒绝)" if t.id in cache.rejected_theory_ids else ""
        table.add_row(
            t.id, type_zh, t.natural_language_summary,
            f"{len(t.supporting_sessions)}/{n_sessions}",
            f"{t.predictive_power:.0%}",
            status_zh + rejected_mark,
        )

    return [ChatEvent(
        type="slash_theories",
        content=_render_table_to_string(table),
    )]


async def _handle_theory(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase 16 Task 14: /theory <id> [reject] — 看 theory 详情 / 拒绝它.

    Usage:
        /theory <id>           显详情 (subgraph + theme list + supporting session)
        /theory <id> reject    加入 rejected_theory_ids, 后续不 bootstrap inject

    Reject 模式:
    - reject_theory(storage, id) 返 True (存在 + mark, 含 idempotent) 或 False
      (不存在). False → err_theory_not_found.
    - 不加 confirm prompt (plan 没要求, idempotent so 误操作 cost 低).

    详情模式:
    - 走 BGE-M3 singleton (跟 Task 13 同款) get_embedder() 拿 cache.
    - cache 内无 theory_id → err_theory_not_found.
    - 渲: 自然语言描述 / 类型 (因果链/星型/环路) / 状态 (已稳定 / 待观察 / 已拒绝)
      / 覆盖 session 数 / 预测准确度 / 首次+最近 session / theme list (含
      前 5 member preview) / 因果边 (zh(relation_type)) / 支撑 session list
      (前 10 + 总数).
    """
    from explain_engine.chat.session import ChatEvent
    from explain_engine.engines.theory.cache import (
        get_active_theories,
        reject_theory,
    )
    from explain_engine.persistence.storage_v2 import StorageV2

    if not args:
        return [ChatEvent(
            type="slash_error",
            content="用法: /theory <id> [reject]",
        )]

    theory_id = args[0]
    is_reject = len(args) > 1 and args[1] == "reject"

    storage = StorageV2()

    if is_reject:
        try:
            ok = reject_theory(storage, theory_id)
        except Exception as exc:
            return [ChatEvent(type="slash_error", content=err_failed("theory", exc))]
        if not ok:
            return [ChatEvent(type="slash_error", content=err_theory_not_found(theory_id))]
        return [ChatEvent(type="slash_theory", content=msg_theory_rejected(theory_id))]

    # 详情模式 — 同 Task 13 用 BGE-M3 singleton (chat 无 .embedder attr).
    # EXPLAIN_EMBEDDING_DISABLED=1 (test env) → embedder=None → 走 stale cache.
    embedder: object | None
    try:
        from explain_engine.embedding.bge_m3 import get_embedder
        embedder = get_embedder()
    except Exception:
        embedder = None

    try:
        cache = get_active_theories(storage, embedder=embedder)
    except Exception as exc:
        return [ChatEvent(type="slash_error", content=err_failed("theory", exc))]

    all_theories = cache.stable_theories + cache.tentative_theories
    theory = next((t for t in all_theories if t.id == theory_id), None)
    if theory is None:
        return [ChatEvent(type="slash_error", content=err_theory_not_found(theory_id))]

    # 渲染详情
    type_zh_map = {"chain": "因果链", "star": "星型", "cycle": "环路"}
    motif_type_zh = type_zh_map.get(theory.motif_type, theory.motif_type)
    status_zh = "已稳定" if theory.stability_status == "stable" else "待观察"
    if theory.id in cache.rejected_theory_ids:
        status_zh += " (已拒绝)"

    lines = [
        f"=== Theory {theory.id} ===",
        f"模式: {theory.natural_language_summary}",
        f"类型: {motif_type_zh}",
        f"状态: {status_zh}",
        f"覆盖 session: {len(theory.supporting_sessions)}/{len(cache.session_ids_snapshot)}",
        f"预测准确度: {theory.predictive_power:.0%}",
        f"首次发现: {theory.first_seen_session}",
        f"最近出现: {theory.last_seen_session}",
        "",
        "=== 涉及的 theme ===",
    ]

    theme_by_id = {th.id: th for th in cache.themes}
    for theme_id in theory.theme_ids:
        th = theme_by_id.get(theme_id)
        if th is not None:
            members_preview = ", ".join(th.member_global_ids[:5])
            if len(th.member_global_ids) > 5:
                members_preview += f"... (共 {len(th.member_global_ids)} 个)"
            lines.append(
                f"  · {th.name} (含 {len(th.member_global_ids)} 个变量: {members_preview})"
            )
    lines.append("")
    lines.append("=== 因果结构 ===")
    for src, tgt, rel in theory.edges:
        rel_zh = zh(rel)
        lines.append(f"  {src} {rel_zh} {tgt}")
    lines.append("")
    lines.append("=== 支撑 session ===")
    for sid in theory.supporting_sessions[:10]:
        lines.append(f"  · {sid}")
    if len(theory.supporting_sessions) > 10:
        lines.append(f"  ... 还有 {len(theory.supporting_sessions) - 10} 个")

    return [ChatEvent(type="slash_theory", content="\n".join(lines))]


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
        return [ChatEvent(type="slash_error", content=err_failed("migrate", exc))]

    if not sids:
        return [ChatEvent(
            type="slash_migrate",
            content="无老 session 文件需迁移 (或目录不存在).",
        )]

    n = len(sids)
    if chat.input_provider is None:
        return [ChatEvent(
            type="slash_migrate",
            content=(
                f"检测到 {n} 个老 session 文件: {sids}. "
                f"需在交互模式 (chat REPL) 中调用 /migrate 确认; "
                f"当前无输入通道, 跳过."
            ),
        )]

    try:
        confirm = (await chat.input_provider(
            f"将迁移 {n} 个 session 到 ~/.explain/projects/<proj>/sessions/. "
            f"确认 (y/n)? "
        )).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return [ChatEvent(type="slash_migrate", content="已取消.")]

    if confirm not in ("y", "yes"):
        return [ChatEvent(type="slash_migrate", content="已取消.")]

    try:
        results = await asyncio.to_thread(migrate_all, dry_run=False)
    except Exception as exc:
        return [ChatEvent(type="slash_error", content=err_failed("migrate", exc))]

    migrated = [r["sid"] for r in results if r["migrated"]]
    skipped = [(r["sid"], r["reason"]) for r in results if not r["migrated"]]

    lines = [f"成功迁移 {len(migrated)}/{len(results)} 个 session."]
    if migrated:
        head = migrated[:5]
        tail = "..." if len(migrated) > 5 else ""
        lines.append(f"  已迁移: {head}{tail}")
    if skipped:
        lines.append(f"  跳过 ({len(skipped)}):")
        for sid, reason in skipped[:5]:
            lines.append(f"    {sid}: {reason}")

    return [ChatEvent(
        type="slash_migrate",
        content="\n".join(lines),
    )]


# ─────────────────────────────────────────────────────────────────────────
# Phase 18 Task 9-14: /deepen — ephemeral 显式触发 bootstrap pipeline.
#
# Hybrid 设计 (chat REPL 默 system-1, /deepen escalate 到 reasoning pipeline):
# - ephemeral 状态: /deepen [Q] 走 promote_to_persistent (= 现 bootstrap pipeline)
# - 已 promote 的 ChatSession 内: /deepen 拒绝 + 提示 /new (一 session 一 /deepen)
# - 不带参 → 取 transcript 末最近 user msg fallback
# - transcript 空 + 不带参 → slash_error 用法提示
# - promote 失败 → slash_error 保留 ephemeral (REPL outer loop 不切 chat var)
# ─────────────────────────────────────────────────────────────────────────


async def _handle_deepen(chat, args: list[str]) -> list[ChatEvent]:
    """Phase 18: /deepen [question] — ephemeral 状态显式触发 bootstrap pipeline.

    Wave 2 review (I-1, M-1, M-2 fix):
    - 用 `getattr(chat, "is_ephemeral", False)` duck-typing 跟现 19 handler 一致,
      去掉局部 `from explain_engine.chat.ephemeral import EphemeralChatSession`.
    - 删 `_llm_for_test` test-only fallback — test 改用显式
      EphemeralChatSession(llm=MagicMock()) 注入.
    - promote 失败用 `err_failed("deepen", exc)` 复用现 chat_copy helper.
    """
    from explain_engine.chat.chat_copy import (
        err_deepen_already_promoted,
        err_deepen_no_question,
        msg_deepen_promote_start,
    )
    from explain_engine.chat.session import ChatEvent

    # 已 promote 的 ChatSession 内 /deepen → 拒绝 + 提示 /new
    if not getattr(chat, "is_ephemeral", False):
        # ChatSession.state 是 CognitiveState (field=root_question, 非 question);
        # _handle_show 用 chat._session.meta.question, 这里跟它一致.
        sess_meta = getattr(chat, "_session", None)
        if sess_meta is not None and hasattr(sess_meta, "meta"):
            current_q = sess_meta.meta.question
        else:
            current_q = getattr(getattr(chat, "state", None), "root_question", "?")
        return [ChatEvent(
            type="slash_error",
            content=err_deepen_already_promoted(current_q),
        )]

    # 取 question: 带参用参; 不带参倒序取 transcript 末 role=user
    if args:
        question = " ".join(args)
    else:
        question = None
        for msg in reversed(chat.transcript):
            if msg.get("role") == "user":
                question = msg["content"]
                break
        if question is None:
            return [ChatEvent(type="slash_error", content=err_deepen_no_question())]

    # LLM: Task 12 加 EphemeralChatSession.llm field; Task 16 让 REPL outer loop
    # 构造时注入. Test 路径直接 EphemeralChatSession(llm=MagicMock()), 不再
    # 走 _llm_for_test fallback (M-1).
    llm = getattr(chat, "llm", None)
    if llm is None:
        return [ChatEvent(
            type="slash_error",
            content="LLM 未配置, 无法 /deepen. 设 LLM_* env 后重启 REPL.",
        )]

    # Phase 19 Task 10: promote_to_persistent 跑 bootstrap pipeline (classify +
    # variable_extraction + HITL review + persist), 5-10s 长. 包对 status_start/end
    # event 让 textual TUI 在期间 visible 显示 LoadingIndicator.
    # 失败也 yield status_end 保 spinner 一定清.
    events: list[ChatEvent] = [
        ChatEvent(type="status_start", content=STATUS_DEEPEN_CLASSIFY),
    ]
    try:
        real_chat = await chat.promote_to_persistent(question, llm)
    except Exception as exc:
        events.append(ChatEvent(type="status_end", content=None))
        events.append(ChatEvent(
            type="slash_error",
            content=err_failed("deepen", exc),
        ))
        return events

    events.append(ChatEvent(type="status_end", content=None))
    # Phase X2: 现象默认全采纳, promote 消息追加一行采纳提示 + /review 修订入口
    # (验收标准 #1). 合进同一 event content, 避免第二个 slash_deepen_promoted
    # 触发 tui _switch_to_chat_session 缺 sid 报错.
    from explain_engine.chat.chat_copy import msg_phenomena_auto_accepted
    n_phen = sum(
        1 for n in real_chat.state.graph.nodes.values()
        if n.abstraction_level == 0
    )
    events.append(ChatEvent(
        type="slash_deepen_promoted",
        content=(
            msg_deepen_promote_start(question)
            + "\n" + msg_phenomena_auto_accepted(n_phen)
        ),
        metadata={"sid": real_chat.sid},
    ))
    return events


# ─────────────────────────────────────────────────────────────────────────
# Phase 19 Wave 4 Task 21: /thinking on|off — 切 thinking Collapsible 折叠
# ─────────────────────────────────────────────────────────────────────────
# slash 跟 chat session lifecycle 完全解耦, 仅 yield slash_thinking_toggle
# event 带 metadata={"visible": bool}. textual tui_app._render_event 接 →
# 同 action_toggle_thinking 等价 (切 _thinking_visible + 同步现 mount
# Collapsible.collapsed). 同时给用户一个 zh msg 反馈.
# ─────────────────────────────────────────────────────────────────────────


async def _handle_thinking(chat, args: list[str]) -> list[ChatEvent]:
    """Phase 19 Task 21: /thinking on|off — 切 tui_app 的 thinking 折叠状态.

    - /thinking on → slash_thinking_toggle metadata.visible=True + zh msg
    - /thinking off → slash_thinking_toggle metadata.visible=False + zh msg
    - 无参 / 错参 (非 on/off) → slash_error 用法提示
    """
    from explain_engine.chat.chat_copy import (
        err_thinking_usage,
        msg_thinking_off,
        msg_thinking_on,
    )
    from explain_engine.chat.session import ChatEvent

    if len(args) != 1 or args[0] not in ("on", "off"):
        return [ChatEvent(type="slash_error", content=err_thinking_usage())]

    visible = args[0] == "on"
    msg = msg_thinking_on() if visible else msg_thinking_off()
    return [
        ChatEvent(
            type="slash_thinking_toggle",
            content=msg,
            metadata={"visible": visible},
        ),
    ]


# ─────────────────────────────────────────────────────────────────────────
# Phase 20.4: /llm — 管理 LLM provider (base_url / api_key / model, 多 profile)
# ─────────────────────────────────────────────────────────────────────────
# 适配 anthropic + openai 两协议. 跟 chat session lifecycle 解耦 (纯配置管理):
# - /llm (无参) / /llm config → slash_open_llm_config event, tui_app 接到 push
#   LLMConfigScreen modal (列表: 激活/新增/编辑/删除; 改完热重载当前 session 的
#   app.llm / light_llm / chat.llm).
# - /llm show → 文本列出当前 profiles + active (无 TUI 也能看, 测试友好).
# - /llm use <name> → 直接切 active 不弹窗 (顺手快捷); 切完仍需 tui 热重载,
#   故 yield slash_llm_reload event 让 tui rebuild client.
# ─────────────────────────────────────────────────────────────────────────


def _mask_key(key: str) -> str:
    """api_key 脱敏显示: 前 4 + … + 后 4 (短 key 全 ★)。"""
    if len(key) <= 8:
        return "★" * len(key)
    return f"{key[:4]}…{key[-4:]}"


async def _handle_llm(chat, args: list[str]) -> list[ChatEvent]:
    """Phase 20.4: /llm — 管理 LLM provider 配置。

    用法:
        /llm | /llm config   弹 LLMConfigScreen 管理弹窗 (增删改切)
        /llm show            文本列出 profiles + active (无 TUI 也可用)
        /llm use <name>      切 active 到 <name> 并热重载 (不弹窗)

    Event 契约:
    - slash_open_llm_config: content=hint str, metadata=None — tui push modal.
    - slash_llm: content=str — 纯文本信息 (show / use 结果), tui mount Static.
    - slash_llm_reload: content=str — tui 据此 rebuild app.llm/light_llm/chat.llm
      (active profile 改了, 需热重载当前 session)。
    - slash_error: 用法 / name 不存在。
    """
    from explain_engine.chat.session import ChatEvent
    from explain_engine.llm.llm_config import LLMConfigStore

    sub = args[0].strip().lower() if args else ""

    if sub == "show":
        cfg = LLMConfigStore().load()
        if not cfg.profiles:
            return [ChatEvent(
                type="slash_llm",
                content=(
                    "还没配置任何 LLM profile — 当前走 .env / 环境变量。\n"
                    "输入 /llm 打开管理弹窗新增 provider。"
                ),
            )]
        lines = ["[bold]LLM profiles[/bold] (active 标 ★):"]
        for name, p in cfg.profiles.items():
            mark = " [green]★active[/green]" if name == cfg.active else ""
            lines.append(
                f"  • [cyan]{name}[/cyan]{mark}\n"
                f"      {p.protocol} | {p.base_url} | {p.model} | "
                f"key={_mask_key(p.api_key)}"
            )
        return [ChatEvent(type="slash_llm", content="\n".join(lines))]

    if sub == "use":
        if len(args) < 2 or not args[1].strip():
            return [ChatEvent(
                type="slash_error",
                content="用法: /llm use <profile 名> (先 /llm show 看可选名)",
            )]
        name = args[1].strip()
        store = LLMConfigStore()
        try:
            store.set_active(name)
        except KeyError:
            return [ChatEvent(
                type="slash_error",
                content=f"profile {name!r} 不存在 — /llm show 看可选名。",
            )]
        return [ChatEvent(
            type="slash_llm_reload",
            content=f"已切换 active LLM profile → [cyan]{name}[/cyan], 已热重载。",
        )]

    # 默认 (无参 / config / 未知子命令) → 弹管理 modal
    return [ChatEvent(
        type="slash_open_llm_config",
        content="管理 LLM provider (↑↓ 选, Enter 激活, a 新增, e 编辑, d 删除, Esc 关)",
        metadata=None,
    )]


# ─────────────────────────────────────────────────────────────────────────
# Phase X2: /review — 随时回看现象/洞察, 按编号编辑/删除/新增 (事后修订)。
# 兑现"默认全采纳 + 事后可修订": bootstrap/compress 不再阻塞主流程, 用户用
# /review 修订。复用 hitl.review_graph_async (编辑后 source 升级 user)。
# 不需 LLM, 不改 stage; 任意 stage 可调; ephemeral reject (无真 graph)。
# ─────────────────────────────────────────────────────────────────────────


async def _handle_review(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """Phase X2: /review — 回看 + 修订现象与洞察。

    - ephemeral → reject (没真 session 的 graph 可修订)。
    - input_provider 非 None (`explain chat <sid>` prompt_toolkit 路径) →
      交互 loop: 编号编辑 / d<编号> 删除 / a 新增现象 / q 退出; 有改动则 persist。
    - input_provider None (TUI / test) → 只读返回列表 + 提示。
    """
    del args
    from explain_engine.chat.chat_copy import (
        REVIEW_READONLY_HINT,
        msg_review_done,
    )
    from explain_engine.chat.session import ChatEvent
    from explain_engine.hitl.cli_interactive import (
        format_review_listing,
        review_graph_async,
    )

    if getattr(chat, "is_ephemeral", False):
        return _ephemeral_reject("review")

    # 无输入通道: 只读列出 (TUI 无 sub-prompt 通道, 跟 /predict 同限制)。
    if chat.input_provider is None:
        listing = format_review_listing(chat.state)
        return [ChatEvent(
            type="slash_review",
            content=f"{listing}\n{REVIEW_READONLY_HINT}",
        )]

    # 交互修订 (console 走 Rich, 跟 /budget 同 pattern — prompt_toolkit 路径可见)。
    changed = await review_graph_async(chat.state, chat.input_provider)
    if changed:
        chat.persist()
    return [ChatEvent(type="slash_review", content=msg_review_done(changed))]


# Registry — 25 default slash commands + 1 alias (/cf → counterfactual).
# 顺序决定 /help 列出顺序, 按"管理 → inspection → 操作 → engines → cross-session"分组.
#
# Phase 16.2 Wave 3: 所有 handler 经 _wrap_handler 包装 — 自动 snapshot graph
# 前后 diff 算 delta + 写 repl_history.jsonl entry (ephemeral / sid=None 时
# noop, 仅调原 handler). handler 本身完全不知道 wrapper 存在, 零侵入.
DEFAULT_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("quit",           COMMAND_DESCRIPTIONS["quit"],           _wrap_handler("quit", _handle_quit)),
    SlashCommand("help",           COMMAND_DESCRIPTIONS["help"],           _wrap_handler("help", _handle_help)),
    SlashCommand("show",           COMMAND_DESCRIPTIONS["show"],           _wrap_handler("show", _handle_show)),
    SlashCommand("graph",          COMMAND_DESCRIPTIONS["graph"],          _wrap_handler("graph", _handle_graph)),
    SlashCommand("budget",         COMMAND_DESCRIPTIONS["budget"],         _wrap_handler("budget", _handle_budget)),
    SlashCommand("compact",        COMMAND_DESCRIPTIONS["compact"],        _wrap_handler("compact", _handle_compact)),
    SlashCommand("save",           COMMAND_DESCRIPTIONS["save"],           _wrap_handler("save", _handle_save)),
    SlashCommand("new",            COMMAND_DESCRIPTIONS["new"],            _wrap_handler("new", _handle_new)),
    SlashCommand("resume",         COMMAND_DESCRIPTIONS["resume"],         _wrap_handler("resume", _handle_resume)),
    # Phase 11 Wave 3: 6 single-session engines slash + /cf alias.
    SlashCommand("compress",       COMMAND_DESCRIPTIONS["compress"],       _wrap_handler("compress", _handle_compress)),
    SlashCommand("run",            COMMAND_DESCRIPTIONS["run"],            _wrap_handler("run", _handle_run)),
    SlashCommand("check",          COMMAND_DESCRIPTIONS["check"],          _wrap_handler("check", _handle_check)),
    SlashCommand("predict",        COMMAND_DESCRIPTIONS["predict"],        _wrap_handler("predict", _handle_predict)),
    SlashCommand("counterfactual", COMMAND_DESCRIPTIONS["counterfactual"], _wrap_handler("counterfactual", _handle_counterfactual)),
    SlashCommand("cf",             COMMAND_DESCRIPTIONS["cf"],             _wrap_handler("cf", _handle_counterfactual)),
    SlashCommand("rescore",        COMMAND_DESCRIPTIONS["rescore"],        _wrap_handler("rescore", _handle_rescore)),
    # Phase 11 Wave 4: 3 cross-session slash (不依赖 single session, ephemeral 也 work).
    SlashCommand("list",           COMMAND_DESCRIPTIONS["list"],           _wrap_handler("list", _handle_list)),
    # Phase 17.2 Feature C: /delete <sid> --force (拒绝当前 session, 否则删).
    SlashCommand("delete",         COMMAND_DESCRIPTIONS["delete"],         _wrap_handler("delete", _handle_delete)),
    SlashCommand("lexicon",        COMMAND_DESCRIPTIONS["lexicon"],        _wrap_handler("lexicon", _handle_lexicon)),
    # Phase 16 Task 13: /theories cross-session inspect (跨 session theory list).
    SlashCommand("theories",       COMMAND_DESCRIPTIONS["theories"],       _wrap_handler("theories", _handle_theories)),
    # Phase 16 Task 14: /theory <id> [reject] — 看详情 / 拒绝某 theory.
    SlashCommand("theory",         COMMAND_DESCRIPTIONS["theory"],         _wrap_handler("theory", _handle_theory)),
    # Phase 16.2 Wave 5: /history — 查看本 session 操作历史 (默认 30 / 上限 200).
    SlashCommand("history",        COMMAND_DESCRIPTIONS["history"],        _wrap_handler("history", _handle_history)),
    SlashCommand("migrate",        COMMAND_DESCRIPTIONS["migrate"],        _wrap_handler("migrate", _handle_migrate)),
    # Phase 18 Task 9: /deepen — ephemeral 显式触发 promote_to_persistent.
    SlashCommand("deepen",         COMMAND_DESCRIPTIONS["deepen"],         _wrap_handler("deepen", _handle_deepen)),
    # Phase X2: /review — 回看 + 修订现象/洞察 (默认全采纳后的事后修订入口).
    SlashCommand("review",         COMMAND_DESCRIPTIONS["review"],         _wrap_handler("review", _handle_review)),
    # Phase 19 Task 21: /thinking on|off — 切 tui_app thinking Collapsible 折叠.
    SlashCommand("thinking",       COMMAND_DESCRIPTIONS["thinking"],       _wrap_handler("thinking", _handle_thinking)),
    SlashCommand("llm",            COMMAND_DESCRIPTIONS["llm"],            _wrap_handler("llm", _handle_llm)),
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
