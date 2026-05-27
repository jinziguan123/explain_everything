# Phase 19 Implementation Plan — textual chat REPL + reasoning_content + UX 三连击

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** chat REPL 从 Rich + prompt_toolkit 混合架构换 textual TUI 框架, 实装 Ctrl+O 折叠 LLM thinking content + spinner + splash 三连击.

**Architecture:** 7 wave 分层落地. Wave 1-2 加 LLM provider reasoning_content 提取 + ChatEvent 新 type. Wave 3-6 textual app 重写 REPL + thinking Collapsible + spinner + splash. Wave 7 收口. backward compat: cli batch subcommand 不变, Phase 17.2/18 全保留.

**Tech Stack:** textual (新依赖) + pyfiglet (新依赖) + 现有 Rich + asyncio + pytest. Python 3.13.

**Reference design:** [`docs/plans/2026-05-27-phase-19-tui-design.md`](2026-05-27-phase-19-tui-design.md)

---

## 项目惯例 (跟 Phase 17.2 / 18 严格一致)

- 严格 TDD: red → green → commit, 每 task 一个 commit
- pytest: `.venv/bin/python -m pytest tests/test_X.py -k test_Y -xvs`
- 全量: `.venv/bin/python -m pytest tests/ -q`
- ruff: `.venv/bin/ruff check src/ tests/` (--fix 自动)
- 中文 commit msg + `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
- commit format: `<scope> · Phase 19 Task N: <action>`
- 绝不 push / amend / --no-verify / 动 git config

---

## Wave 1 — LLM client reasoning (provider 层)

### Task 1: `Response` 加 `reasoning: str | None = None` field

**Files:**
- Modify: `src/explain_engine/llm/client.py:19-24`
- Test: `tests/test_llm_client_types.py` (现存)

**Step 1: Write failing test**

```python
def test_response_reasoning_field_default_none():
    from explain_engine.llm.client import Response
    r = Response(text="hi", parsed=None, model="m", usage={})
    assert r.reasoning is None

def test_response_reasoning_field_explicit():
    from explain_engine.llm.client import Response
    r = Response(text="hi", reasoning="思考过程", parsed=None, model="m", usage={})
    assert r.reasoning == "思考过程"
```

**Step 2: Run** — FAIL (`unexpected keyword argument 'reasoning'`)

**Step 3: Impl**

```python
# client.py:19-24
class Response(BaseModel):
    text: str
    reasoning: str | None = None  # Phase 19: extended thinking / reasoning_content
    parsed: dict[str, Any] | None
    model: str
    usage: dict[str, int]
```

**Step 4: Run** — PASS. 全量 `.venv/bin/python -m pytest tests/test_llm_client_types.py tests/test_llm_client_factory.py -xvs` 零回归 (默 None field).

**Step 5: Commit**

```bash
git add src/explain_engine/llm/client.py tests/test_llm_client_types.py
git commit -m "$(cat <<'EOF'
llm/client · Phase 19 Task 1: Response 加 reasoning field (default None)

backward compat: 老 Response(text=..., parsed=...) 不传 reasoning, default
None, 老 caller 完全不感知.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: anthropic chat() 路径解析 thinking blocks

**Files:**
- Modify: `src/explain_engine/llm/anthropic_protocol.py` (chat() method)
- Test: `tests/test_anthropic_protocol.py` (现存)

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_anthropic_chat_extracts_thinking_blocks(monkeypatch):
    """anthropic chat() 流式解析: 含 thinking block → Response.reasoning 填."""
    from explain_engine.llm.anthropic_protocol import AnthropicProtocolClient

    client = AnthropicProtocolClient(api_key="k", default_model="m", base_url="https://example")

    # mock stream 含 thinking block + text block
    fake_stream = ...  # 用 AsyncMock 模拟 SDK stream context manager
    # 实现需要看 chat() 内部具体 stream loop pattern, 模拟 yield thinking 段 + text 段

    monkeypatch.setattr(client._client.messages, "stream", lambda **kw: fake_stream)

    resp = await client.chat([Message(role="user", content="hi")])
    assert resp.reasoning is not None
    assert "thinking content" in resp.reasoning
    assert resp.text == "final answer"
```

**Step 2: Run** — FAIL (现 chat() 不提取 thinking)

**Step 3: Impl** — `anthropic_protocol.py` chat() method 内部 stream loop 加:

```python
async with self._client.messages.stream(**call_kwargs) as stream:
    text_parts = []
    thinking_parts = []
    async for block in stream.content_blocks:
        if block.type == "thinking":
            thinking_parts.append(getattr(block, "thinking", ""))
        elif block.type == "text":
            text_parts.append(block.text)
    final_msg = await stream.get_final_message()

text = "".join(text_parts)
reasoning = "".join(thinking_parts) if thinking_parts else None
return Response(text=text, reasoning=reasoning, parsed=parsed, model=..., usage=...)
```

(具体 stream API 看现有 chat_with_tools 是怎么处理 thinking block 的, 复用同 pattern)

**Step 4: Run** — PASS. 全 anthropic test 零回归.

**Step 5: Commit**

```bash
git add src/explain_engine/llm/anthropic_protocol.py tests/test_anthropic_protocol.py
git commit -m "$(cat <<'EOF'
llm/anthropic_protocol · Phase 19 Task 2: chat() path 暴露 thinking blocks

现 chat_with_tools 已收 thinking 到 ToolsResponse.raw_content_blocks
(Phase 9 Wave F.4), chat() path 对称改: stream loop 遇 block.type=="thinking"
累积到 reasoning_text 填 Response.reasoning.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: anthropic call_kwargs 加 thinking enable

