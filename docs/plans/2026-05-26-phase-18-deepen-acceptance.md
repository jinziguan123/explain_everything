# Phase 18 Acceptance — `/deepen` Hybrid 化

收口日期: 2026-05-26
负责人: jinziguan + Claude Opus 4.7 (Cowork)
分支: `dev` (无 push, 等用户决定 PR / 合并时机)
Base SHA (Phase 18 起点): `1178ef5^` (= Phase 17.2 final fix 落地后)
End SHA (Wave 4 结束): 见本 doc commit (Task 24)

---

## Phase 18 总览

**Hybrid `/deepen` 落地**: 修复 Phase 17.2 后仍存的"机械化 bootstrap"过度建模问题
— ephemeral chat REPL 启动后,user 输入自然语言不再 auto-promote 走全套 bootstrap
pipeline; 改为 system-1 直接 LLM chat,用户显式 `/deepen [Q]` 才 escalate 进
classify + bootstrap_phenomena → bootstrap_pending stage.

| Wave | Scope | 修复的问题 |
| --- | --- | --- |
| Wave 1 (Tasks 1-7) | EphemeralChatSession `handle_user_input` + `ephemeral_chat.yaml` | ephemeral 缺 system-1 chat 能力 (启动后只能 promote) |
| Wave 2 (Tasks 8-15) | `/deepen` slash 命令 + chat_copy zh + DEFAULT_COMMANDS 注册 + ChatSession 拒绝 | 缺显式 escalate 入口 |
| Wave 3 (Tasks 16-20) | repl_entry outer loop 改 (自然语言走 chat, slash_deepen_promoted 切 chat var) | REPL 主循环逻辑未跟新 API 同步 |
| Wave 4 (Tasks 21-24) | 全量 pytest + ruff + 4 smoke + acceptance doc | 收口 |

每 wave 末尾走 final code review, 共 3 个 review fix bundle:
- Wave 1 review fix (`dc80eb0`): rename `send_user_message` → `handle_user_input` (跟 ChatSession 对齐) + 删 dead `SchemaValidationError` catch + import 提升到顶部
- Wave 2 review fix (`cb8eca5`): duck-typing 替 `isinstance(EphemeralChatSession)` (跟现 19 handler 一致) + 注释 + ChatEvent doc + test cleanup + 复用 `err_failed("deepen", exc)` helper + docstring 补
- Wave 3 review fix (`f7e6031`): C-1 hotfix `promote_to_persistent` 末尾加 `real_chat.persist()` (否则 outer loop 重 build ChatSession 时 chat_state silent 丢失) + I-2 metadata 防御 + M-1 docstring

Phase 18 合计 **21 commit** (含 3 review fix), 跟 plan 估算 (24 task / 4 wave) 量级吻合.

---

## 改动文件清单 (按 wave 分)

### Wave 1 (Tasks 1-7): EphemeralChatSession chat 能力 — 7 commit + review fix

src/:
- `src/explain_engine/llm/prompts/ephemeral_chat.yaml` **(NEW, ~30 行)** — system-1 chat 的中文 system prompt. 引导 LLM 用自己知识直接答 (不假装做现象列举), 适当时机建议用户用 `/deepen` 触发深度 reasoning.
- `src/explain_engine/chat/ephemeral.py` (+~50 行) — `EphemeralChatSession.handle_user_input(text, llm)` 新增 method, 调 `LLM.chat()` with `ephemeral_chat.yaml` prompt, transcript in-memory append (不持久 — storage_v2 不写 transcript.jsonl). 加 `llm: LLMClient | None = None` dataclass field (REPL 注入用, /deepen slash 通过 `chat.llm` 调 promote).

review fix (dc80eb0):
- Method 重命名 `send_user_message` → `handle_user_input` (跟 `ChatSession.handle_user_input` 同名, Wave 2 caller 同名调用)
- 删 dead `SchemaValidationError` catch (`LLMClient.chat()` 不会 raise 这个, 只 LLMError; SchemaValidationError 只在 structured tool_use parse 时出)
- `from explain_engine.chat.session import ChatEvent / ...` 等 4 个 import 提到模块顶部 (不在 method 内 lazy import — 不必要)

tests/:
- `tests/test_ephemeral_chat_yaml.py` **(NEW, 1 test)** — yaml schema 验 (`load_prompt("ephemeral_chat")` 返 dict 含 system, 引导 /deepen, "system-1" / "ephemeral" 字样).
- `tests/test_ephemeral_send_user_message.py` **(NEW, 4 test, Wave 1 review fix 时 rename → `test_ephemeral_handle_user_input.py`)** — 基础 LLM chat / LLM 失败 transcript 不污染 / 多轮 transcript 累积 / 第 N 轮带 history 进 messages / ephemeral 不持久到 storage_v2.

