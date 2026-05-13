# Cognitive Engine MVP — Phase 0+1+2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把 `cognitive-engine-mvp` 分支从"金融归因 agent 残骸"清理干净，立起新项目骨架（`explain_engine`），实现 schema + persistence + 三家 LLM client 抽象。Phase 3-6（Engine + Runtime + CLI + 评估）在本 plan 完成后另外 plan。

**Architecture:** Pydantic schema → JSON 落地 session → 三个 LLM provider 走同一个 `LLMClient` Protocol。本 phase 不引入 reasoning loop，不调任何 Engine —— 只立**数据 + I/O** 两层。

**Tech Stack:** Python 3.11+ / uv / pydantic 2 / networkx / anthropic / openai (兼 DeepSeek) / pytest + pytest-asyncio + pytest-mock / ruff / mypy.

**Design Doc**: [2026-05-13-cognitive-engine-mvp-design.md](2026-05-13-cognitive-engine-mvp-design.md)

---

## 任务索引

**Phase 0 — 清理 + scaffold**
- Task 0.1 删除旧金融 agent 代码
- Task 0.2 重写 pyproject.toml
- Task 0.3 创建新项目目录结构
- Task 0.4 重写 README.md 和 .env.example
- Task 0.5 重置 tests/conftest.py 和 pytest 配置

**Phase 1 — Schema + Persistence**
- Task 1.1 `VariableNode`
- Task 1.2 `RelationEdge`
- Task 1.3 `ExplanationGraph`（networkx 包装）
- Task 1.4 `CognitiveState`
- Task 1.5 `Session` 落地（JSON I/O）

**Phase 2 — LLM Client 抽象 + 三 provider**
- Task 2.1 `LLMClient` Protocol + `Message`/`Response` 类型
- Task 2.2 `ClaudeClient` 实现
- Task 2.3 `OpenAIClient` 实现
- Task 2.4 `DeepSeekClient` 实现
- Task 2.5 `config.py` + `make_client` factory

---

# Phase 0 — 清理 + scaffold

## Task 0.1: 删除旧金融 agent 代码

**Files:**
- Delete: `src/explain_agent/`（整个目录）
- Delete: `tests/`（整个目录，新 phase 自己重写）
- Delete: `migrations/`
- Delete: `frameworks/`
- Delete: `data/`
- Delete: `scripts/`
- Delete: `uv.lock`（下个 task 会重新生成）

**Step 1: Verify clean state on `cognitive-engine-mvp` branch**

```bash
git -C /Users/jinziguan/Desktop/explain_everything status
```

Expected:
```
位于分支 cognitive-engine-mvp
无文件要提交，干净的工作区。
```

如果不是这个状态，停下，**不要继续**。

**Step 2: Delete directories**

```bash
cd /Users/jinziguan/Desktop/explain_everything && rm -rf src/explain_agent tests migrations frameworks data scripts uv.lock
```

**Step 3: Verify deletion**

```bash
ls /Users/jinziguan/Desktop/explain_everything
```

Expected（应该剩下）:
```
README.md  docs/  pyproject.toml  设计哲学.md  需求设计.md  技术设计.md  技术设计v1.md
```
（还有 `.env`, `.env.example`, `.gitignore`, `.python-version`, `.venv/`, `.claude/`, `.git/`）

**Step 4: Stage deletion**

```bash
git -C /Users/jinziguan/Desktop/explain_everything add -u
git -C /Users/jinziguan/Desktop/explain_everything status
```

