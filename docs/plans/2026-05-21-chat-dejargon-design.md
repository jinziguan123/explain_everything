# Chat REPL 去技术化 (De-jargon) 设计

**Date**: 2026-05-21
**Status**: brainstorm 完, 待 implementation plan
**Branch**: dev
**Related**:
- Phase 11 Wave 3/4 (chat slash commands, 引入 19 slash)
- Phase 14 chat stage flow + next-step hints (引入 slash_stage_rules.py + HINTS_BY_KEY)
- 用户 brainstorm 反馈: "专业术语太多, 中英文混杂, 用户根本不知道某个命令到底要做什么"

## 1. Motivation

Phase 11+14 之后 chat REPL 内 19 slash 命令的 user-visible 文本撞 3 个 UX 问题:

### 1.1 命令名 / 描述 / 输出全英文或中英混杂

```
SlashCommand("compress", "Compress 当前 session (propose_candidates + HITL + lexicon).", ...)
SlashCommand("run",      "跑 reasoning loop (expansion + reflection).", ...)
SlashCommand("check",    "Multi-signal acceptance report (read-only).", ...)
```

中文用户进 REPL 看到 `/compress`, 描述含 `propose_candidates / HITL / lexicon`, 完全不知道这命令到底做什么.

### 1.2 高密度技术术语暴露

19 个命令的描述 + handler 输出 + status spinner + 错误消息 + Phase 14 hint, 散落超 30 个技术词: `L0/L1/L2`, `driver`, `abstraction`, `graph`, `edge`, `propagation`, `expansion`, `reflection`, `decay`, `reasoning loop`, `lexicon`, `canonical_mechanism`, `multi-signal`, `weak_chain`, `rollout_coverage`, `consistency`, `essentialness`, `HITL`, `manifests_as`, `causes`, `stage` 4 值 (`bootstrap_pending / insight_pending / done / converged`), 等等.

### 1.3 文案散落, 无单一术语词典

handler 内 _console.print / event content 各处独立写, 同一概念 (e.g. "lexicon") 在不同 handler 措辞各异. 真要改文案得 grep 全 codebase, 易漏.

## 2. Goals

1. **chat REPL 输入端**: 19 命令名 **保留英文**. 用户输 `/compress` 仍 work, 不破 muscle memory.
2. **chat REPL 输出端 (描述 + 状态 + 完成消息 + 错误 + hint)**: **全中文化, 去 jargon**. 用户在 REPL 内看到的所有文本应用直观中文短语.
3. **术语词典集中**: 新 file `chat/chat_copy.py` 含 TERMS_MAP / COMMAND_DESCRIPTIONS / HINTS_BY_KEY / RUNTIME_MESSAGES / 模板函数. 一处改全场动.
4. **核心术语去英文**: L0/L1/L2 → 现象/归纳出的模式/深层原因; lexicon → 跨 session 概念库; graph → 因果图; etc.

## 3. Non-Goals

- **重命名 19 slash 命令** (e.g. `/compress` → `/归纳`): 用户决策保留英文命令名.
- **cli 子命令路径中文化** (`explain new` / `explain compress` 等): scope 仅 chat REPL.
- **README / 设计文档中文化**: docs 不在本 phase scope.
- **i18n framework** (gettext / fluent): 硬编码中文够用, YAGNI.
- **Expert mode toggle** (env var 切回英文文案): YAGNI, 老用户读 git history.
- **engine 内部代码 rename** (`abstraction_level=0/1/2` field name): 内部不动, 仅 user-facing 翻.

## 4. Design

### 4.1 Architecture

