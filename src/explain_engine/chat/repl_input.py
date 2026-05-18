"""chat REPL input infrastructure (2026-05-18 prompt_toolkit upgrade).

Wave 1: BufferedLogHandler — capped in-memory log handler 替代 stdout
StreamHandler 在 chat REPL 模式期间 (避免 log 撞 prompt_toolkit prompt).

Wave 2: SlashCompleter — `/cmd` 自动联想 from DEFAULT_COMMANDS.

Wave 3+ 会加 PromptSession factory / read_input().

设计参考 docs/plans/2026-05-18-chat-repl-prompt-toolkit-design.md.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import message_dialog
from prompt_toolkit.styles import Style


class BufferedLogHandler(logging.Handler):
    """Cap-bounded in-memory log handler for chat REPL mode.

    chat REPL 模式期间替代 stdout StreamHandler — 把 logger.info /
    logger.warning 输出收集到 in-memory deque, 不直接写 stdout (会撞
    prompt_toolkit prompt). ctrl+o popup 时 dump buffer 给用户看.

    capacity: 200 line default. 老 line 自动 evict (deque maxlen).
    listeners: 每次 emit 通知, 用于 prompt_toolkit Buffer refresh.

    Thread-safety: chat REPL 单 asyncio loop, 不需 lock. logger.Handler 基类
    内 lock 已覆盖 emit 路径 (Python logging 默认).
    """

    def __init__(self, capacity: int = 200) -> None:
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=capacity)
        self._listeners: list[Callable[[], None]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.buffer.append(msg)
            for cb in self._listeners:
                try:
                    cb()
                except Exception:
                    pass  # listener bug 不该撕裂 logging chain
        except Exception:
            self.handleError(record)

    def add_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def get_text(self) -> str:
        return "\n".join(self.buffer)


class SlashCompleter(Completer):
    """Autocomplete `/cmd` from DEFAULT_COMMANDS when text starts with '/'.

    触发条件:
    1. text 以 '/' 起 (否则不联想, 自然语言输入不打扰)
    2. cursor 仍在第一 token 内 (text.split() 后只有 1 个 token)

    第二 token 起不联想 — 防 `/new 为什么 X` 被错联想 cmd.

    每个 Completion 含:
    - text: cmd name (e.g. 'resume')
    - display: f'/{name}' (UI 显示带 / 前缀)
    - display_meta: SlashCommand.description (短描述, 弹菜单第二行)
    - start_position: -len(current) 让 prompt_toolkit 替换 current input
    """

    def get_completions(self, document: Document, complete_event):
        # Local import 避 module-load 时拉 slash_commands (它 transitively 拉
        # ChatSession / engines / 重物). Completer 用时才 import.
        from explain_engine.chat.slash_commands import DEFAULT_COMMANDS

        text = document.text_before_cursor

        if not text.startswith("/"):
            return

        # split → token list. 第二 token 起不联想.
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            return

        # current = '/' 后到 cursor 的部分
        current = parts[0][1:] if parts else ""

        for cmd in DEFAULT_COMMANDS:
            if cmd.name.startswith(current):
                yield Completion(
                    text=cmd.name,
                    start_position=-len(current),
                    display=f"/{cmd.name}",
                    display_meta=cmd.description,
                )


_REPL_STYLE = Style.from_dict({
    # 灰色 log 显示 (用户需求 #2: "需要修改为灰色")
    "log-line": "fg:#888888",
    # autocomplete 菜单样式
    "completion-menu.completion": "bg:#444444 fg:white",
    "completion-menu.completion.current": "bg:#888888 fg:white",
    # bottom toolbar (显示 buffered log 计数)
    "bottom-toolbar": "fg:#888888 bg:#222222",
})


def _make_bottom_toolbar(log_handler: BufferedLogHandler):
    """Return callable for prompt_toolkit bottom_toolbar.

    显示 'ctrl+o: log (N lines buffered)' 让用户知道有 log 可看.
    """
    def _toolbar():
        n = len(log_handler.buffer)
        return f"  ctrl+o: log ({n} lines buffered) | ctrl+d: exit"
    return _toolbar


def _make_key_bindings(log_handler: BufferedLogHandler) -> KeyBindings:
    """Return KeyBindings for chat REPL.

    - ctrl+o: 弹 message_dialog 显示 log buffer (用户需求 #2: 默认隐藏,
      快捷键展开). 覆盖 emacs default insert-newline-and-stay (chat 单行
      input, 不需 newline; 如需 multi-line 用 alt+enter).
    """
    kb = KeyBindings()

    @kb.add("c-o")
    def _toggle_log(event):
        text = log_handler.get_text() or "(no log buffered yet)"
        n = len(log_handler.buffer)
        title = f"Log buffer ({n} lines)"

        async def _show():
            await message_dialog(
                title=title,
                text=text,
                style=_REPL_STYLE,
            ).run_async()

        event.app.create_background_task(_show())

    return kb


def _make_session(log_handler: BufferedLogHandler) -> PromptSession:
    """Build PromptSession with completer + key bindings + bottom toolbar.

    一个 session 复用整 chat REPL 生命周期 (history 跨 turn 跨 slash 共享 in-memory).
    """
    return PromptSession(
        completer=SlashCompleter(),
        key_bindings=_make_key_bindings(log_handler),
        style=_REPL_STYLE,
        complete_while_typing=True,  # 输 / 时自动弹菜单, 不需 tab
        bottom_toolbar=_make_bottom_toolbar(log_handler),
    )


async def read_input(
    session: PromptSession,
    prompt_text: str = "\n> ",
) -> str:
    """Read one line of user input via prompt_toolkit.

    patch_stdout() 上下文管理器把 print/stdout 写动作 buffered 到 prompt
    上方滚动区, 不撞 prompt — 用户需求 #1.

    EOFError (ctrl+d) / KeyboardInterrupt (ctrl+c) 由 caller (cli.py
    _run_chat_repl_async) 自己 except, 这里不 catch.

    Args:
        session: _make_session() 产的 PromptSession (caller hold reference 复用)
        prompt_text: prompt 前缀, default "\\n> " (与现有 cli 行为对齐)
    """
    with patch_stdout():
        return await session.prompt_async(prompt_text)