**Files:** Modify `src/explain_engine/llm/anthropic_protocol.py` + constructor

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_anthropic_chat_passes_thinking_param_when_enabled():
    client = AnthropicProtocolClient(..., enable_thinking=True)
    captured = {}
    # mock stream, capture call_kwargs
    ...
    await client.chat([Message(role="user", content="hi")])
    assert captured["kwargs"]["thinking"] == {"type": "enabled", "budget_tokens": 4096}


@pytest.mark.asyncio
async def test_anthropic_chat_omits_thinking_param_when_disabled():
    client = AnthropicProtocolClient(..., enable_thinking=False)
    captured = {}
    ...
    await client.chat([Message(role="user", content="hi")])
    assert "thinking" not in captured["kwargs"]
```

**Step 2: Run** — FAIL (constructor 没 enable_thinking 参)

**Step 3: Impl**

`AnthropicProtocolClient.__init__` 加 `enable_thinking: bool = True` 参. chat() / chat_with_tools 内部 if self._enable_thinking: call_kwargs["thinking"] = {"type": "enabled", "budget_tokens": 4096}.

**Step 4: Run** — PASS.

**Step 5: Commit**

```bash
git add src/explain_engine/llm/anthropic_protocol.py tests/test_anthropic_protocol.py
git commit -m "$(cat <<'EOF'
llm/anthropic_protocol · Phase 19 Task 3: enable_thinking 构造参 + call_kwargs

AnthropicProtocolClient 加 enable_thinking=True default 参. True → 调 LLM
时 call_kwargs["thinking"] = {type:enabled, budget_tokens:4096}. False →
omit (省 token, 不出 thinking blocks).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: openai chat() 解析 reasoning_content

**Files:**
- Modify: `src/explain_engine/llm/openai_protocol.py` (chat() method)
- Test: `tests/test_openai_protocol.py` (现存)

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_openai_chat_extracts_reasoning_content():
    """DeepSeek-R1 / openai-compat 返 reasoning_content → Response.reasoning."""
    client = OpenAIProtocolClient(...)
    # mock chat.completions.create 返 msg with reasoning_content
    fake_msg = MagicMock(content="answer", reasoning_content="思考")
    fake_resp = MagicMock(choices=[MagicMock(message=fake_msg)], usage=MagicMock(prompt_tokens=10, completion_tokens=5))
    monkeypatch.setattr(client._client.chat.completions, "create", AsyncMock(return_value=fake_resp))

    resp = await client.chat([Message(role="user", content="hi")])
    assert resp.text == "answer"
    assert resp.reasoning == "思考"


@pytest.mark.asyncio
async def test_openai_chat_no_reasoning_content_field():
    """gpt-4o 不返 reasoning_content → Response.reasoning is None."""
    fake_msg = MagicMock(content="answer", spec=["content"])  # 不含 reasoning_content
    ...
    resp = await client.chat(...)
    assert resp.reasoning is None
```

**Step 2: Run** — FAIL

**Step 3: Impl**

`openai_protocol.py` chat() 内部:
```python
msg = resp.choices[0].message
text = msg.content
reasoning = getattr(msg, "reasoning_content", None)
return Response(text=text, reasoning=reasoning, parsed=parsed, model=..., usage=...)
```

**Step 4: Run** — PASS.

**Step 5: Commit**

```bash
git add src/explain_engine/llm/openai_protocol.py tests/test_openai_protocol.py
git commit -m "$(cat <<'EOF'
llm/openai_protocol · Phase 19 Task 4: chat() path 解析 reasoning_content

getattr(msg, "reasoning_content", None) 兼容 DeepSeek-R1 / openai-compat
路径, gpt-4o 等不返此字段 → reasoning is None.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: config LLM_THINKING_DISABLED env + factory wire

**Files:**
- Modify: `src/explain_engine/config.py` (make_llm_client / make_light_llm_client)
- Test: `tests/test_config.py` 或 `tests/test_config_light_llm.py`

**Step 1: Write failing test**

```python
def test_make_llm_client_thinking_enabled_default(monkeypatch):
    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_BASE_URL", "https://example")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.delenv("LLM_THINKING_DISABLED", raising=False)
    client = make_llm_client()
    assert client._enable_thinking is True


def test_make_llm_client_thinking_disabled_via_env(monkeypatch):
    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_BASE_URL", "https://example")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_THINKING_DISABLED", "1")
    client = make_llm_client()
    assert client._enable_thinking is False
```

**Step 2: Run** — FAIL (factory 未传 enable_thinking)

**Step 3: Impl**

`config.py` `make_llm_client` (+ `make_light_llm_client`) 内:
```python
thinking_disabled = os.environ.get("LLM_THINKING_DISABLED", "").lower() in ("1", "true", "yes")
enable_thinking = not thinking_disabled

if proto == "anthropic":
    return AnthropicProtocolClient(..., enable_thinking=enable_thinking)
if proto == "openai":
    return OpenAIProtocolClient(..., enable_thinking=enable_thinking)
```

(openai 接 enable_thinking 仅做 future-proof, 现 openai 协议 reasoning_content 是 vendor 返字段, 客户端无 disable 控. 仍接参以统一接口.)

**Step 4: Run** — PASS.

**Step 5: Commit**

