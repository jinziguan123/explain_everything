# Phase 11 — REPL Unification (Ephemeral Session + All-Slash UX) Design

> 顶层设计参考: [最终哲学以及技术实现相关设计.md](../../最终哲学以及技术实现相关设计.md) §2.4 / §5.3 / §10
> 上一 phase: [Phase 10 Persistent World Model](2026-05-18-phase10-persistent-world-model-design.md)
> 当前 HEAD: `fce5013` (728 PASS, ruff 0)

**日期**: 2026-05-18
**分支**: `dev`

---

## 0. TL;DR

**Phase 11 主题**: `explain` 默认进 prompt_toolkit REPL (无 subcommand 时), 启动 ephemeral chat session (in-memory, 不持久化). 用户**首句自然语言** → 自动 implicit `/new` 走 bootstrap+HITL+持久化. **12 个 typer cmd 全部做 slash**. 所有 slash **无 explicit 参数**, 需参数的走 `chat.input_provider` sequential prompt. 加 `/budget` config 流 (取代 cli flag). 保留 `explain <subcommand>` cli backward compat.

**Wave 0 bug fix**: deepseek-v4-pro 400 Bad Request 用户报告 (compress 频繁撞) — forced tool_choice fallback 后 LLM auto 模式返 free text, Pydantic 嫌不合 schema. Fix: prompt 加 JSON-mode 强约束 + retry.

**总: 7 wave, +50-70 test, ~2-3 周.**

---

## 1. 背景与动机

### 1.1 当前 CLI 状态

`explain` 是 Typer subcommand router. 12 个 cmd: new / show / compress / run / check / predict / counterfactual / rescore / list / chat / migrate / lexicon. 每个 cmd 独立进程, sid 通过 arg 传入. 用户工作流是:

```bash
explain new "为什么 X"        # bootstrap + HITL + 默认进 chat (Wave 4)
# 退出 chat 后:
explain show <sid>
explain compress <sid>       # HITL review_insights, 阻塞 stdin
explain check <sid>
explain predict <sid> "..."
# ...
```

每次 cmd 启动 Python + load typer + import 重 module ≈ 200ms 启动开销. 跨 cmd 没共享上下文, sid 全手敲.

### 1.2 用户反馈的核心问题

> "我希望用户能够通过 explain 命令来启动 REPL 界面, 进去之后新建一个 chat 的 session (不持久化保存), 如果此时用户第一次发起了对话, 则默认调用 new 命令 (持久化保存这个 session). explain 命令行的各种 Commands 也做成 chat 中的 slash. 这样整个交互都能在 REPL 中实现, 而不是命令行一次一次敲命令."

诉求拆解:
1. `explain` 一键进 REPL (无 subcommand)
2. Ephemeral session 概念 (in-memory, 首句对话才持久化)
3. 12 typer cmd 全部 slash 化
4. cli mode 保留 (调试 / 脚本场景)

### 1.3 设计哲学落地度

设计 §2.4 "认知不是搜索":
```
question
→ graph formation
→ mechanism stabilization
→ abstraction compression
→ insight emergence
```

REPL 一站式让这条流水线在**一个 session** 内连续走完, 不被 cli 进程边界打断. Phase 9 chat REPL 是雏形, Phase 11 是完整 unification.

### 1.4 Wave 0 bug 用户报告 (独立)

用户原话:
> "我在 compress 调用 LLM 的时候经常会遇到 400 的报错: HTTP Request: POST https://api.deepseek.com/anthropic/v1/messages 'HTTP/1.1 400 Bad Request' Forced tool_choice rejected by model (deepseek-v4-pro); retrying with auto. Note: LLM may return free text instead of structured output; Pydantic validation downstream will catch malformed responses. 不会稳定触发, 但是概率较高, 请顺便修复一下."

诊断: Phase 9 session 已加 forced→auto fallback (`414850a`), 但 auto 模式 deepseek 偶尔返 free text, 下游 Pydantic 报错. Phase 11 Wave 0 顺修 — prompt 加 JSON-mode 强约束 + retry 兜底.

---

## 2. Scope

### 2.1 本设计内

