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
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.content import Content, Span
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    Header,
    Input,
    LoadingIndicator,
    OptionList,
    Select,
    Static,
)
from textual.widgets.option_list import Option

from explain_engine.config import _read_splash_pause_env

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from typing import Any

    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.chat.session import ChatEvent, ChatSession
    from explain_engine.llm.client import LLMClient


# ─── Wave 7 follow-up Bug 3: phase 19 debug log (splash lifecycle 诊断用) ───


def _phase19_debug_log_path() -> Path:
    """debug log 路径 — $EXPLAIN_HOME/phase19_debug.log.

    EXPLAIN_HOME 未设时 fallback ~/.explain (跟其他 explain 文件一致).
    实时取 env (不 cache 模块加载时值) — 让测试能 monkeypatch.
    """
    home = os.environ.get("EXPLAIN_HOME") or os.path.expanduser("~/.explain")
    return Path(home) / "phase19_debug.log"


def _log_phase19(event: str, detail: str = "") -> None:
    """append 一行 debug log: [ISO ts] event detail.

    用 user 反馈 "splash 真终端不显" 诊断: SVG snapshot smoke 显示 splash 正常,
    但 macOS Terminal.app user 看不见. 写 log 让 user 跑后给我看真 path.

    Best-effort: IO 失败完全静默 (写 log 失败不影响 chat 启动).
    """
    try:
        log_path = _phase19_debug_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        elapsed_ms = int(time.monotonic() * 1000) % 1_000_000  # 短 monotonic 帮算相对时序
        line = f"[{ts} +{elapsed_ms:06d}ms] {event}"
        if detail:
            line += f" :: {detail}"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # 任何 IO 失败静默吞 — debug log 不影响主流程
        pass


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


# ─── Phase 20.4: LLM provider 管理 modal (列表 + 表单) ───


