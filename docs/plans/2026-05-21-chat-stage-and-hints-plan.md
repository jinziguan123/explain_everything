# Chat Stage Flow + Next-Step Hints Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Chat REPL 内 5 个 mutating slash 命令 (`/compress` / `/run` / `/predict` / `/counterfactual` / `/rescore`) 通过装饰器统一加 stage gate + stage transition + 静态 next-step hint. 用户全程在 REPL 完整推进 session, 命令完后看到"下一步可选"灰色提示.

**Architecture:** 单文件 decorator + 静态规则表 + hint 文案表 (`chat/slash_stage_rules.py`). 5 handler 各加 1 行装饰. 新 event type `slash_next_step_hint` REPL 灰色 dim 渲染. `_handle_compress` 内部加 mid-stage `insight_pending` transition + 重跑短路 LLM (跟 cli 对齐). `_handle_help` 加 6 分组渲染.

**Tech Stack:** Python 3.11 + asyncio, pytest + pytest-mock, Rich (REPL render), Pydantic v2 dataclass-style ChatEvent. Project uses `.venv/bin/python -m pytest` (uv-managed venv) + `.venv/bin/ruff check`.

**Design doc:** [docs/plans/2026-05-21-chat-stage-and-hints-design.md](2026-05-21-chat-stage-and-hints-design.md) — read §4 (Design) + §5 (handler 改动) before starting.

---

## Task 1: Skeleton — `slash_stage_rules.py` decorator pass-through

**Files:**
- Create: `src/explain_engine/chat/slash_stage_rules.py`
- Test: `tests/test_chat_slash_stage_rules.py`

**Step 1: Write the failing test**

```python
# tests/test_chat_slash_stage_rules.py
"""Phase 14: slash_stage_rules decorator tests (装饰器 4 步: gate / 业务 / transition / hint)."""

import pytest

from explain_engine.chat.session import ChatEvent
from explain_engine.chat.slash_stage_rules import with_stage_gate


class TestPassThrough:
    @pytest.mark.asyncio
    async def test_no_gate_no_transition_no_hint_passes_through(self):
        """allowed=None + success_stage=None + 无 hint → handler events 原样返."""

        @with_stage_gate(
            allowed=None,
            success_stage=None,
            fail_hint_key=None,
            success_hint_key=None,
        )
        async def handler(chat, args):
            return [ChatEvent(type="slash_ok", content="done")]

        events = await handler(_FakeChat(stage="bootstrap_pending"), [])
        assert len(events) == 1
        assert events[0].type == "slash_ok"
        assert events[0].content == "done"


class _FakeChat:
    """Minimal duck-typed chat for decorator unit tests."""

    def __init__(self, stage: str = "done", is_ephemeral: bool = False) -> None:
        self._session = _FakeSession(stage)
        self.is_ephemeral = is_ephemeral
        self._persist_count = 0

    def persist(self) -> None:
        self._persist_count += 1


class _FakeSession:
    def __init__(self, stage: str) -> None:
        self.meta = _FakeMeta(stage)


class _FakeMeta:
    def __init__(self, stage: str) -> None:
        self.stage = stage
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_stage_rules.py::TestPassThrough -v`
Expected: FAIL with `ImportError: cannot import name 'with_stage_gate' from 'explain_engine.chat.slash_stage_rules'`

**Step 3: Write minimal implementation**

```python
# src/explain_engine/chat/slash_stage_rules.py
"""Phase 14: chat slash 命令的 stage gate + transition + hint 装饰器."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from explain_engine.chat.session import ChatEvent, ChatSession

Handler = Callable[["ChatSession", list[str]], Awaitable[list["ChatEvent"]]]


HINTS_BY_KEY: dict[str, str] = {}  # 后续 task 填


def with_stage_gate(
    *,
    allowed: list[str] | None = None,
    success_stage: str | None = None,
    fail_hint_key: str | None = None,
    success_hint_key: str | None = None,
) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        @wraps(fn)
        async def wrapped(chat, args):
            # Pass-through: 后续 task 加 gate / transition / hint
            return await fn(chat, args)
        return wrapped
    return deco
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_stage_rules.py::TestPassThrough -v`
Expected: PASS (1 test)

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_stage_rules.py tests/test_chat_slash_stage_rules.py
git commit -m "chat/slash_stage_rules · Phase 14 Task 1: decorator skeleton (pass-through)"
```

---

## Task 2: Decorator gate check (allowed=[...] + stage 不符 → slash_error)

**Files:**
- Modify: `src/explain_engine/chat/slash_stage_rules.py`
- Test: `tests/test_chat_slash_stage_rules.py`

**Step 1: Write the failing test**

```python
# Append to tests/test_chat_slash_stage_rules.py

