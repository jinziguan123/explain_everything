# chat REPL prompt_toolkit 升级 — Design

> 上一 design: [chat-new-resume-slash](2026-05-18-chat-new-resume-slash-design.md)
> 当前 HEAD: `76add14` — Phase 9 + chat /new + /resume 已 land

**日期**: 2026-05-18
**分支**: `dev`

---

## 0. TL;DR

chat REPL 升级到 `prompt_toolkit`, 一次解决 3 个用户 raised UX issue:

1. **log 撞 prompt + 删除残影** — 用户输入行被 LLM HTTP log / session_memory_writer log 同行覆盖, 删字符后留视觉残影
2. **LLM 交互 log 默认隐藏 + 灰色 + 快捷键展开** — log 默认不显示, ctrl+o toggle 一个 popup 看历史 log, 灰色样式
3. **slash 自动联想** — 输 `/` 弹出下拉菜单列 8 个 slash command

加 1 个 dep (`prompt_toolkit>=3.0`), 新建 1 个 module (`chat/repl_input.py`), 改 `cli._run_chat_repl_async` 把 `asyncio.to_thread(input, ...)` 换成 `await read_input(chat)`. chat 模式期间 swap logging handler 把所有 log routed 到 in-memory buffer.

预估改动: ~250-350 行新代码 (含测试).

---

## 1. 背景与动机

### 1.1 当前 chat REPL 实现

`src/explain_engine/cli.py:945-1046` 的 `_run_chat_repl_async` 用:

```python
user_input = await asyncio.to_thread(input, "\n> ")
```

读 user 输入. 配合 `cli.py:20` 的 `import readline  # noqa: F401` side-effect 启用 line editing.

`cli.py:46-49` 的 `logging.basicConfig(level=INFO, format="%(message)s")` 让所有 logger.info / logger.warning 直接打 stdout.

### 1.2 3 个用户报的 UX 问题

#### 问题 #1: log 撞 prompt + 删除残影

用户输入 `> ` 等命令时, async background task (HTTP request log / session_memory_writer log) 直接写 stdout, 覆盖在 user 编辑行上. backspace 字符后, terminal 不会重绘 log 部分 (因为 readline 只管它的 input line), 留视觉残影.

根因: stdout 是 process-wide 共享 fd, readline 假设 stdout 只由它写.

#### 问题 #2: log 默认显示 + 无样式

session_memory_writer log "wrote 1872 chars to memory.md for s_xxx at turn 10" + httpx log "HTTP Request: POST ... HTTP/1.1 200 OK" 默认 INFO level 全显示, 用户调试时有用 / 平时是 noise. 用户希望:
- 默认隐藏
- 灰色样式
- 快捷键 (ctrl+o) toggle 展开/收起

#### 问题 #3: slash 命令无自动联想

输 `/` 后用户需要记忆 8 个 command name (quit/help/show/budget/compact/save/new/resume). 没有 autocomplete 弹出.

### 1.3 为什么 readline 不够

Python `readline` 是 1980s-era stdin line editor:
- 只管自己的 input line, 不感知 async stdout
- KeyBindings 是全局 `set_completer` 接口, 不能 dynamic toggle UI
- 弹出菜单不支持 — `set_completer` 只能 inline tab-cycle
- macOS libedit Unicode 不完美 (Phase 9 时已撞过)

`cli.py:19` 注释自己已经说: "如果还有问题, 后续可考虑 prompt_toolkit." 现在是后续.

### 1.4 为什么 prompt_toolkit

Python 业界标准 REPL framework, IPython / black / awscli / pgcli 都用. 原生支持:
- `patch_stdout()`: log 自动 routes 到 prompt 上方滚动区
- `KeyBindings`: dynamic 切换 UI 状态 (ctrl+o etc)
- `Completer`: 弹下拉菜单 + filter
- Multi-buffer + floating window
- Async-first (`prompt_async()`)
- ANSI/Style 完整支持

dep size ~300KB, mature project (since 2014).

---

## 2. Scope

### 2.1 本设计内