- **Wave 0**: deepseek-v4-pro 400 bug fix (prompt JSON 强约束 + retry)
- `explain` cli 入口重构 (默认 REPL / `explain <subcommand>` 仍 typer)
- `EphemeralChatSession` dataclass + lifecycle (in-memory → promote_to_persistent)
- 首句自然语言 → implicit `/new` 流
- 10 新 slash handler (`/compress` / `/run` / `/check` / `/predict` / `/counterfactual` / `/rescore` / `/list` / `/lexicon` / `/migrate` / `/cf` alias)
- 所有 slash 无 explicit 参数, 需参数走 `chat.input_provider` interactive prompt
- `/budget` config 流 (取代 cli flag)
- HITL `review_phenomena_async` / `review_insights_async` (复用 input_provider)
- 老 typer cli 12 cmd 保持 0 break

### 2.2 本设计外 (YAGNI)

- 跨 chat 调度 (同时多 chat 实例 — single chat at a time OK)
- slash command 参数 tab 自动补全 (prompt_toolkit 已含 cmd name 联想, 参数 stage 不补)
- `/run --watch` 等长 flag 模式
- 远程存储 (Phase 11 不动, Candidate E / Phase 12 评估)
- 多 user 隔离 (项目 single-user)
- Theory Formation (留 Phase 12)

---

## 3. 总体方案

### 3.1 数据流

```
$ explain                            (无 subcommand)
    ↓
typer callback (invoke_without_command=True)
    ↓
enter_repl_async()
    ↓
EphemeralChatSession (in-memory, no sid)
    ↓ chat.input_provider 暴露给 slash
prompt_toolkit REPL while loop
    ├─ user 输 /<slash>
    │   ├─ cross-session slash (/list, /lexicon, /migrate) → 直接执行
    │   ├─ single-session slash 但 ephemeral → reject + 提示
    │   ├─ needs-input slash (/new, /predict, /counterfactual)
    │   │   → await chat.input_provider("...") 收参数 → 执行
    │   └─ config slash (/budget) → sequential prompt 收多字段 → 更新 chat_state
    └─ user 输自然语言
        → trigger implicit /new flow:
            1. await bootstrap_phenomena(input, llm, lexicon=...)
            2. await review_phenomena_async(phenomena, input_provider)
            3. Session.save(...) (持久化)
            4. promote ephemeral → real ChatSession(sid, llm)
            5. REPL 继续 (chat 模式 LLM tool_use loop)

$ explain <subcommand> [args]        (老 cli 路径)
    ↓
typer subcommand → cli.py 现有 logic (0 改动)
```

### 3.2 关键不变量

1. **`explain <subcommand>` 完全 backward compat** — 12 个 typer cmd 行为 0 改动
2. **Ephemeral session 永不写盘** — sid=None, 不调 storage.save 直到 promote
3. **slash 无 explicit 参数** — 全 18 个 slash 接 `(chat, args=[])` 但 args 总空 (defensive); 需参数走 input_provider
4. **promote 失败回滚** — bootstrap LLM 错 / HITL drop all / user cancel → 保持 ephemeral, 不留半 session 文件
5. **`/budget` 实时改 chat_state** — ephemeral session 也 OK (改 in-memory chat_state, promote 时 copy 过去)
6. **default budget 10/50** — Phase 9 ChatStateDict 默认, 启动时**不**靠 cli flag

---

## 4. Entry Point 拓扑

```
explain                       → 进 REPL ephemeral (新 default)
explain <subcommand> [args]   → typer cli (老 path, 0 break)
explain --help                → typer 帮助 (0 break)
explain new "Q"               → typer (Wave 4 已加默认进 chat)
explain new "Q" --no-chat     → typer (脚本/CI 用)
```

`cli.py` 主入口改:

```python
app = typer.Typer(
    help="...",
    invoke_without_command=True,
    no_args_is_help=False,  # 旧默认是 True, 改成 False
)


@app.callback()
def main(ctx: typer.Context):
    """Default: 进 REPL ephemeral. 传 subcommand 走老 cli."""
    if ctx.invoked_subcommand is None:
        asyncio.run(enter_repl_async())
        raise typer.Exit()
```

老 12 subcommand 不动. `--help` 仍 typer 默认行为.

---

## 5. Ephemeral Session 概念

### 5.1 EphemeralChatSession dataclass

新模块 `src/explain_engine/chat/ephemeral.py`:

