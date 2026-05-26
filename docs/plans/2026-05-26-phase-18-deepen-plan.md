# Phase 18 Implementation Plan — `/deepen` Hybrid 化

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Chat REPL 从"机械化 bootstrap"改成"system-1 chat + 显式 /deepen escalate", 让 LLM 默直接答简单问题, user 显式触发深度建模 pipeline.

**Architecture:** EphemeralChatSession 新增 `send_user_message` 让 ephemeral 下也能跟 LLM chat (无 phenomena state, 无持久化). 新增 slash `/deepen [Q]` 显式触发 promote_to_persistent (= 现 bootstrap pipeline 入口). chat REPL outer loop 改: ephemeral 自然语言不再 auto-promote.

**Tech Stack:** Python 3.13 / pytest / asyncio / Rich (REPL render) / typer (cli). 跟 Phase 17.2 同栈.

**Reference design:** [`docs/plans/2026-05-26-phase-18-deepen-design.md`](2026-05-26-phase-18-deepen-design.md)

---

## 项目惯例 (严格 follow, 跟 Phase 17.2 同)

- 严格 TDD: red → green → commit, 每 task 一个 commit
- pytest: `.venv/bin/python -m pytest tests/test_X.py -k test_Y -xvs`
- 全量: `.venv/bin/python -m pytest tests/ -q`
- ruff: `.venv/bin/ruff check src/ tests/`
- 中文 commit msg + 末尾 `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
- commit format: `<scope> · Phase 18 Task N: <action>`
- 绝不 push / amend / --no-verify / 动 git config

---

## Wave 1 — EphemeralChatSession chat 能力 (基础)

### Task 1: `ephemeral_chat.yaml` 新建 + load test

**Files:**
- Create: `src/explain_engine/llm/prompts/ephemeral_chat.yaml`
- Test: `tests/test_ephemeral_chat_yaml.py` (新建)

**Step 1: Write failing test**

```python
# tests/test_ephemeral_chat_yaml.py
"""ephemeral_chat.yaml load test — Phase 18 Task 1."""
from explain_engine.llm.prompts._loader import load_prompt


def test_ephemeral_chat_yaml_loads():
    p = load_prompt("ephemeral_chat")
    assert "system" in p
    assert "deepen" in p["system"].lower()  # 提到 /deepen 引导
    assert "ephemeral" in p["system"].lower() or "system-1" in p["system"]
```

**Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_ephemeral_chat_yaml.py -xvs
```
Expected: FAIL with `FileNotFoundError: prompt ephemeral_chat not found`

**Step 3: 创建 yaml**

```yaml
# src/explain_engine/llm/prompts/ephemeral_chat.yaml
description: |
  Phase 18: EphemeralChatSession 下 LLM chat 的 system prompt.
  目标: 用户在 ephemeral 模式跟 LLM 对话, LLM 用 system-1 直接答, 不假装做现象列举/多领域分析.
  适当时机建议用户用 /deepen 触发深度 reasoning pipeline.

system: |
  你是 explain-engine 的 chat assistant. 用户当前在 ephemeral 模式跟你对话, 系统尚未启动深度建模 pipeline.

  你的工作:
  1. 用户问什么, 你尽量基于自己的知识简洁清晰地回答 (system-1, 直接答)
  2. 若用户问 "为什么 X" 类问题, 直接基于已知机制 / 共识答案回答
  3. **关键**: 不要假装做现象列举 / 多领域分析 — 这些是 deep reasoning pipeline 的工作

  当用户表现出以下信号时, **可以建议 (不强制)** 用户用 `/deepen [问题]` 命令触发深度 reasoning:
  - 用户对你的简短回答不满意, 想更深入
  - 问题涉及多领域因果 (e.g. "为什么年轻人不消费")
  - 问题需要假设性反事实分析 (e.g. "如果 X 变化, Y 会怎样")
  - 用户明确说 "帮我建模" / "深入分析"

  不要每条回答都强推 /deepen — 简单问题答完就够.

user_template: |
  {text}
```

**Step 4: Run** — PASS

**Step 5: Commit**

