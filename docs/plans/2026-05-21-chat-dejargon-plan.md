# Chat REPL De-jargon (Phase 15) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Chat REPL 内所有 user-visible 文本中文化 + 去 jargon. 19 slash 命令名保留英文, 但 desc / status / 成功 / 错误 / hint 全统一引 `chat/chat_copy.py` 中文模板.

**Architecture:** 新 `src/explain_engine/chat/chat_copy.py` 单一文案 source — 含 TERMS_MAP (~35 术语) / COMMAND_DESCRIPTIONS (19 命令) / HELP_GROUPS_ZH (6 组) / RUNTIME_MESSAGES (STATUS_* / INFO_* / msg_*() / err_*()) / HINTS_BY_KEY (Phase 14 hint 去 jargon) / STOP_REASON_MAP / zh() helper. 5 src 文件引模板: `slash_commands.py` / `slash_stage_rules.py` / `cli.py` / `hitl/cli_interactive.py`. engine 内部代码 0 改动.

**Tech Stack:** Python 3.11 + Rich (REPL render), pytest + pytest-mock. `.venv/bin/python -m pytest` (uv-managed venv) + `.venv/bin/ruff check`.

**Design doc:** [docs/plans/2026-05-21-chat-dejargon-design.md](2026-05-21-chat-dejargon-design.md) — 读 §4 (Design) + §5 (影响 file 清单) 先.

---

## Task 1: TERMS_MAP + zh() helper

**Files:**
- Create: `src/explain_engine/chat/chat_copy.py`
- Test: `tests/test_chat_copy.py`

**Step 1: Write the failing test**

```python
# tests/test_chat_copy.py
"""Phase 15: chat_copy 中文文案 + 术语词典."""


class TestTermsMap:
    def test_critical_terms_present(self):
        """关键术语必须有映射 (L0/L1/L2 / stage 4 值 / graph / lexicon)."""
        from explain_engine.chat.chat_copy import TERMS_MAP
        required = {
            "L0", "L1", "L2",
            "bootstrap_pending", "insight_pending", "done", "converged",
            "graph", "edge", "lexicon",
        }
        assert required.issubset(set(TERMS_MAP.keys()))

    def test_l_levels_mapped_to_chinese_phrases(self):
        """L0/L1/L2 应映射成直观短语, 不含 'L'."""
        from explain_engine.chat.chat_copy import TERMS_MAP
        assert "L" not in TERMS_MAP["L0"]
        assert "L" not in TERMS_MAP["L1"]
        assert "L" not in TERMS_MAP["L2"]


class TestZhHelper:
    def test_zh_translates_known_term(self):
        from explain_engine.chat.chat_copy import zh
        assert zh("L0") == "现象"
        assert zh("bootstrap_pending") == "等待启动"

    def test_zh_fallback_returns_original(self):
        from explain_engine.chat.chat_copy import zh
        assert zh("nonexistent_term") == "nonexistent_term"
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_copy.py -v`
Expected: 4 FAIL with `ImportError: cannot import name 'TERMS_MAP'`

**Step 3: Implement TERMS_MAP + zh()**

```python
# src/explain_engine/chat/chat_copy.py
"""Phase 15: chat REPL user-facing 中文文案集中点.

所有 chat REPL 路径 (slash_commands / slash_stage_rules / cli._render_event /
hitl/cli_interactive) 引此 module 的常量 / 模板, 不再硬编中文/英文.
单一改文案 source — 改一处全场动.
"""

from __future__ import annotations


TERMS_MAP: dict[str, str] = {
    # ── Graph 节点层次 ──
    "L0": "现象",
    "L1": "归纳出的模式",
    "L2": "深层原因",
    "abstraction": "归纳",
    "driver": "深层原因",
    "observation": "现象",

    # ── Graph 结构 ──
    "graph": "因果图",
    "node": "节点",
    "edge": "因果关系",
    "weak_chain": "薄弱的因果链",
    "manifests_as": "体现为",
    "causes": "导致",

    # ── Runtime ops ──
    "propagation": "影响传导",
    "expansion": "扩展",
    "reflection": "回顾反思",
    "decay": "衰减",
    "reasoning loop": "推理循环",
    "tick": "推理步",

    # ── Cross-session ──
    "lexicon": "跨 session 概念库",
    "canonical_mechanism": "标准化的机制描述",
    "global_id": "全局 ID",

    # ── Multi-signal acceptance ──
    "multi-signal": "多信号",
    "consistency": "一致性",
    "essentialness": "本质重要性",
    "rollout_coverage": "覆盖率",
    "acceptance report": "接受度评估",

    # ── Stage (session.meta.stage 4 个值) ──
    "bootstrap_pending": "等待启动",
    "insight_pending":   "等待审查",
    "done":              "已归纳",
    "converged":         "已推理",

    # ── 引擎 ops ──
    "propose_candidates": "提候选",
    "score_all": "评分",
    "HITL": "人工审查",
    "persist": "存盘",
}


def zh(term: str) -> str:
    """把英文/技术词翻成中文直观短语. 找不到返原词 (defensive)."""
    return TERMS_MAP.get(term, term)
```

**Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_chat_copy.py -v`
Expected: 4 PASS

**Step 5: Commit**

```bash
git add src/explain_engine/chat/chat_copy.py tests/test_chat_copy.py
git commit -m "chat/chat_copy · Phase 15 Task 1: TERMS_MAP + zh() helper"
```

---

## Task 2: COMMAND_DESCRIPTIONS + HELP_GROUPS_ZH

**Files:**
- Modify: `src/explain_engine/chat/chat_copy.py`
- Test: `tests/test_chat_copy.py`

**Step 1: Write the failing test**

```python
# tests/test_chat_copy.py 末加
class TestCommandDescriptions:
    def test_all_19_commands_present(self):
        from explain_engine.chat.chat_copy import COMMAND_DESCRIPTIONS
        expected = {
            "compress", "run", "rescore", "predict", "counterfactual", "cf",
            "show", "graph", "check", "new", "resume", "list", "lexicon",
            "budget", "compact", "save", "migrate", "help", "quit",
        }
        assert expected.issubset(set(COMMAND_DESCRIPTIONS.keys()))

    def test_descriptions_under_50_chars(self):
        from explain_engine.chat.chat_copy import COMMAND_DESCRIPTIONS
        for cmd, desc in COMMAND_DESCRIPTIONS.items():
            assert len(desc) <= 50, f"/{cmd} desc 过长: '{desc}' ({len(desc)} 字)"

    def test_descriptions_contain_chinese(self):
        """每条 desc 含至少 1 中文字 (catch 漏译)."""
        import re
        from explain_engine.chat.chat_copy import COMMAND_DESCRIPTIONS
        chinese_pattern = re.compile(r'[一-鿿]')
        for cmd, desc in COMMAND_DESCRIPTIONS.items():
            assert chinese_pattern.search(desc), f"/{cmd} desc 无中文"


class TestHelpGroups:
    def test_help_groups_have_six_groups(self):
        from explain_engine.chat.chat_copy import HELP_GROUPS_ZH
        assert len(HELP_GROUPS_ZH) == 6

    def test_help_groups_cover_all_19_commands(self):
        from explain_engine.chat.chat_copy import HELP_GROUPS_ZH
        all_cmds_in_groups = set()
        for _, cmds in HELP_GROUPS_ZH:
            all_cmds_in_groups.update(cmds)
        # /cf 是 alias, 在 group 里不重复列
        expected_excl_cf = {
            "compress", "run", "rescore", "predict", "counterfactual",
            "show", "graph", "check", "new", "resume", "list", "lexicon",
            "budget", "compact", "save", "migrate", "help", "quit",
        }
        assert expected_excl_cf.issubset(all_cmds_in_groups)
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_copy.py -v`
Expected: 5 FAIL

**Step 3: Add COMMAND_DESCRIPTIONS + HELP_GROUPS_ZH to chat_copy.py**

(Append to chat_copy.py after TERMS_MAP — content per design §4.3)

**Step 4: Run test to verify it passes**

Expected: 9 PASS (4 from Task 1 + 5 new)

**Step 5: Commit**

```bash
git commit -m "chat/chat_copy · Phase 15 Task 2: COMMAND_DESCRIPTIONS + HELP_GROUPS_ZH"
```

---

## Task 3: STOP_REASON_MAP + msg_*() success templates

**Files:**
- Modify: `src/explain_engine/chat/chat_copy.py`
- Test: `tests/test_chat_copy.py`

**Step 1: Write the failing test**

```python
class TestStopReasonMap:
    def test_known_stop_reasons_translated(self):
        from explain_engine.chat.chat_copy import STOP_REASON_MAP
        assert "已收敛" in STOP_REASON_MAP.get("converged", "")
        assert "预算耗尽" in STOP_REASON_MAP.get("budget_exhausted", "")


class TestSuccessMessageTemplates:
    def test_msg_compress_done_contains_n_and_chinese(self):
        from explain_engine.chat.chat_copy import msg_compress_done
        msg = msg_compress_done(n_candidates=5, n_to_lexicon=3)
        assert "5" in msg
        assert "3" in msg
        assert "归纳" in msg or "模式" in msg
        assert "概念库" in msg

    def test_msg_run_done_translates_stop_reason(self):
        from explain_engine.chat.chat_copy import msg_run_done
        msg = msg_run_done(stop_reason="converged", tick=7)
        assert "已收敛" in msg
        assert "converged" not in msg
        assert "7" in msg

    def test_msg_rescore_done_includes_avg_and_count(self):
        from explain_engine.chat.chat_copy import msg_rescore_done
        msg = msg_rescore_done(n_edges=25, avg_conf=0.78)
        assert "25" in msg
        assert "0.78" in msg
        assert "重评" in msg
        assert "可信度" in msg
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_chat_copy.py -v`
Expected: 4 FAIL

**Step 3: Add STOP_REASON_MAP + msg_*() to chat_copy.py**

```python
STOP_REASON_MAP: dict[str, str] = {
    "budget_exhausted":     "预算耗尽",
    "converged":            "已收敛 (无更多可推进点)",
    "no_meaningful_action": "无更多可推进点",
    "max_ticks":            "达到最大推理步数",
}


def msg_compress_done(n_candidates: int, n_to_lexicon: int) -> str:
    return f"归纳完成: 加了 {n_candidates} 个模式, 其中 {n_to_lexicon} 个写入概念库."


