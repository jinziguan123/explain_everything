# chat 内 /new + /resume slash commands — Design

> 上一 phase: [Phase 9 Conversational Cognitive Engine Design](2026-05-17-conversational-cognitive-engine-design.md)
> 上一 phase 状态: Phase 9 acceptance + 5 bug fix 已 land (HEAD `414850a`)

**日期**: 2026-05-18
**分支**: `dev`

---

## 0. TL;DR

在 chat REPL 内新增两个 slash command:

- `/new <question>` — 在当前会话中创建新 session (完整复用 `cli new` 的 bootstrap+HITL review 路径), 创建后自动切到新 session 继续对话.
- `/resume` — 列当前 project 下所有历史 session (numbered table), 用户选号后自动切到该 session.

实现以 **in-process 热切 ChatSession 实例** 为核心: 两个 handler 各自完成"建/选 sid"后, 通过新增 ChatEvent `slash_switch_session(sid)` 通知 REPL, 由 REPL 统一做 `await old.aclose() → new ChatSession(sid, llm)`. handler 不直接 mutate 当前 chat, 避免半状态切换.

**改动量**: 1 个新 ChatEvent 子类 / 2 个新 slash handler / `ChatSession.__init__` 加 optional `llm` 参数 / `cli.chat()` REPL 重构成可换 chat_session reference / ~6 个 unit test + 1 个 REPL 集成 test. 预估 ~250-350 行新代码 + 测试.

---

## 1. 背景与动机

### 1.1 现状

