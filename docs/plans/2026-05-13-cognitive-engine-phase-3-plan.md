# Cognitive Engine Phase 3 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把 Phase 3 design 实施落地 —— BootstrapEngine + HITL 1 + CLI (new/show/list)。第一个端到端可跑的 CLI session demo。

**Architecture:** 6 个文件 (1 yaml + 5 py)。Bootstrap 调 LLM 出 8-15 个 concrete phenomena → HITL 1 多轮对话审 → 落 session。`explain show/list` 读 session JSON。

**Tech Stack:** Python 3.11+ / pydantic / typer / rich / pyyaml / pytest. Phase 0+1+2 的 schema + LLM client + session 直接复用。

**Branch:** `cognitive-engine-mvp` (latest: `56bd035` Phase 3 design)

**Design Doc:** [2026-05-13-cognitive-engine-phase-3-design.md](2026-05-13-cognitive-engine-phase-3-design.md)

---

## 任务索引

- Task 3.1 PromptLoader + `variable_extraction.yaml`
- Task 3.2 `BootstrapEngine.bootstrap_phenomena`
- Task 3.3 HITL 1 `review_phenomena`
- Task 3.4 CLI `explain show` + `explain list`
- Task 3.5 CLI `explain new`（整合 Bootstrap + HITL + save）

---

# Task 3.1: PromptLoader + variable_extraction.yaml

**Files:**
- Create: `src/explain_engine/llm/prompts/_loader.py`
- Create: `src/explain_engine/llm/prompts/variable_extraction.yaml`
- Test: `tests/test_llm_prompts_loader.py`

**Step 1: Write failing test**

`tests/test_llm_prompts_loader.py`:

```python
"""PromptLoader test."""

import pytest

from explain_engine.llm.prompts._loader import load_prompt


class TestLoadPrompt:
    def test_load_existing_prompt(self):
        prompt = load_prompt("variable_extraction")
        assert "system" in prompt
        assert "user_template" in prompt
        assert isinstance(prompt["system"], str)
        assert isinstance(prompt["user_template"], str)

    def test_user_template_has_placeholders(self):
        prompt = load_prompt("variable_extraction")
        # 必须有 question / min_count / max_count 占位符
        for placeholder in ["{question}", "{min_count}", "{max_count}"]:
            assert placeholder in prompt["user_template"], (
                f"missing placeholder {placeholder} in user_template"
            )

    def test_load_missing_prompt_raises(self):
        with pytest.raises(FileNotFoundError, match="not_a_real_prompt"):
            load_prompt("not_a_real_prompt")
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_llm_prompts_loader.py -v
```

Expected: `ModuleNotFoundError: No module named 'explain_engine.llm.prompts._loader'`.

**Step 3: Write `_loader.py` + `variable_extraction.yaml`**

`src/explain_engine/llm/prompts/_loader.py`:

```python
"""YAML prompt 模板加载器。"""

from pathlib import Path
from typing import Any

import yaml

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> dict[str, Any]:
    """加载 yaml prompt 模板，返回 dict。

    Raises:
        FileNotFoundError: prompt 文件不存在
        yaml.YAMLError: yaml 解析失败
    """
    p = _PROMPTS_DIR / f"{name}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"prompt {name} not found at {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))
```

`src/explain_engine/llm/prompts/variable_extraction.yaml`:

```yaml
description: |
  Bootstrap engine 用。输入用户问题，输出 8-15 个相关的具体现象。

system: |
  你是一个认知引擎的 Variable Extraction 模块。
  你的唯一职责：从用户的"为什么"问题中提取 8-15 个相关的具体现象。

  规则：
  1. 现象应该是可观察的、具体的、与问题直接相关的
  2. 不要给原因、不要给结论、不要做总结
  3. 每个现象用 1-2 句中文描述
  4. 现象之间应该多样化（不要把一个现象拆 5 次）
  5. 输出严格 JSON，不要额外文字

user_template: |
  问题: {question}

  请输出 {min_count}-{max_count} 个相关现象。
```