def msg_run_done(stop_reason: str, tick: int) -> str:
    reason_zh = STOP_REASON_MAP.get(stop_reason, stop_reason)
    return f"推理完成: 在第 {tick} 步停止 (原因: {reason_zh})."


def msg_rescore_done(n_edges: int, avg_conf: float) -> str:
    return f"重评完成: {n_edges} 条因果关系, 平均可信度 {avg_conf:.2f}. 已存盘."


def msg_save_done(sid: str) -> str:
    return f"已存盘 session {sid} (因果图 + 对话状态 + 转录)."


def msg_resume_already(sid: str) -> str:
    return f"已在 session {sid}, 不切换."


def msg_resume_switching(sid: str) -> str:
    return f"切换到 session {sid}..."
```

**Note for implementer**: 实装前 grep `runtime/runtime.py` 确认 stop_reason 全 enum 是否还有其它值; 若有遗漏在 STOP_REASON_MAP 补:
```bash
grep -rn 'stop_reason' src/explain_engine/runtime/ | head -20
```

**Step 4: Run test to verify it passes**

Expected: 13 PASS

**Step 5: Commit**

```bash
git commit -m "chat/chat_copy · Phase 15 Task 3: STOP_REASON_MAP + msg_*() success templates"
```

---

## Task 4: err_*() error templates

**Files:**
- Modify: `src/explain_engine/chat/chat_copy.py`
- Test: `tests/test_chat_copy.py`

**Step 1: Write the failing test**

```python
class TestErrorTemplates:
    def test_err_failed_includes_exc_info(self):
        from explain_engine.chat.chat_copy import err_failed
        msg = err_failed("compress", ValueError("bad input"))
        assert "compress" in msg
        assert "失败" in msg
        assert "ValueError" in msg
        assert "bad input" in msg

    def test_err_no_llm_chinese(self):
        from explain_engine.chat.chat_copy import err_no_llm
        msg = err_no_llm("predict")
        assert "predict" in msg
        assert "LLM" in msg
        assert "没配置" in msg or "启动" in msg

    def test_err_ephemeral_reject_actionable(self):
        from explain_engine.chat.chat_copy import err_ephemeral_reject
        msg = err_ephemeral_reject("compress")
        assert "/resume" in msg or "新建" in msg or "建 session" in msg

    def test_err_stage_not_allowed_translates_stages(self):
        from explain_engine.chat.chat_copy import err_stage_not_allowed
        msg = err_stage_not_allowed("run", "bootstrap_pending", ["done"])
        assert "等待启动" in msg
        assert "已归纳" in msg
        assert "bootstrap_pending" not in msg
        assert "['done']" not in msg
```

**Step 2: Run test to verify it fails**

Expected: 4 FAIL

**Step 3: Add err_*() to chat_copy.py**

```python
def err_failed(cmd: str, exc: Exception) -> str:
    return f"/{cmd} 失败: {type(exc).__name__}: {exc}"


def err_no_llm(cmd: str) -> str:
    return f"/{cmd} 需要 LLM (启动时没配置)."


def err_ephemeral_reject(cmd: str) -> str:
    return (
        f"/{cmd} 需要先建 session — 输入一个问题让 chat 建 session, "
        f"或 /resume 选历史 session."
    )


def err_stage_not_allowed(cmd: str, current_stage: str, allowed: list[str]) -> str:
    current_zh = zh(current_stage)
    allowed_zh = " / ".join(zh(s) for s in allowed)
    return (
        f"/{cmd} 在当前阶段 ({current_zh}) 不能跑 — "
        f"需要阶段为: {allowed_zh}."
    )
```

**Step 4: Run test to verify it passes**

Expected: 17 PASS

**Step 5: Commit**

```bash
git commit -m "chat/chat_copy · Phase 15 Task 4: err_*() error templates"
```

---

## Task 5: HINTS_BY_KEY 去 jargon 重写

**Files:**
- Modify: `src/explain_engine/chat/chat_copy.py`
- Test: `tests/test_chat_copy.py`

**Step 1: Write the failing test**

```python
class TestHintsByKey:
    def test_all_six_keys_present(self):
        from explain_engine.chat.chat_copy import HINTS_BY_KEY
        expected = {
            "need_promote_first", "need_compress_first",
            "after_compress", "after_run",
            "after_inference", "after_rescore",
        }
        assert expected.issubset(HINTS_BY_KEY.keys())

    def test_after_compress_mentions_recommended_next_commands(self):
        from explain_engine.chat.chat_copy import HINTS_BY_KEY
        c = HINTS_BY_KEY["after_compress"]
        assert "/run" in c
        assert "/predict" in c
        assert "/counterfactual" in c
        # 关键: 无 reasoning loop / drivers / abstraction jargon
        assert "reasoning loop" not in c
        assert "drivers" not in c
        assert "abstraction" not in c

    def test_need_compress_first_uses_intuitive_chinese(self):
        from explain_engine.chat.chat_copy import HINTS_BY_KEY
        c = HINTS_BY_KEY["need_compress_first"]
        assert "/compress" in c
        assert "归纳" in c or "模式" in c
        assert "abstraction" not in c
