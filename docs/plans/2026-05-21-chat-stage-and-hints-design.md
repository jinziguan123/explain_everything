# Chat Stage Flow + Next-Step Hints 设计

**Date**: 2026-05-21
**Status**: brainstorm 完, 待 implementation plan
**Branch**: dev
**Related**:
- Phase 11 Wave 3 (chat slash commands, 引入 single-session mutating slash `/compress` `/run` `/predict` `/counterfactual` `/rescore`)
- Phase 11 Wave 4 (cross-session slash `/list` `/lexicon` `/migrate`)
- `persistence/session.py` SessionMeta (stage 字段)
- 用户 brainstorm 反馈 (3 个 UX 问题, 见 §2)

## 1. Motivation

用户在 chat REPL 内跑完整 session 流程时撞到 3 个 UX 缺陷:

### 1.1 Stage 流转在 chat 内不闭环

`SessionMeta.stage` 有 4 个值: `bootstrap_pending → insight_pending → done → converged`. **cli 子命令** (`explain compress` / `explain run`) 正确推 stage. 但**chat slash** (`/compress` / `/run`) 完全不动 stage. 结果:

- 用户在 chat 跑 `/compress`, 看似成功, 但 `session.meta.stage` 仍是 `bootstrap_pending`
- 跑 `/run` 没有 stage 守门 (因为 chat 没检查), 跑了也不改 stage
- 用户想看进度, `/show` 永远显示 `bootstrap_pending`
- 必须退出 REPL 用 cli 命令才能正确推 stage — 违背 chat-first 的设计意图

### 1.2 缺自动化引导

命令完成后没"下一步" 提示. 用户跑完 `/compress` 不知道接下来该 `/run` 还是 `/predict`. 自学曲线陡, 文档负担重.

### 1.3 (deferred to next phase) 术语/中英文混杂

19 个 slash 命令的描述 + 输出文本含大量技术术语 (`propose_candidates`, `HITL`, `multi-signal`, `manifests_as`, `storage_v2 layout`, ...) + 中英文混杂. 用户不知道每个命令具体做什么. **本 phase 不解, 见 §8 follow-up F-5**.

## 2. Goals

1. **Stage 流转闭环 in chat**: 5 个 mutating slash 命令完整对齐 cli 路径的 stage 转换. 用户全程在 REPL, 不需退出.
2. **Stage gate enforcement in chat**: 跳 stage 的命令 (e.g. bp 跑 `/run`) 被显式拒, 给清晰错误提示.
3. **Next-step hint**: 每个命令完后给"下一步可选"提示, 静态 rule-based, 0 LLM cost, 引导用户走完整 flow.
4. **/help 分组渲染**: 19 命令按用途分 6 组渲染, 帮用户记忆 + 概览能力.
5. **Mid-stage resilience**: chat `/compress` 跟 cli 一样在 `propose+score` 完后 set `insight_pending` + 持久化, 中断重跑跳过 LLM 直接进 HITL review.

## 3. Non-Goals

- **术语去技术化** (问题 1.3): 命令名 / 描述 / 输出文本不改. 单独 phase (follow-up F-5).
- **`/next` auto-progression meta 命令**: 不加. 用户按 hint 自己挑下一步.
- **LLM-生 contextual hint**: 不加. 静态 mapping 够覆盖核心 flow. 升级版留 F-2.
- **CLI 子命令路径改动**: cli `_run_compress` / `_run_run` / `_run_predict` 已正确推 stage. 本 phase 只补 chat 这头, 不动 cli.
- **Stage rollback** (撤销 compress 回 bp): stage 仍单调推进. 不加 rollback.
- **non-mutating slash 加装饰**: `/quit /show /save /compact /check /graph /budget /new /resume /list /lexicon /migrate /help` 都不动 (本就跟 stage 无关).

## 4. Design

### 4.1 Architecture

5 个 mutating slash handler 共享 4 步骤模式: **gate → 业务 → transition → hint**. 抽 1 张规则表 + 1 个装饰器, 5 handler 各加 1 行装饰. handler body 几乎 0 改动.

