# Explain Agent · Phase 2.C 实施计划（CLI REPL + Followup + 报告质感）

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把 agent 从"开发者跑 smoke 脚本"升级到"常驻 REPL 中连续提问 + 秒级追问"的日常工具，同时把 narrative 改为鼓励多源印证、把 ClickHouse 输出的 `symbol_id` 翻译成人类可读的"公司名(代码)"。

**Architecture:** 不动 main_graph 拓扑，新增 `cli/repl.py` REPL 入口（prompt_toolkit）+ `graph/followup.py` 单步异步函数；REPL 接管输入分发（slash 命令本地拦截 + 连续对话默认 followup）。

**Tech Stack:** Python 3.11+、`prompt_toolkit`（新增）、Phase 2.A/2.B 已有的 langgraph / pydantic / sqlalchemy / rich / pytest-asyncio。

**Phase 2.C 不做的：**
- 三级 compact 防爆（追问 token 增长慢，Phase 2.D 看情况）
- 后台 forked agent 跨 session 长期记忆（Phase 3+）
- 永久快照（snapshot_id 写入，Phase 2.D）
- connection_explorer / Lazy News Ingest / fan_out 性能（Phase 2.D）

**前置依赖：**
- Phase 2.B 完工（commit `989f20c`）
- Phase 2.C 设计文档已就绪（commit `20cb2e9`，`docs/plans/2026-05-11-explain-agent-phase2c-design.md`）
- `.env` 真实凭证已就位，Phase 2.A/2.B 所有单测通过（66 passed）

---

## 任务总览

| # | 任务 | 预计时长 |
|---|---|---|
| 1 | 添加 prompt_toolkit 依赖 + cli 目录骨架 | 20 min |
| 2 | ReplState 数据结构 + session 加载工具 | 30 min |
| 3 | Slash command 解析器 (parse_slash_command) | 40 min |
| 4 | Followup async 函数（load + LLM + 异步落盘） | 80 min |
| 5 | `/sessions` 实现（list 最近 N 条 session） | 30 min |
| 6 | `/load` 实现（切换当前 session） | 30 min |
| 7 | `/new` `/clear` `/help` `/quit` 实现 | 30 min |
| 8 | REPL 主循环装配（prompt_toolkit + dispatch） | 60 min |
| 9 | B.2 ClickHouseMarketAdapter 股票名翻译 | 30 min |
| 10 | B.3 narrative prompt 多源化引导 | 20 min |
| 11 | REPL 集成测试 + 端到端 smoke | 60 min |

**合计：约 7.5 小时纯开发，预计 1-2 个工作日完成。**

---

## Task 1: 添加 prompt_toolkit 依赖 + cli 目录骨架

**目标：** 装 prompt_toolkit，建 `cli/repl.py` 等空文件，验证 import。

**Files:**
- Modify: `pyproject.toml`（uv 会自动改）
- Modify: `pyproject.toml` 的 `[project.scripts]` 节加 `explain` 入口
- Create: `src/explain_agent/cli/repl/__init__.py`（空）
- Create: `src/explain_agent/cli/repl/state.py`（空）
- Create: `src/explain_agent/cli/repl/commands.py`（空）
- Create: `src/explain_agent/cli/repl/loop.py`（空）

**Step 1: 添加依赖**

Run:
```bash
uv add prompt_toolkit
```
Expected: 装上 prompt_toolkit 3.x，pyproject.toml 和 uv.lock 都被改。

**Step 2: 创建目录骨架**

```bash
mkdir -p src/explain_agent/cli/repl
touch src/explain_agent/cli/repl/__init__.py
touch src/explain_agent/cli/repl/state.py
touch src/explain_agent/cli/repl/commands.py
touch src/explain_agent/cli/repl/loop.py
```

**Step 3: 验证 prompt_toolkit 可 import**

Run:
```bash
uv run python -c "from prompt_toolkit import PromptSession; print('OK')"
```
Expected: 打印 `OK`，无 ImportError。

**Step 4: 加 `explain` script entry**

读 `pyproject.toml`，在 `[project.scripts]` 节增加：
```toml
[project.scripts]
explain-ingest-news = "explain_agent.cli.ingest_news:app"
explain = "explain_agent.cli.repl.loop:main"
```

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/explain_agent/cli/repl/
git commit -m "$(cat <<'EOF'
新增 prompt_toolkit 依赖与 cli/repl 目录骨架

为 Phase 2.C 的常驻 REPL 做准备。pyproject.toml 注册 explain 入口
（loop:main），实际实现在后续 task 落地。
EOF
)"
```

---

## Task 2: ReplState 数据结构 + session 加载工具

**目标：** 定义 REPL 进程内状态 `ReplState`，以及从 MySQL 加载 session（`load_session`）和列出最近 session（`list_recent_sessions`）的工具。

**Files:**
- Create: `src/explain_agent/cli/repl/state.py`（实现）
- Create: `tests/test_repl_state.py`

**Step 1: 写测试**

`tests/test_repl_state.py`：

```python
import json
from datetime import datetime
from unittest.mock import MagicMock
import pytest

from explain_agent.cli.repl.state import (
    ReplState, list_recent_sessions, load_session,
)


def test_repl_state_defaults():
    s = ReplState()
    assert s.current_session_id is None
    assert s.current_session is None
    assert s.followup_history == []


def test_list_recent_sessions_returns_summaries():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.fetchall.return_value = [
        ("s_abc", "半导体", datetime(2026, 5, 11, 19, 10), "low", 6, 0),
        ("s_def", "光伏",   datetime(2026, 5, 10, 11, 0),  "high", 6, 5),
    ]
    out = list_recent_sessions(mock_engine, limit=5)
    assert len(out) == 2
    assert out[0]["session_id"] == "s_abc"
    assert out[0]["target"] == "半导体"
    assert out[0]["confidence"] == "low"
    assert out[0]["followup_count"] == 0


def test_load_session_assembles_full_state():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    tree = {
        "target": "半导体",
        "time_window": ["2026-05-06", "2026-05-11"],
        "narrative": "测试叙事",
        "narrative_claims": [{"text": "claim1", "evidence_ids": ["e1"]}],
        "dimension_reports": {"policy": "政策报告"},
        "citations": [{"evidence_id": "e1", "url": "http://a", "source_type": "news"}],
    }
    mock_conn.execute.return_value.fetchone.return_value = (
        "s_abc", "为什么半导体涨", "cn_equity_sector_attribution",
        "半导体", "low", json.dumps(tree, ensure_ascii=False),
    )
    s = load_session(mock_engine, "s_abc")
    assert s["session_id"] == "s_abc"
    assert s["target"] == "半导体"
    assert s["narrative"] == "测试叙事"
    assert s["dimension_reports"]["policy"] == "政策报告"