```python
@dataclass
class EphemeralChatSession:
    """In-memory chat session before persistent /new triggered.

    用户进 REPL 时建. 字段大致 mirror ChatSession 但简化:
    - sid=None, state.graph=empty, transcript=[]
    - 接受所有 slash (cross-session slash 直接 work,
      single-session slash 走 _reject)
    - 用户输自然语言 → promote_to_persistent(input, llm)
    """

    storage: StorageV2
    llm: LLMClient | None = None
    state: CognitiveState = field(
        default_factory=lambda: CognitiveState.bootstrap("", budget=0)
    )
    chat_state: ChatStateDict = field(default_factory=ChatStateDict)
    transcript: list[dict] = field(default_factory=list)
    input_provider: Callable[[str], Awaitable[str]] | None = None
    memory_md: str = ""

    @property
    def sid(self) -> None:
        return None  # ephemeral 永无 sid

    @property
    def is_ephemeral(self) -> bool:
        return True

    async def promote_to_persistent(
        self, question: str, llm: LLMClient,
    ) -> ChatSession:
        """触发 implicit /new: bootstrap + HITL + save + 返新 ChatSession.

        失败 (bootstrap LLM error / HITL drop all 后 user cancel) → 抛, caller 留 ephemeral.
        """
        ...
```

### 5.2 promote flow

```python
async def promote_to_persistent(self, question, llm):
    # 1. Load lexicon prior (Phase 10)
    lexicon_data = _load_lexicon(self.storage.knowledge_dir() / "variables.json")
    lexicon = lexicon_data["variables"]

    # 2. Bootstrap LLM
    try:
        phenomena = await bootstrap_phenomena(
            question, llm, lexicon=lexicon, lexicon_top_k=20,
        )
    except (LLMError, SchemaValidationError) as exc:
        console.print(f"[red]bootstrap 失败: {exc}[/red]")
        raise  # caller 留 ephemeral

    # 3. HITL async (Wave A 实装)
    final_phenomena = await review_phenomena_async(
        phenomena, self.input_provider,
    )

    # 4. Build + save session
    settings = Settings()
    new_state = CognitiveState.bootstrap(question, budget=settings.default_budget)
    for p in final_phenomena:
        new_state.graph.add_node(p)

    meta = SessionMeta.new(question=question)
    session = Session(meta=meta, state=new_state)
    SessionStore().save(session)

    # 5. 返新 ChatSession + 拷 chat_state (含 /budget 改的值)
    real_chat = ChatSession(meta.session_id, llm=llm)
    real_chat.chat_state = self.chat_state  # 拷 ephemeral 改的 budget
    real_chat.input_provider = self.input_provider
    return real_chat
```

### 5.3 ephemeral 期间 slash 分类

| 类别 | Slash | 行为 |
|---|---|---|
| cross-session (anytime work) | /list, /lexicon, /migrate, /quit, /help, /resume | 正常工作 |
| config (anytime, 改 chat_state) | /budget | 正常工作 (改 ephemeral.chat_state) |
| single-session (need real session) | /show, /compress, /run, /check, /predict, /counterfactual, /rescore, /save, /compact | reject + 提示 "尚未持久化, 输自然语言新建或 /resume <sid>" |
| /new (建 session 切) | /new | sequential ask question → 同 promote 流程 → 切到真 ChatSession |

---

## 6. Slash 命令完整列表 (18 个)

| Slash | 类别 | 行为 (修订: 无 explicit 参数) |
|---|---|---|
| `/quit` | nav | exit REPL |
| `/help` | nav | 列 slash + tool catalog |
| `/show` | inspect | 当前 session graph snapshot + multi-signal |
| `/list` | cross-session | Rich Table 列所有 session (复用 typer list_cmd) |
| `/lexicon` | cross-session | 列 lexicon (复用 typer lexicon) |
| `/migrate` | cross-session | 一次性迁老 sessions (admin) |
| `/save` | persist | flush 当前 chat_state |
| `/compact` | persist | force memory.md compact |
| `/budget` | **config** | sequential prompt 改 per_turn / per_session limit |
| `/new` | session | sequential prompt 收 question, 建新 session 切 |
| `/resume` | session | numbered picker 选 session 切 (F-1 已实装) |
| `/compress` | single-session | 当前 session 走 compress + HITL review_insights_async + flush_to_lexicon |
| `/run` | single-session | 当前 session reasoning loop (无 HITL, 走 LLM tool_use) |
| `/check` | single-session | multi-signal acceptance report (read-only) |
| `/predict` | single-session | sequential prompt 收 intervention → forward prediction |
| `/counterfactual` | single-session | sequential prompt 收 intervention → counterfactual |
| `/cf` | single-session | alias of /counterfactual |
| `/rescore` | single-session | 重评 edge confidence (无 HITL) |