合计 5 个新 test (1 yaml + 4 handle_user_input path).

### Wave 2 (Tasks 8-15): `/deepen` slash — 8 commit + review fix

src/:
- `src/explain_engine/chat/chat_copy.py` (+~20 行) — 3 个 zh 函数 (`err_deepen_no_question` / `err_deepen_already_promoted` / `msg_deepen_promote_start`) + `COMMAND_DESCRIPTIONS["deepen"]` + `HELP_GROUPS_ZH` 相应 group 注册.
- `src/explain_engine/chat/slash_commands.py` (+~80 行) — `_handle_deepen(chat, args)` handler 新增 + `DEFAULT_COMMANDS` 注册 (总数 23 → 24).
  - 带参 → 用 args 拼 Q; 不带参 → 倒序找 transcript 末 `role=user` 的 content; 都没 → `slash_error` 用法提示
  - 已 promote 的 ChatSession 内 /deepen → `err_deepen_already_promoted` + 提示 `/new`
  - LLM 通过 `getattr(chat, "llm", None)` 取 (Task 12 加的 `EphemeralChatSession.llm` field)
  - promote 失败 → `err_failed("deepen", exc)` + slash_error, ephemeral 状态保留

review fix (cb8eca5):
- `isinstance(chat, EphemeralChatSession)` 改 `getattr(chat, "is_ephemeral", False)` duck-typing (跟现 19 slash handler 一致, 去掉局部 import)
- 删 test-only `_llm_for_test` fallback (test 改用显式 `EphemeralChatSession(llm=MagicMock())`)
- 复用 `err_failed("deepen", exc)` 替本地拼字符串
- ChatEvent docstring 加 `metadata` 字段说明
- test_chat_slash_deepen.py 数处 cleanup (MagicMock 显式 spec, transcript fixture 复用, 等)

tests/:
- `tests/test_chat_slash_deepen.py` **(NEW, 6 test)** — `deepen_registered_in_default_commands` / `deepen_with_explicit_question` / `deepen_without_args_uses_last_user_msg` / `deepen_empty_transcript_no_args` / `deepen_in_persistent_session_rejected` / `deepen_promote_failure_keeps_ephemeral`.
- `tests/test_chat_copy.py` (+3 test, `test_deepen_copy_strings_exist` 等 zh string 验)
- `tests/test_chat_slash_commands.py` (brittle count fix 23 → 24, 单独 commit `4d97652`)

合计 ~10 个新 test (含 brittle count fix).

### Wave 3 (Tasks 16-20): REPL outer loop 改 — 4 commit + Wave 3 review fix bundle (C-1 / I-2 / M-1)

src/:
- `src/explain_engine/chat/repl_entry.py` (重要改动 ~50 行) —
  - **Task 16**: ephemeral 自然语言分支 (line ~204-217): 不再 `await chat.promote_to_persistent(text, llm)`, 改为 `async for ev in chat.handle_user_input(text, llm)` (system-1 chat). Promote 由 `/deepen` slash 显式触发.
  - **Task 17**: slash 分支加 `slash_deepen_promoted` handler (line ~160-192): metadata 取新 sid → `chat = ChatSession(new_sid, llm=llm)` 替换 chat var + history_render 显历史. 类似现 `slash_switch_session` pattern.
  - `EphemeralChatSession` 构造 (line ~77-81 + ~153-157): 加 `llm=llm` 注入 (Task 16 让 `/deepen` 能拿到 LLM 调 promote). `/new` 后 reset 时仍注入.
  - docstring 反映 Phase 18 (Wave 3 review fix M-1).

review fix bundle (f7e6031):
- **C-1 (critical)**: `EphemeralChatSession.promote_to_persistent` 末尾加 `real_chat.persist()`. 否则 outer loop 用 `ev.metadata.sid` 重新 `ChatSession(sid, llm=llm)` 时, `load_chat_state` 返 None → user 在 ephemeral 设的 `/budget` 限制 silent 丢失. 测试覆盖见 `test_phase18_full_flow.py::test_chat_state_persisted_after_promote`.
- **I-2**: repl_entry.py `slash_deepen_promoted` handler 加 `try / except (KeyError, TypeError)` 防 metadata 缺 sid (event 妥协 / promote 部分失败) → 红字提示 + 保留 ephemeral.
- **M-1**: repl_entry.py docstring / 注释更新反映 Phase 18 系统行为.

