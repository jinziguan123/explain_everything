# Phase 10 Persistent World Model — Acceptance Checklist

> Design: [2026-05-18-phase10-persistent-world-model-design.md](2026-05-18-phase10-persistent-world-model-design.md)
> Plan: [2026-05-18-phase10-persistent-world-model-plan.md](2026-05-18-phase10-persistent-world-model-plan.md)

需 LLM key + 真终端. 7 步手测验 cross-session var lexicon 端到端流程.

## Setup

1. HEAD = Wave 5 commit 或之后
2. `.venv/bin/python -m pytest -x` 应全 PASS (~728)
3. `.env` 含 LLM 配置 (LLM_PROTOCOL / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL)
4. 清测试 project lexicon (保险):
   ```bash
   rm -rf ~/.explain/projects/<project_id>/knowledge/
   ```
   `<project_id>` = sha8 of cwd, 实测 `307f226e` for `/Users/jinziguan/Desktop/explain_everything`

## Smoke Steps

### S1: Session 1 — bootstrap 无 lexicon (空库)

```bash
.venv/bin/python -m explain_engine new "为什么年轻人不消费" --no-chat
```

**预期**:
- bootstrap 正常 LLM 调用 + HITL review
- LLM prompt 无 prior section (lexicon 空)
- Session 保存, `[green]Session s_xxx 已保存[/green]`

**失败模式**:
- LLM prompt 含 "已知 abstractions" 段 (说明 lexicon 不空, 没清干净)
- Bootstrap 撞 lexicon load 错误

### S2: 跑 compress 触发 flush

```bash
.venv/bin/python -m explain_engine compress <sid1>
```

**预期**:
- HITL 2 review_insights 走通
- Stage 转 done, session 保存
- Console print `[INFO] N var 写入 lexicon` (N ≥ 1, 一般 1-3)

**失败模式**:
- `[yellow]lexicon flush 失败 (非关键)[/yellow]` (查 stderr 看具体异常)
- N=0 (说明 graph 无 active L1/L2 — 可能 HITL 全 drop)

### S3: 验 lexicon 文件创建

```bash
cat ~/.explain/projects/307f226e/knowledge/variables.json
.venv/bin/python -m explain_engine lexicon
```

**预期**:
- JSON 含 1+ var entry: `global_id` / `name` / `fitness.reuse_count=1` / `source_sessions=[sid1]` / `canonical_mechanism`
- `explain lexicon` Rich Table 显示同样 vars (含 ID / 名称 / Level / reuse / avg_ess / last_seen)

**失败模式**:
- JSON 不存在 (Wave 4 flush 没真触发)
- Table 空 (`暂无变量` 提示, 说明 S2 flush 失败)

### S4: 验 dump-json + top-k flags

```bash
.venv/bin/python -m explain_engine lexicon --dump-json | head -20
.venv/bin/python -m explain_engine lexicon --top-k 1
```

**预期**:
- `--dump-json` 输 raw JSON, 可被 `jq` parse
- `--top-k 1` 仅显 1 行 (fitness 最高的 var)

### S5: Session 2 — bootstrap 看 lexicon prior

```bash
.venv/bin/python -m explain_engine new "为什么年轻人不结婚" --no-chat
```

**预期**:
- LLM prompt 含 "已知 abstractions" prior section (列 S1+S2 promoted var)
- LLM 在生成 phenomena 时**可能**引用 S1 abstractions (LLM 自主决定, 非强制)

**失败模式**:
- LLM prompt 不含 prior section (`lexicon=None` 路径未触发)
- LLM 报 OOC / token limit error (说明 lexicon 太大, Top-K=20 cap 失效)

**Verify prompt 含 prior**: 临时加 print debug 到 `bootstrap.py` 或开 LLM HTTP log:
```bash
LLM_DEBUG=1 .venv/bin/python -m explain_engine new "..." --no-chat
```

### S6: chat 路径 flush (Wave 4 review I-1 cover)

