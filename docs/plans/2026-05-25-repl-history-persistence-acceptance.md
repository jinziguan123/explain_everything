# Phase 16.2: Per-Session REPL History Persistence Acceptance

**Date**: 2026-05-25
**Status**: 全 64 plan task 完成, 9 Wave 全绿, 待手动 smoke 验证
**Branch**: dev (尚未 push)
**Related**:
- Design: [docs/plans/2026-05-25-repl-history-persistence-design.md](2026-05-25-repl-history-persistence-design.md)
- Plan: [docs/plans/2026-05-25-repl-history-persistence-plan.md](2026-05-25-repl-history-persistence-plan.md)

---

## 1. 实施总结

| 维度 | 数据 |
|---|---|
| Plan task | 64 task (10 Wave) |
| 实际 commit | 69 个 Phase 16.2 commit (含 design+plan+1 hotfix+4 e2e+5 内部修复) |
| 自动化 test 数 | 1135 PASS (1069 既有 + 66 Phase 16.2 新) / 9 deselected (embedding marker) |
| Ruff | 全绿 (src/ + tests/) |
| 既有 test 回归 | 0 |
| 时间 | ~2 hour 工作量 (估 13 hr, 实际因 subagent batch 大幅加速) |

### Wave 完成情况

| Wave | 目标 | Task | Commit |
|---|---|---|---|
| 1 | storage append/load + 降级 | 7/7 | `0c47b39..d787e98` |
| 2 | snapshot + delta 纯函数 | 9/9 | `4b1b4ee..079ead9` |
| 3 | dispatcher wrapper | 9/9 | `e6bb423..d7c0c67` |
| 4 | ChatEvent.metadata + 2 handler | 5/5 (+1 hotfix) | `856d65a..e457fb2` |
| 5 | /history slash 命令 | 15/15 | `2e40990..0dc7766` |
| 6 | process_user_turn llm_turn | 4/4 | `b663ae8..2834f7b` |
| 7 | resume banner render | 10/10 | `207895e..5a8c9d7` |
| 8 | chat_copy 文案 | 3/3 | `0f53ccd..31e983e` |
| 9 | e2e smoke | 4/4 | `3c79102..27857a4` |
| 10 | acceptance doc | 1/1 | (本文档) |

### 关键 commit hash

- Design doc: `5607561`
- Plan doc: `bc09af8`
- Acceptance doc: 本文档 commit

---

## 2. 自动化 Verify

### 全量 pytest

```bash
$ .venv/bin/python -m pytest tests/ -q
1135 passed, 9 deselected, 51 warnings in 3.17s
```

- 既有 1069 test 零回归
- 新增 66 test (Phase 16.2 各 Wave 加的, 含 e2e):
  - test_persistence_storage_v2.py: +7 (TestReplHistoryAppend + TestReplHistoryLoad)
  - test_chat_slash_commands.py: +24 (TestSnapshotAndDelta 9 + TestWrapHandler 8 + TestHandleHistory 14 + cf alias 改 1 + register 1 + history copy 等)
  - test_chat_copy.py: +4 (TestHistoryCopy)
  - test_chat_session.py: +6 (TestChatEventMetadata 2 + TestLLMTurnHistory 4)
  - test_chat_repl_history.py: +13 (TestRenderRecentHistory 8 + TestResumeBannerIntegration 1 + TestE2EReplHistory 4)
- 9 deselected: `@pytest.mark.embedding` (需 BGE-M3 4.3 GB model, 跟本 phase 无关)
- 51 warnings: 全是既有 DeprecationWarning (SessionStore directory param), 非本 phase 引入

### Ruff

```bash
$ .venv/bin/ruff check src/ tests/
All checks passed!
```

零警告. 期间触发 1 次 `--fix` 自动修 `datetime.timezone.utc → datetime.UTC` (Python 3.11+ 推荐 alias, Task 6.1 ruff hook 应用).

---

## 3. 手动 Smoke 场景 (待用户验证)

按 design doc §6.1 / 用户验收口径设的 4 场景, 跑真 chat REPL 走完整路径.

### Smoke 1: /predict 写 history + 重启后 banner 显