```bash
git add src/explain_engine/config.py tests/test_config_light_llm.py
git commit -m "$(cat <<'EOF'
config · Phase 19 Task 5: LLM_THINKING_DISABLED env + factory wire enable_thinking

make_llm_client + make_light_llm_client 读 LLM_THINKING_DISABLED env, 默 enable.
传给 AnthropicProtocolClient / OpenAIProtocolClient. anthropic disable 时 omit
thinking call_kwargs; openai 接参 future-proof.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Wave 1 全量 pytest + ruff

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```

Expected: 全量 1310 + ~6 个新 test = 1316 PASS. preexisting 2 fail 仍 ok. ruff 0. 无 commit (验证 step).

---

## Wave 2 — ChatEvent 协议 + handle_user_input yield

### Task 7: ChatEvent docstring 加 3 个 type 说明

**Files:** Modify `src/explain_engine/chat/session.py` (ChatEvent 类 docstring line 100-127)

**Step 1:** 在 docstring `content` payload contract 段加:
```
- thinking_text: str — LLM reasoning 段内容 (extended thinking / reasoning_content).
  Producer: ephemeral.handle_user_input / ChatSession query_loop, 当 Response.reasoning 非 None 时 yield.
  Consumer: tui_app._render_event 走 Collapsible mount (dim color, 默 expand).
- status_start: str — 描述当前 LLM 长时操作 ("思考中..." / "启动深度建模 — classify 中...").
  Producer: handle_user_input 调 LLM 前. _handle_deepen 调 promote 前.
  Consumer: tui_app mount LoadingIndicator + 灰 text.
- status_end: None — 清掉前一个 status_start 显示.
  Producer: 对应 LLM call 完成. Consumer: tui_app unmount LoadingIndicator.
```

**Step 2: Test** — 加 `tests/test_chat_event_docstring.py` 验 3 个 type 字符串在 docstring 内 (确保以后不被误删).

**Step 5: Commit**

```bash
git add src/explain_engine/chat/session.py tests/test_chat_event_docstring.py
git commit -m "$(cat <<'EOF'
chat/session · Phase 19 Task 7: ChatEvent docstring 加 thinking_text + status_start/end

3 个新 event type 文档化 contract: thinking_text (LLM reasoning, dim Collapsible
render), status_start/end (spinner mount/unmount).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: ephemeral.handle_user_input yield 3 个新 event

**Files:**
- Modify: `src/explain_engine/chat/ephemeral.py` `handle_user_input` method
- Test: `tests/test_ephemeral_handle_user_input.py` (现存)

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_handle_user_input_yields_status_start_and_end(tmp_path, monkeypatch):
    """handle_user_input 头尾 yield status_start/end."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")
    ephemeral = EphemeralChatSession(storage=StorageV2())
    llm = AsyncMock()
    llm.chat.return_value = MagicMock(text="answer", reasoning=None)

    events = [ev async for ev in ephemeral.handle_user_input("hi", llm)]
    types = [e.type for e in events]
    assert types[0] == "status_start"
    assert "思考中" in events[0].content
    assert "status_end" in types
    assert types[-1] == "turn_complete"


@pytest.mark.asyncio
async def test_handle_user_input_yields_thinking_text_when_reasoning():
    """resp.reasoning 非 None → yield thinking_text event 在 assistant_text 之前."""
    ephemeral = EphemeralChatSession(storage=...)
    llm = AsyncMock()
    llm.chat.return_value = MagicMock(text="answer", reasoning="思考过程")

    events = [ev async for ev in ephemeral.handle_user_input("hi", llm)]
    thinking = [e for e in events if e.type == "thinking_text"]
    assert len(thinking) == 1
    assert thinking[0].content == "思考过程"
    # thinking_text 在 assistant_text 之前
    thinking_idx = events.index(thinking[0])
    asst_idx = next(i for i, e in enumerate(events) if e.type == "assistant_text")
    assert thinking_idx < asst_idx
```

**Step 2: Run** — FAIL

**Step 3: Impl** — `ephemeral.py:handle_user_input`:

```python
async def handle_user_input(self, text, llm):
    yield ChatEvent(type="status_start", content="思考中...")
    try:
        resp = await llm.chat(messages)
    except LLMError as exc:
        yield ChatEvent(type="status_end")
        yield ChatEvent(type="slash_error", content=...)
        return
    yield ChatEvent(type="status_end")
    if resp.reasoning:
        yield ChatEvent(type="thinking_text", content=resp.reasoning)
    # 现 transcript append + assistant_text + turn_complete 不变
    ...
```

**Step 4: Run** — PASS. 现 ephemeral 5 个 test 零回归 (status_start/end 加在头尾不破现有 event 序列断言, 但若 test assertion 是 `len(events) == N` 这种, 需调整).

**Step 5: Commit**

```bash
git add src/explain_engine/chat/ephemeral.py tests/test_ephemeral_handle_user_input.py
git commit -m "$(cat <<'EOF'
chat/ephemeral · Phase 19 Task 8: handle_user_input yield status_start/end + thinking_text

调 LLM 前 yield status_start("思考中..."), 完成 yield status_end. resp.reasoning
非 None → yield thinking_text event 在 assistant_text 之前. LLM 失败也 yield
status_end (清 spinner). 现 transcript / assistant_text / turn_complete 流程不变.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: ChatSession.handle_user_input (query_loop) 同款 yield

**Files:**
- Modify: `src/explain_engine/chat/session.py` `handle_user_input` 或 `chat/loop.py` `query_loop`
- Test: 现 chat session test

**Step 1: Write failing test** — 类似 Task 8 但 target ChatSession (persistent).

**Step 2-4: Impl** — query_loop 内 LLM call 前后 yield status. response 含 reasoning 时 yield thinking_text.

**Step 5: Commit**

```bash
git add src/explain_engine/chat/session.py src/explain_engine/chat/loop.py tests/test_chat_session.py
git commit -m "$(cat <<'EOF'
chat/session · Phase 19 Task 9: ChatSession.handle_user_input yield status + thinking_text