- 新依赖 `prompt_toolkit>=3.0` (加 pyproject.toml)
- 新模块 `src/explain_engine/chat/repl_input.py` 封装:
  - `read_input(chat) -> str` — 顶层入口
  - `SlashCompleter` — `/cmd` 自动联想
  - `BufferedLogHandler` — capped deque log buffer
  - KeyBindings (ctrl+o toggle log popup)
- `cli._run_chat_repl_async` 改用新 `read_input`
- chat 模式 enter/exit 时 swap logging handler (chat 期间 → buffer, 退出 → 装回 stdout)
- 单测 + manual smoke

### 2.2 本设计外 (YAGNI)

- log search / grep — 用户没要求
- log persistence to file — 用户没要求
- history search (ctrl+r) — 用户没要求, prompt_toolkit 默认有, 自动 free
- multi-line input — chat slash 都是 single-line, 不需要
- syntax highlighting — chat 不是 SQL/code editor
- mouse support — 不必

---

## 3. 总体方案

### 3.1 架构

```
cli._run_chat_repl_async (改造后)
  ↓ 进入 chat 模式
[swap logging handler: StreamHandler→BufferedLogHandler]
  ↓
while True:
  text = await read_input(chat_session)  ← NEW (替代 to_thread(input))
  ↓
  chat_session.handle_user_input(text, llm) 同前
  ↓
  ...switch / quit logic 同前
exit chat 模式
  ↓
[restore logging handler]
```

### 3.2 模块边界

```
src/explain_engine/chat/repl_input.py  (新)
├── BufferedLogHandler(logging.Handler)
│   └── 写 capped deque (200 行), prompt_toolkit Buffer 反应
├── SlashCompleter(Completer)
│   └── get_completions: 看 text_before_cursor 起头 "/", yield 8 个 cmd Completion
├── _make_session() → PromptSession
│   └── 包 SlashCompleter + KeyBindings + bottom_toolbar
├── _toggle_log_popup(app, log_buffer) → KeyBinding handler
└── async def read_input(chat) → str
    └── with patch_stdout(): return await session.prompt_async("\n> ")
```

`cli.py` 改动:
- 加 enter/exit chat 模式时的 handler swap (上下文管理器 or try/finally)
- 调 `read_input(chat_session)` 代替 `to_thread(input, ...)`

### 3.3 数据流

```
[logging stack]
session_memory_writer → logger.info → root logger
                                       ↓
                            chat mode 期间: BufferedLogHandler
                                       ↓
                            deque (cap 200)
                                       ↓
                            prompt_toolkit Buffer (subscribe deque)
                                       ↓
                            ctrl+o 时: 渲染到 floating popup

[stdin/stdout]
user 输入 → prompt_toolkit PromptSession.prompt_async
         ↓ (patch_stdout active)
         任何 print/write to stdout → buffered + replay 在 prompt 上方
                                      不再撞 prompt
```

---

## 4. 详细设计

### 4.1 BufferedLogHandler

```python
from collections import deque
import logging

class BufferedLogHandler(logging.Handler):
    """Cap-bounded in-memory log handler for chat REPL mode.

    Replaces stdout StreamHandler during chat mode so logs from
    httpx / session_memory_writer / 其他 logger.info 都被 buffer 而非
    直接打到 stdout (会撞 prompt_toolkit prompt).

    capacity: 200 line default. 老 line 自动 evict.
    listeners: 可选 callable list, 每次 append 调一次 (e.g. 通知
               prompt_toolkit Buffer refresh).
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
                    pass  # 防 listener bug 死循环
        except Exception:
            self.handleError(record)

    def add_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def get_text(self) -> str:
        return "\n".join(self.buffer)
```

### 4.2 SlashCompleter

```python
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

class SlashCompleter(Completer):
    """Autocomplete `/cmd` from DEFAULT_COMMANDS when text starts with '/'.

    不联想自然语言 — 仅 `/` 起 + 在第一 token 时活跃, 防 `/new 为什么 X`
    被错联想 (第二 token 起不再联想).
    """

    def get_completions(self, document: Document, complete_event):
        from explain_engine.chat.slash_commands import DEFAULT_COMMANDS

        text = document.text_before_cursor

        # 不以 / 起 → 不联想
        if not text.startswith("/"):
            return

        # 找第一 token
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            # 已输 / 后还有空格 + args → 不联想 cmd (e.g. /new 为什么)
            return

        current = parts[0][1:] if parts else ""  # strip leading /

        for cmd in DEFAULT_COMMANDS:
            if cmd.name.startswith(current):
                yield Completion(
                    text=cmd.name,
                    start_position=-len(current),
                    display=f"/{cmd.name}",
                    display_meta=cmd.description,
                )
```

