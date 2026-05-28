# Phase 20 P0 — chat NL 半截 stall + ctrl+c 失灵 + 滚动失灵 设计

## Problem statement

Phase 19 真终端 smoke (user 2026-05-28 上午):

1. `uv run explain` → splash → `/resume <sid>` 进 ChatSession (`s_d05a00a4`).
2. 输入自然语言问题 ("解释一下六十四卦是如何区分四季和吉凶的").
3. LLM 输出半截 markdown ("...## 二、六十四卦如何区分四季") 之后**输出停止**.
4. **slash 命令仍 work** (Footer 滚动条变化证明 textual event loop 没死).
5. **Ctrl+C 无效** — app 不退.
6. **PgUp/PgDn 上下滚动失灵** — 看不到上面被覆盖的输出.

## 根因 (第一性原理)

### 根因 1: AsyncAnthropic / AsyncOpenAI 无 timeout 配置

`llm/anthropic_protocol.py:113` — `self._client = AsyncAnthropic(**kwargs)`, kwargs 只含 `api_key` + 可选 `base_url`, **不传 `timeout=`**.
`llm/openai_protocol.py:53` — `self._client = AsyncOpenAI(**kwargs)`, 同款.

anthropic / openai SDK default `timeout=600s` 是 connect + total request timeout,
但 streaming API 的 **chunk gap timeout (无 chunk N 秒后超时) 没有**.

DeepSeek API via anthropic-compatible endpoint 偶尔 silent drop stream (TCP 连接还活
但服务端不再推 chunk), `await stream.get_final_message()` 永远等. 这就是 user 看到的
"输出半截停了".

### 根因 2: textual ctrl+c → self.exit() 等 task 完成

`chat/tui_app.py:188` BINDINGS `Binding("ctrl+c", "quit_app", "退出")` → `action_quit_app` → `self.exit()`.

textual `App.exit()` 等 message pump drain (含 in-flight async task). chat handler async
task 卡在 `await stream.get_final_message()` 永不返, app 跟着卡 → user 感觉 "Ctrl+C 无效".

### 根因 3: mouse=False (5c3dfac) 副作用 wheel + Input focus 抢键

`enter_repl_async` 跑 `await app.run_async(mouse=False)` 关 mouse tracking 让 terminal selection 复原 (Phase 19 Bug A 修).
副作用: textual 收不到 mouse wheel event → VerticalScroll 不能滚.

剩 keyboard scroll 兜底, 但 `Input#prompt` widget focus 时, 默认 `PgUp/PgDn` 被 Input 用做 history navigation, **不传给 VerticalScroll#output**.
没有 keyboard scroll 路径.

## 设计

### Layer A — SDK timeout

**Files**: `llm/anthropic_protocol.py`, `llm/openai_protocol.py`, `config.py`.

**改动**:

1. `AnthropicProtocolClient.__init__` 加 `read_timeout: float = 120.0` param. `AsyncAnthropic` kwargs 加:
   ```python
   import httpx
   kwargs["timeout"] = httpx.Timeout(
       connect=10.0, read=read_timeout, write=60.0, pool=None
   )
   ```
   `read=120` 是 chunk gap timeout — 无 chunk 120s 抛 `APITimeoutError`.

2. `OpenAIProtocolClient.__init__` 同款 `read_timeout` param + `httpx.Timeout`.

3. `config.make_llm_client` / `make_light_llm_client` 读 env `LLM_READ_TIMEOUT_S` (default 120.0):
   ```python
   read_timeout = float(os.environ.get("LLM_READ_TIMEOUT_S", "120.0"))
   client = AnthropicProtocolClient(..., read_timeout=read_timeout)
   ```

**Why 120s**:
- 短到能在 user UX 容忍内 (2min 已经够长)
- 长到不误杀 extended thinking (默 4096 token budget, 实测 <60s 完)
- env knob 留逃生口

**Error path**: APITimeoutError 被现有 `except (APIConnectionError, APITimeoutError, RateLimitError, APIError)` (anthropic_protocol.py:168) 接住 → 走现有 retry 逻辑. user 看到 retry 或 LLMError 抛, 不再 hang.

### Layer B — esc cancel chat task

**File**: `chat/tui_app.py`.

**改动**:

1. `ExplainChatApp.__init__` 加:
   ```python
   self._chat_task: asyncio.Task | None = None
   ```

2. `_handle_input_submitted` (line 426) 包成 task 跑:
   ```python
   async def _consume_chat_events(text: str) -> None:
       try:
           async for ev in self.chat.handle_user_input(text, self.llm):
               await self._render_event(ev)
       except asyncio.CancelledError:
           await self._unmount_status()  # 兜底清 spinner
           await self._write_styled("", "请求已取消.", suffix_style="dim")
           raise  # 让 task 真正 cancelled state
       except Exception as exc:
           await self._write_styled(
               "[red]chat 失败: [/red]",
               f"{type(exc).__name__}: {exc}",
               suffix_style="red",
           )

   self._chat_task = asyncio.create_task(_consume_chat_events(text))
   try:
       await self._chat_task
   finally:
       self._chat_task = None
   ```

3. BINDINGS 加 `Binding("escape", "cancel_chat", "取消")`.

4. `action_cancel_chat()`:
   ```python
   async def action_cancel_chat(self) -> None:
       if self._chat_task is None or self._chat_task.done():
           await self._write_styled(
               "", "(无 in-flight 请求可取消)", suffix_style="dim"
           )
           return
       self._chat_task.cancel()
       # CancelledError 在 _consume_chat_events 内被 catch, 已 mount "请求已取消"
   ```

