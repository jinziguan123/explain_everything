# chat REPL prompt_toolkit 升级 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** chat REPL 用 prompt_toolkit 替代 raw `input()` + readline, 一次解决 3 个 UX issue (log 撞 prompt / log 默认显示无样式 / slash 无 autocomplete).

**Architecture:** 加 `prompt_toolkit>=3.0` dep. 新模块 `src/explain_engine/chat/repl_input.py` 封装 `BufferedLogHandler` + `SlashCompleter` + `read_input(log_handler)`. `cli._run_chat_repl_async` 进 chat 模式时 swap root logger handler (stdout → BufferedLogHandler), 退出 restore. ctrl+o toggle `message_dialog` 显示 log buffer.

**Tech Stack:** Python 3.11+ / prompt_toolkit 3.x / pytest + pytest-asyncio / Typer / Rich / uv-managed venv

**Setup pre-flight:**

- 分支: `dev` (HEAD `57c50e3` — design doc 已 commit)
- 全测基线: `.venv/bin/python -m pytest -x` 应 665 PASS
- Lint: `.venv/bin/ruff check src/ tests/` 应 0
- Design 参考: [docs/plans/2026-05-18-chat-repl-prompt-toolkit-design.md](2026-05-18-chat-repl-prompt-toolkit-design.md)
- prompt_toolkit docs: https://python-prompt-toolkit.readthedocs.io/

---

## Wave 1 — Dep + BufferedLogHandler

### Task 1: 加 prompt_toolkit dep + BufferedLogHandler 实装

**Files:**
- Modify: `pyproject.toml` (加 prompt_toolkit dep)
- Create: `src/explain_engine/chat/repl_input.py` (新模块, 初版只 BufferedLogHandler)
- Create: `tests/test_chat_repl_input.py` (新测试文件)

**Step 1.1: 加 prompt_toolkit dep**

推荐用 uv 自动管理:
```bash
uv add 'prompt_toolkit>=3.0'
```

或手动编辑 `pyproject.toml` 把 `"prompt_toolkit>=3.0",` 加到 `dependencies = [...]` list 内, 然后:
```bash
uv sync
```

**Step 1.2: Verify dep installed**

Run: `.venv/bin/python -c "import prompt_toolkit; print(prompt_toolkit.__version__)"`
Expected: 3.x.y 输出 (具体版本不重要, 只要是 3.x).

**Step 1.3: 写 failing test — BufferedLogHandler**

Create `tests/test_chat_repl_input.py`:

```python
"""Tests for chat REPL input infrastructure (Wave 1+ 2026-05-18 prompt_toolkit upgrade)."""

import logging

from explain_engine.chat.repl_input import BufferedLogHandler


class TestBufferedLogHandler:
    def test_capacity_caps_buffer_size(self):
        """deque maxlen 限制 buffer 总行数."""
        h = BufferedLogHandler(capacity=3)
        logger = logging.getLogger("test_buffered_capacity")
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        try:
            for i in range(5):
                logger.info("line %d", i)
            assert len(h.buffer) == 3
            assert list(h.buffer) == ["line 2", "line 3", "line 4"]
        finally:
            logger.removeHandler(h)

    def test_listener_notified_on_emit(self):
        """每次 emit 调 listener (用于 prompt_toolkit Buffer refresh)."""
        h = BufferedLogHandler(capacity=10)
        calls: list[int] = []
        h.add_listener(lambda: calls.append(1))

        logger = logging.getLogger("test_buffered_listener")
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        try:
            logger.info("first")
            logger.info("second")
            assert sum(calls) == 2
        finally:
            logger.removeHandler(h)

    def test_listener_exception_does_not_break_emit(self):
        """Listener 抛异常不影响 emit (防 listener bug 死循环)."""
        h = BufferedLogHandler(capacity=10)

        def bad_listener():
            raise RuntimeError("listener bug")

        h.add_listener(bad_listener)
        logger = logging.getLogger("test_buffered_listener_err")
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        try:
            logger.info("still works")
            assert "still works" in h.buffer
        finally:
            logger.removeHandler(h)

    def test_get_text_joins_buffer(self):
        """get_text() 返 buffer 内容用 \\n 拼接."""
        h = BufferedLogHandler(capacity=10)
        logger = logging.getLogger("test_buffered_get_text")
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        try:
            logger.info("a")
            logger.info("b")
            logger.info("c")
            assert h.get_text() == "a\nb\nc"
        finally:
            logger.removeHandler(h)

    def test_get_text_empty_buffer(self):
        """空 buffer get_text 返空 string (不 raise)."""
        h = BufferedLogHandler(capacity=10)
        assert h.get_text() == ""
```

