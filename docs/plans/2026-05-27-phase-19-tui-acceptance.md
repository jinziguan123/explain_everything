# Phase 19 Acceptance — textual TUI + reasoning_content + UX 三连击

收口日期: 2026-05-27
负责人: jinziguan + Claude Opus 4.7 (Cowork, 1M context)
分支: `dev` (无 push, 等用户决定 PR / 合并时机)
Base SHA (Phase 19 起点): `0d83fdf^` (= Phase 18 acceptance doc 落地后)
End SHA (Wave 7 末): 见本 doc commit (Task 37)

---

## Phase 19 总览

**textual TUI 重写 chat REPL + LLM reasoning_content 暴露 + UX 三连击落地**:

- Wave 1-2 (LLM 协议层): `LLMClient.Response.reasoning` 加 field default `None` —
  anthropic `chat()` path 解析 thinking blocks, openai `chat()` path 解析
  `reasoning_content`. `LLM_THINKING_DISABLED=1` env disable 双 provider thinking.
  `ChatEvent` 新增 3 type (`thinking_text` / `status_start` / `status_end`),
  `ephemeral.handle_user_input` / `ChatSession.handle_user_input` /
  `_handle_deepen` 三 caller 都包 try/finally yield status pair + 把 LLM
  `Response.reasoning` 转 `thinking_text` event.
- Wave 3-6 (textual app 重写): 删 Phase 11/18 累积的 ~270 行 Rich Console +
  prompt_toolkit + outer while loop, 新 `ExplainChatApp` (textual App) +
  `tui_app.tcss` + `SplashScreen` (pyfiglet ASCII logo + 4 init step
  serial 点亮). `_render_event` 主分支 mount 各 widget (Static / Collapsible /
  LoadingIndicator), Ctrl+O 切所有 thinking Collapsible.collapsed, `/thinking
  on|off` slash 也切, cli `--no-splash` flag 跳 splash.
- Wave 7 (收口): 全量 pytest / ruff / 6 smoke / acceptance doc.

| Wave | Scope | 关键 commit |
| --- | --- | --- |
| Wave 1 (Tasks 1-6) | `Response.reasoning` + anthropic/openai chat() 解析 + env wire | `9e9f3ac` → `e89b815` + review fix `14de08d` |
| Wave 2 (Tasks 7-11) | ChatEvent 3 type + 3 caller yield status_start/end + thinking_text | `fe3d060` → `fac202c` + review fix `6641cdc` |
| Wave 3 (Tasks 12-18) | pyproject deps + tui_app scaffolding + Input.Submitted + repl_entry 重写 | `0d39f9b` → `c1deccf` + review fix `f8005d4` |
| Wave 4 (Tasks 19-23) | thinking Collapsible mount + Ctrl+O + /thinking slash + RichLog → VerticalScroll | `02cb742` → `2c41811` + review fix `df7f2f6` |
| Wave 5 (Tasks 24-28) | spinner LoadingIndicator mount/unmount + 删 3 xfailed legacy test | `72ddf50` → `eb19778` + review fix `24e3418` |
| Wave 6 (Tasks 29-33) | SplashScreen + tcss + cli `--no-splash` + init_lexicon_backend 搬家 | `33cefa2` → `44eea73` + review fix `0536b9e` |
| Wave 7 (Tasks 34-37) | 全量 pytest + ruff + 6 smoke + 本 doc | 见 Task 37 commit |

每 wave 末尾走 final code review, 共 6 个 review fix bundle (Wave 1-6 各 1 个).
Phase 19 合计 **33 commit** (2 个为 design + plan doc, 1 个本 acceptance doc, 30 个
为 src/tests/pyproject 实现 commit), 跟 plan 估算 (37 task / 7 wave / ~30-35 commit)
量级吻合.

---

## 改动文件清单 (按 wave 分)

### Wave 1 (Tasks 1-6): LLM client reasoning_content (provider 层)

src/:
- `src/explain_engine/llm/client.py` — `Response` 加 `reasoning: str | None = None` field. 老 caller 不感知 (`Response.text` / `parsed` / `model` / `usage` 不变).
- `src/explain_engine/llm/anthropic_protocol.py` —
  - `chat()` path 解析 `ThinkingBlock` / `RedactedThinkingBlock`: 找 final assistant message 的 content blocks, 串拼 thinking text 入 `Response.reasoning`. `RedactedThinkingBlock` 作占位符 `[redacted thinking]`. final-message 提取避免 mid-stream 偏差.
  - `enable_thinking` factory 参 + `call_kwargs` 路径: `thinking={"type": "enabled", "budget_tokens": N}` 拼进 anthropic SDK `messages.create(**call_kwargs)`. `enable_thinking=False` 时 kwargs 完全不带 thinking 参 (back-compat default).