```bash
git add src/explain_engine/llm/prompts/ephemeral_chat.yaml tests/test_ephemeral_chat_yaml.py
git commit -m "$(cat <<'EOF'
prompts · Phase 18 Task 1: ephemeral_chat.yaml 新建

EphemeralChatSession 下 LLM system-1 chat 的 system prompt. 引导 LLM 用
自己知识直接答, 不假装现象列举. 适当时机建议 user 用 /deepen 触发 pipeline.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `EphemeralChatSession.send_user_message` 基础 (LLM chat 调用 + yield assistant_text)

**Files:**
- Modify: `src/explain_engine/chat/ephemeral.py` (新增 method)
- Test: `tests/test_ephemeral_send_user_message.py` (新建)

**Step 1: Write failing test**

```python
# tests/test_ephemeral_send_user_message.py
"""EphemeralChatSession.send_user_message — Phase 18 Task 2."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.persistence.storage_v2 import StorageV2


@pytest.mark.asyncio
async def test_send_user_message_yields_assistant_text(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    resp = MagicMock()
    resp.text = "烧水沸腾是因为水的饱和蒸汽压等于大气压."
    llm.chat.return_value = resp

    events = []
    async for ev in ephemeral.send_user_message("为什么烧水能沸", llm):
        events.append(ev)

    assistant_events = [e for e in events if e.type == "assistant_text"]
    turn_complete_events = [e for e in events if e.type == "turn_complete"]

    assert len(assistant_events) >= 1
    assert "饱和蒸汽压" in assistant_events[0].content
    assert len(turn_complete_events) == 1
```

**Step 2: Run** — FAIL `AttributeError: 'EphemeralChatSession' object has no attribute 'send_user_message'`

**Step 3: Implementation**

在 `src/explain_engine/chat/ephemeral.py` 加 method (放在 promote_to_persistent 之前):

```python
async def send_user_message(
    self,
    text: str,
    llm: LLMClient,
):
    """Phase 18: Ephemeral 下 LLM system-1 chat.

    yield assistant_text + turn_complete events. transcript in-memory append
    (不持久 — storage_v2 不写 transcript.jsonl).

    LLM 失败 (LLMError / SchemaValidationError) → yield slash_error event,
    transcript 不变 (retry 友好).
    """
    from explain_engine.chat.session import ChatEvent
    from explain_engine.llm.client import Message
    from explain_engine.llm.errors import LLMError, SchemaValidationError
    from explain_engine.llm.prompts._loader import load_prompt

    prompt = load_prompt("ephemeral_chat")
    messages = [Message(role="system", content=prompt["system"])]
    # 拼现有 transcript (in-memory)
    for msg in self.transcript:
        messages.append(Message(role=msg["role"], content=msg["content"]))
    messages.append(Message(role="user", content=text))

    try:
        resp = await llm.chat(messages)
    except (LLMError, SchemaValidationError) as exc:
        yield ChatEvent(
            type="slash_error",
            content=f"LLM 调用失败: {type(exc).__name__}: {exc}",
        )
        return

    assistant_text = resp.text or ""
    # transcript append (in-memory)
    self.transcript.append({"role": "user", "content": text})
    self.transcript.append({"role": "assistant", "content": assistant_text})

    yield ChatEvent(type="assistant_text", content=assistant_text)
    yield ChatEvent(type="turn_complete", content=None)
```

注: `LLMClient.chat()` 接受 `list[Message]` 返 `Response` (含 `.text` / `.parsed`). 若现 LLM client 没 `.text` field, 用 `getattr(resp, "text", "") or resp.parsed`.

**Step 4: Run** — PASS

**Step 5: Commit**

```bash
git add src/explain_engine/chat/ephemeral.py tests/test_ephemeral_send_user_message.py
git commit -m "$(cat <<'EOF'
chat/ephemeral · Phase 18 Task 2: send_user_message 基础 LLM chat

EphemeralChatSession 加 send_user_message(text, llm) 方法, 调 LLM.chat()
with ephemeral_chat.yaml system prompt. yield assistant_text + turn_complete events.
transcript in-memory append (不持久 — ephemeral 行为).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `send_user_message` LLM 失败 → yield error event + transcript 不变

**Files:** Modify `tests/test_ephemeral_send_user_message.py`

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_send_user_message_llm_error_no_transcript_pollution(tmp_path, monkeypatch):
    from explain_engine.llm.errors import LLMError

    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    llm.chat.side_effect = LLMError("network down")

    events = []
    async for ev in ephemeral.send_user_message("hi", llm):
        events.append(ev)

    error_events = [e for e in events if e.type == "slash_error"]
    assert len(error_events) == 1
    assert "LLMError" in error_events[0].content
    # transcript 不变 (retry 友好)
    assert ephemeral.transcript == []
```

**Step 2: Run** — PASS (Task 2 impl 已 cover)

**Step 5: Commit**

```bash
git add tests/test_ephemeral_send_user_message.py
git commit -m "$(cat <<'EOF'
chat/ephemeral · Phase 18 Task 3: send_user_message LLM 失败 transcript 不污染

LLM raise → yield slash_error event, transcript 不 append (retry 友好).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `send_user_message` 多轮 chat transcript 累积

**Files:** Modify `tests/test_ephemeral_send_user_message.py`

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_send_user_message_multi_turn_transcript_accumulates(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    # 2 轮 chat
    resp1 = MagicMock(text="第一轮回答")
    resp2 = MagicMock(text="第二轮回答")
    llm.chat.side_effect = [resp1, resp2]

    async for _ in ephemeral.send_user_message("问题1", llm):
        pass
    async for _ in ephemeral.send_user_message("问题2", llm):
        pass

    assert len(ephemeral.transcript) == 4
    assert ephemeral.transcript[0] == {"role": "user", "content": "问题1"}
    assert ephemeral.transcript[1] == {"role": "assistant", "content": "第一轮回答"}
    assert ephemeral.transcript[2] == {"role": "user", "content": "问题2"}
    assert ephemeral.transcript[3] == {"role": "assistant", "content": "第二轮回答"}
```

**Step 2: Run** — PASS

**Step 5: Commit**

```bash
git add tests/test_ephemeral_send_user_message.py
git commit -m "$(cat <<'EOF'
chat/ephemeral · Phase 18 Task 4: send_user_message 多轮 transcript 累积

多次 send_user_message 在 in-memory transcript 累 user/assistant pair,
保证 /deepen 不带参时能取最近 user msg.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `send_user_message` 第 2 轮带 chat history 进 LLM messages

**Files:** Modify `tests/test_ephemeral_send_user_message.py`

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_send_user_message_second_turn_passes_history_to_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)
    ephemeral.transcript = [
        {"role": "user", "content": "之前问题"},
        {"role": "assistant", "content": "之前回答"},
    ]

    llm = AsyncMock()
    resp = MagicMock(text="新回答")
    llm.chat.return_value = resp

    async for _ in ephemeral.send_user_message("新问题", llm):
        pass

    # 验 LLM messages 含 history
    call_args = llm.chat.call_args
    messages = call_args[0][0]
    roles = [m.role for m in messages]
    contents = [m.content for m in messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert "之前问题" in contents
    assert "之前回答" in contents
    assert "新问题" in contents
```

**Step 2: Run** — PASS

**Step 5: Commit**

```bash
git add tests/test_ephemeral_send_user_message.py
git commit -m "$(cat <<'EOF'
chat/ephemeral · Phase 18 Task 5: send_user_message 携带 history 进 LLM

第 N 轮 chat 把 transcript 之前 N-1 轮的 user/assistant pair 一起拼进
messages, 让 LLM 看到上下文. system prompt 仍是 ephemeral_chat.yaml.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: ephemeral transcript 不持久到 storage_v2 (验证)

**Files:** Modify `tests/test_ephemeral_send_user_message.py`

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_ephemeral_chat_does_not_persist_transcript(tmp_path, monkeypatch):
    """ephemeral 下 send_user_message 后, storage_v2 不写 transcript.jsonl."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    llm = AsyncMock()
    llm.chat.return_value = MagicMock(text="answer")

    async for _ in ephemeral.send_user_message("问题", llm):
        pass

    # 验 storage_v2 没有任何 session_dir (ephemeral.sid is None)
    assert ephemeral.sid is None
    # 项目 dir 应该为空 / 无 sessions
    sessions_root = storage.project_dir() / "sessions"
    if sessions_root.exists():
        assert list(sessions_root.iterdir()) == []  # 没 session dir