```
user 输 /run
    ↓
dispatch_slash(chat, "/run")
    ↓
@with_stage_gate(allowed=["done"], success_stage="converged",
                 fail_hint_key="need_compress_first",
                 success_hint_key="after_run")
async def _handle_run(chat, args):  ← 装饰器包住
    ...                              ← handler body 0 改动
    ↓
装饰器:
  ① 查规则表 → stage gate check
     失败 → return [slash_error, slash_next_step_hint(fail_hint_key)]
  ② 调 handler body
  ③ 成功 (无 slash_error) → mutate chat._session.meta.stage = "converged" + persist
  ④ append slash_next_step_hint(success_hint_key) event
  ↓
REPL render: handler events + hint event (灰色 dim)
```

### 4.2 Stage 规则表

新 file `src/explain_engine/chat/slash_stage_rules.py`:

| 命令 | allowed (入口 stage) | success_stage (推到) | fail_hint_key | success_hint_key |
|---|---|---|---|---|
| `/compress` | `bootstrap_pending`, `insight_pending` | `done` | `need_promote_first` | `after_compress` |
| `/run` | `done` | `converged` | `need_compress_first` | `after_run` |
| `/predict <text>` | `done`, `converged` | (None, 不动) | `need_compress_first` | `after_inference` |
| `/counterfactual <text>` (`/cf` alias) | `done`, `converged` | (None) | `need_compress_first` | `after_inference` |
| `/rescore` | (None, 任意 stage 都允许) | (None) | (None) | `after_rescore` |

### 4.3 Hint 文案表 (in 同 file)

```python
HINTS_BY_KEY: dict[str, str] = {
    "need_promote_first": (
        "session 还没启动 — 自然语言输入一个 question 先建 session, "
        "然后再 /compress."
    ),
    "need_compress_first": (
        "需要先 /compress 压缩 graph 抽出 abstraction 层. "
        "当前 stage 不允许这个命令."
    ),
    "after_compress": (
        "▸ 下一步可选:\n"
        "  /run — 自动跑 reasoning loop 推 drivers (推荐)\n"
        "  /predict <现象> — 预测某干预的下游效果\n"
        "  /counterfactual <现象> — 反事实分析"
    ),
    "after_run": (
        "▸ session 已收敛. 可选:\n"
        "  /predict <现象> — 干预预测\n"
        "  /counterfactual <现象> — 反事实\n"
        "  /show — 看完整 graph"
    ),
    "after_inference": (
        "▸ 可继续 /predict 或 /counterfactual 探索, /show 看 graph 更新."
    ),
    "after_rescore": (
        "▸ edge confidence 已重评. /show 看变化, /run 重跑 reasoning loop."
    ),
}
```

文案规则:
- 中文为主, 命令名仍 `/英文` (本 phase 不动命令名, 见 §3 non-goal)
- 起头用 `▸ 下一步...` 标识 (跟普通输出区分)
- 列举 2-3 个 specific 下一步, 不列全集

### 4.4 装饰器实装