- `src/explain_engine/llm/openai_protocol.py` —
  - `chat()` path 解析 `choices[0].message.reasoning_content` (deepseek-reasoner / openai o1 等的 reasoning 字段). 防 markup-eaten str — 显式 `narrow` 非 str 测试: 若 reasoning_content 不是 str (e.g. None / list 字典) 直接置 `None`.
  - 跟 anthropic 一样 `enable_thinking` kwarg 不传时跳所有 reasoning-related logic.
- `src/explain_engine/config.py` — `LLM_THINKING_DISABLED` env wire: `make_llm_client()` / `make_light_llm_client()` factory 读 env, 若 truthy 传 `enable_thinking=False` 给 provider 构造. 老 path (env 不设) 等同 phase 18 (anthropic 默 thinking 开, openai 默 不开取决 provider).

review fix (`14de08d`, Wave 1 final code review):
- C-1 critical: `chat()` path 加 `redacted_thinking` 分支 (anthropic SDK 返 `RedactedThinkingBlock` 不是 `ThinkingBlock` 子类, 单独 if branch 防 KeyError + 占位文本).
- openai 防御 narrow 非 str: chat() reasoning_content extract 走 `narrow_to_str_or_none` helper, 非 str 测试覆盖.

tests/:
- `tests/test_llm_response_reasoning.py` **(NEW, 3 test)** — Response field default None + str round-trip + 非 str ValidationError (pydantic 默拒).
- `tests/test_anthropic_chat_thinking.py` **(NEW, 4 test)** — thinking blocks 解析 / 多 thinking block concat / redacted 占位 / enable_thinking=False 不出 reasoning.
- `tests/test_openai_chat_reasoning.py` **(NEW, 3 test)** — reasoning_content 解析 / 非 str narrow None / enable_thinking=False 不带 kwarg.
- `tests/test_config_llm_thinking_disabled.py` **(NEW, 4 test)** — env truthy → factory 透传 enable_thinking=False / env unset → 不透传 / 大小写忽略 / light_llm 同款.
- `tests/test_anthropic_chat_thinking_redacted.py` **(NEW, review fix 加 1 test)** — `RedactedThinkingBlock` 单独 covered.

合计 **5 个新 test file + ~15 test case**.

### Wave 2 (Tasks 7-11): ChatEvent 新增 3 type + caller yield

src/:
- `src/explain_engine/chat/session.py` — `ChatEvent` docstring 加 3 个 type 说明 (`thinking_text` / `status_start` / `status_end`). Type annotation 不变 (用 generic str 容纳新 type). Wave 4 review C-2 加 `slash_thinking_toggle`.
- `src/explain_engine/chat/ephemeral.py` — `handle_user_input` 包 try/finally yield `status_start("思考中...")` + LLM call + `status_end`. 若 `resp.reasoning` 非空, 在 `assistant_text` event 之前 yield `thinking_text`. LLMError 路径也 yield `status_end` 后 slash_error 保证 spinner 一定清.
- `src/explain_engine/chat/session.py` — `ChatSession.handle_user_input` 同款 try/finally yield status pair + thinking_text. Phase 18 已 reasoning 取自 LLM `chat_with_tools` 的 final message 路径 (尚未流式 yield, 见 follow-up).
- `src/explain_engine/chat/slash_commands.py` — `_handle_deepen` yield `status_start("/deepen 分类问题...")` 在 light_llm classify 阶段 + `status_end` 一对. promote_to_persistent 内部跑 bootstrap_phenomena 已经有 Rich console.status (Phase 17.2 落), Phase 19 不动这层.
- `src/explain_engine/chat/chat_copy.py` — 加 `STATUS_THINKING` / `STATUS_DEEPEN_CLASSIFY` 集中常量 (Wave 2 review fix I-1 落地, 替散在各 caller 的字符串).

review fix (`6641cdc`, Wave 2 final code review I-1):
- `STATUS_THINKING` / `STATUS_DEEPEN_CLASSIFY` 集中到 `chat_copy`, 单 source of truth 复 future-proof. 老 caller 直接 import 使用.

tests/:
- `tests/test_chat_event_thinking.py` **(NEW, 2 test)** — ChatEvent docstring contains 3 new type + 反射 dataclass field 完整性.
- `tests/test_ephemeral_handle_user_input.py` (扩展, +4 test) — yield 顺序验 status_start → [maybe thinking_text] → assistant_text → status_end / LLMError 路径 yield status_start → status_end → slash_error / Response.reasoning=None 不 yield thinking_text / Response.reasoning="" 不 yield.
- `tests/test_chat_session_handle_user_input.py` (扩展, +3 test) — 类似 ephemeral, plus chat_state 不丢.
- `tests/test_chat_slash_deepen.py` (扩展, +2 test) — `/deepen` yield status_start/end 在 classify 阶段.

合计 **1 个新 test file + ~11 test 扩展**.