```

**Step 2: Run test to verify it fails**

Expected: 3 FAIL

**Step 3: Add HINTS_BY_KEY to chat_copy.py**

```python
HINTS_BY_KEY: dict[str, str] = {
    "need_promote_first": (
        "session 还没启动 — 输入一个问题让 chat 建 session, 然后再 /compress."
    ),
    "need_compress_first": (
        "需要先 /compress 把现象归纳成模式, 才能跑这个命令."
    ),
    "after_compress": (
        "▸ 接下来可选:\n"
        "  /run — 自动推理找深层原因 (推荐)\n"
        "  /predict <假设> — 预测某干预的下游影响\n"
        "  /counterfactual <假设> — 反事实分析"
    ),
    "after_run": (
        "▸ session 已完整推理. 接下来可选:\n"
        "  /predict <假设> — 预测干预影响\n"
        "  /counterfactual <假设> — 反事实分析\n"
        "  /show — 看完整因果图"
    ),
    "after_inference": (
        "▸ 可继续 /predict 或 /counterfactual 探索, /show 看因果图更新."
    ),
    "after_rescore": (
        "▸ 因果关系可信度已重评. /show 看变化, /run 重跑推理."
    ),
}
```

**Step 4: Run test to verify it passes**

Expected: 20 PASS

**Step 5: Commit**

```bash
git commit -m "chat/chat_copy · Phase 15 Task 5: HINTS_BY_KEY 去 jargon 重写"
```

---

## Task 6: STATUS_* + INFO_* 常量

**Files:**
- Modify: `src/explain_engine/chat/chat_copy.py`
- Test: `tests/test_chat_copy.py`

**Step 1: Write the failing test**

```python
class TestStatusAndInfoConstants:
    def test_status_constants_have_rich_markup(self):
        from explain_engine.chat.chat_copy import (
            STATUS_COMPRESS_PROPOSE,
            STATUS_RUN,
            STATUS_LEXICON_FLUSH,
        )
        for s in (STATUS_COMPRESS_PROPOSE, STATUS_RUN, STATUS_LEXICON_FLUSH):
            assert "[bold green]" in s
            assert "[/bold green]" in s
            # 含中文
            import re
            assert re.search(r'[一-鿿]', s)

    def test_info_constants_dim_style(self):
        from explain_engine.chat.chat_copy import (
            INFO_INSIGHT_PENDING_RESUME,
            INFO_MID_STAGE_SAVED,
        )
        for s in (INFO_INSIGHT_PENDING_RESUME, INFO_MID_STAGE_SAVED):
            assert "[dim]" in s
            assert "[/dim]" in s
```

**Step 2: Run test to verify it fails**

Expected: 2 FAIL

**Step 3: Add STATUS_* + INFO_* to chat_copy.py**

```python
STATUS_COMPRESS_PROPOSE = "[bold green]正在归纳模式...[/bold green]"
STATUS_COMPRESS_SCORE   = "[bold green]正在评分候选模式...[/bold green]"
STATUS_LEXICON_FLUSH    = "[bold green]正在存盘到概念库...[/bold green]"
STATUS_RUN              = "[bold green]正在自动推理 (扩展 / 反思 / 衰减)...[/bold green]"
STATUS_PREDICT          = "[bold green]正在预测干预影响...[/bold green]"
STATUS_COUNTERFACTUAL   = "[bold green]正在做反事实分析...[/bold green]"
STATUS_RESCORE          = "[bold green]正在重评因果关系...[/bold green]"

INFO_INSIGHT_PENDING_RESUME = "[dim](检测到中途取消, 跳过 LLM 直接进入审查)[/dim]"
INFO_MID_STAGE_SAVED        = "[dim](中间状态已保存, 取消审查可下次重入跳过 LLM)[/dim]"
```

**Step 4: Run test to verify it passes**

Expected: 22 PASS

**Step 5: Commit**

```bash
git commit -m "chat/chat_copy · Phase 15 Task 6: STATUS_* + INFO_* 常量"
```

---

## Task 7: slash_stage_rules.py — HINTS_BY_KEY 改 import from chat_copy

**Files:**
- Modify: `src/explain_engine/chat/slash_stage_rules.py`
- Test: 既有 `tests/test_chat_slash_stage_rules.py`

**Step 1: Replace local HINTS_BY_KEY with import**

```python
# slash_stage_rules.py 顶部
from explain_engine.chat.chat_copy import HINTS_BY_KEY  # 单 source
# 删掉原本 local 定义的 HINTS_BY_KEY: dict[str, str] = {...} 整段
```

**Step 2: Run既有 hint test 看是否还 PASS**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_stage_rules.py -v`
Expected: 既有 hint content 测试可能 FAIL (因新 hint 文案换了关键词).

**Step 3: 修 既有 test assertion 为关键词匹配**

具体 grep `test_chat_slash_stage_rules.py::TestHintsByKey` 各 case, assertion 从硬编"reasoning loop"等改成新关键词如"推理"/"深层原因"/"归纳" etc.

**Step 4: Run again**

Expected: PASS

**Step 5: Commit**

```bash
git commit -m "chat/slash_stage_rules · Phase 15 Task 7: HINTS_BY_KEY import from chat_copy"
```

---

## Task 8: slash_stage_rules.py with_stage_gate 用 err_stage_not_allowed

**Files:**
- Modify: `src/explain_engine/chat/slash_stage_rules.py`
- Test: 既有 `tests/test_chat_slash_stage_rules.py`

**Step 1: Replace inline error formatting**

