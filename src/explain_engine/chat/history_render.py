"""Phase 16.2 Wave 7: REPL history banner 短渲染.

design: docs/plans/2026-05-25-repl-history-persistence-design.md §6.1
plan: docs/plans/2026-05-25-repl-history-persistence-plan.md Wave 7

Public:
  render_recent_history(entries: list[dict], max_n: int = 10) -> str
    Resume banner 用. 按 ts 升序排序取末 N (旧→新). 空 list 返友好提示.
    intervention 截 80 字, llm_turn user/assistant 各截 60 字 — banner 用,
    /history slash 命令完整显示走 slash_commands._render_history_entry (不截).

Helper (私有):
  _truncate, _format_short_ts, _render_history_entry_short
"""

from __future__ import annotations

from datetime import datetime

from explain_engine.chat.chat_copy import (
    BANNER_HISTORY_EMPTY,
    BANNER_HISTORY_FOOTER,
    BANNER_HISTORY_HEADER,
    HISTORY_INTERVENTION_PREFIX,
    HISTORY_TYPE_PREFIX_ASSISTANT,
    HISTORY_TYPE_PREFIX_USER,
)


def _truncate(text: str, max_chars: int) -> str:
    """超过 max_chars 截断 + '...'. 等于或小于直返."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _format_short_ts(iso_ts: str) -> str:
    """ISO ts ('2026-05-25T14:08:00') → '05-25 14:08' (11 字符).

    解析失败时回 fallback: 取前 12 字符 (e.g. '2026-05-25T1'), 完全空时回 '?'.
    """
    if not iso_ts:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_ts)
        return dt.strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso_ts[:12]


def _render_history_entry_short(entry: dict) -> str:
    """单 entry 短版 (banner 用, 字数截断).

    - slash: 主行 '[ts] /cmd<padded> summary', 有 intervention 时第二行缩进
      '假设: <截 80 字>'. summary 可能含失败标记 (e.g. '(执行失败: LLMError)'),
      原样透传.
    - llm_turn: 主行 '[ts] 你: <截 60>', 第二行缩进 'Claude: <截 60>'.
    - 未知 type: '[ts] (unknown type)'.
    """
    ts = _format_short_ts(entry.get("ts", ""))
    typ = entry.get("type")
    if typ == "slash":
        cmd = entry.get("cmd", "?")
        summary = entry.get("summary", "?")
        # cmd 左对齐 18 字符 (design §6.1: '/cmd 左对齐 20 字符 + summary')
        # 这里用 18 让 /cmd_name<padding> + summary 总宽更紧凑.
        head = f"  [{ts}] /{cmd:<18} {summary}"
        if "intervention" in entry:
            iv = _truncate(entry["intervention"], 80)
            head += f"\n                {HISTORY_INTERVENTION_PREFIX}{iv}"
        return head
    if typ == "llm_turn":
        ui = _truncate(entry.get("user_input", ""), 60)
        at = _truncate(entry.get("assistant_text", ""), 60)
        return (
            f"  [{ts}] {HISTORY_TYPE_PREFIX_USER}{ui}\n"
            f"                {HISTORY_TYPE_PREFIX_ASSISTANT}{at}"
        )
    return f"  [{ts}] (unknown type)"


def render_recent_history(entries: list[dict], max_n: int = 10) -> str:
    """Banner 用: 最近 N 条 (旧→新). 空返友好提示, 否则 header + body + footer.

    - 按 ts 升序 (旧→新) 排序; 缺 ts 视作 '' 排最前.
    - 取末 N (max_n 默认 10).
    - header 用实际显示数, 不是 max_n.

    返单个 str (含首尾空行 + 缩进), caller 直 print 即可.
    """
    if not entries:
        return f"\n  {BANNER_HISTORY_EMPTY}\n"
    sorted_entries = sorted(entries, key=lambda e: e.get("ts", ""))
    shown = sorted_entries[-max_n:]
    header = BANNER_HISTORY_HEADER.format(n=len(shown))
    body = "\n".join(_render_history_entry_short(e) for e in shown)
    return f"\n  {header}\n{body}\n\n  {BANNER_HISTORY_FOOTER}\n"