**Step 1.4: 跑测试确认 fail**

Run: `.venv/bin/python -m pytest tests/test_chat_repl_input.py -v`
Expected: 全 FAIL — `ImportError: cannot import name 'BufferedLogHandler' from 'explain_engine.chat.repl_input'` (module 不存在 / class 不存在).

**Step 1.5: 实装 BufferedLogHandler**

Create `src/explain_engine/chat/repl_input.py`:

```python
"""chat REPL input infrastructure (2026-05-18 prompt_toolkit upgrade).

Wave 1: BufferedLogHandler — capped in-memory log handler 替代 stdout
StreamHandler 在 chat REPL 模式期间 (避免 log 撞 prompt_toolkit prompt).

Wave 2+ 会加 SlashCompleter / PromptSession factory / read_input().

设计参考 docs/plans/2026-05-18-chat-repl-prompt-toolkit-design.md.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable


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
```

**Step 1.6: 跑测试确认 pass**

Run: `.venv/bin/python -m pytest tests/test_chat_repl_input.py -v`
Expected: 5/5 PASS.

**Step 1.7: 全测 baseline**

Run: `.venv/bin/python -m pytest -x`
Expected: 670 PASS (665 baseline + 5 new).

**Step 1.8: Lint**

Run: `.venv/bin/ruff check src/explain_engine/chat/repl_input.py tests/test_chat_repl_input.py`
Expected: 0 issue.

**Step 1.9: Commit**

```bash
git add pyproject.toml uv.lock src/explain_engine/chat/repl_input.py tests/test_chat_repl_input.py
git commit -m "$(cat <<'EOF'
chat/repl · 加 prompt_toolkit dep + BufferedLogHandler (Wave 1/5)

为 chat REPL UX 升级铺路 (2026-05-18 design). BufferedLogHandler 是
capped deque (default 200 行) 的 logging.Handler 子类 — chat 模式期间
swap 进 root logger 替代 StreamHandler→stdout, 防 log 撞 prompt_toolkit
prompt. ctrl+o popup 时 dump buffer 显示.

5 unit test 覆盖 (capacity cap / listener notify / listener except 防裂 /
get_text join / empty buffer).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 2 — SlashCompleter

### Task 2: 实装 SlashCompleter + tests

**Files:**
- Modify: `src/explain_engine/chat/repl_input.py` (加 SlashCompleter class)
- Modify: `tests/test_chat_repl_input.py` (加 TestSlashCompleter class)

**Step 2.1: 写 failing test — SlashCompleter**

加到 `tests/test_chat_repl_input.py` 末尾:

```python
class TestSlashCompleter:
    """SlashCompleter — `/cmd` 自动联想 from DEFAULT_COMMANDS."""

    def _make_doc(self, text: str):
        """Helper: 构造 prompt_toolkit Document 模拟 cursor 在末尾."""
        from prompt_toolkit.document import Document
        return Document(text=text, cursor_position=len(text))

    def _make_completer(self):
        from explain_engine.chat.repl_input import SlashCompleter
        return SlashCompleter()

    def test_empty_text_no_completions(self):
        """空 input → 不联想."""
        c = self._make_completer()
        completions = list(c.get_completions(self._make_doc(""), None))
        assert completions == []

    def test_non_slash_no_completions(self):
        """text 不以 / 起 → 不联想 (自然语言对话不打扰)."""
        c = self._make_completer()
        completions = list(c.get_completions(self._make_doc("hello"), None))
        assert completions == []

    def test_slash_only_lists_all_commands(self):
        """text == '/' → 全 8 cmd 都 yield."""
        from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
        c = self._make_completer()
        completions = list(c.get_completions(self._make_doc("/"), None))
        cmd_names = {comp.text for comp in completions}
        expected = {cmd.name for cmd in DEFAULT_COMMANDS}
        assert cmd_names == expected

    def test_slash_prefix_filters(self):
        """text == '/r' → 仅 startswith r 的 cmd (resume)."""
        c = self._make_completer()
        completions = list(c.get_completions(self._make_doc("/r"), None))
        cmd_names = {comp.text for comp in completions}
        assert "resume" in cmd_names
        assert "quit" not in cmd_names

    def test_second_token_no_completions(self):
        """text == '/new 为什么 X' (有空格 + args) → 不联想 cmd.

        防 user 在 /new 之后输 question 被错联想 (commands name 之间
        子串匹配会很 noisy).
        """
        c = self._make_completer()
        completions = list(c.get_completions(self._make_doc("/new 为什么 X"), None))
        assert completions == []

    def test_completion_carries_description(self):
        """Completion 含 display_meta = 该 cmd 的 description (给 prompt_toolkit menu 用)."""
        c = self._make_completer()
        completions = list(c.get_completions(self._make_doc("/h"), None))
        # Should match 'help' command
        help_completion = next((co for co in completions if co.text == "help"), None)
        assert help_completion is not None
        # display_meta 应该是 SlashCommand.description; prompt_toolkit Completion
        # 的 display_meta 字段 type 是 OneStyleAndTextTuples (str-like).
        # 直接 str() 检查含 'slash command' / 描述关键字即可.
        assert help_completion.display_meta is not None