```python
# chat/slash_stage_rules.py
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from explain_engine.chat.session import ChatEvent, ChatSession

Handler = Callable[["ChatSession", list[str]], Awaitable[list["ChatEvent"]]]

HINTS_BY_KEY: dict[str, str] = {...}  # 见 §4.3


def with_stage_gate(
    *,
    allowed: list[str] | None = None,
    success_stage: str | None = None,
    fail_hint_key: str | None = None,
    success_hint_key: str | None = None,
) -> Callable[[Handler], Handler]:
    """装饰 slash handler 加 stage gate + transition + hint.

    allowed=None → 任意 stage 都可调.
    success_stage=None → 不动 stage.
    fail_hint_key=None → gate 拒时不附 hint.
    success_hint_key=None → 成功后不附 hint.
    """
    def deco(fn: Handler) -> Handler:
        @wraps(fn)
        async def wrapped(chat, args):
            from explain_engine.chat.session import ChatEvent
            stage = chat._session.meta.stage

            # ① gate check
            if allowed is not None and stage not in allowed:
                events = [ChatEvent(
                    type="slash_error",
                    content=(
                        f"/{_cmd_name(fn)} 在当前 stage={stage!r} 不允许 "
                        f"(需 stage ∈ {allowed})."
                    ),
                )]
                if fail_hint_key and fail_hint_key in HINTS_BY_KEY:
                    events.append(ChatEvent(
                        type="slash_next_step_hint",
                        content=HINTS_BY_KEY[fail_hint_key],
                    ))
                return events

            # ② 调 handler
            events = await fn(chat, args)

            # ③ handler 自己 yield error → 不 transition, 不 hint
            has_error = any(e.type == "slash_error" for e in events)
            if has_error:
                return events

            # ④ stage transition + persist
            if success_stage is not None and stage != success_stage:
                chat._session.meta.stage = success_stage
                if (
                    hasattr(chat, "persist")
                    and not getattr(chat, "is_ephemeral", False)
                ):
                    try:
                        chat.persist()
                    except Exception:
                        pass  # persist 失败不阻断 hint 显示

            # ⑤ success hint
            if success_hint_key and success_hint_key in HINTS_BY_KEY:
                events.append(ChatEvent(
                    type="slash_next_step_hint",
                    content=HINTS_BY_KEY[success_hint_key],
                ))

            return events
        return wrapped
    return deco


def _cmd_name(fn) -> str:
    """`_handle_run` → `run`. 用于 error 文案."""
    name = fn.__name__
    return name.removeprefix("_handle_") if name.startswith("_handle_") else name
```

### 4.5 Hint event 渲染

#### Event type contract (session.py ChatEvent docstring 加 1 行)

```
- slash_next_step_hint: str — 灰色 dim 渲染. 在普通 slash output event 之后,
  给用户提示当前 stage 下推荐的下一步命令. Producer: with_stage_gate 装饰器.
```

#### REPL 渲染 (cli.py `_render_event` 加 1 个 elif)

```python
elif t == "slash_next_step_hint":
    con.print(f"[dim]{escape(str(event.content or ''))}[/dim]")
```

灰色 dim, 无 prefix (因为 content 自带 `▸` 前缀). `chat/repl_entry.py` 复用 `_render_event`, 单点改全覆盖.

### 4.6 Event 流顺序

#### 成功路径 (例 /compress)

```
1. slash_compress: "compress 完, 17 candidate 写入 lexicon"  ← handler body yield
2. slash_next_step_hint: "▸ 下一步可选: /run ..."             ← 装饰器 append
```

#### 失败路径 (例 /run on bp)

```
1. slash_error: "/run 在当前 stage='bootstrap_pending' 不允许 (需 stage ∈ ['done'])."
2. slash_next_step_hint: "需要先 /compress 压缩 graph ..."
```

## 5. 具体 handler 改动

### 5.1 5 个 handler 加装饰

```python
# slash_commands.py
from explain_engine.chat.slash_stage_rules import with_stage_gate

@with_stage_gate(
    allowed=["bootstrap_pending", "insight_pending"],
    success_stage="done",
    fail_hint_key="need_promote_first",
    success_hint_key="after_compress",
)
async def _handle_compress(chat, args): ...

@with_stage_gate(
    allowed=["done"],
    success_stage="converged",
    fail_hint_key="need_compress_first",
    success_hint_key="after_run",
)
async def _handle_run(chat, args): ...

@with_stage_gate(
    allowed=["done", "converged"],
    success_stage=None,
    fail_hint_key="need_compress_first",
    success_hint_key="after_inference",
)
async def _handle_predict(chat, args): ...

@with_stage_gate(
    allowed=["done", "converged"],
    success_stage=None,
    fail_hint_key="need_compress_first",
    success_hint_key="after_inference",
)
async def _handle_counterfactual(chat, args): ...

@with_stage_gate(
    allowed=None,
    success_stage=None,
    fail_hint_key=None,
    success_hint_key="after_rescore",
)
async def _handle_rescore(chat, args): ...
```

handler body 0 改动 (除 `_handle_compress` 见 §5.2). `_ephemeral_reject` / `chat.llm is None` 等内部 guard 保留, 跟 stage gate 不冲突 (stage 先过, 然后 ephemeral / llm 二次过).