def test_load_session_returns_none_when_missing():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.fetchone.return_value = None
    s = load_session(mock_engine, "s_nope")
    assert s is None
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_repl_state.py -v`
Expected: ImportError（state.py 还是空）。

**Step 3: 实现 state.py**

`src/explain_agent/cli/repl/state.py`：

```python
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text


@dataclass
class ReplState:
    current_session_id: str | None = None
    current_session: dict | None = None
    followup_history: list[dict] = field(default_factory=list)


def list_recent_sessions(engine, limit: int = 5) -> list[dict]:
    sql = """
    SELECT s.session_id, s.target, s.created_at, t.confidence,
           (CASE WHEN t.tree_json IS NULL THEN 0 ELSE 6 END) AS dim_count,
           (SELECT COUNT(*) FROM explain_agent.explain_followup_history f
             WHERE f.session_id = s.session_id) AS followup_count
    FROM explain_agent.explain_session s
    LEFT JOIN explain_agent.explain_evidence_tree t USING (session_id)
    ORDER BY s.created_at DESC
    LIMIT :limit
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"limit": limit}).fetchall()
    return [
        {
            "session_id": r[0],
            "target": r[1],
            "created_at": r[2],
            "confidence": r[3],
            "dim_count": r[4],
            "followup_count": r[5],
        }
        for r in rows
    ]


def load_session(engine, session_id: str) -> dict | None:
    sql = """
    SELECT s.session_id, s.raw_question, s.domain_id, s.target,
           t.confidence, t.tree_json
    FROM explain_agent.explain_session s
    LEFT JOIN explain_agent.explain_evidence_tree t USING (session_id)
    WHERE s.session_id = :sid
    """
    with engine.connect() as conn:
        row = conn.execute(text(sql), {"sid": session_id}).fetchone()
    if row is None:
        return None
    tree = json.loads(row[5]) if row[5] else {}
    return {
        "session_id": row[0],
        "raw_question": row[1],
        "domain_id": row[2],
        "target": row[3] or tree.get("target"),
        "confidence": row[4],
        "time_window": tree.get("time_window"),
        "narrative": tree.get("narrative", ""),
        "narrative_claims": tree.get("narrative_claims", []),
        "dimension_reports": tree.get("dimension_reports", {}),
        "citations": tree.get("citations", []),
        "market_facts": tree.get("market_facts", {}),
    }
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_repl_state.py -v`
Expected: 4 passed。

**Step 5: Commit**

```bash
git add src/explain_agent/cli/repl/state.py tests/test_repl_state.py
git commit -m "$(cat <<'EOF'
新增 ReplState + session 加载工具

ReplState 是 REPL 进程内的轻量状态（dataclass，无 IO）。
load_session 从 explain_session + explain_evidence_tree join 出
完整 session 快照；list_recent_sessions 给 /sessions 命令用。
EOF
)"
```

---

## Task 3: Slash command 解析器

**目标：** 实现 `parse_slash_command(input) -> SlashCommand`，把 `/new 光伏怎么了` 解析成 `("new", "光伏怎么了")`。

**Files:**
- Create: `src/explain_agent/cli/repl/commands.py`（实现 parse 部分）
- Create: `tests/test_repl_commands.py`

**Step 1: 写测试**

`tests/test_repl_commands.py`：

```python
import pytest
from explain_agent.cli.repl.commands import (
    parse_slash_command, SlashCommand, SlashCommandError,
)


def test_parse_new_with_question():
    cmd = parse_slash_command("/new 为什么半导体涨")
    assert cmd.name == "new"
    assert cmd.arg == "为什么半导体涨"


def test_parse_load_with_session_id():
    cmd = parse_slash_command("/load s_abc123")
    assert cmd.name == "load"
    assert cmd.arg == "s_abc123"


def test_parse_sessions_no_arg():
    cmd = parse_slash_command("/sessions")
    assert cmd.name == "sessions"
    assert cmd.arg == ""


def test_parse_clear_help_quit_exit():
    assert parse_slash_command("/clear").name == "clear"
    assert parse_slash_command("/help").name == "help"
    assert parse_slash_command("/quit").name == "quit"
    assert parse_slash_command("/exit").name == "quit"  # exit 是 quit 的别名


def test_parse_unknown_command_raises():
    with pytest.raises(SlashCommandError):
        parse_slash_command("/foobar")


def test_parse_trims_whitespace():
    cmd = parse_slash_command("  /new   半导体  ")
    assert cmd.name == "new"
    assert cmd.arg == "半导体"
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_repl_commands.py -v`
Expected: ImportError。

**Step 3: 实现**

`src/explain_agent/cli/repl/commands.py`：

```python
from dataclasses import dataclass


class SlashCommandError(ValueError):
    pass


@dataclass
class SlashCommand:
    name: str
    arg: str


_ALIASES = {"exit": "quit"}
_KNOWN = {"new", "sessions", "load", "clear", "help", "quit"}


def parse_slash_command(raw: str) -> SlashCommand:
    line = raw.strip()
    if not line.startswith("/"):
        raise SlashCommandError(f"not a slash command: {raw}")
    body = line[1:].strip()
    if not body:
        raise SlashCommandError("empty slash command")
    parts = body.split(maxsplit=1)
    name = parts[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    name = _ALIASES.get(name, name)
    if name not in _KNOWN:
        raise SlashCommandError(f"unknown command: /{name}")
    return SlashCommand(name=name, arg=arg)
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_repl_commands.py -v`
Expected: 6 passed。

**Step 5: Commit**

```bash
git add src/explain_agent/cli/repl/commands.py tests/test_repl_commands.py
git commit -m "$(cat <<'EOF'
新增 slash command 解析器

支持的命令：/new /sessions /load /clear /help /quit /exit。
exit 作为 quit 别名。未知命令抛 SlashCommandError 让上层显示帮助。
EOF
)"
```

---

## Task 4: Followup async 函数

**目标：** 实现 `run_followup(session, history, question, llm, engine)`：拼接上下文 → strong LLM 单次调用 → 后台 `asyncio.create_task` 落盘到 `explain_followup_history`。

**Files:**
- Create: `src/explain_agent/graph/followup.py`
- Create: `tests/test_followup.py`

**Step 1: 写测试**

`tests/test_followup.py`：

```python
import asyncio
import json
from datetime import datetime
from unittest.mock import MagicMock
import pytest

from explain_agent.graph.followup import run_followup, _build_followup_prompt


@pytest.mark.asyncio
async def test_run_followup_returns_answer_and_triggers_persist(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.chat.return_value = "政策面主要是 ..."

    persisted = []

    async def fake_persist(engine, session_id, q, a):
        persisted.append((session_id, q, a))

    monkeypatch.setattr(
        "explain_agent.graph.followup._persist_followup", fake_persist
    )

    session = {
        "session_id": "s_abc",
        "target": "半导体",
        "time_window": ["2026-05-06", "2026-05-11"],
        "narrative": "半导体上涨主因 ...",
        "narrative_claims": [{"text": "claim1", "evidence_ids": ["e1"]}],
        "dimension_reports": {"policy": "政策维度详细", "industry_chain": "..."},
        "citations": [
            {"evidence_id": "e1", "url": "http://a", "source_type": "news"},
        ],
        "market_facts": {"snippet": "板块涨 5%"},
    }
    out = await run_followup(
        session=session,
        history=[],
        question="政策面具体是什么？",
        llm=fake_llm,
        engine=MagicMock(),
    )
    assert out["answer"] == "政策面主要是 ..."
    assert out["session_id"] == "s_abc"
    # 等后台任务完成
    await asyncio.sleep(0.05)
    assert len(persisted) == 1
    assert persisted[0][1] == "政策面具体是什么？"


@pytest.mark.asyncio
async def test_followup_falls_back_when_llm_raises(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.chat.side_effect = RuntimeError("network down")

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(
        "explain_agent.graph.followup._persist_followup", noop
    )

    session = {
        "session_id": "s_abc", "target": "X",
        "time_window": ["2026-05-06", "2026-05-11"],
        "narrative": "", "narrative_claims": [],
        "dimension_reports": {}, "citations": [], "market_facts": {},
    }
    out = await run_followup(
        session=session, history=[], question="?",
        llm=fake_llm, engine=MagicMock(),
    )
    assert out["answer"].startswith("（追问失败")


def test_build_followup_prompt_truncates_citations():
    session = {
        "session_id": "s_a", "target": "半导体",
        "time_window": ["2026-05-06", "2026-05-11"],
        "narrative": "n", "narrative_claims": [],
        "dimension_reports": {f"d{i}": "report" for i in range(6)},
        "citations": [
            {"evidence_id": f"e{i}", "url": f"u{i}",
             "source_type": "news", "snippet": "x" * 500}
            for i in range(40)
        ],
        "market_facts": {"snippet": "锚点"},
    }
    user = _build_followup_prompt(session, history=[], question="why?")
    # top-20 citations 而已
    assert user.count("evidence_id") <= 20
    # 包含 6 维报告
    for i in range(6):
        assert f"d{i}" in user
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_followup.py -v`
Expected: ImportError。

**Step 3: 实现**

`src/explain_agent/graph/followup.py`：

```python
import asyncio
import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import text

from explain_agent.llm import LLMClient


FOLLOWUP_SYSTEM = """你是金融归因 agent 的追问回答器。基于已生成的 6 维归因报告
和历史追问，针对用户的新问题给出 150-400 字的回答。