```

**Step 2.2: 跑测试确认 fail**

Run: `.venv/bin/python -m pytest tests/test_chat_repl_input.py::TestSlashCompleter -v`
Expected: 6 FAIL — SlashCompleter 不存在.

**Step 2.3: 实装 SlashCompleter**

加到 `src/explain_engine/chat/repl_input.py` (在 BufferedLogHandler 之后):

```python
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document


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
            # 已有空格 + args
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
```

**Step 2.4: 跑测试确认 pass**

Run: `.venv/bin/python -m pytest tests/test_chat_repl_input.py::TestSlashCompleter -v`
Expected: 6/6 PASS.

**Step 2.5: 跑全测**

Run: `.venv/bin/python -m pytest -x`
Expected: 676 PASS (670 + 6).

**Step 2.6: Lint**

Run: `.venv/bin/ruff check src/explain_engine/chat/repl_input.py tests/test_chat_repl_input.py`
Expected: 0.

**Step 2.7: Commit**

```bash
git add src/explain_engine/chat/repl_input.py tests/test_chat_repl_input.py
git commit -m "$(cat <<'EOF'
chat/repl · 加 SlashCompleter — /cmd 自动联想 (Wave 2/5)

prompt_toolkit Completer 子类. 触发条件:
- text 以 '/' 起 (否则不打扰自然语言输入)
- cursor 仍在第一 token (第二 token 起不联想, 防 /new 为什么 X 错触发)

每个 Completion 含 display=/cmd + display_meta=description, prompt_toolkit
自动弹下拉菜单. 输 /r 过滤到 resume; 输 /h 过滤到 help.

6 unit test 覆盖 (空 / 非 slash / 全列 / prefix filter / 第二 token / display_meta).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 3 — PromptSession + log popup + read_input

### Task 3: 实装 PromptSession factory + ctrl+o KeyBinding + read_input

**Files:**
- Modify: `src/explain_engine/chat/repl_input.py` (加 _make_session + read_input + KeyBindings)
- 无新 test (prompt_toolkit Application 真实交互需 tty, design §7.2 已说明不测; 留 acceptance 手测)

**Step 3.1: 实装 PromptSession + KeyBindings + read_input**

加到 `src/explain_engine/chat/repl_input.py` 末尾:

```python
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import message_dialog
from prompt_toolkit.styles import Style


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

        # message_dialog 是 modal full-screen, 任意 Enter 关闭, 回到 prompt.
        # run_in_terminal 暂停 prompt UI 期间弹 dialog.
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
        prompt_text: prompt 前缀, default "\n> " (与现有 cli 行为对齐)
    """
    with patch_stdout():
        return await session.prompt_async(prompt_text)
```

**Step 3.2: Verify 没破 import / 现有 test**

Run: `.venv/bin/python -c "from explain_engine.chat.repl_input import read_input, _make_session, BufferedLogHandler, SlashCompleter; print('OK')"`
Expected: "OK"

Run: `.venv/bin/python -m pytest -x`
Expected: 676 PASS (无新 test, 无 regression).

**Step 3.3: Lint**

Run: `.venv/bin/ruff check src/explain_engine/chat/repl_input.py`
Expected: 0.

**Step 3.4: Commit**