ChatSession query_loop 跟 ephemeral 对称, 加 status_start/end + thinking_text yield.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: `_handle_deepen` yield status_start/end

**Files:** Modify `src/explain_engine/chat/slash_commands.py` `_handle_deepen`

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_handle_deepen_yields_status_start_end():
    """_handle_deepen 调 promote 前 yield status_start, 完成 yield status_end."""
    ...
    events = await cmd.handler(ephemeral, ["question"])
    types = [e.type for e in events]
    assert "status_start" in types
    assert "status_end" in types
    assert "slash_deepen_promoted" in types
```

注: 现 `_handle_deepen` 返 `list[ChatEvent]` (跟 ephemeral.handle_user_input 是 async generator 不同). slash dispatch 内部把 generator 转 list. 改后仍返 list, 内部多塞 2 个 event.

**Step 2-4: Impl**

```python
async def _handle_deepen(chat, args) -> list[ChatEvent]:
    ... # 现 question 取位 + reject 等检查
    events = [ChatEvent(type="status_start", content="启动深度建模 — classify 中...")]
    try:
        real_chat = await chat.promote_to_persistent(question, llm)
    except Exception as exc:
        events.append(ChatEvent(type="status_end"))
        events.append(ChatEvent(type="slash_error", content=...))
        return events
    events.append(ChatEvent(type="status_end"))
    events.append(ChatEvent(type="slash_deepen_promoted",
                            content=msg_deepen_promote_start(question),
                            metadata={"sid": real_chat.sid}))
    return events
```

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/test_chat_slash_deepen.py
git commit -m "$(cat <<'EOF'
chat/slash · Phase 19 Task 10: _handle_deepen yield status_start/end

调 promote 前后 yield spinner event, 让 textual TUI 在 5-10s bootstrap pipeline
期间 visible 显示 "启动深度建模 — classify 中..." LoadingIndicator.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Wave 2 全量 pytest + ruff

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```

Expected: 全量 1316 + ~8 = ~1324 PASS. ruff 0. 若现 chat REPL test 假设 event 序列长度, 修补.

---

## Wave 3 — textual app 基础

### Task 12: 加 textual + pyfiglet deps

**Files:**
- Modify: `pyproject.toml` (dependencies 段)
- 跑: `uv sync`

**Step 1-3:** 加:
```toml
[project]
dependencies = [
    ...
    "textual>=0.50",
    "pyfiglet>=1.0",
]
```

跑 `uv sync` 确认拉下来.

**Step 4: Test** — 写 `tests/test_phase19_deps.py`:
```python
def test_textual_importable():
    import textual.app
    import textual.widgets

def test_pyfiglet_importable():
    import pyfiglet
    assert pyfiglet.figlet_format("test", font="standard")
```

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/test_phase19_deps.py
git commit -m "$(cat <<'EOF'
pyproject · Phase 19 Task 12: textual + pyfiglet 新依赖

textual >=0.50 (TUI framework, Rich 作者出品), pyfiglet >=1.0 (splash ASCII logo).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: `chat/tui_app.py` ExplainChatApp scaffolding

