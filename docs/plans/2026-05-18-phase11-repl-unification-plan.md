# Phase 11 REPL Unification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `explain` 默认进 prompt_toolkit REPL ephemeral, 首句自然语言 implicit /new + 持久化. 12 typer cmd 全做 slash (无 explicit 参数, 走 input_provider sequential prompt). `/budget` config 流取代 cli flag. 含 Wave 0 deepseek-v4-pro 400 bug fix.

**Architecture:** 7 wave TDD: Wave 0 LLM retry + JSON prompt 强约束 → Wave 1 EphemeralChatSession + cli 入口重构 → Wave 2 HITL async → Wave 2.5 /budget config → Wave 3 6 single-session slash → Wave 4 3 cross-session slash → Wave 5 acceptance.

**Tech Stack:** Python 3.11+ / Pydantic v2 / pytest + pytest-asyncio / Typer / Rich / prompt_toolkit 3 / uv-managed venv

**Setup pre-flight:**
- 分支: `dev` (HEAD `b9726cb` — design doc 已 commit)
- 全测基线: `.venv/bin/python -m pytest -x` 应 728 PASS
- Lint: `.venv/bin/ruff check src/ tests/` 应 0
- Design 参考: [docs/plans/2026-05-18-phase11-repl-unification-design.md](2026-05-18-phase11-repl-unification-design.md)

---

## Wave 0 — deepseek-v4-pro 400 bug fix

### Task 0: prompt JSON schema 强约束 + retry on SchemaValidationError

**问题诊断 (用户报告)**:
- compress 调 LLM 频繁 HTTP 400
- log: `Forced tool_choice rejected by model (deepseek-v4-pro); retrying with auto`
- fallback 后 LLM 返 free text, Pydantic catch malformed

**Files:**
- Modify: `src/explain_engine/llm/anthropic_protocol.py` (auto fallback 之后 wrap retry)
- Modify: `src/explain_engine/llm/prompts/compression.yaml` + `variable_extraction.yaml` (加 JSON schema instruction)
- Modify: `src/explain_engine/engines/_llm_retry.py` (若已有 retry helper, 扩 schema-error retry)
- Test: `tests/test_llm_retry.py` (or new `tests/test_llm_anthropic_retry.py`)

**Step 0.1: 先 read 现状**

```bash
.venv/bin/python -c "from pathlib import Path; print(Path('src/explain_engine/engines/_llm_retry.py').read_text())"
.venv/bin/python -c "from pathlib import Path; print(Path('src/explain_engine/llm/anthropic_protocol.py').read_text()[:3000])"
```

了解 `_llm_retry.py` 现有 helper (是否已有 retry decorator?). 了解 `anthropic_protocol.py` L79-90 forced→auto fallback 逻辑.

**Step 0.2: 加 JSON schema instruction 到 compression + variable_extraction prompt**

Read `src/explain_engine/llm/prompts/compression.yaml` 看现有 prompt 结构. 在 system 或 user prompt 末尾加 (具体位置看 yaml 结构):

```yaml
# 在现有 prompt 末尾追加 (system 或 user, 看 yaml 结构):
response_format_hint: |

  IMPORTANT: Respond ONLY with valid JSON matching this exact schema (no markdown, no explanation):
  {
    "candidates": [
      {"name": "<string>", "description": "<string>", ...}
    ]
  }

  If you cannot determine the answer, return {"candidates": []} - do NOT include free text.
```

类似改 `variable_extraction.yaml` (bootstrap_phenomena 用).

**Step 0.3: 写 failing test — retry on SchemaValidationError**

Create or extend `tests/test_llm_retry.py`:

```python
"""Wave 0: anthropic_protocol retry on malformed LLM response."""

import pytest
from unittest.mock import AsyncMock

from explain_engine.llm.errors import LLMError, SchemaValidationError


class TestAnthropicProtocolRetry:
    @pytest.mark.asyncio
    async def test_retry_on_malformed_response(self):
        """LLM 第一次返 free text (Pydantic 拒), 第二次返 valid JSON → 成功."""
        # mock 设 2 次 response, 第一次 free text, 第二次 valid
        # 验 chat() 内 retry 2 次后返 parsed valid
        ...

    @pytest.mark.asyncio
    async def test_max_retries_then_raise(self):
        """LLM 3 次都返 malformed → raise SchemaValidationError (含 raw text 在 err msg)."""
        ...

    @pytest.mark.asyncio
    async def test_valid_first_time_no_retry(self):
        """LLM 第一次就 valid → 不 retry, mock called 1 次."""
        ...
```

具体 mock pattern look 现有 `tests/test_chat_loop.py` or 现 LLM mock test 文件.

**Step 0.4: 跑测试 fail**

```bash
.venv/bin/python -m pytest tests/test_llm_retry.py::TestAnthropicProtocolRetry -v
```
Expected: FAIL — retry logic 不存在.

**Step 0.5: 实装 retry**