---

## 7. `/budget` Config 流详细

启动**默认** budget = 10/50 (hardcoded, 与 Phase 9 `ChatStateDict` 一致).

cli flag `explain new --tool-budget-per-turn` / `explain chat --tool-budget-per-turn` **删除** — 改 slash 调.

`/budget` handler 用 `input_provider` sequential prompt:

```python
async def _handle_budget(chat, args):
    cs = chat.chat_state
    console.print(
        f"[bold]Current budget[/bold]\n"
        f"  per-turn limit:    {cs.budget_per_turn_limit}  (剩余 {cs.budget_per_turn_remaining})\n"
        f"  per-session limit: {cs.budget_per_session_limit}  (剩余 {cs.budget_per_session_remaining})\n"
    )

    if chat.input_provider is None:
        return [slash_error("/budget 需 input_provider (test 路径无效)")]

    # Sequential prompt 改两字段
    try:
        new_turn = await chat.input_provider(
            f"新 per-turn limit (回车保持 {cs.budget_per_turn_limit}, q 取消): "
        )
        new_session = await chat.input_provider(
            f"新 per-session limit (回车保持 {cs.budget_per_session_limit}, q 取消): "
        )
    except (EOFError, KeyboardInterrupt):
        return [slash_event("budget_cancelled")]

    if new_turn.strip().lower() in ("q", "quit"):
        return [slash_event("budget_cancelled")]

    # 解析 + 更新 + report
    ...
```

详细输入校验:
- empty input (回车) → 保持原值
- 数字 → 更新 limit (注意 remaining 也 cap 到新 limit)
- `q` / `quit` → 取消
- 非法数字 → 提示 + 取消

ephemeral session 同样支持 — 改 `ephemeral.chat_state.budget_*_limit`, promote 时 copy 过去.

---

## 8. Slash 命令统一无参数原则

### 8.1 修订前 vs 修订后

| 修订前 (草案) | 修订后 (定案) |
|---|---|
| `/new "为什么 X"` | `/new` → prompt 问 "你的问题: " |
| `/predict "如果 X 增加"` | `/predict` → prompt 问 "intervention: " |
| `/counterfactual "移除 Y"` | `/counterfactual` → prompt 问 "counterfactual: " |
| `explain chat --tool-budget-per-turn 20` | `/budget` → sequential prompt 改 limit |

### 8.2 优势

- **UI 一致** — 用户不必记 slash 参数 syntax
- **Multi-line input** — prompt_toolkit 内 prompt 支持 paste long text (intervention 描述可长)
- **Validation 集中** — 在 input_provider 之后用 same code 校验, 不是 split argparse + handler
- **Future-proof** — config slash 模式可加 /preferences / /llm-model / 等

### 8.3 trade-off

- 输 `/predict` 后才输 intervention, 比 `/predict "X"` 多 1 enter
- 但 chat REPL 上下文里, 多 1 enter 微小 (autocomplete 加速 cmd 输入抵消)

---

## 9. HITL Async 重写 (Wave A)

### 9.1 API

```python
# src/explain_engine/hitl/cli_interactive.py
async def review_phenomena_async(
    phenomena: list[VariableNode],
    input_provider: Callable[[str], Awaitable[str]],
    console: Console | None = None,
) -> list[VariableNode]:
    """Async 版 review_phenomena. multi-step k/e/d loop 用 input_provider 收 input."""
    ...

async def review_insights_async(
    state: CognitiveState,
    input_provider: Callable[[str], Awaitable[str]],
    console: Console | None = None,
) -> list[str]:
    ...
```

### 9.2 重写 detail

review_phenomena 现行为 (sync):
```python
def review_phenomena(phenomena, console=None):
    for p in phenomena:
        console.print(f"Phenomenon: {p.name}")
        action = input("k/e/d: ")  # sync stdin
        if action == "k":
            kept.append(p)
        elif action == "e":
            new_desc = input("new description: ")
            ...
```

Async 版:
```python
async def review_phenomena_async(phenomena, input_provider, console=None):
    for p in phenomena:
        console.print(f"Phenomenon: {p.name}")
        action = await input_provider("k/e/d: ")
        if action == "k":
            kept.append(p)
        elif action == "e":
            new_desc = await input_provider("new description: ")
            ...
```

行为完全等价, 只是 input 走 input_provider (在 chat REPL 内自动用 prompt_toolkit).

