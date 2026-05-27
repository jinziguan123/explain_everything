"""Phase 19 Wave 3 Task 13: ExplainChatApp — textual TUI chat REPL 主壳.

替换 Phase 11/18 的 Rich Console + prompt_toolkit 组合. 设计:
docs/plans/2026-05-27-phase-19-tui-design.md §3.1.

Layout:
    Header
    RichLog#output (chat log)
    Input#prompt (用户输入)
    Footer (BINDINGS 帮助)

BINDINGS:
    Ctrl+O — toggle 所有 thinking Collapsible 折叠/展开 (Wave 4 实装).
    Ctrl+C — 退出 app (优雅退出, 现 chat 不持久 — ephemeral; ChatSession 走 aclose).
    Ctrl+L — 清屏 (RichLog.clear).

Task 13 仅 scaffolding. Task 14-16 加 _render_event + Input.Submitted handler.
Task 17 由 repl_entry.enter_repl_async 启 (await app.run_async()).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, RichLog

if TYPE_CHECKING:
    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent, ChatSession
    from explain_engine.llm.client import LLMClient


class ExplainChatApp(App):
    """textual TUI chat REPL App.

    chat var (self.chat) 启始是 EphemeralChatSession, /deepen 成功后
    替换为 ChatSession (slash_deepen_promoted event 触发 — Task 15 落地).
    /new (slash_reset_to_ephemeral) 重建 EphemeralChatSession.

    self.llm / self.light_llm 由 caller 注入 (repl_entry 用 make_llm_client /
    make_light_llm_client).
    """

    BINDINGS = [
        Binding("ctrl+o", "toggle_thinking", "折叠 thinking"),
        Binding("ctrl+c", "quit_app", "退出"),
        Binding("ctrl+l", "clear_log", "清屏"),
    ]
    CSS_PATH = "tui_app.tcss"

    def __init__(
        self,
        llm: "LLMClient | None",
        light_llm: "LLMClient | None",
        ephemeral_chat: "EphemeralChatSession",
    ) -> None:
        super().__init__()
        self.llm = llm
        self.light_llm = light_llm
        # chat 类型可能在 runtime 切换 (ephemeral ↔ ChatSession). Task 15 用.
        self.chat: "EphemeralChatSession | ChatSession" = ephemeral_chat
        # Task 13 scaffolding: thinking visibility 状态, Wave 4 Ctrl+O 切.
        self._thinking_visible: bool = True

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="output", wrap=True, highlight=False, markup=True)
        yield Input(
            id="prompt",
            placeholder="问点什么... (/help, Ctrl+O 折叠 thinking, Ctrl+C 退出)",
        )
        yield Footer()

    # ─── Event render ───
    async def _render_event(self, ev: "ChatEvent") -> None:
        """单 ChatEvent → textual widget 操作 dispatch.

        Wave 3 主分支:
        - assistant_text / slash_help / slash_show / slash_save / slash_compact /
          slash_resume / slash_lexicon / slash_theories 等 user-visible text: log.write
        - slash_error / slash_unknown: 红色 markup log.write
        - slash_next_step_hint: dim markup log.write
        - slash_quit: self.exit() 触发优雅退出
        - slash_deepen_promoted: Task 15 接 — 用 metadata.sid 建 ChatSession 切 self.chat
        - slash_reset_to_ephemeral: Task 15 接 — 重建 EphemeralChatSession
        - thinking_text / status_start / status_end: Wave 4/5 实装 (现 fallback 不 render)
        - 其他: dim fallback "[ev.type]: ev.content" (Phase 19 接受 — 后续 wave 加分支)
        """
        log = self.query_one("#output", RichLog)
        ev_type = ev.type
        content = ev.content

        if ev_type == "slash_quit":
            if content:
                log.write(f"[dim]{content}[/dim]")
            self.exit()
            return

        if ev_type == "slash_error" or ev_type == "slash_unknown":
            log.write(f"[red]{content}[/red]")
            return

        if ev_type == "slash_next_step_hint":
            log.write(f"[dim]{content}[/dim]")
            return

        # Task 15 加 slash_deepen_promoted / slash_reset_to_ephemeral 提前 return.
        # Wave 4/5 加 thinking_text / status_start / status_end. 现阶段视为 no-op,
        # 避免给用户 dim fallback dump (跟 Wave 4/5 真渲染冲突).
        if ev_type in (
            "thinking_text",
            "status_start",
            "status_end",
            "turn_complete",
            "tool_use",
            "tool_result",
            "slash_switch_session",
        ):
            # Wave 4/5 实装. Task 14 暂 no-op.
            return

        # user-visible text events: 直接 write content
        if isinstance(content, str):
            log.write(content)
            return

        # fallback: dim format
        log.write(f"[dim]{ev_type}: {content}[/dim]")

    # ─── Actions ───
    def action_toggle_thinking(self) -> None:
        """Wave 4 Task 20 实装真切. Task 13 scaffolding 仅记 state."""
        self._thinking_visible = not self._thinking_visible

    def action_quit_app(self) -> None:
        """优雅退出 — Ctrl+C 触发. 现 ephemeral 无 aclose, ChatSession 切换 wave 加."""
        self.exit()

    def action_clear_log(self) -> None:
        """Ctrl+L 清屏 RichLog#output."""
        self.query_one("#output", RichLog).clear()