```python
# 老 (Phase 14):
content=(
    f"/{_cmd_name(fn)} 在当前 stage={stage!r} 不允许 "
    f"(需 stage ∈ {allowed})."
),

# 新:
from explain_engine.chat.chat_copy import err_stage_not_allowed
content=err_stage_not_allowed(_cmd_name(fn), stage, allowed),
```

**Step 2: 修 既有 test_chat_slash_stage_rules.py::TestGateCheck::test_blocks_disallowed_stage**

旧 assertion 含 `"bootstrap_pending"` (英文 stage 值直露). 新 assertion 应 `"等待启动"`.

```python
assert "等待启动" in events[0].content  # 翻译过的 stage
assert "已归纳" in events[0].content
assert "bootstrap_pending" not in events[0].content  # 英文不再直露
```

**Step 3: Run**

Expected: PASS

**Step 4: Commit**

```bash
git commit -m "chat/slash_stage_rules · Phase 15 Task 8: stage gate error 用 err_stage_not_allowed"
```

---

## Task 9: cli.py _render_event budget_exhausted scope 翻译

**Files:**
- Modify: `src/explain_engine/cli.py:_render_event` (budget_exhausted 分支)
- Test: `tests/test_cli_chat.py` 或 `tests/test_chat_loop.py`

**Step 1: Write the failing test**

```python
class TestRenderBudgetExhaustedChinese:
    def test_per_turn_scope_translated(self):
        from io import StringIO
        from rich.console import Console
        from explain_engine.chat.loop import BudgetExhaustedEvent
        from explain_engine.cli import _render_event

        buf = StringIO()
        con = Console(file=buf, force_terminal=False, width=100)
        _render_event(con, BudgetExhaustedEvent(scope="per_turn"))
        out = buf.getvalue()
        assert "本轮" in out or "本 turn" in out
        assert "per_turn" not in out

    def test_per_session_scope_translated(self):
        from io import StringIO
        from rich.console import Console
        from explain_engine.chat.loop import BudgetExhaustedEvent
        from explain_engine.cli import _render_event

        buf = StringIO()
        con = Console(file=buf, force_terminal=False, width=100)
        _render_event(con, BudgetExhaustedEvent(scope="per_session"))
        out = buf.getvalue()
        assert "本 session" in out or "本轮 session" in out
        assert "per_session" not in out
```

**Step 2: Run test to verify it fails**

Expected: FAIL (current renders `per_turn` raw).

**Step 3: Modify cli._render_event budget_exhausted branch**

Look for `t == "budget_exhausted"` branch in `_render_event`. 替成:

```python
elif t == "budget_exhausted":
    scope = getattr(event, "scope", "unknown")
    scope_zh_map = {"per_turn": "本轮", "per_session": "本 session"}
    scope_zh = scope_zh_map.get(scope, scope)
    con.print(
        f"[yellow]预算耗尽 ({scope_zh}). "
        f"用 /budget 重设或开新一轮对话.[/yellow]"
    )
```

**Step 4: Run test to verify it passes**

Expected: PASS

**Step 5: Commit**

```bash
git commit -m "cli · Phase 15 Task 9: budget_exhausted 渲染中文化 (per_turn/per_session)"
```

---

## Task 10: slash_commands.py DEFAULT_COMMANDS 引 COMMAND_DESCRIPTIONS

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py` (DEFAULT_COMMANDS 19 个 SlashCommand entries)
- Test: `tests/test_chat_slash_commands.py`

**Step 1: Write the failing test**

```python
class TestSlashRegistryUsesChineseDescriptions:
    def test_all_commands_have_chinese_description(self):
        import re
        from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
        chinese_pattern = re.compile(r'[一-鿿]')
        for c in DEFAULT_COMMANDS:
            assert chinese_pattern.search(c.description), (
                f"/{c.name} 无中文 description: {c.description!r}"
            )

    def test_no_english_jargon_in_descriptions(self):
        from explain_engine.chat.slash_commands import DEFAULT_COMMANDS
        forbidden = ["propose_candidates", "HITL", "reasoning loop",
                     "multi-signal", "manifests_as", "storage_v2",
                     "Multi-signal", "abstraction"]
        for c in DEFAULT_COMMANDS:
            for f in forbidden:
                assert f not in c.description, (
                    f"/{c.name} desc 仍含 jargon: '{f}' in '{c.description}'"
                )
```

**Step 2: Run test to verify it fails**

Expected: 2 FAIL (current desc still contain English jargon).

**Step 3: Replace description strings with COMMAND_DESCRIPTIONS lookups**

```python
# slash_commands.py
from explain_engine.chat.chat_copy import COMMAND_DESCRIPTIONS

DEFAULT_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("quit",    COMMAND_DESCRIPTIONS["quit"],    _handle_quit),
    SlashCommand("help",    COMMAND_DESCRIPTIONS["help"],    _handle_help),
    SlashCommand("show",    COMMAND_DESCRIPTIONS["show"],    _handle_show),
    SlashCommand("graph",   COMMAND_DESCRIPTIONS["graph"],   _handle_graph),
    # ... 19 entries 全部改
)
```

**Step 4: Run test to verify it passes**

Expected: PASS

**Step 5: Commit**

```bash
git commit -m "chat/slash_commands · Phase 15 Task 10: DEFAULT_COMMANDS 引 chat_copy.COMMAND_DESCRIPTIONS"
```

---

## Task 11: slash_commands.py _handle_help 用 HELP_GROUPS_ZH

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py:_handle_help` + `HELP_GROUPS` 删
- Test: `tests/test_chat_slash_commands.py::TestHelpGrouping`

