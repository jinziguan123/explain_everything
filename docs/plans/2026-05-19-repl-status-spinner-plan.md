# REPL Status Spinner Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rich `console.status()` ctx manager 包 6 个 LLM hot path, 让用户在 LLM 调用 5-15s 期间看 spinner 知系统在跑.

**Architecture:** 改 2 文件 (`chat/ephemeral.py` + `chat/slash_commands.py`), 6 处 `with console.status(...): await llm_call(...)` wrap, 1 commit.

**Tech Stack:** Rich 13.x `console.status()` (已 dep), pytest baseline verify.

**Setup pre-flight:**
- 分支: `dev` (HEAD `7ed212f` — design doc 已 commit)
- 全测基线: 806 PASS
- Design 参考: [docs/plans/2026-05-19-repl-status-spinner-design.md](2026-05-19-repl-status-spinner-design.md)

---

## Task 1: 加 Rich Status spinner 包 6 个 LLM hot path

**Files:**
- Modify: `src/explain_engine/chat/ephemeral.py` (1 处: promote_to_persistent 内 bootstrap_phenomena)
- Modify: `src/explain_engine/chat/slash_commands.py` (5 处: compress / predict / counterfactual / run / rescore handler)
- 不加新 test (Rich Status 是 cosmetic, 无法 reliably 在 test 验 spinner; 跑 806 baseline 确保 wrap 不破返回值)

### Step 1.1: Verify Rich Status API

Run: `.venv/bin/python -c "from rich.console import Console; c = Console(); import inspect; print(inspect.signature(c.status))"`

Expected: 类似 `(status, *, spinner='dots', spinner_style='status.spinner', speed=1.0, refresh_per_second=12.5)`. 确认 `with console.status(text):` ctx manager 可用.

### Step 1.2: 改 ephemeral.py `promote_to_persistent`

读 `src/explain_engine/chat/ephemeral.py` 现 promote_to_persistent (约 L84-128).

找 `phenomena = await bootstrap_phenomena(question, llm, lexicon=lexicon, lexicon_top_k=20)` 调用. 包 status:

```python
# Phase 11 polish (2026-05-19): Rich Status spinner LLM 调用期间反馈
with console.status("[bold green]调 LLM 生现象...[/bold green]"):
    phenomena = await bootstrap_phenomena(
        question, llm,
        lexicon=lexicon, lexicon_top_k=20,
    )
```

确认 `console` 是 module-level / 已 import (ephemeral.py 顶 已 `from rich.console import Console` + 局部 `console = Console()` Wave 1). 若无, 加.

### Step 1.3: 改 slash_commands.py — `_handle_compress`

读 `_handle_compress` (约 L484-539). 找 `await propose_candidates(chat.state, chat.llm, ...)` 包 status:

```python
console = Console()  # 已在 handler 内 local (Wave 3 pattern)

with console.status("[bold green]调 LLM 提候选 (compress)...[/bold green]"):
    await propose_candidates(chat.state, chat.llm, min_count=3, max_count=5)
```

**注意**: `review_insights_async` (HITL k/e/d) 不包 — 它 wait user input, spinner 会撞 prompt.

`flush_to_lexicon` 含 `_build_canonical_mechanism` 多 LLM call. 也包 status (separate, 因 review_insights 期间不 spinner):

```python
with console.status("[bold green]写入 lexicon (LLM 生 canonical mechanism)...[/bold green]"):
    n = await flush_to_lexicon(chat._session, chat.storage, llm=chat.llm)
```

### Step 1.4: 改 slash_commands.py — `_handle_predict`

读 `_handle_predict` (约 L623-679). 找 `report = await predict(chat.state, intervention, chat.llm)` 包:

```python
with console.status("[bold green]调 LLM 跑 prediction...[/bold green]"):
    report = await predict(chat.state, intervention, chat.llm)
```

### Step 1.5: 改 slash_commands.py — `_handle_counterfactual`

读 `_handle_counterfactual` (约 L682-746). 找 `report = await substitute(chat.state, intervention, chat.llm)` 包:

```python
with console.status("[bold green]调 LLM 跑 counterfactual...[/bold green]"):
    report = await substitute(chat.state, intervention, chat.llm)
```

### Step 1.6: 改 slash_commands.py — `_handle_run`

读 `_handle_run` (约 L542-580). 找 `report = await run(chat.state, chat.llm, budget=..., ...)` 包:

```python
with console.status("[bold green]调 LLM 跑 reasoning loop...[/bold green]"):
    report = await run(
        chat.state, chat.llm,
        budget=max(chat.state.budget_remaining, 1),
    )
```

### Step 1.7: 改 slash_commands.py — `_handle_rescore`

读 `_handle_rescore` (约 L749-787). 找 `await rescore_session(chat.state, chat.llm)` 包:

```python
with console.status("[bold green]调 LLM 重评 edge confidence...[/bold green]"):
    await rescore_session(chat.state, chat.llm)
```

### Step 1.8: 全测 baseline verify

Run: `.venv/bin/python -m pytest -x --tb=no -q 2>&1 | tail -3`
Expected: 806 PASS (无新 test, wrap 不影响返回值).

若有 FAIL: 看具体 — 可能 mock LLM 路径 + status ctx manager 交互. Rich Status 在 no-tty 自动静默, 应不影响.

### Step 1.9: Lint

Run: `.venv/bin/ruff check src/explain_engine/chat/ephemeral.py src/explain_engine/chat/slash_commands.py`
Expected: 0.

### Step 1.10: Commit

```bash
git add src/explain_engine/chat/ephemeral.py src/explain_engine/chat/slash_commands.py
# 不 stage backup deletes / .env.bak

git commit -m "$(cat <<'EOF'
chat/repl · 加 Rich Status spinner 反馈 LLM 调用 (2026-05-19 polish)

用户反馈: REPL 内 LLM 调用 (5-15s) 期间终端静默, 看似卡死.

加 6 处 `with console.status(...):` ctx manager 包 LLM hot path:
- ephemeral.promote_to_persistent (bootstrap_phenomena)
- /compress (propose_candidates + flush_to_lexicon 各 1 spinner)
- /predict (prediction.predict)
- /counterfactual (counterfactual.substitute)
- /run (runtime.run)
- /rescore (rescore_session)

立即返 slash (/help /list /show 等) 不加. HITL k/e/d 期间不加 (prompt 已
显式 wait user input, spinner 会撞).

Rich Status spinner ⠋ ⠙ ⠹ 自动渲染, ANSI 友好, no-tty (CliRunner)
自动静默 fallback 不破 test.

806 PASS, ruff 0 (无新 test, 仅 cosmetic wrap, 跑 baseline 验返回值不变).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Acceptance

- [ ] 6 hot path 都包 status ctx
- [ ] 806 PASS (无 regression)
- [ ] ruff 0
- [ ] 1 commit, 2 文件改

## Manual smoke (user 跑真终端)

1. `explain` 进 REPL
2. 输 "为什么 X" → 看 spinner "调 LLM 生现象..." 5-15s → 进 HITL k/e/d (无 spinner)
3. 真 chat 内输 `/compress` → 看 spinner "调 LLM 提候选..." → HITL → spinner "写入 lexicon..."
4. 输 `/predict` → 输 intervention → spinner
5. /quit

---

## 参考

- Design: [2026-05-19-repl-status-spinner-design.md](2026-05-19-repl-status-spinner-design.md)
- Rich Status docs: https://rich.readthedocs.io/en/stable/live.html#displaying-a-status