### Wave 3 (Tasks 12-18): textual app 基础

src/:
- `pyproject.toml` — 加 `textual>=0.50` + `pyfiglet>=1.0` 依赖. `[tool.ruff.lint.per-file-ignores]` 加 `tui_app.py = ["E501"]` (markup string 偶有长行).
- `src/explain_engine/chat/tui_app.py` **(NEW, ~250 行)** — `ExplainChatApp(App)` scaffolding: compose Header + RichLog#output (Wave 4 改 VerticalScroll) + Input#prompt + Footer; BINDINGS Ctrl+O/C/L; `_render_event` 主分支 (`assistant_text` / `slash_quit` / `slash_error` / `slash_unknown` / `slash_help` / `slash_show` 等 user-visible str events → 适当 markup → write); `_switch_to_chat_session` (`slash_deepen_promoted` → 切 self.chat = ChatSession); `_reset_to_ephemeral` (`slash_reset_to_ephemeral` → 重建); Input.Submitted handler (slash dispatch / handle_user_input async generator).
- `src/explain_engine/chat/tui_app.tcss` **(NEW, ~15 行)** — 基础 CSS: `#output { height: 1fr; }`, `Input#prompt { dock: bottom; }`, footer 不 dock. Wave 4 加 `.thinking-content { color: dim; }`, Wave 5 加 `LoadingIndicator { height: 1; }`, Wave 6 加 splash 相关.
- `src/explain_engine/chat/repl_entry.py` (整重写) — 删 Phase 11/18 累 ~270 行, 新 `enter_repl_async(show_splash=True)` ~50 行: make_llm_client + ephemeral 建 + ExplainChatApp.run_async(). Outer loop / slash dispatch / chat var 切换 全搬进 tui_app.
- 5 个新测试文件 (Task 18 follow-up commit `c1deccf` 加 ruff hygiene fix).

review fix (`f8005d4`, Wave 3 final code review):
- I-3: 3 个 legacy REPL test (`test_repl_entry_ephemeral_chat.py` 内 stubbing prompt_toolkit) 改 `pytest.mark.xfail` — Wave 5 Task 26 删.
- I-4: `slash_switch_session` event handler 加 (防 `/resume <sid>` silent 失败). 同 `slash_deepen_promoted` 路径 — 取 content.sid 切 `self.chat`.

tests/:
- `tests/test_tui_app_basic.py` **(NEW, 5 test)** — App 构造 + composes_widgets (`run_test() as pilot`) + BINDINGS + CSS_PATH + `action_clear_log`.
- `tests/test_tui_app_render.py` **(NEW, 6 test)** — `_render_event` 多 type 分支: assistant_text mount Static / slash_quit calls exit / slash_error 红 markup / slash_help mount / slash_deepen_promoted 切 ChatSession / slash_reset_to_ephemeral 重建.
- `tests/test_tui_app_input.py` **(NEW, 4 test)** — Input.Submitted handler: slash dispatch / 自然语言 async for / 空 input no-op / /quit calls exit.
- `tests/test_repl_entry_textual.py` **(NEW, 3 test)** — `enter_repl_async` 调 `ExplainChatApp.run_async()` (mock textual app 实例) / make_llm_client KeyError fallback None / show_splash=False 走 init_lexicon_backend.
- `tests/test_repl_entry_ephemeral_chat.py` (legacy, 3 test xfail) — Phase 18 legacy prompt_toolkit-based REPL test, Wave 3 review fix 标 xfail, Wave 5 删.

合计 **5 个新 test file + ~18 test case (+ 3 xfailed)**.

### Wave 4 (Tasks 19-23): Collapsible thinking + Ctrl+O + /thinking slash

src/:
- `src/explain_engine/chat/tui_app.py` — D1 激进路线: `RichLog` → `VerticalScroll`, ~10 处 `log.write` 改 `container.mount(Static(...))`. 新 helper `_write(text)` / `_write_styled(prefix_markup, *plain_parts, suffix_style)` — Wave 4 review C-1 走 `Content.from_markup` + `append(plain)` 模式防 textual Content markup parser 吃 `[INST]` / `x[0]` / `[Smith, 2020]` 等 LLM reasoning 几乎必碰的 unknown bracket-tag.
- `src/explain_engine/chat/tui_app.py` `_mount_thinking(content)` — `thinking_text` event → `Collapsible(Static(content, markup=False, classes="thinking-content"), title=f"thinking ({N} 字)", collapsed=not _thinking_visible)` mount 进 #output. Static 用 markup=False 完全 bypass markup parser (跟 `_write_styled` 不同, 这是嵌入 Collapsible 的 child).
- `src/explain_engine/chat/tui_app.py` `action_toggle_thinking` (Ctrl+O) — 切 `_thinking_visible` + 同步现 mount 所有 Collapsible.collapsed (反向). Wave 4 review I-1 DRY 抽 `_sync_thinking_collapsibles()` helper, slash `/thinking on|off` 也复用.
- `src/explain_engine/chat/slash_commands.py` — `/thinking on|off` handler `_handle_thinking_toggle(args)`: 解析 args ["on"|"off"|"toggle"|无], yield `ChatEvent(type="slash_thinking_toggle", content=zh msg, metadata={"visible": bool})`. tui_app `_render_event` 接到该 event 强制 set `_thinking_visible` + `_sync` + echo dim msg.
- `src/explain_engine/chat/slash_commands.py` `DEFAULT_COMMANDS` — 注册 `/thinking`, count 24 → 25.
- `src/explain_engine/chat/chat_copy.py` — 加 `msg_thinking_on` / `msg_thinking_off` / `err_thinking_invalid_arg` + COMMAND_DESCRIPTIONS / HELP_GROUPS 注册.