```

**Step 2: Run** — PASS (Task 2 impl 没调 storage 持久化)

**Step 5: Commit**

```bash
git add tests/test_ephemeral_send_user_message.py
git commit -m "$(cat <<'EOF'
chat/ephemeral · Phase 18 Task 6: send_user_message 验证 transcript 不持久

ephemeral.send_user_message 走完不写 storage_v2 (sid is None), transcript
只在 in-memory list, /quit 后丢. trade-off: chat 历史不带入 persistent
(留 Phase 19 follow-up).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Wave 1 全量 pytest + ruff 验证

**Step 1:** Run

```bash
.venv/bin/python -m pytest tests/test_ephemeral_chat_yaml.py tests/test_ephemeral_send_user_message.py -xvs
.venv/bin/python -m pytest tests/ -q  # 全量零回归
.venv/bin/ruff check src/ tests/
```

Expected: Wave 1 加 ~5 个 test PASS, 全量 1287+ PASS (允许 preexisting PG flake), ruff 0.

若 fail, 修. 若全过, **不**提 commit (这是验证 step, 不是新 task).

**Step 2: Commit (若有 fix)**

若 ruff / pytest 有需修, 单独 commit.

---

## Wave 2 — `/deepen` slash 命令

### Task 8: `chat_copy.py` 加 deepen zh strings

**Files:**
- Modify: `src/explain_engine/chat/chat_copy.py`
- Test: `tests/test_chat_copy.py` (现存)

**Step 1: Write failing test**

```python
def test_deepen_copy_strings_exist():
    from explain_engine.chat.chat_copy import (
        err_deepen_no_question,
        err_deepen_already_promoted,
        msg_deepen_promote_start,
    )
    assert "用法" in err_deepen_no_question() or "/deepen" in err_deepen_no_question()
    assert "已建模" in err_deepen_already_promoted("Q") or "/new" in err_deepen_already_promoted("Q")
    assert "Q" in err_deepen_already_promoted("Q")
    assert "建模" in msg_deepen_promote_start("Q") or "深度" in msg_deepen_promote_start("Q")
```

**Step 2: Run** — FAIL ImportError

**Step 3: Implementation**

在 `src/explain_engine/chat/chat_copy.py` 加:

```python
def err_deepen_no_question() -> str:
    return "用法: /deepen <问题>  (或先 chat 一句, 再 /deepen 不带参取最近 user 输入)"

def err_deepen_already_promoted(current_question: str) -> str:
    return (
        f"本 session 已 /deepen 过 (建模主题: {current_question}). "
        f"想换主题请用 /new 开新 session."
    )

def msg_deepen_promote_start(question: str) -> str:
    return f"启动深度建模 (主题: {question})..."
```

