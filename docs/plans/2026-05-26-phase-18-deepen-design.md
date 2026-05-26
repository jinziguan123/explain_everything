# Phase 18 Design — `/deepen` Hybrid 化 (LLM 默 system-1 + slash escalate pipeline)

**Date**: 2026-05-26
**Status**: design approved, ready for plan
**Trigger insight**: 用户提出 "现象生成对某些问题是浪费" — 烧水原理这种 LLM 已知答案的问题, 强制 bootstrap 凑 "气泡膨胀" 之类废现象, 是机械化的过度建模.

## Root cause (架构层)

Phase 17.2 落地后, chat REPL 的流程仍是**强制 bootstrap**:
- `EphemeralChatSession` 启动 → user 输入自然语言 → `repl_entry.py:166` 自动 `promote_to_persistent` → classify + bootstrap_phenomena → bootstrap_pending stage → HITL 1 → user 接着用 /compress /run /predict ...
- 整个 pipeline 假设 "每个 user question 都需要从零 reasoning". 但很多问题 (热力学/常识/已有共识) LLM 直接知道答案, 跑 pipeline 反而 hallucinate.

## 修复哲学

把"是否走 reasoning pipeline"的决策从**系统机械化**改成**用户显式触发** (Hybrid 方案):

- 默 (system-1): user 输入自然语言 → LLM 直接 chat 回答 (用主 LLM, 无 phenomena 无 state)
- escalate (system-2): user 觉得回答不够深 / 想多维度建模 → slash `/deepen [question]` → 触发现 bootstrap pipeline

key design: **EphemeralChatSession 在 ephemeral 状态下也能跟 LLM chat**, 不是只在 promote 时一次性引入 LLM.

设计决策 (来自 brainstorm 4 轮 AskUserQuestion):
- D1: actor = LLM 默 system-1 + 用户显式 slash escalate (Hybrid)
- D2: slash 名 = `/deepen` (比 `/bootstrap` 对 user 友好, bootstrap 是内部术语)
- D3: question 来源 = 带参用参 + 不带参 fallback 取 transcript 末最近 user msg
- D4: 一 session 一 /deepen, 第二次拒绝 + 提示 `/new` (与 cli `explain new` 一问一 session 一致)

---

## Architecture

### 现 (Phase 17.2)
```
chat REPL 启动 → EphemeralChatSession (空 state, 无 LLM 能力)
user 输入自然语言 → repl_entry.py:166 auto promote_to_persistent(text, llm)
                  → classify + bootstrap_phenomena
                  → bootstrap_pending stage
                  → HITL 1 (用户审 phenomena)
                  → done → user /compress /run /predict ...
```

### Phase 18 后
```
chat REPL 启动 → EphemeralChatSession (空 state, 已有 in-memory chat 能力)
user 输入自然语言 → ephemeral.send_user_message(text, llm)
                  → LLM.chat() 直接 yield text events
                  → transcript append user/assistant msg (in-memory, 不持久)

user 显式 /deepen [question]
   带参 → 用参 Q
   不带参 → 取 transcript 末最近一句 user msg 作 Q (transcript 空 → slash_error)
   ↓
   promote_to_persistent(Q, llm)
   ↓
   classify + bootstrap_phenomena → bootstrap_pending → HITL → 后续 /compress /run ... 不变

ChatSession (已 promote) 内再 /deepen → 拒绝 + 提示 /new
```

cli `explain new <question>` 保留 (batch mode 强制 bootstrap, user 主动调命令时认为他就想 reasoning, 跟 chat REPL 不同 entry, 不变).

---

## 改动面 (估算)

| 区域 | 改动 | 估算行 |
|---|---|---|
| `chat/ephemeral.py` `send_user_message(text, llm)` 新增 | 调 LLM.chat() with system prompt → yield text/turn_complete events | ~50 |
| `chat/ephemeral.py` transcript append 逻辑 | in-memory, 不持久 | ~10 |
| `llm/prompts/ephemeral_chat.yaml` 新建 | 中文 system prompt, 引导 user 用 /deepen escalate | ~30 |
| `chat/repl_entry.py:158-166` 改 | ephemeral 自然语言不再 auto-promote, 改调 send_user_message + render events | ~10 |
| `chat/slash_commands.py` `_handle_deepen` 新增 | 取 question (带参 / transcript fallback) → promote_to_persistent | ~30 |
| `chat/slash_commands.py` DEFAULT_COMMANDS 注册 `/deepen` | tuple 加 entry | ~5 |
| `chat/chat_copy.py` zh strings | 用法 / 取消 / err_deepen_already_promoted 等 | ~10 |
| `chat/session.py` ChatSession reject 再 /deepen | persistent session 内 /deepen → err | ~5 |
| 测试 | ephemeral.send_user_message + /deepen 各 path | ~15 个新 test (~200 行) |