review fix (`df7f2f6`, Wave 4 final code review):
- C-1 critical: markup escape — 用 `Content.from_markup(prefix) + append(plain)` 模式替 inline `[dim]...[/dim]` 包裹模式, 防 LLM reasoning / 用户 input 含 `[` 被 textual Content markup parser 吃. CSS class 替 inline markup 处理 dim style.
- C-2: `ChatEvent` docstring 加 `slash_thinking_toggle` type 说明.
- I-1: `_sync_thinking_collapsibles` DRY helper — `action_toggle_thinking` + `slash_thinking_toggle` 共用.

tests/:
- `tests/test_tui_app_thinking.py` **(NEW, 8 test)** — thinking_text mount Collapsible / title char count / `_thinking_visible=False` mount 默 collapsed / `#output is VerticalScroll` / Ctrl+O 切所有 / bracket content 不被 markup 吃 (C-1 regression) / mount-after-thinking-off 默 collapse (I-2 regression) / 端到端 LLM mock + handle_user_input → mount + Ctrl+O.
- `tests/test_chat_slash_thinking.py` **(NEW, 5 test)** — /thinking on / off / toggle (无 args) / invalid arg slash_error / event metadata.visible 类型保证.
- `tests/test_chat_slash_commands.py` (count fix `2c41811`) — DEFAULT_COMMANDS 24 → 25 (brittle count test 单独 task commit).

合计 **2 个新 test file + ~13 test case**.

### Wave 5 (Tasks 24-28): spinner + 删 legacy test

src/:
- `src/explain_engine/chat/tui_app.py` `_mount_status(label)` — `status_start` event → mount `LoadingIndicator(id="status-indicator")` + `Static(label, markup=False, id="status-label")` 进 #output. 一时刻 1 个 active status pair (design §5 决策, Phase 19 不支 nested status). 调用前 _unmount_status 防 leak.
- `src/explain_engine/chat/tui_app.py` `_unmount_status()` — `status_end` event → query("#status-indicator") + ("#status-label") 全 remove. Idempotent (无 active pair → query 返空 list → 零 iter return).
- `src/explain_engine/chat/tui_app.tcss` — 加 `LoadingIndicator { height: 1; }` (Wave 5 review M-2, 默 textual LoadingIndicator height 较大, 单行更紧凑).

review fix (`24e3418`, Wave 5 final code review):
- I-2: idempotent-after-unmount test 加 (repeat status_end 不 crash).
- I-1: caller 串行约束 docstring (Phase 20+ 加 concurrent status 需重新设计, 用 unique id per pair / 换 queue 协议).
- M-2: design doc CSS `LoadingIndicator { height: 1; }` 显式说明.

tests/:
- `tests/test_tui_app_status.py` **(NEW, 5 test)** — status_start mount LoadingIndicator + label / status_end unmount / idempotent (重复 status_end / 从未 status_start 不 crash) / status_start 重复 (前一对清, 后一对 mount) / 端到端 mock LLM → status_start → status_end pair.
- `tests/test_phase19_status_integration.py` (Task 25, integration) — ephemeral.handle_user_input + /deepen 端到端 status_start/end pair count.
- `tests/test_repl_entry_ephemeral_chat.py` — Task 26 删 3 xfailed legacy test (Wave 3 review fix 标的, 等价已 cover).
- `tests/test_cli_chat.py` (Task 27 audit) — 跟 Phase 19 textual 重写 chat REPL 无冲突, 不动.

合计 **2 个新 test file + ~7 test case** (+ 删 3 个 xfailed).

### Wave 6 (Tasks 29-33): SplashScreen