```bash
.venv/bin/python -m explain_engine new "为什么年轻人不储蓄"  # 默认进 chat
```

进 chat REPL 后:
- 跟 LLM 对话几轮, LLM 可能 expand 出新 L1/L2
- `/quit` 正常退出

**预期**:
- Chat aclose 后 console print `[dim]N var 写入 lexicon[/dim]`
- `.venv/bin/python -m explain_engine lexicon` 看 var 列表已增长

**失败模式**:
- Chat 退出后无 flush log → flush 未触发 (Wave 4 placement bug)
- `_session` 访问失败 (Wave 4 reviewer M-2 flag)

**注意 Wave 4 I-1**: subagent 把 flush 放 `finally` 之前 (仅 happy path). 用户 ctrl+c 强中断 chat 时 lexicon 不会 flush — 这是 expected (不完美但 safe). 用 `/quit` 正常退则 flush 触发.

### S7: 跨 session reuse_count++ 验证

跑完 S2 + S5 (compress session 2) 后, `explain lexicon` 看是否有重叠 var 的 `reuse_count=2`.

**注意**: 同 name + 同 canonical_mech 才 merge (Wave 2 conservative split). LLM 生 canonical_mech 在两 session 可能略有不同 → 不 merge, 算两个 var. 这是 expected, Candidate E embedding 解决.

**预期** (best-effort):
- 至少 1 个 var 跨 session merge (same name + same canonical_mech LLM 巧合)
- 否则 lexicon 含独立 entries — 不算 failure, 只是 demo split 行为

## Pass/Fail 标准

- S1-S4: **必过** (核心 flow)
- S5-S6: **应过** (集成 path)
- S7: **best-effort** (依赖 LLM 输出稳定性)

任一 必过/应过 step 不过 → 写 issue 含具体 step + 预期 vs 实际.

## Wave 4 review 已知 trade-off (acknowledged)

| Issue | 说明 |
|---|---|
| I-1 | chat aclose 后 flush 在 `finally` 前 — 异常路径 (e.g. ctrl+c) 不触发. happy path (`/quit`) 触发. acceptable trade-off |
| I-2 | `test_resume_insight_pending_skips_llm` await_count 从 0 改 1 — Wave 4 加 flush 后 canonical_mech 1 次 LLM call. 测试名略 misleading (resume 仍 skip score, 只是多了 flush 的 1 call) |
| M-1 | `except Exception` cli 层 broad. 失败时 console 仅显 "lexicon flush 失败" — 难诊断. 建议 future fold `type(exc).__name__` |
| M-3 | `_run_new` 内 `storage = StorageV2()` 单用 `knowledge_dir()` — 可 inline. trivial |
| M-5 | chat-aclose flush 无 auto test (prompt_toolkit Application 需 tty) — 本 acceptance S6 cover |

## Pass 后

- 把本文件状态从 "draft" 改为 "passed" (顶头加一行)
- final code review + finishing-a-development-branch

## 长期 (Phase 11+) follow-up

- **Phase 11 启动**: motif detection on cross-session graph. lexicon 是输入数据层. 评估是否引入 Neo4j (图 pattern matching) — Phase 10 没需要.
- **Candidate E**: Variable Embedding + semantic anchoring. 解决 canonical_mechanism 漂移导致的 split. 可能 fold pgvector.
- **fitness avg 算法重评**: Phase 10 用 `node.activation` / `node.stability` 作 proxy. 真值应取 `state.last_acceptance_report.per_l1` (essentialness/consistency). 留 Phase 10.x.

## 参考

- Wave 1-4 commits: `0f90587` (CRUD) / `c199bd7` (flush+mech) + `4f57fab` (review fix) / `935a0c4` (bootstrap+Top-K) / `04da599` (cli 集成)
- Design: [docs/plans/2026-05-18-phase10-persistent-world-model-design.md](2026-05-18-phase10-persistent-world-model-design.md)
- Plan: [docs/plans/2026-05-18-phase10-persistent-world-model-plan.md](2026-05-18-phase10-persistent-world-model-plan.md)
