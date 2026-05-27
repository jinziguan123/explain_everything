# Phase 19 Design — textual chat REPL + reasoning_content + UX 三连击

**Date**: 2026-05-27
**Status**: design approved, ready for plan
**Trigger**: 用户提 3 UX 改进:
1. Ctrl+O 展开/折叠 LLM 思考内容 (= reasoning_content / extended thinking)
2. 用户输入完成后 spinner 表达"系统处理中"(现 /deepen + 普通 chat 无提示)
3. 启动 TUI splash 图标丝滑过渡

## 设计哲学

Phase 18 修了"机械化 bootstrap"过度建模, Phase 19 修"用户视觉/交互体验". 三个 request 共同点: chat REPL **render 层**升级.

- request 1 (思考折叠) 涉 LLM provider 层 + render 层 + 交互层 (键盘 binding)
- request 2 (spinner) 涉 chat event 层 + render 层
- request 3 (splash) 涉 启动流程 + render 层

render 层是共同瓶颈 — 现 Rich `console.print` 流式输出 + prompt_toolkit input 组合**无法支持事后折叠**. 必须换 full-screen TUI framework. user 选 textual (Will McGugan / Rich 作者出品).

设计决策 (来自 brainstorm 3 轮 AskUserQuestion):
- D1: TUI 框架 = textual (vs Rich Live + KeyBindings 轻量 / vs urwid 老牌)
- D2: 思考内容 = reasoning_content / extended thinking (DeepSeek-R1 `reasoning_content` field / Claude `thinking` block)
- D3: thinking 触发 = 默 enable, env `LLM_THINKING_DISABLED=1` 可关

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 19: textual chat REPL + reasoning_content + UX 三连击 │
└─────────────────────────────────────────────────────────────┘

层级 1: LLM client (provider 层)
  Response 加 reasoning: str | None
  anthropic_protocol.py chat path 暴露 thinking blocks (现仅 tools path 暴露)
  openai_protocol.py 加 reasoning_content 解析 (DeepSeek-R1 / openai-compat 路径)
  默 enable, LLM_THINKING_DISABLED=1 env 可关

层级 2: ChatEvent 协议 (chat layer)
  新增 thinking_text / status_start / status_end 3 个 event type
  ephemeral.handle_user_input / ChatSession query_loop 都 yield

层级 3: chat REPL render (textual TUI)
  替换 repl_entry.enter_repl_async + repl_input.py
  textual App scaffolding: Input widget + RichLog widget + Collapsible widget
  ChatEvent → push to RichLog. thinking_text → Collapsible 包装 (默 expand, dim color)
  Ctrl+O binding 切所有 Collapsible.collapsed
  slash /thinking on|off 也能控

层级 4: UX polish
  spinner: status_start/end event → textual LoadingIndicator mount/unmount
  splash: textual SplashScreen 启动显示 ASCII logo + 4 init step 渐进点亮

不动 (backward compat):
  - bootstrap_phenomena / classify / 4 yaml (Phase 17.2)
  - SessionStore / StorageV2 / lexicon / theories (Phase 17.1 + 16)
  - cli explain new <Q> 仍 batch mode (跟 Phase 18 一致)
  - dispatch_slash / DEFAULT_COMMANDS / 24 个 _handle_* slash handler 全保留
```

---

## Section 1: LLM client 改 (reasoning_content 3 层)

### 1.1 Response 加 reasoning field

`src/explain_engine/llm/client.py`:

```python
class Response(BaseModel):
    text: str
    reasoning: str | None = None  # 新加 — Phase 19
    parsed: dict[str, Any] | None
    model: str
    usage: dict[str, int]