**Step 1: Write the failing test**

```python
class TestHelpGroupingChinese:
    @pytest.mark.asyncio
    async def test_help_shows_chinese_group_headers(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_h0000001")
        chat = ChatSession("s_h0000001")
        events = await dispatch_slash(chat, "/help")
        content = events[0].content
        for header in (
            "推进 session",
            "干预分析",
            "查看状态",
            "管理 session",
            "其他",
            "帮助 / 退出",
        ):
            assert header in content, f"missing chinese group header: {header}"

    @pytest.mark.asyncio
    async def test_help_no_english_group_names(self):
        from explain_engine.chat.session import ChatSession
        _make_done_session("s_h0000002")
        chat = ChatSession("s_h0000002")
        events = await dispatch_slash(chat, "/help")
        content = events[0].content
        assert "Session 推进" not in content  # 老 group name (Phase 14)
        assert "Inspection" not in content
```

**Step 2: Run test to verify it fails**

Expected: FAIL (current help 还用 Phase 14 的"Session 推进" / "Inspection" 等).

**Step 3: Replace local HELP_GROUPS with import + use chinese group names**

```python
# slash_commands.py
from explain_engine.chat.chat_copy import HELP_GROUPS_ZH

# 删: HELP_GROUPS 老 tuple

async def _handle_help(chat, args):
    from explain_engine.chat.session import ChatEvent
    cmd_by_name = {c.name: c for c in DEFAULT_COMMANDS}

    lines = ["所有命令:\n"]
    for group_name, cmd_names in HELP_GROUPS_ZH:
        lines.append(f"  {group_name}:")
        for n in cmd_names:
            c = cmd_by_name.get(n)
            if c is not None:
                lines.append(f"    /{c.name} — {c.description}")
        lines.append("")  # blank line between groups

    if "cf" in cmd_by_name:
        lines.append("  别名: /cf → /counterfactual")

    return [ChatEvent(type="slash_help", content="\n".join(lines))]
```

**Step 4: Run test to verify it passes**

Expected: PASS

**Step 5: Commit**

```bash
git commit -m "chat/slash_commands · Phase 15 Task 11: _handle_help 用 HELP_GROUPS_ZH"
```

---

## Task 12: slash_commands.py — 推进 session handlers (compress / run / rescore)

**Files:**
- Modify: `src/explain_engine/chat/slash_commands.py:_handle_compress / _handle_run / _handle_rescore`
- Test: `tests/test_chat_slash_commands.py`

**Step 1: 把 handler 内英文 / 技术词 print + event content 引 chat_copy 模板**

替换示例:

```python
# 老:
with _console.status("[bold green]调 LLM 提候选 (compress)...[/bold green]"):
# 新:
from explain_engine.chat.chat_copy import STATUS_COMPRESS_PROPOSE
with _console.status(STATUS_COMPRESS_PROPOSE):

# 老:
return [ChatEvent(type="slash_compress", content=f"compress 完, {n_added} candidate 写入...")]
# 新:
from explain_engine.chat.chat_copy import msg_compress_done
return [ChatEvent(type="slash_compress", content=msg_compress_done(n_added, n_to_lexicon))]

# 老:
return [ChatEvent(type="slash_error", content=f"/compress propose_candidates 失败: {type(exc).__name__}: {exc}")]
# 新:
from explain_engine.chat.chat_copy import err_failed
return [ChatEvent(type="slash_error", content=err_failed("compress", exc))]

# 老:
content=f"reasoning loop 完成: stop_reason={reason}, tick={chat.state.tick}"
# 新:
from explain_engine.chat.chat_copy import msg_run_done
content=msg_run_done(reason, chat.state.tick)
```

**Step 2: Update 既有 test assertion**

`tests/test_chat_slash_commands.py::TestSlashCompress / TestSlashRun / TestSlashRescore` 各 case 改关键词匹配:

```python
# Old:
assert "reasoning loop" in events[0].content
# New:
assert "推理" in events[0].content
```

**Step 3: Run pytest**

Run: `.venv/bin/python -m pytest tests/test_chat_slash_commands.py -v -k "TestSlashCompress or TestSlashRun or TestSlashRescore"`
Expected: PASS

**Step 4: Commit**

```bash
git commit -m "chat/slash · Phase 15 Task 12: 推进 session handlers (compress/run/rescore) 引 chat_copy 模板"
```

---

## Task 13: slash_commands.py — 干预 handlers (predict / counterfactual / cf)

**Files:**
- Modify: `_handle_predict`, `_handle_counterfactual` (cf shares same handler)
- Test: `tests/test_chat_slash_commands.py`

**Step 1: Replace English / jargon strings with templates**

Predict / counterfactual handler 内 status / error / success content 改引 STATUS_PREDICT / STATUS_COUNTERFACTUAL / err_failed.

**Step 2: 既有 test 改关键词**

**Step 3: Run pytest**

Expected: PASS

**Step 4: Commit**

```bash
git commit -m "chat/slash · Phase 15 Task 13: 干预 handlers (predict/counterfactual) 中文化"
```