要求:
- 优先引用 dimension_reports 中已有的论据,不要编造新数据
- 用 [e_xxx] 格式标注引用的 evidence_id
- 如果问题完全跳出当前 target 的范围（如用户已问半导体却来问光伏），
  在回答开头明确提示："此问题已超出当前会话的 {target} 主题, 建议 /new 开新会话"
- 不预测/不推荐操作

直接输出回答文本。
"""


def _build_followup_prompt(session: dict, history: list[dict], question: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    citations_text = "\n".join(
        f"  - [{c.get('evidence_id')}] ({c.get('source_type')}) "
        f"{(c.get('snippet') or '')[:200]}"
        for c in (session.get("citations") or [])[:20]
    )
    history_text = "\n".join(
        f"Q: {h['question']}\nA: {h['answer']}"
        for h in history[-5:]
    ) or "（无历史追问）"
    dim_reports_text = "\n\n".join(
        f"## {dim_id}\n{report}"
        for dim_id, report in session.get("dimension_reports", {}).items()
    ) or "（无维度报告）"
    return (
        f"当前时间: {now}\n"
        f"会话标的: {session.get('target')}\n"
        f"时间窗: {session.get('time_window')}\n"
        f"市场锚点: {(session.get('market_facts') or {}).get('snippet', '')}\n\n"
        f"=== 原始叙事 ===\n{session.get('narrative', '')}\n\n"
        f"=== 6 维度报告 ===\n{dim_reports_text}\n\n"
        f"=== 引用证据 (top-20) ===\n{citations_text}\n\n"
        f"=== 追问历史 (最近 5 轮) ===\n{history_text}\n\n"
        f"=== 当前新问题 ===\n{question}"
    )


async def run_followup(
    session: dict,
    history: list[dict],
    question: str,
    llm: LLMClient,
    engine,
) -> dict:
    user = _build_followup_prompt(session, history, question)
    try:
        answer = llm.chat(system=FOLLOWUP_SYSTEM, user=user, max_tokens=2000)
    except Exception as e:
        answer = f"（追问失败：{e!s}。请重试或换问法）"
        return {"answer": answer, "session_id": session["session_id"]}

    asyncio.create_task(_persist_followup(engine, session["session_id"], question, answer))
    return {"answer": answer, "session_id": session["session_id"]}


async def _persist_followup(engine, session_id: str, question: str, answer: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO explain_agent.explain_followup_history
                      (followup_id, session_id, question, answer, created_at)
                    VALUES (:fid, :sid, :q, :a, :ts)
                    """
                ),
                {
                    "fid": f"f_{uuid4().hex[:12]}",
                    "sid": session_id,
                    "q": question,
                    "a": answer,
                    "ts": datetime.now(),
                },
            )
    except Exception:
        pass  # 后台失败不影响主流程
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_followup.py -v`
Expected: 3 passed。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/followup.py tests/test_followup.py
git commit -m "$(cat <<'EOF'
新增 followup 节点（inline async, 不复用 main graph）

run_followup 拼接 6 维报告 + top-20 citation + 最近 5 轮 Q/A 作为
strong LLM 上下文，单步调用拿回答。LLM 失败时回退到错误提示文本而非
抛异常。落盘用 asyncio.create_task 后台触发，不阻塞用户继续提问。
EOF
)"
```

---

## Task 5: `/sessions` 命令实现

**目标：** REPL 收到 `/sessions` 时，调用 `list_recent_sessions(engine)` 并 Rich 表格打印。

**Files:**
- Modify: `src/explain_agent/cli/repl/commands.py`（新增 `handle_sessions` 函数）
- Modify: `tests/test_repl_commands.py`

**Step 1: 在 `tests/test_repl_commands.py` 末尾追加测试**