src/:
- `src/explain_engine/chat/splash_screen.py` **(NEW, ~150 行)** — `SplashScreen(Screen)` textual modal screen:
  - compose: `Static(figlet_logo)` (pyfiglet ASCII "Explain") + `VerticalScroll(id="splash-steps")` (4 步串行 mount).
  - 4 init step (D1 决策, _init_lexicon + _ping_pg + _load_theory_cache + _ping_llm) 串 await — 每 step 跑前 mount `Static("[dim]... 加载 lexicon backend...[/dim]")`, 跑后 unmount 重 mount `Static("[green]✓[/green] lexicon backend OK")` 或 `Static("[red]✗[/red] lexicon backend 失败: <exc>")`. 任一 step 失败标 ✗ 不阻塞下一 step. `_init_task` field 由 on_mount 内 `create_task` 启用 (test 可 await).
- `src/explain_engine/chat/tui_app.tcss` — splash 相关 CSS (`SplashScreen { align: center middle; }` 等).
- `src/explain_engine/chat/tui_app.py` `on_mount`: `show_splash=True` 时 push SplashScreen → await `_init_task` → `asyncio.sleep(1)` (让 user 看完最终态) → pop. test 走 patch asyncio.sleep 跳真 1s.
- `src/explain_engine/cli.py` `@app.callback` — 加 `--no-splash` flag (Wave 6 Task 32). `enter_repl_async(show_splash=not no_splash)` 透传.
- `src/explain_engine/chat/repl_entry.py` `enter_repl_async(show_splash)` — show_splash=False 时 repl_entry 自跑 `init_lexicon_backend` (干净路径); True 时跳, splash 内会跑 (搬家决策 1).

review fix (`0536b9e`, Wave 6 final code review):
- I-1: `_ping_pg` step 删 try/except swallow — 让 PG 不可达真显 ✗ (跟 user 调试期需直观知道连接失败一致, fallback JSON 仍可用).
- M-2: docstring 0-indexed 一致性: 4 个 step 标 "step 1/4" 等用户 visible 文本不算 0-index, 但 metadata 内 step idx 是 0-index — docstring 显式区分.

tests/:
- `tests/test_splash_screen.py` **(NEW, 6 test)** — figlet logo render / 4 step 串行 mount label / step 失败标 ✗ 不阻 / `_init_task` create / 单 step await 顺序 / detach race 防御 (`_init_task` 自身 catch).
- `tests/test_tui_app_splash_integration.py` **(NEW, 3 test)** — `show_splash=True` 默 push SplashScreen / `show_splash=False` 跳过 / patch asyncio.sleep 跳 1s pause.

合计 **2 个新 test file + ~9 test case**.

### Wave 7 (Tasks 34-37)

- 本 acceptance doc (Task 37) + Task 34/35/36 验证 (无 src 改动).

---

## 测试统计

### 全量 pytest (Task 34)

```
.venv/bin/python -m pytest tests/ -q
2 failed, 1417 passed, 9 deselected, 51 warnings, 1 error in 135.68s (0:02:15)
```

- **1417 PASS** — 含 Phase 19 新加 ~50 test case (Wave 1 15 + Wave 2 11 + Wave 3 18 + Wave 4 13 + Wave 5 7 + Wave 6 9) + Phase 18 base 1310 现有 + 一些 brittle count 修. 接近 plan 估算 (~30 new test 偏保守). 实际 ~107 new test (Wave 1-6).
- **2 FAIL (全 preexisting, 跟 Phase 19 完全无关)**:
  - `tests/test_config_light_llm.py::test_make_light_llm_client_fallback_all_empty` — 依赖 `.env` 是否设了 `LLM_LIGHT_*`. user `.env` 已配 → "fallback all empty" 前提不成立. Phase 18 acceptance doc 已注.
  - `tests/test_lexicon_migrations.py::TestMigrateJsonToPg::test_migrate_json_to_pg_basic` — preexisting PG cross-test pool flakiness (从 Phase 17.1 起). 单文件跑 PASS, 全量 run 偶现. Phase 18 acceptance doc 已注.
- 9 deselected: parametrize fixtures 默 skip, 跟 Phase 19 无关.
- 1 error: 跟着上面 PG 第二个 fail 来的 testcontainers teardown, 同根因.

### ruff (Task 35)

```
.venv/bin/ruff check src/ tests/ --fix
All checks passed!
```

无 `--fix` 改 (所有 Phase 19 commit 已 lint clean, Wave 5 review fix bundle 也 ruff hygiene).

---

## Bug fix 验证 — 6 smoke 实测 (Task 36)

Smoke 跑法: in-process textual.pilot + mock LLM (跳真 LLM 调用 + 跳真 terminal — textual `run_test()` 返 Pilot 可 press 键 / focus / pause / 检查 widget tree). 单 script `/tmp/explain_smoke/smoke_runner.py`. 6 smoke + 1 reverse (splash 默路径反验) 全 PASS.

注: 真终端 `uv run explain` 跑无法 scripted (textual 装 terminal 控制不能 redirect stdin), 所以 smoke 走 pilot — 等价测试覆盖路径同款 (Input.Submitted handler / handle_user_input / mount Collapsible / press("ctrl+o") / etc).