`src/explain_engine/llm/anthropic_protocol.py` 现有 `chat()` method. 在 forced→auto fallback 之后 (L89 之后) wrap 整个 chat call 在 retry loop:

```python
MAX_RETRIES_ON_MALFORMED = 2  # 总 3 次调用 (initial + 2 retry)

async def chat(self, messages, schema=None, ...):
    last_raw_text = None
    for attempt in range(MAX_RETRIES_ON_MALFORMED + 1):
        try:
            # 现有 anthropic SDK 调用 logic
            response = await self._raw_chat(...)
            if schema:
                # 现有 Pydantic 解析
                parsed = schema.model_validate(response.parsed)
                return Response(..., parsed=parsed)
            return response
        except (ValidationError, json.JSONDecodeError) as exc:
            last_raw_text = response.text if 'response' in locals() else None
            if attempt < MAX_RETRIES_ON_MALFORMED:
                logger.warning(
                    "LLM response malformed (attempt %d/%d), retrying with JSON-only reminder. "
                    "Raw text preview: %s",
                    attempt + 1, MAX_RETRIES_ON_MALFORMED + 1,
                    (response.text or "")[:200] if 'response' in locals() else "(no response)",
                )
                # Append reminder to messages
                messages = list(messages) + [Message(
                    role="user",
                    content=(
                        "Previous response was not valid JSON. "
                        "Please respond with ONLY valid JSON matching the requested schema, "
                        "no markdown, no explanation."
                    ),
                )]
                continue
            # final attempt failed
            raise SchemaValidationError(
                f"LLM returned malformed response after {MAX_RETRIES_ON_MALFORMED + 1} attempts. "
                f"Raw text (last attempt): {last_raw_text[:500] if last_raw_text else 'None'}"
            ) from exc
```

**注意**: 具体 splice 点 看现 `_raw_chat` / `chat` 结构. 可能在 `_llm_retry.py` 加 decorator 更干净.

**Step 0.6: 跑测试 pass**

```bash
.venv/bin/python -m pytest tests/test_llm_retry.py -v
```
Expected: PASS.

**Step 0.7: 全测 + ruff**

```bash
.venv/bin/python -m pytest -x --tb=no -q 2>&1 | tail -3
```
Expected: 728 + 3 = 731 PASS.

```bash
.venv/bin/ruff check src/explain_engine/llm/ src/explain_engine/engines/_llm_retry.py tests/test_llm_retry.py
```
Expected: 0.

**Step 0.8: Commit**

```bash
git add src/explain_engine/llm/ src/explain_engine/engines/_llm_retry.py tests/test_llm_retry.py
git commit -m "$(cat <<'EOF'
llm/anthropic · 修 deepseek-v4-pro 400 bug (Wave 0)

用户报告 compress 频繁撞 HTTP 400: forced tool_choice rejected →
auto fallback 后 LLM 返 free text, Pydantic catch malformed.

Fix:
- compression.yaml + variable_extraction.yaml prompt 末尾加 JSON schema
  强约束 ("Respond ONLY with valid JSON matching schema, no markdown")
- anthropic_protocol.chat 加 retry 2 次 on ValidationError /
  JSONDecodeError. 每次 retry append "Previous response malformed,
  respond ONLY with valid JSON" reminder
- 3 次都失败 → SchemaValidationError 含 raw text preview, 改善
  diagnostics

3 new test (retry success / max retries / no retry on first valid).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 1 — EphemeralChatSession + cli 入口重构

### Task 1.A: 新 EphemeralChatSession dataclass

**Files:**
- Create: `src/explain_engine/chat/ephemeral.py`
- Create: `tests/test_chat_ephemeral.py`

**Step 1.A.1: 写 failing test**

Create `tests/test_chat_ephemeral.py`:

```python
"""Wave 1: EphemeralChatSession lifecycle + promote tests."""

import pytest
from unittest.mock import AsyncMock

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.persistence.storage_v2 import StorageV2


class TestEphemeralChatSession:
    def test_init_no_sid(self):
        """Ephemeral 永无 sid."""
        eph = EphemeralChatSession(storage=StorageV2())
        assert eph.sid is None
        assert eph.is_ephemeral

    def test_empty_state(self):
        """初始 graph 空, transcript 空."""
        eph = EphemeralChatSession(storage=StorageV2())
        assert len(eph.state.graph.nodes) == 0
        assert eph.transcript == []

    def test_default_chat_state(self):
        """default budget 10/50 (Phase 9 ChatStateDict)."""
        eph = EphemeralChatSession(storage=StorageV2())
        assert eph.chat_state.budget_per_turn_limit == 10
        assert eph.chat_state.budget_per_session_limit == 50

    def test_input_provider_default_none(self):
        """input_provider 默认 None, REPL 启动时 set."""
        eph = EphemeralChatSession(storage=StorageV2())
        assert eph.input_provider is None