```

backward compat: default None, 老 caller (Phase 17.2/18 的 bootstrap classify 等) 完全不感知.

### 1.2 anthropic_protocol.py: chat path 暴露 thinking blocks

现 `chat_with_tools` 路径 (anthropic_protocol.py:299-310) 已收集 thinking blocks 到 `ToolsResponse.raw_content_blocks`. 普通 `chat()` 路径需对称: stream loop 遇到 `block.type == "thinking"` 时 accumulate 到 `reasoning_text`, 完成后填 Response.reasoning.

call_kwargs 加 `thinking={"type": "enabled", "budget_tokens": 4096}` (默 enable, env disable 时 omit).

### 1.3 openai_protocol.py: reasoning_content 解析

`chat()` 内 chat.completions.create 之后:
```python
msg = resp.choices[0].message
text = msg.content
reasoning = getattr(msg, "reasoning_content", None)  # DeepSeek-R1 / 兼容 endpoint
```

填到 Response.reasoning. 对 gpt-4o 这种不返 reasoning_content 的 model, 字段是 None 自然.

### 1.4 env 控

`config.py` make_llm_client / make_light_llm_client:
```python
thinking_disabled = os.environ.get("LLM_THINKING_DISABLED", "").lower() in ("1", "true", "yes")
```
provider 构造时传 `enable_thinking=not thinking_disabled`. anthropic 内部 if disabled 不加 `thinking={}` param. openai 不变 (reasoning_content 是 vendor 返字段, 客户端只能消费不能要求 disable; LLM_THINKING_DISABLED 仅 affect 是否填 Response.reasoning, 不真省 vendor token).

### 1.5 Testing

- `test_response_reasoning_field`: backward compat (老 Response 不传 reasoning, default None)
- `test_anthropic_chat_extracts_thinking_blocks`: mock stream 含 thinking block, 验 resp.reasoning 填了
- `test_openai_chat_extracts_reasoning_content`: mock DeepSeek-R1 完整 response 含 reasoning_content
- `test_make_llm_client_thinking_disabled_env`: LLM_THINKING_DISABLED=1 → anthropic 不传 thinking 参
- 现 ~30 个 LLM client test 零回归 (chat()/chat_with_tools 默 default 行为)

---

## Section 2: ChatEvent 新增 3 个 type

```python
# chat/session.py ChatEvent docstring 加:
- thinking_text: str (LLM reasoning 段, 渲染时用 Collapsible 包装 dim 显示)
  Producer: ephemeral.handle_user_input / ChatSession.handle_user_input.
  Consumer: textual app 内 _render_event 走 Collapsible mount.
- status_start: str (描述 "思考中..." / "启动深度建模 — classify 中...")
  Producer: handle_user_input 调 LLM 前. _handle_deepen 调 promote 前.
  Consumer: textual app mount LoadingIndicator + 灰 text.
- status_end: None (清掉前一个 status_start 显示)
  Producer: 对应 LLM call 完成 / promote 完成. Consumer: unmount LoadingIndicator.
```

`ephemeral.handle_user_input` (Phase 18 落地) 改:
```python
async def handle_user_input(self, text, llm):
    yield ChatEvent(type="status_start", content="思考中...")
    try:
        resp = await llm.chat(messages)
    except (LLMError,) as exc:
        yield ChatEvent(type="status_end")
        yield ChatEvent(type="slash_error", content=...)
        return
    yield ChatEvent(type="status_end")
    if resp.reasoning:
        yield ChatEvent(type="thinking_text", content=resp.reasoning)
    yield ChatEvent(type="assistant_text", content=resp.text)
    yield ChatEvent(type="turn_complete", content=None)
```

`ChatSession.handle_user_input` (现 Phase 9 query_loop) 同模式: 头尾 status_start/end, 中间遇 resp.reasoning yield thinking_text.

`_handle_deepen` (Phase 18 slash) 改:
```python
yield ChatEvent(type="status_start", content="启动深度建模 — classify 中...")
try:
    real_chat = await chat.promote_to_persistent(question, llm)
except ... :
    yield ChatEvent(type="status_end")
    ...
yield ChatEvent(type="status_end")
yield ChatEvent(type="slash_deepen_promoted", ...)
```

---

## Section 3: textual chat REPL 重写

### 3.1 新文件 `chat/tui_app.py`

```python
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header, Footer, Input, RichLog, Collapsible
from textual.containers import VerticalScroll