```bash
git add src/explain_engine/chat/repl_input.py
git commit -m "$(cat <<'EOF'
chat/repl · 加 PromptSession factory + ctrl+o log popup + read_input (Wave 3/5)

3 个 API 接通 prompt_toolkit:
- _make_session(log_handler): 包 SlashCompleter + KeyBindings + bottom_toolbar + Style
- _make_key_bindings: ctrl+o 触发 message_dialog 显示 log buffer (default modal full-screen)
- read_input(session): patch_stdout() 包 prompt_async, log 自动 routes 到 prompt 上方

Style 含灰色 log-line (用户需求 #2) + 自定义 completion 菜单 + bottom toolbar.
complete_while_typing=True 让用户输 / 时自动弹联想菜单, 不必 tab.

prompt_toolkit Application 真实交互测需 tty, 不写自动测; 留 Wave 5 acceptance
手测覆盖.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 4 — cli.py 集成 + logging handler swap

### Task 4: 改 _run_chat_repl_async 用 read_input + swap log handler

**Files:**
- Modify: `src/explain_engine/cli.py` (`_run_chat_repl_async` 函数: 用 read_input + try/finally swap handler)
- Modify: `tests/test_cli_chat.py` (现有 TestReplSwitchSession test 用 monkeypatch input 的 path 需迁到 monkeypatch PromptSession.prompt_async)

**Step 4.1: 读现有 cli.py _run_chat_repl_async + tests/test_cli_chat.py 现状**

Run: `.venv/bin/python -c "import inspect; from explain_engine.cli import _run_chat_repl_async; print(inspect.getsourcelines(_run_chat_repl_async)[1])"`
记起始行号.

读 `src/explain_engine/cli.py` 从该行号到函数末. 现状:
```python
user_input = await asyncio.to_thread(input, "\n> ")
```

读 `tests/test_cli_chat.py` 看现有 2 个 TestReplSwitchSession test 怎么 mock `builtins.input`.

**Step 4.2: 改 cli.py**

修改 `_run_chat_repl_async`:

```python
async def _run_chat_repl_async(
    initial_sid: str,
    llm: "LLMClient | None",
    tool_budget_per_turn: int,
    tool_budget_per_session: int,
) -> None:
    """REPL 主循环 (Wave 2 抽出 + 2026-05-18 prompt_toolkit 升级).

    chat 模式期间:
    - swap root logging handler (stdout → BufferedLogHandler), 退出 restore
    - 用 prompt_toolkit PromptSession + read_input 替代 asyncio.to_thread(input)
    - 用户输入 ctrl+o 弹 log popup 看 buffered log (灰色样式)
    - 输 / 自动弹 slash command 联想菜单

    切换契约: handler yield ChatEvent(type='slash_switch_session', ...) 后,
    本函数 single turn iter 结束后做 aclose+reload (Wave 2 不变).
    """
    from explain_engine.chat.repl_input import (
        BufferedLogHandler,
        _make_session,
        read_input,
    )
    from explain_engine.chat.session import ChatSession, ChatSessionLoadError

    # ── chat 模式 enter: swap logging handler ──
    log_handler = BufferedLogHandler(capacity=200)
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    root_logger.handlers = [log_handler]
    root_logger.setLevel(logging.INFO)

    try:
        chat_session = ChatSession(initial_sid, llm=llm)
    except FileNotFoundError as exc:
        # restore before raising
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    _apply_budget_flags(
        chat_session, tool_budget_per_turn, tool_budget_per_session
    )

    has_tools_api = (
        hasattr(llm, "chat_with_tools") if llm is not None else False
    )
    console.print(
        f"[dim]Loaded session {initial_sid}. "
        f"Type /help for commands. /quit to exit. ctrl+o toggle log.[/dim]"
    )
    if llm is not None and not has_tools_api:
        console.print(
            "[yellow]⚠️  LLM dispatch 未实装 (LLMClient.chat_with_tools 不存在). "
            "自然语言输入会无响应; 仅 slash 命令工作.[/yellow]"
        )

    # ── Build prompt_toolkit session (reuse across turns for history) ──
    pt_session = _make_session(log_handler)

    try:
        while True:
            try:
                user_input = await read_input(pt_session)
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Interrupted. Saving...[/dim]")
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            quit_requested = False
            switch_to_sid: str | None = None
            try:
                async for event in chat_session.handle_user_input(
                    user_input, llm=llm
                ):
                    if event.type == "slash_switch_session":
                        switch_to_sid = event.content["sid"]
                        continue
                    _render_event(console, event)
                    if event.type == "slash_quit":
                        quit_requested = True
            except Exception as exc:
                console.print(
                    f"[red]Error: {type(exc).__name__}: {exc}[/red]"
                )
                continue

            if switch_to_sid and switch_to_sid != chat_session.sid:
                old_sid = chat_session.sid
                await chat_session.aclose()
                try:
                    chat_session = ChatSession(switch_to_sid, llm=llm)
                except (FileNotFoundError, ChatSessionLoadError) as exc:
                    console.print(f"[red]切换失败: {exc}[/red]")
                    try:
                        chat_session = ChatSession(old_sid, llm=llm)
                    except (FileNotFoundError, ChatSessionLoadError) as recover_exc:
                        console.print(
                            f"[red]恢复原 session ({old_sid}) 也失败: "
                            f"{recover_exc}. 退出.[/red]"
                        )
                        return
                _apply_budget_flags(
                    chat_session, tool_budget_per_turn, tool_budget_per_session
                )
                console.print(
                    f"[green]Switched to {chat_session.sid}.[/green]"
                )

            if quit_requested:
                break

        await chat_session.aclose()
        console.print(f"[green]Session {chat_session.sid} saved.[/green]")
    finally:
        # ── chat 模式 exit: restore log handlers ──
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
```

注意:
- `try/finally` 包整 REPL loop, 保证退出时 restore handler 即使中途 exception
- 早 return path (FileNotFoundError) 也手动 restore
- `pt_session` 一份, 跨 turn 复用 (history 自然累积)

**Step 4.3: 改 tests/test_cli_chat.py 适配新 input path**

现有 2 个 test (`test_switch_session_replaces_chat_session` + `test_switch_failure_recovers_to_previous_not_initial`) 用 `monkeypatch.setattr("builtins.input", ...)` mock input. Wave 4 后实际走 `read_input(pt_session)` 不再走 `builtins.input`. 改 mock target.

Mock 策略选 **修改 `read_input` 自身** (更精确, 不需 mock PromptSession 内部):

```python
# tests/test_cli_chat.py 改 (2 个 test 都改, 共用 pattern)

