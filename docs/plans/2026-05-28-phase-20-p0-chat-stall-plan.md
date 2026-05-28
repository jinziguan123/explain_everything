# Phase 20 P0: chat NL stall + ctrl+c 失灵 + 滚动失灵 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修 Phase 19 真终端 smoke 暴露的三个 P0 stuck UX bug: chat NL 输出半截 stall (LLM stream 无 timeout), ctrl+c 无效 (chat task 卡 event loop), 上下滚动失灵 (mouse=False 副作用 + Input 抢键).

**Architecture:** 3 个互相独立的 layer, 每层独立 commit. Layer A (SDK timeout) 修主因; Layer B (esc cancel) 给 user 逃生口; Layer C (PgUp/PgDn) 修副作用. 4 个 task, 每 task 完整 red→green→ruff→commit 循环.

**Tech Stack:** anthropic SDK (AsyncAnthropic) + openai SDK (AsyncOpenAI) + httpx.Timeout / textual TUI (App, Binding, VerticalScroll, asyncio.Task cancel) / pytest + textual `App.run_test()` harness.

**Design doc**: `docs/plans/2026-05-28-phase-20-p0-chat-stall-design.md` (commit dae02ce).

---

## 项目惯例 (subagent 必读)

- 严格 TDD red → green → commit (绝不先写 impl 后补 test)
- venv: `.venv/bin/python` (uv-managed)
- 单 test: `.venv/bin/python -m pytest tests/test_X.py::test_Y -xvs`
- 全量回归: `.venv/bin/python -m pytest tests/ -q`
- ruff: `.venv/bin/ruff check src/ tests/ --fix` (自动 import order / UP017)
- 中文 commit msg + 末尾 trailer:
  ```
  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  ```
- commit format: `<scope> · Phase 20.0 Task N: <action>`
- 绝不 push / 绝不 amend / 绝不 `--no-verify`
- preexisting test fail (跳过, 非本 task 引入):
  - `test_config_light_llm.py::test_make_light_llm_client_fallback_all_empty` (env 污染)
  - `test_lexicon_migrations.py::TestMigrateJsonToPg::test_migrate_json_to_pg_basic` (PG 状态)

---

## Task 1: Layer A — SDK timeout (LLM_READ_TIMEOUT_S env knob)

**目标**: AsyncAnthropic / AsyncOpenAI 加 `httpx.Timeout(connect=10, read=120, write=60, pool=None)` 防 streaming chunk gap 永等. env knob `LLM_READ_TIMEOUT_S` default 120.0.

**Files:**
- Create: `tests/test_llm_timeout_config.py`
- Modify: `src/explain_engine/llm/anthropic_protocol.py` (line 102-115 `__init__`)
- Modify: `src/explain_engine/llm/openai_protocol.py` (line ~50 `__init__`, AsyncOpenAI 处)
- Modify: `src/explain_engine/config.py` (line 53 `make_llm_client` + line 136 `make_light_llm_client`)

### Step 1.1: Write failing tests

Create `tests/test_llm_timeout_config.py`:

```python
"""Phase 20.0 Task 1 Layer A: LLM_READ_TIMEOUT_S env knob + AsyncAnthropic/OpenAI timeout kwarg.

防 streaming chunk gap 永等 — DeepSeek API silent drop 后无 timeout 兜底.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from explain_engine.config import make_light_llm_client, make_llm_client
from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient
from explain_engine.llm.openai_protocol import OpenAIProtocolClient


def test_anthropic_protocol_client_default_read_timeout_120():
    """AnthropicProtocolClient.__init__ 默 read_timeout=120, AsyncAnthropic 收到 httpx.Timeout."""
    with patch("explain_engine.llm.anthropic_protocol.AsyncAnthropic") as mock_anthropic:
        AnthropicProtocolClient(
            api_key="sk-test",
            default_model="claude-test",
            base_url=None,
        )
        # AsyncAnthropic 收到 kwarg 含 timeout
        call_kwargs = mock_anthropic.call_args.kwargs
        assert "timeout" in call_kwargs
        timeout = call_kwargs["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        # httpx.Timeout 内 read=120
        assert timeout.read == 120.0


def test_anthropic_protocol_client_custom_read_timeout():
    """AnthropicProtocolClient(read_timeout=30) → AsyncAnthropic 收 timeout.read=30."""
    with patch("explain_engine.llm.anthropic_protocol.AsyncAnthropic") as mock_anthropic:
        AnthropicProtocolClient(
            api_key="sk-test",
            default_model="claude-test",
            base_url=None,
            read_timeout=30.0,
        )
        call_kwargs = mock_anthropic.call_args.kwargs
        timeout = call_kwargs["timeout"]
        assert timeout.read == 30.0


def test_openai_protocol_client_default_read_timeout_120():
    """OpenAIProtocolClient.__init__ 默 read_timeout=120, AsyncOpenAI 收 httpx.Timeout."""
    with patch("explain_engine.llm.openai_protocol.AsyncOpenAI") as mock_openai:
        OpenAIProtocolClient(
            api_key="sk-test",
            default_model="gpt-test",
            base_url=None,
        )
        call_kwargs = mock_openai.call_args.kwargs
        assert "timeout" in call_kwargs
        timeout = call_kwargs["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read == 120.0


def test_openai_protocol_client_custom_read_timeout():
    """OpenAIProtocolClient(read_timeout=60) → AsyncOpenAI 收 timeout.read=60."""
    with patch("explain_engine.llm.openai_protocol.AsyncOpenAI") as mock_openai:
        OpenAIProtocolClient(
            api_key="sk-test",
            default_model="gpt-test",
            base_url=None,
            read_timeout=60.0,
        )
        call_kwargs = mock_openai.call_args.kwargs
        timeout = call_kwargs["timeout"]
        assert timeout.read == 60.0


def test_make_llm_client_reads_env_default_120(monkeypatch):
    """LLM_READ_TIMEOUT_S 未设 → make_llm_client 传 read_timeout=120 给 protocol client."""
    # 清干净环境只留 LLM_PROVIDER + LLM_API_KEY 让 make_llm_client 能走
    for k in list(os.environ.keys()):
        if k.startswith("LLM_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "claude-test")

    with patch("explain_engine.config.AnthropicProtocolClient") as mock_apc:
        make_llm_client()
        call_kwargs = mock_apc.call_args.kwargs
        assert call_kwargs.get("read_timeout") == 120.0


def test_make_llm_client_env_override(monkeypatch):
    """LLM_READ_TIMEOUT_S=30 → make_llm_client 传 read_timeout=30 给 protocol client."""
    for k in list(os.environ.keys()):
        if k.startswith("LLM_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "claude-test")
    monkeypatch.setenv("LLM_READ_TIMEOUT_S", "30")

    with patch("explain_engine.config.AnthropicProtocolClient") as mock_apc:
        make_llm_client()
        call_kwargs = mock_apc.call_args.kwargs
        assert call_kwargs.get("read_timeout") == 30.0


def test_make_light_llm_client_reads_env_default_120(monkeypatch):
    """make_light_llm_client 同款读 LLM_READ_TIMEOUT_S env, default 120."""
    for k in list(os.environ.keys()):
        if k.startswith("LLM_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_LIGHT_PROVIDER", "openai")
    monkeypatch.setenv("LLM_LIGHT_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_LIGHT_MODEL", "gpt-test")

    with patch("explain_engine.config.OpenAIProtocolClient") as mock_opc:
        client = make_light_llm_client()
        # make_light_llm_client 可能返 None (fallback), 但若返 client 应传 timeout
        if client is not None:
            call_kwargs = mock_opc.call_args.kwargs
            assert call_kwargs.get("read_timeout") == 120.0
```

### Step 1.2: Run tests, verify they fail

```bash
.venv/bin/python -m pytest tests/test_llm_timeout_config.py -xvs
```

Expected: 全部 FAIL (`TypeError: AnthropicProtocolClient.__init__() got an unexpected keyword argument 'read_timeout'` 或 `assert 'timeout' in call_kwargs` AssertionError).

### Step 1.3: Modify `src/explain_engine/llm/anthropic_protocol.py`

`__init__` (line 102-115) 改:

```python
def __init__(
    self,
    api_key: str,
    default_model: str,
    base_url: str | None = None,
    max_tokens: int | None = None,
    enable_thinking: bool = True,
    read_timeout: float = 120.0,  # Phase 20.0 Layer A
) -> None:
    import httpx  # Phase 20.0 Layer A: streaming chunk gap timeout
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        # Phase 20.0 Layer A: streaming chunk gap timeout — DeepSeek API silent
        # drop 后 read 永等的根因. connect=10s / read=read_timeout (chunk gap) /
        # write=60s / pool=None (无 pool 限制).
        "timeout": httpx.Timeout(
            connect=10.0, read=read_timeout, write=60.0, pool=None
        ),
    }
    if base_url:
        kwargs["base_url"] = base_url
    self._client = AsyncAnthropic(**kwargs)
    self._default_model = default_model
    self._max_tokens = max_tokens if max_tokens is not None else self.DEFAULT_MAX_TOKENS
    # Phase 19 Task 3: enable extended thinking by default.
    self._enable_thinking = enable_thinking
```