### 4.3 PromptSession + KeyBindings

```python
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

_LOG_VISIBLE = False  # module-level flag

def _make_session(log_handler: BufferedLogHandler) -> PromptSession:
    kb = KeyBindings()

    @kb.add("c-o")
    def _toggle_log(event):
        """Toggle log overlay popup."""
        global _LOG_VISIBLE
        _LOG_VISIBLE = not _LOG_VISIBLE
        if _LOG_VISIBLE:
            # show popup with log_handler.get_text()
            # 实装见 4.4
            _show_log_popup(log_handler, event.app)
        # toggle off 由 popup 自己 ctrl+o handler 处理

    style = Style.from_dict({
        "log-line": "fg:#888888",  # 灰色 log
        "completion-menu.completion": "bg:#444444 fg:white",
        "completion-menu.completion.current": "bg:#888888 fg:white",
    })

    return PromptSession(
        completer=SlashCompleter(),
        key_bindings=kb,
        style=style,
        complete_while_typing=True,  # 输 / 时自动弹菜单 (不需 tab)
        bottom_toolbar=lambda: _bottom_toolbar_text(log_handler),
    )

def _bottom_toolbar_text(log_handler):
    n = len(log_handler.buffer)
    return f"[ctrl+o: log ({n} lines buffered)]"
```

### 4.4 Log popup 实现 (简化决定)

Design §E 提到 floating window 与 PromptSession 集成有 prompt_toolkit 内部 API 限制. 决定走 **simpler 方案**:

ctrl+o 触发时, **暂停 prompt + 切到 prompt_toolkit `pager`** 显示 log buffer 内容. Pager 支持 PgUp/PgDn 滚动, 任意键退出回 prompt.

```python
from prompt_toolkit.shortcuts import message_dialog
from prompt_toolkit.application.run_in_terminal import in_terminal

def _show_log_popup(log_handler, app):
    text = log_handler.get_text() or "(no log buffered)"
    # 用 run_in_terminal 暂停 prompt, 弹 dialog
    async def _async_popup():
        await message_dialog(
            title=f"Log buffer ({len(log_handler.buffer)} lines)",
            text=text,
            ok_text="Close",
        ).run_async()
    app.create_background_task(_async_popup())
```

(message_dialog 默认全屏 modal, 任意键 / Enter 关闭. 不复杂.)

**simpler 实装**: 用 `prompt_toolkit.print_formatted_text` + `patch_stdout` 临时输出 log 内容到 prompt 上方, 不切 dialog. ctrl+o 第一次 print, 第二次 print 空行清屏... 太脆弱, 选 dialog 路径.

### 4.5 cli.py handler swap

```python
# cli.py 顶 (现有 logging.basicConfig 不动, 作为 non-chat 模式默认)
logging.basicConfig(level=logging.INFO, format="%(message)s")

async def _run_chat_repl_async(...):
    from explain_engine.chat.repl_input import (
        BufferedLogHandler, read_input,
    )

    # ── chat 模式 enter: swap logging handler ──
    log_handler = BufferedLogHandler(capacity=200)
    log_handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    original_handlers = root.handlers[:]
    root.handlers = [log_handler]

    try:
        # ... 原 chat_session init logic ...
        while True:
            try:
                user_input = await read_input(chat_session, log_handler)
            except (EOFError, KeyboardInterrupt):
                ...
            # ... 原 dispatch logic 不变 ...
    finally:
        # ── chat 模式 exit: restore ──
        root.handlers = original_handlers
```

`read_input(chat, log_handler)` 签名: 接 chat (反正 chat.llm 不动它) + log_handler (popup 显示用), 返 str.

实际上 `read_input` 不需 chat 参数 — autocomplete 列 DEFAULT_COMMANDS 直接 import. 只传 log_handler 即可. 但接 chat 留 future (e.g. dynamic prompt based on chat.sid).