class ExplainChatApp(App):
    BINDINGS = [
        Binding("ctrl+o", "toggle_thinking", "折叠/展开 thinking"),
        Binding("ctrl+c", "quit_app", "退出"),
        Binding("ctrl+l", "clear_log", "清屏"),
    ]
    CSS_PATH = "tui_app.tcss"

    def __init__(self, llm, light_llm, ephemeral_chat):
        super().__init__()
        self.llm = llm
        self.light_llm = light_llm
        self.chat = ephemeral_chat
        self._thinking_visible = True

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="output", wrap=True, highlight=False, markup=True)
        yield Input(id="prompt", placeholder="问点什么... (/help, Ctrl+O 折叠 thinking)")
        yield Footer()

    @on(Input.Submitted)
    async def handle_input(self, event):
        text = event.value
        event.input.value = ""
        if self.chat.is_slash_command(text) if hasattr(self.chat, 'is_slash_command') else text.startswith("/"):
            from explain_engine.chat.slash_commands import dispatch_slash
            for ev in await dispatch_slash(self.chat, text):
                await self._render_event(ev)
            return
        # 自然语言
        async for ev in self.chat.handle_user_input(text, self.llm):
            await self._render_event(ev)

    async def _render_event(self, ev):
        log = self.query_one("#output", RichLog)
        if ev.type == "assistant_text":
            log.write(ev.content or "")
        elif ev.type == "thinking_text":
            await self._mount_thinking_section(ev.content)
        elif ev.type == "status_start":
            self._mount_status(ev.content)
        elif ev.type == "status_end":
            self._unmount_status()
        elif ev.type == "slash_deepen_promoted":
            sid = ev.metadata["sid"]
            await self._switch_to_chat_session(sid)
        elif ev.type == "slash_reset_to_ephemeral":
            await self._reset_to_ephemeral()
        elif ev.type == "slash_quit":
            self.exit()
        else:
            log.write(f"[dim]{ev.type}: {ev.content}[/dim]")

    def action_toggle_thinking(self):
        self._thinking_visible = not self._thinking_visible
        for c in self.query(Collapsible):
            c.collapsed = not self._thinking_visible
```

### 3.2 替换 `chat/repl_input.py`

prompt_toolkit → textual:

| 现 prompt_toolkit | textual 对应 |
|---|---|
| `PromptSession.prompt_async()` | `Input` widget + `Input.Submitted` event |
| `Completer` (slash 自动补全) | textual `Suggester` interface |
| `KeyBindings` | `App.BINDINGS` |
| `patch_stdout` | textual 天然分区 (Input 独立 widget) |
| input history (上下方向键) | `Input` 加 `history` field |
| message_dialog (HITL 确认) | `App.push_screen(ModalScreen)` |

### 3.3 `enter_repl_async` 替换

`chat/repl_entry.py` 重写:
```python
async def enter_repl_async(llm, light_llm) -> None:
    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=llm, ...)
    app = ExplainChatApp(llm=llm, light_llm=light_llm, ephemeral_chat=ephemeral)
    await app.run_async()
```

outer loop 逻辑 (slash dispatch / chat var 切换 / /quit) 移进 `tui_app.py` 内.

### 3.4 thinking section UI

```
┌─ ▼ thinking (250 token) ──────────────┐
│  让我先想一下用户问的"为什么烧水能沸". │
│  这是个热力学问题, 关键是水的饱和蒸汽压 │
│  跟环境大气压的关系. 100°C 时蒸汽压...   │
└────────────────────────────────────────┘
水沸腾的本质是水的饱和蒸汽压等于大气压. 在标准大气压下...
```

- thinking section 用 `Collapsible(title="▼ thinking (N token)", collapsed=False)` 包装
- dim gray 文字 (CSS `text-style: dim`)
- 默 expand, Ctrl+O 切所有 collapse 状态
- LLM stream 期间 incremental append; stream 完后冻结, Ctrl+O 仍可切

### 3.5 Test 影响 audit

现 ~10 个 test 涉 chat REPL:
- `test_repl_entry*.py` (~3 test, 直接 mock console.print + Input) → **重写 textual.pilot fixture**
- `test_cli_chat.py` (cli `explain chat <sid>` 启 REPL) → **改 mock textual app**
- `test_repl_history.py` (上下方向键历史) → **textual Input history**
- `test_chat_session.py` 等 ~40 个 (测 handle_user_input event yield) → **零修改** (event 协议不变)

估真 break: 5-10 个 test 需 rewrite (textual.pilot fixture). 40+ 个 chat session test 不动.

### 3.6 input_provider 兼容

`ephemeral_input_provider` (现 input_provider Callable[[str], Awaitable[str]]) 在 HITL k/e/d (review_phenomena_async) 用. textual 下用 `App.push_screen(ModalScreen)` 实现 — 接口对外不变, textual 内部用 modal 实现.

---

## Section 4: UX polish (spinner + splash)

### 4.1 Spinner (Section 2 yield 的 status_start/end render)

textual `LoadingIndicator` widget. `_render_event` 接到:
- `status_start(text)` → query #output → mount `LoadingIndicator` + 灰 text. textual 内建 spinner Unicode 转圈.
- `status_end` → unmount.

ephemeral / ChatSession / _handle_deepen 都 yield 这俩, 复用同款 widget.

不动现 cli `Console.status()` (cli batch mode 不走 TUI).

### 4.2 Splash — 启动 loading screen

`tui_app.py` 启动 `ExplainChatApp.on_mount` 前先 push `SplashScreen`:

```
        ┌──────────────────────────────────┐
        │                                  │
        │   ███████ ██   ██ ██████  ██     │
        │   ██       ██ ██  ██   ██ ██     │
        │   █████     ███   ██████  ██     │
        │   ██       ██ ██  ██      ██     │
        │   ███████ ██   ██ ██      ██     │
        │                                  │
        │       Explain Engine v0.18       │
        │                                  │
        │   ✓ 加载 lexicon (218 var)        │
        │   ✓ 连接 PG (172.30.26.12)        │
        │   ⠋ 加载 theory cache...           │
        │   · 启动 chat REPL                │
        │                                  │
        └──────────────────────────────────┘