class LLMProfileFormScreen(ModalScreen["object | None"]):
    """新增 / 编辑单个 LLM profile 的表单 modal.

    Result: LLMProfile (保存) | None (取消). 适配 anthropic + openai 两协议
    (protocol Select). light_* 留空 = fallback 主配置。

    编辑模式 (传 profile) 时 name 锁定 (name 是 store key, 改名 = 删旧建新,
    交互上避免歧义)。
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "取消"),
        Binding("ctrl+s", "save", "保存"),
    ]

    def __init__(self, profile: object | None = None) -> None:
        super().__init__()
        # profile: LLMProfile | None — None=新增, 非 None=编辑预填
        self._initial = profile

    def compose(self) -> ComposeResult:
        from textual.containers import VerticalScroll

        p = self._initial

        def _g(attr: str) -> str:
            v = getattr(p, attr, None) if p else None
            return "" if v is None else str(v)

        yield VerticalScroll(
            Static(
                "新增 / 编辑 LLM Profile  (Ctrl+S 保存, Esc 取消)",
                classes="picker-title",
            ),
            Static("name (唯一标识, 编辑时锁定)"),
            Input(value=_g("name"), id="f-name", disabled=p is not None),
            Static("protocol"),
            Select(
                [("anthropic", "anthropic"), ("openai", "openai")],
                value=(getattr(p, "protocol", None) or "anthropic"),
                id="f-protocol",
                allow_blank=False,
            ),
            Static("base_url"),
            Input(
                value=_g("base_url"),
                id="f-base_url",
                placeholder="https://api.deepseek.com/anthropic",
            ),
            Static("api_key"),
            Input(value=_g("api_key"), id="f-api_key", password=True),
            Static("model"),
            Input(
                value=_g("model"), id="f-model", placeholder="deepseek-v4-pro",
            ),
            Static("max_tokens (可空, 正整数)"),
            Input(value=_g("max_tokens"), id="f-max_tokens"),
            Static("structured_output_mode (仅 openai: json_schema / json_object)"),
            Select(
                [("json_schema", "json_schema"), ("json_object", "json_object")],
                value=(getattr(p, "structured_output_mode", None) or "json_schema"),
                id="f-mode",
                allow_blank=False,
            ),
            Static("— light LLM 覆盖 (留空 = 用上面主配置) —", classes="picker-title"),
            Static("light_model (可空)"),
            Input(value=_g("light_model"), id="f-light_model"),
            Static("light_base_url (可空)"),
            Input(value=_g("light_base_url"), id="f-light_base_url"),
            Static("light_api_key (可空)"),
            Input(value=_g("light_api_key"), id="f-light_api_key", password=True),
            Static("", id="f-error"),
            id="llm-form-container",
        )

    def on_mount(self) -> None:
        # 新增时 focus name; 编辑时 (name 锁) focus base_url
        target = "#f-base_url" if self._initial is not None else "#f-name"
        self.query_one(target, Input).focus()

    def _val(self, wid: str) -> str:
        return self.query_one(f"#{wid}", Input).value.strip()

    def action_save(self) -> None:
        from pydantic import ValidationError

        from explain_engine.llm.llm_config import LLMProfile

        err = self.query_one("#f-error", Static)
        name = self._val("f-name")
        base_url = self._val("f-base_url")
        api_key = self._val("f-api_key")
        model = self._val("f-model")
        missing = [
            label
            for label, v in [
                ("name", name), ("base_url", base_url),
                ("api_key", api_key), ("model", model),
            ]
            if not v
        ]
        if missing:
            err.update(f"[red]缺必填字段: {', '.join(missing)}[/red]")
            return

        max_tokens: int | None = None
        mt_raw = self._val("f-max_tokens")
        if mt_raw:
            try:
                max_tokens = int(mt_raw)
                if max_tokens < 1:
                    raise ValueError
            except ValueError:
                err.update("[red]max_tokens 必须是正整数或留空[/red]")
                return

        light_max: int | None = None  # 表单暂不暴露 light_max_tokens (用主)
        try:
            profile = LLMProfile(
                name=name,
                protocol=self.query_one("#f-protocol", Select).value,
                base_url=base_url,
                api_key=api_key,
                model=model,
                max_tokens=max_tokens,
                structured_output_mode=self.query_one("#f-mode", Select).value,
                light_model=self._val("f-light_model") or None,
                light_base_url=self._val("f-light_base_url") or None,
                light_api_key=self._val("f-light_api_key") or None,
                light_max_tokens=light_max,
            )
        except ValidationError as exc:
            err.update(f"[red]校验失败: {exc}[/red]")
            return
        self.dismiss(profile)

    def action_cancel(self) -> None:
        self.dismiss(None)


class LLMConfigScreen(ModalScreen[bool]):
    """LLM provider 管理 modal — profile 列表 + 激活/新增/编辑/删除按钮.

    直接读写 LLMConfigStore (持久化到 EXPLAIN_HOME/llm_config.json)。dismiss
    返 bool: 配置是否变过 (变了 → ExplainChatApp 热重载 app.llm/light_llm/chat.llm)。
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "关闭"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._changed = False

    def compose(self) -> ComposeResult:
        from textual.containers import Horizontal, Vertical

        yield Vertical(
            Static("管理 LLM Provider", classes="picker-title"),
            OptionList(id="llm-profile-list", markup=False),
            Horizontal(
                Button("激活", id="btn-activate", variant="primary"),
                Button("新增", id="btn-add", variant="success"),
                Button("编辑", id="btn-edit"),
                Button("删除", id="btn-delete", variant="error"),
                Button("关闭", id="btn-close"),
                id="llm-btn-row",
            ),
            Static("", id="llm-config-status"),
            id="llm-config-container",
        )

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        """从 store 重读 profiles 重建 OptionList (active 标 ★)。"""
        from explain_engine.llm.llm_config import LLMConfigStore

        cfg = LLMConfigStore().load()
        ol = self.query_one("#llm-profile-list", OptionList)
        ol.clear_options()
        if not cfg.profiles:
            ol.add_option(Option("(还没有 profile — 点「新增」)", id=None, disabled=True))
            return
        for name, p in cfg.profiles.items():
            mark = "  ★active" if name == cfg.active else ""
            ol.add_option(
                Option(f"{name}{mark}   ·   {p.protocol} · {p.model}", id=name)
            )

    def _selected_name(self) -> str | None:
        ol = self.query_one("#llm-profile-list", OptionList)
        if ol.highlighted is None:
            return None
        opt = ol.get_option_at_index(ol.highlighted)
        return opt.id if isinstance(opt.id, str) else None

    def _status(self, markup: str) -> None:
        self.query_one("#llm-config-status", Static).update(markup)

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        from explain_engine.llm.llm_config import LLMConfigStore

        bid = event.button.id
        store = LLMConfigStore()

        if bid == "btn-close":
            self.dismiss(self._changed)
            return

        if bid == "btn-add":
            self.app.push_screen(LLMProfileFormScreen(), self._on_form_done)
            return

        if bid == "btn-edit":
            name = self._selected_name()
            if name is None:
                self._status("[yellow]请先选一个 profile[/yellow]")
                return
            profile = store.load().profiles.get(name)
            self.app.push_screen(
                LLMProfileFormScreen(profile), self._on_form_done
            )
            return

        if bid == "btn-activate":
            name = self._selected_name()
            if name is None:
                self._status("[yellow]请先选一个 profile[/yellow]")
                return
            store.set_active(name)
            self._changed = True
            self._refresh()
            self._status(f"[green]已激活 {name}[/green]")
            return

        if bid == "btn-delete":
            name = self._selected_name()
            if name is None:
                self._status("[yellow]请先选一个 profile[/yellow]")
                return
            store.delete(name)
            self._changed = True
            self._refresh()
            self._status(f"[green]已删除 {name}[/green]")
            return

    def _on_form_done(self, profile: object | None) -> None:
        """表单返回 LLMProfile → upsert 到 store (新增首个自动激活)。"""
        if profile is None:
            return
        from explain_engine.llm.llm_config import LLMConfigStore

        LLMConfigStore().upsert(profile)  # type: ignore[arg-type]
        self._changed = True
        self._refresh()
        self._status(f"[green]已保存 {getattr(profile, 'name', '')}[/green]")

    def action_close(self) -> None:
        self.dismiss(self._changed)


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
        # Phase 20.0 Layer B: escape cancel in-flight chat task (LLM stream stall 逃生口)
        Binding("escape", "cancel_chat", "取消"),
        # Phase 20.0 Layer C: PgUp/PgDn 翻 VerticalScroll#output. (Phase 20.2 重开
        # mouse=True 后滚轮也能翻; 保留键盘 PgUp/PgDn 作不用鼠标的替代.)
        Binding("pageup", "scroll_output_up", "上翻"),
        Binding("pagedown", "scroll_output_down", "下翻"),
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
        # Phase 19 真终端 Bug C: textual TUI app 反向引用. 让 slash handler 内 LLM
        # call 前后调 `chat.tui_app._mount_status(label)` / `_unmount_status()`
        # 替 Rich `Console().status(...)` (后者在 textual alt screen 不渲染).
        # 双向引用 (self.chat <-> chat.tui_app): Python GC cycle collector 处理,
        # 无内存泄漏. chat var 切换时 (_switch_to_chat_session / _reset_to_ephemeral)
        # 必须同步 update — 否则新 chat.tui_app 仍 None, spinner 失灵.
        self.chat.tui_app = self
        # Wave 4 Task 19/20: thinking visibility 状态. True 时新 mount
        # Collapsible 默 expand (collapsed=False); False 时默 collapse.
        # Ctrl+O 切 + slash /thinking on|off 也切 (Task 21).
        self._thinking_visible: bool = True
        # Wave 6 Task 31: 是否启动时显 splash. cli --no-splash 时 False (走干净
        # 路径 — repl_entry 自己 init lexicon backend).
        self.show_splash: bool = show_splash
        # Phase 20.0 Layer B: in-flight chat handler async task ref. escape binding
        # 触发 action_cancel_chat 时 cancel 它. _handle_input_submitted 包成 task 启,
        # add_done_callback 清 ref (task 正常完成 / 异常 / cancelled 都走). None
        # = 无 in-flight 请求.
        self._chat_task: asyncio.Task | None = None
        # Phase 20.0 Task 2 follow-up (code-quality review I-1): track in-flight chat
        # tasks in set + add_done_callback discard. 跟 session.py:255 + 395-397 同款
        # RUF006 / GC safety pattern. self._chat_task 单 ref slot 保留作 action_cancel_chat
        # 的 cancel handle; set 仅做额外 strong-ref keeper — user 快速连按 enter race
        # 时, A task 在 _on_done fire 前 B 已覆盖 self._chat_task slot, A 仅靠 callback
        # closure 维持 strong ref, 理论可被 GC. set 防 GC, 单 ref 是 "current cancellable"
        # 句柄, 二者协同.
        self._background_chat_tasks: set[asyncio.Task] = set()
        # Phase 20.3 端到端流式: 当前正在增量更新的 widget 引用 + 文本 buffer.
        # assistant_text_delta → _stream_answer Static; thinking_delta →
        # _stream_thinking Static (内嵌于 _stream_thinking_col Collapsible).
        # 每段正文/推理 mount 一个 widget, 后续 delta 调 .update(buf) 增量刷.
        # turn_complete / tool_use 边界 reset 成 None, 下一段 mount 新 widget
        # (防多轮 tool loop 的多段正文挤进同一 widget).
        self._stream_answer: Static | None = None
        self._stream_answer_buf: str = ""
        self._stream_thinking: Static | None = None
        self._stream_thinking_col: Collapsible | None = None
        self._stream_thinking_buf: str = ""

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
        """启动时 push SplashScreen (若 show_splash), 等 4 step + 5s pause 后 pop,
        写 banner + ready 行到 #output, 最终 focus Input#prompt 让用户立刻能输.

        Wave 7 fix bundle:
        - Bug 1 (4-bug bundle): splash pop 后写 banner (之前没写, #output 空白).
        - Bug 4 (4-bug bundle): splash pop / no-splash 路径都 focus Input#prompt.

        Wave 7 follow-up bundle (本 commit):
        - Bug 3: 真终端 user 看不见 splash (SVG snapshot 显示正常, 矛盾). 三层修:
          1) ~/.explain/phase19_debug.log 记 lifecycle 让 user 跑后给我看 path
          2) sleep 2.5s → 5s 让 user 真看见 (4 ✓ 点亮 + 5s 视觉停顿)
          3) splash pop 后写一行 "✓ 已加载, 启动 chat" — user 即使没看到
             splash logo 也知道它跑过 (logo 不可见 ≠ 没跑)

        show_splash=False (cli --no-splash) 跳过 splash 直接 banner + focus +
        log no_splash_path.

        流程 (show_splash=True):
        1. _log_phase19("app_start") — entry marker
        2. push SplashScreen — _log_phase19("splash_push")
        3. await splash._init_task — _log_phase19("init_task_done")
        4. asyncio.sleep(5.0) — _log_phase19("hold_sleep_done")
        5. pop_screen — _log_phase19("splash_pop")
        6. write banner + ready line — _log_phase19("banner_mounted")
        7. focus Input — _log_phase19("input_focused")

        Test 决策 (Task 31): asyncio.sleep 在测试 patch 掉避免真等 5s.
        """
        _log_phase19(
            "app_start",
            f"show_splash={self.show_splash}",
        )

        # Phase 20.2 P0 真因修: #output 智能锚定 (textual 8.2.7 原生 anchor()).
        # 前两次误诊都不是真因 — Phase 20.0 改 LLM read timeout (无 hang, chat.log
        # 全 200 OK), Phase 20.x 怀疑 markup 注入 (textual 8.2.7 对未知 [tag] 宽容,
        # 真 paint 路径不抛 MarkupError, headless 实测证伪). 真因: tui_app 从不
        # scroll + VerticalScroll 默认不跟随新内容 → 长 LLM 回答 mount 后 scroll_y
        # 钉在 0, 大半内容在 fold 下看不见, 叠加 mouse=False 滚轮死 → 用户感知
        # "输出到一半卡死, 无法上下滚动, slash 还能输出但只能从滚动条看出".
        # anchor(): 新内容跟随到底; 用户上翻 (PgUp/PgDn/scrollbar 走 _scroll_to
        # release_anchor=True) 自动脱锚不被拽下; 翻回底部重新跟随. = 智能锚定.
        self.query_one("#output", VerticalScroll).anchor()

        if not self.show_splash:
            # Bug 1: 无 splash 路径也要 banner — 不然 #output 空白.
            await self._write_banner()
            _log_phase19("banner_mounted", "no_splash_path")
            # Bug 4: 无 splash 路径也要 focus, 不然默 focused 是 VerticalScroll.
            self.query_one("#prompt", Input).focus()
            _log_phase19("input_focused", "no_splash_path")
            return

        from explain_engine.chat.splash_screen import SplashScreen

        # Phase 19 真终端 Bug D: 把 splash 整段 (init steps + 5s hold + pop +
        # banner + focus) 拆到 background worker, 让 on_mount **立刻返回**,
        # 不阻塞 textual app 启动 paint pipeline.
        #
        # 真因 (第一性原理):
        # - 老路径在 on_mount 内 `await self.push_screen(splash)` + `await
        #   splash._init_task` + `await asyncio.sleep(5.0)` + `pop_screen()`
        #   + `await self._write_banner()`. 整个串行 ~6-7s.
        # - textual `on_mount` 是 app lifecycle event — 它跑期间, screen
        #   compositor 把 paint 操作放进 queue 但**等 on_mount 返回**才让
        #   writer thread 刷出 ANSI 到 PTY (实测 PTY out.txt: 整个 splash
        #   期间 **0 frame**, banner_mounted 后才 1 frame, splash 视觉缺失).
        # - 即 user 真终端整 6-7s 看屏幕**完全静止** (老 alt screen 状态),
        #   然后突然显示 banner. lifecycle log (~/.explain/phase19_debug.log)
        #   跑全 7 行只证 splash 的 Python 代码跑了, **不证 PTY 真渲染**.
        #
        # 修法: `self.run_worker(...)` 让 splash 跑在 textual-managed async
        # worker, on_mount 立刻返回 → textual 进入正常 message pump + paint
        # 循环, splash widget paint 立刻 flush 到 PTY (实测: 7 frames 含
        # figlet logo + 4 个 ✓ step 真渲染到 PTY).
        #
        # 设计约束:
        # - thread=False — splash 内部 await async API (push_screen / pop_screen /
        #   sleep), 必须在 textual event loop 跑.
        # - exclusive=False — 不阻塞 future worker (e.g. /resume picker).
        # - 保引用 self._splash_screen 避 splash widget GC (worker fn 持有
        #   splash 引用, but 防御性双保险).
        splash = SplashScreen()
        self._splash_screen = splash
        self.run_worker(
            self._splash_lifecycle(splash),
            name="splash_lifecycle",
            exclusive=False,
            thread=False,
        )
        _log_phase19("app_start", "splash worker scheduled, on_mount returning")

    async def _splash_lifecycle(self, splash) -> None:  # type: ignore[no-untyped-def]
        """Phase 19 真终端 Bug D: splash 完整生命周期 — push / init / hold / pop / banner / focus.

        从 on_mount 拆出来跑在 background worker (textual run_worker), 让
        on_mount 立刻返回, textual paint pipeline 不被 6-7s splash sleep 阻塞.
        user 真终端能在 splash hold 5s 期间真看见 figlet logo + 4 个 ✓ step.

        参数:
            splash: SplashScreen instance (从 on_mount 传, 保引用避 GC).

        生命周期 (匹配 phase19_debug.log lifecycle 7 行):
        1. push_screen(splash) — splash_push
        2. await splash._init_task — init_task_done (4 step + 4×0.3s = 1.2s)
        3. await asyncio.sleep(5.0) — hold_sleep_done (5s 视觉停顿)
        4. pop_screen() — splash_pop
        5. _write_banner() — banner_mounted
        6. focus("#prompt") — input_focused
        """
        # push splash 到 screen stack 顶
        await self.push_screen(splash)
        _log_phase19("splash_push", "screen pushed onto stack")

        # 等 4 step 初始化跑完 (~1.2s 因 4×0.3s sleep)
        if splash._init_task is not None:
            try:
                await splash._init_task
                _log_phase19("init_task_done", "4 steps complete")
            except Exception as exc:
                # 防御: _init_task 自身 catch 了 step 异常, 这里再吞外层意外异常
                # (e.g. screen detach race). splash 仍 pop.
                _log_phase19(
                    "init_task_done",
                    f"with outer exception: {type(exc).__name__}: {exc}",
                )

        # Wave 7 follow-up Bug 3: 视觉停顿让 user 看完 ✓ 点亮. on_mount 已返,
        # textual message pump 在跑, paint pipeline 此时 free, splash 真渲染到 PTY.
        # Phase 20.1 #5: 停顿时长来自 LLM_SPLASH_PAUSE_S env knob (default 5.0,
        # 跟 Wave 7 决策一致). user 设 0 → 跳过 hold, user 设 1 → 1s.
        pause_s = _read_splash_pause_env()
        await asyncio.sleep(pause_s)
        _log_phase19("hold_sleep_done", f"{pause_s}s elapsed")

        # pop splash, main screen 回 stack 顶
        self.pop_screen()
        _log_phase19("splash_pop", "screen popped, main layer visible")

        # Bug 1 fix: splash pop 后写 banner + ready 行
        await self._write_banner()
        # Wave 7 follow-up Bug 3: 加 "已加载" 行让 user 即使 splash 没看到也知道
        # 它真跑过. 写在 banner 之后, dim/绿色 markup 视觉低调.
        await self._write(
            "[dim green]✓ 已加载 (lexicon / PG / theory cache), 进入 chat REPL.[/dim green]"
        )
        _log_phase19("banner_mounted", "banner + ready line written")
        # Bug 4 fix: splash pop 后 focus Input — 用户立刻能输, 不需 Tab.
        self.query_one("#prompt", Input).focus()
        _log_phase19("input_focused", "splash_path")

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

        Phase 20.0 Layer B: 非 slash 路径包成 self._chat_task 让 escape 可 cancel.
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

        # slash 与自然语言都包成 self._chat_task 跑后台 (escape 可 cancel).
        #
        # 设计决策: 用 fire-and-forget task (不 await self._chat_task) — 跟
        # textual message pump 协作.
        # - 若在 handler 内 await, message pump 卡 _handle_input_submitted
        #   handler 内, escape key event 进 queue 但 dispatch 不到 (因前一个
        #   handler 还没返). escape 永不 trigger action_cancel_chat.
        # - input handler 立刻返让 message pump free, escape 才有机会进 action.
        #
        # Phase 20.2 P0 (/compress 卡死修): slash 路径过去是在 handler 内
        # `await dispatch_slash` (inline), 长任务 (/compress 等) 期间 escape 失灵.
        # 现统一走 task. 注意 dispatch_slash 内同步 torch embedding 已卸 to_thread
        # (compress_dedup / flush_to_lexicon), 否则即便在 task 里同步 embed 仍占
        # 同一 event loop 线程, escape 照样 dispatch 不到.
        if text.startswith("/"):
            self._spawn_chat_task(self._consume_slash_events(text))
            return

        self._spawn_chat_task(self._consume_chat_events(text))

    def _spawn_chat_task(self, coro: Coroutine[Any, Any, None]) -> None:
        """把 chat/slash 消费 coro 包成 self._chat_task 跑后台 + 簿记.

        Phase 20.0 Task 2 follow-up (review I-1): task 同时 add 到
        self._background_chat_tasks set (RUF006 / GC safety, mirror session.py
        :395-397 pattern), self._chat_task 单 slot 保留作 cancel handle.
        _on_done 双重清: set.discard(t) 防 GC + (if self._chat_task is t:
        self._chat_task = None) 防误清 user 新开 task.
        """
        task = asyncio.create_task(coro)
        self._chat_task = task
        self._background_chat_tasks.add(task)

        def _on_done(t: asyncio.Task) -> None:
            # 双重清理: set discard (GC safety) + 单 ref nil (cancel handle).
            self._background_chat_tasks.discard(t)
            # 仅清还指本 task 的 ref (防御: user 已开新 task 时不误清).
            if self._chat_task is t:
                self._chat_task = None

        task.add_done_callback(_on_done)

    async def _consume_slash_events(self, text: str) -> None:
        """slash 派发 + 渲染, 跑在可 cancel 的 task 内.

        dispatch_slash 内部已 catch chat handler 异常返 slash_error event; 这里
        兜 dispatch_slash 自身底层异常 + CancelledError (escape 中断长 slash 如
        /compress). app 控制类 event (quit / switch / modal push) 在 _render_event
        里于 event loop 上执行 — task 内 await 同一 loop, 跟 inline 路径语义一致.
        """
        from explain_engine.chat.slash_commands import dispatch_slash

        try:
            events = await dispatch_slash(self.chat, text)
            for ev in events:
                await self._render_event(ev)
        except asyncio.CancelledError:
            await self._unmount_status()
            await self._write_styled("", "请求已取消.", suffix_style="dim")
            raise  # 让 task 真 cancelled, add_done_callback 清 ref
        except Exception as exc:
            # Wave 4 review C-1: 异常 str 当 plain text append + dim 红.
            await self._write_styled(
                "[red]slash 失败: [/red]",
                f"{type(exc).__name__}: {exc}",
                suffix_style="red",
            )

    async def _consume_chat_events(self, text: str) -> None:
        """Phase 20.0 Layer B: chat handler 包 fn — async-for 消费 events,
        catch CancelledError 兜底清 spinner + mount '请求已取消'.

        except 顺序: CancelledError 先 (Python 3.8+ 不是 Exception 子类, 但保险
        显式先 catch 防未来 except 写宽吞掉), 通用 Exception 后.
        """
        try:
            async for ev in self.chat.handle_user_input(text, self.llm):
                await self._render_event(ev)
        except asyncio.CancelledError:
            await self._unmount_status()
            await self._write_styled(
                "", "请求已取消.", suffix_style="dim"
            )
            raise  # 让 task 真 cancelled state, _handle_input_submitted add_done_callback 清 ref
        except Exception as exc:
            # Wave 4 review C-1: 异常 str 当 plain text append.
            await self._write_styled(
                "[red]chat 失败: [/red]",
                f"{type(exc).__name__}: {exc}",
                suffix_style="red",
            )

    # ─── Phase 20.3 流式增量 mount helpers ───
    async def _append_answer_delta(self, delta: str) -> None:
        """assistant_text_delta → 累积到当前正文 Static, 增量 update.

        首 delta mount 一个新 Static (markup=False — LLM 正文逐字流式期间不能
        走 markup parser: 半截 `[bo` 这类未闭合 tag 会让 Content.from_markup 抛/
        乱渲染, 且 LLM 正文里的 `[x]` 不该被当样式吃. 跟 thinking-content 同款
        防御). 后续 delta append buffer 再 .update() 整体刷.
        """
        if self._stream_answer is None:
            self._stream_answer_buf = ""
            self._stream_answer = Static("", markup=False)
            container = self.query_one("#output", VerticalScroll)
            await container.mount(self._stream_answer)
        self._stream_answer_buf += delta
        self._stream_answer.update(self._stream_answer_buf)

    async def _append_thinking_delta(self, delta: str) -> None:
        """thinking_delta → 累积到当前 thinking Collapsible 内 Static, 增量 update.

        首 delta mount Collapsible(Static(markup=False)), 默认按 _thinking_visible
        展开/折叠 (跟非流式 _mount_thinking 一致). 后续 delta 刷 buffer + 更新
        title 字数.
        """
        if self._stream_thinking is None:
            self._stream_thinking_buf = ""
            inner = Static("", markup=False, classes="thinking-content")
            self._stream_thinking = inner
            self._stream_thinking_col = Collapsible(
                inner,
                title="thinking (0 字)",
                collapsed=not self._thinking_visible,
            )
            container = self.query_one("#output", VerticalScroll)
            await container.mount(self._stream_thinking_col)
        self._stream_thinking_buf += delta
        self._stream_thinking.update(self._stream_thinking_buf)
        if self._stream_thinking_col is not None:
            self._stream_thinking_col.title = (
                f"thinking ({len(self._stream_thinking_buf)} 字)"
            )

    def _reset_stream_widgets(self) -> None:
        """正文/推理段边界 (turn_complete / tool_use) reset 流式 widget 引用.

        引用清 None 即可 — 已 mount 的 widget 留在 #output (是已完成内容), 只是
        下一段 delta 不再往它身上 append, 而是 mount 新 widget.
        """
        self._stream_answer = None
        self._stream_answer_buf = ""
        self._stream_thinking = None
        self._stream_thinking_col = None
        self._stream_thinking_buf = ""

    # ─── Phase 20.3: agent 自主调工具的可见反馈 ───
    @staticmethod
    def _tool_input_preview(tool_input: object) -> str:
        """ToolUseEvent.tool_input (dict) → 紧凑可读预览 (k=v, k=v), 截 80 字.

        让用户看到 agent 调工具时具体在操作什么 (e.g. counterfactual 的 intervention
        文本 / expand 的 l1_id). 空值字段跳过. 返空串 = 无可显示输入.
        """
        if not isinstance(tool_input, dict) or not tool_input:
            return ""
        parts = [f"{k}={v}" for k, v in tool_input.items() if v not in (None, "")]
        s = ", ".join(parts)
        return s[:80] + "…" if len(s) > 80 else s

    async def _render_tool_use(self, ev: ChatEvent) -> None:
        """tool_use 事件 → 持久 trace 行 (🔧 工具名 + input 预览) + 动画 spinner.

        - trace 行用 markup (label 来自 chat_copy.TOOL_DISPLAY_LABELS, trusted),
          留在 #output 作"agent 调过哪些工具"的可见记录.
        - input 预览走 _write_styled plain (tool_input 含 LLM 生成文本, 防 markup
          注入), dim 缩进一行.
        - spinner 复用 _mount_status (LoadingIndicator + label) — 工具执行 (可能
          数秒 LLM 调用) 期间动画转, 直接回答用户"是否卡死". tool_result 撤掉.
        """
        from explain_engine.chat.chat_copy import tool_display_label

        tool_name = getattr(ev, "tool_name", "") or ""
        tool_input = getattr(ev, "tool_input", None)
        label = tool_display_label(tool_name)
        await self._write(f"[cyan]🔧 调用工具：{label}[/cyan]")
        preview = self._tool_input_preview(tool_input)
        if preview:
            await self._write_styled("   [dim]↳ [/dim]", preview, suffix_style="dim")
        await self._mount_status(label)

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

        # Phase 20.3 流式: 正文 / 推理段增量 chunk → 增量 update 当前 widget.
        if ev_type == "assistant_text_delta":
            await self._append_answer_delta(content if isinstance(content, str) else "")
            return
        if ev_type == "thinking_delta":
            await self._append_thinking_delta(
                content if isinstance(content, str) else ""
            )
            return

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

        # Phase 20.4: /llm 管理 LLM provider.
        if ev_type == "slash_open_llm_config":
            # push 管理 modal; 关闭后若改过配置 → 热重载 (callback).
            self.push_screen(LLMConfigScreen(), self._on_llm_config_closed)
            return
        if ev_type == "slash_llm":
            # /llm show 文本结果 (trusted markup 串)
            if isinstance(content, str):
                await self._write(content)
            return
        if ev_type == "slash_llm_reload":
            # /llm use <name> 已切 active → 热重载当前 session 的 client
            await self._reload_llm_clients()
            if isinstance(content, str):
                await self._write(content)
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
            # Phase 20.3: turn 起始 reset 流式 widget 引用 — 兜底上一 turn 被
            # escape cancel 中断 (没走到 turn_complete) 残留的 stale ref, 防新
            # turn 首 delta 误 append 到上一答案 widget.
            self._reset_stream_widgets()
            await self._mount_status(content if isinstance(content, str) else "")
            return
        if ev_type == "status_end":
            await self._unmount_status()
            return

        # Phase 20.3: turn_complete 是正文段边界 → reset 流式 widget 引用.
        if ev_type == "turn_complete":
            self._reset_stream_widgets()
            return

        # Phase 20.3: agent 自主调工具的可见反馈 — 之前 tool_use/tool_result 全
        # no-op, 用户看不到 agent 在 compress / counterfactual / ... 干活, 长工具
        # 执行期屏幕静止以为卡死. 现在 tool_use → 持久 trace 行 (留 log 记录调了
        # 哪个工具) + 动画 spinner (表示"执行中, 没卡死"); tool_result → 撤 spinner.
        # 工具段也是正文边界 → reset 流式 widget (下一段正文 mount 新 widget).
        if ev_type == "tool_use":
            self._reset_stream_widgets()
            await self._render_tool_use(ev)
            return
        if ev_type == "tool_result":
            # 工具执行完 → 撤 spinner (留 trace 行). _unmount_status 幂等.
            await self._unmount_status()
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

    # ─── Phase 20.4: LLM provider 热重载 ───
    def _on_llm_config_closed(self, changed: bool | None) -> None:
        """LLMConfigScreen dismiss callback — 改过配置则热重载 (sync callback)。

        改 LLM 配置后 active profile 变了, 需用新配置重建 client。run_worker
        启 async worker (跟 _on_session_picked 同 pattern, callback 瞬返不阻
        message pump)。
        """
        if not changed:
            return
        self.run_worker(self._reload_and_notify(), name="llm_hot_reload")

    async def _reload_and_notify(self) -> None:
        await self._reload_llm_clients()
        from explain_engine.llm.llm_config import LLMConfigStore

        active = LLMConfigStore().load().active
        if active:
            await self._write(
                f"[green]✓ 已切换 LLM provider → [/green][cyan]{active}[/cyan]"
                f"[green], 当前 session 立即生效。[/green]"
            )
        else:
            await self._write(
                "[green]✓ LLM 配置已更新 (无 active profile, 回退 .env)。[/green]"
            )

    async def _reload_llm_clients(self) -> None:
        """用最新配置 (active profile / env) 重建 app.llm + light_llm + chat.llm。

        in-flight chat task 仍持旧 client 引用跑完 (无需中断); 新 turn 由
        _consume_chat_events 传 self.llm → 自动用新 client。chat.llm 同步更新
        让 slash handler (e.g. /deepen promote) 也用新 client。
        """
        from explain_engine.config import make_light_llm_client, make_llm_client

        try:
            new_main = make_llm_client()
        except Exception as exc:
            await self._write_styled(
                "[red]LLM 重建失败 (配置无效?): [/red]",
                f"{type(exc).__name__}: {exc}",
                suffix_style="red",
            )
            return
        try:
            new_light = make_light_llm_client()
        except Exception:
            # light 构造失败不致命 — 退回用主 client (跟 with_light_fallback 语义)
            new_light = new_main
        self.llm = new_main
        self.light_llm = new_light
        # chat var (ephemeral / ChatSession) 的 llm 引用同步
        self.chat.llm = new_main

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
        # Phase 19 真终端 Bug C: 切 chat var 时同步 tui_app 反向引用. 否则
        # 新 ChatSession.tui_app = None, slash handler spinner 失灵.
        self.chat.tui_app = self
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
        # Phase 19 真终端 Bug C: 重建 ephemeral 时同步 tui_app 反向引用.
        self.chat.tui_app = self
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
            "/help 看 slash, /quit 退出. "
            "[dim](滚轮翻历史; 鼠标拖选 + Ctrl+C 复制)[/dim]"
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
    async def action_cancel_chat(self) -> None:
        """Phase 20.0 Layer B Task 2: escape binding → cancel in-flight chat task.

        无 task / task 已 done → mount '(无 in-flight 请求可取消)' 行.
        有 task → cancel; CancelledError 在 _consume_chat_events 内 catch 已 mount
        '请求已取消'.

        设计: escape vs ctrl+c 语义分离 — escape = cancel current op (跟
        SessionPickerScreen 一致), ctrl+c = quit app (action_quit_app 行为不变).
        """
        if self._chat_task is None or self._chat_task.done():
            await self._write_styled(
                "", "(无 in-flight 请求可取消)", suffix_style="dim"
            )
            return
        self._chat_task.cancel()

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

    def action_scroll_output_up(self) -> None:
        """Phase 20.0 Layer C: PgUp → VerticalScroll#output 上翻一页."""
        self.query_one("#output", VerticalScroll).scroll_page_up()

    def action_scroll_output_down(self) -> None:
        """Phase 20.0 Layer C: PgDn → VerticalScroll#output 下翻一页."""
        self.query_one("#output", VerticalScroll).scroll_page_down()