`DEFAULT_COMMANDS` registry 0 改动 — Python decorator 让 `_handle_xxx` 已经是 wrapped 版本.

### 5.2 `_handle_compress` 加 mid-stage (cli 对齐)

cli `_run_compress` 在 propose+score 完后 (review 前) 把 stage 推到 `insight_pending` + 立刻 save. 重跑时检测 ip 跳过 LLM 直接进 review. 这是中断恢复 resilience. Chat `_handle_compress` 现缺这个, 顺便补.

```python
@with_stage_gate(
    allowed=["bootstrap_pending", "insight_pending"],
    success_stage="done",
    fail_hint_key="need_promote_first",
    success_hint_key="after_compress",
)
async def _handle_compress(chat, args):
    if getattr(chat, "is_ephemeral", False):
        return _ephemeral_reject("compress")
    if chat.llm is None:
        return [ChatEvent(type="slash_error", content="...")]

    current_stage = chat._session.meta.stage

    # NEW: insight_pending 入口短路 (中断恢复)
    if current_stage == "insight_pending":
        _console.print(
            "[dim](检测到 stage=insight_pending, 跳过 LLM 直接进入审查)[/dim]"
        )
    else:
        # bootstrap_pending 入口: 跑 propose + score
        try:
            top_k = get_lexicon_top_k_for_compress(chat.storage, k=20)
            with _console.status("[bold green]调 LLM 提候选...[/bold green]"):
                await propose_candidates(chat.state, chat.llm, existing_lexicon=top_k)
            await score_all(chat.state, chat.llm)
        except Exception as exc:
            return [ChatEvent(type="slash_error", content=f"...")]

        # NEW: mid-stage transition + persist (中断恢复)
        chat._session.meta.stage = "insight_pending"
        chat.persist()
        _console.print(
            "[dim](中间状态已保存, 即便 review 取消也能下次重入跳过 LLM)[/dim]"
        )

    # review_insights_async (HITL, 用户可取消)
    # ...

    # flush_to_lexicon
    # ...

    chat.persist()
    return [ChatEvent(type="slash_compress", content="...")]
```

装饰器最后会推 stage=done. 中间 `_handle_compress` 自己推 ip 不冲突 (装饰器只看最终 success 与否).

### 5.3 `_handle_help` 加分组渲染

```python
HELP_GROUPS: list[tuple[str, list[str]]] = [
    ("Session 推进", ["compress", "run", "rescore"]),
    ("Session 干预 (需先 /compress)", ["predict", "counterfactual"]),
    ("Inspection (read-only)", ["show", "graph", "check"]),
    ("Session 管理", ["new", "resume", "list", "lexicon"]),
    ("其他", ["budget", "compact", "save", "migrate"]),
    ("帮助 / 退出", ["help", "quit"]),
]
```

`_handle_help` 改写: 按 group 输出, 每个 group 内列出 `/cmd — description`. `/cf` (alias) 跟 `/counterfactual` 一起显示一行.

## 6. Testing

### 6.1 新加 test class (~21 case)

| Test class | case 数 | 覆盖 |
|---|---|---|
| `TestSlashStageGate` | 5 | 各命令在不合法 stage 拒, 含正确 fail hint event |
| `TestSlashStageTransition` | 4 | /compress bp→done, ip→done, /run done→converged, /predict 不动 stage |
| `TestSlashNextStepHint` | 5 | 各 hint_key 内容正确, success_hint_key=None 时不 yield |
| `TestCompressMidStageResilience` | 2 | propose+score 完 stage→ip + persist, ip 入口短路 LLM |
| `TestSlashStageRulesHelpers` | 3 | 装饰器单测 (gate pass / fail / hint append) |
| `TestHelpGrouping` | 2 | /help 输出 6 分组 + 19 命令全在 |

### 6.2 既有 test 改动 (~25 个)

- `TestSlashCompress` (~7 case): 当前用 `_make_done_session` 跑 /compress → gate 拒. 改 stage=bootstrap_pending.
- `TestSlashRun/Predict/Counterfactual/Rescore` (各 ~3-5): stage gate 都允许, 行为不变. 但断言 `len(events) == 1` → `== 2` (含 hint event).
- 估改 ~25 个 existing test.