也加到 `COMMAND_DESCRIPTIONS["deepen"]` + `HELP_GROUPS_ZH` 相应 group.

**Step 4: Run** — PASS

**Step 5: Commit**

```bash
git add src/explain_engine/chat/chat_copy.py tests/test_chat_copy.py
git commit -m "$(cat <<'EOF'
chat/chat_copy · Phase 18 Task 8: /deepen zh strings + COMMAND_DESCRIPTIONS 注册

3 个 zh 函数: err_deepen_no_question / err_deepen_already_promoted /
msg_deepen_promote_start. COMMAND_DESCRIPTIONS + HELP_GROUPS_ZH 加 "deepen" 条.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: `_handle_deepen` 带显式 question 参数

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py`
- Test: `tests/test_chat_slash_deepen.py` (新建)

**Step 1: Write failing test**

```python
# tests/test_chat_slash_deepen.py
"""/deepen handler — Phase 18 Task 9+."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from explain_engine.chat.ephemeral import EphemeralChatSession
from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
from explain_engine.persistence.storage_v2 import StorageV2


def test_deepen_registered_in_default_commands():
    names = [c.name for c in DEFAULT_COMMANDS]
    assert "deepen" in names


@pytest.mark.asyncio
async def test_deepen_with_explicit_question(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)

    # 注入 fake llm + mock promote_to_persistent 避真 LLM 调用
    fake_real_chat = MagicMock(sid="s_test1234")
    ephemeral.promote_to_persistent = AsyncMock(return_value=fake_real_chat)
    ephemeral._llm_for_test = MagicMock()  # 测试用属性, 待 Task 12 wire

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, ["为什么", "烧水", "能沸"])

    ephemeral.promote_to_persistent.assert_called_once()
    call_args = ephemeral.promote_to_persistent.call_args
    assert "为什么 烧水 能沸" in (call_args[0][0] if call_args[0] else call_args.kwargs["question"])
    assert any(e.type == "slash_deepen_promoted" for e in events)
```

**Step 2: Run** — FAIL (no /deepen handler)

**Step 3: Implementation** in `src/explain_engine/chat/slash_commands.py`:

```python
async def _handle_deepen(chat, args: list[str]) -> list[ChatEvent]:
    """Phase 18: /deepen [question] — ephemeral 下显式触发 bootstrap pipeline."""
    from explain_engine.chat.chat_copy import (
        err_deepen_already_promoted, err_deepen_no_question,
        msg_deepen_promote_start,
    )
    from explain_engine.chat.session import ChatEvent
    from explain_engine.chat.ephemeral import EphemeralChatSession

    # 已 promote 的 ChatSession 内 /deepen → 拒绝
    if not isinstance(chat, EphemeralChatSession):
        # ChatSession 持有 question 在 state.question (现 CognitiveState 有)
        current_q = getattr(getattr(chat, "state", None), "question", "?")
        return [ChatEvent(
            type="slash_error",
            content=err_deepen_already_promoted(current_q),
        )]

    # 取 question
    if args:
        question = " ".join(args)
    else:
        # 倒序找最近一句 user msg
        for msg in reversed(chat.transcript):
            if msg.get("role") == "user":
                question = msg["content"]
                break
        else:
            return [ChatEvent(type="slash_error", content=err_deepen_no_question())]

    # 调 promote_to_persistent (LLM 在 chat 实例上注入 — 看 repl_entry 怎么注入)
    llm = getattr(chat, "_llm_for_test", None)
    if llm is None:
        # production 路径: REPL outer loop 会把 chat 替换成 promote 后的 ChatSession,
        # _llm_for_test 是 test-only attribute. production caller 用 dispatch_slash
        # 上下文不能传 llm — Task 12 改 REPL outer loop 处理.
        return [ChatEvent(
            type="slash_error",
            content="LLM 未注入到 EphemeralChatSession (Task 12 待 wire)",
        )]

    try:
        real_chat = await chat.promote_to_persistent(question, llm)
    except Exception as exc:
        return [ChatEvent(
            type="slash_error",
            content=f"建模失败: {type(exc).__name__}: {exc}",
        )]

    return [
        ChatEvent(type="slash_deepen_promoted",
                  content=msg_deepen_promote_start(question),
                  metadata={"sid": real_chat.sid}),
    ]
```

注册到 DEFAULT_COMMANDS:

```python
SlashCommand(
    name="deepen",
    description=COMMAND_DESCRIPTIONS["deepen"],
    handler=_handle_deepen,
),
```

**Step 4: Run** — PASS

**Step 5: Commit**

