# Explain Agent · Phase 2.C 设计文档

> CLI REPL + Followup Graph + 报告质感小改造，让 agent 从"开发者脚本"变成"日常可对话工具"。

**前置：** Phase 2.B 已完工（commit `989f20c`），main graph 端到端跑通 + 强模型叙事 + 数值校验 + 维度报告重写。

**目标：** 让用户在终端常驻 REPL 中：
1. 输入第一句问题 → 跑完整 main graph 拿 6 维归因报告
2. 后续直接追问 → 走轻量 followup（不重跑 6 维），秒级响应
3. 历史 session 可列出、可切换、可清空
4. 报告中股票代码翻译为人类可读、confidence 反映多源印证度

---

## 设计哲学：参考 Claude Code

读完 `/Users/jinziguan/Desktop/Claude-code-architecture-instruction` 后的关键迁移：

1. **斜杠命令本地拦截**：`/` 开头的输入立即本地处理，不进入对话流（不污染上下文）
2. **连续对话默认 followup**：取代"新问题 vs 追问"的显式切换，符合用户直觉
3. **冷启动注入物理世界**：每次 LLM 调用前自动注入当前时间、当前 session 状态（target、time_window、6 维报告摘要）
4. **后台异步落盘**：用 `asyncio.create_task` 在主流程外把 (Q, A) 落到 `explain_followup_history`，不阻塞用户继续提问

**不做的（Phase 2.D/2.E/3+）：**
- 三级 compact 防爆（追问场景 token 增长慢）
- 后台 forked agent 跨 session 长期记忆（Phase 3+）
- MCP / Hooks 扩展机制
- 永久快照（永久快照功能完整版移到 Phase 2.D）

---

## 整体架构

```
┌──────────────────────────────────────────────────────────────┐
│ CLI REPL (prompt_toolkit, 常驻进程, 一次 init)               │
│                                                              │
│  启动:                                                        │
│  - get_engine / get_qdrant_client / get_embedder warm-up    │
│  - 打印最近 5 个 session 列表                                │
│  - 进入对话循环                                              │
│                                                              │
│  对话循环:                                                    │
│  ┌────────────────────────────────────────────────┐         │
│  │ 用户输入 → prompt_toolkit (历史/上下行支持)     │         │
│  └──────────────┬─────────────────────────────────┘         │
│                 ↓                                            │
│  ┌────────────────────────────────────────────────┐         │
│  │ 输入分发 (router)                               │         │
│  │  - "/" 前缀 → slash command 本地处理            │         │
│  │  - 无 session → 自动 /new (走 main graph)       │         │
│  │  - 有 session → 走 followup (轻量 LLM)          │         │
│  └──────────────┬─────────────────────────────────┘         │
└─────────────────┼────────────────────────────────────────────┘
                  ↓
       ┌──────────┴────────────┐
       ↓                       ↓
┌─────────────┐      ┌──────────────────┐
│ main graph  │      │ followup (inline)│
│ (Phase 2.B) │      │  load_session    │
│ 6 维归因     │      │  + LLM 单调用    │
│ ~10 min     │      │  + 异步落盘      │
└──────┬──────┘      │  ~5-10s          │
       │              └──────────┬───────┘
       ↓                         ↓
       MySQL: explain_session + explain_evidence_tree +
              explain_followup_history
```

---

## 7 个核心设计决策

### 1. REPL 实现：prompt_toolkit

- 依赖 `prompt_toolkit`（新增 ~5MB 包）
- 支持：命令历史（↑↓）、多行输入（Esc+Enter）、Ctrl+C 优雅、自动补全 slash command
- 常驻进程：一次 init `embedder / qdrant_client / engines / llm`，避免每次问题 ~10s 启动开销

### 2. 输入分发逻辑

```python
def dispatch(input: str) -> Action:
    if input.startswith("/"):
        return parse_slash_command(input)  # 本地处理, 不进 LLM
    if state.current_session is None:
        return RunMainGraph(input)         # 第一句话自动 /new
    return RunFollowup(input, state.current_session)
```

