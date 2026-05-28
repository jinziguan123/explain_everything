# Phase 20.0 P0 — acceptance

**Design**: `docs/plans/2026-05-28-phase-20-p0-chat-stall-design.md` (commit `dae02ce`)
**Plan**: `docs/plans/2026-05-28-phase-20-p0-chat-stall-plan.md` (commit `69a0bd8`)

## 范围

Phase 19 真终端 smoke (user 2026-05-28 上午) 发现的 3 个互联 P0 stuck UX bug 一起修.

## Layer A — SDK timeout (Task 1)

**Before**: AsyncAnthropic / AsyncOpenAI 不传 timeout, streaming chunk gap permanent hang.

**After**: `httpx.Timeout(connect=10, read=120, write=60, pool=None)`. env knob `LLM_READ_TIMEOUT_S=120` (default). chunk gap 120s → APITimeoutError 走现有 retry path.

**TDD**: 9 tests in `tests/test_llm_timeout_config.py`, 全 PASS.

**Commits**:
- `5da900b` — initial impl (7 tests + 3 file modify)
- `07ca4e4` — code review I-1 (env validation helper) + I-2 (httpx direct dep) + 2 tests

## Layer B — escape cancel chat task (Task 2)

**Before**: textual ctrl+c → self.exit() 等 task drain → chat task 卡 await stream → app 跟卡 → "ctrl+c 无效".

**After**: escape binding → action_cancel_chat → cancel self._chat_task. _consume_chat_events 兜底 _unmount_status + mount '请求已取消'. ctrl+c 行为不变.

**TDD**: 3 tests in `tests/test_tui_app_cancel_chat.py`, 全 PASS.

**Deviation from plan** (justified): plan 写 `await self._chat_task` in `_handle_input_submitted` 会 deadlock textual message pump (verified at `textual/message_pump.py:634-673` 是 serial dispatch loop, await 内部 blocks escape event dispatch). 改 fire-and-forget + add_done_callback pattern. Future Input.disabled work 待 phase 20.1+ 解决 race scenario (user 快速连按 enter 时旧 task orphan).

**Commits**:
- `523e4ec` — initial impl (escape binding + _chat_task ref + _consume_chat_events helper + action_cancel_chat + 3 tests)
- `7f813c9` — code review I-1 (RUF006 self._background_chat_tasks set pattern mirror session.py:255) + I-2 (test 1 pin '请求已取消' mount assertion)

## Layer C — PgUp/PgDn keyboard scroll (Task 3)

**Before**: mouse=False (5c3dfac Phase 19 Bug A) 副作用 wheel 失效 + Input 焦点抢 PgUp/Dn → 无 keyboard scroll 路径.

**After**: app-level BINDINGS PgUp/PgDn → VerticalScroll.scroll_page_up/down. Footer 显 hint "上翻" / "下翻".

**TDD**: 2 tests in `tests/test_tui_app_keyboard_scroll.py`, 全 PASS.

**Commit**: `d7e4433`

## Test 累计

14 个新 test 全 PASS:
- test_llm_timeout_config.py: 9 (Task 1 + follow-up)
- test_tui_app_cancel_chat.py: 3 (Task 2 + follow-up)
- test_tui_app_keyboard_scroll.py: 2 (Task 3)

全量回归: 1456 passed, 6 failed + 1 error — ALL preexisting (verified consistent across all 5 commits via git stash):
- tests/test_config_light_llm.py (env pollution, single-run PASS)
- tests/test_lexicon_migrations.py (PG state pollution, single-run PASS)
- tests/test_chat_slash_resume_hotfix.py (transient)
- tests/test_lexicon_pg_api.py × 2 + 1 error (transient PG)
- tests/test_tui_app_input.py × 3 (Phase 19 tui_app preexisting fail, 待 Phase 20.1+ 修)

Phase 20.0 引入 **0 增量 regression**.

## 真终端 smoke 验证 (user 自己跑)

启 terminal (macOS Terminal.app / iTerm2):

1. `uv run explain` → splash → ephemeral REPL.
2. `/resume <sid>` 进任意 ChatSession.
3. 输自然语言问题 (e.g. "解释一下六十四卦如何区分四季和吉凶").
4. **case 1 (LLM 正常 stream)**: 看屏幕逐步输出 markdown, 完整, 不卡.
5. **case 2 (LLM stream 中途卡 / 想中途中断)**:
   - 等屏幕停一会, 按 **Esc** → 应 mount "请求已取消." dim 文字, Input 立刻可输下一句.
6. **滚动验证**: 输 10 个问题让 #output 累积内容超一屏. 按 **PgUp** 上翻一页, **PgDn** 下翻一页. 应工作.
7. **ctrl+c**: 仍能退出 app (走 action_quit_app 不变).

如有 case 失败, 在本 doc 加 "已知问题" section + 开 follow-up task.

## Follow-up (Phase 20.1+)

- 小型 UX 套餐 (设计已敲定: #4 改名 "检查 theory cache" 留 step / #5 LLM_SPLASH_PAUSE_S env knob default 5.0 / #8 picker 排序+截断) — 等本 P0 acceptance 通过后做
- Input.disabled 防 race (Task 2 deviation 提到的 user 快速连按 enter 时旧 task orphan)
- VerticalScroll widget cap LRU evict (user follow-up #6)
- textual non-TTY guard (user follow-up #7)
- chat_with_tools thinking blocks streaming 实时 yield (follow-up #3)
- cli `_run_chat_repl_async` legacy 切 textual (follow-up #2)
- test_tui_app_input.py 3 个 preexisting fail (Phase 19 引入, Task 2 fire-and-forget pattern 改不动同区域, 待专门排查)

## 关联

- Phase 19 Bug A (`5c3dfac`): mouse=False 副作用 Layer C 修
- Phase 19 Bug C (`fb2563b`): _spinner _unmount_status 是 Layer B 兜底依赖
- Phase 19 Wave 7 (`9838507`): /resume picker 死锁同款 textual stdin hold 风险, Phase 20.0 不再 regress
- session.py:255 `_background_tasks` set pattern: Task 2 follow-up I-1 mirror reference
- textual/message_pump.py:634-673: Task 2 deviation 真因 reference (serial dispatch loop)