@pytest.mark.asyncio
async def test_switch_session_replaces_chat_session(self, monkeypatch) -> None:
    from tests.test_chat_session import _make_done_session
    from explain_engine.cli import _run_chat_repl_async
    from explain_engine.chat.session import ChatEvent, ChatSession

    _make_done_session("s_22222001")
    _make_done_session("s_22222002")

    # 改: mock chat.repl_input.read_input (替代 builtins.input)
    inputs = iter(["switch please", "/quit"])

    async def fake_read_input(pt_session, prompt_text="\n> "):
        return next(inputs)

    monkeypatch.setattr(
        "explain_engine.cli.read_input",  # 注: import 是 from chat.repl_input,
        fake_read_input,                  # cli._run_chat_repl_async 内 local import
        raising=False,                    # 若 attribute 不存在不抛 (因 local import)
    )
    # 实际上 cli.py 用 local import — monkeypatch 要 patch source 模块
    monkeypatch.setattr(
        "explain_engine.chat.repl_input.read_input", fake_read_input
    )

    observed_sids: list[str] = []

    async def fake_handle(self, text, llm=None):
        observed_sids.append(self.sid)
        if text == "switch please":
            yield ChatEvent(
                type="slash_switch_session",
                content={"sid": "s_22222002"},
            )
        elif text == "/quit":
            yield ChatEvent(type="slash_quit", content="bye")

    monkeypatch.setattr(ChatSession, "handle_user_input", fake_handle)

    await _run_chat_repl_async(
        initial_sid="s_22222001",
        llm=None,
        tool_budget_per_turn=10,
        tool_budget_per_session=50,
    )

    assert observed_sids == ["s_22222001", "s_22222002"]
```

同样改 `test_switch_failure_recovers_to_previous_not_initial`. 把每个 test 内的 `monkeypatch.setattr("builtins.input", ...)` 整体替换为 `fake_read_input` async function + monkeypatch `explain_engine.chat.repl_input.read_input`.

**Step 4.4: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_cli_chat.py -v`
Expected: 6/6 PASS (现有 4 surface test + 2 改造后的 switch test).

Run: `.venv/bin/python -m pytest -x`
Expected: 676 PASS (Wave 2-3 没加 test, 仍 676).