**步骤**:
1. `.venv/bin/python -m explain_engine.cli chat`
2. 自然语言输 "为什么 X 跟 Y 有关?" 让 chat bootstrap session
3. `/compress` → `/run`
4. `/predict` 输 intervention 文本 (>80 字, 例 "假设 LeCun JEPA 真正解决了 c_001 结构先验内化深度...")
5. `Ctrl+D` 退 chat
6. 重进 `.venv/bin/python -m explain_engine.cli chat`
7. `/resume` 选刚才 sid

**预期**: banner 含 `─── 最近 N 条操作 (旧 → 新) ───` 段, 内列 `/compress` `/run` `/predict` 等行. `/predict` 行下方有 `假设: 假设 LeCun JEPA 真...` (前 80 字截断 + `...`). 底部含 `输 /history 看完整历史`.

### Smoke 2: /history 完整 intervention

**步骤** (接 Smoke 1 之后): `/history`

**预期**: 输出含 `本 session 共 N 条历史记录, 显示最近 N 条 (旧 → 新):`. 找到 `/predict` 段, 验 `假设:` 后 intervention 完整 (不截 80 字, 全文显示).

### Smoke 3: /history --type 过滤

**步骤** (接 Smoke 2): `/history --type llm_turn`

**预期**: 输出仅含自然语言对话回合 (`你: ...` `Claude: ...` 行), 不含 `/compress` 等 slash. 若无 llm_turn (用户没自然对话只 slash), 输出空 history 友好提示.

### Smoke 4: 老 session resume 友好

**步骤**:
1. 找一个 Phase 16 之前创的老 session (无 `repl_history.jsonl`):
   ```bash
   ls ~/.explain/projects/*/sessions/s_*/  # 看哪个 sid 目录无 repl_history.jsonl
   ```
2. `.venv/bin/python -m explain_engine.cli chat` → `/resume` 选该老 sid
3. 看 banner

**预期**: banner 显 `(本 session 无历史操作记录)` (非 crash / 非 noise). 后续操作开始自动累 history, 下次 resume 该 session 会显新积的记录.

---

## 4. 关键设计落地确认

跟 design doc §4-7 五大段对照:

### §4 Architecture ✅

- [x] `storage_v2.append_repl_history / load_repl_history` (Wave 1)
- [x] `_wrap_handler` 中央 dispatcher (Wave 3, slash_commands.py)
- [x] `_handle_history` 新 slash 命令 (Wave 5)
- [x] resume 入口拼 history 段 (Wave 7 Task 7.10, **改在 cli.py 而非 repl_entry.py** — subagent 调整, plan 假设位置不准)
- [x] llm_turn 写入路径 (Wave 6, handle_user_input async generator 末尾)
- [x] `ChatEvent.metadata` optional 字段 (Wave 4)

### §5 Schema ✅

- [x] 3 type entry (slash 无 intervention / slash 含 intervention / llm_turn)
- [x] `error` 字段 (handler 异常分支)
- [x] timestamp ISO 8601 + UTC (Task 6.1 ruff 改用 `datetime.UTC` alias)
- [x] Append 原子性 (单 write < PIPE_BUF, 不需 atomic rename)
- [x] Load + 损坏行降级 (silent skip + rate-limited warn 5+1)

### §6 用户接触面 ✅

- [x] Banner 渲染 (Wave 7 render_recent_history, 旧→新, intervention 80 字截 + llm_turn 60 字截)
- [x] `/history` 命令 (Wave 5: --limit / --type / --all / 错误信息)
- [x] chat_copy.py 文案常量 (Wave 8: BANNER_HISTORY_* + HISTORY_* + err_history_*)

### §7 Error Handling ✅

- [x] 写入失败: silent + log warn (Task 3.6)
- [x] 读取失败 (missing/corrupt/blank): 各对应 (Task 1.4/1.5/1.6)
- [x] Snapshot 失败: 返 None + `(变化未知)` (Task 2.8 + 3.7)
- [x] Handler 异常: 写带 error 的 entry, 再 raise (Task 3.5)
- [x] SIGINT: 不写, 直 propagate (Task 3.8 + 6.3)
- [x] llm_turn 中断: 同 SIGINT, 不写 partial (Task 6.2 + 6.3)
- [x] /history 参数边界: 6 类 reject (Task 5.7-5.11)
- [x] 老 session 兼容: 空 history 友好提示 (Task 5.12 + 7.7 + 9.4)