### Slash 命令列表

| 命令 | 作用 |
|---|---|
| `/new <问题>` | 显式开新 session, 走 main graph |
| `/sessions` | 列出最近 10 个 session（id, target, date, confidence, 追问次数） |
| `/load <session_id>` | 切到指定 session, 接下来的追问基于它 |
| `/clear` | 清空当前 session 的追问历史（保留 6 维报告）|
| `/help` | 显示命令帮助 |
| `/quit` / `/exit` / `Ctrl+D` | 退出 REPL |

### 3. Followup LLM 输入：中等 ~10K token

每次 followup 注入：

```python
context = {
    "target": session.target,
    "time_window": session.time_window,
    "asked_at": now,                          # 仿 Claude Code 时间注入
    "narrative": session.narrative,
    "narrative_claims": session.narrative_claims,
    "dimension_reports": session.dimension_reports,  # 6 维完整报告
    "top_citations": session.citations[:20],         # snippet 截断 ~200 字
    "followup_history": last_5_qa,
    "question": current_question,
}
```

Strong LLM 直接基于此回答，**不做检索 / 不重跑 worker**。

### 4. Followup 实现形态：inline async 函数

```python
async def run_followup(
    session_id: str,
    question: str,
    llm: LLMClient,
    engine: Engine,
) -> FollowupResult:
    # 1. 加载 session
    session = await load_session(engine, session_id)
    # 2. 加载最近追问历史
    history = await load_followup_history(engine, session_id, limit=5)
    # 3. 构造 prompt
    answer = llm.chat(system=FOLLOWUP_SYSTEM, user=_build_user(session, history, question), max_tokens=2000)
    # 4. 后台落盘 (不阻塞用户继续提问)
    asyncio.create_task(_persist_followup(engine, session_id, question, answer))
    return FollowupResult(answer=answer, session_id=session_id)
```

- 不用 LangGraph（单步调用 graph 是 overkill）
- 后台落盘失败也不阻塞用户（容错记 errors 但不抛）

### 5. 启动行为：c（启动列表 + 不自动恢复）

启动 REPL 时：

```
$ explain
✓ Embedder warm-up (1.2s)
✓ MySQL / ClickHouse / Qdrant connected

最近 session（输入 /load <id> 继续追问，或直接提新问题）:
  1. s_869d3239  2026-05-11 19:10  半导体    confidence=low   6 维 + 0 追问
  2. s_4b6ecfd3  2026-05-11 15:00  半导体    confidence=high  6 维 + 0 追问
  3. s_xxxxx     2026-05-10 11:00  光伏      confidence=med   6 维 + 5 追问

explain> _
```

第一句话默认走 `/new`（除非用户用 `/load` 显式切换）。

### 6. 永久快照：移出 Phase 2.C

调研发现：178 条新闻全部 `snapshot_id IS NULL`，`explain_snapshot_blob` 表 0 条记录。snapshot 功能 schema 准备好了但完全没实现。

完整实现 ~3-4h（改 ingest pipeline + 历史回填），超出 Phase 2.C 预算。**移到 Phase 2.D**（同期改 ingest pipeline 时搭顺风车）。

### 7. B.2 symbol_id 翻译 + B.3 confidence 多源化

#### B.2 股票代码翻译

`ClickHouseMarketAdapter.query()` 改造：
- 多 join 一次 `quant_data.stock_symbol` 拿 (symbol, name)
- snippet 拼接：从 `symbol_id=2332 涨跌=92.52%` 改为 `长电科技(300661) 涨跌=92.52%`
- ~30 行代码 + 1 个单测保护

#### B.3 confidence 多源化 prompt

`NARRATIVE_SYSTEM` 末尾加一句：

```
鼓励：claims 引用的 evidence 来自不同 source_type（news / market_data /
capital_flow / policy 等），多源印证比单源更可信。
```