```bash
git add src/explain_engine/chat/slash_commands.py tests/test_chat_slash_deepen.py
git commit -m "$(cat <<'EOF'
chat/slash · Phase 18 Task 9: /deepen handler + DEFAULT_COMMANDS 注册

/deepen [question] — ephemeral 状态显式触发 promote_to_persistent.
带参 → 用参 Q; 不带参 → 取 transcript 末 user msg fallback.
已 promote 的 ChatSession 内 /deepen → 拒绝, 提示 /new.

Task 12 把 LLM 从 REPL outer loop 注入到 ephemeral.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: `/deepen` 不带参 取 transcript 末 user msg

**Files:** Modify `tests/test_chat_slash_deepen.py`

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_deepen_without_args_uses_last_user_msg(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)
    ephemeral.transcript = [
        {"role": "user", "content": "为什么烧水能沸"},
        {"role": "assistant", "content": "因为蒸汽压..."},
        {"role": "user", "content": "能再说说吗"},
    ]
    fake_real_chat = MagicMock(sid="s_test1234")
    ephemeral.promote_to_persistent = AsyncMock(return_value=fake_real_chat)
    ephemeral._llm_for_test = MagicMock()

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    await cmd.handler(ephemeral, [])

    call_args = ephemeral.promote_to_persistent.call_args
    question = call_args[0][0] if call_args[0] else call_args.kwargs["question"]
    assert question == "能再说说吗"  # 最近 user msg, 简化 — 不识别 "为什么" 模式
```

**Step 2: Run** — PASS

**Step 5: Commit**

```bash
git add tests/test_chat_slash_deepen.py
git commit -m "$(cat <<'EOF'
chat/slash · Phase 18 Task 10: /deepen 不带参取 transcript 末 user msg

倒序找最近 role=user 的 content, 不识别 "为什么" 模式 (简化, user 掌控).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: `/deepen` 不带参 + transcript 空 → slash_error

**Files:** Modify `tests/test_chat_slash_deepen.py`

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_deepen_empty_transcript_no_args(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage)
    # transcript 空, 没 user msg

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, [])

    error_events = [e for e in events if e.type == "slash_error"]
    assert len(error_events) == 1
    assert "用法" in error_events[0].content
```

**Step 2: Run** — PASS

**Step 5: Commit**

```bash
git add tests/test_chat_slash_deepen.py
git commit -m "$(cat <<'EOF'
chat/slash · Phase 18 Task 11: /deepen 空 transcript + 无参 → slash_error

用法提示 "/deepen <问题>" 友好 fail.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: REPL outer loop 注入 LLM 到 EphemeralChatSession + /deepen 触发 chat 切换

**Files:**
- Modify: `src/explain_engine/chat/repl_entry.py` (主改点)
- Modify: `src/explain_engine/chat/slash_commands.py` (`_handle_deepen` 改用 chat.llm)
- Test: `tests/test_repl_deepen_flow.py` (新建)

**Step 1: Write failing test**

```python
# tests/test_repl_deepen_flow.py
"""REPL outer loop /deepen 集成 — Phase 18 Task 12."""
# 较复杂 integration test, 用 monkeypatch + AsyncMock 模拟 REPL loop
# 关键 assertion: ephemeral 状态 /deepen 后, REPL chat var 变 ChatSession
# (slash_deepen_promoted event 触发 outer loop 切换, 类似 slash_switch_session)
...
```

(复杂, 实际 test 编写时具体看 repl_entry.py:158-180 现 dispatch 模式)

**Step 2-4: Implementation**

`repl_entry.py:158-180` 改: ephemeral 状态下 user 输入自然语言 → 调 `send_user_message`, 而非自动 promote. `_handle_deepen` 处理 promote.

`slash_commands.py` 的 `_handle_deepen` 改用 `chat._llm` (REPL outer loop 注入到 ephemeral).

`EphemeralChatSession` 加 `llm` field (optional, REPL 注入):

```python
@dataclass
class EphemeralChatSession:
    storage: StorageV2
    state: CognitiveState = field(default_factory=...)
    chat_state: ChatStateDict = field(default_factory=ChatStateDict)
    transcript: list[dict] = field(default_factory=list)
    input_provider: Callable[[str], Awaitable[str]] | None = None
    memory_md: str = ""
    llm: "LLMClient | None" = None  # Phase 18: REPL 注入用
    ...
```

`repl_entry.py` 构造 EphemeralChatSession 时传 `llm=llm` (line ~73).

`slash_commands._handle_deepen` 改 `llm = getattr(chat, "llm", None) or getattr(chat, "_llm_for_test", None)`.

`slash_deepen_promoted` event 在 REPL outer loop 处理: 类似 `slash_switch_session`, 把 chat var 替换成 promote 返回的 ChatSession.

**Step 5: Commit**

```bash
git add src/explain_engine/chat/repl_entry.py src/explain_engine/chat/slash_commands.py src/explain_engine/chat/ephemeral.py tests/test_repl_deepen_flow.py
git commit -m "$(cat <<'EOF'
chat/repl_entry + slash · Phase 18 Task 12: REPL outer loop /deepen 切 ChatSession

EphemeralChatSession 加 llm field, REPL 构造时注入. _handle_deepen 用
chat.llm 调 promote. slash_deepen_promoted event 在 outer loop 触发 chat
var 替换成 promote 返回的 ChatSession (类似 slash_switch_session).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: ChatSession (已 promote) 内 /deepen 拒绝