### 9.3 老 sync 版保留

老 `review_phenomena` / `review_insights` **保留** — `explain new "Q" --no-chat` / `explain compress <sid>` (cli mode) 仍调 sync 版. 不破坏 typer cli backward compat.

---

## 10. Wave 0 — deepseek-v4-pro 400 Bug Fix

### 10.1 现状诊断

Bug 4 (`414850a`) 加了 forced tool_choice → auto fallback. fallback 后 LLM 返 free text, 下游 Pydantic 报错. 用户报告 "概率较高".

### 10.2 Fix 方向

候选:
- (a) compress prompt 加 JSON-mode 强约束 (e.g. "Respond with valid JSON matching: {...schema...}")
- (b) Retry 2-3 次直到拿 structured (重 LLM 调用)
- (c) auto fallback 后 try regex parse free text (脆)
- (d) deepseek json_mode API 探测 (若支持, anthropic 协议是否暴露)

**推荐 (a) + (b) 组合**:
1. compress 路径 prompt 末尾加显式 JSON schema instruction (强约束 LLM 输出 valid JSON)
2. fallback 后 LLM 返不合 schema → retry 2 次 (含原 prompt + "Previous response was malformed. Respond ONLY with valid JSON matching schema.")
3. 仍失败 → raise SchemaValidationError 给 user 看具体错误 (改善 diagnostics)

### 10.3 Affected modules

- `src/explain_engine/llm/anthropic_protocol.py` (chat method retry loop)
- `src/explain_engine/llm/prompts/*.py` (compress 用的 prompt 加 JSON schema instruction)
- 可选: `src/explain_engine/engines/compression.py` (调 LLM 处加 retry counter)

### 10.4 测试

- mock LLM 第一次返 free text, 第二次返 valid JSON → retry 成功
- mock LLM 三次都返 free text → SchemaValidationError 抛, error msg 含具体 raw text

---

## 11. Module 改动汇总

| 文件 | 新/改 | 内容 |
|---|---|---|
| `src/explain_engine/cli.py` | 改主入口 | `invoke_without_command=True` + `@app.callback()` enter_repl |
| `src/explain_engine/chat/repl_entry.py` | 新 | `enter_repl_async()` — ephemeral lifecycle + REPL outer loop |
| `src/explain_engine/chat/ephemeral.py` | 新 | `EphemeralChatSession` dataclass + `promote_to_persistent` |
| `src/explain_engine/chat/slash_commands.py` | 改 | 加 10 新 slash handler + `/cf` alias + 改 /budget 成 config 流 |
| `src/explain_engine/hitl/cli_interactive.py` | 改 | 加 `review_phenomena_async` + `review_insights_async` (sync 保留) |
| `src/explain_engine/llm/anthropic_protocol.py` | 改 | Wave 0 retry loop |
| `src/explain_engine/llm/prompts/` | 改 | bootstrap / compress prompt 加 JSON schema 强约束 |
| `tests/test_chat_ephemeral.py` | 新 | EphemeralChatSession + promote 单测 |
| `tests/test_chat_slash_commands.py` | 扩 | 10 新 slash handler 测 + `/budget` config 测 |
| `tests/test_hitl_async.py` | 新 | review_phenomena_async + review_insights_async 测 |
| `tests/test_cli_main_entry.py` | 新 | `explain` 默认进 REPL / `explain show` 仍 typer |
| `tests/test_llm_retry.py` | 扩 | Wave 0 retry + fallback 测 |
| `README.md` | 改 | Phase 11 milestone + REPL UX 段 |

---

## 12. 测试 + acceptance

### 12.1 自动测覆盖

- ~5 Wave 0 retry/fallback test
- ~5 EphemeralChatSession test (promote / reject single-session slash / /budget 改 chat_state)
- ~15 新 slash handler test (10 cmd × happy/edge)
- ~5 HITL async test (k/e/d 路径 + cancel + drop all)
- ~3 cli entry test (default REPL / subcommand 仍 typer / --help)

预估 +50-70 test.

### 12.2 Manual smoke (acceptance doc)