```

实现:
- ASCII art logo: `pyfiglet` lib (新依赖, 1 行调用) 生成 "Explain" 字样, font="standard" or "slant"
- 4 个 init step 渐进点亮:
  1. `init_lexicon_backend()` (现 startup 调)
  2. PG ping (`EXPLAIN_DB_URL` 健康检查)
  3. `get_active_theories()` cache load
  4. `chat REPL` ready signal
- 每 step 完用 `✓` checkmark + 灰文字, 当前 step 用 textual spinner `⠋`
- 全 step done 1s 后 `App.pop_screen` 进 chat REPL

textual `Worker` API 并发跑 4 个 init step, 渐进显示. 总时间 ≈ max(step) 不是 sum.

加 `--no-splash` CLI flag 跳 (CI / 测试用).

### 4.3 Visual style

CSS (`tui_app.tcss`):
```css
Screen { background: $surface; }
RichLog#output { border: solid $primary; padding: 1; }
Input#prompt { dock: bottom; }
Collapsible.thinking { background: $surface-darken-2; }
Collapsible.thinking > .collapsible--title { color: $text-muted; text-style: dim italic; }
/* 注: height: 1 必要 — textual LoadingIndicator DEFAULT_CSS height 100% 会撑满 VerticalScroll, override 让 indicator 占 1 行高. */
LoadingIndicator { color: $accent; height: 1; }
.splash-logo { color: $accent; text-align: center; }
.splash-step-done { color: $success; }
.splash-step-pending { color: $text-muted; }
```

### 4.4 Testing

- `test_tui_spinner_start_end_events`: 模拟 ChatSession yield status_start/end, 验 textual app mount/unmount LoadingIndicator (用 `App.pilot()`)
- `test_tui_splash_renders_logo_and_init_steps`: pilot 启动 app, 验 SplashScreen 显示 + 4 step 都 transition done
- `test_tui_splash_pops_to_chat_repl`: 4 step done 后 1s, 验 current screen = chat (not splash)
- `test_tui_thinking_collapsible_default_expand`: mount thinking_text event, 验 Collapsible.collapsed == False
- `test_tui_ctrl_o_toggles_all_thinking`: pilot press Ctrl+O, 验所有 Collapsible.collapsed flip

---

## Section 5: backward compat + 已知 trade-off

### 5.1 backward compat 保证 (硬约束)

| 区域 | Phase 19 行为 |
|---|---|
| cli `explain new <Q>` | **完全不变** — batch mode, 不走 TUI |
| cli `explain compress / run / predict / counterfactual / rescore / list / lexicon / theories / delete / migrate-lexicon-pg` | **不变** — batch subcommand, Rich Console |
| Phase 17.2 classify + 4 yaml + light_llm + lexicon delete | **全保留**, TUI 不动 bootstrap pipeline |
| Phase 18 ephemeral chat + /deepen pipeline | **全保留**, 只换 render 层 (Rich → textual) |
| `ChatEvent` dataclass + dispatch_slash + 24 个 slash handler | **全保留**, 只加 3 个新 event type |
| `LLMClient.chat()` Response.text/parsed/model/usage | **全保留**, 新加 reasoning field (default None) |
| Phase 17.1 PG lexicon / Phase 16 theory cache / SessionStore / StorageV2 | **完全不动** |
| `make_llm_client / make_light_llm_client` | enable_thinking 参数 optional (default True) |
| `LLM_THINKING_DISABLED` env | 新 opt-out, 默 enable |
| `ephemeral.handle_user_input` / `ChatSession.handle_user_input` 现有 event | 不动现 yield, 仅头尾加 status, 中间可能加 thinking_text |

### 5.2 已知 trade-off (Phase 19 接受)

- **chat REPL 加 textual + pyfiglet 重依赖** — `uv sync` 多拉 ~5MB.
- **prompt_toolkit 仍保留** — typer.confirm + cli 个别 input 用. chat REPL 不依赖. 待 Phase 20+ 视情况删.
- **textual 在 non-TTY 环境会 fail** — CI / docker logs / non-interactive shell 跑 `uv run explain` 挂. cli batch subcommand 不挂. 接受.
- **不实装真"单条 thinking 折叠"** — Ctrl+O 切**所有** thinking 一起. 单条切要鼠标 click (textual Collapsible 内建).
- **splash 4 step 渐进, 总时间 max(step)** — 可能 500-1000ms. `--no-splash` 跳过.
- **现 ~5-10 个 chat REPL test 需 rewrite** — textual.pilot fixture.
- **HITL k/e/d 用 textual ModalScreen** — interface 不变, 内部从 prompt_toolkit message_dialog 换 textual modal.

### 5.3 实施顺序

| Wave | Scope | 估 task | 估 commit |
|---|---|---|---|
| 1 | LLM client reasoning (Response field + anthropic thinking + openai reasoning_content + env disable) | ~6 | ~6 |
| 2 | ChatEvent 新增 3 type + ephemeral/ChatSession.handle_user_input yield + _handle_deepen yield | ~5 | ~5 |
| 3 | textual app 基础 (App / Input / RichLog / 路由 ChatEvent → render + replace repl_entry) | ~7 | ~7 |
| 4 | Collapsible thinking + Ctrl+O 切换 + slash /thinking on\|off | ~5 | ~5 |
| 5 | spinner (LoadingIndicator + status_start/end render) + 修补现 chat test | ~5 | ~5 |
| 6 | splash screen (ASCII logo + 4 init step 渐进 + --no-splash flag) | ~5 | ~5 |
| 7 | 全量 pytest 零回归 + ruff + 4 smoke + acceptance doc | ~4 | ~3 |

总 **~37 task / 7 wave**.

### 5.4 已知 follow-up (Phase 20+)

- 真"单条 thinking 折叠" (textual TreeControl 实现键盘单条 nav)
- prompt_toolkit 完全移除 (typer.confirm 改 textual modal)
- streaming thinking 期间也 Ctrl+O 实时切 (现是 stream 完成才能切)
- splash 步骤可定制 (user 加自己 init hook)

---

## Smoke 验收 (Phase 19 完后)

```bash
# Smoke 1: chat 默 thinking 显示
uv run explain
# 期望: splash 显示 1-2s → chat REPL. 问 "为什么烧水能沸"
# → "思考中..." spinner → thinking section (dim, expand 默) → answer text.

# Smoke 2: Ctrl+O 折叠
Ctrl+O
# 期望: thinking section 都缩成 ▶ 一行. 再 Ctrl+O 展开 ▼.

# Smoke 3: slash /thinking off
> /thinking off
# 期望: 后续 chat 不渲染 thinking_text event. /thinking on 恢复.

# Smoke 4: env disable
LLM_THINKING_DISABLED=1 uv run explain
# 期望: chat 不出现 thinking section (Response.reasoning is None).
```

---

## 已知不破坏的状态

- Phase 18 全 wave 落地, 1310 PASS — Phase 19 同基线
- Phase 17.2 classify + 4 yaml + light_llm + delete 全保留
- cli explain new/compress/run/predict 等 batch subcommand 不变
- lexicon / theories / PG schema 不动