**Step 4.5: Lint**

Run: `.venv/bin/ruff check src/explain_engine/cli.py tests/test_cli_chat.py`
Expected: 0.

**Step 4.6: Commit**

```bash
git add src/explain_engine/cli.py tests/test_cli_chat.py
git commit -m "$(cat <<'EOF'
cli/chat · _run_chat_repl_async 集成 prompt_toolkit (Wave 4/5)

chat 模式 enter 时 swap root logger handler 到 BufferedLogHandler
(原 stdout StreamHandler 暂存, 退出 restore). 用 prompt_toolkit
PromptSession 替代 asyncio.to_thread(input) — 3 个 issue 落地:

- #1 patch_stdout() 包 prompt_async, 任何 stdout 写动作自动 routes
  到 prompt 上方滚动区, 不再撞 user 编辑行
- #2 LLM 调用 + session_memory_writer log 改写到 BufferedLogHandler
  in-memory deque (200 line cap), ctrl+o 弹 message_dialog 显示 (灰色)
- #3 输 / 自动弹 SlashCompleter 菜单含 8 个 cmd + description

pt_session 跨 turn 复用让 up/down 历史导航 free. try/finally 包整 REPL
loop 保证 logger handler restore 即使中途 exception.

tests/test_cli_chat.py 2 个 TestReplSwitchSession test 适配新 input
path (从 monkeypatch builtins.input 迁到 monkeypatch
explain_engine.chat.repl_input.read_input async fake).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 5 — Acceptance smoke 文档

### Task 5: 写 manual smoke checklist

**Files:**
- Create: `docs/plans/2026-05-18-chat-repl-prompt-toolkit-acceptance.md`

**Step 5.1: 写 acceptance doc**

Create `docs/plans/2026-05-18-chat-repl-prompt-toolkit-acceptance.md`:

```markdown
# chat REPL prompt_toolkit 升级 Acceptance Checklist

> Design: [2026-05-18-chat-repl-prompt-toolkit-design.md](2026-05-18-chat-repl-prompt-toolkit-design.md)
> Plan: [2026-05-18-chat-repl-prompt-toolkit-plan.md](2026-05-18-chat-repl-prompt-toolkit-plan.md)

prompt_toolkit Application 真实交互需 tty, 自动测难做. 这 8 步手测覆盖
3 个用户报的 UX issue.

## Setup

1. 确认在 dev branch HEAD = Wave 4 commit
2. 跑 `.venv/bin/python -m pytest -x` 应全 PASS
3. 准备一个 existing session (跑 `.venv/bin/python -m explain_engine list` 看现成 sid)
   或创建: `.venv/bin/python -m explain_engine new "smoke test question"`

## Smoke Steps

### S1: `/` 弹自动联想菜单 (#3)

操作: 进 chat `python -m explain_engine chat <sid>`. 输 `/` (不按 enter).
预期: 终端弹出下拉菜单列 8 个 slash command (quit/help/show/budget/compact/save/new/resume), 每项后面是 description.

### S2: `/r` 过滤到 resume (#3)

操作: 输 `/r` (不按 enter).
预期: 菜单收窄到只显示 `resume`.

### S3: `/new 为什么 X` 不再联想 (#3)

操作: 输 `/new 为什么 X` (含空格).
预期: 菜单消失. 表示第二 token 不联想自然语言.

### S4: LLM 调用期间 log 不撞 prompt (#1)

操作: 退出菜单不输. 输自然语言 e.g. "帮我看看 graph 哪里需要 expand". Enter.
等 LLM 调用 + tool dispatch + session_memory_writer 触发.
预期: 编辑行 `> ` 始终独立, log 不"覆盖"在 user 输入字符上. log 不可见 (走 BufferedLogHandler 了).

### S5: 删除中文字符无残影 (#1, regression from Phase 9 readline)

操作: 输 `你好` 然后 backspace 删. 重复几次.
预期: 字符完全消失, 不留视觉残影. (Phase 9 readline 有这个 bug, prompt_toolkit 应 fix.)

### S6: ctrl+o 弹 log popup (#2)