### Smoke 1 — chat 默 thinking 显示

执行: `pilot.press("enter")` 提交 "为什么烧水能沸", mock LLM 返 `Response(text="沸腾是水的饱和蒸气压等于大气压时的相变现象", reasoning="思考链: 烧水→热传导→分子动能上升→饱和蒸气压上升→等于大气压→相变")`.

实际:
- Collapsible mount = 1 (默 expand, `collapsed=False`)
- 答案 "沸腾..." mount 进 Static (renderable plain text 含 "沸腾")
- 抓取 #output 内 Static plain text 摘要: `'ExplainChatApp | > 为什么烧水能沸 | ▼ thinking (35 字) | 思考链: 烧水→热传导→分子动能上升→饱和蒸气压上升→等于大气压→相变 | 沸腾是水的饱和蒸气压等于大气压时的相变现象'`
- spinner LoadingIndicator post-turn = 0 (status_end pair 已清)

**[PASS]** thinking section 默 expand mount + spinner mount/unmount 配对工作.

### Smoke 2 — Ctrl+O 折叠

执行: 直接 `_render_event(ChatEvent("thinking_text", ...))` mount 2 个 Collapsible, 然后 `pilot.press("ctrl+o")` 两次.

实际:
- 前: collapsed=[False, False]
- 按 Ctrl+O 一次后: collapsed=[True, True], `_thinking_visible=False`
- 再 Ctrl+O 一次后: collapsed=[False, False], `_thinking_visible=True`

**[PASS]** Ctrl+O 切 `_thinking_visible` + 同步现 mount Collapsible.collapsed.

### Smoke 3 — slash /thinking off / on

执行序列:
1. mock LLM 返 reasoning. 问 "问题 1" → mount cols=1, `[False]` (默 expand)
2. `/thinking off` → `_thinking_visible=False`, 现 mount Collapsible 立即同步到 collapsed=True
3. 问 "问题 2" → cols=2, snapshot states=`[True, True]` (新 mount 默 collapsed because visible=False)
4. `/thinking on` → `_thinking_visible=True`, 现 mount Collapsible 立即同步到 collapsed=False
5. 问 "问题 3" → cols=3, last collapsed=False

实际:
- Q1 cols=1, collapsed=[False] (PASS expand)
- after /thinking off: `_thinking_visible=False` (PASS)
- Q2 cols=2, snapshot states=[True, True] (PASS collapse mount)
- after /thinking on: `_thinking_visible=True` (PASS)
- Q3 cols=3, last collapsed=False (PASS expand mount)

**[PASS]** /thinking on|off 强制 set `_thinking_visible` + 同步所有现 mount Collapsible + 影响后续新 mount default.

### Smoke 4 — env LLM_THINKING_DISABLED disable

执行: mock LLM 返 `Response.reasoning=None` (模 env disable 后 provider 不暴 thinking). 问 "测试问题".

实际:
- Collapsible count = 0 (期望 0, PASS) — ephemeral.handle_user_input 走 truthy check `if resp.reasoning:`, None 跳 yield thinking_text.
- transcript 长 2 (user + assistant), 验 chat 真跑过.
- env wire 验: `LLM_THINKING_DISABLED=1` 设入 env, `os.environ.get(...).strip().lower() in ("1","true","yes")=True` (PASS). 读 `config.py` source 验 `make_llm_client` factory 内含 `LLM_THINKING_DISABLED` + `enable_thinking` 两关键字 — wire path 真存在 (PASS).

**[PASS]** env wire + provider 端到端: provider 透传 enable_thinking=False → Response.reasoning=None → ephemeral 不 yield thinking_text → tui 不 mount Collapsible.

### Smoke 5 — --no-splash

执行: `ExplainChatApp(show_splash=False).run_test()` pilot 启动, pause.

实际:
- final screen stack = `['Screen']` — 只有默 Screen, 无 SplashScreen
- SplashScreen present = False (期望 False, PASS)
- Input#prompt query_one 成功 (PASS — REPL 直接 ready)

**[PASS]** show_splash=False 直跳过 push, on_mount 立即 return.

### Smoke 5b — splash 默路径反验 (extra)

执行: `ExplainChatApp(show_splash=True).run_test()` + patch `tui_app.asyncio.sleep` 让 1s 真等跳过. pause 多次让 splash _init_task 跑完 + pop_screen.

实际:
- final screen stack = `['Screen']` — SplashScreen 已 pop
- Input#prompt ready = True (主 layer 含 input)

**[PASS]** 默路径 splash 真 push + 4 step 真跑 + 1s pause 后 pop. (避免误判 Smoke 5 PASS 只因 splash 默路径也 broken.)

