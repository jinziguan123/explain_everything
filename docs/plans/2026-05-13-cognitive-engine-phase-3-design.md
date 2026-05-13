# Cognitive Engine Phase 3 — Design Doc

**日期**: 2026-05-13
**分支**: `cognitive-engine-mvp`
**前置**: [Phase 0+1+2 完工](2026-05-13-cognitive-engine-phase-0-1-2-plan.md), [MVP Design Doc](2026-05-13-cognitive-engine-mvp-design.md), [Hardening Plan](2026-05-13-phase-1-hardening-plan.md)

---

## 0. TL;DR

Phase 3 把 Phase 0+1+2 立起来的 schema + LLM client 串成第一个**端到端可跑的 CLI 入口**。用户输入 `explain new "为什么 X"`，系统调 LLM 生 8-15 个 concrete phenomena，进入多轮对话 HITL 让用户审 / 改 / 加 / 减，最后落 session JSON。**不引入 reasoning loop**，runtime engines 推 Phase 4。

Phase 3 跑通后，能演示：
- LLM 真实出 phenomena
- HITL 真实跑通 (用户能干预 LLM 输出)
- Session 落地能 round-trip

---

## 1. Scope

### 1.1 在 Phase 3 内

- `BootstrapEngine` — LLM 调 `variable_extraction.yaml` prompt → 生 8-15 个 `VariableNode` (level=0, epistemic="observation")
- **HITL 1 多轮对话** — 逐条 keep/edit/drop + 末尾 "添加吗" 循环
- **CLI 3 命令**:
  - `explain new <question>` — 入口，跑 Bootstrap + HITL 1 + 落 session
  - `explain show <session_id>` — rich table 显示某 session 的 phenomena
  - `explain list` — rich table 显示所有 session meta

### 1.2 推到 Phase 4+

- ❌ Reasoning loop（Expansion / Compression / Evaluation / Convergence engines）
- ❌ `explain continue` 命令
- ❌ HITL 2 (insight 选择)
- ❌ Render (Markdown / explanation.md)

### 1.3 推到 v0.2+

- ❌ `VariableNode.source` 字段（schema 不改）
- ❌ HITL 1 中的 "重新生成全部" 按钮
- ❌ Session 进度可视化 / TUI

---

## 2. 文件结构

```
src/explain_engine/
├── engines/
│   ├── __init__.py
│   └── bootstrap.py              # BootstrapEngine.bootstrap_phenomena()
├── llm/
│   └── prompts/
│       ├── __init__.py (新)
│       ├── _loader.py (新)        # 加载 yaml prompt 模板
│       └── variable_extraction.yaml
├── hitl/
│   ├── __init__.py
│   └── cli_interactive.py        # HITL 1 多轮对话
└── cli.py                        # typer app: new / show / list

tests/
├── test_engines_bootstrap.py
├── test_hitl_cli_interactive.py
├── test_cli.py
└── test_llm_prompts_loader.py
```

---

## 3. Component 设计

### 3.1 `variable_extraction.yaml`

```yaml
# src/explain_engine/llm/prompts/variable_extraction.yaml
description: |
  Bootstrap engine 用。输入用户问题，输出 8-15 个相关的具体现象。

system: |
  你是一个认知引擎的 Variable Extraction 模块。
  你的唯一职责：从用户的"为什么"问题中提取 8-15 个相关的**具体现象**。

  规则：
  1. 现象应该是可观察的、具体的、与问题直接相关的
  2. 不要总结、不要给原因、不要给结论
  3. 每个现象用 1-2 句中文描述
  4. 现象之间应该多样化（不要把一个现象拆 5 次）
  5. 输出严格 JSON，不要额外文字

user_template: |
  问题: {question}

  请输出 {min_count}-{max_count} 个相关现象。
```

### 3.2 `BootstrapEngine`