- S1: `explain` 进 REPL, ephemeral 提示
- S2: 输 `/list` cross-session work; 输 `/show` reject (ephemeral)
- S3: 输 "为什么年轻人不消费" → bootstrap + HITL k/e/d (走 prompt_toolkit) → session 持久化, 切真 chat
- S4: 输 `/compress` → HITL review_insights → flush lexicon
- S5: 输 `/predict` → interactive prompt 收 intervention → 跑 predict
- S6: 输 `/budget` → 改 limit 20/100
- S7: `/quit` 退出. 再跑 `explain show <sid>` cli — 仍 work (backward compat)
- S8: Wave 0 bug fix — 模拟连续 5 次 compress, 看 400 频率 (要 LLM key + 真测)

---

## 13. 风险与 open issues

### 13.1 prompt_toolkit + 长 HITL 会话稳定性

review_phenomena (10 phenomena) × k/e/d × prompt_toolkit input_provider 长链. 现 F-1 实测 /resume picker 单 prompt 稳定, 10+ prompt 串联未验. 留 acceptance smoke.

### 13.2 ephemeral session "首句" 边界判定

slash 起头 (`/`) 不算自然语言. 其他全算 — 包括 `?` `1` `测试` 等. 用户误触输入会被当 question 触发 bootstrap. 缓解: bootstrap 失败 (LLM 嫌 question 无意义) → 留 ephemeral + 报错让用户重试.

### 13.3 /budget 改 limit 后 remaining 怎么算

当前 remaining 是 `limit - 已消耗`. 改 limit 后:
- 若 new_limit > old_limit: remaining += diff (扩容)
- 若 new_limit < remaining: remaining = new_limit (cap)
- 已消耗的 tool call 不退还

### 13.4 promote 后 ephemeral 怎么清

promote 成功 → caller (REPL outer loop) 把 `chat = await ephemeral.promote_to_persistent(...)` 重赋值. ephemeral GC 自然回收. ephemeral.chat_state 已 copy 进真 ChatSession.

### 13.5 cli mode 内调 LLM 的 HITL 仍 sync

`explain new "Q" --no-chat` / `explain compress <sid>` 仍走 sync review_phenomena / review_insights (bare stdin). 不破坏 batch 脚本场景. async 版仅 REPL 内 chat path 用.

---

## 14. 决策摘要 (brainstorming Q&A)

| Q | 选项 | 决策 |
|---|---|---|
| 首句自然语言怎么持久化 | 自动 implicit /new / 弹 confirm / /save 手动 | **自动 implicit /new** |
| typer cmd 哪些做 slash | 全 12 / cross-session / single-session / 部分 | **全 12** |
| HITL 怎么嵌入 prompt_toolkit | async + input_provider / pause 回 stdin / 留 cli only | **async + input_provider** |
| slash 命令是否带参数 | 部分带 string 参数 / 全无参数走 prompt | **全无参数走 prompt** (修订) |
| budget 怎么调 | cli flag / `/budget` config 流 | **`/budget` config (slash 内 sequential prompt)** |
| cli mode 保留形式 | `explain <subcommand>` typer 不动 | **subcommand 路径 0 break** |

---

## 15. 落地顺序 (waves)

预估 7 wave, 2-3 周:

1. **Wave 0** — deepseek-v4-pro 400 bug fix (prompt JSON 强约束 + retry, ~5 test)
2. **Wave 1** — EphemeralChatSession + `enter_repl_async` + cli 入口重构 (default REPL / subcommand 仍 typer)
3. **Wave 2** — HITL async 重写 (review_phenomena_async + review_insights_async, ~5 test)
4. **Wave 2.5** — `/budget` config slash (sequential prompt + chat_state 更新, ~3 test). 删 cli `--tool-budget-per-turn` flag.
5. **Wave 3** — 6 single-session slash (`/compress` + `/run` + `/check` + `/predict` + `/counterfactual` + `/rescore` + `/cf` alias)
6. **Wave 4** — 3 cross-session slash (`/list` + `/lexicon` + `/migrate`)
7. **Wave 5** — acceptance smoke + README + tests baseline

每 wave 内独立 commit, TDD (failing test → impl → pass → commit).

---

## 16. 关联文档

- 顶层: [最终哲学以及技术实现相关设计.md](../../最终哲学以及技术实现相关设计.md)
- Phase 9: [chat REPL prompt_toolkit](2026-05-18-chat-repl-prompt-toolkit-design.md)
- Phase 10: [Persistent World Model](2026-05-18-phase10-persistent-world-model-design.md)
- cli 现状: [src/explain_engine/cli.py](../../src/explain_engine/cli.py)
- chat slash: [src/explain_engine/chat/slash_commands.py](../../src/explain_engine/chat/slash_commands.py)