```
                    chat/chat_copy.py (新)
                   ┌────────────────────┐
                   │  TERMS_MAP         │  L0 → 现象 / lexicon → 跨 session 概念库
                   │  COMMAND_DESCRIPTIONS  /compress → "把多个现象归纳成模式"
                   │  HELP_GROUPS_ZH    │  6 组中文 group name
                   │  HINTS_BY_KEY      │  Phase 14 hint 去 jargon
                   │  STOP_REASON_MAP   │  converged → 已收敛
                   │  STATUS_*          │  status spinner 文案
                   │  INFO_*            │  dim info 提示
                   │  msg_*() / err_*() │  template 函数
                   │  zh() helper       │  TERMS_MAP 查询 (fallback 原词)
                   └────────────────────┘
                              ↑
              ┌───────────────┼───────────────┬─────────────┐
              │               │               │             │
        slash_commands.py    slash_stage_   cli.py      hitl/cli_
        (19 handler 引)      rules.py       _render_     interactive.py
                             (with_stage_   event 渲      (HITL prompt)
                              gate decorator) budget event scope
```

`chat_copy.py` 是单一文案 source. 所有 user-facing 输出引这里. handler 体内不再硬编中文 (除极琐碎 internal info).

### 4.2 TERMS_MAP (术语词典)

```python
TERMS_MAP: dict[str, str] = {
    # ── Graph 节点层次 (abstraction level) ──
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

    # ── 引擎 ops (出错信息里偶尔露) ──
    "propose_candidates": "提候选",
    "score_all": "评分",
    "HITL": "人工审查",
    "persist": "存盘",
}


def zh(term: str) -> str:
    """把英文/技术词翻成中文直观短语. 找不到返原词 (defensive)."""
    return TERMS_MAP.get(term, term)
```

### 4.3 COMMAND_DESCRIPTIONS (19 命令中文 desc)

```python
COMMAND_DESCRIPTIONS: dict[str, str] = {
    # 推进 session
    "compress": "把多个现象归纳成模式 (建 session 后第一步)",
    "run":      "自动推理找深层原因 (compress 之后做)",
    "rescore":  "重新评估所有因果关系的可信度",

    # 干预分析
    "predict":         "预测某假设干预会带来什么下游影响",
    "counterfactual":  "反事实分析: 如果不发生某事, 系统会怎么样",
    "cf":              "(等同 /counterfactual)",

    # 查看状态
    "show":  "显示当前 session 的因果图 + 接受度评估",
    "graph": "渲染因果图的可视化 (需安装 graphviz)",
    "check": "查看接受度评估报告 (跟 /show 信息侧重不同)",

    # 管理 session
    "new":     "重置当前 chat, 回到刚启动的空白状态",
    "resume":  "切换到历史 session",
    "list":    "列出当前项目所有 session",
    "lexicon": "查看跨 session 累积的概念库",

    # 其他
    "budget":  "查看 / 设置 LLM 调用预算",
    "compact": "强制压缩对话记忆",
    "save":    "立即把当前所有状态存盘",
    "migrate": "(一次性) 老 session 文件迁移到新存储格式",

    # 帮助 / 退出
    "help": "看命令列表",
    "quit": "退出 (自动存盘)",
}

HELP_GROUPS_ZH: list[tuple[str, list[str]]] = [
    ("推进 session",                ["compress", "run", "rescore"]),
    ("干预分析 (需先 /compress)",   ["predict", "counterfactual"]),
    ("查看状态 (只读)",             ["show", "graph", "check"]),
    ("管理 session",                ["new", "resume", "list", "lexicon"]),
    ("其他",                        ["budget", "compact", "save", "migrate"]),
    ("帮助 / 退出",                 ["help", "quit"]),
]
```

### 4.4 RUNTIME_MESSAGES (handler 内部 status + info + msg + err 模板)

```python
# Status spinners (Rich console.status during long ops)
STATUS_COMPRESS_PROPOSE = "[bold green]正在归纳模式...[/bold green]"
STATUS_COMPRESS_SCORE   = "[bold green]正在评分候选模式...[/bold green]"
STATUS_LEXICON_FLUSH    = "[bold green]正在存盘到概念库...[/bold green]"
STATUS_RUN              = "[bold green]正在自动推理 (扩展 / 反思 / 衰减)...[/bold green]"
STATUS_PREDICT          = "[bold green]正在预测干预影响...[/bold green]"
STATUS_COUNTERFACTUAL   = "[bold green]正在做反事实分析...[/bold green]"
STATUS_RESCORE          = "[bold green]正在重评因果关系...[/bold green]"

# Info / dim
INFO_INSIGHT_PENDING_RESUME = "[dim](检测到中途取消, 跳过 LLM 直接进入审查)[/dim]"
INFO_MID_STAGE_SAVED        = "[dim](中间状态已保存, 取消审查可下次重入跳过 LLM)[/dim]"


# Success event content (template functions)
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


# Error templates
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


STOP_REASON_MAP: dict[str, str] = {
    "budget_exhausted":     "预算耗尽",
    "converged":            "已收敛 (无更多可推进点)",
    "no_meaningful_action": "无更多可推进点",
    "max_ticks":            "达到最大推理步数",
}
```