操作: 跑过几轮 LLM 调用后, 按 `ctrl+o`.
预期: 弹出 modal full-screen dialog 显示 log buffer 内容 (HTTP request log + session_memory_writer log). 灰色样式 (#888888 fg). 任意 Enter 关闭回 prompt.

### S7: bottom toolbar 计数 (#2)

操作: 看 prompt 底部.
预期: 一行 toolbar: "ctrl+o: log (N lines buffered) | ctrl+d: exit". N 随 log 增长.

### S8: 退出后 stdout log 恢复 (#2 副效益)

操作: `/quit` 退出 chat. 跑 `.venv/bin/python -m explain_engine list`.
预期: list 命令的 logger.info 正常打 stdout, 不再静默 (chat 模式的 swap 已 restore).

## Pass/Fail 标准

8 步全过 → ✅ 接受
任一不过 → 提 issue 含具体 step + 预期 vs 实际

## 已知 trade-off

- ctrl+o 覆盖 prompt_toolkit emacs default newline-and-stay. chat 单行 input 不冲突.
- log popup 是 modal full-screen (而非 inline floating window) — design §4.4 决定走 simpler 路径.
- history 是 InMemoryHistory (不持久化跨 session) — YAGNI.

## Follow-up risks

- macOS Terminal.app vs iTerm 行为差异 (理论 prompt_toolkit cover, 验证)
- 老版 ssh 终端 (e.g. PuTTY old) 可能 ANSI 不全, 备用方案见 design §8.2
```

**Step 5.2: Commit**

```bash
git add docs/plans/2026-05-18-chat-repl-prompt-toolkit-acceptance.md
git commit -m "$(cat <<'EOF'
docs/plans · 加 chat REPL prompt_toolkit 升级 acceptance smoke (Wave 5/5)

8 步手测 checklist 覆盖 3 个用户 raised UX issue:
- S1-S3: slash 自动联想 (/, /r filter, /new <空格> 不联想)
- S4-S5: log 不撞 prompt + 中文删除无残影 (Phase 9 readline bug regression)
- S6-S7: ctrl+o 弹 log popup (灰色样式) + bottom toolbar 计数
- S8: 退出后 stdout log 恢复 (cli list 等命令仍打 log)

prompt_toolkit Application 真实交互需 tty, 自动测难做. 留手测 acceptance.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Acceptance checklist (整体)

落地完成后:

- [ ] 全测 PASS: `.venv/bin/python -m pytest -x` (~676 total: 665 + 11 new)
- [ ] ruff 0: `.venv/bin/ruff check src/ tests/`
- [ ] `git log --oneline dev ^master` 显示 5 commit (4 wave + 1 acceptance doc)
- [ ] pyproject.toml 含 `prompt_toolkit>=3.0`
- [ ] (Manual) 跑 acceptance smoke 8 步, 全过

---

## Risk 回顾

- **prompt_toolkit API 实际差异** — design 假设 `PromptSession`, `patch_stdout`, `message_dialog`, `Completer`, `Style.from_dict`, `KeyBindings` 都是 3.x stable API. 个别签名差异 (e.g. `complete_while_typing` 参数名) 实装时 verify pt 文档.
- **`run_in_terminal` vs `create_background_task`** — message_dialog 在 prompt_toolkit 内部需要 take over terminal, design 用 `event.app.create_background_task(_show())`. 实装时如果 hang 或 dialog 不显示, 改用 `await event.app.run_system_async(...)` 或类似 API.
- **handler swap 副作用** — root logger 改了 handlers, 任何在 chat 模式期间 import + 顶层 logger.info 都会写 buffer. 如果某模块期望 stdout log, chat 模式期间不会有. 这是 expected.
- **session_memory_writer log 在 chat 退出后丢** — Wave 4 设计 restore handler 后, buffer 不刷到 stdout. 用户如果 ctrl+o 看过那 OK, 没看过就丢. 这是 expected (用户希望默认隐藏).

---

## 参考

- Design doc: [2026-05-18-chat-repl-prompt-toolkit-design.md](2026-05-18-chat-repl-prompt-toolkit-design.md)
- 当前 chat REPL: [src/explain_engine/cli.py:945-1046](../../src/explain_engine/cli.py#L945) (Wave 4 改)
- DEFAULT_COMMANDS source: [src/explain_engine/chat/slash_commands.py](../../src/explain_engine/chat/slash_commands.py)
- prompt_toolkit docs: https://python-prompt-toolkit.readthedocs.io/en/master/
- prompt_toolkit `patch_stdout`: https://python-prompt-toolkit.readthedocs.io/en/master/pages/asking_for_input.html#prompt-in-an-asyncio-application
