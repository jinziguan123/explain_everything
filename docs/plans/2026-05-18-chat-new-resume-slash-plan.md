# chat 内 /new + /resume slash Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** chat REPL 内新增 `/new <question>` 和 `/resume` 两个 slash command, in-process 热切 ChatSession 实例, 不退出当前进程.

**Architecture:** 两个 handler 完成 "建/选 sid" 后通过新 ChatEvent `slash_switch_session(sid)` 通知 REPL; REPL 在单 turn iter 结束后做 `await old.aclose() + new ChatSession(sid, llm)`. ChatSession 加 optional llm 字段让 /new 能调 bootstrap. REPL 主循环抽成可测 async function.

**Tech Stack:** Python 3.11+ / pytest + pytest-asyncio / Typer / Rich / Anthropic + OpenAI SDK / Pydantic v2

**Setup pre-flight:**

- 分支: `dev` (HEAD `4754768` — design doc 已 commit)
- 环境: `.venv/bin/python -m pytest` (uv-managed venv, **不要用裸 python**)
- Lint: `.venv/bin/ruff check src/ tests/`
- 全测基线: `.venv/bin/python -m pytest -x` (应 649 PASS)
- Design 参考: [docs/plans/2026-05-18-chat-new-resume-slash-design.md](2026-05-18-chat-new-resume-slash-design.md)

---

## Wave 1 — ChatSession.llm 字段

### Task 1: ChatSession.__init__ 接受 optional llm

**Files:**
- Modify: `src/explain_engine/chat/session.py:101` (`__init__` 签名 + body)
- Modify: `tests/test_chat_session.py` (加 2 个新 test)

**Step 1.1: 写 failing test**

加到 `tests/test_chat_session.py` 末尾 (新 class TestChatSessionLLM, 或 append 到 TestChatSessionLoad).

```python
class TestChatSessionLLM:
    def test_default_llm_is_none(self) -> None:
        """Backward compat: 不传 llm 时 chat.llm is None."""
        _make_done_session("s_llm_001")
        chat = ChatSession("s_llm_001")
        assert chat.llm is None

    def test_accepts_llm_kwarg(self) -> None:
        """ChatSession(sid, llm=client) — slash /new 路径需要."""
        _make_done_session("s_llm_002")
        sentinel = object()  # 任意 stub 即可, 此 test 不调它
        chat = ChatSession("s_llm_002", llm=sentinel)  # type: ignore[arg-type]
        assert chat.llm is sentinel
```

**Step 1.2: 跑测试确认 fail**

Run: `.venv/bin/python -m pytest tests/test_chat_session.py::TestChatSessionLLM -v`
Expected: 两个 test 都 FAIL — `TypeError: __init__() got an unexpected keyword argument 'llm'` (test 2) + `AttributeError: 'ChatSession' object has no attribute 'llm'` (test 1).

**Step 1.3: 实装**

修改 `src/explain_engine/chat/session.py:101` 的 `__init__` 签名 + body:

```python
def __init__(self, sid: str, llm: "LLMClient | None" = None):
    """加载 session 的 5 sidecar files.

    Args:
        sid: session id
        llm: optional LLMClient — slash handler 调 bootstrap / 其他需 LLM
             的操作时通过 chat.llm 访问. 默认 None (backward compat: 老 caller +
             不需 LLM 的 slash 不受影响).

    Phase 9 Wave C.1 fix · I1: 去掉了 storage 参数 — 内部 SessionStore
    ...  # 保留原 docstring 其余部分
    """
    self.sid = sid
    self.llm = llm  # NEW: for /new handler chain (2026-05-18 slash 扩展)
    self.storage = StorageV2()
    # ...其余 init body 不动
```

**Step 1.4: 跑测试确认 pass**

Run: `.venv/bin/python -m pytest tests/test_chat_session.py::TestChatSessionLLM -v`
Expected: 2 PASS.

**Step 1.5: 跑全 ChatSession test 验 backward compat**

Run: `.venv/bin/python -m pytest tests/test_chat_session.py tests/test_chat_slash_commands.py tests/test_chat_loop.py tests/test_chat_hooks.py -v`
Expected: 全 PASS (无新增 failure).

**Step 1.6: Commit**

```bash
git add src/explain_engine/chat/session.py tests/test_chat_session.py
git commit -m "$(cat <<'EOF'
chat/session · ChatSession.__init__ 加 optional llm 字段

为 Wave 2026-05-18 chat 内 /new slash 铺路 — /new 要调 bootstrap_phenomena
需要 llm client, 但 slash handler 签名 (chat, args) 拿不到. 改 chat.llm
属性让 handler 通过 self 访问. 默认 None backward compat 现有 caller.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 2 — REPL 抽出 + slash_switch_session 切换 logic

### Task 2: 抽 REPL 主循环 + 加切换 logic

抽出 `_run_chat_repl_async(initial_sid, llm, budget_per_turn, budget_per_session)` 让 REPL 可测; 同时把 slash_switch_session 切换分支加进去.

**Files:**
- Modify: `src/explain_engine/cli.py:889-1001` (`chat` 命令 + 内部 `repl()`)
- Test: `tests/test_cli_chat.py` (加新 class TestReplSwitchSession)

**Step 2.1: 写 failing test — slash_switch_session 触发切换**

加到 `tests/test_cli_chat.py` 末尾:

```python
import pytest