### Step 1.4: Modify `src/explain_engine/llm/openai_protocol.py`

找 `OpenAIProtocolClient.__init__` (line ~30-55, AsyncOpenAI 在 line 53). 加 `read_timeout` param + httpx.Timeout kwarg:

```python
def __init__(
    self,
    api_key: str,
    default_model: str,
    base_url: str | None = None,
    mode: Mode = ...,  # 保留原有 mode default
    read_timeout: float = 120.0,  # Phase 20.0 Layer A
) -> None:
    import httpx  # Phase 20.0 Layer A
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": httpx.Timeout(
            connect=10.0, read=read_timeout, write=60.0, pool=None
        ),
    }
    if base_url:
        kwargs["base_url"] = base_url
    self._client = AsyncOpenAI(**kwargs)
    self._default_model = default_model
    self._mode = mode
```

⚠️ Subagent 必先 Read openai_protocol.py 完整 `__init__` 确认现有 param 顺序 + Mode default, 别破坏老 caller.

### Step 1.5: Modify `src/explain_engine/config.py` make_llm_client (line 53-) + make_light_llm_client (line 136-)

每个 `AnthropicProtocolClient(...)` / `OpenAIProtocolClient(...)` 调用前加:

```python
import os
read_timeout = float(os.environ.get("LLM_READ_TIMEOUT_S", "120.0"))
```

然后 client 构造 kwarg 加 `read_timeout=read_timeout`. 例如 line 106:

```python
return AnthropicProtocolClient(
    api_key=api_key,
    default_model=model,
    base_url=base_url,
    max_tokens=max_tokens,
    enable_thinking=enable_thinking,
    read_timeout=read_timeout,  # Phase 20.0 Layer A
)
```

同样改 line 124 (OpenAI) / line 189 (Anthropic light) / line 204 (OpenAI light) 4 处.

⚠️ Subagent 必 Read config.py 完整 make_llm_client / make_light_llm_client 看现有 kwarg, 别漏哪个.

### Step 1.6: Run tests, verify they pass

```bash
.venv/bin/python -m pytest tests/test_llm_timeout_config.py -xvs
```

Expected: 全部 PASS (7 test).

### Step 1.7: Run ruff + 全量回归

```bash
.venv/bin/ruff check src/ tests/ --fix
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_config_light_llm.py --ignore=tests/test_lexicon_migrations.py
```

Expected: 全部 PASS (跳过 2 preexisting fail).

如有新 test 失败, **不要继续到 Step 1.8**, 排查根因 (可能 kwarg signature 改动破坏老 caller).

### Step 1.8: Commit