### 6.3 Fixture 改动

`tests/test_chat_session.py::_make_done_session` 加 `stage` 参数 (default "done" 向后兼容):

```python
def _make_done_session(sid: str, stage: str = "done") -> None:
    ...
    meta = SessionMeta(session_id=sid, question="...", stage=stage, ...)
```

新 test 显式传 `stage="bootstrap_pending"` 等.

### 6.4 Manual acceptance smoke (新 doc `2026-05-21-chat-stage-and-hints-acceptance.md`)

10 步 (从 ephemeral REPL → 完整 stage flow → mid-stage resilience), 含每步验证点. 见独立文件.

## 7. Risks / Trade-offs

1. **Persist 双调用**: handler 自己 `chat.persist()` + decorator 又 `chat.persist()`. 双 I/O ~1KB 多写一次, 实测可忽略.
2. **既有 ~25 test 改 length assertion**: 改动面广. 缓解 — `pytest --cov` 验证覆盖率没漏 case.
3. **Mid-stage resilience 测较繁**: 需 mock `review_insights_async` 模拟用户取消. 加 `TestCompressMidStageResilience` 显式覆盖.
4. **Hint event 不污染 LLM context**: slash event 走 `dispatch_slash` 不进 transcript, LLM 看不到. ✓
5. **`/compress` 重入 ip 时 decorator 仍正确**: handler 跑完返成功, decorator `allowed=[bp,ip]` 通过, success_stage=done. ✓

## 8. Follow-ups (本 phase 外)

- **F-1**: `/help <command>` 显示单命令详细描述 + 当前 stage 能否跑
- **F-2**: contextual LLM-生 hint (升级 §4.3 静态 mapping)
- **F-3**: `/next` meta auto-progression (用户连按 /next 自动推 stage)
- **F-4**: chat predict/counterfactual 完后基于 graph state 给更精细 hint (含 weak L1 提示)
- **F-5**: **#3 术语去技术化 phase** — 全 slash 命名 + 描述 + 输出文本系统性 de-jargonify (中英文统一)
- **F-6**: chat `/compress` 跟 cli 对齐 Rich table 输出格式 (cli 用 table 显示候选 + scores)

## 9. Rollout

- 1 feature branch / 1 squash commit 进 dev
- 改动以新文件为主: `chat/slash_stage_rules.py` + 3 doc
- src 现存文件改动小: 5 handler 各加一行装饰 + `_handle_compress` mid-stage + `_handle_help` 分组 + `cli._render_event` 加 hint 分支
- 估实装: **1 天** (~250 行 src + tests, 半天 src, 半天 test + verify)
- 提交顺序:
  1. decorator + rules + hint table
  2. 5 handler 加装饰
  3. `_handle_compress` mid-stage + `_handle_help` 分组
  4. tests + acceptance — 收尾

## 10. 文件清单

| 文件 | 改动 |
|---|---|
| `src/explain_engine/chat/slash_stage_rules.py` (新) | ~120 行 (装饰器 + 规则表 + 文案表) |
| `src/explain_engine/chat/slash_commands.py` | ~30 行 (5 处装饰 + _handle_compress mid-stage + _handle_help 分组) |
| `src/explain_engine/chat/session.py` | ~3 行 (ChatEvent docstring 加 slash_next_step_hint 契约) |
| `src/explain_engine/cli.py` | ~3 行 (`_render_event` 加 hint 分支) |
| `tests/test_chat_slash_commands.py` | ~80 行 (新增 4 test class) + ~50 行 (改老 test length assert) |
| `tests/test_chat_session.py` | ~5 行 (`_make_done_session` 加 stage 参数) |
| `docs/plans/2026-05-21-chat-stage-and-hints-design.md` (本文) | 设计文档 |
| `docs/plans/2026-05-21-chat-stage-and-hints-plan.md` (待 writing-plans 阶段生成) | implementation plan |
| `docs/plans/2026-05-21-chat-stage-and-hints-acceptance.md` (待 plan 阶段生成) | manual smoke 步骤 |

**估总改动**: ~330 行 src + tests + 3 个 doc.