class TestPromoteToPersistent:
    @pytest.mark.asyncio
    async def test_promote_creates_session(self, monkeypatch):
        """promote_to_persistent 跑 bootstrap + 建 real Session + save."""
        from explain_engine.schema.nodes import VariableNode
        from explain_engine.persistence.session import SessionStore

        # mock bootstrap_phenomena 返 1 phenomenon
        async def fake_bootstrap(question, llm, lexicon=None, lexicon_top_k=20):
            return [VariableNode(
                id="p_001", name="test", description="test",
                abstraction_level=0, confidence=0.7, epistemic="observation",
            )]
        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.bootstrap_phenomena", fake_bootstrap
        )

        # mock review_phenomena_async (Wave 2 实装, 但 test 用 fake)
        async def fake_review(phenomena, input_provider, console=None):
            return phenomena  # accept all
        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.review_phenomena_async", fake_review
        )

        eph = EphemeralChatSession(storage=StorageV2())
        eph.input_provider = AsyncMock()

        mock_llm = AsyncMock()
        real_chat = await eph.promote_to_persistent("why?", mock_llm)

        assert real_chat.sid is not None
        assert not getattr(real_chat, "is_ephemeral", False)
        # session 已 save
        assert any(m.session_id == real_chat.sid for m in SessionStore().list())

    @pytest.mark.asyncio
    async def test_promote_propagates_chat_state(self, monkeypatch):
        """promote 后 ephemeral.chat_state (含 /budget 改的) 拷贝到 real ChatSession."""
        # similar setup, 加 eph.chat_state.budget_per_turn_limit = 99 before promote
        # 验 real_chat.chat_state.budget_per_turn_limit == 99
        ...

    @pytest.mark.asyncio
    async def test_promote_bootstrap_error_keeps_ephemeral(self, monkeypatch):
        """bootstrap LLM error → raise, ephemeral 不消失 (caller 保留)."""
        from explain_engine.llm.errors import LLMError

        async def fake_bootstrap(*args, **kwargs):
            raise LLMError("api down")
        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.bootstrap_phenomena", fake_bootstrap
        )

        eph = EphemeralChatSession(storage=StorageV2())
        mock_llm = AsyncMock()
        with pytest.raises(LLMError):
            await eph.promote_to_persistent("why?", mock_llm)
```

**Step 1.A.2: 实装 ephemeral.py**

```python
"""Phase 11 Wave 1: EphemeralChatSession — in-memory chat before persistent /new.

REPL 启动时建. 用户输自然语言时 promote_to_persistent 把 question 跑
bootstrap+HITL+save, 切到真 ChatSession.

设计 §5: docs/plans/2026-05-18-phase11-repl-unification-design.md
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from explain_engine.chat.session import ChatSession, ChatStateDict
from explain_engine.engines.bootstrap import bootstrap_phenomena
from explain_engine.hitl.cli_interactive import review_phenomena_async  # Wave 2 实装
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.state import CognitiveState

if TYPE_CHECKING:
    from explain_engine.llm.client import LLMClient
    from explain_engine.persistence.storage_v2 import StorageV2


@dataclass
class EphemeralChatSession:
    """In-memory chat session pre-persistence."""

    storage: "StorageV2"
    llm: "LLMClient | None" = None
    state: CognitiveState = field(
        default_factory=lambda: CognitiveState.bootstrap("", budget=0)
    )
    chat_state: ChatStateDict = field(default_factory=ChatStateDict)
    transcript: list[dict] = field(default_factory=list)
    input_provider: "Callable[[str], Awaitable[str]] | None" = None
    memory_md: str = ""

    @property
    def sid(self) -> None:
        return None

    @property
    def is_ephemeral(self) -> bool:
        return True

    async def promote_to_persistent(
        self, question: str, llm: "LLMClient",
    ) -> ChatSession:
        """Trigger bootstrap+HITL+save, 返 real ChatSession.

        失败 (bootstrap LLM error) → 抛, caller 留 ephemeral.
        """
        # Phase 10 lexicon prior
        from explain_engine.engines.lexicon import _load_lexicon
        lexicon_path = self.storage.knowledge_dir() / "variables.json"
        lexicon_data = _load_lexicon(lexicon_path)
        lexicon = lexicon_data["variables"]

        # bootstrap (raise → caller 留 ephemeral)
        phenomena = await bootstrap_phenomena(
            question, llm, lexicon=lexicon, lexicon_top_k=20,
        )

        # HITL async (Wave 2 实装的 review_phenomena_async)
        final_phenomena = await review_phenomena_async(
            phenomena, self.input_provider,
        )

        # Build + save Session
        from explain_engine.config import Settings
        settings = Settings()
        new_state = CognitiveState.bootstrap(question, budget=settings.default_budget)
        for p in final_phenomena:
            new_state.graph.add_node(p)

        meta = SessionMeta.new(question=question)
        session = Session(meta=meta, state=new_state)
        SessionStore().save(session)

        # Build real ChatSession + copy ephemeral chat_state (含 /budget 改的)
        real_chat = ChatSession(meta.session_id, llm=llm)
        real_chat.chat_state = self.chat_state
        real_chat.input_provider = self.input_provider
        return real_chat
```

**Step 1.A.3 ~ 1.A.5**: 跑 fail → impl → pass → 全测 → ruff.

Note: 此时 `review_phenomena_async` 还没实装 (Wave 2). 用 monkeypatch 替换. test_promote_creates_session 已 mock.

**Step 1.A.6: Commit**

```bash
git add src/explain_engine/chat/ephemeral.py tests/test_chat_ephemeral.py
git commit -m "..."
```

### Task 1.B: enter_repl_async + cli 入口重构

**Files:**
- Create: `src/explain_engine/chat/repl_entry.py`
- Modify: `src/explain_engine/cli.py` (app 配置 + @app.callback)
- Test: `tests/test_cli_main_entry.py`

**Step 1.B.1: 实装 enter_repl_async**

```python
# src/explain_engine/chat/repl_entry.py
"""Phase 11 Wave 1: enter_repl_async — explain 默认 entry, ephemeral REPL.