---

## 5. 风险点 Retro (对比 plan §风险)

| Plan 风险 | 实际发生 | 处理 |
|---|---|---|
| `session.py` 没有清晰 process_user_turn 入口 | 实际是 `handle_user_input` async generator (非 return value), Wave 6 需在 async for 循环中收集 events | 改 impl: 累 `_assistant_chunks` 列表, 末尾 join. plan 估计错入口名但精神一致, 改动量小 |
| `_wrap_handler` 应用到 21 handler 某 handler 测试挂 | `test_cf_alias_in_default_commands` identity 比较挂 | Wave 3 subagent 加 `wrapped.__wrapped__ = handler` + 改 test 用 underlying 比较 |
| 60/80 字截断对中文 char vs byte | 用 `len()` (Python char 数, 中英 1 单位), 视觉宽差异接受 (banner 不强求 grid 对齐) | 落地按 design |
| /history 走 wrapper 自身产 entry, 测试干扰 | 测试用 `_handle_history(chat, [])` raw 跳 wrapper | Wave 5 test 全部用 raw, e2e Wave 9 接受 wrapper 副作用 |
| 既有 test 期待 ChatEvent 2 字段 | 加 metadata=None 后 dataclass equality 不挂 (现有 test 没用 dataclass `==`) | 无回归 |
| 64 task 串行 1 天紧 | 实际 ~2 hr (主 session + 3 subagent batch + Wave 8 主 session 平铺) | 大幅低于估时, subagent batch 模式高效 |

### 新增 deviation (plan 未预期)

1. **Task 4.3 hotfix** — sid `s_md40003` 含 'm' 非 hex, ValidatorError 挂. 改 `s_dead0004`. 这是我主 session 写 test 时 sid 没按 `^s_[0-9a-f]{8}$` regex 选. 加了 1 个 hotfix commit.
2. **Wave 7 Task 7.10 scope 调整** — plan 假设 resume 入口在 `chat/repl_entry.py`, 实际在 `cli.py` `_run_chat_repl_async` (~line 1047 `Loaded session {sid}` print 后). subagent 主动 grep 发现 + 改 commit scope `cli ·` 而非 `chat/repl_entry ·`. 准确反映改动位置.
3. **Wave 5 `test_total_count_is_21` → `_is_22`** — DEFAULT_COMMANDS 加 /history 后既有 count test 名 + assert 需要更新, subagent Wave 5.15 顺手改 + commit msg 注明.

---

## 6. 后续 Phase 17+ 候选 (本 hotfix 之外)

1. **JEPA 启示 (d) Hierarchical L0/L1/L2 时间尺度对齐重设** — defer 自 Phase 16
2. **Theory inject 扩到 chat 全 path** — 当前仅 bootstrap 阶段 inject, 让自然语言追问也 inject
3. **Multi-Perspective Runtime** (技术设计 v2 §7)
4. **Cognitive Simulation / Rollout** (技术设计 v2 §8)
5. **History 反查驱动 theory recompute** — `/history` 数据可喂 cross-session pattern detection (跟 Phase 16 theory 协同)
6. **History 跨 session aggregation** — project-level "全局操作流" (跟 Phase 10 lexicon 同维度)

---

## 7. 总结 — Phase 16.2 评估

**核心成就**:
- 完整端到端 history 链路: 写入 (slash wrapper + llm_turn) → 存盘 (sidecar jsonl) → 读取 (load + 降级) → 展示 (banner + /history)
- 21 个现有 slash handler 零侵入 (仅 2 个 handler 加 1 行 metadata)
- 1069 既有 test 零回归
- 完整 TDD 节奏 — 每 task red→green→commit

**用户价值**:
- `/predict` `/counterfactual` 的 intervention text 终于持久化 (之前重启即丢)
- Resume 后立刻能看到上次操作流, 不再"只看到 graph state, 不知怎么到的"
- 复现实验有可靠记录 (跟 Phase 16 stable theory 可重复性追求一致)

**待跑**:
- Smoke 1-4 手动验证 (上面 §3 列了步骤)
- merge dev → master (Phase 16 + 16.2 一并; 当前已 25+ commit 待 push)