```bash
git add tests/test_llm_timeout_config.py src/explain_engine/llm/anthropic_protocol.py src/explain_engine/llm/openai_protocol.py src/explain_engine/config.py
git commit -m "$(cat <<'EOF'
llm + config · Phase 20.0 Task 1 Layer A: LLM_READ_TIMEOUT_S env knob (default 120s)

# 根因 (设计 §根因 1)
AsyncAnthropic / AsyncOpenAI kwargs 不传 timeout → streaming chunk gap 永等;
DeepSeek API silent drop 后 client side 永远 await get_final_message.

# 改动
- AnthropicProtocolClient / OpenAIProtocolClient __init__ 加 read_timeout=120
  默认 param, AsyncAnthropic / AsyncOpenAI kwargs 加 httpx.Timeout(connect=10,
  read=read_timeout, write=60, pool=None).
- config.make_llm_client / make_light_llm_client 读 env LLM_READ_TIMEOUT_S
  (default 120.0), 传给 protocol client.

# 行为
chunk gap 无数据 120s → APITimeoutError 走现有 retry path (anthropic_protocol
.py:168 / openai_protocol.py:124). user 看到 retry 或 LLMError 抛, 不再 hang.

# 测试 (TDD red → green, 7 test)
tests/test_llm_timeout_config.py:
- test_anthropic_protocol_client_default_read_timeout_120
- test_anthropic_protocol_client_custom_read_timeout
- test_openai_protocol_client_default_read_timeout_120
- test_openai_protocol_client_custom_read_timeout
- test_make_llm_client_reads_env_default_120
- test_make_llm_client_env_override
- test_make_light_llm_client_reads_env_default_120

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Layer B — escape cancel chat task

**目标**: ExplainChatApp 加 `self._chat_task` ref, BINDINGS `escape → action_cancel_chat`. esc 按下时 cancel current chat handler task + 兜底清 spinner + mount "请求已取消" 行. ctrl+c 行为不变.

**Files:**
- Create: `tests/test_tui_app_cancel_chat.py`
- Modify: `src/explain_engine/chat/tui_app.py`:
  - line 184-188 BINDINGS 加 escape binding
  - line 191-216 `__init__` 加 `self._chat_task = None`
  - line 426-474 `_handle_input_submitted` 包成 task
  - 加 `action_cancel_chat` method (放在 line 822 `action_toggle_thinking` 附近)

### Step 2.1: Write failing tests

Create `tests/test_tui_app_cancel_chat.py`:

```python
"""Phase 20.0 Task 2 Layer B: esc cancel in-flight chat task.

textual App.run_test() harness, mock chat.handle_user_input async gen 永等
模拟 LLM stream stall, press_keys('escape') 验 task cancelled + spinner 清.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.chat.session import ChatEvent
from explain_engine.chat.tui_app import ExplainChatApp


def _make_app_with_mock_chat(handle_user_input_gen) -> ExplainChatApp:
    """Helper: 造 ExplainChatApp 用 mock chat (避免真 storage/llm)."""
    mock_chat = MagicMock(spec=EphemeralChatSession)
    mock_chat.handle_user_input = handle_user_input_gen
    mock_chat.is_slash_command = lambda t: t.startswith("/")
    mock_chat.tui_app = None  # 让 __init__ set self
    return ExplainChatApp(
        llm=MagicMock(),
        light_llm=None,
        ephemeral_chat=mock_chat,
        show_splash=False,  # 跳过 splash 加速 test
    )


@pytest.mark.asyncio
async def test_escape_cancels_inflight_chat_task():
    """提交 input → mock chat handler 永等 → press escape → task cancelled."""
    # mock handle_user_input 返一个永远不完成的 async gen
    async def never_ending_gen(text, llm):
        yield ChatEvent(type="status_start", content="思考中...")
        await asyncio.sleep(99)  # 永等模拟 LLM hang
        yield ChatEvent(type="assistant_text", content="never")

    app = _make_app_with_mock_chat(never_ending_gen)

    async with app.run_test() as pilot:
        # 提交 user input
        await pilot.press(*"hello")
        await pilot.press("enter")
        # 等 _chat_task 启动 (textual 给 message handler spawn task 后短延迟)
        await pilot.pause(0.1)
        assert app._chat_task is not None
        assert not app._chat_task.done()

        # press escape → action_cancel_chat
        await pilot.press("escape")
        await pilot.pause(0.1)

        # task 应已 cancelled
        assert app._chat_task is None or app._chat_task.done()


@pytest.mark.asyncio
async def test_escape_no_inflight_task_shows_message():
    """无 in-flight task → escape 仅 mount '(无 in-flight 请求可取消)' 行."""
    async def normal_gen(text, llm):
        yield ChatEvent(type="assistant_text", content="ok")

    app = _make_app_with_mock_chat(normal_gen)

    async with app.run_test() as pilot:
        # 不提交 input 直接 escape
        await pilot.press("escape")
        await pilot.pause(0.05)

        # _chat_task 应 None (从未启动)
        assert app._chat_task is None
        # 屏 mount 一行含 "无 in-flight"
        from textual.widgets import Static
        statics = list(app.query(Static))
        assert any("无 in-flight" in str(s.renderable) for s in statics), (
            f"Expected '无 in-flight' message, got: {[str(s.renderable) for s in statics]}"
        )


@pytest.mark.asyncio
async def test_chat_task_normal_completion_clears_ref():
    """chat handler 正常完成 → _chat_task 回 None."""
    async def quick_gen(text, llm):
        yield ChatEvent(type="status_start", content="...")
        yield ChatEvent(type="assistant_text", content="hello")
        yield ChatEvent(type="status_end", content=None)

    app = _make_app_with_mock_chat(quick_gen)

    async with app.run_test() as pilot:
        await pilot.press(*"hi")
        await pilot.press("enter")
        # 等 gen drain
        await pilot.pause(0.2)
        # 完成后 ref 清
        assert app._chat_task is None
```

### Step 2.2: Run tests, verify fail

```bash
.venv/bin/python -m pytest tests/test_tui_app_cancel_chat.py -xvs
```

Expected: 全 FAIL. `AttributeError: 'ExplainChatApp' object has no attribute '_chat_task'` (test 1/3) 或 escape 无 binding 不触发 cancel.

### Step 2.3: Modify `src/explain_engine/chat/tui_app.py`

**3.a — BINDINGS** (line 184-188) 加 escape:

```python
BINDINGS: ClassVar[list[BindingType]] = [
    Binding("ctrl+o", "toggle_thinking", "折叠 thinking"),
    Binding("ctrl+c", "quit_app", "退出"),
    Binding("ctrl+l", "clear_log", "清屏"),
    # Phase 20.0 Layer B: escape cancel in-flight chat task (LLM stream stall 逃生口)
    Binding("escape", "cancel_chat", "取消"),
]
```

**3.b — `__init__`** (line 191-216) 末尾加 `self._chat_task = None`:

```python
# Phase 20.0 Layer B: in-flight chat handler async task ref. escape binding
# 触发 action_cancel_chat 时 cancel 它. _handle_input_submitted 包成 task 启,
# finally 清 ref. None = 无 in-flight 请求.
self._chat_task: asyncio.Task | None = None
```

**3.c — `_handle_input_submitted`** (line 426-474) 重构: 非 slash 分支 (line 464-474) 包成 task. 完整 method 改:

```python
@on(Input.Submitted, "#prompt")
async def _handle_input_submitted(self, event: Input.Submitted) -> None:
    """User 按 Enter — 派发到 slash dispatch 或 chat.handle_user_input.

    Phase 20.0 Layer B: 非 slash 路径包成 self._chat_task 让 escape 可 cancel.
    """
    text = (event.value or "").strip()
    event.input.value = ""
    if not text:
        return

    await self._write_styled("[bold cyan]>[/bold cyan] ", text)

    if text.startswith("/"):
        from explain_engine.chat.slash_commands import dispatch_slash
        try:
            events = await dispatch_slash(self.chat, text)
        except Exception as exc:
            await self._write_styled(
                "[red]slash 失败: [/red]",
                f"{type(exc).__name__}: {exc}",
                suffix_style="red",
            )
            return
        for ev in events:
            await self._render_event(ev)
        return

    # 非 slash — 自然语言 → 包 task 让 escape 可 cancel
    self._chat_task = asyncio.create_task(self._consume_chat_events(text))
    try:
        await self._chat_task
    finally:
        self._chat_task = None


async def _consume_chat_events(self, text: str) -> None:
    """Phase 20.0 Layer B: chat handler 包 fn — async-for 消费 events,
    catch CancelledError 兜底清 spinner + mount '请求已取消'.
    """
    try:
        async for ev in self.chat.handle_user_input(text, self.llm):
            await self._render_event(ev)
    except asyncio.CancelledError:
        # esc 触发 — 兜底清 spinner (status_end event 可能没 yield 到)
        await self._unmount_status()
        await self._write_styled(
            "", "请求已取消.", suffix_style="dim"
        )
        raise  # 让 task 真 cancelled state, finally 清 ref
    except Exception as exc:
        await self._write_styled(
            "[red]chat 失败: [/red]",
            f"{type(exc).__name__}: {exc}",
            suffix_style="red",
        )
```

**3.d — `action_cancel_chat`** (放 `action_toggle_thinking` 上方 line 822 附近):

```python
async def action_cancel_chat(self) -> None:
    """Phase 20.0 Layer B Task 2: escape binding → cancel in-flight chat task.

    无 task / task 已 done → mount '(无 in-flight 请求可取消)' 行.
    有 task → cancel; CancelledError 在 _consume_chat_events 内 catch 已 mount
    '请求已取消'.
    """
    if self._chat_task is None or self._chat_task.done():
        await self._write_styled(
            "", "(无 in-flight 请求可取消)", suffix_style="dim"
        )
        return
    self._chat_task.cancel()
```

### Step 2.4: Run tests, verify pass

```bash
.venv/bin/python -m pytest tests/test_tui_app_cancel_chat.py -xvs
```

Expected: 3 PASS.

如 textual `app.run_test()` API 跟 test 假设不一致 (e.g. press("escape") 不触发 app binding), Subagent 看 `tests/test_tui_app.py` 或 textual docs 确认 pilot.press("escape") 正确 usage.

### Step 2.5: Run ruff + 全量回归

```bash
.venv/bin/ruff check src/ tests/ --fix
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_config_light_llm.py --ignore=tests/test_lexicon_migrations.py
```

Expected: 全 PASS (含 Task 1 新增 7 test).

⚠️ 现 `test_tui_app.py` 可能有 test 测 `_handle_input_submitted` 内部行为 (e.g. mock `chat.handle_user_input` 立刻完成). 这些 test 应仍 PASS — `_chat_task` 完成后 finally 清 ref, 行为对外不变. 如有 fail, 检查是不是 test 直接 inspect `_handle_input_submitted` 的 await chain.

### Step 2.6: Commit

```bash
git add tests/test_tui_app_cancel_chat.py src/explain_engine/chat/tui_app.py
git commit -m "$(cat <<'EOF'
chat/tui_app · Phase 20.0 Task 2 Layer B: escape binding 取消 in-flight chat task

# 根因 (设计 §根因 2)
textual ctrl+c → self.exit() 等所有 task drain. chat handler async task 卡
await stream (Layer A 没修 / 跑 stale binary 时) → app 跟卡 → user "ctrl+c 无效".

# 改动
- ExplainChatApp.__init__ 加 self._chat_task: asyncio.Task | None = None
- _handle_input_submitted 非 slash 路径包成 self._chat_task =
  asyncio.create_task(_consume_chat_events(text)), finally 清 ref
- 新 _consume_chat_events helper: catch CancelledError 兜底 _unmount_status +
  mount '请求已取消' + re-raise (让 task 真 cancelled)
- BINDINGS 加 Binding("escape", "cancel_chat", "取消")
- 新 action_cancel_chat: 无 task → mount '(无 in-flight 请求可取消)'; 有 →
  self._chat_task.cancel()

# 设计
escape vs ctrl+c 语义分离: escape = cancel current op (跟 SessionPickerScreen
一致), ctrl+c = quit app (现 action_quit_app 行为不变).

# 测试 (TDD red → green, 3 test, textual App.run_test harness)
tests/test_tui_app_cancel_chat.py:
- test_escape_cancels_inflight_chat_task
- test_escape_no_inflight_task_shows_message
- test_chat_task_normal_completion_clears_ref

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Layer C — PgUp/PgDn keyboard scroll

**目标**: BINDINGS PgUp/PgDn → action_scroll_output_up/down → `VerticalScroll#output.scroll_page_up/down()`. mouse=False 下 keyboard scroll 兜底.

**Files:**
- Create: `tests/test_tui_app_keyboard_scroll.py`
- Modify: `src/explain_engine/chat/tui_app.py`:
  - line 184-188 BINDINGS 加 pageup / pagedown
  - 加 `action_scroll_output_up` / `action_scroll_output_down` (放 `action_clear_log` 附近)

### Step 3.1: Write failing tests

Create `tests/test_tui_app_keyboard_scroll.py`:

```python
"""Phase 20.0 Task 3 Layer C: PgUp/PgDn keyboard scroll for VerticalScroll#output.