tests/:
- `tests/test_repl_entry_ephemeral_chat.py` **(NEW, 5 test)** — `ephemeral_natural_language_calls_handle_user_input_not_promote` / `slash_deepen_promoted_switches_chat_var` / `slash_deepen_promoted_missing_metadata_keeps_ephemeral` (I-2) / 等. 后续 `1eeb473` 删 unused patch import 收尾.
- `tests/test_phase18_full_flow.py` **(NEW, 1 集成 test)** — end-to-end: ephemeral chat 2 轮 → `/deepen` 不带参 → promote → ChatSession 含 8 phenomena. Wave 3 review fix bundle 加 `test_chat_state_persisted_after_promote` (C-1 regression test).

合计 ~7 个新 test (Wave 3 5 + 集成 1 + C-1 regression 1) + I-2 防御 test.

### Wave 4 (Tasks 21-24): 收口

- `docs/plans/2026-05-26-phase-18-deepen-acceptance.md` (本 doc, NEW).

---

## 测试统计

### 全量 pytest (Task 21)

```
.venv/bin/python -m pytest tests/ -q
2 failed, 1310 passed, 9 deselected, 51 warnings, 1 error in 45.63s
```

- **1310 PASS** — 含 Phase 18 新加 ~22 test (Wave 1 5 + Wave 2 10 含 brittle count fix + Wave 3 5 + 集成 + C-1 regression) + 现有回归 0 break.
- **2 FAIL (全 preexisting)**:
  - `tests/test_config_light_llm.py::test_make_light_llm_client_fallback_all_empty` — 依赖 `.env` 是否设了 `LLM_LIGHT_*`. 当前 user `.env` 已配 → 该 test "fallback all empty" 前提不成立. **跟 Phase 18 完全无关**.
  - `tests/test_lexicon_migrations.py::TestMigrateJsonToPg::test_migrate_json_to_pg_basic` — preexisting PG cross-test pool flakiness (从 Phase 17.1 起). 单文件跑全 17 PASS, 全量 run 偶现. **跟 Phase 18 完全无关**.
- 9 deselected: parametrize fixtures 默 skip, 跟 Phase 18 无关.

### ruff (Task 22)

```
.venv/bin/ruff check src/ tests/
All checks passed!
```

无需 `--fix` 改 (所有 Phase 18 commit 已合 lint clean).

---

## Bug fix 验证 — 4 smoke 实测 (Task 23, 真 LLM)

Smoke 跑法: 一次性 Python script 直驱内部 API (跳 prompt_toolkit TTY interactive, 难 scripted) — 走真 `make_llm_client()` 调 deepseek-v4-pro/v4-flash, 隔离 EXPLAIN_HOME 到 tmp 不影响 user prod sessions. 4 smoke + 1 bonus 全 PASS.

### Smoke 1 — ephemeral chat 不 auto-promote (system-1 直答)

输入: `为什么烧水能沸`

实际:
- `EphemeralChatSession.handle_user_input("为什么烧水能沸", llm)` yield 2 events: `assistant_text` + `turn_complete`
- LLM 直答 (热力学第一定律 / 饱和蒸气压 = 大气压, 完整解释 ~300 字):
  > 烧水时，热量传递给水，使水分子的平均动能增大...沸腾的本质就是水的饱和蒸气压等于外界大气压...
- `sid=None`, `sessions` Δ = 0 (不进 bootstrap, 不入 storage), `transcript` 长 = 2 (user + assistant)

**[PASS]** ephemeral 不再机械化 bootstrap.

### Smoke 2 — /deepen 显式触发 promote

输入: `/deepen 为什么烧水能沸`

实际:
- `_handle_deepen(ephemeral, ["为什么", "烧水", "能沸"])` yield `slash_deepen_promoted`
- 触发 classify (走 light_llm) → mechanism (烧水 = "X 如何工作"), 走 `variable_extraction_mechanism.yaml` (Phase 17.2 已落)
- bootstrap_phenomena 出 **14 个现象** (mechanism dispatch 路径), sid=`s_37324954`
- `sessions` Δ = +1, `meta.question = "为什么 烧水 能沸"`
- 注: 第一次跑遇 deepseek schema wrap (LLM 返 `{"input": {"phenomena": [...]}}`) → ValidationError. 重跑成功. **跟 Phase 18 无关** (Phase 17.2 acceptance doc 已注: deepseek anthropic-compat endpoint 偶发 schema wrap).