class TestReplSwitchSession:
    """REPL 收到 slash_switch_session event 后切到新 chat_session."""

    @pytest.mark.asyncio
    async def test_switch_session_replaces_chat_session(
        self, monkeypatch
    ) -> None:
        """模拟用户输 magic input → handler yield slash_switch_session → REPL 切.

        用 monkeypatch 把 input 改成返预设序列 ("trigger switch", /quit);
        把 ChatSession.handle_user_input mock 成对第 1 input yield switch event
        (sid=s_target), 第 2 input yield slash_quit.
        验切换后 chat_session.sid == 's_target'.
        """
        from tests.test_chat_session import _make_done_session
        from explain_engine.cli import _run_chat_repl_async  # NEW symbol (Step 2.3)
        from explain_engine.chat.session import ChatEvent

        _make_done_session("s_repl_src")
        _make_done_session("s_repl_dst")

        # input 序列: 1st 触发切换, 2nd /quit
        inputs = iter(["switch please", "/quit"])
        monkeypatch.setattr(
            "builtins.input",
            lambda *a, **kw: next(inputs),
        )

        # 跟踪每次切换后 chat_session.sid
        observed_sids: list[str] = []

        async def fake_handle(self, text, llm=None):
            observed_sids.append(self.sid)
            if text == "switch please":
                yield ChatEvent(
                    type="slash_switch_session",
                    content={"sid": "s_repl_dst"},
                )
            elif text == "/quit":
                yield ChatEvent(type="slash_quit", content="bye")

        from explain_engine.chat.session import ChatSession
        monkeypatch.setattr(ChatSession, "handle_user_input", fake_handle)

        # 跑 (llm=None 即可, fake_handle 不用)
        await _run_chat_repl_async(
            initial_sid="s_repl_src",
            llm=None,
            tool_budget_per_turn=10,
            tool_budget_per_session=50,
        )

        # 第 1 input 时 chat 仍是 src, 第 2 input 时已切到 dst
        assert observed_sids == ["s_repl_src", "s_repl_dst"]
```

**Step 2.2: 跑测试确认 fail**

Run: `.venv/bin/python -m pytest tests/test_cli_chat.py::TestReplSwitchSession -v`
Expected: FAIL — `ImportError: cannot import name '_run_chat_repl_async' from 'explain_engine.cli'`.

**Step 2.3: 抽 REPL 出来 + 加切换分支**

修改 `src/explain_engine/cli.py:889-1001` 的 `chat()` 命令. 把内部 `repl()` 改成 module-level `_run_chat_repl_async` (可测), `chat()` 命令只做 typer parsing + 调它.

替换 `chat()` body:

```python
@app.command()
def chat(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
    no_input_check: bool = typer.Option(
        False, "--no-input-check",
        help="(Phase 9 Wave G+ TODO: wire input_validation into chat startup)",
        hidden=True,
    ),
    tool_budget_per_turn: int = typer.Option(
        10, "--tool-budget-per-turn",
        help="Max tool calls per user turn (Phase 9 Q5γ)",
    ),
    tool_budget_per_session: int = typer.Option(
        50, "--tool-budget-per-session",
        help="Max tool calls per session lifetime",
    ),
) -> None:
    """Phase 9 Wave F.2: 进 conversational chat REPL."""
    del no_input_check
    llm = make_llm_client()
    asyncio.run(_run_chat_repl_async(
        initial_sid=session_id,
        llm=llm,
        tool_budget_per_turn=tool_budget_per_turn,
        tool_budget_per_session=tool_budget_per_session,
    ))


def _apply_budget_flags(
    chat_session,
    tool_budget_per_turn: int,
    tool_budget_per_session: int,
) -> None:
    """启动 / 切换 session 后, 应用 cli flag 到 chat_state.

    切 session 后 budget flag 继承 (会话级偏好).
    """
    chat_session.chat_state.budget_per_turn_limit = tool_budget_per_turn
    chat_session.chat_state.budget_per_turn_remaining = tool_budget_per_turn
    chat_session.chat_state.budget_per_session_limit = tool_budget_per_session
    if (
        chat_session.chat_state.budget_per_session_remaining
        > tool_budget_per_session
    ):
        chat_session.chat_state.budget_per_session_remaining = tool_budget_per_session