```python
# src/explain_engine/engines/bootstrap.py
from pydantic import BaseModel

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.nodes import VariableNode


class _PhenomenonOutput(BaseModel):
    name: str
    description: str


class BootstrapOutput(BaseModel):
    """variable_extraction prompt 的 structured output schema."""
    phenomena: list[_PhenomenonOutput]


async def bootstrap_phenomena(
    question: str,
    llm: LLMClient,
    min_count: int = 8,
    max_count: int = 15,
) -> list[VariableNode]:
    """调 variable_extraction prompt 生 8-15 个 concrete phenomena.

    LLM 出 {name, description} list, 系统补 id / level / confidence /
    epistemic default。返回 VariableNode list。
    """
    prompt = load_prompt("variable_extraction")
    messages = [
        Message(role="system", content=prompt["system"]),
        Message(
            role="user",
            content=prompt["user_template"].format(
                question=question,
                min_count=min_count,
                max_count=max_count,
            ),
        ),
    ]
    resp = await llm.chat(messages, schema=BootstrapOutput)
    if resp.parsed is None:
        raise ValueError("LLM 未返回 structured output")
    raw = BootstrapOutput.model_validate(resp.parsed)

    # 截断到 max_count
    phenomena = raw.phenomena[:max_count]

    return [
        VariableNode(
            id=f"p_{i+1:03d}",
            name=p.name,
            description=p.description,
            abstraction_level=0,
            confidence=0.7,
            epistemic="observation",
        )
        for i, p in enumerate(phenomena)
    ]
```

### 3.3 Prompt Loader

```python
# src/explain_engine/llm/prompts/_loader.py
from pathlib import Path
import yaml

_PROMPTS_DIR = Path(__file__).parent

def load_prompt(name: str) -> dict:
    """加载 yaml prompt 模板."""
    p = _PROMPTS_DIR / f"{name}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"prompt {name} not found at {p}")
    return yaml.safe_load(p.read_text())
```

### 3.4 `cli_interactive.py` (HITL 1)

```python
# src/explain_engine/hitl/cli_interactive.py
from rich.console import Console
from rich.prompt import Prompt, Confirm

from explain_engine.schema.nodes import VariableNode


def review_phenomena(
    phenomena: list[VariableNode],
    console: Console | None = None,
) -> list[VariableNode]:
    """逐条 keep / edit / drop, 末尾允许 add. 返回最终保留的 list."""
    console = console or Console()
    kept: list[VariableNode] = []

    for i, p in enumerate(phenomena, start=1):
        console.print(f"\n[bold cyan][{i}/{len(phenomena)}][/bold cyan] {p.name}")
        console.print(f"       {p.description}", style="dim")
        choice = Prompt.ask(
            "       [k]eep / [e]dit / [d]rop",
            choices=["k", "e", "d"],
            default="k",
        )
        if choice == "k":
            kept.append(p)
        elif choice == "e":
            new_name = Prompt.ask("       新名称", default=p.name)
            new_desc = Prompt.ask("       新描述", default=p.description)
            kept.append(p.model_copy(update={"name": new_name, "description": new_desc}))
        # d → 不加入 kept

    console.print(f"\n[INFO] 审查完成。当前保留 [bold]{len(kept)}[/bold] 条现象。")

    # 添加循环
    while Confirm.ask("要添加新的现象吗？", default=False):
        new_name = Prompt.ask("新名称").strip()
        if not new_name:
            break
        new_desc = Prompt.ask("新描述").strip()
        next_id = f"p_user_{len(kept) - len(phenomena) + 1:03d}"
        kept.append(
            VariableNode(
                id=next_id,
                name=new_name,
                description=new_desc,
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
        )

    return kept
```

注：用户加的 phenomena id 用 `p_user_NNN` 区分（虽然 schema 没 source 字段，但 id 前缀能事后追溯）。

### 3.5 `cli.py` (typer app)