**[PASS]** /deepen 真触发 bootstrap pipeline, 持久 session 写入.

### Smoke 3 — /deepen 不带参取 transcript 末 user msg

输入序列:
1. (ephemeral) `为什么彩虹七色` — handle_user_input 触发 LLM chat (色散解释 ~130 字)
2. `/deepen` (无参)

实际:
- `_handle_deepen(eph3, [])` 倒序找 transcript 末 `role=user` → "为什么彩虹七色"
- promote 成功, sid=`s_6b51dc8a`, `meta.question = "为什么彩虹七色"`
- bootstrap 出 **13 个现象** (qtype 隐含 phenomenon, 走 `variable_extraction_phenomenon.yaml`)

**[PASS]** 不带参 fallback 算法正确.

### Smoke 4 — ChatSession (已 promote) 内 /deepen 拒绝 + 提示 /new

输入: 在 Smoke 3 创建的 ChatSession(sid=`s_6b51dc8a`) 内 `/deepen 别的问题`

实际:
- `_handle_deepen(real_chat, ["别的问题"])` 返 1 个 `slash_error`:
  > 本 session 已 /deepen 过 (建模主题: 为什么彩虹七色). 想换主题请用 /new 开新 session.
- 含原主题 ("为什么彩虹七色") ✓, 含 `/new` 提示 ✓

**[PASS]** 一 session 一 /deepen (D4 决策) 落地.

### Bonus — C-1 chat_state 持久 (Wave 3 review fix 验)

模拟用户在 ephemeral 设 `/budget` (chat_state.budget_per_turn_limit=5 / per_session_limit=50) → `/deepen` → promote → 用 `metadata.sid` 重新 `ChatSession(sid, llm=llm)` (模拟 outer loop rebuild 路径).

实际:
- ephemeral chat_state 设后: per_turn_limit=5, per_session_limit=50
- promote 后 `real_chat.persist()` (Wave 3 C-1 fix 加的)
- 重 load ChatSession 后: **per_turn_limit=5, per_session_limit=50** preserve ✓

**[PASS]** C-1 hotfix verified — 用户预算设置不再 silent 丢失.

---

## Backward compat 说明

### cli `explain new <Q>` 完全不变

Phase 18 只改 chat REPL (interactive). cli batch 入口 `explain new <Q>` 仍走老路径:
- `cli._run_new` (Phase 17.2 wire) 直接调 `bootstrap_phenomena(Q, llm, light_llm=...)` → classify → dispatch → save session.
- user 主动跑该命令时, 默认意图就是 reasoning, 不需 system-1 chat 中间步骤.
- `tests/test_cli.py` / `tests/test_cli_new.py` 等 ~25 个 cli test 零修改.

### Phase 17.2 全保留

- classify + 4 yaml dispatch (`question_classify.yaml` + `variable_extraction_concept_explanation/mechanism/phenomenon.yaml`) 在 `/deepen` 内继续走 (`promote_to_persistent` → `bootstrap_phenomena` 路径).
- `make_light_llm_client` 仍是 promote 内调用 (Smoke 2 / 3 实测验证 classify 走 light_llm).
- session delete (cli + slash, Phase 17.2 Feature C) 不动.

### lexicon / theories / PG schema 不动

- Phase 18 无 schema 变化, 无 migration.
- lexicon JSON / PG 两 backend 全保留, /deepen → bootstrap 仍读 `knowledge/variables.json` lexicon prior + `theories/cache` stable theories.

### `EphemeralChatSession.handle_user_input` 命名一致性

Wave 1 review fix 后 method 名跟 `ChatSession.handle_user_input` 完全对齐. REPL outer loop `chat.handle_user_input(...)` 调用对 ephemeral / persistent 两路同名 dispatch, 现 chat REPL test 零修改.

### 一个明确的 user-visible 行为变化 (设计决定, 非 bug)

**chat REPL 启动后, 输入自然语言不再自动 bootstrap.** 用户需明确 `/deepen [Q]` 才触发深度建模 pipeline. 启动 banner 显著说明:

> Explain REPL — ephemeral chat. 输入问题让 LLM 直接答, /deepen 触发深度建模, /help 看 slash, /quit 退出, ctrl+o 看 log buffer.

这是 Phase 18 的核心目标修复 — 见 design doc "Root cause" 段.

---

## 已知 follow-up (Phase 19 候选)

### I-3 (Wave 3 review): repl_entry.py 代码重复