---

## 5. KeyBindings 总览

| 键 | 行为 |
|---|---|
| `ctrl+o` | toggle log popup |
| `ctrl+c` | KeyboardInterrupt (现有, cli REPL 主循环 except 兜底) |
| `ctrl+d` | EOF (同) |
| `enter` | submit input |
| `tab` | 在 completer 菜单内移动 (prompt_toolkit 默认) |
| `up / down` | history navigation (prompt_toolkit 默认, 跨 turn) |
| `alt+enter` | (留 future) multi-line input — chat 不需要 |

**concern**: prompt_toolkit emacs-mode 下 `ctrl+o` 默认是 `insert-newline-and-stay`. 我们覆盖 — chat 单行, 不需要. 如果 user 需要 multi-line, 用 `alt+enter` (vi 用户对此不熟, 但 chat 场景几乎不需 multi-line).

---

## 6. logging handler swap 细节

### 6.1 为什么 swap 而非加

直接给 root logger `addHandler(BufferedLogHandler)` 不行 — 现有 StreamHandler 还会打 stdout. 必须 remove + add. 也不能 `setLevel(WARNING)` 让 stdout 静默 — 我们要 INFO 到 buffer, 不是 silence.

### 6.2 chat 模式之外 logger 行为

`cli new / list / compress / run` 等非 chat 命令: `logging.basicConfig` 在 cli.py 顶部已 init, root logger 已有 StreamHandler. 这些命令进入时直接用 stdout log, 退出 process 后无所谓.

只有 `chat()` 命令进入 REPL 时 swap, 退出时 restore. swap 上下文管理器封装在 `_run_chat_repl_async` 的 try/finally.

### 6.3 third-party logger (httpx)

httpx INFO log "HTTP Request: POST ..." 是 httpx 内部的 `logger = logging.getLogger("httpx")` 走 root logger propagation. swap root handler 后, httpx log 自动 routed 到 BufferedLogHandler. 不必单独 patch httpx.

---

## 7. 测试 plan

### 7.1 单测可做

- `test_buffered_log_handler.py`:
  - capacity cap (push 250 line, 验 deque len == 200)
  - listener 通知 (subscribe + push → listener called)
  - get_text return 拼接

- `test_slash_completer.py`:
  - text `""` → 空联想
  - text `"/"` → 全 8 cmd
  - text `"/r"` → 仅 resume (filter 后)
  - text `"/new 为什么"` → 空 (第二 token 不联想)
  - mock DEFAULT_COMMANDS 验 description 在 display_meta

### 7.2 集成测难点

prompt_toolkit Application 真实 input 需 tty. CI 不友好.

务实方案:
- `test_cli_chat.py` 现有 surface test 保留 (CliRunner 验 cmd 存在 / flag accept)
- 不测真 prompt_toolkit input 流
- 留给手测 acceptance

### 7.3 手测 acceptance

`docs/plans/2026-05-18-chat-repl-prompt-toolkit-acceptance.md` (或加进 plan 末尾):
1. `explain chat <sid>` 进 REPL
2. 输 `/` 验弹出菜单含 8 个 cmd
3. 输 `/r` 验过滤到 resume
4. 输 `/new 为什么 X` 验不再联想 (第二 token)
5. 自然语言输入触发 LLM call, 期间 log buffer + bottom toolbar 显示 "N lines buffered"
6. ctrl+o 验弹 dialog 显示 log, 任意键关
7. backspace 中文字符验无残影 (Phase 9 readline bug 应被 prompt_toolkit 修)
8. 退出 chat (q 或 ctrl+d), 验 stdout log 恢复正常 (跑 `explain list` 不静默)

---

## 8. 风险与 open issues

### 8.1 prompt_toolkit dep 大小

~300KB python wheel + 间接 dep wcwidth. 项目已有 anthropic + openai + rich + typer + networkx 等重 dep, 加 prompt_toolkit 不显增. 接受.

### 8.2 macOS terminal 兼容

prompt_toolkit 在 macOS Terminal.app / iTerm / Alacritty / Kitty 都长期验证. 不可能因平台 break. 不担心.