class TestGateCheck:
    @pytest.mark.asyncio
    async def test_blocks_disallowed_stage(self):
        """stage=bp + allowed=[done] → slash_error, handler 不被调."""
        called = {"n": 0}

        @with_stage_gate(allowed=["done"])
        async def handler(chat, args):
            called["n"] += 1
            return [ChatEvent(type="slash_ok", content="should not run")]

        events = await handler(_FakeChat(stage="bootstrap_pending"), [])
        assert called["n"] == 0
        assert len(events) == 1
        assert events[0].type == "slash_error"
        assert "bootstrap_pending" in events[0].content
        assert "['done']" in events[0].content or "'done'" in events[0].content

    @pytest.mark.asyncio
    async def test_allows_listed_stage(self):
        """stage=done + allowed=[done] → handler 被调."""

        @with_stage_gate(allowed=["done"])
        async def handler(chat, args):
            return [ChatEvent(type="slash_ok", content="ran")]

        events = await handler(_FakeChat(stage="done"), [])
        assert events[0].type == "slash_ok"

    @pytest.mark.asyncio
    async def test_allowed_none_means_any_stage(self):
        """allowed=None → 任意 stage 都允许."""

        @with_stage_gate(allowed=None)
        async def handler(chat, args):
            return [ChatEvent(type="slash_ok", content="ran")]

        for stage in ("bootstrap_pending", "insight_pending", "done", "converged"):
            events = await handler(_FakeChat(stage=stage), [])
            assert events[0].type == "slash_ok"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_stage_rules.py::TestGateCheck -v`
Expected: 3 FAIL (decorator currently pass-through, gate not enforced)

**Step 3: Implement gate check**

Modify `with_stage_gate` 的 `wrapped`:

```python
async def wrapped(chat, args):
    from explain_engine.chat.session import ChatEvent
    stage = chat._session.meta.stage

    # Gate check
    if allowed is not None and stage not in allowed:
        return [ChatEvent(
            type="slash_error",
            content=(
                f"/{_cmd_name(fn)} 在当前 stage={stage!r} 不允许 "
                f"(需 stage ∈ {allowed})."
            ),
        )]

    return await fn(chat, args)
```

Add module-level helper:

```python
def _cmd_name(fn) -> str:
    """`_handle_run` → `run`. 用于 error 文案."""
    name = fn.__name__
    return name.removeprefix("_handle_") if name.startswith("_handle_") else name
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_stage_rules.py -v`
Expected: 4 PASS (1 from Task 1 + 3 new)

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_stage_rules.py tests/test_chat_slash_stage_rules.py
git commit -m "chat/slash_stage_rules · Phase 14 Task 2: gate check (allowed=[...])"
```

---

## Task 3: Decorator gate fail → 附 slash_next_step_hint event

**Files:**
- Modify: `src/explain_engine/chat/slash_stage_rules.py`
- Test: `tests/test_chat_slash_stage_rules.py`

**Step 1: Write the failing test**

```python
class TestFailHint:
    @pytest.mark.asyncio
    async def test_fail_hint_appended_when_key_in_table(self):
        """gate 失败 + fail_hint_key 在 HINTS_BY_KEY → 加 hint event."""
        from explain_engine.chat.slash_stage_rules import HINTS_BY_KEY

        # 临时插入 test key
        HINTS_BY_KEY["__test_fail__"] = "test fail message"
        try:
            @with_stage_gate(allowed=["done"], fail_hint_key="__test_fail__")
            async def handler(chat, args):
                return [ChatEvent(type="slash_ok", content="x")]

            events = await handler(_FakeChat(stage="bootstrap_pending"), [])
            types = [e.type for e in events]
            assert "slash_error" in types
            assert "slash_next_step_hint" in types
            hint = next(e for e in events if e.type == "slash_next_step_hint")
            assert hint.content == "test fail message"
        finally:
            HINTS_BY_KEY.pop("__test_fail__", None)

    @pytest.mark.asyncio
    async def test_no_fail_hint_when_key_none(self):
        @with_stage_gate(allowed=["done"], fail_hint_key=None)
        async def handler(chat, args):
            return [ChatEvent(type="slash_ok", content="x")]

        events = await handler(_FakeChat(stage="bootstrap_pending"), [])
        assert all(e.type != "slash_next_step_hint" for e in events)

    @pytest.mark.asyncio
    async def test_no_fail_hint_when_key_missing_from_table(self):
        """fail_hint_key 指向不存在的 key → 静默跳 (不抛)."""

        @with_stage_gate(allowed=["done"], fail_hint_key="nonexistent_key")
        async def handler(chat, args):
            return [ChatEvent(type="slash_ok", content="x")]

        events = await handler(_FakeChat(stage="bootstrap_pending"), [])
        assert all(e.type != "slash_next_step_hint" for e in events)
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_stage_rules.py::TestFailHint -v`
Expected: 1 FAIL (test_fail_hint_appended_when_key_in_table — hint not appended yet), 2 PASS by accident

**Step 3: Implement fail hint append**

Modify gate fail branch in `wrapped`:

```python
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
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_stage_rules.py -v`
Expected: 7 PASS

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_stage_rules.py tests/test_chat_slash_stage_rules.py
git commit -m "chat/slash_stage_rules · Phase 14 Task 3: gate fail → append fail hint event"
```

---

## Task 4: Decorator success transition + persist

**Files:**
- Modify: `src/explain_engine/chat/slash_stage_rules.py`
- Test: `tests/test_chat_slash_stage_rules.py`

**Step 1: Write the failing test**

```python
class TestSuccessTransition:
    @pytest.mark.asyncio
    async def test_stage_updated_on_success(self):
        """handler 返无 error + success_stage 设 → stage 更新."""
        chat = _FakeChat(stage="done")

        @with_stage_gate(allowed=["done"], success_stage="converged")
        async def handler(c, args):
            return [ChatEvent(type="slash_run", content="ok")]

        await handler(chat, [])
        assert chat._session.meta.stage == "converged"

    @pytest.mark.asyncio
    async def test_persist_called_on_success_transition(self):
        chat = _FakeChat(stage="done")

        @with_stage_gate(allowed=["done"], success_stage="converged")
        async def handler(c, args):
            return [ChatEvent(type="slash_run", content="ok")]

        await handler(chat, [])
        assert chat._persist_count == 1

    @pytest.mark.asyncio
    async def test_no_transition_when_handler_returns_error(self):
        """handler 自己 yield slash_error → 不 transition."""
        chat = _FakeChat(stage="done")

        @with_stage_gate(allowed=["done"], success_stage="converged")
        async def handler(c, args):
            return [ChatEvent(type="slash_error", content="business logic 出错")]

        await handler(chat, [])
        assert chat._session.meta.stage == "done"  # 未推
        assert chat._persist_count == 0

    @pytest.mark.asyncio
    async def test_no_transition_when_success_stage_none(self):
        chat = _FakeChat(stage="done")

        @with_stage_gate(allowed=["done"], success_stage=None)
        async def handler(c, args):
            return [ChatEvent(type="slash_predict", content="ok")]

        await handler(chat, [])
        assert chat._session.meta.stage == "done"

    @pytest.mark.asyncio
    async def test_idempotent_transition_no_double_persist(self):
        """stage 已等于 success_stage → 不 persist (no-op)."""
        chat = _FakeChat(stage="converged")

        @with_stage_gate(allowed=["converged"], success_stage="converged")
        async def handler(c, args):
            return [ChatEvent(type="slash_x", content="ok")]

        await handler(chat, [])
        assert chat._persist_count == 0  # 没改值, 不重复 persist
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_stage_rules.py::TestSuccessTransition -v`
Expected: 5 FAIL (transition + persist not implemented)

**Step 3: Implement transition + persist**

Append to `wrapped` after handler call:

```python
events = await fn(chat, args)