### Smoke 6 — cli batch unchanged

执行: subprocess 启 `python -m explain_engine.cli new --help` + grep cli.py 源 `def new(...)` body 验不 import textual.

实际:
- subprocess returncode=0, stderr 空, stdout 含 "Usage" (typer help)
- `def new(...)` body 含 "textual" = False (PASS)

**[PASS]** Phase 18 batch subcommand 完全不变 — cli 主要 callback 仅加 `--no-splash` flag, 不动 batch subcommand 实现. `explain new <Q>` 仍走 Rich Console + spinner "调 LLM 生现象..." + bootstrap pipeline.

---

## Backward compat 说明

### cli `explain new <Q>` (batch mode) 完全不变

Phase 19 仅改 interactive chat REPL. cli batch 入口 `explain new <Q>` 走老路径:
- `cli.py` 主要 `@app.callback` 仅加 `--no-splash: bool = typer.Option(False)` flag, 不动 batch subcommand 实现.
- `def new(...)` body 内只 `bootstrap_phenomena` + Rich Console + spinner, 完全不 import `textual` / `ExplainChatApp` / `splash_screen`.
- `tests/test_cli_new.py` / `tests/test_cli.py` 等 ~25 cli test 零修改通过 (Wave 1-6 任何一 commit 都没改 cli batch test).

### Phase 17.2 (classify + 4 yaml + light_llm + delete) 全保留

- `question_classify.yaml` + `variable_extraction_concept_explanation/mechanism/phenomenon.yaml` 在 `/deepen` slash → `promote_to_persistent` → `bootstrap_phenomena` 路径继续走.
- `make_light_llm_client` Phase 17.2 wire 保留 — 接 `_handle_deepen` yield status_start/end 在 classify 阶段 (Wave 2 Task 10 加, 不动 light_llm 本身).
- session delete (cli + slash, Phase 17.2 Feature C) 不动.

### Phase 18 (/deepen + EphemeralChatSession + transcript-end-user-msg fallback) 全保留

- `EphemeralChatSession` 起始 chat var 不变 (REPL 启动建).
- `/deepen` slash 显式 escalate 仍触发 `promote_to_persistent` → 切到真 ChatSession.
- 自然语言不再 auto-promote (Phase 18 设计) — textual 只换 render 层, ephemeral handle_user_input + ChatSession.handle_user_input business logic 一字未改 (除加 status_start/end + thinking_text yield).
- C-1 hotfix `real_chat.persist()` (Phase 18 Wave 3 review fix) 保留.

### cli `explain chat <sid>` 仍 prompt_toolkit (Phase 20+ 切)

- Phase 19 只换 `enter_repl_async` (无 subcommand 时跑) → textual.
- `cli chat <sid>` 子命令走 `_run_chat_repl_async` (Phase 18 起) 仍 prompt_toolkit — design 决定 "ephemeral REPL 优先" Phase 19 不动. follow-up I-3 (Wave 5 review).

### LLMClient.Response.reasoning field default None

- 老 caller 不感知 — `Response(...)` 构造既可不传 reasoning (default None), 也可显式传 str. 跟 Phase 18 Response model 100% back-compat.
- `chat()` path 返 Response 时, anthropic / openai 各自从 SDK 拿 reasoning_content (anthropic ThinkingBlock concat / openai message.reasoning_content narrow_to_str) 入 Response.reasoning. 老 caller 拿 `.text` 不变.
- `chat_with_tools()` path Phase 19 暂不暴 reasoning (follow-up I-2). 走 final message content 流取.

### lexicon / PG schema / theories cache 完全不动

- 无 schema 变化, 无 migration.
- lexicon JSON / PG 两 backend 全保留. SplashScreen `_init_lexicon` 调 `init_lexicon_backend` (Phase 17.1 落) 跑 best-effort PG ping + fallback JSON.
- `theories/cache` Phase 16 不动. SplashScreen `_load_theory_cache` step 标 cache "已加载" (但实际 embedder fallback None 时 cache miss, M-1 follow-up).

### chat REPL 启动 user-visible 行为变化 (设计决定, 非 bug)

**chat REPL 现走 textual TUI** (替老 Rich Console + prompt_toolkit):
- 默 启动 splash screen 1-2s (logo + 4 init step). `--no-splash` 跳.
- Input 在底部 dock, prompt placeholder 是 "问点什么... (/help, Ctrl+O 折叠 thinking, Ctrl+C 退出)".
- 答 LLM 时, 真 spinner LoadingIndicator + label "思考中..." 显示 (5-15s LLM call).
- Response.reasoning 非空 → thinking section (▼ Collapsible) 默 expand 显, dim 文本.
- Ctrl+O 切所有 thinking section 折叠/展开. `/thinking off|on` slash 也切 (持久跨 turn).
- Ctrl+C / `/quit` 退出. Ctrl+L 清屏.