注意：`prompts/__init__.py` 可能不存在或为空。loader 是 `_` 前缀私有模块，需要包结构。先检查 `src/explain_engine/llm/prompts/__init__.py` 是否存在，没有则 `touch` 创建空文件。

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_llm_prompts_loader.py -v
```

Expected: 3 tests PASS。

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/llm/prompts/ tests/test_llm_prompts_loader.py && git commit -m "$(cat <<'EOF'
prompts · PromptLoader + variable_extraction.yaml

- _loader.py: 从 src/explain_engine/llm/prompts/ 加载 yaml 模板
- variable_extraction.yaml: Bootstrap 用，system + user_template
  (含 question/min_count/max_count 占位符)
- 3 单测覆盖加载 / 占位符存在 / 缺失抛 FileNotFoundError

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Task 3.2: BootstrapEngine

**Files:**
- Create: `src/explain_engine/engines/bootstrap.py`
- Test: `tests/test_engines_bootstrap.py`

**Step 1: Write failing test**

`tests/test_engines_bootstrap.py`:

```python
"""BootstrapEngine test."""

from unittest.mock import AsyncMock

import pytest

from explain_engine.engines.bootstrap import BootstrapOutput, bootstrap_phenomena
from explain_engine.llm.client import Response


def _mock_resp(phenomena: list[dict]) -> Response:
    return Response(
        text="",
        parsed={"phenomena": phenomena},
        model="mock",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


class TestBootstrapPhenomena:
    async def test_returns_phenomena_with_defaults(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=_mock_resp([
            {"name": "房价上涨", "description": "一线城市房价持续高位"},
            {"name": "收入停滞", "description": "工资5年无明显增长"},
        ]))

        result = await bootstrap_phenomena("why?", llm)

        assert len(result) == 2
        assert result[0].id == "p_001"
        assert result[0].name == "房价上涨"
        assert result[0].abstraction_level == 0
        assert result[0].confidence == 0.7
        assert result[0].epistemic == "observation"
        assert result[0].evidence_ids == []

    async def test_sequential_ids(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=_mock_resp([
            {"name": f"现象 {i}", "description": "x"} for i in range(5)
        ]))

        result = await bootstrap_phenomena("why?", llm)

        assert [n.id for n in result] == ["p_001", "p_002", "p_003", "p_004", "p_005"]

    async def test_truncates_over_max_count(self):
        llm = AsyncMock()
        # LLM 出 20 个，但 max_count=15
        llm.chat = AsyncMock(return_value=_mock_resp([
            {"name": f"现象 {i}", "description": "x"} for i in range(20)
        ]))

        result = await bootstrap_phenomena("why?", llm, max_count=15)

        assert len(result) == 15
        assert result[-1].id == "p_015"

    async def test_raises_when_no_parsed(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=Response(
            text="garbage",
            parsed=None,
            model="mock",
            usage={"input_tokens": 0, "output_tokens": 0},
        ))

        with pytest.raises(ValueError, match="未返回 structured output"):
            await bootstrap_phenomena("why?", llm)

    async def test_passes_schema_to_llm(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=_mock_resp([
            {"name": "x", "description": "y"}
        ]))

        await bootstrap_phenomena("why?", llm)

        kwargs = llm.chat.call_args.kwargs
        # schema 参数应该是 BootstrapOutput
        assert kwargs.get("schema") is BootstrapOutput

    async def test_user_message_contains_question_and_counts(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=_mock_resp([
            {"name": "x", "description": "y"}
        ]))

        await bootstrap_phenomena("为什么年轻人不消费", llm, min_count=10, max_count=12)

        # 第一个 positional arg 是 messages list
        messages = llm.chat.call_args.args[0]
        # 应该有一条 user message 含 question + count
        user_msg = next(m for m in messages if m.role == "user")
        assert "为什么年轻人不消费" in user_msg.content
        assert "10" in user_msg.content
        assert "12" in user_msg.content
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_engines_bootstrap.py -v
```

Expected: `ModuleNotFoundError`.

**Step 3: Write `bootstrap.py`**

`src/explain_engine/engines/bootstrap.py`:

```python
"""BootstrapEngine — Phase 3 入口。

调 variable_extraction prompt 生 8-15 个 concrete phenomena。
"""

from pydantic import BaseModel

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.nodes import VariableNode


class _PhenomenonOutput(BaseModel):
    name: str
    description: str


class BootstrapOutput(BaseModel):
    """variable_extraction prompt 的 structured output schema。"""

    phenomena: list[_PhenomenonOutput]


async def bootstrap_phenomena(
    question: str,
    llm: LLMClient,
    min_count: int = 8,
    max_count: int = 15,
) -> list[VariableNode]:
    """调 variable_extraction prompt 生 concrete phenomena。

    LLM 出 {name, description}，系统补 id / level / confidence / epistemic
    default。截断到 max_count 条。

    Raises:
        ValueError: LLM 未返回 parsed 内容
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
    phenomena = raw.phenomena[:max_count]

    return [
        VariableNode(
            id=f"p_{i + 1:03d}",
            name=p.name,
            description=p.description,
            abstraction_level=0,
            confidence=0.7,
            epistemic="observation",
        )
        for i, p in enumerate(phenomena)
    ]
```

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_engines_bootstrap.py -v
```

Expected: 6 tests PASS。

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/engines/bootstrap.py tests/test_engines_bootstrap.py && git commit -m "$(cat <<'EOF'
engines · BootstrapEngine.bootstrap_phenomena

- LLM 调 variable_extraction prompt 生 {name, description} list
- 系统补 id (p_001-p_015) / abstraction_level=0 / confidence=0.7 /
  epistemic="observation" / evidence_ids=[]
- 截断到 max_count (default 15)
- LLM 无 structured output → 抛 ValueError
- 6 单测覆盖默认填充 / 顺序 id / 截断 / 失败 / schema 传递 /
  prompt 占位符替换

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Task 3.3: HITL 1 (review_phenomena)

**Files:**
- Create: `src/explain_engine/hitl/cli_interactive.py`
- Test: `tests/test_hitl_cli_interactive.py`

**Step 1: Write failing test**

`tests/test_hitl_cli_interactive.py`:

```python
"""HITL 1 review_phenomena test."""

from collections.abc import Iterator

import pytest

from explain_engine.hitl.cli_interactive import review_phenomena
from explain_engine.schema.nodes import VariableNode


def _node(id_: str, name: str = "x", desc: str = "x") -> VariableNode:
    return VariableNode(
        id=id_,
        name=name,
        description=desc,
        abstraction_level=0,
        confidence=0.7,
        epistemic="observation",
    )


def _make_prompt_sequence(answers: list[str]) -> Iterator[str]:
    """生成 Prompt.ask 的固定回答序列。"""
    return iter(answers)


@pytest.fixture
def mock_prompts(monkeypatch):
    """提供一个工具：patch Prompt.ask 返回预设序列。

    用法：mock_prompts(["k", "k", "k"]) 表示连续 3 次 keep。
    Confirm.ask 走同一个 patch（True/False）。
    """

    def _setup(answers: list, confirm_answers: list[bool] | None = None):
        ask_iter = iter(answers)
        confirm_iter = iter(confirm_answers or [])

        def _ask(*args, **kwargs):
            return next(ask_iter)

        def _confirm(*args, **kwargs):
            try:
                return next(confirm_iter)
            except StopIteration:
                return False  # 默认 False (停止 add 循环)

        monkeypatch.setattr(
            "explain_engine.hitl.cli_interactive.Prompt.ask", _ask
        )
        monkeypatch.setattr(
            "explain_engine.hitl.cli_interactive.Confirm.ask", _confirm
        )

    return _setup


class TestReviewPhenomena:
    def test_keep_all(self, mock_prompts):
        phenomena = [_node(f"p_{i:03d}") for i in range(1, 4)]
        # 3 个 k 选择 + add 循环 False
        mock_prompts(answers=["k", "k", "k"], confirm_answers=[False])

        result = review_phenomena(phenomena)

        assert len(result) == 3
        assert [n.id for n in result] == ["p_001", "p_002", "p_003"]

    def test_drop_one(self, mock_prompts):
        phenomena = [_node(f"p_{i:03d}") for i in range(1, 4)]
        # k, d, k
        mock_prompts(answers=["k", "d", "k"], confirm_answers=[False])

        result = review_phenomena(phenomena)

        assert len(result) == 2
        assert [n.id for n in result] == ["p_001", "p_003"]

    def test_edit_one(self, mock_prompts):
        phenomena = [_node("p_001", name="old name", desc="old desc")]
        # e, "新名", "新描述", add 循环 False
        mock_prompts(answers=["e", "新名", "新描述"], confirm_answers=[False])

        result = review_phenomena(phenomena)

        assert len(result) == 1
        assert result[0].name == "新名"
        assert result[0].description == "新描述"
        assert result[0].id == "p_001"  # id 不变

    def test_drop_all(self, mock_prompts):
        phenomena = [_node(f"p_{i:03d}") for i in range(1, 4)]
        mock_prompts(answers=["d", "d", "d"], confirm_answers=[False])

        result = review_phenomena(phenomena)

        assert result == []

    def test_add_new_phenomena(self, mock_prompts):
        phenomena = [_node("p_001")]
        # keep 原 1 条 → 添加循环 True 1 次输入新名/描述 → False 退出
        mock_prompts(
            answers=["k", "新现象1", "描述1"],
            confirm_answers=[True, False],
        )

        result = review_phenomena(phenomena)

        assert len(result) == 2
        assert result[1].name == "新现象1"
        assert result[1].description == "描述1"
        # 用户加的 id 用 p_user_NNN 前缀
        assert result[1].id.startswith("p_user_")

    def test_add_skipped_when_empty_name(self, mock_prompts):
        phenomena = [_node("p_001")]
        # keep → 添加循环 True 1 次 → 空名 → 跳出
        mock_prompts(
            answers=["k", ""],
            confirm_answers=[True],
        )

        result = review_phenomena(phenomena)

        assert len(result) == 1
        assert result[0].id == "p_001"

    def test_user_added_phenomena_have_user_prefix(self, mock_prompts):
        phenomena = [_node("p_001"), _node("p_002")]
        mock_prompts(
            answers=["k", "k", "u1", "d1", "u2", "d2"],
            confirm_answers=[True, True, False],
        )

        result = review_phenomena(phenomena)

        assert len(result) == 4
        assert result[2].id == "p_user_001"
        assert result[3].id == "p_user_002"
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_hitl_cli_interactive.py -v
```

Expected: `ModuleNotFoundError`.

**Step 3: Write `cli_interactive.py`**

`src/explain_engine/hitl/cli_interactive.py`:

```python
"""HITL 1 — 多轮对话审 Bootstrap 出的 phenomena。

逐条 keep/edit/drop + 末尾 add 循环。
"""

from rich.console import Console
from rich.prompt import Confirm, Prompt

from explain_engine.schema.nodes import VariableNode


def review_phenomena(
    phenomena: list[VariableNode],
    console: Console | None = None,
) -> list[VariableNode]:
    """逐条 keep/edit/drop，末尾允许 add，返回最终保留 list。

    用户加的 phenomena id 用 p_user_NNN 前缀（NNN 从 001 起）。
    """
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
        # d: 不加入 kept

    console.print(f"\n[INFO] 审查完成。当前保留 [bold]{len(kept)}[/bold] 条现象。")

    user_count = 0
    while Confirm.ask("要添加新的现象吗？", default=False):
        new_name_raw = Prompt.ask("新名称")
        new_name = new_name_raw.strip() if new_name_raw else ""
        if not new_name:
            console.print("[dim]名称为空，结束添加。[/dim]")
            break
        new_desc_raw = Prompt.ask("新描述")
        new_desc = new_desc_raw.strip() if new_desc_raw else ""
        user_count += 1
        kept.append(
            VariableNode(
                id=f"p_user_{user_count:03d}",
                name=new_name,
                description=new_desc or new_name,  # 描述空时退回名称
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
        )

    return kept
```

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_hitl_cli_interactive.py -v
```

Expected: 7 tests PASS。

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/hitl/cli_interactive.py tests/test_hitl_cli_interactive.py && git commit -m "$(cat <<'EOF'
hitl · review_phenomena 多轮对话

- 逐条 keep/edit/drop (rich.Prompt)
- 末尾循环 add (rich.Confirm + Prompt)
- 用户加的 phenomena id 用 p_user_NNN 前缀
- 空名则跳出 add 循环
- 7 单测覆盖 keep_all / drop / edit / drop_all / add / 空名跳出 /
  user 前缀

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Task 3.4: CLI show + list (read-only)

**Files:**
- Create: `src/explain_engine/cli.py`（部分，只 show + list + app skeleton）
- Test: `tests/test_cli_show_list.py`

**Step 1: Write failing test**

`tests/test_cli_show_list.py`:

```python
"""CLI show + list test."""

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_session(question: str, sessions_dir: Path) -> str:
    """工具：在 sessions_dir 落一个 session，返回 session_id。"""
    store = SessionStore(directory=sessions_dir)
    state = CognitiveState.bootstrap(question, budget=10)
    state.graph.add_node(VariableNode(
        id="p_001",
        name="房价上涨",
        description="一线城市房价持续高位",
        abstraction_level=0,
        confidence=0.7,
        epistemic="observation",
    ))
    meta = SessionMeta.new(question=question)
    session = Session(meta=meta, state=state)
    store.save(session)
    return meta.session_id


class TestShow:
    def test_show_existing_session(self, runner, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
        # API key 任意，cli show 不调 LLM
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        sid = _make_session("why?", sessions_dir)

        result = runner.invoke(app, ["show", sid])

        assert result.exit_code == 0
        assert "why?" in result.stdout
        assert "房价上涨" in result.stdout
        assert sid in result.stdout

    def test_show_missing_session_exits_1(self, runner, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        result = runner.invoke(app, ["show", "s_deadbeef"])

        assert result.exit_code == 1


class TestList:
    def test_list_empty(self, runner, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        # 空 list 表格至少要有标题
        assert "Sessions" in result.stdout or "ID" in result.stdout

    def test_list_with_sessions(self, runner, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        _make_session("question 1", sessions_dir)
        time.sleep(0.01)  # 确保 created_at 不同，便于排序
        _make_session("question 2", sessions_dir)
        time.sleep(0.01)
        _make_session("question 3", sessions_dir)

        result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        # 3 个 session 都应该列出
        for q in ["question 1", "question 2", "question 3"]:
            assert q in result.stdout
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_cli_show_list.py -v
```

Expected: `ModuleNotFoundError: No module named 'explain_engine.cli'`.

**Step 3: Write `cli.py` (initial)**

`src/explain_engine/cli.py`:

```python
"""Explain Engine CLI 入口。

命令：
- explain new <question> — Bootstrap + HITL 1 + 落 session（Task 3.5 加）
- explain show <session_id> — 显示 session 内容
- explain list — 列出所有 session

Phase 3 v0.1。
"""

from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table

from explain_engine.config import Settings
from explain_engine.persistence.session import SessionStore

app = typer.Typer(
    help="Cognitive Engine for explanation-centric reasoning",
    no_args_is_help=True,
)
console = Console()


def _get_store() -> SessionStore:
    settings = Settings()
    return SessionStore(directory=settings.sessions_dir)


@app.command()
def show(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
) -> None:
    """显示某个 session 的 phenomena 和 metadata。"""
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
            nid,
            node.name,
            node.description,
            str(node.abstraction_level),
            node.epistemic,
        )
    console.print(table)


@app.command(name="list")
def list_cmd() -> None:
    """列出所有 session（按创建时间降序）。"""
    store = _get_store()
    metas = store.list()

    table = Table(title="Sessions")
    table.add_column("ID", style="cyan")
    table.add_column("问题", style="bold")
    table.add_column("Stage")
    table.add_column("Created")
    for m in metas:
        ts = datetime.fromtimestamp(m.created_at).strftime("%Y-%m-%d %H:%M")
        table.add_row(m.session_id, m.question, m.stage, ts)
    console.print(table)


if __name__ == "__main__":
    app()
```

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_cli_show_list.py -v
```

Expected: 4 tests PASS。

注意：`Settings()` 在 `_get_store()` 内被调用，每次 CLI 调用都重新创建。这样 `monkeypatch.setenv("SESSIONS_DIR", ...)` 才能在 test 中生效。

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/cli.py tests/test_cli_show_list.py && git commit -m "$(cat <<'EOF'
cli · show + list (read-only commands)

- typer app skeleton (no_args_is_help)
- explain show <session_id>: rich table 显示 phenomena
- explain list: rich table 列所有 session (created_at 降序)
- 4 单测覆盖 show 存在 / show 缺失退出 1 / list 空 / list 多 session

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Task 3.5: CLI new (整合 Bootstrap + HITL + save)

**Files:**
- Modify: `src/explain_engine/cli.py`（加 `new` 命令）
- Test: `tests/test_cli_new.py`

**Step 1: Write failing test**

`tests/test_cli_new.py`:

```python
"""CLI new (整合 Bootstrap + HITL + save) test."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.llm.client import Response
from explain_engine.persistence.session import SessionStore


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _mock_llm_response(phenomena: list[dict]) -> Response:
    return Response(
        text="",
        parsed={"phenomena": phenomena},
        model="mock",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    """配置 sessions_dir + API key + provider = claude."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("LLM_MODEL", "claude-test")
    return sessions_dir


@pytest.fixture
def mock_llm_chat(monkeypatch):
    """Patch make_client → mock LLM."""

    def _setup(phenomena: list[dict]):
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value=_mock_llm_response(phenomena))
        monkeypatch.setattr(
            "explain_engine.cli.make_client",
            lambda settings: mock_llm,
        )
        return mock_llm

    return _setup


@pytest.fixture
def mock_review_phenomena(monkeypatch):
    """Patch review_phenomena → 默认返回原 list（keep_all）。"""

    def _setup(keep_strategy: str = "all"):
        def _review(phenomena, console=None):
            if keep_strategy == "all":
                return phenomena
            elif keep_strategy == "none":
                return []
            elif keep_strategy == "first":
                return phenomena[:1]
            else:
                raise ValueError(f"unknown strategy: {keep_strategy}")

        monkeypatch.setattr("explain_engine.cli.review_phenomena", _review)

    return _setup


class TestCliNew:
    def test_new_full_flow_keep_all(
        self, runner, setup_env, mock_llm_chat, mock_review_phenomena
    ):
        sessions_dir = setup_env
        mock_llm_chat([
            {"name": "房价上涨", "description": "..."},
            {"name": "收入停滞", "description": "..."},
        ])
        mock_review_phenomena("all")

        result = runner.invoke(app, ["new", "为什么年轻人不消费"])

        assert result.exit_code == 0
        assert "Session" in result.stdout
        assert "已保存" in result.stdout

        # 应该有一个 session 文件落地
        json_files = list(sessions_dir.glob("s_*.json"))
        assert len(json_files) == 1

        # 加载 session 验证内容
        store = SessionStore(directory=sessions_dir)
        metas = store.list()
        assert len(metas) == 1
        loaded = store.load(metas[0].session_id)
        assert loaded.state.root_question == "为什么年轻人不消费"
        assert len(loaded.state.graph.nodes) == 2

    def test_new_drop_all(
        self, runner, setup_env, mock_llm_chat, mock_review_phenomena
    ):
        """HITL drop 全部后，session 仍然落地（含 0 phenomena）。"""
        sessions_dir = setup_env
        mock_llm_chat([{"name": "x", "description": "y"}])
        mock_review_phenomena("none")

        result = runner.invoke(app, ["new", "why?"])

        assert result.exit_code == 0
        json_files = list(sessions_dir.glob("s_*.json"))
        assert len(json_files) == 1
        store = SessionStore(directory=sessions_dir)
        loaded = store.load(store.list()[0].session_id)
        assert len(loaded.state.graph.nodes) == 0

    def test_new_llm_failure_exits_1(
        self, runner, setup_env, mock_review_phenomena, monkeypatch
    ):
        """LLM 抛错时 CLI exit 1，不落 session。"""
        sessions_dir = setup_env

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(side_effect=RuntimeError("API down"))
        monkeypatch.setattr(
            "explain_engine.cli.make_client",
            lambda settings: mock_llm,
        )
        mock_review_phenomena("all")

        result = runner.invoke(app, ["new", "why?"])

        assert result.exit_code == 1
        # 不应该有 session 落地
        json_files = list(sessions_dir.glob("s_*.json"))
        assert len(json_files) == 0
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_cli_new.py -v
```

Expected: 测试集体失败（`new` command 不存在或 import 失败）。

**Step 3: Modify `cli.py` to add `new` command**

在 `src/explain_engine/cli.py` 顶部 imports 后加：

```python
import asyncio

from explain_engine.config import make_client
from explain_engine.engines.bootstrap import bootstrap_phenomena
from explain_engine.hitl.cli_interactive import review_phenomena
from explain_engine.persistence.session import Session, SessionMeta
from explain_engine.schema.state import CognitiveState
```

在 `app = typer.Typer(...)` 之后、`show` 命令之前，加 `new`:

```python
@app.command()
def new(
    question: str = typer.Argument(..., help="为什么 X 问题"),
) -> None:
    """启动新 session：Bootstrap + HITL 1 + 落 session。"""
    asyncio.run(_run_new(question))


async def _run_new(question: str) -> None:
    settings = Settings()
    llm = make_client(settings)

    console.print(
        f"\n[INFO] 调 {settings.llm_provider} ({settings.llm_model}) 生现象..."
    )
    try:
        phenomena = await bootstrap_phenomena(question, llm)
    except Exception as exc:
        console.print(f"[red]LLM 失败: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"[INFO] 生成 {len(phenomena)} 个现象，请审查。")
    final_phenomena = review_phenomena(phenomena, console=console)

    # 构造 session
    state = CognitiveState.bootstrap(question, budget=settings.default_budget)
    for p in final_phenomena:
        state.graph.add_node(p)
    meta = SessionMeta.new(question=question)
    session = Session(meta=meta, state=state)

    store = _get_store()
    store.save(session)

    console.print(f"\n[green]Session {meta.session_id} 已保存。[/green]")
    console.print(f"       下一步：explain show {meta.session_id}")
```

完整 `cli.py` 此时含：imports + `app` + `_get_store` + `new` + `_run_new` + `show` + `list_cmd` + `if __name__`。

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_cli_new.py -v
```

Expected: 3 tests PASS。

**Step 5: Run full suite + ruff**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest 2>&1 | tail -3
```

Expected: 全部 PASS（73 baseline + 6+6+7+4+3 = 26 new ≈ 99 tests）。

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run ruff check src/ tests/ 2>&1 | tail -2
```

Expected: `All checks passed!`。

**Step 6: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/cli.py tests/test_cli_new.py && git commit -m "$(cat <<'EOF'
cli · new (Bootstrap + HITL + save)

- explain new <question>: 调 LLM 生 phenomena → HITL 1 审 → 落 session
- LLM 失败 → console error + exit 1，不落 session
- HITL 中途 keep_all / drop_all 都正常落地
- 3 单测覆盖完整链路 / drop 全部 / LLM 失败退出

Phase 3 完工：BootstrapEngine + HITL 1 + CLI new/show/list 全部接通。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Phase 3 完工验收

满足以下条件才算 Phase 3 完成：

- [ ] 5 commits added on `cognitive-engine-mvp` 分支
- [ ] `uv run pytest` 全 PASS（~99 tests）
- [ ] `uv run ruff check src/ tests/` 0 error
- [ ] `git status` 干净
- [ ] CLI 可调用：`uv run explain --help` 显示 new / show / list 三命令

**手动 smoke (可选，需真实 API key)：**

```bash
# .env 配置 ANTHROPIC_API_KEY / LLM_MODEL=claude-opus-4-7
uv run explain new "为什么年轻人不消费"
# → 应该调真实 Claude，生 8-15 个现象，进入 HITL 审查
uv run explain list
uv run explain show <session_id>
```

---

# Phase 4 brainstorm 启动条件

Phase 3 跑通 + 至少 1 个真实 LLM session 验证后，brainstorm Phase 4 (Reasoning Loop + Engines)。

Phase 4 brainstorm 时要特别处理（audit 揭示）：
1. `compression_score` metric 退化 → 重新设计
2. `CognitiveState` 加 `reasoning_trace` / 可选 `attention_map`
3. 静态 graph vs 演化的结构性 tension → 明确 graph mutation 语义