async def _run_chat_repl_async(
    initial_sid: str,
    llm,
    tool_budget_per_turn: int,
    tool_budget_per_session: int,
) -> None:
    """Phase 9 Wave F.2 / 2026-05-18: REPL 主循环 (可测).

    抽出来的目的:
    1. tests 可直接 await + monkeypatch input — 不必走 typer CliRunner.
    2. /new + /resume slash 触发 in-process session 切换需要外层 mutable
       chat_session reference, 抽函数后变量作用域清晰.
    """
    from explain_engine.chat.session import (
        ChatSession,
        ChatSessionLoadError,
    )

    try:
        chat_session = ChatSession(initial_sid, llm=llm)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    _apply_budget_flags(
        chat_session, tool_budget_per_turn, tool_budget_per_session
    )

    has_tools_api = hasattr(llm, "chat_with_tools") if llm is not None else False
    console.print(
        f"[dim]Loaded session {initial_sid}. "
        f"Type /help for commands. /quit to exit.[/dim]"
    )
    if llm is not None and not has_tools_api:
        console.print(
            "[yellow]⚠️  LLM dispatch 未实装 (LLMClient.chat_with_tools 不存在). "
            "自然语言输入会无响应; 仅 slash 命令工作.[/yellow]"
        )

    while True:
        try:
            user_input = await asyncio.to_thread(input, "\n> ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Interrupted. Saving...[/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        quit_requested = False
        switch_to_sid: str | None = None
        try:
            async for event in chat_session.handle_user_input(
                user_input, llm=llm
            ):
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
                console.print(
                    f"[red]切换失败: {exc}[/red]"
                )
                # 退回原 sid (旧 chat 已 aclose, 重新打开)
                chat_session = ChatSession(initial_sid, llm=llm)
            _apply_budget_flags(
                chat_session, tool_budget_per_turn, tool_budget_per_session
            )
            console.print(
                f"[green]Switched to {chat_session.sid}.[/green]"
            )

        if quit_requested:
            break

    await chat_session.aclose()
    console.print(f"[green]Session {chat_session.sid} saved.[/green]")
```

**Step 2.4: 跑测试确认 pass**

Run: `.venv/bin/python -m pytest tests/test_cli_chat.py::TestReplSwitchSession -v`
Expected: PASS.

**Step 2.5: 跑全 cli_chat test 验 surface 没破**

Run: `.venv/bin/python -m pytest tests/test_cli_chat.py -v`
Expected: 全 PASS (老 surface test 依赖 `chat` 命令, surface 没动).

**Step 2.6: Lint + 全测**

Run: `.venv/bin/ruff check src/explain_engine/cli.py tests/test_cli_chat.py`
Expected: 0 issue.

Run: `.venv/bin/python -m pytest -x`
Expected: 全 PASS.

**Step 2.7: Commit**

```bash
git add src/explain_engine/cli.py tests/test_cli_chat.py
git commit -m "$(cat <<'EOF'
cli/chat · 抽 REPL 主循环 + 加 slash_switch_session 切换 logic

抽 _run_chat_repl_async 出来 (可测 + 切换需要 mutable chat_session 引用).
REPL 收到 ChatEvent(type='slash_switch_session', content={'sid': ...})
后: await old.aclose() → new ChatSession(sid, llm) → 继承 cli budget flag.

切失败 (sid 不存在 / 文件损坏) → 退回原 sid 不让 REPL crash.

为 2026-05-18 chat 内 /new + /resume slash 铺路 (handler 不直接 mutate,
只 yield switch event signal REPL).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 3 — /new handler

### Task 3: 实装 /new <question> handler

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py` (加 `_handle_new` + 注册)
- Modify: `tests/test_chat_slash_commands.py` (加 TestSlashNew class, ~5 test)

**Step 3.1: 写 failing test — empty args reject**

加到 `tests/test_chat_slash_commands.py` 末尾:

```python
class TestSlashNew:
    @pytest.mark.asyncio
    async def test_empty_args_rejects(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_new_001")
        chat = ChatSession("s_new_001")
        events = await dispatch_slash(chat, "/new")
        assert len(events) == 1
        assert events[0].type == "slash_error"
        assert "Usage" in events[0].content

    @pytest.mark.asyncio
    async def test_no_llm_rejects(self):
        """Chat 没绑 llm 时 /new 应该明确报错 (而非裸 AttributeError)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_new_002")
        chat = ChatSession("s_new_002")  # llm 默认 None
        events = await dispatch_slash(chat, "/new 为什么 X")
        assert events[0].type == "slash_error"
        assert "llm" in events[0].content.lower()

    @pytest.mark.asyncio
    async def test_success_creates_session_and_yields_switch(
        self, monkeypatch
    ):
        """Mock bootstrap + review → 验创建 session + yield slash_switch_session."""
        from explain_engine.chat.session import ChatSession
        from explain_engine.schema.nodes import VariableNode

        _make_done_session("s_new_003")

        # Mock bootstrap_phenomena: 返 2 个固定 phenomena
        async def fake_bootstrap(question, llm, min_count=8, max_count=15):
            assert question == "为什么 中文 测试"
            return [
                VariableNode(
                    id="p_001", name="A", description="da",
                    abstraction_level=0, confidence=0.7, epistemic="observation",
                ),
                VariableNode(
                    id="p_002", name="B", description="db",
                    abstraction_level=0, confidence=0.7, epistemic="observation",
                ),
            ]
        monkeypatch.setattr(
            "explain_engine.chat.slash_commands.bootstrap_phenomena",
            fake_bootstrap,
        )

        # Mock review_phenomena: pass-through (keep all)
        def fake_review(phenomena, console=None):
            return list(phenomena)
        monkeypatch.setattr(
            "explain_engine.chat.slash_commands.review_phenomena",
            fake_review,
        )

        # ChatSession 必须带 llm 才能 /new — sentinel 即可 (fake_bootstrap 不用真 llm)
        chat = ChatSession("s_new_003", llm=object())  # type: ignore[arg-type]

        events = await dispatch_slash(chat, "/new 为什么 中文 测试")

        # 1 个 info + 1 个 switch event
        types = [e.type for e in events]
        assert "slash_new" in types
        assert "slash_switch_session" in types

        switch_ev = next(e for e in events if e.type == "slash_switch_session")
        new_sid = switch_ev.content["sid"]
        assert new_sid.startswith("s_")
        assert new_sid != "s_new_003"

        # 真存盘了
        from explain_engine.persistence.session import SessionStore
        store = SessionStore()
        loaded = store.load(new_sid)
        assert loaded.meta.question == "为什么 中文 测试"
        assert len(loaded.state.graph.nodes) == 2  # 2 phenomena

    @pytest.mark.asyncio
    async def test_bootstrap_error_returns_error_no_switch(self, monkeypatch):
        """Mock bootstrap raise → slash_error, 不 yield switch."""
        from explain_engine.chat.session import ChatSession
        from explain_engine.llm.errors import LLMError

        _make_done_session("s_new_004")

        async def fake_bootstrap_fails(question, llm, min_count=8, max_count=15):
            raise LLMError("mock LLM down")
        monkeypatch.setattr(
            "explain_engine.chat.slash_commands.bootstrap_phenomena",
            fake_bootstrap_fails,
        )

        chat = ChatSession("s_new_004", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/new question")

        types = [e.type for e in events]
        assert "slash_error" in types
        assert "slash_switch_session" not in types
        err = next(e for e in events if e.type == "slash_error")
        assert "LLMError" in err.content or "mock LLM down" in err.content

    @pytest.mark.asyncio
    async def test_registered_in_default_commands(self):
        names = {c.name for c in DEFAULT_COMMANDS}
        assert "new" in names
```

注册测试 (TestSlashRegistry.test_has_6_default_commands) 也要改 — `6` → `8` (加 /new + /resume), names 集合加 "new" + "resume". 但 /resume 在 Wave 4 加, 这里先把已有 test 改成允许 superset:

直接 patch 现有断言:

```python
# tests/test_chat_slash_commands.py:17 改
def test_has_required_default_commands(self):
    names = {c.name for c in DEFAULT_COMMANDS}
    # 至少包含这些 (Wave 4 后会再加 resume)
    required = {"quit", "help", "show", "budget", "compact", "save", "new"}
    assert required.issubset(names)
```

(Wave 4 再把 "resume" 加进 required 集合.)

**Step 3.2: 跑测试确认 fail**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestSlashNew -v`
Expected: 全部 FAIL (handler 不存在).

**Step 3.3: 实装 _handle_new + 注册**

在 `src/explain_engine/chat/slash_commands.py` 加 imports + handler. import 加到顶部 (lazy import 避免 ChatSession 循环 import — 类似现有 handler 用 from-inside-function 模式).

在 `_handle_save` 之后, registry 之前 加:

```python
async def _handle_new(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """/new <question> — 建新 session (bootstrap + HITL) + 切到它.

    完整复用 cli `new` 命令路径 (bootstrap_phenomena → review_phenomena →
    SessionStore.save). 之后 yield 两个 event:
    - slash_new: info text 给用户
    - slash_switch_session: signal REPL 切到新 sid (REPL 单 turn iter 结束后做)

    失败时只 yield slash_error, 不 yield switch → REPL 留原 session.

    Args:
        chat: 当前 ChatSession (拿 chat.llm 调 bootstrap)
        args: split-by-whitespace 后的 token list, 重组成 question 文本
    """
    import asyncio

    from explain_engine.chat.session import ChatEvent
    from explain_engine.config import Settings
    from explain_engine.engines.bootstrap import bootstrap_phenomena
    from explain_engine.hitl.cli_interactive import review_phenomena
    from explain_engine.llm.errors import LLMError, SchemaValidationError
    from explain_engine.persistence.session import (
        Session,
        SessionMeta,
        SessionStore,
    )
    from explain_engine.schema.state import CognitiveState

    question = " ".join(args).strip()
    if not question:
        return [ChatEvent(
            type="slash_error",
            content="Usage: /new <你的问题>  (例: /new 为什么 X 现象)",
        )]

    if chat.llm is None:
        return [ChatEvent(
            type="slash_error",
            content="/new 需要 LLM client; 当前 ChatSession 启动时未传 llm "
                    "(test path or backward-compat caller).",
        )]

    # Bootstrap (调 LLM)
    try:
        phenomena = await bootstrap_phenomena(question, chat.llm)
    except (SchemaValidationError, LLMError) as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"/new bootstrap 失败: {type(exc).__name__}: {exc}",
        )]

    # HITL review (sync stdin via Rich Prompt — 包 to_thread 不 block event loop).
    # 用全局 console (cli.py 顶部已 init); handler 不应建新 Console.
    from explain_engine.cli import console
    final_phenomena = await asyncio.to_thread(
        review_phenomena, phenomena, console
    )

    # 建 session + 存
    settings = Settings()
    state = CognitiveState.bootstrap(question, budget=settings.default_budget)
    for p in final_phenomena:
        state.graph.add_node(p)
    meta = SessionMeta.new(question=question)
    sess = Session(meta=meta, state=state)

    store = SessionStore()
    try:
        store.save(sess)
    except OSError as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"/new 存盘失败: {exc}",
        )]

    return [
        ChatEvent(
            type="slash_new",
            content=f"Session {meta.session_id} 已创建 ({len(final_phenomena)} 现象).",
        ),
        ChatEvent(
            type="slash_switch_session",
            content={"sid": meta.session_id},
        ),
    ]
```

修改 DEFAULT_COMMANDS tuple, 在 _handle_save 后加:

```python
DEFAULT_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("quit", "Exit chat session (saves first).", _handle_quit),
    SlashCommand("help", "List slash commands and available tools.", _handle_help),
    SlashCommand("show", "Show graph snapshot + multi-signal.", _handle_show),
    SlashCommand("budget", "Show per-turn / per-session budget remaining.", _handle_budget),
    SlashCommand("compact", "Force trigger sessionMemory compaction.", _handle_compact),
    SlashCommand("save", "Explicit flush of all sidecar files.", _handle_save),
    SlashCommand("new", "新建 session (bootstrap + HITL) 后自动切.", _handle_new),
)
```

把现有 docstring 第一行 (`"""Phase 9 Wave F.1: 6 default slash commands ..."""`) 改为 `"""Phase 9 Wave F.1 + 2026-05-18: 7 default slash commands (含 /new). ..."""` (Wave 4 加 /resume 后再改 8).

**Step 3.4: 跑测试确认 pass**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestSlashNew tests/test_chat_slash_commands.py::TestSlashRegistry -v`
Expected: 全 PASS.

**Step 3.5: 跑全测 + lint**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py tests/test_chat_session.py tests/test_cli_chat.py -v`
Expected: 全 PASS.

Run: `.venv/bin/ruff check src/explain_engine/chat/slash_commands.py tests/test_chat_slash_commands.py`
Expected: 0 issue.

**Step 3.6: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/test_chat_slash_commands.py
git commit -m "$(cat <<'EOF'
chat/slash · /new <question> handler 实装

新 slash: /new 在 chat REPL 内建 session 并自动切.
- 完整复用 cli new 路径 (bootstrap_phenomena + review_phenomena + save)
- 失败 (empty args / no llm / bootstrap LLMError / 存盘 OSError) 返 slash_error
  不 yield switch event, REPL 留原 session
- 成功 yield slash_new (info) + slash_switch_session (sid) 两 event,
  REPL Wave 2 已能消费 switch event 做切换

5 unit test 覆盖 (empty / no-llm / success / bootstrap-error / 注册).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 4 — /resume handler

### Task 4: 实装 /resume numbered picker handler

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py` (加 `_handle_resume` + 注册)
- Modify: `tests/test_chat_slash_commands.py` (加 TestSlashResume class)

**Step 4.1: 写 failing test**

加到 `tests/test_chat_slash_commands.py` (TestSlashNew 之后):

```python
class TestSlashResume:
    @pytest.mark.asyncio
    async def test_no_sessions_returns_info(self, monkeypatch):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_res_only")
        chat = ChatSession("s_res_only")
        # Monkey patch list_sessions 返空
        monkeypatch.setattr(
            type(chat.storage), "list_sessions", lambda self: []
        )
        events = await dispatch_slash(chat, "/resume")
        types = [e.type for e in events]
        assert "slash_resume" in types
        assert "slash_switch_session" not in types
        info = next(e for e in events if e.type == "slash_resume")
        assert "无" in info.content or "no session" in info.content.lower()

    @pytest.mark.asyncio
    async def test_args_rejected(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_res_002")
        chat = ChatSession("s_res_002")
        events = await dispatch_slash(chat, "/resume extra")
        assert events[0].type == "slash_error"

    @pytest.mark.asyncio
    async def test_picks_session_yields_switch(self, monkeypatch):
        """2 个 session: 当前 + 另一个. 输入 1 → switch 到 latest (按 created_at desc)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_res_a")
        # 稍 sleep 让 created_at 不同 — 实际上 SessionMeta.new 用 time.time(),
        # 同 ms 内 _make_done_session 调用可能 tied, 用显式 override:
        from explain_engine.persistence.session import (
            Session, SessionMeta, SessionStore
        )
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.state import CognitiveState
        meta_b = SessionMeta.new(question="qb")
        meta_b.session_id = "s_res_b"
        meta_b.created_at = 9999999999.0  # newer than s_res_a
        state_b = CognitiveState(
            graph=ExplanationGraph(root_question="qb"),
            budget_remaining=10, root_question="qb",
        )
        SessionStore().save(Session(meta=meta_b, state=state_b))

        chat = ChatSession("s_res_a")

        # Mock input 返 "1" (选 latest = s_res_b)
        monkeypatch.setattr(
            "builtins.input", lambda *a, **kw: "1"
        )

        events = await dispatch_slash(chat, "/resume")
        types = [e.type for e in events]
        assert "slash_switch_session" in types
        switch_ev = next(e for e in events if e.type == "slash_switch_session")
        assert switch_ev.content["sid"] == "s_res_b"

    @pytest.mark.asyncio
    async def test_invalid_number_cancels(self, monkeypatch):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_res_c")
        chat = ChatSession("s_res_c")
        monkeypatch.setattr(
            "builtins.input", lambda *a, **kw: "abc"
        )
        events = await dispatch_slash(chat, "/resume")
        types = [e.type for e in events]
        assert "slash_error" in types
        assert "slash_switch_session" not in types

    @pytest.mark.asyncio
    async def test_out_of_range_cancels(self, monkeypatch):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_res_d")
        chat = ChatSession("s_res_d")
        monkeypatch.setattr(
            "builtins.input", lambda *a, **kw: "99"
        )
        events = await dispatch_slash(chat, "/resume")
        types = [e.type for e in events]
        assert "slash_error" in types
        assert "slash_switch_session" not in types

    @pytest.mark.asyncio
    async def test_q_cancels(self, monkeypatch):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_res_e")
        chat = ChatSession("s_res_e")
        monkeypatch.setattr(
            "builtins.input", lambda *a, **kw: "q"
        )
        events = await dispatch_slash(chat, "/resume")
        types = [e.type for e in events]
        assert "slash_resume" in types
        assert "slash_switch_session" not in types
        info = next(e for e in events if e.type == "slash_resume")
        assert "取消" in info.content or "cancel" in info.content.lower()

    @pytest.mark.asyncio
    async def test_picking_current_session_noop(self, monkeypatch):
        """只 1 session (当前). 输 1 选自己 → 不 yield switch, 只 info."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_res_only2")
        chat = ChatSession("s_res_only2")
        monkeypatch.setattr(
            "builtins.input", lambda *a, **kw: "1"
        )
        events = await dispatch_slash(chat, "/resume")
        types = [e.type for e in events]
        assert "slash_switch_session" not in types
        info = next(
            e for e in events if e.type == "slash_resume"
        )
        assert "已在" in info.content or "current" in info.content.lower()
```

也更新 TestSlashRegistry:

```python
def test_has_required_default_commands(self):
    names = {c.name for c in DEFAULT_COMMANDS}
    required = {
        "quit", "help", "show", "budget", "compact", "save", "new", "resume"
    }
    assert required.issubset(names)
```

**Step 4.2: 跑测试确认 fail**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestSlashResume -v`
Expected: 全 FAIL.

**Step 4.3: 实装 _handle_resume**

在 `src/explain_engine/chat/slash_commands.py` _handle_new 后加:

```python
async def _handle_resume(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    """/resume — numbered picker 列当前 project 所有 session, 用户选号后切.

    无参数. 列 + 弹 input 收 # → yield slash_switch_session.
    输无效 / out-of-range → slash_error 取消 (无 retry, 保持简单).
    输 q / empty → slash_resume 取消.
    选当前 sid → slash_resume info 'already there', 不 yield switch.

    Args:
        chat: 当前 ChatSession (拿 chat.storage list / chat.sid 比 current)
        args: 必须空 (有 args → reject)
    """
    import asyncio
    from datetime import datetime

    from rich.table import Table

    from explain_engine.chat.session import ChatEvent
    from explain_engine.cli import console
    from explain_engine.persistence.session import SessionStore

    if args:
        return [ChatEvent(
            type="slash_error",
            content="Usage: /resume  (无参数, 弹列表后选号)",
        )]

    sids = chat.storage.list_sessions()
    if not sids:
        return [ChatEvent(
            type="slash_resume",
            content="当前 project 无 session.",
        )]

    # 加载 metadata (轻 — TODO future: SessionStore.load_meta_only)
    sstore = SessionStore()
    metas = []
    for sid in sids:
        try:
            metas.append(sstore.load(sid).meta)
        except Exception:
            continue  # 坏 session 跳过, 不让 picker 整体 crash

    if not metas:
        return [ChatEvent(
            type="slash_resume",
            content="当前 project 无可读 session (全部加载失败).",
        )]

    metas.sort(key=lambda m: m.created_at, reverse=True)

    # 渲染表
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

    # 收 user input
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
        return [ChatEvent(
            type="slash_error",
            content=f"输入需为数字 1-{len(metas)}; 已取消.",
        )]

    idx = int(choice)
    if not (1 <= idx <= len(metas)):
        return [ChatEvent(
            type="slash_error",
            content=f"# {idx} 超范围 (1-{len(metas)}); 已取消.",
        )]

    target_sid = metas[idx - 1].session_id
    if target_sid == chat.sid:
        return [ChatEvent(
            type="slash_resume",
            content=f"已在 session {target_sid}, 不切换.",
        )]

    return [
        ChatEvent(
            type="slash_resume",
            content=f"切换到 session {target_sid}...",
        ),
        ChatEvent(
            type="slash_switch_session",
            content={"sid": target_sid},
        ),
    ]
```

更新 DEFAULT_COMMANDS:

```python
DEFAULT_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("quit", "Exit chat session (saves first).", _handle_quit),
    SlashCommand("help", "List slash commands and available tools.", _handle_help),
    SlashCommand("show", "Show graph snapshot + multi-signal.", _handle_show),
    SlashCommand("budget", "Show per-turn / per-session budget remaining.", _handle_budget),
    SlashCommand("compact", "Force trigger sessionMemory compaction.", _handle_compact),
    SlashCommand("save", "Explicit flush of all sidecar files.", _handle_save),
    SlashCommand("new", "新建 session (bootstrap + HITL) 后自动切.", _handle_new),
    SlashCommand("resume", "列历史 session, 选号后切.", _handle_resume),
)
```

把模块顶 docstring `"7 default slash commands"` → `"8 default slash commands (含 /new + /resume)"`.

**Step 4.4: 跑测试确认 pass**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestSlashResume tests/test_chat_slash_commands.py::TestSlashRegistry -v`
Expected: 全 PASS.

**Step 4.5: 跑全 slash test + lint**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py -v`
Expected: 全 PASS.

Run: `.venv/bin/ruff check src/explain_engine/chat/slash_commands.py tests/test_chat_slash_commands.py`
Expected: 0 issue.

**Step 4.6: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/test_chat_slash_commands.py
git commit -m "$(cat <<'EOF'
chat/slash · /resume numbered picker handler 实装

新 slash: /resume 列当前 project 下所有 session (按 created_at desc),
用户输 # 选择后 yield slash_switch_session 让 REPL 切.

边角:
- 无 session → slash_resume info
- 有 args → slash_error (用法纯无参数)
- 输 q / empty → 取消
- 非数字 / 超范围 → slash_error 取消 (无 retry, 保持简单)
- 选当前 sid → 'already there' info, 不 yield switch

7 unit test 覆盖. 当前 session 用 '*' 标记让用户知道在哪.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 5 — /help 输出 + docstring 同步

### Task 5: 修 slash_commands.py 顶 docstring + 验 /help 自动列新命令

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py:1` (顶 docstring)
- Modify: `tests/test_chat_slash_commands.py` (test_help_lists_commands_and_tools 加 new + resume 验证)

**Step 5.1: 改 /help test 验新命令出现**

修改 `tests/test_chat_slash_commands.py::TestDispatchSlash::test_help_lists_commands_and_tools`:

```python
@pytest.mark.asyncio
async def test_help_lists_commands_and_tools(self):
    from explain_engine.chat.session import ChatSession
    _make_done_session("s_51a55002")
    chat = ChatSession("s_51a55002")
    events = await dispatch_slash(chat, "/help")
    content = events[0].content
    # Should list all 8 slash commands (含 2026-05-18 新增 /new + /resume)
    for name in [
        "quit", "help", "show", "budget", "compact", "save", "new", "resume"
    ]:
        assert f"/{name}" in content
    assert "expand" in content
```

**Step 5.2: 跑测试**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestDispatchSlash::test_help_lists_commands_and_tools -v`
Expected: PASS (因为 _handle_help 是 for cmd in DEFAULT_COMMANDS 自动列, Wave 3 + 4 注册后自动出现).

**Step 5.3: 修 docstring**

`src/explain_engine/chat/slash_commands.py:1` 改为:

```python
"""Phase 9 Wave F.1 + 2026-05-18: 8 default slash commands (local intercept, bypass LLM).

设计参考 Claude Code 同款 slash 模式 — 本地 intercept 不走 LLM,
廉价 inspection + exit + force compact 等管理命令; slash 不计入
transcript / turn_count (因为非真正 user→assistant 对话).

⚠️ 例外: /new 调 LLM (bootstrap_phenomena 必须). 这是 2026-05-18 新增
设计的妥协 — /new 必然需要 bootstrap, 否则就要求 user 切回 cli new 命令再回
chat, 体验割裂. /new 失败 (LLM error / 存盘 error) 返 slash_error, 不 yield
switch event, REPL 留原 session.

每个 SlashCommand:
- name: str (e.g. "quit")
- description: str (shown in /help)
- handler: async (chat, args: list[str]) -> list[ChatEvent]
  返 list[ChatEvent] (multiple events 可能).

设计参考 docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave F.1.
设计参考 docs/plans/2026-05-18-chat-new-resume-slash-design.md (/new + /resume).
"""
```

**Step 5.4: 全测 + lint baseline 验证**

Run: `.venv/bin/python -m pytest -x`
Expected: 全 PASS (基线 649 + 新增测试 ~12-14 → 应 ~661-663 total).

Run: `.venv/bin/ruff check src/ tests/`
Expected: 0 issue.

**Step 5.5: 手动 smoke (optional, 但建议跑一次)**

如果用户 .env 有 LLM 配置可跑:

```bash
# 准备: 至少有一个 session
.venv/bin/python -m explain_engine new "smoke test question"

# 进 chat
.venv/bin/python -m explain_engine chat <existing_sid>

# 在 chat 内测:
/help         # 应列 8 commands
/resume       # 应列 session 表 + 弹 input
/new 测试新会话  # 应跑 bootstrap (有 LLM 输出) → review → 切到新 sid
/quit         # 退出
```

**Step 5.6: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/test_chat_slash_commands.py
git commit -m "$(cat <<'EOF'
chat/slash · 同步 docstring 到 8 commands + /help test 加新命令

顶 docstring 8 commands, 加 /new 调 LLM 的例外说明.
test_help_lists_commands_and_tools 验 /help 输出含 /new + /resume.

完成 2026-05-18 chat-new-resume-slash design 落地 5 Wave.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Acceptance checklist

落地完成后, 检查:

- [ ] 全测 PASS: `.venv/bin/python -m pytest -x` (~661-663 total)
- [ ] ruff 0: `.venv/bin/ruff check src/ tests/`
- [ ] `git log --oneline dev ^master` 显示 6 commit (design + 5 Wave)
- [ ] /help 在 chat REPL 内列 8 commands
- [ ] (Manual) /new 跑通 bootstrap + HITL + 切; /resume 列表 + 选号 + 切
- [ ] 切换后 chat_state budget 是 cli flag 值 (不是 default 10/50)

## Risk 回顾

- /resume 加载所有 graph (O(N) graph IO) — 当前可接受; 100+ session 时加 SessionStore.load_meta_only TODO
- /new 期间 chat REPL 进入 review_phenomena (Rich Prompt block stdin); 用户期望 ("HITL"), 非 bug
- handler 通过 `from explain_engine.cli import console` 拿全局 console — 这造成 chat module → cli module 反向依赖. cli 已 import slash_commands 间接, 看是否破循环 import. 如果破 → 改成把 console 实例存到 ChatSession.console 字段 (init 时传入), handler 用 chat.console

如最后一条 risk 触发, 处理方案:

```python
# slash_commands.py 改:
# from explain_engine.cli import console  # 不行
# 改用 chat.console (Wave 2 _run_chat_repl_async 里 chat_session.console = console)
```

但当前先 import 试; 实测撞 import error 再改.

---

## 参考

- Design doc: [2026-05-18-chat-new-resume-slash-design.md](2026-05-18-chat-new-resume-slash-design.md)
- 相关 Phase 9 文档: [conversational-cognitive-engine-plan.md](2026-05-17-conversational-cognitive-engine-plan.md) Wave F.1
- 现有 slash registry: [src/explain_engine/chat/slash_commands.py](../../src/explain_engine/chat/slash_commands.py)
- 现有 REPL: [src/explain_engine/cli.py:889](../../src/explain_engine/cli.py#L889)
- bootstrap engine: [src/explain_engine/engines/bootstrap.py](../../src/explain_engine/engines/bootstrap.py)
- HITL review: [src/explain_engine/hitl/cli_interactive.py](../../src/explain_engine/hitl/cli_interactive.py)