```python
from datetime import datetime
from unittest.mock import MagicMock
from rich.console import Console
import io


def test_handle_sessions_prints_table_when_some_exist(monkeypatch):
    from explain_agent.cli.repl.commands import handle_sessions

    fake_sessions = [
        {
            "session_id": "s_abc", "target": "半导体",
            "created_at": datetime(2026, 5, 11, 19, 10),
            "confidence": "low", "dim_count": 6, "followup_count": 0,
        },
    ]
    monkeypatch.setattr(
        "explain_agent.cli.repl.commands.list_recent_sessions",
        lambda engine, limit=10: fake_sessions,
    )
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    handle_sessions(engine=MagicMock(), console=console)
    out = buf.getvalue()
    assert "s_abc" in out
    assert "半导体" in out
    assert "low" in out


def test_handle_sessions_prints_empty_message_when_none(monkeypatch):
    from explain_agent.cli.repl.commands import handle_sessions
    monkeypatch.setattr(
        "explain_agent.cli.repl.commands.list_recent_sessions",
        lambda engine, limit=10: [],
    )
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    handle_sessions(engine=MagicMock(), console=console)
    out = buf.getvalue()
    assert "无历史 session" in out or "no session" in out.lower()
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_repl_commands.py -k handle_sessions -v`
Expected: ImportError（handle_sessions 不存在）。

**Step 3: 实现 handle_sessions**

修改 `src/explain_agent/cli/repl/commands.py`，在末尾追加：

```python
from rich.console import Console
from rich.table import Table

from explain_agent.cli.repl.state import list_recent_sessions


def handle_sessions(engine, console: Console, limit: int = 10) -> None:
    sessions = list_recent_sessions(engine, limit=limit)
    if not sessions:
        console.print("[dim]无历史 session。输入问题即可开始新会话。[/dim]")
        return
    table = Table(title="最近 session", show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("session_id")
    table.add_column("时间")
    table.add_column("target")
    table.add_column("confidence")
    table.add_column("追问")
    for i, s in enumerate(sessions, 1):
        table.add_row(
            str(i),
            s["session_id"],
            s["created_at"].strftime("%Y-%m-%d %H:%M"),
            s["target"] or "-",
            s["confidence"] or "-",
            str(s["followup_count"]),
        )
    console.print(table)
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_repl_commands.py -v`
Expected: 8 passed（前 6 个 + 新 2 个）。

**Step 5: Commit**

```bash
git add src/explain_agent/cli/repl/commands.py tests/test_repl_commands.py
git commit -m "$(cat <<'EOF'
/sessions 命令 · Rich 表格输出最近 session

每行展示 session_id / 时间 / target / confidence / 追问次数。
list 为空时打印友好提示而非空表。
EOF
)"
```

---

## Task 6: `/load` 命令实现

**目标：** 加载指定 session，更新 ReplState；找不到时报错。

**Files:**
- Modify: `src/explain_agent/cli/repl/commands.py`（新增 `handle_load` 函数）
- Modify: `tests/test_repl_commands.py`

**Step 1: 写测试**

在 `tests/test_repl_commands.py` 末尾追加：

```python
def test_handle_load_updates_state(monkeypatch):
    from explain_agent.cli.repl.commands import handle_load
    from explain_agent.cli.repl.state import ReplState

    fake_session = {
        "session_id": "s_abc", "target": "半导体",
        "narrative": "测试叙事", "dimension_reports": {}, "citations": [],
    }
    monkeypatch.setattr(
        "explain_agent.cli.repl.commands.load_session",
        lambda engine, sid: fake_session if sid == "s_abc" else None,
    )
    state = ReplState()
    console = Console(file=io.StringIO(), force_terminal=False, width=120)
    handle_load(engine=MagicMock(), console=console, state=state, session_id="s_abc")
    assert state.current_session_id == "s_abc"
    assert state.current_session["target"] == "半导体"
    assert state.followup_history == []  # 切换 session 时清空 in-memory 历史


def test_handle_load_unknown_session(monkeypatch):
    from explain_agent.cli.repl.commands import handle_load
    from explain_agent.cli.repl.state import ReplState
    monkeypatch.setattr(
        "explain_agent.cli.repl.commands.load_session", lambda engine, sid: None,
    )
    state = ReplState()
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    handle_load(engine=MagicMock(), console=console, state=state, session_id="s_nope")
    assert state.current_session_id is None  # 未变更
    assert "找不到" in buf.getvalue() or "not found" in buf.getvalue().lower()
```

**Step 2: 实现 handle_load**

在 `src/explain_agent/cli/repl/commands.py` 末尾追加：

```python
from explain_agent.cli.repl.state import load_session, ReplState


def handle_load(engine, console: Console, state: ReplState, session_id: str) -> None:
    if not session_id:
        console.print("[red]/load 需要 session_id 参数[/red]")
        return
    session = load_session(engine, session_id)
    if session is None:
        console.print(f"[red]找不到 session: {session_id}[/red]")
        return
    state.current_session_id = session_id
    state.current_session = session
    state.followup_history = []
    console.print(
        f"[green]✓[/green] 切到 session [bold]{session_id}[/bold] "
        f"(target=[cyan]{session.get('target')}[/cyan])"
    )
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_repl_commands.py -v`
Expected: 10 passed。

**Step 4: Commit**

```bash
git add src/explain_agent/cli/repl/commands.py tests/test_repl_commands.py
git commit -m "$(cat <<'EOF'
/load 命令 · 切换当前 session 并清空 in-memory 追问历史

无参或 session 不存在时给红色提示，state 保持原值。
切换成功时打印 target 让用户确认是否对的 session。
EOF
)"
```

---

## Task 7: `/new` `/clear` `/help` `/quit` 实现

**目标：** 把剩余 4 个简单命令实现完，全部本地处理。

**Files:**
- Modify: `src/explain_agent/cli/repl/commands.py`
- Modify: `tests/test_repl_commands.py`

**Step 1: 写测试**

在 `tests/test_repl_commands.py` 末尾追加：

```python
def test_handle_clear_resets_followup_history():
    from explain_agent.cli.repl.commands import handle_clear
    from explain_agent.cli.repl.state import ReplState

    state = ReplState()
    state.current_session_id = "s_abc"
    state.current_session = {"target": "半导体"}
    state.followup_history = [{"question": "q", "answer": "a"}]

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    handle_clear(console=console, state=state)
    assert state.followup_history == []
    assert state.current_session_id == "s_abc"  # /clear 不丢 session, 只清追问


def test_handle_help_prints_command_list():
    from explain_agent.cli.repl.commands import handle_help

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    handle_help(console=console)
    out = buf.getvalue()
    for cmd in ["/new", "/sessions", "/load", "/clear", "/help", "/quit"]:
        assert cmd in out


def test_handle_quit_raises_sentinel():
    from explain_agent.cli.repl.commands import handle_quit, ReplExit

    with pytest.raises(ReplExit):
        handle_quit(console=Console(file=io.StringIO(), force_terminal=False, width=120))
```