mouse=False (Phase 19 Bug A 副作用) 下 wheel 失效, keyboard 兜底.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.chat.tui_app import ExplainChatApp


def _make_app() -> ExplainChatApp:
    mock_chat = MagicMock(spec=EphemeralChatSession)
    mock_chat.is_slash_command = lambda t: t.startswith("/")
    return ExplainChatApp(
        llm=MagicMock(),
        light_llm=None,
        ephemeral_chat=mock_chat,
        show_splash=False,
    )


@pytest.mark.asyncio
async def test_pageup_scrolls_output_up():
    """PgUp 按下 → VerticalScroll#output.scroll_page_up 被调."""
    app = _make_app()
    async with app.run_test() as pilot:
        from textual.containers import VerticalScroll
        from textual.widgets import Static

        # mount 30 个 Static widget 让 VerticalScroll 真有 scroll 空间
        container = app.query_one("#output", VerticalScroll)
        for i in range(30):
            await container.mount(Static(f"line {i}"))
        await pilot.pause(0.05)
        # 滚到底先
        container.scroll_end(animate=False)
        await pilot.pause(0.05)
        y_before = container.scroll_offset.y

        # PgUp
        await pilot.press("pageup")
        await pilot.pause(0.1)

        # scroll_offset.y 应减 (上翻 = offset 变小)
        y_after = container.scroll_offset.y
        assert y_after < y_before, (
            f"Expected scroll_offset.y to decrease, before={y_before}, after={y_after}"
        )