**Why escape**:
- 跟 SessionPickerScreen 的 escape=cancel 语义一致
- 跟 ctrl+c=quit_app 语义分离 (cancel vs quit)
- terminal Esc 不被任何系统快捷键占
- Input widget 默 Esc 是 blur — 我们 app-level binding 优先, 不冲突 (textual binding 解析顺序: app > screen > focused widget)

**Edge cases**:
- 已 done task: `task.cancel()` no-op
- task ref 在 finally 清: 防 stale ref
- spinner widget 已 unmount 时 `_unmount_status` idempotent (line 689-693 注释保证)

### Layer C — keyboard scroll

**File**: `chat/tui_app.py`.

**改动**:

1. BINDINGS 加:
   ```python
   Binding("pageup", "scroll_output_up", "上翻"),
   Binding("pagedown", "scroll_output_down", "下翻"),
   ```

2. Actions:
   ```python
   def action_scroll_output_up(self) -> None:
       self.query_one("#output", VerticalScroll).scroll_page_up()

   def action_scroll_output_down(self) -> None:
       self.query_one("#output", VerticalScroll).scroll_page_down()
   ```

**Out of scope (留 follow-up)**:
- `ctrl+home/end` 跳首末 (低优先 — PgUp/Down 多按几下也能到)
- `up/down` 行级滚动 (Input 已用做 history, 冲突)
- VerticalScroll widget cap LRU evict (user 开局 follow-up #6, 留 Phase 20.1+)

## TDD 计划

3 layer 各独立 commit. red → green → ruff → 单元测 pass → commit.

### Layer A tests (`tests/test_llm_timeout_config.py`)

1. `test_make_llm_client_passes_read_timeout_default` — env 没 `LLM_READ_TIMEOUT_S`, 验 AnthropicProtocolClient 收 `read_timeout=120.0`.
2. `test_make_llm_client_env_override` — env `LLM_READ_TIMEOUT_S=30`, 验收 `read_timeout=30.0`.
3. `test_async_anthropic_kwargs_include_timeout` — mock `AsyncAnthropic`, 验 init kwargs 含 `timeout=httpx.Timeout(read=120, ...)`.
4. `test_async_openai_kwargs_include_timeout` — 同款 OpenAI.

### Layer B tests (`tests/test_tui_app_cancel_chat.py`)

Test infra: 用 textual's `App.run_test()` async context manager.

1. `test_escape_cancels_inflight_task` — mount app, mock `chat.handle_user_input` 是 async gen 永等 `asyncio.sleep(99)`, submit input → wait task 启动 → press_keys("escape") → 验 `_chat_task.cancelled()` is True + 屏 mount "请求已取消" + spinner widget 不在.
2. `test_escape_no_inflight_task_message` — 直接 press_keys("escape"), 无 task, 验屏 mount "(无 in-flight 请求可取消)".
3. `test_chat_task_normal_completion_clears_ref` — chat handler 正常完成, 验 `_chat_task is None` 之后.

### Layer C tests (`tests/test_tui_app_keyboard_scroll.py`)

1. `test_pageup_scrolls_output_up` — mount app + mock mount 50 widget (强制滚动条 active), press_keys("pageup"), 验 `VerticalScroll#output.scroll_offset.y` 减 (或 scroll_page_up 被调).
2. `test_pagedown_scrolls_output_down` — 同款 down.

## Risk / 兜底

1. **textual `App.run_test()` infra**: tests/test_tui_app.py 已用过, 装好的. Layer B/C test 跟前 Phase 19 test 同 pattern.
2. **httpx version compat**: `httpx.Timeout(pool=None)` 是 httpx 0.24+ API. pyproject 已 pin httpx ≥0.25, 安全.
3. **anthropic SDK retry**: SDK 自己有 2 次 retry, 加上 timeout 一起总 ~6min worst case. user 体验仍能容忍 (有 esc 兜底).
4. **回归现 quit_app test**: ctrl+c 行为不变, 只是 escape 多了一条 binding. 现 ctrl+c 退出 test 不影响.
5. **CancelledError propagation**: `_consume_chat_events` catch CancelledError 后 re-raise 让 task 真 cancelled. asyncio 标准 pattern.

## Out of scope (Phase 20.0)

- VerticalScroll widget cap LRU evict (user follow-up #6) — Phase 20.1+
- textual non-TTY guard (user follow-up #7) — Phase 20.1+
- 小型 UX 套餐 #4 (theory cache label 改名) + #5 (LLM_SPLASH_PAUSE_S env knob) + #8 (picker sort) — P0 修完回去做 (task #4)
- chat_with_tools thinking blocks streaming 实时 yield (user follow-up #3) — Phase 20.2+

## Implementation 顺序

1. **commit 1** (Layer A): SDK timeout + env knob + 4 test
2. **commit 2** (Layer B): esc cancel + task ref + 3 test
3. **commit 3** (Layer C): PgUp/PgDn scroll + 2 test
4. **commit 4** (acceptance doc): `docs/plans/2026-05-28-phase-20-p0-acceptance.md` 总结 3 layer 现象 / before / after / TDD evidence + 真终端 smoke 通过验证

每 commit 独立, 任何一层 reject 不影响其他.

## 关联

- Phase 19 Bug A (5c3dfac): mouse=False 的副作用 Layer C 修
- Phase 19 Bug C (fb2563b): _spinner helper, Layer B 的 `_unmount_status` 兜底依赖它
- Phase 19 Wave 7 hotfix (9838507): /resume picker 死锁同款 textual stdin hold 风险, 本 phase 不再 regress