设计 §3.1: docs/plans/2026-05-18-phase11-repl-unification-design.md
"""

from __future__ import annotations

import asyncio
import logging

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.chat.repl_input import (
    BufferedLogHandler,
    make_session,
    read_input,
)
from explain_engine.chat.session import ChatSession
from explain_engine.config import make_llm_client
from explain_engine.persistence.storage_v2 import StorageV2


async def enter_repl_async() -> None:
    """Ephemeral REPL outer loop.

    1. Build EphemeralChatSession + input_provider
    2. while loop: 读 input
       - slash → dispatch_slash
       - 自然语言 → ephemeral.promote_to_persistent → real ChatSession
    3. 切到真 chat 后, 走现 query_loop (Phase 9 chat REPL)
    """
    storage = StorageV2()
    llm = make_llm_client()
    log_handler = BufferedLogHandler(capacity=200)
    log_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    root_logger.handlers = [log_handler]
    root_logger.setLevel(logging.INFO)

    try:
        pt_session = make_session(log_handler)

        async def _input_provider(prompt_text):
            return await read_input(pt_session, prompt_text=prompt_text)

        chat: EphemeralChatSession | ChatSession = EphemeralChatSession(
            storage=storage, llm=llm, input_provider=_input_provider,
        )

        console.print(
            "[bold green]Explain REPL[/bold green] — ephemeral session. "
            "输入问题创建持久 session, /help 看 slash, /quit 退出."
        )

        while True:
            try:
                text = await read_input(pt_session)
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]退出.[/dim]")
                break

            text = text.strip()
            if not text:
                continue

            # slash 路径
            if text.startswith("/"):
                # dispatch slash — 支持 ephemeral context
                from explain_engine.chat.slash_commands import dispatch_slash
                events = await dispatch_slash(chat, text)
                for ev in events:
                    _render_event(console, ev)
                    if ev.type == "slash_quit":
                        return
                    if ev.type == "slash_switch_session":
                        new_sid = ev.content["sid"]
                        chat = ChatSession(new_sid, llm=llm)
                        chat.input_provider = _input_provider
                continue

            # 自然语言 — ephemeral 时 promote, 非 ephemeral 时走 query_loop
            if isinstance(chat, EphemeralChatSession):
                try:
                    chat = await chat.promote_to_persistent(text, llm)
                    console.print(
                        f"[green]Session {chat.sid} 已创建, 进入 chat 模式.[/green]"
                    )
                except Exception as exc:
                    console.print(f"[red]建 session 失败: {exc}[/red]")
                continue

            # 真 chat — 走 query_loop (复用现 ChatSession.handle_user_input)
            try:
                async for ev in chat.handle_user_input(text, llm=llm):
                    _render_event(console, ev)
                    if ev.type == "slash_quit":
                        return
            except Exception as exc:
                console.print(f"[red]Error: {exc}[/red]")
                continue

    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
```

**Step 1.B.2: 改 cli.py 主入口**

```python
# cli.py:50-55 现状: app = typer.Typer(no_args_is_help=True)
# 改成:
app = typer.Typer(
    help="Cognitive Engine for explanation-centric reasoning",
    invoke_without_command=True,
    no_args_is_help=False,  # 旧默认 True, 改 False
)


@app.callback()
def main(ctx: typer.Context) -> None:
    """Default: 进 REPL ephemeral. 传 subcommand 走老 cli."""
    if ctx.invoked_subcommand is None:
        from explain_engine.chat.repl_entry import enter_repl_async
        asyncio.run(enter_repl_async())
        raise typer.Exit()
```

**Step 1.B.3: test_cli_main_entry.py**

```python
class TestCliMainEntry:
    def test_no_subcommand_enters_repl(self, monkeypatch):
        """`explain` 无参数 → enter_repl_async called."""
        called = []

        async def fake_enter():
            called.append(True)

        monkeypatch.setattr(
            "explain_engine.chat.repl_entry.enter_repl_async", fake_enter
        )
        runner = CliRunner()
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert called == [True]

    def test_subcommand_still_typer(self, monkeypatch):
        """`explain list` 仍走 typer subcommand."""
        runner = CliRunner()
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        # list 命令应该正常输 (即使 empty project)

    def test_help_works(self):
        """`explain --help` 仍 typer 默认."""
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Cognitive Engine" in result.output
```

**Step 1.B.4 ~ 1.B.6**: 跑测试 + ruff + commit.

```bash
git commit -m "$(cat <<'EOF'
chat/repl · Phase 11 Wave 1 — EphemeralChatSession + cli REPL 入口

EphemeralChatSession dataclass (chat/ephemeral.py): in-memory session,
sid=None, promote_to_persistent(question, llm) 跑 bootstrap_phenomena
+ review_phenomena_async (Wave 2 实装) + save → 返 real ChatSession.

enter_repl_async (chat/repl_entry.py): ephemeral 起步 REPL 主循环.
slash → dispatch_slash; 自然语言 → promote 或 query_loop.

cli.py 改 app config (invoke_without_command=True, no_args_is_help=False)
+ @app.callback() invoke enter_repl_async if no subcommand. 老 12 subcommand 0 break.

3 new test (ephemeral init / promote 流 / cli entry 默认 REPL +
subcommand 仍 typer).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 2 — HITL async (review_phenomena_async + review_insights_async)

### Task 2: 重写 HITL 用 input_provider

**Files:**
- Modify: `src/explain_engine/hitl/cli_interactive.py` (加 2 个 async fn)
- Test: `tests/test_hitl_async.py`

**Step 2.1: 写 failing test**

```python
"""Wave 2: review_phenomena_async + review_insights_async tests."""

import pytest
from unittest.mock import AsyncMock


class TestReviewPhenomenaAsync:
    @pytest.mark.asyncio
    async def test_keep_all(self):
        """input_provider 全 'k' → 返全 phenomena."""
        from explain_engine.hitl.cli_interactive import review_phenomena_async
        from explain_engine.schema.nodes import VariableNode

        phenomena = [
            VariableNode(id=f"p_{i:03d}", name=f"p{i}", description="d",
                         abstraction_level=0, confidence=0.7, epistemic="observation")
            for i in range(3)
        ]

        provider_calls = []
        async def fake_provider(prompt):
            provider_calls.append(prompt)
            return "k"  # 全 keep

        result = await review_phenomena_async(phenomena, fake_provider)
        assert len(result) == 3
        assert all(p.name in ["p0", "p1", "p2"] for p in result)

    @pytest.mark.asyncio
    async def test_drop_all(self):
        """input_provider 全 'd' → 返 []."""
        ...

    @pytest.mark.asyncio
    async def test_edit_changes_description(self):
        """input_provider 'e' + new desc → phenomenon.description 更新."""
        ...

    @pytest.mark.asyncio
    async def test_cancel_via_keyboard_interrupt(self):
        """input_provider raise KeyboardInterrupt → review 返 (部分 kept) or 抛?"""
        # 看 sync review_phenomena 行为 — 应该一致
        ...


class TestReviewInsightsAsync:
    @pytest.mark.asyncio
    async def test_keep_all(self):
        ...
```

**Step 2.2: 实装**

读现 `src/explain_engine/hitl/cli_interactive.py` `review_phenomena` + `review_insights`. 加 async 版本:

```python
async def review_phenomena_async(
    phenomena: list[VariableNode],
    input_provider: Callable[[str], Awaitable[str]],
    console: Console | None = None,
) -> list[VariableNode]:
    """Async 版 review_phenomena. multi-step k/e/d 用 input_provider."""
    if console is None:
        console = Console()

    kept = []
    for i, p in enumerate(phenomena, start=1):
        console.print(
            f"[bold]({i}/{len(phenomena)})[/bold] {p.name}: {p.description}"
        )
        action = (await input_provider("k(keep)/e(edit)/d(drop): ")).strip().lower()
        if action == "k":
            kept.append(p)
        elif action == "e":
            new_desc = (await input_provider("新 description: ")).strip()
            if new_desc:
                p = p.model_copy(update={"description": new_desc})
            kept.append(p)
        elif action == "d":
            continue
        else:
            console.print(f"[yellow]unknown action {action!r}, treating as keep[/yellow]")
            kept.append(p)
    return kept


async def review_insights_async(
    state: CognitiveState,
    input_provider: Callable[[str], Awaitable[str]],
    console: Console | None = None,
) -> list[str]:
    """Async 版 review_insights. 跟 sync 版行为等价."""
    # 看 review_insights 现 sync 版逻辑 + 改 await input_provider
    ...
```

**Step 2.3 ~ 2.6**: 跑 fail → impl → pass → ruff → commit.

```bash
git commit -m "$(cat <<'EOF'
hitl · Phase 11 Wave 2 — review_phenomena_async + review_insights_async

新加 async 版本, 用 input_provider (Callable[[str], Awaitable[str]])
收 input — 跟 prompt_toolkit chat REPL 集成. sync 版保留 (cli new
--no-chat / cli compress 仍用).

multi-step k/e/d loop 改 await input_provider 每 step. 行为 1:1 与 sync
等价 (input_provider == input 时输出 byte-equal).

~5 test (keep all / drop all / edit changes desc / cancel /
review_insights 类似 path).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 2.5 — /budget config slash + 删 cli flag

### Task 2.5: /budget interactive config

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py` (重写 `_handle_budget`)
- Modify: `src/explain_engine/cli.py` (删 `--tool-budget-per-turn` flag)
- Test: `tests/test_chat_slash_commands.py` (扩 budget config test)

**Step 2.5.1: 重写 _handle_budget**

```python
async def _handle_budget(chat, args):
    """Phase 11 Wave 2.5: 改 /budget 成 interactive config (取代 cli flag)."""
    cs = chat.chat_state

    # Display current
    console = Console()
    console.print(
        f"[bold]Current budget[/bold]\n"
        f"  per-turn limit:    {cs.budget_per_turn_limit}  "
        f"(剩余 {cs.budget_per_turn_remaining})\n"
        f"  per-session limit: {cs.budget_per_session_limit}  "
        f"(剩余 {cs.budget_per_session_remaining})\n"
    )

    if chat.input_provider is None:
        return [ChatEvent(
            type="slash_budget",
            content="(no input_provider; 仅 display 不可改 — test/non-REPL 路径)",
        )]

    # Sequential prompt
    try:
        new_turn_str = await chat.input_provider(
            f"新 per-turn limit (回车保持 {cs.budget_per_turn_limit}, q 取消): "
        )
    except (EOFError, KeyboardInterrupt):
        return [ChatEvent(type="slash_budget", content="已取消.")]

    new_turn_str = new_turn_str.strip()
    if new_turn_str.lower() in ("q", "quit"):
        return [ChatEvent(type="slash_budget", content="已取消.")]

    # Parse per-turn
    new_turn = cs.budget_per_turn_limit  # default 保持
    if new_turn_str:
        try:
            new_turn = int(new_turn_str)
            if new_turn < 1:
                return [ChatEvent(
                    type="slash_error",
                    content=f"per-turn limit 需 >= 1; 已取消.",
                )]
        except ValueError:
            return [ChatEvent(
                type="slash_error",
                content=f"输入非数字 {new_turn_str!r}; 已取消.",
            )]

    # 同 per-session
    try:
        new_session_str = await chat.input_provider(
            f"新 per-session limit (回车保持 {cs.budget_per_session_limit}): "
        )
    except (EOFError, KeyboardInterrupt):
        return [ChatEvent(type="slash_budget", content="已取消.")]

    new_session_str = new_session_str.strip()
    new_session = cs.budget_per_session_limit
    if new_session_str:
        try:
            new_session = int(new_session_str)
            if new_session < 1:
                return [...error...]
        except ValueError:
            return [...error...]

    # Apply
    old_turn, old_session = cs.budget_per_turn_limit, cs.budget_per_session_limit
    cs.budget_per_turn_limit = new_turn
    cs.budget_per_session_limit = new_session
    # remaining cap
    cs.budget_per_turn_remaining = min(cs.budget_per_turn_remaining, new_turn)
    cs.budget_per_session_remaining = min(cs.budget_per_session_remaining, new_session)

    return [ChatEvent(
        type="slash_budget",
        content=(
            f"[已更新]\n"
            f"  per-turn: {old_turn} → {new_turn}\n"
            f"  per-session: {old_session} → {new_session}"
        ),
    )]
```

**Step 2.5.2: 删 cli flag**

`cli.py new()` 删 `tool_budget_per_turn` / `tool_budget_per_session` flag (Wave 4 加的). `chat()` 同删. `_run_new` / `_run_chat_repl_async` 内部相应参数也删 (用 ChatStateDict 默认).

注: 用户**仍能**通过 cli mode 跑 `explain new --no-chat` 但不能调 budget — 那种场景 batch CI 用 default 10/50 即可 (不要 over-engineer).

**Step 2.5.3 ~ 2.5.6**: test (display only / change / cancel / invalid) + ruff + commit.

```bash
git commit -m "$(cat <<'EOF'
chat/slash · Phase 11 Wave 2.5 — /budget config 流 + 删 cli flag

/budget 重写: display current → sequential prompt 改 per_turn /
per_session limit → cap remaining + report. 用 chat.input_provider
集成 prompt_toolkit (无 provider 时仅 display).

empty input 保持原值, q/quit 取消, 非数字 / < 1 报错 + 取消.

cli `new` + `chat` 命令删 --tool-budget-per-turn / per-session flag —
default 10/50 (ChatStateDict), 改通过 /budget slash.

cli batch 场景 (--no-chat) 仍 work, 默认 budget. 想调 budget 需进 REPL.

~5 test (display / change / cancel / invalid / remaining cap).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 3 — 6 single-session slash + /cf alias

### Task 3: 加 /compress /run /check /predict /counterfactual /rescore + /cf

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py` (加 6 + 1 alias handler)
- Modify: `src/explain_engine/chat/repl_entry.py` (单 session slash 在 ephemeral 时 reject)
- Test: `tests/test_chat_slash_commands.py` (扩 ~12 test)

**Step 3.1: 各 slash 实装大纲**

每个 slash handler 复用 engines layer logic (不复用 cli command body 因为 cli body 含 typer 装饰 + console + storage init). 推荐 直接 import engines + 调.

`_handle_compress` (走 HITL):

```python
async def _handle_compress(chat, args):
    if chat.is_ephemeral if hasattr(chat, "is_ephemeral") else False:
        return [ChatEvent(type="slash_error",
            content="尚未持久化, 输自然语言新建 session 或 /resume 选历史 session.")]

    # 跑 propose_candidates + review_insights_async + save + flush_to_lexicon
    from explain_engine.engines.compression import propose_candidates
    from explain_engine.engines.evaluation import score_all
    from explain_engine.hitl.cli_interactive import review_insights_async
    from explain_engine.engines.lexicon import flush_to_lexicon

    state = chat.state
    try:
        await propose_candidates(state, chat.llm, min_count=3, max_count=5)
    except SchemaValidationError as exc:
        return [ChatEvent(type="slash_error", content=f"compress 失败: {exc}")]

    # HITL
    final_candidates = await review_insights_async(state, chat.input_provider)
    state.insight_candidates = final_candidates

    # save + flush lexicon
    chat.persist()
    n = await flush_to_lexicon(chat._session, chat.storage, llm=chat.llm)

    return [ChatEvent(
        type="slash_compress",
        content=f"compress 完成. {len(final_candidates)} insight 候选. {n} var 写入 lexicon.",
    )]
```

`_handle_predict`:

```python
async def _handle_predict(chat, args):
    if chat.is_ephemeral if hasattr(chat, "is_ephemeral") else False:
        return [reject_error]

    if chat.input_provider is None:
        return [ChatEvent(type="slash_error", content="/predict 需 input_provider")]

    intervention = (await chat.input_provider("intervention 描述 (e.g. '如果 X 增加'): ")).strip()
    if not intervention or intervention.lower() in ("q", "quit"):
        return [ChatEvent(type="slash_predict", content="已取消.")]

    from explain_engine.engines.prediction import predict
    try:
        report = await predict(chat.state, intervention, chat.llm)
    except Exception as exc:
        return [ChatEvent(type="slash_error", content=f"predict 失败: {exc}")]

    return [ChatEvent(
        type="slash_predict",
        content=f"prediction: new={report.new_node_ids}, "
                f"predicted_L0={report.predicted_L0_ids}",
    )]
```

`_handle_counterfactual`: 类似 (走 `counterfactual.substitute`)
`_handle_run`: 走 `runtime.run_reasoning_loop` (复用 explain run 老逻辑)
`_handle_check`: 走 `simulation.aggregate_acceptance` (read-only)
`_handle_rescore`: 走 `engines.rescore.rescore_all`

/cf alias: 在 DEFAULT_COMMANDS 加 SlashCommand("cf", desc, _handle_counterfactual).

**Step 3.2: 集成测**

每个 slash 写 happy + ephemeral_reject + (有 input prompt 的) cancel test.

```python
class TestSlashCompress:
    @pytest.mark.asyncio
    async def test_ephemeral_rejects(self):
        eph = EphemeralChatSession(storage=StorageV2())
        events = await dispatch_slash(eph, "/compress")
        types = [e.type for e in events]
        assert "slash_error" in types

    @pytest.mark.asyncio
    async def test_persistent_runs(self, monkeypatch):
        # mock propose_candidates + review_insights_async + flush
        ...
```

**Step 3.3 ~ 3.7**: 实装 + 测 + ruff + commit (可分成 3 个 commit: HITL-heavy compress/run, read-only check/rescore, intervention-input predict/counterfactual+cf).

或者**全合 1 commit**: ~6 handler 一起改. plan implementer 决定.

```bash
git commit -m "$(cat <<'EOF'
chat/slash · Phase 11 Wave 3 — 6 single-session slash + /cf alias

新加 6 slash + 1 alias 调 engines layer:
- /compress (含 HITL review_insights_async + flush_to_lexicon)
- /run (reasoning loop, 无 HITL)
- /check (multi-signal acceptance read-only)
- /predict (interactive prompt 收 intervention)
- /counterfactual (同; /cf 短名 alias)
- /rescore (重评 edge confidence)

ephemeral session 时拒 (single-session 需 real ChatSession) + 提示
"输自然语言或 /resume 选历史 session".

~12 test (各 slash happy + ephemeral reject + cancel).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 4 — 3 cross-session slash

### Task 4: 加 /list /lexicon /migrate

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py`
- Test: `tests/test_chat_slash_commands.py`

**Step 4.1: 实装**

```python
async def _handle_list(chat, args):
    """复用 cli list_cmd 逻辑."""
    from explain_engine.persistence.session import SessionStore
    metas = SessionStore().list()
    if not metas:
        return [ChatEvent(type="slash_list", content="无 session.")]

    # Rich Table render to string (RichConsole 默认 print, 这里 return content)
    from io import StringIO
    from rich.console import Console as _Console
    from rich.table import Table
    buf = StringIO()
    rendered_console = _Console(file=buf, force_terminal=False)
    table = Table(title=f"Sessions ({len(metas)})")
    table.add_column("ID", style="cyan")
    table.add_column("Question", style="bold")
    table.add_column("Stage")
    table.add_column("Created")
    for m in metas:
        from datetime import datetime
        ts = datetime.fromtimestamp(m.created_at).strftime("%Y-%m-%d %H:%M")
        table.add_row(m.session_id, m.question, m.stage, ts)
    rendered_console.print(table)
    return [ChatEvent(type="slash_list", content=buf.getvalue())]


async def _handle_lexicon(chat, args):
    """复用 cli lexicon 逻辑."""
    # 类似 _handle_list, render Rich Table 进 buf
    ...


async def _handle_migrate(chat, args):
    """一次性迁老 sessions/*.json → ~/.explain/projects/<proj>/sessions/<sid>/."""
    from explain_engine.persistence.migration import migrate_legacy_sessions
    # 调 dry-run preview 或直接 migrate?
    # 加 input_provider confirm prompt
    ...
```

**Step 4.2: 测 + ruff + commit**

~3 happy + ~3 ephemeral OK (cross-session 不依赖 real session).

```bash
git commit -m "..."
```

---

## Wave 5 — Acceptance smoke + README

### Task 5: 手测 doc + README Phase 11 段

**Files:**
- Create: `docs/plans/2026-05-18-phase11-repl-unification-acceptance.md`
- Modify: `README.md`

**Step 5.1: Write acceptance doc**

8 步 smoke (design §12.2 列):
- S1 `explain` 进 REPL ephemeral
- S2 ephemeral slash 分类 (cross-session work / single-session reject)
- S3 输 "为什么..." → bootstrap + HITL k/e/d → 切真 chat
- S4 chat 内 `/compress` → HITL + flush
- S5 `/predict` → interactive prompt
- S6 `/budget` → 改 limit
- S7 `/quit` → 再跑 `explain show <sid>` cli (backward compat)
- S8 Wave 0 bug fix — 连跑 5 次 compress 看 400 频率

**Step 5.2: README 更新**

- 顶 Phase 11 milestone (`~800 tests pass`)
- 加 Phase 11 段 (REPL 入口 + slash 列表 + 7 wave)

**Step 5.3: Commit**

---

## Acceptance checklist (整体)

落地完成后:
- [ ] 全测 PASS (`pytest -x`): ~800 total (728 baseline + ~70 new)
- [ ] ruff 0
- [ ] `git log --oneline dev ^master` 显 ~10 commit (Wave 0-5 + 可能 review fix)
- [ ] `explain` 默认进 REPL ephemeral, `explain show <sid>` 仍 typer
- [ ] 18 slash (8 旧 + 10 新) /help 全列
- [ ] `/budget` interactive 改 limit + cli flag 删
- [ ] (Manual) acceptance smoke 8 步全过

---

## Risk 回顾

- **Wave 0 retry 重 LLM call cost**: 3 次调用 × ~500 token = ~1.5k token / compress. 用户报告频率高时仍可控.
- **prompt_toolkit 长 HITL session 稳定性**: review_phenomena 10+ phenomena × k/e/d 串联. 未实测.
- **ephemeral 首句边界**: `?` `1` 等都当 question. bootstrap 失败 fallback 留 ephemeral.
- **slash 在 ephemeral / 真 chat 双场景测**: 每个 slash handler 测两个 path.
- **typer.callback() 跟现有 cli command 行为**: callback fires before subcommand. 验 `explain list` 时 callback 也 fire 但 invoked_subcommand 非 None → 不进 REPL.

---

## 参考

- Design: [2026-05-18-phase11-repl-unification-design.md](2026-05-18-phase11-repl-unification-design.md)
- Phase 10 plan: [2026-05-18-phase10-persistent-world-model-plan.md](2026-05-18-phase10-persistent-world-model-plan.md)
- 现 cli.py: [src/explain_engine/cli.py](../../src/explain_engine/cli.py)
- 现 chat slash: [src/explain_engine/chat/slash_commands.py](../../src/explain_engine/chat/slash_commands.py)
- prompt_toolkit docs: https://python-prompt-toolkit.readthedocs.io/