**Files:** Modify `tests/test_chat_slash_deepen.py`

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_deepen_in_persistent_session_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    from explain_engine.chat.session import ChatSession
    from explain_engine.persistence.session import Session, SessionMeta, SessionStore
    from explain_engine.schema.state import CognitiveState

    # 预存一个 persistent session
    state = CognitiveState.bootstrap("已建模问题", budget=20)
    meta = SessionMeta.new("已建模问题")
    SessionStore().save(Session(meta=meta, state=state))

    chat = ChatSession(meta.session_id, llm=MagicMock())

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(chat, ["别的问题"])

    error_events = [e for e in events if e.type == "slash_error"]
    assert len(error_events) == 1
    assert "已 /deepen" in error_events[0].content or "已建模" in error_events[0].content
    assert "/new" in error_events[0].content
```

**Step 2: Run** — PASS (Task 9 impl 已 cover)

**Step 5: Commit**

```bash
git add tests/test_chat_slash_deepen.py
git commit -m "$(cat <<'EOF'
chat/slash · Phase 18 Task 13: /deepen 在 ChatSession 内拒绝 + 提示 /new

ChatSession (已 promote) 内 /deepen → err_deepen_already_promoted +
显示当前 modeling question + 提示 /new.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: promote 失败保留 ephemeral

**Files:** Modify `tests/test_chat_slash_deepen.py`

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_deepen_promote_failure_keeps_ephemeral(tmp_path, monkeypatch):
    from explain_engine.llm.errors import LLMError

    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    ephemeral = EphemeralChatSession(storage=storage, llm=MagicMock())
    ephemeral.promote_to_persistent = AsyncMock(side_effect=LLMError("classify failed"))

    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, ["为什么"])

    error_events = [e for e in events if e.type == "slash_error"]
    assert len(error_events) == 1
    assert "建模失败" in error_events[0].content or "LLMError" in error_events[0].content
    # No slash_deepen_promoted event
    promoted_events = [e for e in events if e.type == "slash_deepen_promoted"]
    assert len(promoted_events) == 0
```

**Step 2: Run** — PASS

**Step 5: Commit**

```bash
git add tests/test_chat_slash_deepen.py
git commit -m "$(cat <<'EOF'
chat/slash · Phase 18 Task 14: /deepen promote 失败保留 ephemeral

LLM raise → slash_error event, ephemeral 状态保留 (REPL outer loop 不切).
跟 repl_entry 现 try/except 模式一致.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Wave 2 全量 pytest + ruff 验证

```bash
.venv/bin/python -m pytest tests/test_ephemeral_chat_yaml.py tests/test_ephemeral_send_user_message.py tests/test_chat_slash_deepen.py tests/test_repl_deepen_flow.py tests/test_chat_copy.py -xvs
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```

Expected: 全 PASS, ruff 0. 若有 brittle test (e.g. `test_chat_slash_commands.py::test_total_count_is_23` 现 23 → 24 因为加 /deepen), 修.

**Commit (若有 brittle test fix)**:

```bash
git commit -m "$(cat <<'EOF'
test_chat_slash_commands · Phase 18 Wave 2 follow-up: count 23 → 24

/deepen 加入 DEFAULT_COMMANDS, total_count_is_23 → _24.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave 3 — REPL outer loop 改 (ephemeral 自然语言不 promote)

### Task 16: repl_entry ephemeral 自然语言 → send_user_message (替换 promote)

**Files:**
- Modify: `src/explain_engine/chat/repl_entry.py:158-180` (主改)
- Test: `tests/test_repl_entry_ephemeral_chat.py` (新建)

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_ephemeral_natural_language_no_longer_promotes(tmp_path, monkeypatch):
    """ephemeral 状态下 user 输入自然语言 → send_user_message, NOT promote_to_persistent."""
    # 较复杂 — monkeypatch repl loop, mock send_user_message + promote_to_persistent
    # assert send_user_message 被调用, promote_to_persistent 0 次
    ...
```

**Step 2-4: Implementation**

`repl_entry.py:158-166` 现:
```python
if isinstance(chat, EphemeralChatSession):
    if llm is None:
        console.print("[red]LLM 未配置...[/red]")
        continue
    try:
        chat = await chat.promote_to_persistent(text, llm)
    except Exception as exc:
        ...
```

改为:
```python
if isinstance(chat, EphemeralChatSession):
    if llm is None:
        console.print("[red]LLM 未配置, 无法 chat. 设 LLM_* env 后重启.[/red]")
        continue
    # Phase 18: ephemeral 状态 user 输入自然语言 → send_user_message (system-1 chat).
    # promote 由 /deepen slash 显式触发.
    async for ev in chat.send_user_message(text, llm):
        _render_event(console, ev)
    continue
```

注: `_render_event` 已 import 自 cli.py 或 repl_entry.py 自己有.

**Step 5: Commit**

