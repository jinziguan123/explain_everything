"""Phase 19 Wave 3-4: ExplainChatApp — textual TUI chat REPL 主壳.

替换 Phase 11/18 的 Rich Console + prompt_toolkit 组合. 设计:
docs/plans/2026-05-27-phase-19-tui-design.md §3.1 / §3.4.

Layout (Wave 4 激进路线: RichLog → VerticalScroll):
    Header
    VerticalScroll#output  (chat log 容器, 支持 mount 任意 widget)
      └─ Static / Collapsible / LoadingIndicator (按事件 mount 进去)
    Input#prompt
    Footer

BINDINGS:
    Ctrl+O — toggle 所有现 mount thinking Collapsible 折叠/展开 (Wave 4 实装).
    Ctrl+C — 退出 app.
    Ctrl+L — 清屏 (VerticalScroll.remove_children).

Phase 19 真线性流哲学 (Wave 4 §3.4): 所有输出 (assistant text / thinking
Collapsible / spinner / splash) 共一个垂直容器 mount, 不再用 RichLog.write 文本流.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Footer, Header, Input, Static

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

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+o", "toggle_thinking", "折叠 thinking"),
        Binding("ctrl+c", "quit_app", "退出"),
        Binding("ctrl+l", "clear_log", "清屏"),
    ]
    CSS_PATH = "tui_app.tcss"

    def __init__(
        self,
        llm: LLMClient | None,
        light_llm: LLMClient | None,
        ephemeral_chat: EphemeralChatSession,
    ) -> None:
        super().__init__()
        self.llm = llm
        self.light_llm = light_llm
        # chat 类型可能在 runtime 切换 (ephemeral ↔ ChatSession). Task 15 用.
        self.chat: EphemeralChatSession | ChatSession = ephemeral_chat
        # Wave 4 Task 19/20: thinking visibility 状态. True 时新 mount
        # Collapsible 默 expand (collapsed=False); False 时默 collapse.
        # Ctrl+O 切 + slash /thinking on|off 也切 (Task 21).
        self._thinking_visible: bool = True

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield VerticalScroll(id="output")
        yield Input(
            id="prompt",
            placeholder="问点什么... (/help, Ctrl+O 折叠 thinking, Ctrl+C 退出)",
        )
        yield Footer()

    # ─── Mount helper (Wave 4) ───
    async def _write(self, text: str) -> None:
        """统一文本 mount helper — 写一行 Static 到 #output 容器.

        替代 RichLog.write — 现在所有 user-visible 文本都用 Static widget
        mount, 跟 Collapsible/LoadingIndicator 等结构性 widget 一致.
        Static 支持 Rich markup ([red]/[dim]/[bold] 等).
        """
        container = self.query_one("#output", VerticalScroll)
        await container.mount(Static(text))

    # ─── Input handler (Task 16) ───
    @on(Input.Submitted, "#prompt")
    async def _handle_input_submitted(self, event: Input.Submitted) -> None:
        """User 按 Enter — 派发到 slash dispatch 或 chat.handle_user_input.

        - 空 input (strip 后) → no-op (跟 prompt_toolkit REPL 同行为).
        - text 以 / 开头 → dispatch_slash (返 list[ChatEvent]); 注意 dispatch_slash
          内部已 catch chat handler 异常返 slash_error event, 但 dispatch_slash
          自身底层异常 (e.g. _command_by_name 抛) 用 try 兜底.
        - 否则 → chat.handle_user_input(text, self.llm) async generator → async for.
          self.llm 为 None 时 ephemeral.handle_user_input 会抛 — 兜在 try.
        """
        text = (event.value or "").strip()
        event.input.value = ""
        if not text:
            return

        # 回显用户 input — 文本前缀 [bold cyan]>[/bold cyan].
        await self._write(f"[bold cyan]>[/bold cyan] {text}")

        if text.startswith("/"):
            from explain_engine.chat.slash_commands import dispatch_slash

            try:
                events = await dispatch_slash(self.chat, text)
            except Exception as exc:
                await self._write(
                    f"[red]slash 失败: {type(exc).__name__}: {exc}[/red]"
                )
                return
            for ev in events:
                await self._render_event(ev)
            return

        # 自然语言 — async generator
        try:
            async for ev in self.chat.handle_user_input(text, self.llm):
                await self._render_event(ev)
        except Exception as exc:
            await self._write(f"[red]chat 失败: {type(exc).__name__}: {exc}[/red]")

    # ─── Event render ───
    async def _render_event(self, ev: ChatEvent) -> None:
        """单 ChatEvent → textual widget 操作 dispatch.

        Wave 3 主分支:
        - assistant_text / slash_help / slash_show / slash_save / slash_compact /
          slash_resume / slash_lexicon / slash_theories 等 user-visible text:
          mount Static
        - slash_error / slash_unknown: 红色 markup
        - slash_next_step_hint: dim markup
        - slash_quit: self.exit()
        - slash_deepen_promoted / slash_switch_session: 切 chat var
        - slash_reset_to_ephemeral: 重建 ephemeral
        Wave 4:
        - thinking_text: mount Collapsible(Static(content)) 同 _thinking_visible
          标 collapsed (默 expand). title 含字符数.
        Wave 5/6:
        - status_start/end: spinner mount/unmount (现暂 no-op)
        - 其他: fallback dim mount
        """
        ev_type = ev.type
        content = ev.content

        if ev_type == "slash_quit":
            if content:
                await self._write(f"[dim]{content}[/dim]")
            self.exit()
            return

        if ev_type == "slash_error" or ev_type == "slash_unknown":
            await self._write(f"[red]{content}[/red]")
            return

        if ev_type == "slash_next_step_hint":
            await self._write(f"[dim]{content}[/dim]")
            return

        # Task 15: /deepen 成功 promote → 切到新 ChatSession.
        if ev_type == "slash_deepen_promoted":
            if content:
                await self._write(content)
            await self._switch_to_chat_session(ev.metadata or {})
            return

        # Task 15: /new → 重建 ephemeral.
        if ev_type == "slash_reset_to_ephemeral":
            await self._reset_to_ephemeral()
            return

        # Phase 19 Wave 3 review I-4: /resume → slash_switch_session.
        if ev_type == "slash_switch_session":
            if not isinstance(content, dict) or "sid" not in content:
                await self._write(
                    "[red]slash_switch_session event 缺 content.sid; 保留当前 chat.[/red]"
                )
                return
            await self._switch_to_chat_session(content)
            return

        # Wave 4 Task 19: thinking_text → Collapsible mount (默 expand).
        if ev_type == "thinking_text":
            await self._mount_thinking(content if isinstance(content, str) else "")
            return

        # Wave 5/6 加 status_start / status_end. 现阶段视为 no-op.
        if ev_type in (
            "status_start",
            "status_end",
            "turn_complete",
            "tool_use",
            "tool_result",
        ):
            return

        # user-visible text events: mount Static
        if isinstance(content, str):
            await self._write(content)
            return

        # fallback: dim format
        await self._write(f"[dim]{ev_type}: {content}[/dim]")

    # ─── Wave 4: thinking Collapsible mount helper ───
    async def _mount_thinking(self, content: str) -> None:
        """Task 19: thinking_text → mount Collapsible(Static(content)).

        - title: "thinking ({N} 字)" — N 是 content 字符数 (D2 决策).
        - collapsed: not _thinking_visible — visible=True 默 expand (D3).
        - 内嵌 Static 用 dim markup, 视觉上跟主 assistant_text 区分.
        """
        char_count = len(content)
        container = self.query_one("#output", VerticalScroll)
        col = Collapsible(
            Static(f"[dim]{content}[/dim]"),
            title=f"thinking ({char_count} 字)",
            collapsed=not self._thinking_visible,
        )
        await container.mount(col)

    # ─── Chat var 切换 helper (Task 15) ───
    async def _switch_to_chat_session(self, metadata: dict) -> None:
        """Phase 18 /deepen 落地 + 切换 chat var to ChatSession."""
        sid = metadata.get("sid")
        if not sid:
            await self._write(
                "[red]slash_deepen_promoted event 缺 metadata.sid; 保留 ephemeral.[/red]"
            )
            return
        try:
            from explain_engine.chat.session import ChatSession
            new_chat = ChatSession(sid, llm=self.llm)
        except Exception as exc:
            await self._write(
                f"[red]切换至新 session 失败: {type(exc).__name__}: {exc}[/red]"
            )
            return
        self.chat = new_chat
        await self._write(
            f"[green]已进入持久 session {sid}, 可继续 /compress /run 等.[/green]"
        )

    async def _reset_to_ephemeral(self) -> None:
        """Phase 18 /new — 清屏 + (若 ChatSession) aclose + 重建 ephemeral."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.chat.session import ChatSession
        from explain_engine.persistence.storage_v2 import StorageV2

        if isinstance(self.chat, ChatSession):
            try:
                await self.chat.aclose()
            except Exception as exc:
                await self._write(
                    f"[yellow]aclose 当前 session 失败 (继续 reset): {exc}[/yellow]"
                )
        container = self.query_one("#output", VerticalScroll)
        await container.remove_children()
        self.chat = EphemeralChatSession(
            storage=StorageV2(),
            llm=self.llm,
        )
        await self._write(
            "[bold green]Explain REPL[/bold green] — ephemeral chat. "
            "输入问题让 LLM 直接答, /deepen 触发深度建模, "
            "/help 看 slash, /quit 退出."
        )

    # ─── Actions ───
    def action_toggle_thinking(self) -> None:
        """Wave 4 Task 20: 切 _thinking_visible + 同步现 mount Collapsible.collapsed.

        Ctrl+O 触发. visible=True ↔ collapsed=False 反向关系.
        """
        self._thinking_visible = not self._thinking_visible
        for c in self.query(Collapsible):
            c.collapsed = not self._thinking_visible

    def action_quit_app(self) -> None:
        """优雅退出 — Ctrl+C 触发."""
        self.exit()

    def action_clear_log(self) -> None:
        """Ctrl+L 清屏 — 移走 #output 容器所有 children."""
        container = self.query_one("#output", VerticalScroll)
        container.remove_children()