**总改动**: ~150 行 src + ~200 行 test. 跟 Phase 17.2 Feature C (session 删除) 同量级.

---

## 关键 design 决策

### Ephemeral chat 的 LLM 调用

- 用**主 LLM** (`llm`), **不**用 light_llm — chat 回答需要 quality, 跟 classify 这种简单任务不同
- 走 `LLMClient.chat()` 直接返 text (无 structured output, 无 tool_use)
- yield event 类型复用 `ChatSession`: `assistant_text` + `turn_complete`
- REPL `_render_event` (cli.py:915) 已支持这些 event 类型, 复用 0 改动

### Ephemeral system prompt 要点

```yaml
# llm/prompts/ephemeral_chat.yaml
system: |
  你是 explain-engine 的 chat assistant. 用户在 ephemeral 模式下跟你对话, 你尚未启动深度建模 pipeline.

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
```

### `/deepen` question 取位算法

```python
def get_deepen_question(args: list[str], transcript: list[dict]) -> str | None:
    """取 /deepen 的 question.

    带参 → 拼 args 作 question.
    不带参 → 倒序遍历 transcript, 取第一条 role='user' 的 content.
    transcript 空 → None (caller raise slash_error 提示用法).
    """
    if args:
        return " ".join(args)
    for msg in reversed(transcript):
        if msg.get("role") == "user":
            return msg["content"]
    return None
```

简化: **不识别 "为什么" 模式**, 取最近 user msg 即可 — user 能掌控自己说什么.

### Transcript 不带入 persistent

Ephemeral transcript 是 in-memory list of dict, **不持久, promote 时不传给 ChatSession**.

trade-off:
- ✅ Promote 后 persistent session 起点干净, phenomena 是 reasoning 起点
- ❌ User 可能感觉 chat 历史"丢了"
- 备选 (Phase 19 follow-up): promote 时把 ephemeral transcript 转入 persistent ChatSession.transcript, storage_v2 落 transcript.jsonl. Phase 18 暂不做.

### 一 session 一 /deepen (D4 决策)

- 第一次 /deepen → ephemeral → promote_to_persistent → ChatSession (persistent)
- 第二次 /deepen (在 persistent ChatSession 内) → 拒绝, 提示 `/new` 开新 ephemeral session

实现:
- ChatSession 的 `_handle_deepen` 走 `err_deepen_already_promoted` 路径, 类似现 `_ephemeral_reject` 反对称
- 提示文案: "本 session 已经 /deepen 过 (建模主题: {question}), 想换主题请用 /new 开新 session"

---

## 替代方案 (落选)

**B (LLM 自决 tool)**: chat 启动后 LLM 自由决定何时调 `bootstrap_tool`. tool-use 全套.
- ❌ 太大 (改 chat tool dispatch 机制, session lifecycle 重写, system prompt 重写)
- ❌ LLM 可能过度跳 pipeline (古典/复杂问题也 system-1 答, 退化 ChatGPT)
- 留 Phase 19+

**C (classify 加 5th common_knowledge)**: Phase 17.2 classify 加新类, 该类 skip bootstrap.
- ❌ 跟现 4 类 (现象配比) 维度不同, classify schema 混乱
- ❌ 启发式在边界 case 不准 (烧水 vs Raft 重提 都可能误判)
- ❌ user 失去 control

**A (用户显式 /bootstrap 完全无 chat)**: ephemeral 下自然语言 reject, 必须先 /bootstrap.
- ❌ user 失去 system-1 chat 能力
- ❌ UX 倒退 (跟 cli `explain new` 同 batch 模式, REPL 价值减少)

---

## Error handling

- `send_user_message` LLM 失败 (LLMError / SchemaValidationError) → yield error event + 不修改 transcript (保持 retry 友好)
- `/deepen <Q>` promote 失败 (现有 promote_to_persistent 已 raise) → caller 抓 + 保留 ephemeral (跟现 `repl_entry.py:170-174` 同 pattern, 不变)
- `/deepen` 不带参 + transcript 空 → slash_error "用法: /deepen <问题>"
- `/deepen` 在 ChatSession (已 promote) 内 → slash_error "本 session 已建模, /new 开新 session"

---

## Testing

