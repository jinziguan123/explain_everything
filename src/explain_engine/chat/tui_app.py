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

import asyncio
from typing import TYPE_CHECKING, ClassVar

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.content import Content, Span
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.widgets import (
    Collapsible,
    Footer,
    Header,
    Input,
    LoadingIndicator,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent, ChatSession
    from explain_engine.llm.client import LLMClient


# ─── Wave 7 follow-up Bug 1: textual ModalScreen 交互式 /resume picker ───


class SessionPickerScreen(ModalScreen[str | None]):
    """`/resume` 无 args 时弹的 textual modal picker.

    Wave 7 (9838507): /resume 无 args 简化为 slash_error 用法提示 (避死锁).
    Wave 7 follow-up (本 commit): user 反馈期望真 picker. textual ModalScreen
    + OptionList 是 native widget, 走 textual message pump, 跟之前 asyncio
    .to_thread(input) 完全不同路径 — 不抢 stdin, 不死锁.

    Result type: str | None
    - str: 用户选中的 sid (Enter / 鼠标点击 select 触发)
    - None: 用户取消 (Esc binding) — caller 应当不切 chat

    sessions payload (from `_handle_resume` slash_open_session_picker event):
        [{"sid": str, "question": str, "stage": str, "created_at": float}, ...]
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "取消"),
    ]

    def __init__(
        self,
        sessions: list[dict],
        current_sid: str | None = None,
    ) -> None:
        super().__init__()
        # sessions 是 dict list (kotlin-style payload), 不耦合 SessionMeta 类型
        self._sessions: list[dict] = sessions
        self._current_sid: str | None = current_sid

    def compose(self) -> ComposeResult:
        """渲染: 居中 Vertical 容器, 含 1 行 hint + OptionList 选项."""
        from textual.containers import Vertical

        # 每 entry 显: "{question[:40]}  ({sid} · {stage})" — 截 question 避超长
        # markup=False 防 user-provided question 包含 [INST] 等被 textual Content
        # markup parser 吞 (跟 Wave 4 thinking_content 同款防御).
        options: list[Option] = []
        for s in self._sessions:
            sid = s["sid"]
            q = s.get("question", "") or ""
            stage = s.get("stage", "") or ""
            q_truncated = q if len(q) <= 40 else q[:39] + "…"
            marker = "  [当前]" if sid == self._current_sid else ""
            label = f"{q_truncated}  ({sid} · {stage}){marker}"
            options.append(Option(label, id=sid))

        yield Vertical(
            Static(
                "选择 session (↑↓ 移动, Enter 确认, Esc 取消)",
                classes="picker-title",
            ),
            OptionList(*options, id="session-picker-list", markup=False),
            id="session-picker-container",
        )

    def on_mount(self) -> None:
        """focus OptionList 让 ↑↓ Enter 立即可用 (默 focus 是 first focusable)."""
        self.query_one("#session-picker-list", OptionList).focus()

    @on(OptionList.OptionSelected, "#session-picker-list")
    def _on_select(self, event: OptionList.OptionSelected) -> None:
        """Enter / click → dismiss with sid. event.option.id 是我们 compose 时
        塞的 sid (str).

        约束: option.id 一定是 str (compose 时塞 sid str), 但 textual API
        signature 允许 None — 保险断言 + dismiss(None) 失败 fallback.
        """
        sid = event.option.id
        # 防御: id 不应是 None (compose 塞了 sid), 但 textual API 允许 None
        if not isinstance(sid, str):
            self.dismiss(None)
            return
        self.dismiss(sid)

    def action_cancel(self) -> None:
        """Esc → dismiss with None (caller 不切 chat)."""
        self.dismiss(None)


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
        show_splash: bool = True,
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
        # Wave 6 Task 31: 是否启动时显 splash. cli --no-splash 时 False (走干净
        # 路径 — repl_entry 自己 init lexicon backend).
        self.show_splash: bool = show_splash

    def compose(self) -> ComposeResult:
        # Wave 7 Bug 3 fix: slash 自动补全 — SuggestFromList(slash names) 注入 Input.
        # 用户输 "/h" 时, textual 灰字提示 "/help" / "/history" 等, 按 → 接受.
        # 用 DEFAULT_COMMANDS.name 拼 "/{name}" — 跟 dispatch_slash 解析一致.
        from explain_engine.chat.slash_commands import DEFAULT_COMMANDS

        slash_names = [f"/{cmd.name}" for cmd in DEFAULT_COMMANDS]
        suggester = SuggestFromList(slash_names, case_sensitive=False)

        yield Header(show_clock=False)
        yield VerticalScroll(id="output")
        yield Input(
            id="prompt",
            placeholder="问点什么... (/help, Ctrl+O 折叠 thinking, Ctrl+C 退出)",
            suggester=suggester,
        )
        yield Footer()

    # ─── Wave 6 Task 31 + Wave 7 production smoke fix: on_mount push SplashScreen ───
    async def on_mount(self) -> None:
        """启动时 push SplashScreen (若 show_splash), 等 4 step + 2.5s pause 后 pop,
        写 banner 到 #output, 最终 focus Input#prompt 让用户立刻能输.

        Wave 7 fix bundle:
        - Bug 1: splash pop 后写 banner (之前没写, #output 空白; 抽 _write_banner
          helper 跟 _reset_to_ephemeral 共用). sleep 1s → 2.5s 让 user 真看见
          4 ✓ 点亮 (1s 太快, 点亮过程不可见).
        - Bug 4: splash pop / no-splash 路径都 focus Input#prompt (之前默
          focused 是 VerticalScroll, 用户按 Enter 无反应).

        show_splash=False (cli --no-splash) 跳过 splash 直接 banner + focus.

        流程 (show_splash=True):
        1. push SplashScreen — 立刻覆盖主 layer.
        2. await splash._init_task — 等 4 step 串行跑完 (任一 step 失败标
           ✗ 不阻塞下一 step, 故 task 总能完成).
        3. asyncio.sleep(2.5) — 让 user 看完 4 step 点亮 + 最终态.
        4. pop_screen — 回 default screen (主 chat layer 含 #prompt).
        5. write banner + focus Input.

        Test 决策 (Task 31): asyncio.sleep 在测试 patch 掉避免真等 2.5s.
        """
        if not self.show_splash:
            # Bug 1: 无 splash 路径也要 banner — 不然 #output 空白.
            await self._write_banner()
            # Bug 4: 无 splash 路径也要 focus, 不然默 focused 是 VerticalScroll.
            self.query_one("#prompt", Input).focus()
            return

        from explain_engine.chat.splash_screen import SplashScreen

        splash = SplashScreen()
        await self.push_screen(splash)
        # _init_task 在 splash.on_mount 已 create_task 启 — 等它完成
        if splash._init_task is not None:
            try:
                await splash._init_task
            except Exception:
                # 防御: _init_task 自身 catch 了 step 异常, 这里再吞外层意外异常
                # (e.g. screen detach race). splash 仍 pop.
                pass
        # Wave 7 Bug 1: 1s → 2.5s, 让 user 真看见 4 ✓ 点亮 (1s 太快, 直接闪过).
        await asyncio.sleep(2.5)
        self.pop_screen()
        # Bug 1 fix: splash pop 后写 banner (之前没写, #output 空白).
        await self._write_banner()
        # Bug 4 fix: splash pop 后 focus Input — 用户立刻能输, 不需 Tab.
        self.query_one("#prompt", Input).focus()

    # ─── Mount helper (Wave 4) ───
    async def _write(self, text: str) -> None:
        """统一文本 mount helper — 写一行 Static 到 #output 容器.

        替代 RichLog.write — 现在所有 user-visible 文本都用 Static widget
        mount, 跟 Collapsible/LoadingIndicator 等结构性 widget 一致.
        Static 支持 textual markup ([red]/[dim]/[bold] 等).

        ⚠️ Wave 4 review C-1: 该 helper 假定 `text` 是 **trusted markup
        string** (例如 chat_copy 模块 zh msg + 我们写的 inline markup).
        若需要插入 user/LLM 输入, 用 _write_styled(prefix_markup, *parts).
        """
        container = self.query_one("#output", VerticalScroll)
        await container.mount(Static(text))

    async def _write_styled(
        self,
        prefix_markup: str,
        *plain_parts: str,
        suffix_style: str = "",
    ) -> None:
        """Wave 4 review C-1: 安全 mount markup-prefix + plain user/LLM text.

        prefix_markup: trusted markup (例 "[bold cyan]>[/bold cyan] " /
                       "[red]chat 失败: [/red]")
        plain_parts:   untrusted (user input / LLM 异常 str / sid 等),
                       直接 append 为 plain text (markup parser 不解析,
                       `[INST]` / `x[0]` / `[Smith, 2020]` 等不被吃).
        suffix_style:  optional textual Style name 应用到 plain_parts
                       (例 "dim" / "red") — 取代 inline [dim]...[/dim]
                       前后包裹模式 (那种含 user content 也会被 markup 解析).

        textual Content markup parser 跟 rich.markup 不一致 — rich.escape()
        仅转 "valid tag 模样" 的 `[red]` 等, 但 `[INST]` rich 不转,
        textual.Content 当 unknown style 静默吃. 用 from_markup(prefix) +
        append(plain) 是唯一稳路径.
        """
        container = self.query_one("#output", VerticalScroll)
        content = Content.from_markup(prefix_markup)
        for part in plain_parts:
            if suffix_style:
                plain_content = Content(
                    part, spans=[Span(0, len(part), suffix_style)]
                )
            else:
                plain_content = Content(part)
            content = content.append(plain_content)
        await container.mount(Static(content))

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
        # Wave 4 review C-1: 走 _write_styled 防 markup 注入
        # (textual Content markup parser 把 [INST] / [FOO] 当未知 style
        # 吃掉, rich.escape() 对它不转, 故用 prefix + plain append).
        await self._write_styled("[bold cyan]>[/bold cyan] ", text)

        if text.startswith("/"):
            from explain_engine.chat.slash_commands import dispatch_slash

            try:
                events = await dispatch_slash(self.chat, text)
            except Exception as exc:
                # Wave 4 review C-1: 异常 str 当 plain text append + dim 红.
                await self._write_styled(
                    "[red]slash 失败: [/red]",
                    f"{type(exc).__name__}: {exc}",
                    suffix_style="red",
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
            # Wave 4 review C-1: 异常 str 当 plain text append.
            await self._write_styled(
                "[red]chat 失败: [/red]",
                f"{type(exc).__name__}: {exc}",
                suffix_style="red",
            )

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
        - slash_thinking_toggle (Task 21): metadata.visible (bool) → 强制 set
          _thinking_visible + 同步现 mount Collapsible.collapsed. echo zh msg.
        Wave 5/6:
        - status_start/end: spinner mount/unmount (现暂 no-op)
        - 其他: fallback dim mount
        """
        ev_type = ev.type
        content = ev.content

        if ev_type == "slash_quit":
            if content:
                # Wave 4 review C-1: slash content 当 plain text + dim style
                # (避免 inline [dim]...[/dim] 包裹 markup-looking content
                # 被 textual Content parser 吃).
                await self._write_styled(
                    "", str(content), suffix_style="dim"
                )
            self.exit()
            return

        if ev_type == "slash_error" or ev_type == "slash_unknown":
            await self._write_styled(
                "", str(content), suffix_style="red"
            )
            return

        if ev_type == "slash_next_step_hint":
            await self._write_styled(
                "", str(content), suffix_style="dim"
            )
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

        # Wave 7 follow-up Bug 1: /resume 无 args → push SessionPickerScreen modal.
        # event metadata 含 sessions list + current_sid (handler 包好的, REPL
        # 不用再 fetch).
        if ev_type == "slash_open_session_picker":
            meta = ev.metadata or {}
            sessions = meta.get("sessions")
            if not isinstance(sessions, list) or not sessions:
                # 协议防御: handler 应不送空 list (空 → slash_error 路径),
                # 但 future-proof.
                await self._write(
                    "[red]slash_open_session_picker event 缺 metadata.sessions; "
                    "无 picker 可弹.[/red]"
                )
                return
            current_sid = meta.get("current_sid")
            if isinstance(content, str) and content:
                # echo hint 到主 layer (modal 退出后仍可见, 提示 user 刚做了什么)
                await self._write_styled("", content, suffix_style="dim")
            picker = SessionPickerScreen(
                sessions=sessions,
                current_sid=current_sid if isinstance(current_sid, str) else None,
            )
            # 非阻塞 push: callback 在 dismiss 时由 textual 调
            self.push_screen(picker, self._on_session_picked)
            return

        # Wave 4 Task 19: thinking_text → Collapsible mount (默 expand).
        if ev_type == "thinking_text":
            await self._mount_thinking(content if isinstance(content, str) else "")
            return

        # Wave 4 Task 21: /thinking on|off → slash_thinking_toggle event.
        # 强制 set _thinking_visible 跟 metadata.visible 一致, 同步现 mount
        # Collapsible.collapsed (跟 action_toggle_thinking 等价路径, 但
        # set 而非 toggle — 保证 on/off 幂等). 再 echo 中文 msg.
        if ev_type == "slash_thinking_toggle":
            meta = ev.metadata or {}
            if "visible" not in meta or not isinstance(meta["visible"], bool):
                await self._write(
                    "[red]slash_thinking_toggle event 缺 metadata.visible (bool); "
                    "保留当前 thinking 状态.[/red]"
                )
                return
            self._thinking_visible = meta["visible"]
            self._sync_thinking_collapsibles()
            if isinstance(content, str) and content:
                # Wave 4 review C-1: content (chat_copy zh msg) 当 plain
                # text + dim style. trusted 但 future-proof.
                await self._write_styled(
                    "", content, suffix_style="dim"
                )
            return

        # Wave 5 Task 24: status_start/end → mount/unmount LoadingIndicator.
        if ev_type == "status_start":
            await self._mount_status(content if isinstance(content, str) else "")
            return
        if ev_type == "status_end":
            await self._unmount_status()
            return

        # Wave 6+ 用 — 现阶段 no-op (turn_complete / tool_use / tool_result).
        if ev_type in (
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
        - 内嵌 Static 用 dim 颜色 (走 .thinking-content CSS class), 跟主
          assistant_text 区分.

        Wave 4 review C-1: LLM reasoning 内容用 `markup=False` 完全 bypass
        textual Content markup parser — rich.markup.escape() 仅转 "看起来像
        valid tag" 的 `[red]` 等, 但对 `[INST]` / `[FOO]` 这类 LLM 几乎必
        碰的 unknown bracket-tag 不会转 (rich.escape 留它原样, 但 textual
        Content parser 会把它当 unknown style 静默吃). markup=False 是唯一
        稳路径. dim 样式走 CSS class 而非 inline [dim].
        """
        char_count = len(content)
        container = self.query_one("#output", VerticalScroll)
        col = Collapsible(
            Static(content, markup=False, classes="thinking-content"),
            title=f"thinking ({char_count} 字)",
            collapsed=not self._thinking_visible,
        )
        await container.mount(col)

    # ─── Wave 5 Task 24: status spinner mount/unmount helper ───
    async def _mount_status(self, label: str) -> None:
        """status_start event → mount LoadingIndicator + Static label.

        固定 id "status-indicator" / "status-label" — 一时刻 1 个 active
        status pair (design §5 决策, Phase 19 不支持 nested status).
        若已有现存 status pair (e.g. 连续 status_start 无 status_end —
        防御场景), 先 unmount 再 mount, 防 widget leak.

        Wave 5 review C-1 preempt: label 走 markup=False — LLM-provided
        status text (e.g. `"处理 [INST] 段..."`) 不被 textual Content markup
        parser 吃, 跟 Wave 4 thinking_text 同款防御.

        **约束**: caller 必须串行 await — 现 ephemeral / ChatSession /
        _handle_deepen 都是顺序 yield 不会并发 mount. Phase 20+ 如加
        concurrent status (e.g. nested pipeline) 需重新设计 (用 unique id
        per pair, 或换 queue 协议).
        """
        # 防 leak: 先清前一对 (若有)
        await self._unmount_status()
        container = self.query_one("#output", VerticalScroll)
        indicator = LoadingIndicator(id="status-indicator")
        label_static = Static(
            label,
            markup=False,
            id="status-label",
            classes="status-label",
        )
        await container.mount(indicator)
        await container.mount(label_static)

    async def _unmount_status(self) -> None:
        """status_end event → 移走前一对 LoadingIndicator + label.

        Idempotent — 无 active pair 时静默 no-op (handle_user_input
        异常 path 可能 yield status_end 跳过 status_start, 或重复 yield).
        用 query("#xxx") 找所有同 id widget (理论 1 个), 全部 remove.

        调用时若无 active status pair (e.g. 重复 status_end / 从未
        status_start), query 返空 list, for-loop 零 iter, return without
        error.
        """
        for wid in list(self.query("#status-indicator")):
            await wid.remove()
        for wid in list(self.query("#status-label")):
            await wid.remove()

    # ─── Wave 7 follow-up Bug 1: SessionPickerScreen dismiss callback ───
    def _on_session_picked(self, sid: str | None) -> None:
        """SessionPickerScreen.dismiss callback — 异步 wrap _switch_to_chat_session.

        textual push_screen(screen, callback) 的 callback 是 sync fn — 但
        _switch_to_chat_session 是 async (要 mount widget). 用 self.run_worker
        启 textual-managed async worker, callback 自身瞬返不阻 message pump.

        sid is None → 用户 Esc 取消, mount 一行 dim "已取消".
        sid 等当前 chat.sid → 不切, mount info "已在该 session".
        sid 不等 → 走 _switch_to_chat_session 等价路径 (跟 slash_switch_session
        event handler 一致, 同样从 metadata.sid 切).

        用 run_worker 而非 asyncio.create_task 因为:
        1. RUF006 — 裸 create_task 易被 GC, textual worker 由 App 持有
        2. textual 自带 exception 处理 + 生命周期管理.
        """
        if sid is None:
            # 取消 — 启 worker mount "已取消" 行 (worker async fn)
            async def _cancel_msg() -> None:
                await self._write_styled(
                    "", "已取消 session 切换.", suffix_style="dim"
                )
            self.run_worker(_cancel_msg(), name="picker_cancel_msg")
            return

        # 选了 sid — 启 worker 切 chat
        async def _switch() -> None:
            # 跟当前 chat 同 sid → echo info, 不重建 ChatSession
            from explain_engine.chat.session import ChatSession
            if isinstance(self.chat, ChatSession) and self.chat.sid == sid:
                await self._write_styled(
                    "",
                    f"已在 session {sid}, 无需切换.",
                    suffix_style="dim",
                )
                return
            await self._switch_to_chat_session({"sid": sid})
        self.run_worker(_switch(), name="picker_switch_session")

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
            # Wave 4 review C-1: exception str 当 plain text append + red.
            await self._write_styled(
                "[red]切换至新 session 失败: [/red]",
                f"{type(exc).__name__}: {exc}",
                suffix_style="red",
            )
            return
        self.chat = new_chat
        # sid 是 chat_copy / persistence 返的 trusted id (e.g. "s_xxx"),
        # 但保险用 plain text append, 后缀文本走 markup.
        container = self.query_one("#output", VerticalScroll)
        line = (
            Content.from_markup("[green]已进入持久 session [/green]")
            .append(Content(str(sid), spans=[Span(0, len(str(sid)), "green")]))
            .append(Content.from_markup(
                "[green], 可继续 /compress /run 等.[/green]"
            ))
        )
        await container.mount(Static(line))

    async def _reset_to_ephemeral(self) -> None:
        """Phase 18 /new — 清屏 + (若 ChatSession) aclose + 重建 ephemeral."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.chat.session import ChatSession
        from explain_engine.persistence.storage_v2 import StorageV2

        if isinstance(self.chat, ChatSession):
            try:
                await self.chat.aclose()
            except Exception as exc:
                # Wave 4 review C-1: exception str 当 plain text append.
                await self._write_styled(
                    "[yellow]aclose 当前 session 失败 (继续 reset): [/yellow]",
                    str(exc),
                    suffix_style="yellow",
                )
        container = self.query_one("#output", VerticalScroll)
        await container.remove_children()
        self.chat = EphemeralChatSession(
            storage=StorageV2(),
            llm=self.llm,
        )
        await self._write_banner()

    async def _write_banner(self) -> None:
        """Wave 7 Bug 1 fix: 抽 banner write 出来, on_mount + _reset_to_ephemeral 共用.

        production smoke 实证 splash pop 后 #output 容器空白 — 用户看不到任何
        banner 引导. 原因: on_mount 只 push splash + pop, 从没写过 banner.
        修: on_mount 末尾 + _reset_to_ephemeral 都调本 helper, 用户首次看到主
        chat layer 时 banner 立即可见.
        """
        await self._write(
            "[bold green]Explain REPL[/bold green] — ephemeral chat. "
            "输入问题让 LLM 直接答, /deepen 触发深度建模, "
            "/help 看 slash, /quit 退出."
        )

    # ─── Wave 4 review I-1 (DRY): 抽 _sync_thinking_collapsibles helper ───
    def _sync_thinking_collapsibles(self) -> None:
        """同步所有现 mount Collapsible.collapsed = not self._thinking_visible.

        Wave 4 review I-1: 被 action_toggle_thinking (Ctrl+O) +
        slash_thinking_toggle handler (_render_event 内) 共用. Phase 20 加更多
        Collapsible source (e.g. /lexicon 嵌套 collapse) 时只改一处.
        """
        for c in self.query(Collapsible):
            c.collapsed = not self._thinking_visible

    # ─── Actions ───
    def action_toggle_thinking(self) -> None:
        """Wave 4 Task 20: 切 _thinking_visible + 同步现 mount Collapsible.collapsed.

        Ctrl+O 触发. visible=True ↔ collapsed=False 反向关系.
        """
        self._thinking_visible = not self._thinking_visible
        self._sync_thinking_collapsibles()

    def action_quit_app(self) -> None:
        """优雅退出 — Ctrl+C 触发."""
        self.exit()

    def action_clear_log(self) -> None:
        """Ctrl+L 清屏 — 移走 #output 容器所有 children."""
        container = self.query_one("#output", VerticalScroll)
        container.remove_children()