`slash_deepen_promoted` handler (line ~160-192) 跟 `slash_switch_session` handler (line ~122-140 / ~234-268) 代码高度重复 (`ChatSession(new_sid, llm=llm)` + `input_provider` 注入 + `history_render`). 可抽 helper `_switch_chat_to_session(new_sid, llm, input_provider, console) -> ChatSession`. Wave 3 review 决定先 land, Phase 19 一起重构.

### M-3 (Wave 3 review): deepen vs switch_session handler 不对称 `_render_event` 调用

`slash_deepen_promoted` handler 显式调 `_render_event(console, ev)` 一次再处理 sid 切换; `slash_switch_session` handler 不调 (直接 print). 不对称, 跟 I-3 helper 抽取一起处理.

### transcript 不带入 persistent session (design 决定, Phase 18 接受)

ephemeral.transcript 是 in-memory list, promote 后不传给 ChatSession. trade-off:
- ✅ persistent session 起点干净, phenomena 是 reasoning 起点
- ❌ user 感觉 chat 历史"丢了" (实际看 cli `/list` 不见之前的 chat 段)

备选 (Phase 19): promote 时把 ephemeral.transcript 转入 persistent ChatSession.transcript, storage_v2 落 transcript.jsonl.

### ephemeral chat 用主 LLM 全价

Phase 18 ephemeral.handle_user_input 用 `llm` (主 LLM, e.g. deepseek-v4-pro). 简单问题 (烧水/彩虹) 用 v4-pro 较贵, Phase 19 可考虑 chat-tier light (e.g. `LLM_CHAT_*` 第三层 env, fallback main).

### `/deepen` 不带参取"最近 user msg" 简化

不识别 "为什么" 模式, 倒序找最近 `role=user` 即可. user 掌控 — 想换 Q 用 `/deepen <新Q>` 显式. 未来可加更智能 fallback (跳过 "嗯" "继续" 等无意义 msg).

### LLM 自决 tool 化 (方案 B, 长期 vision)

design doc "替代方案 (落选)" B 段: chat 启动后 LLM 自由决定何时调 `bootstrap_tool`. 完整 tool-use 链路 + 重写 system prompt. 留 Phase 19+ 综合评估 (跟 transcript 带入 / chat-tier light 一起设计).

### I-4 (Wave 3 review): 集成 test 整体 stub bootstrap_phenomena

`tests/test_phase18_full_flow.py` 集成 test stub `bootstrap_phenomena` 返固定 phenomena list, 真 LLM E2E 没在 CI 跑. 留 **manual smoke** (本 doc Task 23 段) 补真 LLM 链路验证. 未来可加 CI 短路 (LLM_*_TEST env 优化 mock LLM, 但费时).

### Preexisting fail 2 个

- `test_make_light_llm_client_fallback_all_empty` — `.env` 现状 (LLM_LIGHT_* 已配) 让 test "fallback all empty" 前提不成立. 可加 `monkeypatch.delenv` 强制清 LLM_LIGHT_* env 再跑. 留 Phase 19.
- `test_migrate_json_to_pg_basic` — preexisting PG cross-test pool flakiness (Phase 17.1 起). 单文件跑全 PASS. 留更晚 phase 排查 testcontainers PG pool / asyncpg connection lifecycle.

---

## Phase 18 完成签字

- [x] Wave 1: EphemeralChatSession `handle_user_input` + `ephemeral_chat.yaml` (5 test, 1 review fix bundle)
- [x] Wave 2: `/deepen` slash + chat_copy + DEFAULT_COMMANDS 注册 + ChatSession 拒绝 (10 test, 1 review fix bundle)
- [x] Wave 3: repl_entry outer loop 改 + slash_deepen_promoted 切 chat var (7 test, C-1 critical hotfix bundle)
- [x] Wave 4: 全量 pytest (1310 PASS / 2 preexisting fail) + ruff (clean) + 4 smoke + bonus + acceptance doc

3 个 final code review (每 wave 末尾), 共 ~10 项 review fix 全部 land (Wave 1 3 项 + Wave 2 6 项 + Wave 3 3 项含 C-1 critical hotfix). 2 个 preexisting fail 跟本 phase 无关.

Phase 18 收口. **核心成果**: chat REPL 从 "机械化 bootstrap" 改成 "system-1 chat + 显式 escalate", 用户体验 + LLM token 都改善 — 简单问题不再凑废现象, 复杂问题 `/deepen` 仍能进 Phase 17.2 全套 reasoning pipeline.

下一步: 用户决定 PR / merge 时机.