```bash
git add src/explain_engine/chat/repl_entry.py tests/test_repl_entry_ephemeral_chat.py
git commit -m "$(cat <<'EOF'
chat/repl_entry · Phase 18 Task 16: ephemeral 自然语言改 send_user_message

repl_entry outer loop ephemeral 分支: 自然语言 → chat.send_user_message
(system-1 LLM chat), 不再 auto promote_to_persistent. promote 仅由 /deepen 触发.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: repl_entry 处理 slash_deepen_promoted event 切 ChatSession

**Files:** Modify `tests/test_repl_entry_ephemeral_chat.py`

**Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_repl_deepen_triggers_chat_var_switch(tmp_path, monkeypatch):
    """ephemeral 收 slash_deepen_promoted event 后, chat var 变 ChatSession."""
    ...
```

**Step 2-4: Implementation**

`repl_entry.py` 现已 handle `slash_reset_to_ephemeral` (line ~132), 加 handler for `slash_deepen_promoted`:

```python
async for ev in dispatch_slash(chat, text):
    _render_event(console, ev)
    if ev.type == "slash_quit":
        quit_requested = True
    elif ev.type == "slash_reset_to_ephemeral":
        ...
    elif ev.type == "slash_deepen_promoted":
        # Phase 18: /deepen 成功 → outer loop 替换 chat var.
        # promote_to_persistent 已经在 _handle_deepen 内调过, sid 在 ev.metadata.
        from explain_engine.chat.session import ChatSession
        new_sid = ev.metadata["sid"]
        await chat.aclose() if hasattr(chat, "aclose") else None
        chat = ChatSession(new_sid, llm=llm)
        chat.input_provider = ephemeral_input_provider  # 维持 readline
```

注: `aclose` 通常 ephemeral 不实现 (无资源), ChatSession 才有. 但 ephemeral close 应 noop.

**Step 5: Commit**

```bash
git add src/explain_engine/chat/repl_entry.py tests/test_repl_entry_ephemeral_chat.py
git commit -m "$(cat <<'EOF'
chat/repl_entry · Phase 18 Task 17: slash_deepen_promoted event 触发 chat var 切换

/deepen 成功 → repl outer loop 接 slash_deepen_promoted event, 用 ev.metadata.sid
建新 ChatSession 替换 chat var. 类似现 slash_switch_session 处理.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 18: 现有 chat REPL 集成 test 修补

**Files:**
- Modify: 现有 `tests/test_chat_*.py` 任何假设 "user 输入自然语言 = promote" 的 test

**Step 1:** 全量 pytest:

```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | grep -E "FAIL|ERROR" | head -20
```

记录 fail test, 逐个分析:
- 多数 chat test 是 mock LLM 测 ChatSession (已 promote) — 不受影响
- 少数 ephemeral test (e.g. `test_repl_entry.py`) 可能假设自然语言 → promote — 需改为"用 /deepen 触发"
- 估 ~3-5 个 test 需修

**Step 2-4:** 修每个 fail test, 把它的"输自然语言" 改成 "输 /deepen <Q>" 或显式调 promote.

**Step 5: Commit** (1 commit 收集所有 test 修补):

```bash
git add tests/test_chat_*.py
git commit -m "$(cat <<'EOF'
tests/test_chat_* · Phase 18 Task 18: 现有 chat test 修补 (ephemeral chat 不再 auto-promote)

Phase 18 起 ephemeral 自然语言走 send_user_message (system-1 chat), 不
auto-promote. 现有假设 "自然语言 = promote" 的 N 个 test 改为显式 /deepen.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 19: 集成验证: 完整 chat 然后 /deepen flow

**Files:** New `tests/test_phase18_full_flow.py`

**Step 1: Write integration test**

```python
@pytest.mark.asyncio
async def test_full_flow_ephemeral_chat_then_deepen(tmp_path, monkeypatch):
    """Phase 18 end-to-end: chat 2 轮 → /deepen 不带参 → promote → 后续 /compress 可用."""
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test")

    storage = StorageV2()
    llm = AsyncMock()
    # Mock: 2 轮 chat response + 1 次 bootstrap 出 8 现象
    llm.chat.side_effect = [
        MagicMock(text="烧水沸是蒸汽压等于大气压"),  # chat 1
        MagicMock(text="更详细的解释..."),  # chat 2 (if needed)
        # 后续 bootstrap_phenomena 走 /deepen 内 promote
        MagicMock(parsed={"type": "mechanism"}),  # classify
        MagicMock(parsed={"phenomena": [{"name": f"p{i}", "description": "d"} for i in range(8)]}),
    ]

    ephemeral = EphemeralChatSession(storage=storage, llm=llm)

    # Chat 1
    async for _ in ephemeral.send_user_message("为什么烧水能沸", llm):
        pass
    assert len(ephemeral.transcript) == 2

    # /deepen 不带参
    from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
    cmd = next(c for c in DEFAULT_COMMANDS if c.name == "deepen")
    events = await cmd.handler(ephemeral, [])

    promoted_events = [e for e in events if e.type == "slash_deepen_promoted"]
    assert len(promoted_events) == 1
    # sid 已 valid format
    assert promoted_events[0].metadata["sid"].startswith("s_")

    # 现 promote 后 ChatSession 可加载, state 含 8 phenomena
    from explain_engine.persistence.session import SessionStore
    sess = SessionStore().load(promoted_events[0].metadata["sid"])
    assert len(sess.state.graph.nodes) == 8
```