```python
# src/explain_engine/cli.py
import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from explain_engine.config import Settings, make_client
from explain_engine.engines.bootstrap import bootstrap_phenomena
from explain_engine.hitl.cli_interactive import review_phenomena
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.state import CognitiveState

app = typer.Typer(help="Cognitive Engine for explanation-centric reasoning")
console = Console()


def _get_store() -> SessionStore:
    settings = Settings()
    return SessionStore(directory=settings.sessions_dir)


@app.command()
def new(question: str = typer.Argument(..., help="为什么 X 问题")) -> None:
    """启动新 session：Bootstrap + HITL 1 + 落 session。"""
    asyncio.run(_run_new(question))


async def _run_new(question: str) -> None:
    settings = Settings()
    llm = make_client(settings)

    console.print(f"\n[INFO] 调 {settings.llm_provider} ({settings.llm_model}) 生现象...")
    try:
        phenomena = await bootstrap_phenomena(question, llm)
    except Exception as exc:
        console.print(f"[red]LLM 失败: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"[INFO] 生成 {len(phenomena)} 个现象，请审查。")
    final_phenomena = review_phenomena(phenomena, console=console)

    # 落 session
    state = CognitiveState.bootstrap(question, budget=settings.default_budget)
    for p in final_phenomena:
        state.graph.add_node(p)
    meta = SessionMeta.new(question=question)
    session = Session(meta=meta, state=state)
    store = _get_store()
    store.save(session)

    console.print(f"\n[green]Session {meta.session_id} 已保存。[/green]")
    console.print(f"       下一步：explain show {meta.session_id}")


@app.command()
def show(session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)")) -> None:
    """显示某 session 的 phenomena."""
    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"\n[bold]Session {session.meta.session_id}[/bold]")
    console.print(f"问题: {session.meta.question}")
    console.print(f"Stage: {session.meta.stage}")
    console.print(f"现象数: {len(session.state.graph.nodes)}\n")

    table = Table(title="Phenomena")
    table.add_column("ID", style="cyan")
    table.add_column("名称", style="bold")
    table.add_column("描述", style="dim")
    table.add_column("Level", justify="right")
    table.add_column("Epistemic")
    for nid, node in session.state.graph.nodes.items():
        table.add_row(
            nid, node.name, node.description,
            str(node.abstraction_level), node.epistemic,
        )
    console.print(table)


@app.command(name="list")
def list_cmd() -> None:
    """列出所有 session."""
    store = _get_store()
    metas = store.list()

    table = Table(title="Sessions")
    table.add_column("ID", style="cyan")
    table.add_column("问题", style="bold")
    table.add_column("Stage")
    table.add_column("Created")
    for m in metas:
        from datetime import datetime
        ts = datetime.fromtimestamp(m.created_at).strftime("%Y-%m-%d %H:%M")
        table.add_row(m.session_id, m.question, m.stage, ts)
    console.print(table)


if __name__ == "__main__":
    app()
```

---

## 4. 数据流

```
$ explain new "为什么年轻人不消费"
    ↓
Settings 加载 (.env)
    ↓
make_client(settings) → ClaudeClient
    ↓
bootstrap_phenomena(question, llm)
    ↓ structured output via variable_extraction.yaml
    ↓ List[VariableNode] (12 个，level=0)
    ↓
review_phenomena(phenomena)   # HITL 1
    ↓ 用户 keep/edit/drop 12 次 + 末尾 add 循环
    ↓ List[VariableNode] (最终 10 条)
    ↓
CognitiveState.bootstrap(question, budget) + 把 phenomena 灌入 graph
SessionMeta.new(question) → "s_xxxxxxxx"
Session(meta, state)
    ↓
SessionStore.save(session) → ./sessions/s_xxxxxxxx.json
    ↓
console.print: "Session s_xxxxxxxx 已保存"
```

---

## 5. Stage transition

Phase 3 跑完后 `meta.stage` 仍是 `"bootstrap_pending"` —— 含义重新解读为"HITL 1 已完成但 reasoning loop 未启动"。

**Phase 4 brainstorm 时**统一调整 Stage Literal，可能引入：
- `"bootstrap_complete"` → `"running"` → `"finalize_pending"` → `"done"`

Phase 3 **不动 schema**。Stage Literal 保持现状 4 个值。

---

## 6. Error handling

| 错误 | 处理 |
|---|---|
| LLM 网络/API 错误 | tenacity retry 3 次（exponential backoff），失败抛 ValueError + 中文消息 |
| LLM 输出 schema 不合法 | tenacity retry 1 次（同 prompt），仍失败抛错 |
| LLM 出现象 < 5 个 | console.print warn，但接受（用户 HITL 可补） |
| LLM 出现象 > 15 个 | 截断到前 15 个 |
| 用户 HITL Ctrl-C | Python 默认 KeyboardInterrupt 冒泡，console 给一行 "中断，未保存"，exit 130 |
| 用户输入空名 / 非法 keep/edit/drop | rich.Prompt.ask 自动 re-prompt |
| `explain show <id>` id 不存在 | console.print FileNotFoundError 消息，exit 1 |
| `.env` 缺 API key | make_client 已经抛 ValueError("ANTHROPIC_API_KEY required ..."), exit 1 |