# handler 自己 yield error → 不 transition, 不 hint
has_error = any(e.type == "slash_error" for e in events)
if has_error:
    return events

# success path: stage transition + persist
if success_stage is not None and stage != success_stage:
    chat._session.meta.stage = success_stage
    if (
        hasattr(chat, "persist")
        and not getattr(chat, "is_ephemeral", False)
    ):
        try:
            chat.persist()
        except Exception:
            pass

return events
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_stage_rules.py -v`
Expected: 12 PASS

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_stage_rules.py tests/test_chat_slash_stage_rules.py
git commit -m "chat/slash_stage_rules · Phase 14 Task 4: success transition + persist"
```

---

## Task 5: Decorator success hint append

**Files:**
- Modify: `src/explain_engine/chat/slash_stage_rules.py`
- Test: `tests/test_chat_slash_stage_rules.py`

**Step 1: Write the failing test**

```python
class TestSuccessHint:
    @pytest.mark.asyncio
    async def test_success_hint_appended_when_key_in_table(self):
        from explain_engine.chat.slash_stage_rules import HINTS_BY_KEY
        HINTS_BY_KEY["__test_success__"] = "test success message"
        try:
            @with_stage_gate(allowed=["done"], success_hint_key="__test_success__")
            async def handler(c, args):
                return [ChatEvent(type="slash_run", content="ok")]

            events = await handler(_FakeChat(stage="done"), [])
            assert events[-1].type == "slash_next_step_hint"
            assert events[-1].content == "test success message"
        finally:
            HINTS_BY_KEY.pop("__test_success__", None)

    @pytest.mark.asyncio
    async def test_no_success_hint_when_handler_returns_error(self):
        @with_stage_gate(allowed=["done"], success_hint_key="after_run")
        async def handler(c, args):
            return [ChatEvent(type="slash_error", content="x")]

        events = await handler(_FakeChat(stage="done"), [])
        assert all(e.type != "slash_next_step_hint" for e in events)
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_stage_rules.py::TestSuccessHint -v`
Expected: 1 FAIL + 1 PASS

**Step 3: Implement success hint**

Append before `return events` in success path:

```python
if success_hint_key and success_hint_key in HINTS_BY_KEY:
    events.append(ChatEvent(
        type="slash_next_step_hint",
        content=HINTS_BY_KEY[success_hint_key],
    ))

return events
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_stage_rules.py -v`
Expected: 14 PASS

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_stage_rules.py tests/test_chat_slash_stage_rules.py
git commit -m "chat/slash_stage_rules · Phase 14 Task 5: success hint append"
```

---

## Task 6: 填 HINTS_BY_KEY 6 个 key

**Files:**
- Modify: `src/explain_engine/chat/slash_stage_rules.py`
- Test: `tests/test_chat_slash_stage_rules.py`

**Step 1: Write the failing test**

```python
class TestHintsByKey:
    def test_all_six_keys_present(self):
        from explain_engine.chat.slash_stage_rules import HINTS_BY_KEY
        expected = {
            "need_promote_first",
            "need_compress_first",
            "after_compress",
            "after_run",
            "after_inference",
            "after_rescore",
        }
        assert expected.issubset(HINTS_BY_KEY.keys())

    def test_hint_content_mentions_relevant_commands(self):
        from explain_engine.chat.slash_stage_rules import HINTS_BY_KEY
        # after_compress 应 mention /run / /predict / /counterfactual
        c = HINTS_BY_KEY["after_compress"]
        assert "/run" in c
        assert "/predict" in c
        # need_compress_first 应 mention /compress
        assert "/compress" in HINTS_BY_KEY["need_compress_first"]
        # after_rescore 应 mention /show
        assert "/show" in HINTS_BY_KEY["after_rescore"]
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_stage_rules.py::TestHintsByKey -v`
Expected: 2 FAIL (HINTS_BY_KEY 还是空 dict)

**Step 3: Fill HINTS_BY_KEY**

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

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_stage_rules.py -v`
Expected: 16 PASS

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_stage_rules.py tests/test_chat_slash_stage_rules.py
git commit -m "chat/slash_stage_rules · Phase 14 Task 6: HINTS_BY_KEY 填 6 文案"
```

---

## Task 7: ChatEvent contract + cli REPL render

**Files:**
- Modify: `src/explain_engine/chat/session.py` (ChatEvent docstring)
- Modify: `src/explain_engine/cli.py` (`_render_event`)
- Test: `tests/test_cli_chat.py`

**Step 1: Write the failing test**

加 test 到 `tests/test_cli_chat.py` (或新文件 `tests/test_cli_render_hint.py`):

```python
class TestRenderHintEvent:
    def test_hint_rendered_with_dim_style(self):
        from io import StringIO
        from rich.console import Console
        from explain_engine.chat.session import ChatEvent
        from explain_engine.cli import _render_event

        buf = StringIO()
        con = Console(file=buf, force_terminal=False, width=100)
        ev = ChatEvent(
            type="slash_next_step_hint",
            content="▸ 下一步: /run",
        )
        _render_event(con, ev)
        output = buf.getvalue()
        assert "▸ 下一步: /run" in output
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_chat.py::TestRenderHintEvent -v`
Expected: PASS by accident (because `_render_event` has generic fallback for `slash_*` type prefix that prints content). But not dim-styled. Test only checks content present.

Actually verify: current `_render_event` (cli.py:961):
```python
elif t.startswith("slash_"):
    con.print(escape(str(event.content or "")))