**Step 2: 实现**

在 `src/explain_agent/cli/repl/commands.py` 末尾追加：

```python
class ReplExit(SystemExit):
    """正常退出 REPL 的哨兵异常"""


def handle_clear(console: Console, state: ReplState) -> None:
    state.followup_history = []
    console.print("[dim]✓ 已清空追问历史（当前 session 保留）[/dim]")


def handle_help(console: Console) -> None:
    console.print(
        """[bold]命令列表:[/bold]
  [cyan]/new[/cyan] [问题]       开启新 session, 走完整 6 维归因
  [cyan]/sessions[/cyan]         列最近 10 个 session
  [cyan]/load[/cyan] [session_id] 切换到指定 session 继续追问
  [cyan]/clear[/cyan]            清空当前 session 的追问历史
  [cyan]/help[/cyan]             显示此帮助
  [cyan]/quit[/cyan] / [cyan]/exit[/cyan]       退出 REPL (或 Ctrl+D)

直接输入文字, 默认作为当前 session 的追问;
当前无 session 时, 自动作为新问题 (/new)"""
    )


def handle_quit(console: Console) -> None:
    console.print("[dim]bye.[/dim]")
    raise ReplExit(0)
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_repl_commands.py -v`
Expected: 13 passed。

**Step 4: Commit**

```bash
git add src/explain_agent/cli/repl/commands.py tests/test_repl_commands.py
git commit -m "$(cat <<'EOF'
/clear /help /quit 命令实现 + ReplExit 哨兵

/clear 只清追问历史不丢 session；/help 列所有命令；/quit 抛 ReplExit
让上层主循环优雅退出。/new 由 loop 直接路由到 main graph（不在 commands
中处理）。
EOF
)"
```

---

## Task 8: REPL 主循环装配

**目标：** 写 `loop.py` 的 `main()` 函数：init 资源 → 启动列表 → prompt_toolkit 输入循环 → 分发到 main graph / followup / slash 命令。

**Files:**
- Modify: `src/explain_agent/cli/repl/loop.py`
- Create: `tests/test_repl_loop.py`

**Step 1: 写测试**

`tests/test_repl_loop.py`：

```python
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
import pytest

from explain_agent.cli.repl.loop import dispatch_input, _build_environment
from explain_agent.cli.repl.state import ReplState


@pytest.mark.asyncio
async def test_dispatch_slash_command_handled_locally(monkeypatch):
    state = ReplState()
    env = MagicMock()
    env.engine = MagicMock()
    env.console = MagicMock()
    handle_called = []

    def fake_handle_sessions(engine, console, limit=10):
        handle_called.append(("sessions", engine))

    monkeypatch.setattr(
        "explain_agent.cli.repl.loop.handle_sessions", fake_handle_sessions
    )
    await dispatch_input(state, env, "/sessions")
    assert handle_called and handle_called[0][0] == "sessions"


@pytest.mark.asyncio
async def test_dispatch_first_message_auto_new(monkeypatch):
    state = ReplState()  # 无 session
    env = MagicMock()
    env.console = MagicMock()
    env.run_main_graph = AsyncMock(
        return_value={"session_id": "s_new", "target": "半导体"}
    )
    await dispatch_input(state, env, "为什么半导体涨")
    env.run_main_graph.assert_called_once_with("为什么半导体涨")
    assert state.current_session_id == "s_new"


@pytest.mark.asyncio
async def test_dispatch_with_session_goes_followup(monkeypatch):
    state = ReplState()
    state.current_session_id = "s_abc"
    state.current_session = {"target": "半导体", "session_id": "s_abc",
                             "dimension_reports": {}, "citations": [],
                             "narrative": "", "narrative_claims": [],
                             "market_facts": {}, "time_window": []}
    env = MagicMock()
    env.console = MagicMock()
    env.run_followup = AsyncMock(return_value={"answer": "...", "session_id": "s_abc"})
    await dispatch_input(state, env, "政策是什么")
    env.run_followup.assert_called_once()
    # 追问被记入 in-memory 历史
    assert len(state.followup_history) == 1
    assert state.followup_history[0]["question"] == "政策是什么"


@pytest.mark.asyncio
async def test_dispatch_new_explicit_with_question(monkeypatch):
    state = ReplState()
    state.current_session_id = "s_old"  # 有旧 session 也要丢掉
    env = MagicMock()
    env.console = MagicMock()
    env.run_main_graph = AsyncMock(return_value={"session_id": "s_new2", "target": "光伏"})
    await dispatch_input(state, env, "/new 光伏怎么了")
    env.run_main_graph.assert_called_once_with("光伏怎么了")
    assert state.current_session_id == "s_new2"
    assert state.followup_history == []
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_repl_loop.py -v`
Expected: ImportError（loop.py 还是空）。

**Step 3: 实现 loop.py**

`src/explain_agent/cli/repl/loop.py`：