### 8.3 ctrl+o 与现有 emacs binding 冲突

prompt_toolkit emacs-mode 默认 `ctrl+o` 是 `insert-newline-and-stay-in-place`. 我们覆盖之让它 toggle log popup. 用户 expectation 是 chat 单行 input, 不冲突. 如果未来要 multi-line, 用 `alt+enter`.

### 8.4 logging handler swap 期间, post-turn hook 抛错怎样

`ChatSession.handle_user_input` 内 post-turn hook (lifecycle/reflect/session_memory_writer) 走 logger.warning. swap 后到 BufferedLogHandler, popup 时能看到. exit chat 后 restore, 但此时 log 已 buffer 完, 不会重 print 到 stdout — 这是 expected (chat 模式日志归 chat, 不污染 cli 后续命令).

### 8.5 `_LOG_VISIBLE` module-level state

repl_input.py module-level `_LOG_VISIBLE` 不是 thread-safe. 但 chat REPL 单线程 (asyncio), 不会撞. 接受.

### 8.6 prompt_toolkit history persistence

prompt_toolkit `PromptSession(history=FileHistory(...))` 可以跨 chat session persist history. **YAGNI**: 不加, 用 default InMemoryHistory. 当前 chat session 内 up/down 翻历史够用. user 没要求跨 session history.

---

## 9. 决策摘要

| Q | A |
|---|---|
| 整体方案 | prompt_toolkit (推荐, 一站式) |
| Log overlay UI | `message_dialog` modal popup (任意键关). 不走 floating window (与 PromptSession API 集成复杂) |
| 快捷键 | ctrl+o (覆盖 emacs default newline-and-stay) |
| Log handler scope | chat 模式期间 swap, 退出 restore (try/finally). 非 chat 命令 stdout log 不动 |
| Completer 触发 | text 以 "/" 起 + 第一 token 内. 第二 token 起不联想 (防 `/new 为什么` 错触发) |
| Buffer 容量 | 200 line (deque maxlen) |
| History persistence | 默认 InMemoryHistory, 不持久化 (YAGNI) |

---

## 10. 落地顺序

预估 4 wave + 1 acceptance:

1. **Wave 1 — dep + BufferedLogHandler** (~1 task)
   - 加 prompt_toolkit dep 到 pyproject.toml
   - 新模块 chat/repl_input.py 加 BufferedLogHandler class
   - 单测 (capacity / listener)

2. **Wave 2 — SlashCompleter** (~1 task)
   - 加 SlashCompleter class
   - 单测 (空/`/`/`/r`/`/new 为什么`)

3. **Wave 3 — PromptSession 集成 + log popup** (~1 task)
   - `_make_session` factory
   - KeyBindings ctrl+o → message_dialog
   - bottom_toolbar
   - Style (灰色)
   - async def read_input(log_handler) -> str
   - 不写自动测 (prompt_toolkit Application 需 tty)

4. **Wave 4 — cli.py 集成 + handler swap** (~1 task)
   - `_run_chat_repl_async` 改用 `read_input`
   - try/finally 包 logging handler swap
   - 现有 5 个 cli_chat test 应仍 PASS (typer surface 不变)

5. **Wave 5 — acceptance smoke 文档** (~0.5 task)
   - 写 `docs/plans/2026-05-18-chat-repl-prompt-toolkit-acceptance.md` 列 8 个手测步骤
   - 留 user 跑

预估总: ~3-4 hour subagent 工作 + manual smoke.

---

## 11. 关联文档

- 上一 design: [chat /new + /resume slash design](2026-05-18-chat-new-resume-slash-design.md)
- 当前 chat REPL: [src/explain_engine/cli.py:945-1046](../../src/explain_engine/cli.py#L945) `_run_chat_repl_async`
- 当前 readline workaround: [src/explain_engine/cli.py:13-20](../../src/explain_engine/cli.py#L13)
- 当前 logging setup: [src/explain_engine/cli.py:46-49](../../src/explain_engine/cli.py#L46)
- session_memory_writer log source: [src/explain_engine/chat/hooks.py:200-206](../../src/explain_engine/chat/hooks.py#L200)
- prompt_toolkit docs: https://python-prompt-toolkit.readthedocs.io/