```
So `slash_next_step_hint` already routes to this generic branch — content gets printed. Test passes immediately. The actual visible difference is **dim style** which the test doesn't check.

Skip this test → just add `slash_next_step_hint` explicit branch + ChatEvent docstring update + manually verify dim in acceptance smoke.

**Step 3: Implement dim render branch + docstring**

In `cli.py:_render_event`, before the generic `slash_` catch-all:

```python
elif t == "slash_next_step_hint":
    con.print(f"[dim]{escape(str(event.content or ''))}[/dim]")
```

In `chat/session.py` ChatEvent docstring (`content` payload contract section), add:

```
- slash_next_step_hint: str — 灰色 dim 渲染. 在普通 slash output event 之后,
  给用户提示当前 stage 下推荐的下一步命令. Producer: with_stage_gate decorator
  (slash_stage_rules.py).
```

**Step 4: Run pytest (verify nothing broke)**

Run: `.venv/bin/python -m pytest tests/test_cli_chat.py -v`
Expected: All existing tests PASS

**Step 5: Commit**

```bash
git add src/explain_engine/cli.py src/explain_engine/chat/session.py
git commit -m "chat/session + cli · Phase 14 Task 7: slash_next_step_hint event + dim render"
```

---

## Task 8: `_make_done_session` fixture 加 stage 参数

**Files:**
- Modify: `tests/test_chat_session.py:_make_done_session`
- Test: `tests/test_chat_session.py` (新 test)

**Step 1: Write the failing test**

```python
class TestMakeDoneSessionFixture:
    """Helper fixture 支持自定义 stage (Phase 14 stage gate test 用)."""

    def test_default_stage_is_done(self):
        from tests.test_chat_session import _make_done_session
        from explain_engine.persistence.session import SessionStore
        _make_done_session("s_fff00001")
        assert SessionStore().load("s_fff00001").meta.stage == "done"

    def test_stage_param_creates_bootstrap_pending(self):
        from tests.test_chat_session import _make_done_session
        from explain_engine.persistence.session import SessionStore
        _make_done_session("s_fff00002", stage="bootstrap_pending")
        assert SessionStore().load("s_fff00002").meta.stage == "bootstrap_pending"

    def test_stage_param_creates_insight_pending(self):
        from tests.test_chat_session import _make_done_session
        from explain_engine.persistence.session import SessionStore
        _make_done_session("s_fff00003", stage="insight_pending")
        assert SessionStore().load("s_fff00003").meta.stage == "insight_pending"

    def test_stage_param_creates_converged(self):
        from tests.test_chat_session import _make_done_session
        from explain_engine.persistence.session import SessionStore
        _make_done_session("s_fff00004", stage="converged")
        assert SessionStore().load("s_fff00004").meta.stage == "converged"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_session.py::TestMakeDoneSessionFixture -v`
Expected: 3 FAIL (`_make_done_session` 不支持 stage 参数)

**Step 3: Modify `_make_done_session`**

Find `_make_done_session` in `tests/test_chat_session.py` (top of file). Add `stage` param:

```python
def _make_done_session(sid: str, stage: str = "done") -> None:
    """Phase 14 (2026-05-21): 加 stage 参数. Default "done" 向后兼容."""
    # ... existing implementation, but use stage param for SessionMeta:
    meta = SessionMeta(
        session_id=sid,
        question="...",
        stage=stage,  # was hardcoded "done"
        created_at=...,
        updated_at=...,
    )
    # ... rest unchanged
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_session.py -v`
Expected: All PASS (含 4 个新 + 既有)

**Step 5: Commit**

```bash
git add tests/test_chat_session.py
git commit -m "tests · Phase 14 Task 8: _make_done_session 加 stage 参数 (向后兼容)"
```

---

## Task 9: 装饰 `_handle_run` + 新 TestSlashStageGate test

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py:_handle_run`
- Test: `tests/test_chat_slash_commands.py`

**Step 1: Write the failing test**