```python
import asyncio
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from explain_agent.cli.repl.commands import (
    parse_slash_command, SlashCommand, SlashCommandError, ReplExit,
    handle_sessions, handle_load, handle_clear, handle_help, handle_quit,
)
from explain_agent.cli.repl.state import ReplState, load_session


@dataclass
class ReplEnvironment:
    engine: Any
    console: Console
    run_main_graph: Any  # async callable: question -> {session_id, target, ...}
    run_followup: Any    # async callable: (session, history, question) -> {answer}


async def dispatch_input(state: ReplState, env: ReplEnvironment, raw: str) -> None:
    line = raw.strip()
    if not line:
        return

    if line.startswith("/"):
        try:
            cmd = parse_slash_command(line)
        except SlashCommandError as e:
            env.console.print(f"[red]{e}[/red] (/help 查看可用命令)")
            return
        await _run_slash(state, env, cmd)
        return

    if state.current_session_id is None:
        await _run_main(state, env, line)
        return

    await _run_followup(state, env, line)


async def _run_slash(state: ReplState, env: ReplEnvironment, cmd: SlashCommand) -> None:
    if cmd.name == "new":
        if not cmd.arg:
            env.console.print("[red]/new 需要带问题（例：/new 为什么半导体涨）[/red]")
            return
        state.current_session_id = None
        state.current_session = None
        state.followup_history = []
        await _run_main(state, env, cmd.arg)
    elif cmd.name == "sessions":
        handle_sessions(engine=env.engine, console=env.console)
    elif cmd.name == "load":
        handle_load(engine=env.engine, console=env.console, state=state, session_id=cmd.arg)
    elif cmd.name == "clear":
        handle_clear(console=env.console, state=state)
    elif cmd.name == "help":
        handle_help(console=env.console)
    elif cmd.name == "quit":
        handle_quit(console=env.console)


async def _run_main(state: ReplState, env: ReplEnvironment, question: str) -> None:
    result = await env.run_main_graph(question)
    if not result:
        env.console.print("[red]main graph 未返回有效结果[/red]")
        return
    state.current_session_id = result.get("session_id")
    # 加载完整 session
    state.current_session = load_session(env.engine, state.current_session_id)
    state.followup_history = []


async def _run_followup(state: ReplState, env: ReplEnvironment, question: str) -> None:
    out = await env.run_followup(
        session=state.current_session,
        history=state.followup_history,
        question=question,
    )
    env.console.print(f"\n{out['answer']}\n")
    state.followup_history.append({"question": question, "answer": out["answer"]})


def _build_environment() -> ReplEnvironment:
    """实际构建生产环境的 env（adapters/llm/engines/main_graph）。"""
    from explain_agent.adapters.clickhouse_market import ClickHouseMarketAdapter, IndustryResolver
    from explain_agent.adapters.mysql_fundamentals import MySQLFundamentalsAdapter
    from explain_agent.adapters.akshare_capital_flow import AkshareCapitalFlowAdapter
    from explain_agent.adapters.news_corpus import NewsCorpusAdapter
    from explain_agent.db.clickhouse import get_client as ch_client
    from explain_agent.db.mysql import get_engine
    from explain_agent.db.qdrant import get_qdrant_client
    from explain_agent.embedding.bge_m3 import get_embedder
    from explain_agent.graph.dimension_worker import DimensionWorker
    from explain_agent.graph.main_graph import build_main_graph
    from explain_agent.graph.state import new_attribution_state
    from explain_agent.graph.followup import run_followup
    from explain_agent.llm import get_strong_llm, get_weak_llm

    console = Console()
    console.print("[dim]初始化 DB / embedder ...[/dim]")
    quant_engine = get_engine("quant")
    explain_engine = get_engine("explain")
    resolver = IndustryResolver(quant_engine)
    embedder = get_embedder()
    embedder.embed(["warm up"])
    registry = {
        "clickhouse_market": ClickHouseMarketAdapter(ch_client(), resolver),
        "mysql_fundamentals": MySQLFundamentalsAdapter(quant_engine, resolver),
        "akshare_capital_flow": AkshareCapitalFlowAdapter(),
        "news_corpus": NewsCorpusAdapter(
            qdrant=get_qdrant_client(), embedder=embedder, engine=explain_engine,
        ),
    }
    weak = get_weak_llm()
    strong = get_strong_llm()

    def worker_factory(dimension_config, worker_config):
        return DimensionWorker(
            dimension_config=dimension_config, worker_config=worker_config,
            llm=weak, adapter_registry=registry,
        )
    graph = build_main_graph(
        market_adapter=registry["clickhouse_market"],
        worker_factory=worker_factory,
        weak_llm=weak, strong_llm=strong, engine=explain_engine,
    )

    async def _run_main_graph(question: str) -> dict:
        st = new_attribution_state(question)
        return await graph.ainvoke(st)

    async def _run_followup_wrapper(session, history, question):
        return await run_followup(
            session=session, history=history, question=question,
            llm=strong, engine=explain_engine,
        )

    console.print("[green]✓[/green] ready.")
    return ReplEnvironment(
        engine=explain_engine, console=console,
        run_main_graph=_run_main_graph, run_followup=_run_followup_wrapper,
    )


def main() -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    env = _build_environment()
    state = ReplState()

    # 启动列表
    handle_sessions(engine=env.engine, console=env.console, limit=5)

    session_input = PromptSession(history=InMemoryHistory())

    async def _loop():
        while True:
            try:
                raw = await session_input.prompt_async("explain> ")
            except (EOFError, KeyboardInterrupt):
                env.console.print("[dim]bye.[/dim]")
                return
            try:
                await dispatch_input(state, env, raw)
            except ReplExit:
                return
            except Exception as e:
                env.console.print(f"[red]Error:[/red] {e!r}")

    asyncio.run(_loop())


if __name__ == "__main__":
    main()
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_repl_loop.py -v`
Expected: 4 passed。

**Step 5: 跑全量回归**

Run: `uv run pytest`
Expected: 全部通过。

**Step 6: Commit**

```bash
git add src/explain_agent/cli/repl/loop.py tests/test_repl_loop.py
git commit -m "$(cat <<'EOF'
REPL 主循环装配 (prompt_toolkit + 输入分发)

dispatch_input: /-前缀 → slash 命令本地处理; 无 session → 走 main
graph; 有 session → 走 followup。
_build_environment: 一次性 init 4 个 adapter + embedder + LLM +
main graph + followup wrapper, 让 REPL 进程常驻避免每次启动开销。
EOF
)"
```

---

## Task 9: B.2 ClickHouseMarketAdapter 股票名翻译

**目标：** snippet 从 `symbol_id=2332 涨跌=92.52%` 改为 `长电科技(300661) 涨跌=92.52%`。

**Files:**
- Modify: `src/explain_agent/adapters/clickhouse_market.py`
- Modify: `tests/test_clickhouse_market_adapter.py`

**Step 1: 写测试**

在 `tests/test_clickhouse_market_adapter.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_snippet_contains_company_name_and_code(monkeypatch):
    """snippet 不能含 symbol_id 数字 ID, 应为'长电科技(300661)'格式。"""
    from datetime import date
    from unittest.mock import MagicMock
    from explain_agent.adapters.clickhouse_market import (
        ClickHouseMarketAdapter, IndustryResolver,
    )
    from explain_agent.core.types import AdapterQuery

    fake_resolver = MagicMock()
    fake_resolver.resolve_industry_symbols.return_value = [2332, 1057]
    fake_resolver.resolve_symbol_meta = MagicMock(
        return_value={2332: ("300661", "长电科技"), 1057: ("002475", "立讯精密")}
    )

    mock_ch = MagicMock()
    result_obj = MagicMock()
    result_obj.result_rows = [
        (2332, 100.0, 92.52, 1e9),
        (1057, 50.0, 60.20, 5e8),
    ]
    mock_ch.query.return_value = result_obj

    adapter = ClickHouseMarketAdapter(mock_ch, fake_resolver)
    q = AdapterQuery(keywords=[], time_window=(date(2026, 5, 5), date(2026, 5, 11)), target="半导体")
    out = await adapter.query(q)

    assert len(out) == 1
    snippet = out[0].snippet
    assert "长电科技(300661)" in snippet
    assert "立讯精密(002475)" in snippet
    assert "symbol_id=" not in snippet  # 不再裸 ID
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_clickhouse_market_adapter.py::test_snippet_contains_company_name_and_code -v`
Expected: FAIL（resolver 当前没有 `resolve_symbol_meta` 方法）。