@pytest.mark.asyncio
async def test_pagedown_scrolls_output_down():
    """PgDn 按下 → VerticalScroll#output.scroll_page_down 被调."""
    app = _make_app()
    async with app.run_test() as pilot:
        from textual.containers import VerticalScroll
        from textual.widgets import Static

        container = app.query_one("#output", VerticalScroll)
        for i in range(30):
            await container.mount(Static(f"line {i}"))
        await pilot.pause(0.05)
        container.scroll_home(animate=False)
        await pilot.pause(0.05)
        y_before = container.scroll_offset.y

        await pilot.press("pagedown")
        await pilot.pause(0.1)

        y_after = container.scroll_offset.y
        assert y_after > y_before, (
            f"Expected scroll_offset.y to increase, before={y_before}, after={y_after}"
        )
```

### Step 3.2: Run tests, verify fail

```bash
.venv/bin/python -m pytest tests/test_tui_app_keyboard_scroll.py -xvs
```

Expected: 全 FAIL — pageup/pagedown 无 binding, scroll_offset 不变.

### Step 3.3: Modify `src/explain_engine/chat/tui_app.py`

**3.a — BINDINGS** (line 184-188):

```python
BINDINGS: ClassVar[list[BindingType]] = [
    Binding("ctrl+o", "toggle_thinking", "折叠 thinking"),
    Binding("ctrl+c", "quit_app", "退出"),
    Binding("ctrl+l", "clear_log", "清屏"),
    Binding("escape", "cancel_chat", "取消"),  # Task 2 Layer B
    # Phase 20.0 Layer C: PgUp/PgDn 给 VerticalScroll#output (mouse=False 副作用
    # 下 wheel 失效 + Input focus 抢 PgUp/Dn, app-level binding 兜底).
    Binding("pageup", "scroll_output_up", "上翻"),
    Binding("pagedown", "scroll_output_down", "下翻"),
]
```

**3.b — Actions** (`action_clear_log` 附近 line 835):

```python
def action_scroll_output_up(self) -> None:
    """Phase 20.0 Layer C: PgUp → VerticalScroll#output 上翻一页."""
    self.query_one("#output", VerticalScroll).scroll_page_up()