Phase 9 chat REPL ([cli.py:889](../../src/explain_engine/cli.py#L889)) 启动时必须传 session_id:

```bash
explain chat s_705f0435
```

REPL 全程跑在该 ChatSession 实例上. 想换 session 或开新 session 必须:
1. `/quit` 退出当前 REPL
2. 跑 `explain new "..."` (异步, bootstrap + HITL review, 终端命令)
3. 重新 `explain chat <new_sid>`

3 步 shell 切换, 期间需要记 sid, 终端历史也散.

### 1.2 用户需求

在 chat 内一句 `/new` 或 `/resume` 就完成 session 切换, **不退出当前 REPL 进程**, LLMClient / Rich Console / asyncio loop 全部复用.

### 1.3 为什么不用 subprocess re-exec

可选方案是触发 slash 后退出当前 REPL, `subprocess` exec 新 `explain chat <sid>`. 但:
- LLMClient 重新创建 (httpx client / cache 全丢)
- `--tool-budget-per-turn` 等 cli flag 要重新传或丢失
- 终端会闪一下
- 父进程退出后子进程接管 stdin 有边界情况 (e.g. job control)

In-process 热切干净.

---

## 2. Scope

### 2.1 本设计内

- 新 slash `/new <question>` + `/resume`
- `ChatEvent` 子类 `slash_switch_session`
- `ChatSession.__init__` 加 `llm: LLMClient | None = None` 参数
- `cli.chat()` REPL 重构成可换 chat_session reference
- 单测 + REPL 集成测

### 2.2 本设计外 (留 future)

- /resume 的 fuzzy search / sid 前缀直传 (YAGNI; numbered picker 已够)
- /new 跑 bootstrap 期间的 cancel (Ctrl-C 走现有 asyncio 路径)
- /switch-project (跨 project 切 — Phase 10 cross-session knowledge pool 才用到)
- /delete-session, /rename-session (本设计不动 session lifecycle)

---

## 3. 总体方案

### 3.1 核心: handler 不切换, REPL 切换

handler 拿不到 outer REPL 的 chat_session 变量 (Python closure 限制). 让 handler 在 ChatSession 内部"换 self" 也不行 (handle_user_input async generator 还在 iterate, mutate self.sid / self.storage 等会撕裂).

方案: handler 完成 "建/选 sid" 后 yield 一个特殊 event:

```python
ChatEvent(type="slash_switch_session", content={"sid": new_sid})
```

REPL `_render_event` 后检测该 type, 单 turn `async for` 结束 (handler return) 后做切换:

```python
if new_sid_to_switch and new_sid_to_switch != chat_session.sid:
    await chat_session.aclose()
    chat_session = ChatSession(new_sid_to_switch, llm=llm)
    # re-apply cli budget flags (会话级偏好继承)
    chat_session.chat_state.budget_per_turn_limit = tool_budget_per_turn
    ...
```

时序保证: `async for event in chat_session.handle_user_input(...)` 是 finite generator, slash 走 `dispatch_slash` 返 list[event] 然后 yield 完即 return; REPL 在 `for` 退出后才 mutate, 安全.

### 3.2 ChatSession 加 llm 字段

`/new` 要调 `bootstrap_phenomena(question, llm)`, 但 slash handler 签名是 `(chat, args)`, 拿不到 llm. 加 `ChatSession.__init__(sid, llm=None)`:

```python
class ChatSession:
    def __init__(self, sid: str, llm: LLMClient | None = None):
        ...
        self.llm = llm  # NEW; None for slash-only / test paths
```

- `cli.chat()` 启动时 `ChatSession(sid, llm=make_llm_client())`
- `cli.chat()` 切 session 时 `ChatSession(new_sid, llm=llm)` (复用同一 client)
- 现有 caller (test fixture / 老 path) 不传仍 default None, 不影响 (它们没调 /new)
- `handle_user_input` 仍接 llm 参数 (现有签名不动, backward compat)

---

## 4. /new handler 详细设计

### 4.1 签名

```python
async def _handle_new(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
```

### 4.2 参数解析

`dispatch_slash` 用 `split()` 拆 token, 中文 question 会被拆成多个 args. 用 `" ".join(args)` 重组:

```python
question = " ".join(args).strip()
if not question:
    return [ChatEvent(type="slash_error", content="Usage: /new <你的问题>")]
```

不支持 quote (`/new "foo bar"`) — YAGNI, join 已够.

### 4.3 调用 bootstrap + HITL

```python
if chat.llm is None:
    return [ChatEvent(type="slash_error",
                      content="/new 需要 LLM client; 当前 chat 启动时未传 (test path?)")]

try:
    phenomena = await bootstrap_phenomena(question, chat.llm)
except (SchemaValidationError, LLMError) as exc:
    return [ChatEvent(type="slash_error",
                      content=f"/new bootstrap 失败: {type(exc).__name__}: {exc}")]

# HITL review (sync stdin, 包 to_thread 不 block event loop)
final_phenomena = await asyncio.to_thread(
    review_phenomena, phenomena, console=...
)
```

**console 注入**: handler 拿不到 REPL 的 Rich Console. 方案: 用 `from explain_engine.cli import console` (全局 console 实例 — cli.py 顶部已有). 或者给 ChatSession 加 console 字段. 倾向前者 (handler 直接 import), 避免 ChatSession 字段膨胀.

### 4.4 创建 + 存盘 + yield switch event

```python
settings = Settings()
state = CognitiveState.bootstrap(question, budget=settings.default_budget)
for p in final_phenomena:
    state.graph.add_node(p)
meta = SessionMeta.new(question=question)
session = Session(meta=meta, state=state)

store = _get_store()
try:
    store.save(session)
except OSError as exc:
    return [ChatEvent(type="slash_error",
                      content=f"/new 存盘失败: {exc}")]

return [
    ChatEvent(type="slash_new",
              content=f"Session {meta.session_id} 已创建."),
    ChatEvent(type="slash_switch_session",
              content={"sid": meta.session_id}),
]
```

两个 event: 一条 info 给用户看, 一条 signal 让 REPL 切换.

### 4.5 失败时的行为

任何失败 (bootstrap LLM error / HITL cancel / 存盘 OSError) → 返 `slash_error`, **不**返 `slash_switch_session`. REPL 不会切, 用户留在原 session.

---

## 5. /resume handler 详细设计

### 5.1 签名 + 0 参数

```python
async def _handle_resume(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    if args:
        return [ChatEvent(type="slash_error",
                          content="Usage: /resume (无参数; 弹列表后选号)")]
```

### 5.2 列出 + 渲染

```python
sids = chat.storage.list_sessions()
if not sids:
    return [ChatEvent(type="slash_resume",
                      content="当前 project 无 session.")]

# 加载 metadata (轻; 不读 graph 避免 N x graph IO)
from explain_engine.persistence.session import SessionStore
sstore = SessionStore()
metas = []
for sid in sids:
    try:
        sess = sstore.load(sid)  # loads graph too — TODO future: 只读 meta
        metas.append(sess.meta)
    except Exception:
        continue  # 坏 session 跳过

metas.sort(key=lambda m: m.created_at, reverse=True)

# Rich Table
from explain_engine.cli import console
from rich.table import Table
table = Table(title=f"Sessions ({len(metas)})")
table.add_column("#", style="bold")
table.add_column("ID", style="cyan")
table.add_column("问题", style="bold")
table.add_column("Stage")
table.add_column("Created")
for i, m in enumerate(metas, start=1):
    is_current = "* " if m.session_id == chat.sid else "  "
    ts = datetime.fromtimestamp(m.created_at).strftime("%Y-%m-%d %H:%M")
    table.add_row(f"{is_current}{i}", m.session_id, m.question, m.stage, ts)
console.print(table)
```

`*` 标记当前 session, 用户知道自己在哪.

**性能 note**: `SessionStore.load(sid)` 读 metadata + graph (graph 可能上百 KB). 列表 100 session 会读 100 graph. 当前先这样 (用户 session 通常 < 50), TODO 加 `SessionStore.load_meta_only(sid)`.

### 5.3 收用户输入

```python
try:
    choice = await asyncio.to_thread(
        input, "选 # (q 取消): "
    )
except (EOFError, KeyboardInterrupt):
    return [ChatEvent(type="slash_resume", content="已取消.")]

choice = choice.strip().lower()
if choice in ("", "q", "quit"):
    return [ChatEvent(type="slash_resume", content="已取消.")]

if not choice.isdigit():
    return [ChatEvent(type="slash_error",
                      content=f"输入需为数字 1-{len(metas)}; 取消.")]

idx = int(choice)
if not (1 <= idx <= len(metas)):
    return [ChatEvent(type="slash_error",
                      content=f"# {idx} 超范围 (1-{len(metas)}); 取消.")]

target_sid = metas[idx - 1].session_id
if target_sid == chat.sid:
    return [ChatEvent(type="slash_resume",
                      content=f"已在 session {target_sid}.")]

return [
    ChatEvent(type="slash_resume",
              content=f"切换到 session {target_sid}..."),
    ChatEvent(type="slash_switch_session",
              content={"sid": target_sid}),
]
```

**无 retry** — 用户输错一次直接取消, 留原 session. 节省 prompt 嵌套复杂度.

---

## 6. REPL 重构

### 6.1 当前 ([cli.py:962](../../src/explain_engine/cli.py#L962))

`chat_session` 在 `repl()` 开头 init, while loop 内部固定引用.

### 6.2 改造后伪码

```python
async def repl() -> None:
    chat_session = ChatSession(session_id, llm=llm)  # NEW: 传 llm

    # apply budget flags (现有逻辑不动)
    chat_session.chat_state.budget_per_turn_limit = tool_budget_per_turn
    ...

    console.print(...)
    if not has_tools_api: console.print(warn...)

    while True:
        try:
            user_input = await asyncio.to_thread(input, "\n> ")
        except (EOFError, KeyboardInterrupt):
            ...
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        quit_requested = False
        switch_to_sid: str | None = None
        try:
            async for event in chat_session.handle_user_input(user_input, llm=llm):
                _render_event(console, event)
                if event.type == "slash_quit":
                    quit_requested = True
                elif event.type == "slash_switch_session":
                    switch_to_sid = event.content["sid"]
        except Exception as exc:
            console.print(f"[red]Error: {type(exc).__name__}: {exc}[/red]")
            continue

        # 单 turn 结束后再切, 避免在 iter 中 mutate
        if switch_to_sid and switch_to_sid != chat_session.sid:
            await chat_session.aclose()
            try:
                chat_session = ChatSession(switch_to_sid, llm=llm)
            except (FileNotFoundError, ChatSessionLoadError) as exc:
                console.print(f"[red]切换失败: {exc}[/red]")
                # fallback: 重 load 旧 sid (旧 chat_session 已 aclose, 重新打开)
                chat_session = ChatSession(session_id, llm=llm)
                continue
            # re-apply budget flags
            chat_session.chat_state.budget_per_turn_limit = tool_budget_per_turn
            chat_session.chat_state.budget_per_turn_remaining = tool_budget_per_turn
            chat_session.chat_state.budget_per_session_limit = tool_budget_per_session
            if chat_session.chat_state.budget_per_session_remaining > tool_budget_per_session:
                chat_session.chat_state.budget_per_session_remaining = tool_budget_per_session
            console.print(f"[green]Switched to {switch_to_sid}.[/green]")

        if quit_requested:
            break

    await chat_session.aclose()
    console.print(f"[green]Session {chat_session.sid} saved.[/green]")
```

### 6.3 几个 edge case

- **切到当前 session**: handler 已 noop, REPL 不会进 `if switch_to_sid ...` 块.
- **切失败 (sid 不存在)**: 罕见 (用户输的 sid 来自 /resume 自己列的), 但 race 可能 (其他进程删了). fallback 重 load 原 sid (此时已 aclose, 但文件还在). 简单可接受.
- **/new 失败**: handler 不 yield switch event, REPL 不切, 用户留原 session.
- **/quit 同 turn 也 yield switch**: 不可能 — /new /resume 和 /quit 是互斥 handler.

---

## 7. 测试 plan

### 7.1 单测 (`tests/chat/test_slash_commands.py` 扩)

- `test_slash_new_empty_args_rejects` — `/new` 无 args → `slash_error`
- `test_slash_new_no_llm_rejects` — `chat.llm is None` + `/new "Q"` → `slash_error`
- `test_slash_new_creates_session_and_yields_switch` — mock `bootstrap_phenomena` + `review_phenomena`, 验:
  - 返 list 含 `slash_new` + `slash_switch_session`
  - `switch_session.content["sid"]` 是真存盘的 sid
  - 新 session metadata.question 正确
  - graph 含 mock 出的 phenomena
- `test_slash_new_bootstrap_error_returns_error` — mock bootstrap raise LLMError → `slash_error`, 不 yield switch
- `test_slash_resume_no_sessions` — empty project → `slash_resume` info, 不 yield switch
- `test_slash_resume_invalid_number` — mock input "abc" → `slash_error`, 不 yield switch
- `test_slash_resume_out_of_range` — mock input "999" → `slash_error`
- `test_slash_resume_cancel_q` — mock input "q" → `slash_resume` cancel
- `test_slash_resume_picks_session_yields_switch` — mock input "1" → 验 `slash_switch_session.content["sid"]` 匹配 metas[0].sid
- `test_slash_resume_current_session_noop` — mock input 选到当前 sid → `slash_resume` info, 不 yield switch

### 7.2 REPL 集成测 (`tests/test_chat_cli.py` 扩)

- `test_chat_repl_new_switches_session` — mock LLM + bootstrap + review, 模拟用户输 `/new Q2` → 验后续 turn `chat_session.sid` 已变 + 旧 session 文件仍存
- `test_chat_repl_resume_switches_session` — 预先建 2 个 session, 进 chat session A → `/resume` mock input "2" → 验切到 B
- `test_chat_repl_switch_preserves_budget_flag` — `explain chat --tool-budget-per-turn 20` → /new → 验新 chat_session.budget_per_turn_limit == 20

### 7.3 /help 输出

`_handle_help` 自动列 DEFAULT_COMMANDS 全部, 不需额外测.

---

## 8. 风险与 open issues

### 8.1 已知 trade-off

- **/new 调 LLM** 打破了"slash 不调 LLM"的旧设计 (slash_commands.py:1 docstring). 文档需更新. 但行为是用户预期的 (/new 必然要 bootstrap).
- **/resume 加载所有 graph** (SessionStore.load 读 graph). 100+ session 时慢. 加 TODO; 真正 Phase 10 cross-session 时统一改 load_meta_only.
- **HITL review_phenomena 用 sync input** — 在 chat REPL 内 to_thread 包了 OK, 但占用 stdin 期间 chat REPL 的 `input("> ")` 走不到. 这是 expected (用户在 review).

### 8.2 未来扩展点

- `/switch-project <proj>` (跨 project) — Phase 10
- `/clone-session <sid>` (复制当前 graph 作 new session 起点)
- `/delete-session <sid>` (需 destructive HITL gate)
- `/resume <sid_prefix>` 精确跳 (跳过 numbered picker)

---

## 9. 决策摘要 (brainstorming Q&A)

| Q | 选项 | 决策 |
|---|---|---|
| /new 怎么处理 bootstrap + HITL? | A 完整复用 / B 跳过 bootstrap 开空 session / C 跳 HITL | A |
| session 切换怎么实现? | A In-process 热切 / B subprocess re-exec / C 不切只打印 sid | A |
| /resume 怎么选 session? | A Numbered picker / B /resume <sid> / C 两者均支持 | A |

---

## 10. 落地顺序

按 wave 切, 每 wave 独立可测:

1. **Wave 1 — ChatSession.llm 字段** (~1 task)
   - `ChatSession.__init__` 加 `llm` 参数
   - `cli.chat()` init 时传 llm
   - 现有 test 验 default None backward compat
2. **Wave 2 — slash_switch_session event + REPL 重构** (~1 task)
   - 新 event type (str 即可, 不必加新 dataclass — ChatEvent base 够)
   - REPL 重构成可换 chat_session
   - 集成测: 手动构造 slash_switch_session event 验 REPL 切
3. **Wave 3 — /new handler** (~1 task)
   - handler 实装 + 注册到 DEFAULT_COMMANDS
   - 单测 (mock bootstrap + review)
   - REPL 集成测
4. **Wave 4 — /resume handler** (~1 task)
   - handler 实装 + 注册
   - 单测 (mock input)
   - REPL 集成测
5. **Wave 5 — /help doc 更新 + slash_commands.py docstring 修正** (~0.5 task)

预估总: ~3-5 task, 1-2 个 session.

---

## 11. 关联文档

- [Phase 9 Design](2026-05-17-conversational-cognitive-engine-design.md) §3 slash command 概念
- [Phase 9 Plan](2026-05-17-conversational-cognitive-engine-plan.md) Wave F.1 6 默认 slash
- [src/explain_engine/chat/slash_commands.py](../../src/explain_engine/chat/slash_commands.py) 现有 6 handler
- [src/explain_engine/cli.py:889](../../src/explain_engine/cli.py#L889) `chat()` 命令 + REPL