### EphemeralChatSession chat 能力
- `test_ephemeral_send_user_message_basic`: mock llm.chat, 验 yield assistant_text + turn_complete
- `test_ephemeral_transcript_append_after_chat`: chat 后 transcript 含 user + assistant 各 1 条
- `test_ephemeral_transcript_not_persisted`: 看 storage_v2 是否未写 transcript.jsonl (ephemeral 下应不写)
- `test_ephemeral_send_user_message_llm_error`: LLM raise → yield error event, transcript 不变

### /deepen slash
- `test_deepen_with_explicit_question`: `/deepen 为什么 X` → 调 promote_to_persistent 用 "为什么 X"
- `test_deepen_without_args_uses_last_user_msg`: ephemeral chat 完, /deepen 无参 → 取 transcript 末 user msg
- `test_deepen_empty_transcript_no_args`: 启动后立即 /deepen 无参 → slash_error "用法: ..."
- `test_deepen_in_persistent_session_rejected`: 已 promote 的 ChatSession 内 /deepen → err_deepen_already_promoted
- `test_deepen_registered_in_default_commands`: DEFAULT_COMMANDS 含 name='deepen'
- `test_deepen_promote_failure_keeps_ephemeral`: promote raise → ephemeral 状态保留

### REPL outer loop
- `test_repl_ephemeral_natural_language_no_longer_promotes`: ephemeral 下自然语言 → send_user_message (不再 auto promote)
- `test_repl_deepen_triggers_promote_and_switches_to_chat_session`: /deepen → repl 切到 ChatSession

### 集成
- `test_full_flow_ephemeral_chat_then_deepen`: 进 chat → user 问问题 → LLM chat 答 → user /deepen → promote → bootstrap_pending → /compress 等可用

### 零回归
- 现 `tests/test_chat_repl.py` 等 ~50 个 chat REPL test: 多数测的是 promote 后行为, 不该 break. 但少数 test 假设 "user 输入自然语言 = promote" 需要改 — 这些改成 "/deepen 触发 promote" 用法.

---

## 实施顺序

| Wave | Scope | 估 task | 估 commit |
|---|---|---|---|
| Wave 1 | EphemeralChatSession.send_user_message + ephemeral_chat.yaml (基础 chat 能力) | ~8 | ~8 |
| Wave 2 | `/deepen` slash + chat_copy + DEFAULT_COMMANDS 注册 + ChatSession 拒绝 | ~10 | ~10 |
| Wave 3 | repl_entry 改 (ephemeral 自然语言不 promote) + 现有 chat test 修补 | ~10 | ~10 |
| Wave 4 | 全量 pytest 零回归 + ruff + 3 smoke (chat / deepen / 拒绝再 deepen) + acceptance doc | ~5 | ~5 |

总 ~33 task / ~33 commit. 跟 Phase 17.2 同量级.

---

## Smoke 验收 (Phase 18 完后)

```bash
uv run explain
> 为什么烧水能沸
# 期望: LLM 直接 chat 答 (热力学第一定律 / 蒸汽压 = 大气压 etc.), 不 bootstrap, 不 promote.

> 嗯, 能再深入吗 (LLM chat 继续答)
> /deepen 为什么烧水能沸
# 期望: 调 promote_to_persistent → classify (qtype=concept_explanation 或 mechanism) → bootstrap → bootstrap_pending → HITL 1

> /list
# 期望: 现 session 出现 in list

> /new
# 期望: 切回 EphemeralChatSession (清屏 + banner)

> 为什么年轻人不消费 (ephemeral chat)
> /deepen
# 不带参, 取 transcript 末 = "为什么年轻人不消费" → promote (qtype=causal_modern)

> /deepen
# 第二次 /deepen 在 persistent session 内 → 拒绝 "已建模 /new"

uv run explain delete s_xxx --force  # Phase 17.2 验证仍 work, 无回归
```

---

## 已知不破坏的状态

- Phase 17.2 全 wave 落地, 1287 PASS / 1 preexisting PG flake — Phase 18 同基线
- `bootstrap_phenomena` 不动 (Phase 17.2 落的 classify + 4 yaml dispatch 全保留, /deepen 内部继续走它)
- `make_light_llm_client` 不动, ephemeral chat 不用 light_llm (主 LLM)
- lexicon / theories / PG schema 不动
- cli `explain new <Q>` 不变 (batch 强制 bootstrap)
- 现 slash 命令对 ephemeral 拒绝逻辑 (`/compress /run /predict 等`) 不变, 只新增 `/deepen` 允许在 ephemeral