```python
# tests/test_chat_slash_commands.py 末加
class TestSlashStageGateRun:
    @pytest.mark.asyncio
    async def test_run_blocked_at_bootstrap_pending(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_g0000001", stage="bootstrap_pending")
        chat = ChatSession("s_g0000001", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/run")
        types = [e.type for e in events]
        assert "slash_error" in types
        assert "slash_next_step_hint" in types
        hint = next(e for e in events if e.type == "slash_next_step_hint")
        assert "/compress" in hint.content

    @pytest.mark.asyncio
    async def test_run_blocked_at_converged(self):
        """已经 converged 重跑 /run 也拒 (需 stage=done 精确)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_g0000002", stage="converged")
        chat = ChatSession("s_g0000002", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/run")
        assert any(e.type == "slash_error" for e in events)
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestSlashStageGateRun -v`
Expected: 2 FAIL (no gate enforcement yet, /run handler 直接跑)

**Step 3: Apply decorator to `_handle_run`**

In `slash_commands.py`, import + decorate:

```python
from explain_engine.chat.slash_stage_rules import with_stage_gate

@with_stage_gate(
    allowed=["done"],
    success_stage="converged",
    fail_hint_key="need_compress_first",
    success_hint_key="after_run",
)
async def _handle_run(chat: ChatSession, args: list[str]) -> list[ChatEvent]:
    # 现有 body 0 改动
    ...
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestSlashStageGateRun -v`
Expected: 2 PASS

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/test_chat_slash_commands.py
git commit -m "chat/slash · Phase 14 Task 9: decorate _handle_run (gate done→converged)"
```

---

## Task 10: 装饰 `_handle_predict`

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py:_handle_predict`
- Test: `tests/test_chat_slash_commands.py`

**Step 1: Write the failing test**

```python
class TestSlashStageGatePredict:
    @pytest.mark.asyncio
    async def test_predict_blocked_at_bootstrap_pending(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_g0000003", stage="bootstrap_pending")
        chat = ChatSession("s_g0000003", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/predict 测试")
        assert any(e.type == "slash_error" for e in events)
        assert any(e.type == "slash_next_step_hint" for e in events)

    @pytest.mark.asyncio
    async def test_predict_allowed_at_done(self, monkeypatch):
        # Mock prediction module 避免真调
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_g0000004", stage="done")
        # ... mock prediction_mod.predict ... (照 TestSlashPredict 既有 mock pattern)
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestSlashStageGatePredict -v`
Expected: 1 FAIL (test_predict_blocked_at_bootstrap_pending)

**Step 3: Apply decorator**

```python
@with_stage_gate(
    allowed=["done", "converged"],
    success_stage=None,
    fail_hint_key="need_compress_first",
    success_hint_key="after_inference",
)
async def _handle_predict(chat, args):
    ...
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestSlashStageGatePredict -v`
Expected: PASS

**Step 5: Commit**

```bash
git commit -m "chat/slash · Phase 14 Task 10: decorate _handle_predict (gate done|converged)"
```

---

## Task 11: 装饰 `_handle_counterfactual` (覆盖 /cf alias)

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py:_handle_counterfactual`
- Test: `tests/test_chat_slash_commands.py`

**Step 1-5**: 同 Task 10 (config: `allowed=["done","converged"]`, `success_stage=None`, `fail_hint_key="need_compress_first"`, `success_hint_key="after_inference"`). 加 test 验证 `/cf` (alias) 也走 gate (DEFAULT_COMMANDS 指 `_handle_counterfactual` 同一 wrapped 函数, 自动覆盖).

```bash
git commit -m "chat/slash · Phase 14 Task 11: decorate _handle_counterfactual (含 /cf alias)"
```

---

## Task 12: 装饰 `_handle_rescore` (任意 stage)

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py:_handle_rescore`
- Test: `tests/test_chat_slash_commands.py`

**Step 1: Write the failing test**

```python
class TestSlashStageGateRescore:
    @pytest.mark.asyncio
    async def test_rescore_allowed_at_any_stage(self, monkeypatch):
        """rescore allowed=None → bootstrap_pending 也允许 (不 gate)."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_g0000005", stage="bootstrap_pending")
        chat = ChatSession("s_g0000005", llm=object())  # type: ignore[arg-type]
        # Mock rescore_session 不真调 LLM
        async def fake_rescore(state, llm):
            return {}
        monkeypatch.setattr(
            "explain_engine.engines.rescore.rescore_session", fake_rescore,
        )
        events = await dispatch_slash(chat, "/rescore")
        # 没 stage error (但可能有别 error e.g. 空 graph, 不重要)
        assert all(
            "不允许" not in (e.content if isinstance(e.content, str) else "")
            for e in events
        )

    @pytest.mark.asyncio
    async def test_rescore_yields_after_rescore_hint(self, monkeypatch):
        # 成功路径验证 after_rescore hint
        ...  # similar pattern
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py::TestSlashStageGateRescore -v`
Expected: hint test fails (no hint event yet)

**Step 3: Apply decorator**

```python
@with_stage_gate(
    allowed=None,
    success_stage=None,
    fail_hint_key=None,
    success_hint_key="after_rescore",
)
async def _handle_rescore(chat, args):
    ...
```

**Step 4: Run test to verify it passes**

Expected: PASS

**Step 5: Commit**

```bash
git commit -m "chat/slash · Phase 14 Task 12: decorate _handle_rescore (任意 stage)"
```

---