**Step 2-4: Run + verify**

**Step 5: Commit**

```bash
git add tests/test_phase18_full_flow.py
git commit -m "$(cat <<'EOF'
tests · Phase 18 Task 19: 集成验 — ephemeral chat → /deepen → promote → 8 phenomena

end-to-end: 启动 → send_user_message 2 轮 → /deepen 不带参取 transcript 末
user msg → promote 成功 → 持久 session 含 8 phenomena.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 20: Wave 3 全量 pytest + ruff 验证

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```

Expected: 全 PASS (允许 preexisting PG flake), ruff 0.

**Commit (若有 fix)**:

---

## Wave 4 — 收口

### Task 21: 全量 pytest 零回归

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: ~1287 + Phase 18 新 ~25-30 test = ~1315 PASS. 1 PG flake 仍存. 0 真 fail.

---

### Task 22: ruff check + auto-fix 任何 import order / UP017

```bash
.venv/bin/ruff check src/ tests/ --fix
```

Expected: 0 报错. 若 --fix 改了文件, commit.

---

### Task 23: 3 smoke 验证

#### Smoke 1: ephemeral chat 不再 auto-promote
```bash
uv run explain
> 为什么烧水能沸
# 期望: LLM 直接 chat 答 (热力学第一定律 / 蒸汽压 = 大气压 etc.).
# 期望: 不进 bootstrap, 不进 bootstrap_pending stage. /list 不应见新 session.
```

#### Smoke 2: /deepen 触发 promote
```bash
> /deepen 为什么烧水能沸
# 期望: 调 classify (qtype=mechanism 或 concept_explanation) → bootstrap → bootstrap_pending stage.
# /list 应见新 session.
```

#### Smoke 3: /deepen 不带参取最近 user msg
```bash
> /new  # 回 ephemeral
> 为什么彩虹七色  # ephemeral chat
> (LLM 答)
> /deepen
# 期望: 取 "为什么彩虹七色" 作 question → promote (qtype=phenomenon)
```

#### Smoke 4: ChatSession 内 /deepen 拒绝
```bash
> /deepen 别的问题
# 期望: "本 session 已 /deepen 过 (建模主题: 为什么彩虹七色). 想换主题请用 /new 开新 session."
```

记录: 实际 LLM 调用次数 / 各 smoke 是否符预期 / 任何意外 behavior.

若 smoke 有 fail, 修 + 单 task commit.

---

### Task 24: Acceptance doc + Phase 18 收口

**Files:** Create `docs/plans/2026-05-26-phase-18-deepen-acceptance.md`

内容:
- Phase 18 总结 (Hybrid 方案落地, /deepen slash 显式触发)
- 改动文件清单 (src + tests + prompts + docs)
- 测试统计 (Wave 1-3 新加 ~25-30 test, 全量 PASS 数)
- Bug fix 验证 (4 smoke 实测输出)
- backward compat: cli `explain new` 不变, Phase 17.2 classify + light_llm + delete 全保留
- 已知 follow-up:
  - transcript promote 时不带入 persistent (Phase 19 视情况做)
  - LLM 自决 tool 化 (方案 B, 长期 vision)
  - /deepen 不带参取"最近 user msg" 简化 — 未来可识别"为什么"模式
  - ephemeral chat 用主 LLM (无 light_llm) — 简单问题用主 LLM 较贵, Phase 19 可考虑 chat-tier light

```bash
git add docs/plans/2026-05-26-phase-18-deepen-acceptance.md
git commit -m "$(cat <<'EOF'
docs/plans · Phase 18 Task 24: acceptance doc + 收口

Hybrid /deepen 落地 (4 wave / ~24 task), 修复"机械化 bootstrap"过度建模问题.
ephemeral chat 加 send_user_message 能 system-1 LLM 直答, /deepen 显式
escalate 触发现 bootstrap pipeline. Phase 17.2 全保留, cli `explain new` 不变.

4 smoke 全过. 全量 ~1315 PASS, preexisting PG flake 1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Summary

- **24 task / 4 wave** (Wave 1 ephemeral chat 能力 / Wave 2 /deepen slash / Wave 3 REPL outer loop / Wave 4 收口)
- 每 task 严格 red → green → commit
- 中文 commit msg, `<scope> · Phase 18 Task N: <action>` 格式
- backward compat: cli `explain new <Q>` 完全不变, Phase 17.2 (classify + light_llm + delete) 全保留, lexicon / PG 不动
- 用户体验改: chat REPL 启动后 LLM 直接答简单问题, user 显式 /deepen 触发深度 reasoning
- 已知 trade-off (Phase 19 follow-up):
  - transcript 不带入 persistent session
  - ephemeral chat 用主 LLM 全价
  - /deepen 不带参取最近 user msg 不识别"为什么"模式