**Step 3: 实现 resolver + adapter 改造**

修改 `src/explain_agent/adapters/clickhouse_market.py`，在 `IndustryResolver` 内新增方法：

```python
class IndustryResolver:
    # ...已有方法保持不动

    def resolve_symbol_meta(self, symbol_ids: list[int]) -> dict[int, tuple[str, str]]:
        """批量查 symbol_id → (symbol_code, name)。"""
        if not symbol_ids:
            return {}
        sql = """
        SELECT symbol_id, COALESCE(code, symbol) AS code, COALESCE(name, symbol) AS name
        FROM quant_data.stock_symbol
        WHERE symbol_id IN :ids
        """
        # SQLAlchemy core 不直接支持 IN tuple, 用 text + expanding bindparam
        from sqlalchemy import text, bindparam
        stmt = text(sql).bindparams(bindparam("ids", expanding=True))
        with self.engine.begin() as conn:
            rows = conn.execute(stmt, {"ids": symbol_ids}).fetchall()
        return {r[0]: (str(r[1]), str(r[2])) for r in rows}
```

然后改 `ClickHouseMarketAdapter.query` 的 snippet 拼接部分：

```python
class ClickHouseMarketAdapter:
    # ...
    async def query(self, q: AdapterQuery) -> list[Evidence]:
        symbol_ids = self.resolver.resolve_industry_symbols(q.target)
        if not symbol_ids:
            return []
        sql = """
        SELECT
            symbol_id,
            argMax(close, trade_date) AS last_close,
            (argMax(close, trade_date) / argMin(close, trade_date) - 1) * 100 AS pct_chg,
            sum(amount_k) * 1000 AS total_amount
        FROM stock_bar_1d
        WHERE symbol_id IN %(ids)s
          AND trade_date BETWEEN %(start)s AND %(end)s
        GROUP BY symbol_id
        ORDER BY pct_chg DESC
        LIMIT 20
        """
        result = self.ch.query(
            sql,
            parameters={
                "ids": tuple(symbol_ids),
                "start": q.time_window[0],
                "end": q.time_window[1],
            },
        )
        rows = result.result_rows
        if not rows:
            return []

        leaders = rows[:3]
        # 翻译 symbol_id 为 name(code)
        meta = self.resolver.resolve_symbol_meta([int(r[0]) for r in leaders])
        leader_parts = []
        for r in leaders:
            sid = int(r[0])
            code, name = meta.get(sid, (str(sid), f"id={sid}"))
            leader_parts.append(f"{name}({code}) 涨跌={r[2]:.2f}%")
        leader_snippet = "; ".join(leader_parts)
        return [
            Evidence(
                id=str(uuid4()),
                source=self.name,
                source_type="market_data",
                snippet=f"{q.target} 板块 {q.time_window[0]}~{q.time_window[1]} 龙头股: {leader_snippet}",
                raw_payload={"rows": [list(r) for r in rows]},
                timestamp=datetime.now(),
                metadata={"target": q.target, "kind": "industry_leaders"},
            )
        ]
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_clickhouse_market_adapter.py -v`
Expected: 全部通过（原有的不破 + 新的通过）。

**Step 5: Commit**

```bash
git add src/explain_agent/adapters/clickhouse_market.py tests/test_clickhouse_market_adapter.py
git commit -m "$(cat <<'EOF'
ClickHouseMarketAdapter · snippet 把 symbol_id 翻译成"公司名(代码)"

Phase 2.B smoke 暴露的可读性问题：维度报告里出现 "symbol_id=1057
涨跌=0.00%" 这种黑话。新增 IndustryResolver.resolve_symbol_meta
批量查 (code, name)，snippet 拼接改为 "长电科技(300661) 涨跌=..."。
EOF
)"
```

---

## Task 10: B.3 narrative prompt 多源化引导

**目标：** 在 `NARRATIVE_SYSTEM` 中加一句鼓励 claims 引用不同 source_type，提升 confidence。

**Files:**
- Modify: `src/explain_agent/graph/nodes/report_builder.py:NARRATIVE_SYSTEM`
- Modify: `tests/test_node_report_builder.py`

**Step 1: 写测试（确保 prompt 含关键字）**

在 `tests/test_node_report_builder.py` 末尾追加：

```python
def test_narrative_system_prompt_encourages_multi_source():
    """NARRATIVE_SYSTEM 必须显式提醒 LLM 鼓励多 source_type 引用。"""
    from explain_agent.graph.nodes.report_builder import NARRATIVE_SYSTEM
    assert "source_type" in NARRATIVE_SYSTEM
    # 必须显式列举多种 source 鼓励 LLM 跨源引用
    for stype in ("news", "market_data", "capital_flow"):
        assert stype in NARRATIVE_SYSTEM
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_node_report_builder.py::test_narrative_system_prompt_encourages_multi_source -v`
Expected: FAIL（当前 prompt 没提 source_type）。

**Step 3: 改 prompt**

修改 `src/explain_agent/graph/nodes/report_builder.py` 的 `NARRATIVE_SYSTEM`，在已有的"要求:"段落末尾追加一行：

```python
NARRATIVE_SYSTEM = """你是审慎的金融研究员。基于以下六维证据池，写一段 80-150 字的归因叙事。

输出格式 (JSON):
{
  "claims": [
    {"text": "一句话(15-40 字)", "evidence_ids": ["e_xxx", ...]}
  ]
}

要求:
- 每个 claim 必须挂 ≥1 个 evidence_id, evidence_id 必须来自给定证据池
- claim 之间逻辑连贯,可读为一段完整叙事
- 不出现数据(涨跌幅/金额/百分比)若该数据未在引用证据中出现
- 不预测/推荐操作
- 整体长度 80-150 字
- 鼓励：claims 引用的 evidence 来自不同 source_type（news / market_data /
  capital_flow / policy 等），多源印证比单源更可信
只输出 JSON。
"""
```

**Step 4: 跑测试 + 回归**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: 全部通过（11 个）。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/nodes/report_builder.py tests/test_node_report_builder.py
git commit -m "$(cat <<'EOF'
narrative prompt · 鼓励 claims 跨 source_type 引用以提升 confidence