**Files:**
- Create: `src/explain_engine/chat/tui_app.py`
- Create: `src/explain_engine/chat/tui_app.tcss`
- Test: `tests/test_tui_app_basic.py` (新)

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_tui_app_composes_input_and_output(tmp_path, monkeypatch):
    """ExplainChatApp.compose yield Input + RichLog."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.persistence.storage_v2 import StorageV2

    ephemeral = EphemeralChatSession(storage=StorageV2(), llm=AsyncMock())
    app = ExplainChatApp(llm=AsyncMock(), light_llm=AsyncMock(), ephemeral_chat=ephemeral)
    async with app.run_test() as pilot:
        # 验 Input + RichLog widget mount
        assert app.query_one("#prompt") is not None
        assert app.query_one("#output") is not None
        await pilot.press("q")  # Ctrl+C 不行 (会真退出), 用 binding key
```

**Step 2: Run** — FAIL (ExplainChatApp 不存在)

**Step 3: Impl** — `tui_app.py`:

```python
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header, Footer, Input, RichLog


class ExplainChatApp(App):
    BINDINGS = [
        Binding("ctrl+o", "toggle_thinking", "折叠 thinking"),
        Binding("ctrl+c", "quit_app", "退出"),
        Binding("ctrl+l", "clear_log", "清屏"),
    ]
    CSS_PATH = "tui_app.tcss"

    def __init__(self, llm, light_llm, ephemeral_chat):
        super().__init__()
        self.llm = llm
        self.light_llm = light_llm
        self.chat = ephemeral_chat
        self._thinking_visible = True

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="output", wrap=True, highlight=False, markup=True)
        yield Input(id="prompt", placeholder="问点什么... (/help, Ctrl+O 折叠 thinking)")
        yield Footer()

    def action_toggle_thinking(self):
        # Wave 4 实装
        pass

    def action_quit_app(self):
        self.exit()

    def action_clear_log(self):
        self.query_one("#output", RichLog).clear()
```

`tui_app.tcss` (基础):
```css
Screen { background: $surface; }
RichLog#output { border: solid $primary; padding: 1; }
Input#prompt { dock: bottom; }
```

**Step 4: Run** — PASS.

**Step 5: Commit**

```bash
git add src/explain_engine/chat/tui_app.py src/explain_engine/chat/tui_app.tcss tests/test_tui_app_basic.py
git commit -m "$(cat <<'EOF'
chat/tui_app · Phase 19 Task 13: ExplainChatApp scaffolding

textual App 主壳, compose Header + RichLog + Input + Footer. BINDINGS:
Ctrl+O (toggle thinking, Wave 4 实装), Ctrl+C (退出), Ctrl+L (清屏).
基础 tui_app.tcss style.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: tui_app._render_event 主分支

**Files:** Modify `src/explain_engine/chat/tui_app.py` + `tests/test_tui_app_render.py` (新)

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_render_event_assistant_text():
    ...
    app = ExplainChatApp(...)
    async with app.run_test() as pilot:
        await app._render_event(ChatEvent(type="assistant_text", content="hello"))
        log = app.query_one("#output", RichLog)
        # 验 log 含 "hello" 行
        ...


@pytest.mark.asyncio
async def test_render_event_slash_quit_exits():
    app = ExplainChatApp(...)
    async with app.run_test() as pilot:
        await app._render_event(ChatEvent(type="slash_quit", content="bye"))
        # 验 app exit pending
        ...
```

**Step 3: Impl** — 加 `_render_event` method, 接 ChatEvent dispatch 到 RichLog write / exit / 其他.

**Step 5: Commit** — `chat/tui_app · Phase 19 Task 14: _render_event 主分支 (assistant_text / slash_quit / 其他 fallback)`

---

### Task 15: tui_app slash_deepen_promoted / slash_reset_to_ephemeral handler

**Files:** Modify `tui_app.py`, test `tests/test_tui_app_render.py`

**Step 1: Test** — 模拟 slash_deepen_promoted event, 验 app.chat 替换成 ChatSession instance.

**Step 3: Impl** — `_render_event` 加分支:
```python
elif ev.type == "slash_deepen_promoted":
    new_sid = ev.metadata["sid"]
    from explain_engine.chat.session import ChatSession
    self.chat = ChatSession(new_sid, llm=self.llm)
    log.write("[green]已切到 ChatSession[/green]")
elif ev.type == "slash_reset_to_ephemeral":
    # 重建 ephemeral
    ...
```

**Step 5: Commit** — `chat/tui_app · Phase 19 Task 15: slash_deepen_promoted + slash_reset_to_ephemeral handler`

---

### Task 16: Input.Submitted handler 接 user input → dispatch_slash / handle_user_input

**Files:** Modify `tui_app.py`, test `tests/test_tui_app_input.py` (新)

**Step 1: Test** — pilot 输入 "你好" + Enter, 验 chat.handle_user_input 调到.

**Step 3: Impl**:
```python
from textual import on

@on(Input.Submitted)
async def _handle_input(self, event: Input.Submitted):
    text = event.value
    event.input.value = ""
    if text.startswith("/"):
        from explain_engine.chat.slash_commands import dispatch_slash
        events = await dispatch_slash(self.chat, text)
        for ev in events:
            await self._render_event(ev)
        return
    async for ev in self.chat.handle_user_input(text, self.llm):
        await self._render_event(ev)
```

**Step 5: Commit** — `chat/tui_app · Phase 19 Task 16: Input.Submitted handler 接 slash + 自然语言`

---

### Task 17: repl_entry.enter_repl_async 重写用 textual App

**Files:**
- Modify: `src/explain_engine/chat/repl_entry.py` (整 enter_repl_async 重写)
- Test: `tests/test_repl_entry.py` (现有, 估需 rewrite)

**Step 1: Test** — 验 enter_repl_async 真启 textual App (mock ExplainChatApp.run_async).

**Step 3: Impl** — `repl_entry.py`:
```python
async def enter_repl_async(llm, light_llm=None) -> None:
    from explain_engine.chat.tui_app import ExplainChatApp
    from explain_engine.chat.ephemeral import EphemeralChatSession
    from explain_engine.persistence.storage_v2 import StorageV2

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage, llm=llm)
    app = ExplainChatApp(llm=llm, light_llm=light_llm, ephemeral_chat=ephemeral)
    await app.run_async()
```

老 Rich Console + prompt_toolkit 代码全删 (在 git history 内仍可查).

**Step 5: Commit** — `chat/repl_entry · Phase 19 Task 17: 重写 enter_repl_async 用 textual App`

---

### Task 18: Wave 3 全量 pytest + ruff

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```

Expected: 现 chat session test (~40 个) 零回归. 现 chat REPL test (~5-10 个) 可能 break — 列出来留 Wave 5 集中修. Wave 3 末: ruff 0.

---

## Wave 4 — Collapsible thinking + Ctrl+O

### Task 19: tui_app `_mount_thinking_section` 用 Collapsible widget

**Files:** Modify `tui_app.py` + `tui_app.tcss`, test `tests/test_tui_app_thinking.py` (新)

**Step 1: Test**:
```python
@pytest.mark.asyncio
async def test_thinking_section_mounts_collapsible_default_expand():
    app = ExplainChatApp(...)
    async with app.run_test() as pilot:
        await app._render_event(ChatEvent(type="thinking_text", content="思考过程"))
        # 验 RichLog 内有 Collapsible widget, collapsed=False
        collapsibles = app.query("Collapsible")
        assert len(collapsibles) == 1
        assert collapsibles[0].collapsed is False
        # 验 dim color
        ...
```

**Step 3: Impl** — `_render_event` thinking_text 分支:
```python
elif ev.type == "thinking_text":
    from textual.widgets import Collapsible, Static
    log = self.query_one("#output", RichLog)
    # 不能直接 mount widget 到 RichLog (RichLog 只 write text).
    # 改用 VerticalScroll 容器 替代 RichLog, 或用 Markdown widget.
    # 或: thinking 段直接 log.write("[dim]" + ev.content + "[/dim]") + 标 collapsible marker.
```

注意: textual RichLog 不支持 mount 子 widget. 需要在 compose 用 `VerticalScroll` 容器 (line-based message log), thinking 段 mount `Collapsible` 内嵌 dim text. 这是结构改动 — 实际看 textual docs 选用 widget 组合 (e.g. `Log` + `Collapsible`s).

实现细节 implementer 自己探.

**Step 5: Commit** — `chat/tui_app · Phase 19 Task 19: thinking_text → Collapsible widget mount (dim, 默 expand)`

---

### Task 20: `action_toggle_thinking` 切换所有 Collapsible.collapsed

**Files:** Modify `tui_app.py`, test `tests/test_tui_app_thinking.py`

**Step 1: Test**:
```python
@pytest.mark.asyncio
async def test_ctrl_o_toggles_all_thinking():
    app = ExplainChatApp(...)
    async with app.run_test() as pilot:
        await app._render_event(ChatEvent(type="thinking_text", content="1"))
        await app._render_event(ChatEvent(type="thinking_text", content="2"))
        assert all(c.collapsed is False for c in app.query("Collapsible"))
        await pilot.press("ctrl+o")
        assert all(c.collapsed is True for c in app.query("Collapsible"))
        await pilot.press("ctrl+o")
        assert all(c.collapsed is False for c in app.query("Collapsible"))
```

**Step 3: Impl**:
```python
def action_toggle_thinking(self):
    self._thinking_visible = not self._thinking_visible
    for c in self.query("Collapsible"):
        c.collapsed = not self._thinking_visible
```

**Step 5: Commit** — `chat/tui_app · Phase 19 Task 20: Ctrl+O 切所有 Collapsible.collapsed`

---

### Task 21: slash `/thinking on|off` handler + 注册

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py` (`_handle_thinking`)
- Modify: `src/explain_engine/chat/chat_copy.py` (zh str + COMMAND_DESCRIPTIONS)
- Test: `tests/test_chat_slash_thinking.py` (新)

**Step 1: Test**:
```python
def test_thinking_registered():
    from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
    assert "thinking" in [c.name for c in DEFAULT_COMMANDS]


@pytest.mark.asyncio
async def test_thinking_on_off():
    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "thinking")
    # mock chat with _thinking_visible attribute
    chat = MagicMock()
    events_on = await cmd.handler(chat, ["on"])
    assert any("已开启" in (e.content or "") for e in events_on)
    events_off = await cmd.handler(chat, ["off"])
    assert any("已关闭" in (e.content or "") for e in events_off)
    events_invalid = await cmd.handler(chat, ["bad"])
    assert any("用法" in (e.content or "") for e in events_invalid)
```

**Step 3: Impl** — 新 `_handle_thinking(chat, args) -> list[ChatEvent]` + 注册 DEFAULT_COMMANDS. 25 → 25 (实际是 24 → 25 加 /thinking).

注: /thinking on|off 实际 affect 是 textual app 的 _thinking_visible. slash 需 yield 个 event 让 app 切. 用 `slash_thinking_toggle` event with metadata={"visible": True/False}, tui_app `_render_event` 接到 → action_toggle_thinking 等价行为.

或者更简单: /thinking 直接修 chat._thinking_visible attribute, tui_app 下次 mount Collapsible 时读这值定 collapsed. 但已 mount 的 Collapsible 没动. → 还得 toggle 现 mount 的, 走 event yield path.

**Step 5: Commit** — `chat/slash · Phase 19 Task 21: /thinking on|off handler + DEFAULT_COMMANDS + tui app 接 toggle event`

---

### Task 22: thinking_text + Collapsible + LiveStream integration test

**Files:** `tests/test_tui_app_thinking.py`

**Step 1: Test** — end-to-end: 启 app, mock LLM 返 reasoning, 走 handle_user_input, 验 Collapsible mount + 默 expand + Ctrl+O 切.

**Step 5: Commit** — `tests/test_tui_app_thinking · Phase 19 Task 22: LLM reasoning → thinking_text → Collapsible 端到端`

---

### Task 23: Wave 4 全量 pytest + ruff (+ brittle count fix 24 → 25)

```bash
.venv/bin/python -m pytest tests/ -q
```

`test_chat_slash_commands.py::test_total_count_is_24` → `_25`. 修.

**Step 5: Commit** — `test_chat_slash_commands · Phase 19 Task 23 follow-up: count 24 → 25`

---

## Wave 5 — Spinner (status mount/unmount) + 现 chat REPL test rewrite

### Task 24: tui_app `_mount_status` / `_unmount_status` (LoadingIndicator)

**Files:** Modify `tui_app.py`, test `tests/test_tui_app_status.py` (新)

**Step 1: Test**:
```python
@pytest.mark.asyncio
async def test_status_start_mounts_loading_indicator():
    app = ExplainChatApp(...)
    async with app.run_test():
        await app._render_event(ChatEvent(type="status_start", content="思考中..."))
        # 验 mount 了 LoadingIndicator + 灰 text "思考中..."
        assert app.query("LoadingIndicator")


@pytest.mark.asyncio
async def test_status_end_unmounts_loading_indicator():
    app = ExplainChatApp(...)
    async with app.run_test():
        await app._render_event(ChatEvent(type="status_start", content="思考中..."))
        await app._render_event(ChatEvent(type="status_end"))
        assert not app.query("LoadingIndicator")
```

**Step 3: Impl** — `_render_event` status_start/end 分支:
```python
elif ev.type == "status_start":
    from textual.widgets import LoadingIndicator, Static
    container = self.query_one("#output")  # 或新 container
    indicator = LoadingIndicator()
    label = Static(f"[dim]{ev.content}[/dim]")
    self._mount_widgets_for_status = (indicator, label)
    container.mount(indicator, label)
elif ev.type == "status_end":
    if hasattr(self, "_mount_widgets_for_status"):
        for w in self._mount_widgets_for_status:
            w.remove()
        del self._mount_widgets_for_status
```

**Step 5: Commit** — `chat/tui_app · Phase 19 Task 24: status_start/end mount/unmount LoadingIndicator`

---

### Task 25: ChatSession + ephemeral status_start/end → tui app render 集成

**Files:** test `tests/test_phase19_full_flow.py` (新, 类 phase18_full_flow)

**Step 1: Test** — mock LLM, 启 app, 走 chat input, 验 status_start mount → handler 完成 → status_end unmount.

**Step 5: Commit** — `tests · Phase 19 Task 25: status_start/end → tui_app LoadingIndicator 端到端`

---

### Task 26: 现 chat REPL test rewrite (textual.pilot fixture)

**Files:**
- Modify: `tests/test_repl_entry.py` / `tests/test_repl_entry_ephemeral_chat.py` / `tests/test_cli_chat.py` / `tests/test_repl_history.py`
- 估 5-10 test 改

**Step 1-3:** 把 prompt_toolkit + Rich Console mock 替换成 textual.pilot fixture. 跟 Phase 18 Wave 3 同 pattern, 但 render layer 全换.

**Step 5: Commit** — `tests/test_chat_repl · Phase 19 Task 26: 现 REPL test rewrite textual.pilot fixture`

---

### Task 27: cli `test_cli_chat.py` audit

可能跟 Task 26 合并 commit. 看 cli explain chat <sid> 启 ChatSession 还是 textual App. 改 mock.

---

### Task 28: Wave 5 全量 pytest + ruff

Expected: 全量 ~1330 PASS. ruff 0.

---

## Wave 6 — Splash screen

### Task 29: `chat/splash_screen.py` SplashScreen + pyfiglet ASCII logo

**Files:**
- Create: `src/explain_engine/chat/splash_screen.py`
- Test: `tests/test_splash_screen.py` (新)

**Step 1: Test**:
```python
def test_splash_screen_renders_logo():
    from explain_engine.chat.splash_screen import SplashScreen
    s = SplashScreen()
    # 验 compose 含 ASCII logo Static widget
    ...


def test_splash_screen_4_init_steps():
    s = SplashScreen()
    assert len(s.INIT_STEPS) == 4
    assert "lexicon" in s.INIT_STEPS[0]["label"].lower()
```

**Step 3: Impl** — `splash_screen.py`:
```python
import pyfiglet
from textual.screen import Screen
from textual.widgets import Static, LoadingIndicator
from textual.containers import Vertical


class SplashScreen(Screen):
    INIT_STEPS = [
        {"label": "加载 lexicon", "fn": "_init_lexicon"},
        {"label": "连接 PG", "fn": "_ping_pg"},
        {"label": "加载 theory cache", "fn": "_load_theory_cache"},
        {"label": "启动 chat REPL", "fn": "_ready_signal"},
    ]

    def compose(self):
        logo = pyfiglet.figlet_format("Explain", font="standard")
        yield Vertical(
            Static(f"[bold cyan]{logo}[/bold cyan]", classes="splash-logo"),
            Static("[dim]Explain Engine[/dim]", classes="splash-version"),
            *[Static(f"· {s['label']}", id=f"step-{i}", classes="splash-step-pending")
              for i, s in enumerate(self.INIT_STEPS)],
        )

    async def on_mount(self):
        # 4 init step 并发跑, 渐进点亮
        ...
```

**Step 5: Commit** — `chat/splash_screen · Phase 19 Task 29: SplashScreen + pyfiglet ASCII logo + 4 init step widget`

---

### Task 30: Splash 4 init step textual Worker 并发跑

**Files:** Modify `splash_screen.py`, test `tests/test_splash_screen.py`

**Step 1: Test** — pilot 启动 SplashScreen, mock 4 个 init fn, 验 渐进点亮 (前 step done 后 next active).

**Step 3: Impl** — `on_mount`:
```python
async def on_mount(self):
    for i, step in enumerate(self.INIT_STEPS):
        widget = self.query_one(f"#step-{i}", Static)
        widget.update(f"[yellow]⠋ {step['label']}...[/yellow]")
        fn = getattr(self, step["fn"])
        try:
            await fn()
            widget.update(f"[green]✓ {step['label']}[/green]")
        except Exception as exc:
            widget.update(f"[red]✗ {step['label']}: {exc}[/red]")
    await self._signal_done()
```

`_init_lexicon` / `_ping_pg` / `_load_theory_cache` / `_ready_signal` 各自 async method.

**Step 5: Commit** — `chat/splash_screen · Phase 19 Task 30: 4 init step 串行渐进点亮 (异步 step fn)`

---

### Task 31: ExplainChatApp.on_mount push SplashScreen 1s 后 pop

**Files:** Modify `tui_app.py`, test `tests/test_tui_app_splash_integration.py` (新)

**Step 1: Test** — 启 app, 验 SplashScreen first push, 1s 后 pop, current_screen = main chat.

**Step 3: Impl** — `tui_app.py`:
```python
async def on_mount(self):
    splash = SplashScreen()
    await self.push_screen(splash)
    await splash.wait_done()  # SplashScreen 提供 wait_done 协程
    await asyncio.sleep(1)  # 显示 1s
    self.pop_screen()
```

**Step 5: Commit** — `chat/tui_app · Phase 19 Task 31: on_mount push SplashScreen, 4 step done + 1s 后 pop`

---

### Task 32: cli `--no-splash` flag 跳

**Files:** Modify `src/explain_engine/cli.py` + `tui_app.py`

**Step 1: Test** — cli `uv run explain --no-splash` → ExplainChatApp 接受 `show_splash=False` 不 push SplashScreen.

**Step 3: Impl** — typer 加 `--no-splash` flag, pass 给 ExplainChatApp, on_mount 内 if not show_splash: return.

**Step 5: Commit** — `cli + chat/tui_app · Phase 19 Task 32: --no-splash flag 跳 (CI / 测试用)`

---

### Task 33: Wave 6 全量 pytest + ruff

Expected: 全量 ~1340 PASS. ruff 0.

---

## Wave 7 — 收口

### Task 34: 全量 pytest 零回归

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: 1310 (Phase 18 base) + Phase 19 新 ~30 test = ~1340 PASS. preexisting 2 fail 仍 ok.

若有真 fail (非 preexisting), 修 + 单 task commit.

---

### Task 35: ruff check + auto-fix

```bash
.venv/bin/ruff check src/ tests/ --fix
```

Expected: 0 报错.

---

### Task 36: 4 smoke 实测

```bash
# Smoke 1: chat 默 thinking 显示
uv run explain
# 期望: splash 显示 1-2s → chat REPL. 问 "为什么烧水能沸"
# → "思考中..." spinner → thinking section (dim, expand 默) → answer text.

# Smoke 2: Ctrl+O 折叠
Ctrl+O
# 期望: thinking section 都缩成 ▶ 一行. 再 Ctrl+O 展开 ▼.

# Smoke 3: slash /thinking off
> /thinking off
# 期望: 后续 chat 不渲染 thinking_text event. /thinking on 恢复.

# Smoke 4: env disable
LLM_THINKING_DISABLED=1 uv run explain
# 期望: chat 不出现 thinking section (Response.reasoning is None).

# Smoke 5: --no-splash
uv run explain --no-splash
# 期望: 不显 SplashScreen, 直进 chat REPL.

# Smoke 6: cli batch unchanged
uv run explain new "为什么烧水能沸"
# 期望: 跟 Phase 18 batch mode 完全一致, 不走 textual.
```

记录: 实际 LLM 调用 / thinking section 是否真显示 / Ctrl+O 真切 / smoke 是否全过.

---

### Task 37: Acceptance doc + 收口

**Files:** Create `docs/plans/2026-05-27-phase-19-tui-acceptance.md`

内容:
- Phase 19 总览 (textual chat REPL + reasoning_content + UX 三连击)
- 改动文件清单 (按 wave 分)
- 测试统计 (Wave 1-6 新 ~30 test, 全量 ~1340 PASS / preexisting 2)
- Bug fix 验证 (6 smoke 实测输出)
- backward compat: cli batch subcommand 全不变, Phase 17.2/18 全保留, LLMClient Response 加 reasoning field default None 老 caller 不感知
- 已知 follow-up (Phase 20+):
  - 真"单条 thinking 折叠" (textual TreeControl 键盘单条 nav)
  - prompt_toolkit 完全移除 (typer.confirm 改 textual modal)
  - streaming thinking 期间也 Ctrl+O 实时切
  - splash 步骤 user 可定制
  - non-TTY 环境 (CI / docker) textual 启动会挂 — `--no-splash` 已 cover, 但 `uv run explain chat` 仍需 TTY. 可加 fallback Rich Console 模式
- preexisting fail 2 个长期 follow-up

**Commit**:
```bash
git add docs/plans/2026-05-27-phase-19-tui-acceptance.md
git commit -m "$(cat <<'EOF'
docs/plans · Phase 19 Task 37: acceptance doc + 收口

textual TUI 重写 chat REPL 落地, Hybrid UX 三连击 (Ctrl+O 折叠 thinking +
spinner + splash). cli batch subcommand 全不变, Phase 17.2/18 全保留.

7 wave / ~37 task, 全量 ~1340 PASS / preexisting 2 fail.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Summary

- **37 task / 7 wave**
- 每 task TDD red → green → commit, 中文 commit msg, scope · Phase 19 Task N: action format
- backward compat: cli batch subcommand 完全不变, Phase 17.2 (classify + light_llm + delete) + Phase 18 (/deepen) 全保留, lexicon / PG / theories 不动
- 新依赖: textual >=0.50, pyfiglet >=1.0
- 已知 trade-off (Phase 19 接受):
  - 现 ~5-10 chat REPL test 需 rewrite (textual.pilot)
  - prompt_toolkit 仍保留 (chat REPL 不用, 别处仍用)
  - textual 在 non-TTY 环境 fail (CI 用 cli batch / `--no-splash`)
  - Ctrl+O 切所有 thinking (不单条)
- 已知 Phase 20+ follow-up:
  - 键盘单条 thinking nav
  - prompt_toolkit 完全移除
  - streaming 期间实时 Ctrl+O 切
  - splash 步骤 user 定制
  - non-TTY fallback Rich 模式