注：HITL Ctrl-C **不落 session**。理由：HITL 中途状态半成品，重启 reuse 没意义。用户重跑 `explain new` 即可。**v0.2 可加 "中途落地 + continue"**。

---

## 7. 测试策略

### L1 Unit tests (mock LLM)

`tests/test_engines_bootstrap.py`:
- `test_bootstrap_returns_phenomena_with_defaults` — 验证 id 顺序 / level=0 / confidence=0.7 / epistemic="observation"
- `test_bootstrap_assigns_sequential_ids` — p_001, p_002, ..., p_NNN
- `test_bootstrap_truncates_over_15` — LLM 返 20 个，系统截到 15
- `test_bootstrap_raises_if_no_parsed` — LLM resp.parsed=None
- `test_bootstrap_calls_llm_with_correct_prompt` — verify Message content
- `test_bootstrap_passes_schema_to_llm` — verify schema= 参数

`tests/test_llm_prompts_loader.py`:
- `test_load_prompt_parses_yaml`
- `test_load_prompt_not_found_raises`

### L2 HITL tests (mock stdin via rich.console + monkeypatch)

`tests/test_hitl_cli_interactive.py`:
- `test_review_keep_all` — 全 keep
- `test_review_drop_some` — drop 几条
- `test_review_edit_one` — edit 一条 (name + description 都改)
- `test_review_add_new_phenomena` — 用户加 2 条新
- `test_review_add_skip_when_empty_name` — 用户加时空名，跳出循环

Mock 策略：用 `monkeypatch` 替换 `Prompt.ask` 返回预设序列。

### L3 CLI integration tests

`tests/test_cli.py`:
- `test_cli_new_full_flow` — mock LLM + mock HITL + verify session 落地内容
- `test_cli_show_existing_session` — fixture: 预存 session.json，调 show 验证输出含问题/phenomena
- `test_cli_show_missing_id_exits_1`
- `test_cli_list_empty`
- `test_cli_list_with_sessions` — 预存 3 个 session，verify rich table 含 3 行

Mock 用 typer.testing.CliRunner + 预 patch LLMClient.

### L4 Integration (skipped by default)

- `@pytest.mark.integration` — 真 LLM call，手动 smoke

预计新增测试 ~25 个，total 73 + 25 = 98 个。

---

## 8. 验收标准

- [ ] `explain new "..."` 命令能跑通（mock LLM smoke 测试通过）
- [ ] HITL 1 多轮对话 keep/edit/drop/add 全 path 测试通过
- [ ] `explain show <id>` 显示正确 phenomena table
- [ ] `explain list` 按 created_at 降序显示
- [ ] L1+L2+L3 测试 ≥25 个全 PASS
- [ ] Phase 0+1+2 73 测试不破
- [ ] ruff check src/ tests/ 0 error
- [ ] 手动跑一次真 LLM (Claude / OpenAI / DeepSeek 任选)，能完成 bootstrap + HITL，session 落地 JSON 可 round-trip

---

## 9. 已知风险 / Open Questions

1. **LLM 出的现象质量未验证** — 直到真实跑过 10 个种子问题，不知道是否 wow 满足。Phase 6 会验证。
2. **HITL 1 体验是否会太繁琐** — 12 条逐条问可能让用户疲劳。如果测试发现，v0.2 可加"批量 keep all" 之类。
3. **`stage="bootstrap_pending"` 在 Phase 3 结束后表意模糊** — Phase 4 brainstorm 时统一调。
4. **`p_user_NNN` vs `p_NNN`** — 用户加的 ID 前缀只是约定，未在 schema 强制。后续 source 字段加上后清理。
5. **Prompt 是否能稳定出 8-15 条** — LLM 可能出 5 条或 25 条。已加截断 / warn，但 prompt engineering 可能要 iteration。

---

## 10. Phase 3 之后

Phase 4 brainstorm 启动条件：
- Phase 3 跑通 ≥1 个真实 session（不 mock）
- 知道 HITL 1 实际体验如何
- Phase 4 design 起点：把 BootstrapEngine 之后的 Reasoning Loop 接上