Phase 2.B smoke 中 confidence=low 是因为 narrative 几乎只引用 news
单源（source_type 多样性 < 2）。在 prompt 中显式列出 news /
market_data / capital_flow / policy 鼓励 LLM 跨源印证。
EOF
)"
```

---

## Task 11: REPL 集成测试 + 端到端 smoke

**目标：** 完整模拟用户输入序列跑 REPL 集成测试（mock LLM），并写一个 smoke 脚本用真实凭证跑通"问 → /sessions → /quit"流程。

**Files:**
- Create: `tests/test_repl_integration.py`
- Create: `scripts/run_repl_smoke.py`

**Step 1: 集成测试**

`tests/test_repl_integration.py`：

```python
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest

from explain_agent.cli.repl.loop import dispatch_input, ReplEnvironment
from explain_agent.cli.repl.state import ReplState


@pytest.mark.asyncio
async def test_full_session_flow(monkeypatch):
    """模拟: 启动 -> 问新问题 -> 追问 -> /sessions -> /clear -> /quit"""
    state = ReplState()
    env = ReplEnvironment(
        engine=MagicMock(),
        console=MagicMock(),
        run_main_graph=AsyncMock(return_value={"session_id": "s_new", "target": "半导体"}),
        run_followup=AsyncMock(return_value={"answer": "政策面 ...", "session_id": "s_new"}),
    )

    # 第一句话 → main graph
    monkeypatch.setattr(
        "explain_agent.cli.repl.loop.load_session",
        lambda engine, sid: {"session_id": sid, "target": "半导体", "dimension_reports": {},
                             "citations": [], "narrative": "", "narrative_claims": [],
                             "market_facts": {}, "time_window": []},
    )
    await dispatch_input(state, env, "为什么半导体涨")
    env.run_main_graph.assert_called_once()
    assert state.current_session_id == "s_new"

    # 第二句 → followup
    await dispatch_input(state, env, "政策面具体是什么")
    env.run_followup.assert_called_once()
    assert len(state.followup_history) == 1

    # /sessions
    monkeypatch.setattr(
        "explain_agent.cli.repl.commands.list_recent_sessions",
        lambda engine, limit=10: [{
            "session_id": "s_new", "target": "半导体",
            "created_at": datetime(2026, 5, 11, 19, 10),
            "confidence": "medium", "dim_count": 6, "followup_count": 1,
        }],
    )
    await dispatch_input(state, env, "/sessions")

    # /clear → 不丢 session, 但清追问
    await dispatch_input(state, env, "/clear")
    assert state.current_session_id == "s_new"
    assert state.followup_history == []

    # /quit
    from explain_agent.cli.repl.commands import ReplExit
    with pytest.raises(ReplExit):
        await dispatch_input(state, env, "/quit")
```

**Step 2: 跑测试**

Run: `uv run pytest tests/test_repl_integration.py -v`
Expected: 1 passed。

**Step 3: 写 smoke 脚本**

`scripts/run_repl_smoke.py`：

```python
"""端到端 REPL smoke：模拟 4-5 个真实输入跑通 main graph + followup + /sessions。

直接运行: uv run python scripts/run_repl_smoke.py
"""
import asyncio

from rich.console import Console

from explain_agent.cli.repl.loop import _build_environment, dispatch_input
from explain_agent.cli.repl.state import ReplState


SCRIPTED_INPUTS = [
    "为什么半导体板块今天涨",       # 1. 跑 main graph (~10 min)
    "政策面具体是哪些政策？",       # 2. followup (~10s)
    "长电科技为什么涨停？",         # 3. followup
    "/sessions",                    # 4. 列 session
    "/clear",                       # 5. 清追问
    "AI 算力对板块的拉动有多大？",  # 6. followup (清空后从 session 主报告再来)
    "/quit",
]


async def main():
    console = Console()
    env = _build_environment()
    state = ReplState()
    for inp in SCRIPTED_INPUTS:
        console.print(f"\n[bold cyan]> {inp}[/bold cyan]")
        try:
            await dispatch_input(state, env, inp)
        except SystemExit:
            break
    console.print("\n[green]smoke done.[/green]")


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 4: 跑全量回归确认无破**

Run: `uv run pytest`
Expected: 全部通过（含 Phase 2.A/2.B 回归）。

**Step 5: 跑真实 smoke（手动验收）**

```bash
PYTHONUNBUFFERED=1 uv run python -u scripts/run_repl_smoke.py
```

**验收标准：**
- 启动列出最近 session 列表
- 第一个问题跑通 main graph 落库新 session
- 后续 2 个追问 ≤10s 响应，引用 `[e_xxx]`
- `/sessions` 能看到刚才新建的 session
- `/clear` 后继续追问仍能基于 session 的核心报告
- `/quit` 优雅退出

**Step 6: 更新 README 验收清单**

在 README 末尾追加：

```markdown

## Phase 2.C 验收状态

- [x] CLI REPL（prompt_toolkit + 启动列表 + slash command 本地拦截）
- [x] Followup 链路（inline async + 中等 ~10K 上下文 + 后台异步落盘）
- [x] Session 管理（`/sessions` `/load` `/clear` `/new` `/help` `/quit`）
- [x] symbol_id 翻译为"公司名(代码)"格式
- [x] narrative prompt 鼓励多 source_type 引用
- [x] 全部单测通过（Phase 2.A/2.B 回归不破）
- [x] 端到端 REPL smoke：4-5 个输入序列跑通
```

**Step 7: Commit**

```bash
git add tests/test_repl_integration.py scripts/run_repl_smoke.py README.md
git commit -m "$(cat <<'EOF'
Phase 2.C 集成测试 + REPL smoke 脚本 + 验收清单

集成测试覆盖完整流程: 首句→main graph、追问→followup、/sessions、
/clear、/quit。smoke 脚本用真实凭证跑 6 步输入序列。README 标注
所有验收项已完成。
EOF
)"
```

---

## 完成后

Phase 2.C 完成后的产出：

- ✅ CLI REPL（prompt_toolkit + slash command + 常驻进程，~10s 启动开销摊销）
- ✅ Followup 链路（inline async + 中等粒度上下文 + 后台异步落盘）
- ✅ Session 管理（`/sessions` `/load` `/clear` `/new` `/help` `/quit`）
- ✅ symbol_id → 公司名(代码) 翻译
- ✅ narrative 鼓励多源印证
- ✅ 单测覆盖每个改动 + 集成测试 + 端到端 smoke

**下一步：进入 Phase 2.D 实施计划**，候选范围：

- Lazy News Ingest（用户问到的板块语料不足时按需采集）
- fan_out 性能优化（max_rounds 自适应、并发度提升、缓存）
- 永久快照实现（snapshot_id 真的写入 + blob 落盘）
- connection_explorer 节点骨架（Phase 3 前置，让 agent 在 6 维归因外主动发散 ≤3 个延伸议题）

待 Phase 2.C 跑通且用户日常使用一段时间后，再做 Phase 2.D 的 brainstorm（积累真实痛点）。