Expected: 大量 `删除：` 列表（旧 src/explain_agent/**, tests/**, migrations/**, frameworks/**, scripts/**, data/**, uv.lock）。

**Step 5: Commit**

```bash
git -C /Users/jinziguan/Desktop/explain_everything commit -m "$(cat <<'EOF'
清理 · 删除旧金融归因 agent 代码

cognitive-engine-mvp 分支从 0 开始，旧 src/explain_agent / tests /
migrations / frameworks / data / scripts 全部删除。master 分支保留
作为金融 agent 墓碑。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 0.2: 重写 pyproject.toml

**Files:**
- Modify: `pyproject.toml`（整段重写）

**Step 1: Read current pyproject.toml**

读 [pyproject.toml](pyproject.toml) 确认当前 deps。重点是 `[project]` 段和 `[project.scripts]` 段会被整段替换。

**Step 2: Overwrite pyproject.toml**

完整内容（直接 Write 覆盖）:

```toml
[project]
name = "explain-engine"
version = "0.0.1"
description = "Cognitive Engine for explanation-centric reasoning"
readme = "README.md"
authors = [
    { name = "jinziguan", email = "18917950960@163.com" }
]
requires-python = ">=3.11"
dependencies = [
    "anthropic>=0.100.0",
    "networkx>=3.5",
    "openai>=2.36.0",
    "prompt-toolkit>=3.0.52",
    "pydantic>=2.13.4",
    "pydantic-settings>=2.14.1",
    "python-dotenv>=1.2.2",
    "pyyaml>=6.0.3",
    "rich>=15.0.0",
    "tenacity>=9.1.4",
    "typer>=0.25.1",
]

[project.scripts]
explain = "explain_engine.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[dependency-groups]
dev = [
    "mypy>=2.0.0",
    "pytest>=9.0.3",
    "pytest-asyncio>=1.3.0",
    "pytest-mock>=3.15.1",
    "ruff>=0.15.12",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "integration: 需要真实 LLM API 的集成测试，CI 默认跳过",
]
addopts = "-m 'not integration'"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "RUF"]
ignore = ["E501"]

[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_configs = true
```

**Step 3: Sync dependencies**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv sync
```

Expected: `Resolved N packages` + 新 `uv.lock` 生成。**注意**：会重新创建 `.venv`，旧的可能要先 `rm -rf .venv/` 才干净。

**Step 4: Verify**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run python -c "import anthropic, openai, networkx, pydantic, rich, typer; print('OK')"
```

Expected: `OK`

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add pyproject.toml uv.lock && git commit -m "$(cat <<'EOF'
pyproject · 重写为 explain-engine

- name: explain-agent → explain-engine
- deps 砍掉 langgraph / qdrant / clickhouse / akshare / sqlalchemy /
  sentence-transformers / tavily / trafilatura / cryptography / pymysql /
  httpx
- 新增 networkx (graph 包装)
- script entry: explain → explain_engine.cli:app
- pytest: asyncio_mode=auto
- 新增 ruff / mypy 严格配置

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 0.3: 创建新项目目录结构

**Files:**
- Create: `src/explain_engine/__init__.py`
- Create: `src/explain_engine/schema/__init__.py`
- Create: `src/explain_engine/runtime/__init__.py`
- Create: `src/explain_engine/engines/__init__.py`
- Create: `src/explain_engine/llm/__init__.py`
- Create: `src/explain_engine/llm/prompts/.gitkeep`
- Create: `src/explain_engine/hitl/__init__.py`
- Create: `src/explain_engine/persistence/__init__.py`
- Create: `tests/__init__.py`
- Create: `seeds/.gitkeep`
- Create: `sessions/.gitkeep`

**Step 1: Create directory structure**

```bash
cd /Users/jinziguan/Desktop/explain_everything && mkdir -p \
  src/explain_engine/schema \
  src/explain_engine/runtime \
  src/explain_engine/engines \
  src/explain_engine/llm/prompts \
  src/explain_engine/hitl \
  src/explain_engine/persistence \
  tests \
  seeds \
  sessions
```

**Step 2: Create __init__.py files**

每个 Python 包目录创建一个空 `__init__.py`:

```bash
cd /Users/jinziguan/Desktop/explain_everything && touch \
  src/explain_engine/__init__.py \
  src/explain_engine/schema/__init__.py \
  src/explain_engine/runtime/__init__.py \
  src/explain_engine/engines/__init__.py \
  src/explain_engine/llm/__init__.py \
  src/explain_engine/hitl/__init__.py \
  src/explain_engine/persistence/__init__.py \
  tests/__init__.py \
  src/explain_engine/llm/prompts/.gitkeep \
  seeds/.gitkeep \
  sessions/.gitkeep
```

**Step 3: Add `sessions/` to .gitignore**

读 `.gitignore`，确认有以下行（如果没有，加上）:

```
sessions/*.json
!sessions/.gitkeep
.venv/
__pycache__/
*.pyc
.pytest_cache/
```

如果 `.gitignore` 缺少行，用 Edit 工具加（不要覆盖已有内容）。

**Step 4: Verify package importable**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run python -c "import explain_engine; import explain_engine.schema; import explain_engine.runtime; import explain_engine.engines; import explain_engine.llm; import explain_engine.hitl; import explain_engine.persistence; print('all importable')"
```

Expected: `all importable`

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/ tests/ seeds/ sessions/ .gitignore && git commit -m "$(cat <<'EOF'
scaffold · 新建 explain_engine 目录骨架

src/explain_engine/{schema,runtime,engines,llm/prompts,hitl,persistence}
+ tests/ + seeds/ + sessions/。所有 Python 包含 __init__.py。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 0.4: 重写 README.md 和 .env.example

**Files:**
- Modify: `README.md`（整段覆盖）
- Modify: `.env.example`（整段覆盖）

**Step 1: Overwrite README.md**

完整内容:

```markdown
# Explain Engine

通用解释引擎（Cognitive Engine for explanation-centric reasoning）。

> 不是 chatbot，不是 workflow agent，是一个 World Explanation Engine。

## 设计文档

- [设计哲学](设计哲学.md) — 项目本质 / 解释哲学 / 世界观
- [需求设计](需求设计.md) — 核心能力 / 系统分层
- [技术设计 v1](技术设计v1.md) — Cognitive Runtime 完整愿景
- [MVP Design Doc](docs/plans/2026-05-13-cognitive-engine-mvp-design.md) — 当前实现的范围与取舍

## MVP 目标

从乱七八糟现象里**压出一个隐藏高维变量**（compression / hidden variable discovery）。

输入: `"为什么年轻人不消费"`
输出: `ExplanationGraph`，其中包含一个 abstract variable（如"长期不确定性"），通过 `manifests_as` 边连接到多个 concrete 现象。

## Status

🚧 **Pre-alpha** — Phase 0+1+2（scaffold + schema + LLM client）实施中。
Phase 3-6（Engine + Runtime + CLI + 评估）后续规划。

## 开发

```bash
uv sync                                  # 安装依赖
cp .env.example .env                     # 配置 LLM provider
uv run pytest                            # 单测
uv run ruff check .                      # 静态检查
uv run mypy src/                         # 类型检查
```

## 历史

- `master` 分支：A 股板块归因 agent（Phase 2.D-3，**产品死亡**）
- `cognitive-engine-mvp` 分支：通用解释引擎，从 0 重做
```

**Step 2: Overwrite .env.example**

完整内容:

```bash
# ============================================================
# LLM Provider 配置
# ============================================================
# 三选一：claude | openai | deepseek
LLM_PROVIDER=claude

# 默认模型（不同 provider 含义不同）
LLM_MODEL=claude-opus-4-7

# ---- Claude ----
ANTHROPIC_API_KEY=sk-ant-xxx

# ---- OpenAI ----
OPENAI_API_KEY=sk-xxx

# ---- DeepSeek (OpenAI 兼容 API) ----
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com

# ============================================================
# Runtime 配置
# ============================================================
# 默认 cognitive budget（每个 session 最多多少 tick）
DEFAULT_BUDGET=20

# Session JSON 落地目录
SESSIONS_DIR=./sessions
```

**Step 3: Verify**

```bash
cat /Users/jinziguan/Desktop/explain_everything/README.md | head -5
cat /Users/jinziguan/Desktop/explain_everything/.env.example | head -5
```

Expected: 内容是上面写的。

**Step 4: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add README.md .env.example && git commit -m "$(cat <<'EOF'
README · 重写为 explain-engine

- README 描述新方向 / MVP wow moment / pre-alpha status
- .env.example 三家 provider 配置 + runtime budget / sessions dir

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 0.5: 重置 tests/conftest.py 和 pytest 配置

**Files:**
- Create: `tests/conftest.py`

**Step 1: Write `tests/conftest.py`**

```python
"""pytest fixtures for explain_engine tests.

约定：
- 不写测试 import 应用代码（避免 fixture 与 production code 互依赖）
- LLM provider 测试 mock，集成测放 @pytest.mark.integration
- session 落地用 tmp_path，绝不污染 sessions/
"""

import pytest


@pytest.fixture
def tmp_sessions_dir(tmp_path, monkeypatch):
    """提供临时 sessions 目录，自动 monkeypatch 到 SESSIONS_DIR 环境变量。"""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setenv("SESSIONS_DIR", str(sessions_dir))
    return sessions_dir


@pytest.fixture
def mock_llm_response():
    """返回一个工具函数：给定 JSON dict，生成 mock LLM response。"""

    def _make(payload: dict, raw_text: str | None = None):
        from explain_engine.llm.client import Response

        return Response(
            text=raw_text if raw_text is not None else "",
            parsed=payload,
            model="mock",
            usage={"input_tokens": 0, "output_tokens": 0},
        )

    return _make
```

**Step 2: Verify pytest 能跑（哪怕 0 个 test）**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/ -v
```

Expected: `no tests ran` 或类似（因为 `mock_llm_response` 引用了还不存在的 `explain_engine.llm.client.Response`，但 import-time 不会触发——是 fixture 内部 lazy import，所以收集阶段 OK）。

如果报 ImportError 在 collection 阶段，那 fixture 写错了。

**Step 3: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add tests/conftest.py && git commit -m "$(cat <<'EOF'
tests · 新建 conftest.py

- tmp_sessions_dir fixture: 隔离 session 落地
- mock_llm_response fixture: lazy import 不阻塞 collection

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Phase 1 — Schema + Persistence

## Task 1.1: `VariableNode`

**Files:**
- Create: `src/explain_engine/schema/nodes.py`
- Test: `tests/test_schema_nodes.py`

**Step 1: Write failing test**

`tests/test_schema_nodes.py`:

```python
"""VariableNode schema test."""

import pytest
from pydantic import ValidationError

from explain_engine.schema.nodes import (
    AbstractionLevel,
    Epistemic,
    VariableNode,
)


class TestVariableNode:
    def test_minimal_concrete_node(self):
        node = VariableNode(
            id="n_001",
            name="房价上涨",
            description="一线城市房价持续高位",
            abstraction_level=0,
            confidence=0.9,
            epistemic="fact",
        )
        assert node.id == "n_001"
        assert node.abstraction_level == 0
        assert node.evidence_ids == []  # 默认空

    def test_abstract_node_with_evidence(self):
        node = VariableNode(
            id="n_010",
            name="长期不确定性",
            description="对未来收入 / 地位 / 价值的预期不可测",
            abstraction_level=2,
            confidence=0.7,
            epistemic="insight",
            evidence_ids=["e_001", "e_002"],
        )
        assert node.epistemic == "insight"
        assert node.evidence_ids == ["e_001", "e_002"]

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            VariableNode(
                id="n_002",
                name="x",
                description="x",
                abstraction_level=0,
                confidence=1.5,  # >1
                epistemic="fact",
            )

    def test_abstraction_level_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            VariableNode(
                id="n_003",
                name="x",
                description="x",
                abstraction_level=5,  # 超过 2
                confidence=0.5,
                epistemic="fact",
            )

    def test_epistemic_literal_enforced(self):
        with pytest.raises(ValidationError):
            VariableNode(
                id="n_004",
                name="x",
                description="x",
                abstraction_level=0,
                confidence=0.5,
                epistemic="wrong_label",  # type: ignore[arg-type]
            )

    def test_serializes_to_dict(self):
        node = VariableNode(
            id="n_001",
            name="房价上涨",
            description="x",
            abstraction_level=0,
            confidence=0.9,
            epistemic="fact",
        )
        d = node.model_dump()
        assert d["id"] == "n_001"
        assert d["evidence_ids"] == []

    def test_round_trip_json(self):
        node = VariableNode(
            id="n_001",
            name="房价",
            description="x",
            abstraction_level=1,
            confidence=0.6,
            epistemic="inference",
        )
        s = node.model_dump_json()
        restored = VariableNode.model_validate_json(s)
        assert restored == node
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_nodes.py -v
```

Expected: `ModuleNotFoundError: No module named 'explain_engine.schema.nodes'` (或类似 import 错)。

**Step 3: Write minimal implementation**

`src/explain_engine/schema/nodes.py`:

```python
"""VariableNode — explain engine 的认知原子。

参考 docs/plans/2026-05-13-cognitive-engine-mvp-design.md §3.2。
"""

from typing import Literal

from pydantic import BaseModel, Field

Epistemic = Literal[
    "fact",          # 可验证事实（有数据 / 共识）
    "observation",   # 主观可观察（用户自述 / 现象描述）
    "inference",     # 基于已知关系的推断
    "insight",       # 抽象跃迁后的解释性变量
    "speculation",   # 弱推断 / 不确定
]

AbstractionLevel = Literal[0, 1, 2]
# 0 = concrete (房价上涨)
# 1 = mid       (经济压力)
# 2 = abstract  (长期不确定性)


class VariableNode(BaseModel):
    """认知图中的节点。"""

    id: str
    name: str
    description: str
    abstraction_level: AbstractionLevel
    confidence: float = Field(ge=0.0, le=1.0)
    epistemic: Epistemic
    evidence_ids: list[str] = Field(default_factory=list)

    model_config = {"frozen": False}  # MVP 可变，v0.2 可考虑 frozen + new_with()
```

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_nodes.py -v
```

Expected: 6 个 test PASS。

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/schema/nodes.py tests/test_schema_nodes.py && git commit -m "$(cat <<'EOF'
schema · VariableNode + Epistemic / AbstractionLevel 类型

字段：id / name / description / abstraction_level / confidence /
epistemic / evidence_ids。confidence ∈ [0,1]，abstraction_level ∈
{0,1,2}，epistemic 5 类。pydantic 验证 + JSON round-trip + 6 单测。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.2: `RelationEdge`

**Files:**
- Create: `src/explain_engine/schema/edges.py`
- Test: `tests/test_schema_edges.py`

**Step 1: Write failing test**

`tests/test_schema_edges.py`:

```python
"""RelationEdge schema test."""

import pytest
from pydantic import ValidationError

from explain_engine.schema.edges import RelationEdge, RelationType


class TestRelationEdge:
    def test_minimal_edge(self):
        edge = RelationEdge(
            id="e_001",
            source_node="n_010",
            target_node="n_001",
            relation_type="manifests_as",
            confidence=0.8,
            mechanism_description="高房价是不确定性的具体表现",
        )
        assert edge.relation_type == "manifests_as"

    def test_self_loop_rejected(self):
        with pytest.raises(ValidationError, match="self-loop"):
            RelationEdge(
                id="e_002",
                source_node="n_001",
                target_node="n_001",  # same
                relation_type="causes",
                confidence=0.5,
                mechanism_description="x",
            )

    def test_empty_mechanism_rejected(self):
        with pytest.raises(ValidationError):
            RelationEdge(
                id="e_003",
                source_node="n_001",
                target_node="n_002",
                relation_type="causes",
                confidence=0.5,
                mechanism_description="",  # 空
            )

    def test_invalid_relation_type_rejected(self):
        with pytest.raises(ValidationError):
            RelationEdge(
                id="e_004",
                source_node="n_001",
                target_node="n_002",
                relation_type="wrong",  # type: ignore[arg-type]
                confidence=0.5,
                mechanism_description="x",
            )

    def test_all_relation_types_accepted(self):
        types: list[RelationType] = [
            "causes",
            "amplifies",
            "suppresses",
            "constrains",
            "manifests_as",
        ]
        for t in types:
            edge = RelationEdge(
                id=f"e_{t}",
                source_node="n_001",
                target_node="n_002",
                relation_type=t,
                confidence=0.5,
                mechanism_description="x",
            )
            assert edge.relation_type == t
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_edges.py -v
```

Expected: `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

`src/explain_engine/schema/edges.py`:

```python
"""RelationEdge — explain graph 中的边。

参考 docs/plans/2026-05-13-cognitive-engine-mvp-design.md §3.2。
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

RelationType = Literal[
    "causes",         # X 生成 Y
    "amplifies",      # X 加剧 Y
    "suppresses",     # X 抑制 Y
    "constrains",     # X 限制 Y
    "manifests_as",   # X 在具体层表现为 Y（抽象→具体专用）
]


class RelationEdge(BaseModel):
    """认知图中的有向边。"""

    id: str
    source_node: str
    target_node: str
    relation_type: RelationType
    confidence: float = Field(ge=0.0, le=1.0)
    mechanism_description: str = Field(min_length=1)

    @model_validator(mode="after")
    def _no_self_loop(self) -> "RelationEdge":
        if self.source_node == self.target_node:
            raise ValueError(f"self-loop not allowed: {self.source_node}")
        return self
```

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_edges.py -v
```

Expected: 5 个 test PASS.

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/schema/edges.py tests/test_schema_edges.py && git commit -m "$(cat <<'EOF'
schema · RelationEdge + RelationType 类型

5 种 relation_type (causes / amplifies / suppresses / constrains /
manifests_as)。强制 mechanism_description 非空 + 禁止 self-loop。
5 单测覆盖。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.3: `ExplanationGraph`（networkx 包装）

**Files:**
- Create: `src/explain_engine/schema/graph.py`
- Test: `tests/test_schema_graph.py`

**Step 1: Write failing test**

`tests/test_schema_graph.py`:

```python
"""ExplanationGraph (networkx 包装) test."""

import pytest

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(id_: str, level: int = 0, epi: str = "observation") -> VariableNode:
    return VariableNode(
        id=id_,
        name=id_,
        description=id_,
        abstraction_level=level,  # type: ignore[arg-type]
        confidence=0.5,
        epistemic=epi,  # type: ignore[arg-type]
    )


def _edge(eid: str, src: str, tgt: str, rt: str = "manifests_as") -> RelationEdge:
    return RelationEdge(
        id=eid,
        source_node=src,
        target_node=tgt,
        relation_type=rt,  # type: ignore[arg-type]
        confidence=0.5,
        mechanism_description="x",
    )


class TestExplanationGraph:
    def test_empty_graph(self):
        g = ExplanationGraph(root_question="why?")
        assert g.nodes == {}
        assert g.edges == {}
        assert g.compression_score() == 0.0
        assert g.coverage_score() == 0.0

    def test_add_node(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_001"))
        assert "n_001" in g.nodes

    def test_add_duplicate_node_rejected(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_001"))
        with pytest.raises(ValueError, match="already exists"):
            g.add_node(_node("n_001"))

    def test_add_edge(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_abs", level=2))
        g.add_node(_node("n_con", level=0))
        g.add_edge(_edge("e_001", "n_abs", "n_con"))
        assert "e_001" in g.edges

    def test_add_edge_unknown_node_rejected(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_001"))
        with pytest.raises(ValueError, match="unknown node"):
            g.add_edge(_edge("e_001", "n_001", "n_missing"))

    def test_compression_score_one_abstract_covers_three(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_abs", level=2))
        for i in range(3):
            cid = f"n_con_{i}"
            g.add_node(_node(cid))
            g.add_edge(_edge(f"e_{i}", "n_abs", cid))
        # 1 个 abstract 覆盖 3 个 concrete = 压缩 3
        assert g.compression_score() == 3.0

    def test_coverage_score_partial(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_abs", level=2))
        for i in range(4):
            g.add_node(_node(f"n_con_{i}"))
        # 只有 2 条边
        g.add_edge(_edge("e_0", "n_abs", "n_con_0"))
        g.add_edge(_edge("e_1", "n_abs", "n_con_1"))
        # 4 个 concrete 中 2 个被覆盖
        assert g.coverage_score() == 0.5

    def test_frontier_returns_abstract_no_outgoing(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_abs_isolated", level=2))
        g.add_node(_node("n_abs_used", level=2))
        g.add_node(_node("n_con"))
        g.add_edge(_edge("e", "n_abs_used", "n_con"))
        # n_abs_used 已经有 outgoing，不是 frontier
        # n_abs_isolated 没有 outgoing，是 frontier
        assert g.frontier() == ["n_abs_isolated"]

    def test_serialize_round_trip(self):
        g = ExplanationGraph(root_question="why?")
        g.add_node(_node("n_abs", level=2))
        g.add_node(_node("n_con"))
        g.add_edge(_edge("e", "n_abs", "n_con"))
        d = g.to_dict()
        restored = ExplanationGraph.from_dict(d)
        assert restored.nodes == g.nodes
        assert restored.edges == g.edges
        assert restored.root_question == g.root_question
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_graph.py -v
```

Expected: `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

`src/explain_engine/schema/graph.py`:

```python
"""ExplanationGraph — networkx.DiGraph 包装。

把 VariableNode / RelationEdge 暴露成 dict-like，并提供 compression /
coverage / frontier 计算。

参考 docs/plans/2026-05-13-cognitive-engine-mvp-design.md §3.2。
"""

import networkx as nx

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode


class ExplanationGraph:
    def __init__(self, root_question: str) -> None:
        self.root_question = root_question
        self._g: nx.DiGraph = nx.DiGraph()
        self.nodes: dict[str, VariableNode] = {}
        self.edges: dict[str, RelationEdge] = {}

    def add_node(self, node: VariableNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"node {node.id} already exists")
        self.nodes[node.id] = node
        self._g.add_node(node.id)

    def add_edge(self, edge: RelationEdge) -> None:
        if edge.source_node not in self.nodes:
            raise ValueError(f"unknown node: {edge.source_node}")
        if edge.target_node not in self.nodes:
            raise ValueError(f"unknown node: {edge.target_node}")
        if edge.id in self.edges:
            raise ValueError(f"edge {edge.id} already exists")
        self.edges[edge.id] = edge
        self._g.add_edge(edge.source_node, edge.target_node, edge_id=edge.id)

    def compression_score(self) -> float:
        """abstract 节点覆盖了多少 concrete。

        v0.1 简化：返回所有 abstraction_level >= 1 节点的 out-degree 之和。
        """
        return float(
            sum(
                self._g.out_degree(nid)
                for nid, node in self.nodes.items()
                if node.abstraction_level >= 1
            )
        )

    def coverage_score(self) -> float:
        """concrete 节点中被任意 high-abstraction 节点覆盖的比例。"""
        concretes = [nid for nid, n in self.nodes.items() if n.abstraction_level == 0]
        if not concretes:
            return 0.0

        covered = {
            nid
            for nid in concretes
            if any(
                pred for pred in self._g.predecessors(nid)
                if self.nodes[pred].abstraction_level >= 1
            )
        }
        return len(covered) / len(concretes)

    def frontier(self) -> list[str]:
        """没有 outgoing edge 的 high-abstraction 节点。"""
        return sorted(
            nid
            for nid, n in self.nodes.items()
            if n.abstraction_level >= 1 and self._g.out_degree(nid) == 0
        )

    def to_dict(self) -> dict:
        return {
            "root_question": self.root_question,
            "nodes": {nid: n.model_dump() for nid, n in self.nodes.items()},
            "edges": {eid: e.model_dump() for eid, e in self.edges.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExplanationGraph":
        g = cls(root_question=d["root_question"])
        for nid, n in d["nodes"].items():
            g.add_node(VariableNode.model_validate(n))
        for eid, e in d["edges"].items():
            g.add_edge(RelationEdge.model_validate(e))
        return g
```

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_graph.py -v
```

Expected: 9 个 test PASS.

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/schema/graph.py tests/test_schema_graph.py && git commit -m "$(cat <<'EOF'
schema · ExplanationGraph (networkx 包装)

API: add_node / add_edge / compression_score / coverage_score /
frontier / to_dict / from_dict。强制 add_edge 前置节点存在，
重复 id 抛 ValueError。9 单测覆盖空图 / 单点 / 边添加 /
压缩计算 / 覆盖率 / frontier / round-trip。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.4: `CognitiveState`

**Files:**
- Create: `src/explain_engine/schema/state.py`
- Test: `tests/test_schema_state.py`

**Step 1: Write failing test**

`tests/test_schema_state.py`:

```python
"""CognitiveState test."""

import pytest

from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState


class TestCognitiveState:
    def test_create_from_question(self):
        s = CognitiveState.bootstrap(question="why?", budget=15)
        assert s.root_question == "why?"
        assert s.budget_remaining == 15
        assert s.tick == 0
        assert s.last_gain_tick == 0
        assert s.insight_candidates == []
        assert isinstance(s.graph, ExplanationGraph)
        assert s.graph.root_question == "why?"

    def test_tick_advances(self):
        s = CognitiveState.bootstrap(question="why?", budget=3)
        s.advance_tick()
        assert s.tick == 1
        assert s.budget_remaining == 2

    def test_advance_below_zero_rejected(self):
        s = CognitiveState.bootstrap(question="why?", budget=1)
        s.advance_tick()
        with pytest.raises(ValueError, match="budget exhausted"):
            s.advance_tick()

    def test_record_gain(self):
        s = CognitiveState.bootstrap(question="why?", budget=10)
        s.advance_tick()
        s.advance_tick()
        s.record_gain()
        assert s.last_gain_tick == s.tick

    def test_round_trip_json(self):
        s = CognitiveState.bootstrap(question="why?", budget=20)
        s.advance_tick()
        s.insight_candidates.append("n_abs_001")
        d = s.to_dict()
        restored = CognitiveState.from_dict(d)
        assert restored.tick == s.tick
        assert restored.budget_remaining == s.budget_remaining
        assert restored.insight_candidates == s.insight_candidates
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_state.py -v
```

Expected: `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

`src/explain_engine/schema/state.py`:

```python
"""CognitiveState — runtime 运行时状态。

参考 docs/plans/2026-05-13-cognitive-engine-mvp-design.md §3.2。
"""

from dataclasses import dataclass, field

from explain_engine.schema.graph import ExplanationGraph


@dataclass
class CognitiveState:
    graph: ExplanationGraph
    budget_remaining: int
    root_question: str
    active_frontier: list[str] = field(default_factory=list)
    insight_candidates: list[str] = field(default_factory=list)
    tick: int = 0
    last_gain_tick: int = 0

    @classmethod
    def bootstrap(cls, question: str, budget: int) -> "CognitiveState":
        return cls(
            graph=ExplanationGraph(root_question=question),
            budget_remaining=budget,
            root_question=question,
        )

    def advance_tick(self) -> None:
        if self.budget_remaining <= 0:
            raise ValueError("budget exhausted")
        self.budget_remaining -= 1
        self.tick += 1

    def record_gain(self) -> None:
        self.last_gain_tick = self.tick

    def to_dict(self) -> dict:
        return {
            "graph": self.graph.to_dict(),
            "budget_remaining": self.budget_remaining,
            "root_question": self.root_question,
            "active_frontier": list(self.active_frontier),
            "insight_candidates": list(self.insight_candidates),
            "tick": self.tick,
            "last_gain_tick": self.last_gain_tick,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CognitiveState":
        return cls(
            graph=ExplanationGraph.from_dict(d["graph"]),
            budget_remaining=d["budget_remaining"],
            root_question=d["root_question"],
            active_frontier=list(d.get("active_frontier", [])),
            insight_candidates=list(d.get("insight_candidates", [])),
            tick=d.get("tick", 0),
            last_gain_tick=d.get("last_gain_tick", 0),
        )
```

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_state.py -v
```

Expected: 5 个 test PASS.

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/schema/state.py tests/test_schema_state.py && git commit -m "$(cat <<'EOF'
schema · CognitiveState (runtime 运行时状态)

dataclass，含 graph / budget / tick / last_gain_tick /
active_frontier / insight_candidates。bootstrap classmethod 一步起
session。advance_tick 在 budget 用完时抛错。5 单测覆盖 JSON
round-trip。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.5: `Session` 落地（JSON I/O）

**Files:**
- Create: `src/explain_engine/persistence/session.py`
- Test: `tests/test_persistence_session.py`

**Step 1: Write failing test**

`tests/test_persistence_session.py`:

```python
"""Session JSON I/O test."""

import json

import pytest

from explain_engine.persistence.session import (
    Session,
    SessionMeta,
    SessionStore,
)
from explain_engine.schema.state import CognitiveState


class TestSessionMeta:
    def test_fresh_session_meta(self):
        meta = SessionMeta.new(question="why?")
        assert meta.session_id.startswith("s_")
        assert len(meta.session_id) == 2 + 8  # s_ + 8 hex
        assert meta.question == "why?"
        assert meta.stage == "bootstrap_pending"


class TestSessionStore:
    def test_save_and_load(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        state = CognitiveState.bootstrap("why?", budget=20)
        meta = SessionMeta.new(question="why?")
        session = Session(meta=meta, state=state)

        store.save(session)
        loaded = store.load(meta.session_id)

        assert loaded.meta.session_id == meta.session_id
        assert loaded.state.root_question == "why?"
        assert loaded.state.budget_remaining == 20

    def test_load_missing_raises(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        with pytest.raises(FileNotFoundError):
            store.load("s_nonexistent")

    def test_list_returns_metas_sorted(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        for q in ["q1", "q2", "q3"]:
            store.save(
                Session(
                    meta=SessionMeta.new(question=q),
                    state=CognitiveState.bootstrap(q, budget=10),
                )
            )
        metas = store.list()
        assert len(metas) == 3
        # 按 created_at 降序
        assert metas[0].created_at >= metas[1].created_at >= metas[2].created_at

    def test_save_writes_json_file(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        session = Session(
            meta=SessionMeta.new(question="why?"),
            state=CognitiveState.bootstrap("why?", budget=10),
        )
        store.save(session)
        path = tmp_sessions_dir / f"{session.meta.session_id}.json"
        assert path.exists()
        # 文件是合法 JSON
        json.loads(path.read_text())

    def test_update_session_overwrites(self, tmp_sessions_dir):
        store = SessionStore(directory=tmp_sessions_dir)
        session = Session(
            meta=SessionMeta.new(question="why?"),
            state=CognitiveState.bootstrap("why?", budget=10),
        )
        store.save(session)

        # 修改 state 再保存
        session.state.advance_tick()
        session.meta.stage = "in_progress"
        store.save(session)

        loaded = store.load(session.meta.session_id)
        assert loaded.state.tick == 1
        assert loaded.meta.stage == "in_progress"
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_persistence_session.py -v
```

Expected: `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

`src/explain_engine/persistence/session.py`:

```python
"""Session 落地：每个 session 一个 JSON 文件。

文件名: {session_id}.json
session_id 格式: s_{8 hex}
"""

import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from explain_engine.schema.state import CognitiveState

Stage = Literal[
    "bootstrap_pending",   # 等 HITL 1
    "running",             # runtime loop 中
    "finalize_pending",    # 等 HITL 2
    "done",                # render 完成
]


def _new_session_id() -> str:
    return "s_" + secrets.token_hex(4)


@dataclass
class SessionMeta:
    session_id: str
    question: str
    stage: Stage
    created_at: float
    updated_at: float

    @classmethod
    def new(cls, question: str) -> "SessionMeta":
        now = time.time()
        return cls(
            session_id=_new_session_id(),
            question=question,
            stage="bootstrap_pending",
            created_at=now,
            updated_at=now,
        )


@dataclass
class Session:
    meta: SessionMeta
    state: CognitiveState

    def to_dict(self) -> dict:
        return {
            "meta": asdict(self.meta),
            "state": self.state.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            meta=SessionMeta(**d["meta"]),
            state=CognitiveState.from_dict(d["state"]),
        )


class SessionStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.directory / f"{session_id}.json"

    def save(self, session: Session) -> None:
        session.meta.updated_at = time.time()
        p = self._path(session.meta.session_id)
        p.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2))

    def load(self, session_id: str) -> Session:
        p = self._path(session_id)
        if not p.exists():
            raise FileNotFoundError(f"session {session_id} not found at {p}")
        return Session.from_dict(json.loads(p.read_text()))

    def list(self) -> list[SessionMeta]:
        metas: list[SessionMeta] = []
        for p in self.directory.glob("s_*.json"):
            d = json.loads(p.read_text())
            metas.append(SessionMeta(**d["meta"]))
        metas.sort(key=lambda m: m.created_at, reverse=True)
        return metas
```

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_persistence_session.py -v
```

Expected: 5 个 test PASS.

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/persistence/session.py tests/test_persistence_session.py && git commit -m "$(cat <<'EOF'
persistence · Session JSON I/O

- SessionMeta: id (s_<hex8>) / question / stage / created_at / updated_at
- Stage: bootstrap_pending / running / finalize_pending / done
- Session: meta + CognitiveState
- SessionStore: save / load / list (按 created_at 降序)
- 5 单测覆盖落地 + 加载 + 缺失抛错 + 列表排序 + 覆盖写入

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Phase 2 — LLM Client 抽象 + 三 Provider

## Task 2.1: `LLMClient` Protocol + `Message`/`Response` 类型

**Files:**
- Create: `src/explain_engine/llm/client.py`
- Test: `tests/test_llm_client_types.py`

**Step 1: Write failing test**

`tests/test_llm_client_types.py`:

```python
"""LLMClient Protocol + 基本类型 test。

LLMClient 是 Protocol，不直接测；测 Message / Response 数据类。
"""

import pytest
from pydantic import BaseModel, ValidationError

from explain_engine.llm.client import LLMClient, Message, Response


class _DemoSchema(BaseModel):
    answer: str
    confidence: float


class TestMessage:
    def test_user_message(self):
        m = Message(role="user", content="why?")
        assert m.role == "user"

    def test_role_validation(self):
        with pytest.raises(ValidationError):
            Message(role="invalid", content="x")  # type: ignore[arg-type]

    def test_system_user_assistant_roles_accepted(self):
        for role in ("system", "user", "assistant"):
            Message(role=role, content="x")  # type: ignore[arg-type]


class TestResponse:
    def test_response_with_parsed(self):
        r = Response(
            text='{"answer": "yes", "confidence": 0.9}',
            parsed={"answer": "yes", "confidence": 0.9},
            model="claude-opus",
            usage={"input_tokens": 100, "output_tokens": 50},
        )
        assert r.parsed["answer"] == "yes"
        assert r.usage["input_tokens"] == 100

    def test_response_text_only(self):
        r = Response(text="hello", parsed=None, model="x", usage={})
        assert r.parsed is None


class TestLLMClientIsProtocol:
    """LLMClient 必须可作为 Protocol 标注，不必实例化。"""

    def test_protocol_can_be_used_as_type(self):
        def takes_client(c: LLMClient) -> str:
            return "ok"

        # 至少不能在 import / 函数定义阶段崩
        assert takes_client.__annotations__["c"] is LLMClient
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_llm_client_types.py -v
```

Expected: `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

`src/explain_engine/llm/client.py`:

```python
"""LLMClient Protocol + 基础类型。

三个 provider (Claude / OpenAI / DeepSeek) 都实现这个 Protocol。
"""

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str


class Response(BaseModel):
    text: str
    parsed: dict[str, Any] | None
    model: str
    usage: dict[str, int]


@runtime_checkable
class LLMClient(Protocol):
    """统一的 LLM 调用接口。

    每个 provider 实现 `chat`。`schema` 不为 None 时启用 structured
    output (provider 内部选择 tools / response_format / JSON mode)。
    """

    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str | None = None,
    ) -> Response: ...
```

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_llm_client_types.py -v
```

Expected: 7 个 test PASS.

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/llm/client.py tests/test_llm_client_types.py && git commit -m "$(cat <<'EOF'
llm · LLMClient Protocol + Message / Response 类型

- Message: role (system/user/assistant) + content
- Response: text + parsed (structured output dict) + model + usage
- LLMClient: Protocol，三家 provider 都实现 .chat(messages, schema, model)
- 7 单测覆盖类型 + Protocol 标注

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2.2: `ClaudeClient` 实现

**Files:**
- Create: `src/explain_engine/llm/claude.py`
- Test: `tests/test_llm_claude.py`

**Step 1: Write failing test**

`tests/test_llm_claude.py`:

```python
"""ClaudeClient 单测（mock anthropic SDK）。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from explain_engine.llm.claude import ClaudeClient
from explain_engine.llm.client import Message


class _DemoSchema(BaseModel):
    answer: str
    confidence: float


@pytest.fixture
def mock_anthropic(mocker):
    mock_client = AsyncMock()
    mocker.patch(
        "explain_engine.llm.claude.AsyncAnthropic",
        return_value=mock_client,
    )
    return mock_client


def _mock_message_response(text: str, model: str = "claude-opus-4-7"):
    resp = MagicMock()
    resp.content = [MagicMock(type="text", text=text)]
    resp.model = model
    resp.usage = MagicMock(input_tokens=10, output_tokens=20)
    return resp


def _mock_tool_use_response(tool_input: dict, model: str = "claude-opus-4-7"):
    resp = MagicMock()
    block = MagicMock()
    block.type = "tool_use"
    block.input = tool_input
    resp.content = [block]
    resp.model = model
    resp.usage = MagicMock(input_tokens=10, output_tokens=20)
    return resp


class TestClaudeClient:
    async def test_chat_text_response(self, mock_anthropic):
        mock_anthropic.messages.create = AsyncMock(
            return_value=_mock_message_response("hello world")
        )
        client = ClaudeClient(api_key="sk-test", default_model="claude-opus-4-7")
        r = await client.chat([Message(role="user", content="hi")])
        assert r.text == "hello world"
        assert r.parsed is None
        assert r.usage == {"input_tokens": 10, "output_tokens": 20}

    async def test_chat_with_schema_uses_tools(self, mock_anthropic):
        mock_anthropic.messages.create = AsyncMock(
            return_value=_mock_tool_use_response({"answer": "yes", "confidence": 0.9})
        )
        client = ClaudeClient(api_key="sk-test", default_model="claude-opus-4-7")
        r = await client.chat(
            [Message(role="user", content="hi")],
            schema=_DemoSchema,
        )
        assert r.parsed == {"answer": "yes", "confidence": 0.9}
        # 校验 anthropic API 被传了 tools 参数
        call_kwargs = mock_anthropic.messages.create.call_args.kwargs
        assert "tools" in call_kwargs
        assert call_kwargs["tools"][0]["name"] == "_DemoSchema"
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "_DemoSchema"}

    async def test_system_message_extracted(self, mock_anthropic):
        mock_anthropic.messages.create = AsyncMock(
            return_value=_mock_message_response("ok")
        )
        client = ClaudeClient(api_key="sk-test", default_model="claude-opus-4-7")
        await client.chat(
            [
                Message(role="system", content="you are helpful"),
                Message(role="user", content="hi"),
            ]
        )
        kwargs = mock_anthropic.messages.create.call_args.kwargs
        assert kwargs["system"] == "you are helpful"
        # system 不应该出现在 messages
        assert all(m["role"] != "system" for m in kwargs["messages"])

    async def test_default_model_used(self, mock_anthropic):
        mock_anthropic.messages.create = AsyncMock(
            return_value=_mock_message_response("ok")
        )
        client = ClaudeClient(api_key="sk-test", default_model="claude-haiku")
        await client.chat([Message(role="user", content="hi")])
        kwargs = mock_anthropic.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-haiku"

    async def test_model_override(self, mock_anthropic):
        mock_anthropic.messages.create = AsyncMock(
            return_value=_mock_message_response("ok")
        )
        client = ClaudeClient(api_key="sk-test", default_model="claude-opus-4-7")
        await client.chat([Message(role="user", content="hi")], model="claude-haiku")
        kwargs = mock_anthropic.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-haiku"
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_llm_claude.py -v
```

Expected: `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

`src/explain_engine/llm/claude.py`:

```python
"""Claude (Anthropic) provider 实现。

Structured output 走 tools API: 把 schema 塞进 tools[0].input_schema,
强制 tool_choice。
"""

from typing import Any

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from explain_engine.llm.client import Message, Response


class ClaudeClient:
    def __init__(self, api_key: str, default_model: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._default_model = default_model

    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str | None = None,
    ) -> Response:
        # 拆 system message（Anthropic API 单独传 system）
        system_text: str | None = None
        chat_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_text = (system_text + "\n\n" if system_text else "") + m.content
            else:
                chat_messages.append({"role": m.role, "content": m.content})

        call_kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "max_tokens": 4096,
            "messages": chat_messages,
        }
        if system_text:
            call_kwargs["system"] = system_text

        if schema is not None:
            tool_name = schema.__name__
            call_kwargs["tools"] = [
                {
                    "name": tool_name,
                    "description": schema.__doc__ or f"Structured output: {tool_name}",
                    "input_schema": schema.model_json_schema(),
                }
            ]
            call_kwargs["tool_choice"] = {"type": "tool", "name": tool_name}

        api_resp = await self._client.messages.create(**call_kwargs)

        text = ""
        parsed: dict[str, Any] | None = None
        for block in api_resp.content:
            if block.type == "tool_use":
                parsed = dict(block.input)
            elif block.type == "text":
                text += block.text

        return Response(
            text=text,
            parsed=parsed,
            model=api_resp.model,
            usage={
                "input_tokens": api_resp.usage.input_tokens,
                "output_tokens": api_resp.usage.output_tokens,
            },
        )
```

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_llm_claude.py -v
```

Expected: 5 个 test PASS.

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/llm/claude.py tests/test_llm_claude.py && git commit -m "$(cat <<'EOF'
llm · ClaudeClient 实现

- async chat 走 anthropic AsyncAnthropic
- system message 单独拆出（Anthropic API 要求）
- schema 传入 → 自动走 tools + tool_choice 强制结构化输出
- model 参数允许调用时覆盖默认
- 5 单测 mock anthropic SDK 覆盖 text / structured / system /
  default model / override

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2.3: `OpenAIClient` 实现

**Files:**
- Create: `src/explain_engine/llm/openai_client.py`
- Test: `tests/test_llm_openai.py`

**Step 1: Write failing test**

`tests/test_llm_openai.py`:

```python
"""OpenAIClient 单测（mock openai SDK）。"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from explain_engine.llm.client import Message
from explain_engine.llm.openai_client import OpenAIClient


class _DemoSchema(BaseModel):
    answer: str
    confidence: float


@pytest.fixture
def mock_openai(mocker):
    mock_client = AsyncMock()
    mocker.patch(
        "explain_engine.llm.openai_client.AsyncOpenAI",
        return_value=mock_client,
    )
    return mock_client


def _mock_choice(content: str, model: str = "gpt-4o"):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.model = model
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
    return resp


class TestOpenAIClient:
    async def test_chat_text_response(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice("hello world")
        )
        client = OpenAIClient(api_key="sk-test", default_model="gpt-4o")
        r = await client.chat([Message(role="user", content="hi")])
        assert r.text == "hello world"
        assert r.parsed is None
        assert r.usage == {"input_tokens": 10, "output_tokens": 20}

    async def test_chat_with_schema_uses_json_schema(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice(
                json.dumps({"answer": "yes", "confidence": 0.9})
            )
        )
        client = OpenAIClient(api_key="sk-test", default_model="gpt-4o")
        r = await client.chat(
            [Message(role="user", content="hi")],
            schema=_DemoSchema,
        )
        assert r.parsed == {"answer": "yes", "confidence": 0.9}
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert kwargs["response_format"]["type"] == "json_schema"
        assert kwargs["response_format"]["json_schema"]["name"] == "_DemoSchema"

    async def test_messages_passed_through(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice("ok")
        )
        client = OpenAIClient(api_key="sk-test", default_model="gpt-4o")
        await client.chat(
            [
                Message(role="system", content="you are helpful"),
                Message(role="user", content="hi"),
            ]
        )
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert kwargs["messages"] == [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hi"},
        ]

    async def test_model_override(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice("ok")
        )
        client = OpenAIClient(api_key="sk-test", default_model="gpt-4o")
        await client.chat([Message(role="user", content="hi")], model="gpt-4-turbo")
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "gpt-4-turbo"
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_llm_openai.py -v
```

Expected: `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

`src/explain_engine/llm/openai_client.py`:

```python
"""OpenAI provider 实现。

Structured output 走 response_format={"type": "json_schema", ...}。
"""

import json
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel

from explain_engine.llm.client import Message, Response


class OpenAIClient:
    def __init__(
        self,
        api_key: str,
        default_model: str,
        base_url: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)
        self._default_model = default_model

    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str | None = None,
    ) -> Response:
        call_kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }

        if schema is not None:
            call_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            }

        api_resp = await self._client.chat.completions.create(**call_kwargs)

        text = api_resp.choices[0].message.content or ""
        parsed: dict[str, Any] | None = None
        if schema is not None and text:
            parsed = json.loads(text)

        return Response(
            text=text,
            parsed=parsed,
            model=api_resp.model,
            usage={
                "input_tokens": api_resp.usage.prompt_tokens,
                "output_tokens": api_resp.usage.completion_tokens,
            },
        )
```

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_llm_openai.py -v
```

Expected: 4 个 test PASS.

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/llm/openai_client.py tests/test_llm_openai.py && git commit -m "$(cat <<'EOF'
llm · OpenAIClient 实现

- async chat 走 openai AsyncOpenAI
- schema 传入 → response_format=json_schema (strict mode)
- base_url 可选 → 复用同一个 client 类给 DeepSeek
- 4 单测 mock openai SDK

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2.4: `DeepSeekClient` 实现

**Files:**
- Create: `src/explain_engine/llm/deepseek.py`
- Test: `tests/test_llm_deepseek.py`

**Step 1: Write failing test**

`tests/test_llm_deepseek.py`:

```python
"""DeepSeekClient 单测。

DeepSeek 用 OpenAI 兼容 API，但 structured output 是 JSON mode 而不是
json_schema，所以 schema 通过 prompt 描述注入。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from explain_engine.llm.client import Message
from explain_engine.llm.deepseek import DeepSeekClient


class _DemoSchema(BaseModel):
    answer: str
    confidence: float


@pytest.fixture
def mock_openai(mocker):
    mock_client = AsyncMock()
    mocker.patch(
        "explain_engine.llm.deepseek.AsyncOpenAI",
        return_value=mock_client,
    )
    return mock_client


def _mock_choice(content: str, model: str = "deepseek-chat"):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.model = model
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
    return resp


class TestDeepSeekClient:
    async def test_chat_with_schema_injects_into_system_prompt(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice(
                json.dumps({"answer": "yes", "confidence": 0.9})
            )
        )
        client = DeepSeekClient(
            api_key="sk-test",
            default_model="deepseek-chat",
            base_url="https://api.deepseek.com",
        )
        r = await client.chat(
            [Message(role="user", content="hi")],
            schema=_DemoSchema,
        )
        assert r.parsed == {"answer": "yes", "confidence": 0.9}

        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        # JSON mode
        assert kwargs["response_format"] == {"type": "json_object"}
        # schema 应该被注入第一条 system message
        first_msg = kwargs["messages"][0]
        assert first_msg["role"] == "system"
        assert "_DemoSchema" in first_msg["content"]
        assert "answer" in first_msg["content"]
        assert "confidence" in first_msg["content"]

    async def test_chat_no_schema_no_json_mode(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice("hello")
        )
        client = DeepSeekClient(
            api_key="sk-test",
            default_model="deepseek-chat",
            base_url="https://api.deepseek.com",
        )
        r = await client.chat([Message(role="user", content="hi")])
        assert r.text == "hello"
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        assert "response_format" not in kwargs

    async def test_existing_system_message_preserved(self, mock_openai):
        mock_openai.chat.completions.create = AsyncMock(
            return_value=_mock_choice(json.dumps({"answer": "x", "confidence": 0.5}))
        )
        client = DeepSeekClient(
            api_key="sk-test",
            default_model="deepseek-chat",
            base_url="https://api.deepseek.com",
        )
        await client.chat(
            [
                Message(role="system", content="you are helpful"),
                Message(role="user", content="hi"),
            ],
            schema=_DemoSchema,
        )
        kwargs = mock_openai.chat.completions.create.call_args.kwargs
        first_msg = kwargs["messages"][0]
        # schema 注入应该 prepend 到原 system message
        assert "you are helpful" in first_msg["content"]
        assert "_DemoSchema" in first_msg["content"]
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_llm_deepseek.py -v
```

Expected: `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

`src/explain_engine/llm/deepseek.py`:

```python
"""DeepSeek provider 实现。

DeepSeek 用 OpenAI 兼容 API。structured output 走 JSON mode +
prompt 注入 schema 描述 —— 不支持 json_schema strict 模式。
"""

import json
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel

from explain_engine.llm.client import Message, Response


def _schema_instructions(schema: type[BaseModel]) -> str:
    json_schema = schema.model_json_schema()
    return (
        f"You MUST respond with a single JSON object matching schema "
        f"{schema.__name__}:\n```json\n{json.dumps(json_schema, indent=2)}\n```\n"
        f"Do not include any explanation outside the JSON."
    )


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        default_model: str,
        base_url: str,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._default_model = default_model

    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str | None = None,
    ) -> Response:
        api_messages: list[dict[str, str]] = [
            {"role": m.role, "content": m.content} for m in messages
        ]

        call_kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": api_messages,
        }

        if schema is not None:
            schema_text = _schema_instructions(schema)
            if api_messages and api_messages[0]["role"] == "system":
                api_messages[0] = {
                    "role": "system",
                    "content": schema_text + "\n\n" + api_messages[0]["content"],
                }
            else:
                api_messages.insert(0, {"role": "system", "content": schema_text})
            call_kwargs["messages"] = api_messages
            call_kwargs["response_format"] = {"type": "json_object"}

        api_resp = await self._client.chat.completions.create(**call_kwargs)

        text = api_resp.choices[0].message.content or ""
        parsed: dict[str, Any] | None = None
        if schema is not None and text:
            parsed = json.loads(text)

        return Response(
            text=text,
            parsed=parsed,
            model=api_resp.model,
            usage={
                "input_tokens": api_resp.usage.prompt_tokens,
                "output_tokens": api_resp.usage.completion_tokens,
            },
        )
```

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_llm_deepseek.py -v
```

Expected: 3 个 test PASS.

**Step 5: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/llm/deepseek.py tests/test_llm_deepseek.py && git commit -m "$(cat <<'EOF'
llm · DeepSeekClient 实现

DeepSeek 用 OpenAI 兼容 API + base_url=https://api.deepseek.com。
structured output 走 JSON mode + prompt 注入 schema 描述（不支持
json_schema strict mode）。3 单测覆盖 schema 注入 / 无 schema /
原 system message 保留。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2.5: `config.py` + `make_client` factory

**Files:**
- Create: `src/explain_engine/config.py`
- Test: `tests/test_config.py`

**Step 1: Write failing test**

`tests/test_config.py`:

```python
"""Settings + make_client factory test。"""

import pytest

from explain_engine.config import Settings, make_client
from explain_engine.llm.claude import ClaudeClient
from explain_engine.llm.deepseek import DeepSeekClient
from explain_engine.llm.openai_client import OpenAIClient


class TestSettings:
    def test_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "claude")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-7")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("DEFAULT_BUDGET", "30")

        s = Settings()
        assert s.llm_provider == "claude"
        assert s.llm_model == "claude-opus-4-7"
        assert s.default_budget == 30

    def test_invalid_provider_rejected(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "invalid")
        with pytest.raises(Exception):  # pydantic ValidationError
            Settings()


class TestMakeClient:
    def test_make_claude(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "claude")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-7")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        client = make_client(Settings())
        assert isinstance(client, ClaudeClient)

    def test_make_openai(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        client = make_client(Settings())
        assert isinstance(client, OpenAIClient)

    def test_make_deepseek(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        client = make_client(Settings())
        assert isinstance(client, DeepSeekClient)

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "claude")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-7")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            make_client(Settings())
```

**Step 2: Run to verify it fails**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError`.

**Step 3: Write minimal implementation**

`src/explain_engine/config.py`:

```python
"""Settings (pydantic-settings) + make_client factory。"""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from explain_engine.llm.claude import ClaudeClient
from explain_engine.llm.client import LLMClient
from explain_engine.llm.deepseek import DeepSeekClient
from explain_engine.llm.openai_client import OpenAIClient

Provider = Literal["claude", "openai", "deepseek"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: Provider = "claude"
    llm_model: str = "claude-opus-4-7"

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"

    default_budget: int = Field(default=20, ge=1)
    sessions_dir: Path = Path("./sessions")


def make_client(settings: Settings) -> LLMClient:
    match settings.llm_provider:
        case "claude":
            if not settings.anthropic_api_key:
                raise ValueError("ANTHROPIC_API_KEY required for provider=claude")
            return ClaudeClient(
                api_key=settings.anthropic_api_key,
                default_model=settings.llm_model,
            )
        case "openai":
            if not settings.openai_api_key:
                raise ValueError("OPENAI_API_KEY required for provider=openai")
            return OpenAIClient(
                api_key=settings.openai_api_key,
                default_model=settings.llm_model,
            )
        case "deepseek":
            if not settings.deepseek_api_key:
                raise ValueError("DEEPSEEK_API_KEY required for provider=deepseek")
            return DeepSeekClient(
                api_key=settings.deepseek_api_key,
                default_model=settings.llm_model,
                base_url=settings.deepseek_base_url,
            )
```

**Step 4: Run to verify it passes**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_config.py -v
```

Expected: 5 个 test PASS。

**Step 5: Run full test suite**

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest -v
```

Expected: 全部 PASS（Phase 0+1+2 累计 ~36 个 test）。

**Step 6: Commit**

```bash
cd /Users/jinziguan/Desktop/explain_everything && git add src/explain_engine/config.py tests/test_config.py && git commit -m "$(cat <<'EOF'
config · Settings (pydantic-settings) + make_client factory

- Settings: 读 .env，三家 provider 凭证 + budget + sessions_dir
- llm_provider: Literal["claude","openai","deepseek"]
- make_client: 按 provider 实例化对应 client，缺 API key 报错
- 5 单测覆盖 env 加载 + 三家 factory + missing key

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Phase 0+1+2 完工验收

满足以下条件才算 Phase 0+1+2 完成:

- [ ] `git log --oneline` 显示 15 个 commit (Phase 0 5 个 + Phase 1 5 个 + Phase 2 5 个)
- [ ] `uv run pytest -v` 全 PASS（~36 个 test）
- [ ] `uv run ruff check .` 0 error
- [ ] `uv run mypy src/` 0 error
- [ ] 目录结构与 design doc §4.3 一致
- [ ] `pyproject.toml` 不再含旧 agent dep（无 langgraph / qdrant / clickhouse 等）
- [ ] 三家 provider 都能 mock chat（不需要真实 API key）

---

# Phase 3-6 预告（不在本 plan 内）

Phase 0+1+2 完成后另起 plan 处理:

- **Phase 3**: Bootstrap engine + variable_extraction prompt + HITL 1 + CLI `explain new`
- **Phase 4**: Expansion / Compression / Evaluation / Convergence Engine + Scheduler + Runtime loop
- **Phase 5**: HITL 2 + Render (Markdown + JSON) + CLI `explain continue / show / list`
- **Phase 6**: 10 个种子问题脚本 + baseline CoT + 评估报告

Phase 3 起，**先跑通 Phase 0+1+2** 之后 brainstorm 一次，确认 schema 形态实际跑出来 OK，再写 Phase 3+ plan。