---

## Task 14: slash_commands.py — Inspection handlers (show / graph / check)

**Files:**
- Modify: `_handle_show`, `_handle_graph`, `_handle_check`
- Test: `tests/test_chat_slash_commands.py`

**Step 1: 改 inspection 类 handler 输出**

这些是只读命令, 输出复杂结构 (graph dump / acceptance table). 关键改:
- header 行从英文术语 ("Multi-signal acceptance report") → 中文 ("接受度评估报告")
- L0/L1/L2 用 zh() 转
- weak_chain / rollout_coverage 等用 TERMS_MAP 翻

**Step 2: 改 test assertion**

**Step 3: Run pytest**

**Step 4: Commit**

```bash
git commit -m "chat/slash · Phase 15 Task 14: Inspection handlers (show/graph/check) 中文化"
```

---

## Task 15: slash_commands.py — 管理 handlers (new / resume / list / lexicon)

**Files:**
- Modify: `_handle_new`, `_handle_resume`, `_handle_list`, `_handle_lexicon`
- Test: `tests/test_chat_slash_commands.py`

**Step 1: 改 handler 内文案**

- `/new`: 已基本中文 (Phase 11 重构), 微调措辞
- `/resume`: msg_resume_already / msg_resume_switching 引模板; table 表头中文
- `/list`: table 表头中文
- `/lexicon`: 输出表头中文 + TERMS_MAP 翻 abstraction_level 等列名

**Step 2: 改 test assertion**

**Step 3: Run pytest**

**Step 4: Commit**

```bash
git commit -m "chat/slash · Phase 15 Task 15: 管理 handlers 中文化"
```

---

## Task 16: slash_commands.py — 其他 + 退出 handlers (budget / compact / save / migrate / quit)

**Files:**
- Modify: `_handle_budget`, `_handle_compact`, `_handle_save`, `_handle_migrate`, `_handle_quit`
- Test: `tests/test_chat_slash_commands.py`

**Step 1: 改 handler 内文案**

- `/budget`: Phase 14 已部分中文化, 进一步统一术语 (e.g. "已用" / "剩余")
- `/compact`: Phase 14 已基本 OK
- `/save`: 引 msg_save_done
- `/migrate`: 输出文本中文化
- `/quit`: farewell 改中文 ("再见, session 已存盘.")

**Step 2: 改 test assertion**

**Step 3: Run pytest**

**Step 4: Commit**

```bash
git commit -m "chat/slash · Phase 15 Task 16: 其他 + 退出 handlers 中文化"
```

---

## Task 17: hitl/cli_interactive.py — HITL prompts 中文化

**Files:**
- Modify: `src/explain_engine/hitl/cli_interactive.py:review_phenomena / review_phenomena_async / review_insights / review_insights_async / review_predicted_l0`
- Test: `tests/test_hitl_*` (扫看哪些 test 含英文 prompt 断言)

**Step 1: Identify English prompt strings**

```bash
grep -n 'Prompt\|prompt\|press\|enter\|"k"\|"d"\|"e"\|"q"\|"y"\|"n"\|input(' src/explain_engine/hitl/cli_interactive.py | head -30
```

**Step 2: 中文化 prompt**

举例:
```python
# 老:
console.print(f"[bold]Phenomenon {i+1}/{n}:[/bold] {p.name}")
choice = prompt("(k)eep / (e)dit / (d)rop / (q)uit: ").strip().lower()

# 新:
console.print(f"[bold]现象 {i+1}/{n}:[/bold] {p.name}")
choice = prompt("(k) 保留 / (e) 编辑 / (d) 删除 / (q) 退出: ").strip().lower()
```

按键标签 (k/e/d/q) 保留 — 跟命令名同语义 (input shortcut).

**Step 3: 改 既有 hitl test assertion**

**Step 4: Run pytest**

Run: `.venv/bin/python -m pytest tests/test_hitl_*.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git commit -m "hitl/cli_interactive · Phase 15 Task 17: HITL prompts 中文化 (review_phenomena / review_insights / review_predicted_l0)"
```

---

## Task 18: Acceptance smoke doc

**Files:**
- Create: `docs/plans/2026-05-21-chat-dejargon-acceptance.md`

**Step 1: 写 acceptance doc**