**注**: STOP_REASON_MAP 这里 4 值是猜的, 实装阶段 grep `runtime/runtime.py` 全 enum 补全.

### 4.5 HINTS_BY_KEY (Phase 14 6 hint 全部去 jargon)

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

`slash_stage_rules.py` 改成 `from chat.chat_copy import HINTS_BY_KEY` (单 source).

## 5. 影响文件清单 + 改动规模

### 新文件

| 文件 | 行数 | 内容 |
|---|---|---|
| `src/explain_engine/chat/chat_copy.py` | ~250 | TERMS_MAP / COMMAND_DESCRIPTIONS / HELP_GROUPS_ZH / HINTS_BY_KEY / STOP_REASON_MAP / STATUS_* / INFO_* / msg_*() / err_*() / zh() |

### 改现有 src (5 文件)

| 文件 | 改 (行) | 改什么 |
|---|---|---|
| `chat/slash_commands.py` | ~150 | DEFAULT_COMMANDS 引 chat_copy.COMMAND_DESCRIPTIONS; _handle_help 用 HELP_GROUPS_ZH; 19 handler 内 _console.print / event content 引模板 |
| `chat/slash_stage_rules.py` | ~20 | HINTS_BY_KEY 改 import from chat_copy; with_stage_gate stage error 用 err_stage_not_allowed |
| `hitl/cli_interactive.py` | ~30 | review_phenomena / review_insights_async / review_predicted_l0 等 HITL prompt 中文化 |
| `cli.py` | ~10 | `_render_event` 对 budget_exhausted scope 字段做 zh map |
| `chat/loop.py` | 0 | BudgetExhaustedEvent scope 仍机器字段, 渲染层翻 — loop.py 0 改 |

### 改 tests (4 文件)

| 文件 | 改 (行) | 改什么 |
|---|---|---|
| `tests/test_chat_slash_commands.py` | ~50 | TestSlashXxx 类 ~20 assertion 改 (英文 → 中文 substring) |
| `tests/test_chat_slash_stage_rules.py` | ~15 | Phase 14 hint content + stage_not_allowed assertion 改 |
| `tests/test_chat_repl_input.py` | ~5 | /help 输出 group name 改中文 |
| `tests/test_chat_session.py` | 0-3 | 几乎不影响 |

### 新 tests

| 文件 | 行数 | 内容 |
|---|---|---|
| `tests/test_chat_copy.py` (新) | ~80 | TERMS_MAP completeness / 19 desc 全在 + ≤50 字 + 含中文 / zh() fallback / err_*() / msg_*() / STOP_REASON_MAP |

### 新 doc

| 文件 | 行数 |
|---|---|
| `docs/plans/2026-05-21-chat-dejargon-design.md` (本文) | ~600 |
| `docs/plans/2026-05-21-chat-dejargon-acceptance.md` | ~150 (11 步 manual smoke) |
| `docs/plans/2026-05-21-chat-dejargon-plan.md` (待 writing-plans 阶段生成) | TBD |

### 总规模

| 类型 | 行数 |
|---|---|
| 新 src | ~250 |
| 改 src | ~210 |
| 新 tests | ~80 |
| 改 tests | ~70 |
| **代码小计** | **~610** |
| 新 docs | ~750 |

**估实装**: 2-3 天 (每条文案需 review user-facing 合理性, 比 Phase 14 长).

## 6. Testing 策略