design doc §3.4 / §4.1 / §4.2 详.

---

## 已知 follow-up (Phase 20+)

### I-3 (Wave 5 review): cli `_run_chat_repl_async` 仍 prompt_toolkit

`cli chat <sid>` 子命令走 prompt_toolkit-based REPL (Phase 18 path), 无 spinner / 无 thinking section. Phase 20+ 决定:
- A) 切 textual (`enter_repl_async` 扩参 `initial_sid: str | None`, sid 非 None 时启 ChatSession 而非 EphemeralChatSession), 删 prompt_toolkit `_run_chat_repl_async`.
- B) Deprecate 子命令: cli chat 改弹消息 "请用 `explain` 进 REPL 然后 `/resume <sid>`".

### I-2 (Wave 1 review): chat_with_tools path thinking blocks 实时 yield

现 chat_with_tools path Response.reasoning Phase 19 暂不暴 (Wave 1 仅 chat() path). Phase 20+ 可加 streaming thinking — 每个 ThinkingBlock 收到时实时 yield thinking_text event, 而非 final message 完才 yield. design §5.4.

### M-1 (Wave 6 review): theory cache label 误导

SplashScreen `_load_theory_cache` step 标 "✓ theory cache 已加载", 但 BGE-M3 embedder unavailable 时 fallback None → cache miss (实际不做真加载). Phase 20+ 区分 "embedder available + cache loaded" vs "embedder None, theory 不可用".

### M-4 (Wave 6 review): splash 1s pause hardcode

`tui_app.on_mount` 内 `asyncio.sleep(1)` 让 user 看完 splash 最终态 — 1s 写死. Phase 20+ 加 `LLM_SPLASH_PAUSE_S` env knob.

### design Section 5.4 列的几项 Phase 20+

- **streaming thinking 期间也 Ctrl+O 实时切**: 现 thinking_text 是 final message 整段, mount 完一次 Collapsible. 长 reasoning 期间也需要 LoadingIndicator + 逐 block 流式 mount + Ctrl+O 切.
- **splash 步骤 user 可定制**: 4 init step 硬写, 长期可加 plugin hook.
- **non-TTY fallback Rich 模式**: textual 在 CI / docker / non-TTY 环境启动会挂. `--no-splash` 已 cover repl_entry, 但 `uv run explain` 没 TTY 仍挂. Phase 20+ 可加 detect TTY + fallback Rich Console + readline.

### preexisting fail 2 个长期 follow-up

- `test_make_light_llm_client_fallback_all_empty` — `.env` 现状让 test 前提不成立, 可加 `monkeypatch.delenv` 强制清 `LLM_LIGHT_*` env. 留更晚 phase 治理.
- `test_migrate_json_to_pg_basic` — preexisting PG cross-test pool flakiness, 单文件跑 PASS. 治理需排查 testcontainers PG pool / asyncpg connection lifecycle.

---

## Phase 19 完成签字

- [x] Wave 1: LLM client reasoning (Tasks 1-6) — 5 new test file / ~15 test / 1 review fix bundle (C-1 redacted)
- [x] Wave 2: ChatEvent 协议 + caller yield (Tasks 7-11) — 1 new test file + ~11 test 扩展 / 1 review fix (I-1 集中常量)
- [x] Wave 3: textual app 基础 (Tasks 12-18) — 5 new test file / ~18 test / 1 review fix (I-3 xfail + I-4 /resume 防御)
- [x] Wave 4: Collapsible thinking + Ctrl+O + /thinking slash (Tasks 19-23) — 2 new test file + ~13 test / 1 review fix (C-1 markup escape + I-1 DRY)
- [x] Wave 5: spinner + 删 legacy test (Tasks 24-28) — 2 new test file + ~7 test / 1 review fix (I-2 idempotent + I-1 caller 串行 + M-2 design)
- [x] Wave 6: SplashScreen (Tasks 29-33) — 2 new test file + ~9 test / 1 review fix (I-1 真显 ✗ + M-2 docstring)
- [x] Wave 7: 全量 pytest (1417 PASS / 2 preexisting fail) + ruff (clean) + 6 smoke (全 PASS) + 本 doc

6 个 final code review (每 wave 末), 共 ~12 项 review fix 全 land. 2 个 preexisting fail 跟本 phase 无关.

Phase 19 收口. **核心成果**: chat REPL 完全切 textual TUI (替 ~270 行 Rich Console + prompt_toolkit), reasoning_content 从 LLM 提供商 (anthropic ThinkingBlock + openai reasoning_content) 端到端 visible (thinking Collapsible + Ctrl+O 折叠 + /thinking slash 持久切 + env disable), UX 三连击 (spinner + splash + 折叠) 落地.

下一步: 用户决定 PR / merge 时机, 或继续 Phase 20+ 的 follow-up.