def action_scroll_output_down(self) -> None:
    """Phase 20.0 Layer C: PgDn → VerticalScroll#output 下翻一页."""
    self.query_one("#output", VerticalScroll).scroll_page_down()
```

### Step 3.4: Run tests, verify pass

```bash
.venv/bin/python -m pytest tests/test_tui_app_keyboard_scroll.py -xvs
```

Expected: 2 PASS.

### Step 3.5: Run ruff + 全量回归

```bash
.venv/bin/ruff check src/ tests/ --fix
.venv/bin/python -m pytest tests/ -q --ignore=tests/test_config_light_llm.py --ignore=tests/test_lexicon_migrations.py
```

Expected: 全 PASS (Task 1 + 2 + 3 累计 ~12 新 test).

### Step 3.6: Commit

```bash
git add tests/test_tui_app_keyboard_scroll.py src/explain_engine/chat/tui_app.py
git commit -m "$(cat <<'EOF'
chat/tui_app · Phase 20.0 Task 3 Layer C: PgUp/PgDn keyboard scroll for #output

# 根因 (设计 §根因 3)
Phase 19 Bug A (5c3dfac) mouse=False 让 terminal selection 复原, 副作用 textual
收不到 wheel event → VerticalScroll 不能鼠标滚. Input focus 下 PgUp/Dn 被
Input 抢 (history navigation), 没 keyboard scroll 兜底路径.

# 改动
- BINDINGS 加 Binding("pageup", "scroll_output_up", "上翻") + pagedown 同款
- action_scroll_output_up: VerticalScroll#output.scroll_page_up()
- action_scroll_output_down: 同款 down

# 行为
app-level binding 优先 Input focus 之下, PgUp/Dn 给 #output 翻页. Footer
显 hint "PgUp 上翻 / PgDn 下翻".

# Out of scope (留 follow-up)
- ctrl+home/end 跳首末
- up/down 行级滚动 (Input 已用 history, 冲突)

# 测试 (TDD red → green, 2 test, textual App.run_test harness)
tests/test_tui_app_keyboard_scroll.py:
- test_pageup_scrolls_output_up
- test_pagedown_scrolls_output_down

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Acceptance doc + 真终端 smoke 备忘

**目标**: 写 acceptance doc 总结 3 layer before/after + TDD evidence + 真终端 smoke 验证步骤 (user 自己跑).

**Files:**
- Create: `docs/plans/2026-05-28-phase-20-p0-acceptance.md`

### Step 4.1: Write acceptance doc

Create `docs/plans/2026-05-28-phase-20-p0-acceptance.md`:

```markdown
# Phase 20.0 P0 — acceptance

**Design**: `docs/plans/2026-05-28-phase-20-p0-chat-stall-design.md` (dae02ce)
**Plan**: `docs/plans/2026-05-28-phase-20-p0-chat-stall-plan.md`

## 范围

Phase 19 真终端 smoke 发现的 3 个互联 P0 stuck UX bug 一起修.

## Layer A — SDK timeout (Task 1)

**Before**: AsyncAnthropic / AsyncOpenAI 不传 timeout, streaming chunk gap
permanent hang.

**After**: `httpx.Timeout(connect=10, read=120, write=60, pool=None)`. env knob
`LLM_READ_TIMEOUT_S=120` (default). chunk gap 120s → APITimeoutError 走现有
retry path.

**TDD**: 7 test in `tests/test_llm_timeout_config.py`, 全 PASS.

**Commit**: `<sha>` (Task 1)

## Layer B — escape cancel chat task (Task 2)

**Before**: textual ctrl+c → self.exit() 等 task drain → chat task 卡 await
stream → app 跟卡 → "ctrl+c 无效".

**After**: escape binding → action_cancel_chat → cancel self._chat_task. 兜底
_unmount_status + mount "请求已取消". ctrl+c 行为不变.

**TDD**: 3 test in `tests/test_tui_app_cancel_chat.py`, 全 PASS.

**Commit**: `<sha>` (Task 2)

## Layer C — PgUp/PgDn keyboard scroll (Task 3)

**Before**: mouse=False (5c3dfac) 副作用 wheel 失效 + Input 焦点抢 PgUp/Dn →
无 keyboard scroll 路径.

**After**: app-level BINDINGS PgUp/PgDn → VerticalScroll.scroll_page_up/down.

**TDD**: 2 test in `tests/test_tui_app_keyboard_scroll.py`, 全 PASS.

**Commit**: `<sha>` (Task 3)

## 真终端 smoke 验证 (user 自己跑)

启 terminal (macOS Terminal.app / iTerm2):

1. `uv run explain` → splash → ephemeral REPL.
2. `/resume <sid>` 进任意 ChatSession.
3. 输自然语言问题 (e.g. "解释一下六十四卦如何区分四季和吉凶").
4. **case 1 (LLM 正常 stream)**: 看屏幕逐步输出 markdown, 完整, 不卡.
5. **case 2 (LLM stream 卡 / 你想中途中断)**:
   - 看屏幕停了一会, 按 **Esc** → 应 mount "请求已取消." dim 文字, Input 可输下一句.
6. **滚动验证**: 输 10 个问题让 #output 累积内容超一屏. 按 **PgUp** 上翻一页, **PgDn** 下翻一页. 应工作.
7. **ctrl+c**: 仍能退出 app (走 action_quit_app 不变).

如有任何 case 失败, 在本 doc 加 "已知问题" section + 开 follow-up task.

## Follow-up (留下一 phase)

- `/compress` 后 P0 (本 user 5-28 误报, 实际是 NL chat stall) — 本 Phase 解决
- 小型 UX 套餐 #4 (theory cache label 改名) + #5 (LLM_SPLASH_PAUSE_S env knob) + #8 (picker sort)
- VerticalScroll widget cap LRU evict (user follow-up #6)
- textual non-TTY guard (user follow-up #7)
- chat_with_tools thinking streaming 实时 yield (follow-up #3)
- cli legacy `_run_chat_repl_async` 切 textual (follow-up #2)
```

⚠️ Subagent 写 acceptance 时把 `<sha>` 占位符替换为真实 commit hash (Task 1/2/3 commit 的 `git rev-parse --short HEAD` after each).

### Step 4.2: Commit acceptance doc

```bash
git add docs/plans/2026-05-28-phase-20-p0-acceptance.md
git commit -m "$(cat <<'EOF'
docs/plans · Phase 20.0 Acceptance: P0 chat stall + ctrl+c + 滚动 三层修法验收

# Layer A/B/C TDD 全 PASS (12 新 test 全绿)
- Task 1 LLM_READ_TIMEOUT_S env knob (7 test)
- Task 2 escape cancel chat task (3 test)
- Task 3 PgUp/PgDn keyboard scroll (2 test)

# 真终端 smoke 步骤 (user 跑)
1. /resume → 输 NL → 期间 esc 中断 → 应清 spinner mount '请求已取消'
2. PgUp/PgDn 上下翻 (mouse=False 下 keyboard 兜底)
3. ctrl+c 仍 quit (不变)

# Follow-up (Phase 20.1+)
- 小 UX 套餐 #4+#5+#8 (P0 修完回去)
- VerticalScroll widget cap / non-TTY guard / cli legacy 切 textual / thinking streaming

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 完成判据

- [ ] Task 1 commit done, `pytest tests/test_llm_timeout_config.py` 7 PASS
- [ ] Task 2 commit done, `pytest tests/test_tui_app_cancel_chat.py` 3 PASS
- [ ] Task 3 commit done, `pytest tests/test_tui_app_keyboard_scroll.py` 2 PASS
- [ ] Task 4 commit done, acceptance doc 含 Task 1/2/3 真 sha
- [ ] 全量 `pytest tests/ -q --ignore=tests/test_config_light_llm.py --ignore=tests/test_lexicon_migrations.py` 全绿
- [ ] `ruff check src/ tests/` 0 issue
- [ ] 4 个新 commit + 1 design commit (dae02ce) 在 dev branch
- [ ] user 真终端 smoke 验通过 (acceptance §真终端 smoke)