不改 `_estimate_overall_confidence` 逻辑（Phase 2.B 已定义为 cited_count × source_type 多样性）。仅引导强模型主动跨源引用。

---

## State Schema 变更

REPL 状态（内存）：

```python
@dataclass
class ReplState:
    current_session_id: str | None = None
    current_session: dict | None = None  # 加载后缓存
    followup_history: list[dict] = field(default_factory=list)
```

数据库 schema 无需新增表（Phase 1 已建 `explain_followup_history`）。

`explain_followup_history` 现有字段：`followup_id, session_id, question, answer, intent, created_at`。Phase 2.C 不动 schema。

---

## 测试策略

### 单测覆盖

- `tests/test_cli_repl.py`：slash command 解析、输入分发逻辑（mock prompt_toolkit input）
- `tests/test_followup.py`：load_session / followup / 后台落盘的单测（mock engine + LLM）
- `tests/test_clickhouse_market_adapter.py`：扩展 1 个用例确认 snippet 含股票名

### 集成测试

- `tests/test_repl_integration.py`：模拟用户输入序列 `["新问题", "追问1", "/new", "新问题2", "/sessions", "/quit"]`，验证状态流转正确
- 用 mock LLM + mock engine

### 端到端 smoke

`scripts/run_repl_smoke.py`：
- 启动 REPL（带 prompt 输入流注入 4-5 个真实问题）
- 验证：
  - 首句走 main graph 输出完整报告
  - 追问 ≤10s 响应
  - `/sessions` 列出含本次新 session
  - 后台 `explain_followup_history` 写入成功

---

## 失败模式与回退

- **prompt_toolkit 在某些终端崩溃**：回退到标准 `input()`，明确告诉用户失去历史/补全功能
- **session 加载失败**：提示用户 `/sessions` 重选，不影响 REPL 主循环
- **Followup LLM 调用失败**：3 次重试（复用 Phase 2.B 的 `_call_with_retry`），全部失败提示用户换问法
- **后台落盘失败**：记 errors 但不阻塞，下次 `/sessions` 仍能看到 session（仅没记到追问历史）

---

## 任务总览

| # | 任务 | 预计 |
|---|---|---|
| 1 | 添加 prompt_toolkit 依赖 + REPL 骨架 | 30 min |
| 2 | Slash command 解析器 + dispatch 逻辑 | 1 h |
| 3 | `/sessions` + `/load` 实现（含 MySQL 加载） | 1 h |
| 4 | `/new` + `/clear` + `/help` + `/quit` 实现 | 0.5 h |
| 5 | Followup async 函数（load + LLM + 异步落盘） | 1.5 h |
| 6 | REPL 主循环装配（启动列表 + 输入分发） | 1 h |
| 7 | B.2 ClickHouseMarketAdapter 股票名翻译 | 0.5 h |
| 8 | B.3 narrative prompt 多源化引导 | 0.3 h |
| 9 | 集成测试 + REPL smoke 脚本 | 1.2 h |

**合计：约 7.5 小时纯开发，预计 1-2 个工作日完成。**

---

## 完成后

Phase 2.C 完成后的产出：

- ✅ CLI REPL（prompt_toolkit + slash command + 常驻进程）
- ✅ Followup 链路（inline async + 轻量 strong LLM + 后台落盘）
- ✅ Session 管理（`/sessions` `/load` `/clear`）
- ✅ 股票代码人类可读 + narrative 鼓励多源印证
- ✅ 完整单测覆盖 + REPL smoke

**下一步：进入 Phase 2.D 实施计划**，重点是：

- Lazy News Ingest（用户问到的板块语料不足时按需采集）
- fan_out 性能优化（reduce max_rounds + 并发度提升）
- 永久快照（snapshot_id 实现）
- connection_explorer 节点骨架（Phase 3 前置，让 agent 在 6 维归因外主动发散）

待 Phase 2.C 跑通且用户日常使用一段时间后，再做 Phase 2.D 的 brainstorm（积累真实痛点）。