### 6.1 新 `test_chat_copy.py` (~10 case)

- TERMS_MAP 关键术语 (L0/L1/L2/stage 4 值/graph/edge/lexicon 等) 必须存在
- zh() fallback 返原词
- 19 命令 desc 全在 + 含中文 + ≤50 字
- err_stage_not_allowed 翻译 stage 值, 不直露英文
- msg_run_done 翻译 stop_reason

### 6.2 既有 test 适配

assertion 改 **关键词 substring** 而非完整字符串相等:

```python
# Bad (脆弱):
assert events[0].content == "/compress 失败: ValueError: bad"

# Good (语义匹配):
assert "compress" in events[0].content
assert "失败" in events[0].content
```

### 6.3 Acceptance smoke (manual)

`docs/plans/2026-05-21-chat-dejargon-acceptance.md` — 11 步, 跑完整 chat flow 验证:
- /help 全中文 + 6 中文 group name
- /show 含 "因果图" / "现象", 不见 L0/L1/L2/edge 字面
- /run on bp 时 stage error 中文 ("需要阶段为: 已归纳")
- /compress status + 完成 msg 中文
- /run 完成 stop_reason 中文 ("已收敛")
- /budget 耗尽 event 中文 ("本轮预算" / "本 session 预算")

### 6.4 (Defer follow-up) CI gate test

扫 chat_copy 之外 user-facing 文案是否漏 jargon. 实装较复杂 (AST 区分 docstring vs 真 user-facing string), 不在本 phase scope. 加入 follow-up F-X.

## 7. Risks / Trade-offs

| 风险 | 严重度 | 缓解 |
|---|---|---|
| Test fragility (assertion 改太硬) | 中 | 关键词 substring 匹配, 不全字符串 equal |
| 遗漏 jargon (handler 个别 message 没引模板) | 中 | acceptance smoke 跑主路径; 用户 report 增量补 |
| STOP_REASON_MAP enum 不全 | 低 | 实装时 grep runtime.py 全 enum 补 |
| HITL 路径文案改 → 老 test mock 文案 hardcode 英文 | 中 | assertion 改关键词匹配 |
| 用户认知 dissonance (命令名英文 + desc 中文) | 低 | Q1 决策已 accept |
| 回归 Phase 14 工作 (HINTS_BY_KEY 改) | 低 | Phase 14 hint test ~5 个改关键词 |

## 8. Out-of-scope (deferred)

- cli 子命令路径中文化
- README / 设计文档中文化
- Expert mode toggle (env 切回英文)
- i18n framework
- 命令名重命名 + ChatEvent type 字段重命名

## 9. Rollout

1 feature branch, **3-4 个 commit** 分批 (避免单 squash 太大难 review):

1. **chat_copy.py 新建** + 4 数据结构 (TERMS_MAP / COMMAND_DESCRIPTIONS / HELP_GROUPS_ZH / STOP_REASON_MAP) + helper (zh / err_* / msg_*) + 新 `test_chat_copy.py`
2. **slash_commands.py 改**: DEFAULT_COMMANDS + _handle_help + 19 handler 引文案 + 既有 test 适配
3. **slash_stage_rules.py + cli.py 改**: HINTS_BY_KEY 接 chat_copy + stage error 中文 + budget event 渲染
4. **hitl/cli_interactive.py 改** + acceptance smoke doc + 最终 verify (pytest + ruff + 11 步 manual)

每 commit 后 `.venv/bin/python -m pytest` + `.venv/bin/ruff check src/ tests/` 必跑.

## 10. Follow-ups (本 phase 外)

| F# | 内容 |
|---|---|
| F-1 | cli 子命令路径同步中文化 |
| F-2 | README + 顶层设计文档中文化 |
| F-3 | 命令名引入中文 alias (`/归纳` 同 `/compress`) |
| F-4 | Expert mode env var (切回老英文) |
| F-5 | CI gate test 扫 jargon 残留 |
| F-6 | i18n framework (gettext) |
| F-7 | "术语对应表" doc 帮老用户从英文 mental model 过渡 |