## Task 13: 装饰 `_handle_compress` (gate only, mid-stage 留 Task 14)

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py:_handle_compress`
- Test: `tests/test_chat_slash_commands.py`

**Step 1: Write the failing test**

```python
class TestSlashStageGateCompress:
    @pytest.mark.asyncio
    async def test_compress_blocked_at_done(self):
        """重跑 /compress on done session → gate 拒."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_g0000006", stage="done")
        chat = ChatSession("s_g0000006", llm=object())  # type: ignore[arg-type]
        events = await dispatch_slash(chat, "/compress")
        assert any(e.type == "slash_error" for e in events)

    @pytest.mark.asyncio
    async def test_compress_allowed_at_bootstrap_pending(self, monkeypatch):
        """bp 允许 — gate 不拒. 用 mock 全跑 + 验 stage=done."""
        # Apply existing TestSlashCompress 的 fake_propose / fake_score /
        # fake_review_insights_async / fake_flush mock pattern.
        # 完成后 assert chat._session.meta.stage == "done"
```

**Step 2: Run test to verify it fails**

Expected: test_compress_blocked_at_done FAIL (current 没 gate), test_compress_allowed_at_bootstrap_pending 可能 FAIL 因 stage 不变.

**Step 3: Apply decorator**

```python
@with_stage_gate(
    allowed=["bootstrap_pending", "insight_pending"],
    success_stage="done",
    fail_hint_key="need_promote_first",
    success_hint_key="after_compress",
)
async def _handle_compress(chat, args):
    # body 0 改动 (mid-stage 留下 task)
    ...
```

**Step 4: Run test to verify it passes**

Expected: PASS (gate + transition 通过装饰器, mid-stage 还没加)

**Step 5: Commit**

```bash
git commit -m "chat/slash · Phase 14 Task 13: decorate _handle_compress (gate bp|ip → done)"
```

---

## Task 14: `_handle_compress` 加 insight_pending mid-stage transition

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py:_handle_compress` (body)
- Test: `tests/test_chat_slash_commands.py`

**Step 1: Write the failing test**

```python
class TestCompressMidStageResilience:
    @pytest.mark.asyncio
    async def test_mid_stage_set_after_score(self, monkeypatch):
        """propose+score 完之后, review 之前, stage 应已是 insight_pending + persisted."""
        from explain_engine.chat.session import ChatSession
        from explain_engine.persistence.session import SessionStore
        _make_done_session("s_g0000007", stage="bootstrap_pending")
        chat = ChatSession("s_g0000007", llm=object())  # type: ignore[arg-type]

        # propose / score mock OK
        async def fake_propose(state, llm, existing_lexicon=None):
            pass
        async def fake_score(state, llm):
            pass
        monkeypatch.setattr(
            "explain_engine.engines.compression.propose_candidates", fake_propose,
        )
        monkeypatch.setattr(
            "explain_engine.engines.evaluation.score_all", fake_score,
        )

        # review_insights_async 抛 KeyboardInterrupt 模拟用户取消
        async def fake_review_cancel(state, console=None, input_provider=None):
            raise KeyboardInterrupt()
        monkeypatch.setattr(
            "explain_engine.hitl.cli_interactive.review_insights_async",
            fake_review_cancel,
        )

        try:
            await dispatch_slash(chat, "/compress")
        except KeyboardInterrupt:
            pass

        # Verify stage saved as ip on disk (mid-stage persist)
        meta = SessionStore().load("s_g0000007").meta
        assert meta.stage == "insight_pending"
```

**Step 2: Run test to verify it fails**

Expected: FAIL (stage 仍 bp because mid-stage transition 没加)

**Step 3: Implement mid-stage transition**

Modify `_handle_compress` body — propose+score 完 + review_insights_async 之前:

```python
async def _handle_compress(chat, args):
    if getattr(chat, "is_ephemeral", False):
        return _ephemeral_reject("compress")
    if chat.llm is None:
        return [ChatEvent(type="slash_error", content="...")]

    from rich.console import Console
    from explain_engine.engines.compression import propose_candidates
    from explain_engine.engines.evaluation import score_all
    from explain_engine.engines.lexicon import (
        flush_to_lexicon, get_lexicon_top_k_for_compress,
    )
    from explain_engine.hitl.cli_interactive import review_insights_async
    _console = Console()

    # propose
    try:
        top_k = get_lexicon_top_k_for_compress(chat.storage, k=20)
        with _console.status("[bold green]调 LLM 提候选 (compress)...[/bold green]"):
            await propose_candidates(chat.state, chat.llm, existing_lexicon=top_k)
    except Exception as exc:
        return [ChatEvent(type="slash_error", content=f"...")]

    # score
    try:
        await score_all(chat.state, chat.llm)
    except Exception as exc:
        return [ChatEvent(type="slash_error", content=f"...")]

    # NEW: mid-stage persist (中断恢复)
    chat._session.meta.stage = "insight_pending"
    chat.persist()
    _console.print(
        "[dim](中间状态已保存, 即便 review 取消也能下次重入跳过 LLM)[/dim]"
    )

    # review + flush + persist
    # ... (existing)
```

**Step 4: Run test to verify it passes**

Expected: PASS

**Step 5: Commit**

```bash
git commit -m "chat/slash · Phase 14 Task 14: _handle_compress mid-stage insight_pending"
```

---

## Task 15: `_handle_compress` ip 入口短路 LLM

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py:_handle_compress`
- Test: `tests/test_chat_slash_commands.py`

**Step 1: Write the failing test**

```python
class TestCompressInsightPendingShortCircuit:
    @pytest.mark.asyncio
    async def test_ip_entry_skips_propose_and_score(self, monkeypatch):
        """stage=ip 入口跑 /compress → propose / score 不被调."""
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_g0000008", stage="insight_pending")
        chat = ChatSession("s_g0000008", llm=object())  # type: ignore[arg-type]

        calls = {"propose": 0, "score": 0}
        async def track_propose(*a, **kw):
            calls["propose"] += 1
        async def track_score(*a, **kw):
            calls["score"] += 1
        monkeypatch.setattr("...propose_candidates", track_propose)
        monkeypatch.setattr("...score_all", track_score)
        # Mock review + flush 成功
        async def fake_review(*a, **kw):
            pass
        async def fake_flush(*a, **kw):
            return 0
        monkeypatch.setattr("...review_insights_async", fake_review)
        monkeypatch.setattr("...flush_to_lexicon", fake_flush)

        await dispatch_slash(chat, "/compress")
        assert calls["propose"] == 0
        assert calls["score"] == 0
```

**Step 2: Run test to verify it fails**

Expected: FAIL (propose / score 在 ip 入口仍调)

**Step 3: Add short-circuit**

Wrap propose+score block with `if current_stage != "insight_pending"`:

```python
async def _handle_compress(chat, args):
    # ...
    current_stage = chat._session.meta.stage
    
    if current_stage == "insight_pending":
        _console.print(
            "[dim](检测到 stage=insight_pending, 跳过 LLM 直接进入审查)[/dim]"
        )
    else:
        # bootstrap_pending 入口: 跑 propose + score
        try:
            top_k = ...
            await propose_candidates(...)
        except Exception as exc:
            return [...]
        try:
            await score_all(...)
        except Exception as exc:
            return [...]
        
        # mid-stage persist
        chat._session.meta.stage = "insight_pending"
        chat.persist()

    # review + flush + persist (两 entry 都走)
    # ...
```

**Step 4: Run test to verify it passes**

Expected: PASS

**Step 5: Commit**

```bash
git commit -m "chat/slash · Phase 14 Task 15: _handle_compress ip 入口短路 LLM"
```

---

## Task 16: `_handle_help` 6 分组渲染

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py:_handle_help`
- Test: `tests/test_chat_slash_commands.py`

**Step 1: Write the failing test**

```python
class TestHelpGrouping:
    @pytest.mark.asyncio
    async def test_help_includes_all_six_group_headers(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_g0000009")
        chat = ChatSession("s_g0000009")
        events = await dispatch_slash(chat, "/help")
        content = events[0].content
        for header in (
            "Session 推进",
            "Session 干预",
            "Inspection",
            "Session 管理",
            "其他",
            "帮助 / 退出",
        ):
            assert header in content, f"missing group header: {header}"

    @pytest.mark.asyncio
    async def test_help_includes_all_commands(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_g000000a")
        chat = ChatSession("s_g000000a")
        events = await dispatch_slash(chat, "/help")
        content = events[0].content
        # 19 commands all present
        for cmd in (
            "compress", "run", "rescore", "predict", "counterfactual",
            "show", "graph", "check", "new", "resume", "list", "lexicon",
            "budget", "compact", "save", "migrate", "help", "quit",
        ):
            assert f"/{cmd}" in content, f"missing /{cmd}"
```

**Step 2: Run test to verify it fails**

Expected: 2 FAIL (current /help 是平铺无 group headers)

**Step 3: Refactor `_handle_help`**

```python
HELP_GROUPS: list[tuple[str, list[str]]] = [
    ("Session 推进", ["compress", "run", "rescore"]),
    ("Session 干预 (需先 /compress)", ["predict", "counterfactual"]),
    ("Inspection (read-only)", ["show", "graph", "check"]),
    ("Session 管理", ["new", "resume", "list", "lexicon"]),
    ("其他", ["budget", "compact", "save", "migrate"]),
    ("帮助 / 退出", ["help", "quit"]),
]


async def _handle_help(chat, args):
    from explain_engine.chat.session import ChatEvent
    cmd_by_name = {c.name: c for c in DEFAULT_COMMANDS}
    
    lines = ["Available slash commands:\n"]
    for group_name, cmd_names in HELP_GROUPS:
        lines.append(f"  {group_name}:")
        for n in cmd_names:
            if n in cmd_by_name:
                c = cmd_by_name[n]
                lines.append(f"    /{c.name} — {c.description}")
        lines.append("")  # blank between groups
    
    # /cf alias 单独提一行
    if "cf" in cmd_by_name:
        lines.append("  Alias: /cf → /counterfactual")
    
    return [ChatEvent(type="slash_help", content="\n".join(lines))]
```

**Step 4: Run test to verify it passes**

Expected: PASS

**Step 5: Commit**

```bash
git commit -m "chat/slash · Phase 14 Task 16: _handle_help 6 分组渲染"
```

---

## Task 17: 既有 test 迁移 — length assertion + bp session

**Files:**
- Modify: `tests/test_chat_slash_commands.py` (TestSlashCompress / Run / Predict / Counterfactual / Rescore 既有 ~25 case)

**Step 1: Run full pytest 看哪些 fail**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py -v`
Expected: ~15-25 FAIL (length assert / stage gate)

**Step 2: Migrate failing tests**

Scan failing tests, fix:
- `len(events) == 1` → `len(events) >= 1` 或 `events[0].type == "slash_xxx"` (ignore hint at end)
- `_make_done_session(sid)` for /compress tests → `_make_done_session(sid, stage="bootstrap_pending")`

逐 test 修, 单 test 跑通后再下个. 不批量改避免 regression.

**Step 3: Run full pytest**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py -v`
Expected: ALL PASS

**Step 4: Run ruff**

Run: `.venv/bin/ruff check src/ tests/`
Expected: All checks passed

**Step 5: Commit**

```bash
git commit -m "tests · Phase 14 Task 17: 既有 slash test 迁移 (stage gate + hint event)"
```

---

## Task 18: Acceptance smoke doc

**Files:**
- Create: `docs/plans/2026-05-21-chat-stage-and-hints-acceptance.md`

**Step 1: 写 acceptance doc**

参考 design doc §6.4 + 之前 brainstorming Section 4E 10 步, 写完整 manual smoke.

```markdown
# Phase 14 Acceptance: Chat Stage Flow + Next-Step Hints

**Date**: 2026-05-21

## Manual Smoke Test (10 步)

### 1. Ephemeral REPL 启动
```bash
.venv/bin/python -m explain_engine.cli
```
预期: 输出 `Explain REPL — ephemeral session.` banner.

### 2. 自然语言输入 → promote 到真 session
```
> 为什么年轻人储蓄少了
```
预期: bootstrap 完, 进入真 ChatSession.

### 3. /show 验 stage=bootstrap_pending
```
> /show
```
预期: 输出含 `Stage: bootstrap_pending`.

### 4. /run 拒 + hint
```
> /run
```
预期:
- slash_error 提到 `bootstrap_pending` + `[done]`
- slash_next_step_hint (灰色) 提到 `/compress`

### 5. /compress → 推 done + hint
```
> /compress
[propose / score / HITL review / lexicon flush]
```
预期:
- 完成提示 (`compress 完, N candidate 写入 lexicon` 之类)
- 灰色 hint: `▸ 下一步可选: /run ...`
- `/show` 验 stage=done

### 6. /run → 推 converged + hint
```
> /run
[reasoning loop]
```
预期:
- reasoning 完成
- 灰色 hint: `▸ session 已收敛...`
- `/show` 验 stage=converged

### 7. /predict <text> → stage 不变 + hint
```
> /predict 假设 X 行业资本撤退
```
预期:
- 预测完成
- 灰色 hint: `▸ 可继续 /predict 或 /counterfactual ...`
- `/show` 验 stage 仍 converged

### 8. /compress 重跑 → 拒 (stage=converged) + hint
```
> /compress
```
预期: slash_error + hint.

### 9. /help → 6 分组
```
> /help
```
预期: 输出含 6 个 group header (`Session 推进:` 等) + 19 命令全在.

### 10. Mid-stage resilience
- /new → reset
- 自然语言 → promote
- /compress → 中途 HITL review 时输 `q` 取消
- `/show` 验 stage=insight_pending
- /compress 再跑 → 验 `(检测到 stage=insight_pending, 跳过 LLM 直接进入审查)` 提示, 直接进 review
- 完成 review → stage=done

## 通过标准

10 步全过 = Phase 14 acceptance pass.
```

**Step 2: Commit**

```bash
git add docs/plans/2026-05-21-chat-stage-and-hints-acceptance.md
git commit -m "docs · Phase 14 Task 18: acceptance smoke doc"
```

---

## Task 19: 全量验证 + ruff + 修最后边角

**Files:**
- 任何遗漏修复

**Step 1: Run full pytest**

Run: `.venv/bin/python -m pytest`
Expected: ALL PASS (936 baseline + ~20-30 new — 但有 ~25 老 test 改了所以 net 大概 940+)

**Step 2: Run ruff**

Run: `.venv/bin/ruff check src/ tests/`
Expected: All checks passed.

**Step 3: 如有 fail/警告**

修. 单独 commit.

**Step 4: 跑 acceptance smoke** (跟 doc §18 一致)

如果手动 smoke 任一步 fail, 回去找问题点修.

**Step 5: 最终 commit (如有变更)**

```bash
git commit -m "Phase 14 · 最终 polish + verify (pytest + ruff + acceptance smoke)"
```

---

## 总结

19 个 task 完成 = Phase 14 chat stage flow + next-step hints 全部上线.

预期最终 git log (反序):
```
xxxxxxx Phase 14 · 最终 polish + verify
xxxxxxx docs · Phase 14 Task 18: acceptance smoke doc
xxxxxxx tests · Phase 14 Task 17: 既有 slash test 迁移
xxxxxxx chat/slash · Phase 14 Task 16: _handle_help 6 分组渲染
xxxxxxx chat/slash · Phase 14 Task 15: _handle_compress ip 入口短路 LLM
xxxxxxx chat/slash · Phase 14 Task 14: _handle_compress mid-stage insight_pending
xxxxxxx chat/slash · Phase 14 Task 13: decorate _handle_compress
xxxxxxx chat/slash · Phase 14 Task 12: decorate _handle_rescore
xxxxxxx chat/slash · Phase 14 Task 11: decorate _handle_counterfactual
xxxxxxx chat/slash · Phase 14 Task 10: decorate _handle_predict
xxxxxxx chat/slash · Phase 14 Task 9: decorate _handle_run
xxxxxxx tests · Phase 14 Task 8: _make_done_session 加 stage 参数
xxxxxxx chat/session + cli · Phase 14 Task 7: slash_next_step_hint event + dim render
xxxxxxx chat/slash_stage_rules · Phase 14 Task 6: HINTS_BY_KEY 填 6 文案
xxxxxxx chat/slash_stage_rules · Phase 14 Task 5: success hint append
xxxxxxx chat/slash_stage_rules · Phase 14 Task 4: success transition + persist
xxxxxxx chat/slash_stage_rules · Phase 14 Task 3: gate fail → append fail hint event
xxxxxxx chat/slash_stage_rules · Phase 14 Task 2: gate check (allowed=[...])
xxxxxxx chat/slash_stage_rules · Phase 14 Task 1: decorator skeleton
xxxxxxx docs/plans · Phase 14 (待实装) chat stage flow + next-step hints 设计
```

## Skills Used

- `superpowers:executing-plans` (per task TDD discipline)
- `superpowers:test-driven-development` (TDD red-green-commit cycle)
- `superpowers:verification-before-completion` (Task 19 final smoke)