```markdown
# Phase 15 Acceptance: chat REPL 去技术化

**Date**: 2026-05-21

## Manual Smoke Test (11 步)

### 1. Ephemeral REPL 启动
```bash
.venv/bin/python -m explain_engine.cli
```
预期: banner 中文 "进入 chat (空白状态)" 或类似.

### 2. /help 验输出全中文 + 6 中文 group
预期: 含 "推进 session:" / "干预分析" / "查看状态" 等中文 header. 19 条 command desc 都含中文且 ≤50 字.

### 3. 输自然语言 question → promote
```
> 为什么年轻人储蓄少了
```
预期: 进真 session.

### 4. /show 验输出中文术语
预期: 含 "现象" / "归纳出的模式" / "因果图" / "接受度评估". 不见 "L0" / "L1" / "abstraction" / "graph" 字面.

### 5. /run on bp 验 stage error 中文
```
> /run
```
预期: "/run 在当前阶段 (等待启动) 不能跑 — 需要阶段为: 已归纳."
hint: "需要先 /compress 把现象归纳成模式..."

### 6. /compress 验中文 status + 完成 msg
```
> /compress
```
预期: spinner "正在归纳模式..." → "正在评分候选模式..." → HITL 中文 prompt ("(k) 保留 / (e) 编辑...") → "归纳完成: 加了 N 个模式..."

### 7. /run 完成 stop_reason 中文
```
> /run
```
预期: status "正在自动推理 (扩展 / 反思 / 衰减)..." → "推理完成: 在第 K 步停止 (原因: 已收敛)."

### 8. /predict 中文
```
> /predict 假设某干预
```
预期: status "正在预测干预影响..." + 输出含中文术语.

### 9. /rescore 中文
```
> /rescore
```
预期: status + "重评完成: N 条因果关系, 平均可信度 X.XX. 已存盘."

### 10. /budget 触发耗尽 验"本轮预算" / "本 session 预算"
- 设 per_turn=1 + per_session=2
- 让 LLM 调用快速耗尽
- 预期: `[yellow]预算耗尽 (本轮). 用 /budget 重设或开新一轮对话.[/yellow]` (类似)

### 11. /quit 验中文 farewell
预期: "再见, session 已存盘." 或类似中文.

## 通过标准

11 步全过, **任何步骤输出有 L0/L1/L2/abstraction/propagation/lexicon/HITL/reasoning loop 等 jargon 直露 = FAIL**.

跑完后再跑全量 pytest + ruff:
- `.venv/bin/python -m pytest`  → 0 失败
- `.venv/bin/ruff check src/ tests/` → 0 警告
```

**Step 2: Commit**

```bash
git add docs/plans/2026-05-21-chat-dejargon-acceptance.md
git commit -m "docs · Phase 15 Task 18: acceptance smoke doc (11 步 manual)"
```

---

## Task 19: 全量验证 + 修最后边角

**Files:**
- 任何 cleanup needed

**Step 1: Run full pytest**

Run: `.venv/bin/python -m pytest`
Expected: ALL PASS (estimate ~990+ test).

**Step 2: Run ruff**

Run: `.venv/bin/ruff check src/ tests/`
Expected: All checks passed.

**Step 3: 手动 acceptance smoke (11 步)**

照 Task 18 doc 跑一遍, 任何步骤现 jargon 都修补:

```bash
.venv/bin/python -m explain_engine.cli
# 跑 11 步, 截关键 output, verify 跟 doc 期望对得上.
```

**Step 4: 如有 jargon 漏 fix**

- 漏 jargon 现在 chat REPL 路径 → 找出对应 handler → 把硬编 string 改成 chat_copy 模板引用
- 漏 TERMS_MAP entry → 添加 + 加 test
- 提 follow-up commit:

```bash
git commit -m "chat · Phase 15 Task 19: 最终 polish + smoke verify"
```

---

## 总结

19 个 task 完成 = Phase 15 chat REPL 去技术化全部上线.

最终预期 git log (反序):

```
xxxxxxx chat · Phase 15 Task 19: 最终 polish + smoke verify
xxxxxxx docs · Phase 15 Task 18: acceptance smoke doc (11 步 manual)
xxxxxxx hitl/cli_interactive · Phase 15 Task 17: HITL prompts 中文化
xxxxxxx chat/slash · Phase 15 Task 16: 其他 + 退出 handlers 中文化
xxxxxxx chat/slash · Phase 15 Task 15: 管理 handlers 中文化
xxxxxxx chat/slash · Phase 15 Task 14: Inspection handlers 中文化
xxxxxxx chat/slash · Phase 15 Task 13: 干预 handlers 中文化
xxxxxxx chat/slash · Phase 15 Task 12: 推进 session handlers 引模板
xxxxxxx chat/slash_commands · Phase 15 Task 11: _handle_help 用 HELP_GROUPS_ZH
xxxxxxx chat/slash_commands · Phase 15 Task 10: DEFAULT_COMMANDS 引 chat_copy
xxxxxxx cli · Phase 15 Task 9: budget_exhausted 渲染中文
xxxxxxx chat/slash_stage_rules · Phase 15 Task 8: gate error 用 err_stage_not_allowed
xxxxxxx chat/slash_stage_rules · Phase 15 Task 7: HINTS_BY_KEY import from chat_copy
xxxxxxx chat/chat_copy · Phase 15 Task 6: STATUS_* + INFO_* 常量
xxxxxxx chat/chat_copy · Phase 15 Task 5: HINTS_BY_KEY 去 jargon 重写
xxxxxxx chat/chat_copy · Phase 15 Task 4: err_*() error templates
xxxxxxx chat/chat_copy · Phase 15 Task 3: STOP_REASON_MAP + msg_*() success templates
xxxxxxx chat/chat_copy · Phase 15 Task 2: COMMAND_DESCRIPTIONS + HELP_GROUPS_ZH
xxxxxxx chat/chat_copy · Phase 15 Task 1: TERMS_MAP + zh() helper
xxxxxxx docs/plans · Phase 15 (待实装) chat REPL 去技术化设计
```

## Skills Used

- `superpowers:executing-plans` (per task TDD discipline)
- `superpowers:test-driven-development` (TDD red-green-commit cycle)
- `superpowers:verification-before-completion` (Task 19 final smoke)
- `superpowers:systematic-debugging` (若 implementation 撞 bug 走此 skill)
