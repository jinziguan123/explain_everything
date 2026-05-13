# Cognitive Engine Phase 4 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把 Phase 4 design 实施落地 —— Compression + Evaluation 双 engine + HITL 2 + `explain compress <id>` CLI 命令。把 s_e3fb6675 的 12 现象压成 3-5 个 abstract 候选。

**Architecture:** 9 个 task，TDD 流水线。schema 改动先行（Stage Literal / source / remove_node），然后 LLM errors，然后 2 个新 prompt，然后 Compression / Evaluation 两个 engine，然后 HITL 2，最后 CLI 整合 + 真实 LLM smoke。

**Tech Stack:** Python 3.11+ / pydantic / typer / rich / pyyaml / tenacity / pytest. Phase 0-3 全部直接复用。

**Branch:** `cognitive-engine-mvp` (latest: `118f8a5` Phase 4 design)

**Design Doc:** [2026-05-13-cognitive-engine-phase-4-design.md](2026-05-13-cognitive-engine-phase-4-design.md)

**Phase 0-3 现状:** 96 tests pass, ruff 0 errors, s_e3fb6675 (12 现象 / "为什么年轻人不消费") + s_8b145f65 (另一个 smoke session) 已存盘。

---

## 任务索引

- Task 4.1 Schema: Stage Literal + VariableNode.source
- Task 4.2 ExplanationGraph: remove_node + remove_edge
- Task 4.3 LLM errors module + provider wrap + Phase 3 回填
- Task 4.4 Prompts: compression.yaml + scoring.yaml + loader tests
- Task 4.5 CompressionEngine.propose_candidates
- Task 4.6 EvaluationEngine.score_all
- Task 4.7 HITL 2 review_insights
- Task 4.8 CLI `explain compress` + integrate
- Task 4.9 Acceptance smoke on s_e3fb6675（手动）

---

# Task 4.1: Schema — Stage Literal + VariableNode.source

**目的**: 把 Stage Literal 从 4 个值（`bootstrap_pending` / `running` / `finalize_pending` / `done`）改成 3 个（`bootstrap_pending` / `insight_pending` / `done`），并给 VariableNode 加 `source: Literal["llm", "user"] = "llm"` 字段。

> **注**: `running` / `finalize_pending` 在 Phase 4 不存在（无 loop / no separate finalize），Phase 5 加 Expansion + Loop 时再决定要不要回来。

**Files:**
- Modify: `src/explain_engine/persistence/session.py`（Stage Literal + `_VALID_STAGES`）
- Modify: `src/explain_engine/schema/nodes.py`（VariableNode.source）
- Create: `tests/test_schema_state_stage_literal.py`
- Create: `tests/test_schema_nodes_source.py`

---

## Step 1: 写 Stage Literal 失败测试

`tests/test_schema_state_stage_literal.py`:

```python
"""Stage Literal Phase 4 拓展测试。

Phase 4 Stage Literal: bootstrap_pending / insight_pending / done。
"""

import pytest

from explain_engine.persistence.session import SessionMeta


class TestStageLiteral:
    def test_bootstrap_pending_valid(self) -> None:
        meta = SessionMeta(
            session_id="s_abcd1234",
            question="why",
            stage="bootstrap_pending",
            created_at=1.0,
            updated_at=1.0,
        )
        assert meta.stage == "bootstrap_pending"

    def test_insight_pending_valid(self) -> None:
        meta = SessionMeta(
            session_id="s_abcd1234",
            question="why",
            stage="insight_pending",
            created_at=1.0,
            updated_at=1.0,
        )
        assert meta.stage == "insight_pending"

    def test_done_valid(self) -> None:
        meta = SessionMeta(
            session_id="s_abcd1234",
            question="why",
            stage="done",
            created_at=1.0,
            updated_at=1.0,
        )
        assert meta.stage == "done"

    def test_running_no_longer_valid(self) -> None:
        """Phase 4 砍掉 running / finalize_pending。"""
        with pytest.raises(ValueError, match="invalid stage"):
            SessionMeta(
                session_id="s_abcd1234",
                question="why",
                stage="running",
                created_at=1.0,
                updated_at=1.0,
            )

    def test_finalize_pending_no_longer_valid(self) -> None:
        with pytest.raises(ValueError, match="invalid stage"):
            SessionMeta(
                session_id="s_abcd1234",
                question="why",
                stage="finalize_pending",
                created_at=1.0,
                updated_at=1.0,
            )
```

## Step 2: 运行测试确认失败

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run pytest tests/test_schema_state_stage_literal.py -v
```
Expected: `test_insight_pending_valid` FAIL（ValueError `invalid stage`）；`test_running_no_longer_valid` 和 `test_finalize_pending_no_longer_valid` 也 FAIL（不抛异常，因为当前 Stage 还接受这两值）。

## Step 3: 改 `session.py` 的 Stage Literal

`src/explain_engine/persistence/session.py:21-29` 替换：

```python
Stage = Literal[
    "bootstrap_pending",   # 等 HITL 1
    "insight_pending",     # Compression + Evaluation 完成，等 HITL 2
    "done",                # HITL 2 完成
]

_SESSION_ID_RE = re.compile(r"^s_[0-9a-f]{8}$")
_VALID_STAGES = frozenset({"bootstrap_pending", "insight_pending", "done"})
```

## Step 4: 运行测试确认通过

```bash
uv run pytest tests/test_schema_state_stage_literal.py -v
```
Expected: 5 PASS.

## Step 5: 跑全量测试确认旧 session JSON 反序列化兼容

```bash
uv run pytest -v
```
Expected: 96 PASS（Phase 0-3 不破）+ 5 PASS（新）。如果旧 session JSON 含 `stage: "running"` 或 `"finalize_pending"`，会失败 —— 检查 `sessions/` 目录：

```bash
cd /Users/jinziguan/Desktop/explain_everything && grep -l '"stage": "running"\|"stage": "finalize_pending"' sessions/*.json || echo "no legacy stages"
```
Expected: `no legacy stages`（Phase 3 只产 `bootstrap_pending`）。

## Step 6: Commit

```bash
git add src/explain_engine/persistence/session.py tests/test_schema_state_stage_literal.py
git commit -m "$(cat <<'EOF'
schema · Stage Literal Phase 4 拓展 (insight_pending)

砍掉 running / finalize_pending（Phase 4 无 loop），加 insight_pending。
Phase 5 加 Expansion + Loop 时再决定要不要回来。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Step 7: 写 VariableNode.source 失败测试

`tests/test_schema_nodes_source.py`:

```python
"""VariableNode.source 字段测试（Phase 4 新加）。"""

import pytest
from pydantic import ValidationError

from explain_engine.schema.nodes import VariableNode


class TestVariableNodeSource:
    def test_default_source_is_llm(self) -> None:
        """不传 source 时 default 'llm'（兼容旧 JSON）。"""
        n = VariableNode(
            id="p_001",
            name="x",
            description="x",
            abstraction_level=0,
            confidence=0.7,
            epistemic="observation",
        )
        assert n.source == "llm"

    def test_explicit_user_source(self) -> None:
        n = VariableNode(
            id="p_001",
            name="x",
            description="x",
            abstraction_level=0,
            confidence=0.7,
            epistemic="observation",
            source="user",
        )
        assert n.source == "user"

    def test_invalid_source_raises(self) -> None:
        with pytest.raises(ValidationError):
            VariableNode(
                id="p_001",
                name="x",
                description="x",
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
                source="auto",  # type: ignore[arg-type]
            )

    def test_source_round_trip(self) -> None:
        n = VariableNode(
            id="c_001",
            name="x",
            description="x",
            abstraction_level=1,
            confidence=0.7,
            epistemic="insight",
            source="user",
        )
        d = n.model_dump()
        assert d["source"] == "user"
        n2 = VariableNode.model_validate(d)
        assert n2.source == "user"

    def test_legacy_json_without_source(self) -> None:
        """旧 session JSON 无 source 字段，反序列化默认 'llm'。"""
        legacy = {
            "id": "p_001",
            "name": "x",
            "description": "x",
            "abstraction_level": 0,
            "confidence": 0.7,
            "epistemic": "observation",
            "evidence_ids": [],
        }
        n = VariableNode.model_validate(legacy)
        assert n.source == "llm"
```

## Step 8: 运行测试确认失败

```bash
uv run pytest tests/test_schema_nodes_source.py -v
```
Expected: 全 FAIL（AttributeError: no attribute 'source'）。

## Step 9: 改 `nodes.py` 加 source 字段

`src/explain_engine/schema/nodes.py` 在 `Epistemic` 下方加：

```python
Source = Literal["llm", "user"]
# llm  = LLM 生成（默认）
# user = HITL 用户 add / edit 过的
```

`VariableNode` 类体内 `evidence_ids` 之后加：

```python
    source: Source = "llm"
```

## Step 10: 运行测试确认通过

```bash
uv run pytest tests/test_schema_nodes_source.py -v
```
Expected: 5 PASS.

## Step 11: 跑全量 + 反序列化兼容确认

```bash
uv run pytest -v && uv run python -c "
from explain_engine.persistence.session import SessionStore
from pathlib import Path
store = SessionStore(Path('sessions'))
for m in store.list():
    s = store.load(m.session_id)
    for nid, n in s.state.graph.nodes.items():
        assert n.source in ('llm', 'user'), f'{nid}: {n.source}'
print('legacy session round-trip OK')
"
```
Expected: All tests PASS + `legacy session round-trip OK`.

## Step 12: Commit

```bash
git add src/explain_engine/schema/nodes.py tests/test_schema_nodes_source.py
git commit -m "$(cat <<'EOF'
schema · VariableNode.source 字段 (llm / user)

替代之前的 p_user_NNN 前缀 hack。HITL 1 add / HITL 2 edit 的节点
source="user"，其余默认 "llm"。旧 session JSON 反序列化默认 "llm"。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Task 4.2: ExplanationGraph mutation API

**目的**: 给 `ExplanationGraph` 加 `remove_node` / `remove_edge`，HITL 2 drop 候选时用。

**Files:**
- Modify: `src/explain_engine/schema/graph.py`
- Create: `tests/test_schema_graph_remove.py`

## Step 1: 写失败测试

`tests/test_schema_graph_remove.py`:

```python
"""ExplanationGraph.remove_node / remove_edge 测试。"""

import pytest

from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode


def _node(nid: str, level: int = 0) -> VariableNode:
    return VariableNode(
        id=nid,
        name=nid,
        description="",
        abstraction_level=level,  # type: ignore[arg-type]
        confidence=0.7,
        epistemic="observation",
    )


def _edge(eid: str, src: str, tgt: str) -> RelationEdge:
    return RelationEdge(
        id=eid,
        source_node=src,
        target_node=tgt,
        relation_type="manifests_as",
        confidence=0.7,
        mechanism_description="m",
    )


class TestRemoveNode:
    def test_remove_node_basic(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("a"))
        g.remove_node("a")
        assert "a" not in g.nodes

    def test_remove_node_cascade_outgoing_edges(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("a", level=1))
        g.add_node(_node("b"))
        g.add_edge(_edge("e1", "a", "b"))
        g.remove_node("a")
        assert "a" not in g.nodes
        assert "e1" not in g.edges
        assert "b" in g.nodes

    def test_remove_node_cascade_incoming_edges(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("a", level=1))
        g.add_node(_node("b"))
        g.add_edge(_edge("e1", "a", "b"))
        g.remove_node("b")
        assert "b" not in g.nodes
        assert "e1" not in g.edges
        assert "a" in g.nodes

    def test_remove_node_missing_raises(self) -> None:
        g = ExplanationGraph(root_question="q")
        with pytest.raises(ValueError, match="not found"):
            g.remove_node("nonexistent")


class TestRemoveEdge:
    def test_remove_edge_basic(self) -> None:
        g = ExplanationGraph(root_question="q")
        g.add_node(_node("a", level=1))
        g.add_node(_node("b"))
        g.add_edge(_edge("e1", "a", "b"))
        g.remove_edge("e1")
        assert "e1" not in g.edges
        assert "a" in g.nodes
        assert "b" in g.nodes

    def test_remove_edge_missing_raises(self) -> None:
        g = ExplanationGraph(root_question="q")
        with pytest.raises(ValueError, match="not found"):
            g.remove_edge("nonexistent")
```

## Step 2: 运行确认失败

```bash
uv run pytest tests/test_schema_graph_remove.py -v
```
Expected: All FAIL（AttributeError: no attribute `remove_node`）。

## Step 3: 实现 remove_node + remove_edge

`src/explain_engine/schema/graph.py` 在 `add_edge` 后加：

```python
    def remove_node(self, node_id: str) -> None:
        """删 node + 所有 incident edges（incoming + outgoing）。

        Raises:
            ValueError: node_id 不存在。
        """
        if node_id not in self._nodes:
            raise ValueError(f"node {node_id} not found")
        incident_edge_ids = [
            eid
            for eid, e in self._edges.items()
            if e.source_node == node_id or e.target_node == node_id
        ]
        for eid in incident_edge_ids:
            del self._edges[eid]
        del self._nodes[node_id]
        self._g.remove_node(node_id)

    def remove_edge(self, edge_id: str) -> None:
        """删单 edge，保留两端 node。

        Raises:
            ValueError: edge_id 不存在。
        """
        if edge_id not in self._edges:
            raise ValueError(f"edge {edge_id} not found")
        e = self._edges[edge_id]
        del self._edges[edge_id]
        # networkx DiGraph 同一对节点之间可能有多 edge_id 记录，但本项目
        # add_edge 时一对 (source,target) 在 _g 只有一条边，删 _edges 后
        # 也要把 _g 上的边删掉。
        if self._g.has_edge(e.source_node, e.target_node):
            stored_eid = self._g[e.source_node][e.target_node].get("edge_id")
            if stored_eid == edge_id:
                self._g.remove_edge(e.source_node, e.target_node)
```

## Step 4: 运行测试确认通过

```bash
uv run pytest tests/test_schema_graph_remove.py -v
```
Expected: 6 PASS.

## Step 5: 跑全量确认不破

```bash
uv run pytest -v
```
Expected: 96 (Phase 0-3) + 5 (Stage) + 5 (source) + 6 (remove) = 112 PASS.

## Step 6: Commit

```bash
git add src/explain_engine/schema/graph.py tests/test_schema_graph_remove.py
git commit -m "$(cat <<'EOF'
schema · ExplanationGraph.remove_node / remove_edge

remove_node 级联删 incident edges (in + out)。HITL 2 drop candidate 时用。
Phase 4 mutation 语义：可 append + remove node/edge，不可 update existing。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Task 4.3: LLM errors module + provider wrap + Phase 3 回填

**目的**: 引入 `LLMError` / `SchemaValidationError` 两个异常类，让 3 个 provider client wrap 底层 SDK 异常成这两类，并回填 Phase 3 `_run_new` 用新异常分类。

**Files:**
- Create: `src/explain_engine/llm/errors.py`
- Modify: `src/explain_engine/llm/claude.py` / `openai.py` / `deepseek.py`
- Modify: `src/explain_engine/cli.py`（`_run_new` 回填）
- Create: `tests/test_llm_errors.py`

## Step 1: 写失败测试

`tests/test_llm_errors.py`:

```python
"""LLMError / SchemaValidationError + provider wrap 测试。"""

import pytest

from explain_engine.llm.errors import LLMError, SchemaValidationError


class TestErrorTypes:
    def test_llm_error_is_exception(self) -> None:
        assert issubclass(LLMError, Exception)

    def test_schema_validation_error_is_exception(self) -> None:
        assert issubclass(SchemaValidationError, Exception)

    def test_llm_error_message(self) -> None:
        err = LLMError("network timeout")
        assert str(err) == "network timeout"

    def test_schema_validation_error_message(self) -> None:
        err = SchemaValidationError("missing field 'name'")
        assert "missing field" in str(err)


class TestClaudeWrap:
    """ClaudeClient 把 anthropic SDK 异常 wrap 成 LLMError。"""

    @pytest.mark.asyncio
    async def test_claude_wraps_api_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from anthropic import APIError

        from explain_engine.llm.claude import ClaudeClient
        from explain_engine.llm.client import Message

        async def fake_create(**kwargs):  # type: ignore[no-untyped-def]
            raise APIError(
                message="network down",
                request=None,  # type: ignore[arg-type]
                body=None,
            )

        client = ClaudeClient(api_key="x", default_model="claude-test")
        monkeypatch.setattr(client._client.messages, "create", fake_create)

        with pytest.raises(LLMError, match="network down"):
            await client.chat([Message(role="user", content="hi")])
```

## Step 2: 运行确认失败

```bash
uv run pytest tests/test_llm_errors.py -v
```
Expected: ImportError on `LLMError`。

## Step 3: 实现 errors module

`src/explain_engine/llm/errors.py`:

```python
"""LLM 调用相关异常。"""


class LLMError(Exception):
    """网络 / API / 超时 / rate limit 等底层调用错误。

    所有 provider client 把底层 SDK 异常 wrap 成此类。
    """


class SchemaValidationError(Exception):
    """LLM 输出不 fit Pydantic schema。

    retry 1 次仍失败后抛出。
    """
```

## Step 4: Wrap ClaudeClient 异常

`src/explain_engine/llm/claude.py` 顶部 import 加：

```python
from anthropic import APIError, APIConnectionError, APITimeoutError, RateLimitError
from pydantic import ValidationError

from explain_engine.llm.errors import LLMError, SchemaValidationError
```

`chat` 方法 body 整个 wrap：

```python
    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str | None = None,
    ) -> Response:
        try:
            return await self._chat_impl(messages, schema, model)
        except (APIConnectionError, APITimeoutError, RateLimitError, APIError) as exc:
            raise LLMError(str(exc)) from exc
        except ValidationError as exc:
            raise SchemaValidationError(str(exc)) from exc
```

把原 `chat` body 改名为 `_chat_impl`（相同签名，不带 try）。

## Step 5: 同样 wrap OpenAIClient / DeepSeekClient

`src/explain_engine/llm/openai.py` 同 pattern：catch `openai.APIError` / `openai.APIConnectionError` / `openai.RateLimitError` → `LLMError`；`ValidationError` → `SchemaValidationError`。

`src/explain_engine/llm/deepseek.py`（用 `requests`）：catch `requests.RequestException` / `requests.Timeout` / `json.JSONDecodeError` → `LLMError`；`ValidationError` → `SchemaValidationError`。

> **检查现有 provider 实现**：先 `cat src/explain_engine/llm/{claude,openai,deepseek}.py` 确认底层 SDK 用法再 wrap。如果发现某 provider 已有自己的异常处理逻辑，统一拢成 `LLMError`。

## Step 6: 安装 pytest-asyncio (如果还没装)

```bash
uv run python -c "import pytest_asyncio" 2>/dev/null || uv add --dev pytest-asyncio
```

确认 `pyproject.toml` 有 `asyncio_mode = "auto"` 或测试用 `@pytest.mark.asyncio`。

## Step 7: 运行测试确认通过

```bash
uv run pytest tests/test_llm_errors.py -v
```
Expected: All PASS.

## Step 8: 回填 Phase 3 `_run_new` 异常分类

`src/explain_engine/cli.py:51-55` 改：

```python
    try:
        phenomena = await bootstrap_phenomena(question, llm)
    except SchemaValidationError as exc:
        console.print(f"[red]LLM 输出不合规: {exc}[/red]")
        raise typer.Exit(2) from exc
    except LLMError as exc:
        console.print(f"[red]LLM 调用失败: {exc}[/red]")
        raise typer.Exit(1) from exc
```

加 import：

```python
from explain_engine.llm.errors import LLMError, SchemaValidationError
```

`store.save(session)` 也包 try：

```python
    try:
        store.save(session)
    except OSError as exc:
        console.print(f"[red]session 保存失败: {exc}[/red]")
        raise typer.Exit(3) from exc
```

## Step 9: 全量测试确认 Phase 3 不破

```bash
uv run pytest -v
```
Expected: Phase 3 cli 测试可能因异常分类变化要小调（之前测的是 `Exception` 一把抓，现在拆开了）。检查 `tests/test_cli.py` 中相关 assertion，把通用 Exception 改成具体 `LLMError`。

## Step 10: Commit

```bash
git add src/explain_engine/llm/errors.py src/explain_engine/llm/claude.py src/explain_engine/llm/openai.py src/explain_engine/llm/deepseek.py src/explain_engine/cli.py tests/test_llm_errors.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
llm · LLMError / SchemaValidationError 异常体系

3 provider client wrap 底层 SDK 异常成 LLMError / SchemaValidationError。
回填 Phase 3 _run_new 用新异常分类（reviewer Q3 解决）。

exit code: 0 成功 / 1 LLM / 2 schema / 3 save / 4 stage / 130 Ctrl-C

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Task 4.4: Prompts — compression.yaml + scoring.yaml + loader tests

**Files:**
- Create: `src/explain_engine/llm/prompts/compression.yaml`
- Create: `src/explain_engine/llm/prompts/scoring.yaml`
- Create: `tests/test_llm_prompts_compression_loader.py`
- Create: `tests/test_llm_prompts_scoring_loader.py`

## Step 1: 写失败测试

`tests/test_llm_prompts_compression_loader.py`:

```python
"""compression.yaml 加载测试。"""

from explain_engine.llm.prompts._loader import load_prompt


class TestCompressionPrompt:
    def test_load_compression(self) -> None:
        p = load_prompt("compression")
        assert "system" in p
        assert "user_template" in p

    def test_user_template_placeholders(self) -> None:
        p = load_prompt("compression")
        for ph in ["{question}", "{phenomena_table}", "{min_count}", "{max_count}"]:
            assert ph in p["user_template"], f"missing {ph}"

    def test_system_mentions_compression(self) -> None:
        p = load_prompt("compression")
        # 中文 prompt 中应提到 "压缩" / "abstract" / "候选"
        assert any(w in p["system"] for w in ["压缩", "abstract", "候选", "Compression"])
```

`tests/test_llm_prompts_scoring_loader.py`:

```python
"""scoring.yaml 加载测试。"""

from explain_engine.llm.prompts._loader import load_prompt


class TestScoringPrompt:
    def test_load_scoring(self) -> None:
        p = load_prompt("scoring")
        assert "system" in p
        assert "user_template" in p

    def test_user_template_placeholders(self) -> None:
        p = load_prompt("scoring")
        for ph in [
            "{abstract_name}",
            "{abstract_description}",
            "{concrete_name}",
            "{concrete_description}",
            "{mechanism}",
        ]:
            assert ph in p["user_template"], f"missing {ph}"

    def test_system_mentions_scoring(self) -> None:
        p = load_prompt("scoring")
        assert any(w in p["system"] for w in ["评分", "score", "plausibility", "1-5"])
```

## Step 2: 运行确认失败

```bash
uv run pytest tests/test_llm_prompts_compression_loader.py tests/test_llm_prompts_scoring_loader.py -v
```
Expected: FAIL (`prompt compression not found`).

## Step 3: 写 compression.yaml

`src/explain_engine/llm/prompts/compression.yaml`:

```yaml
description: |
  Compression Engine 用。输入用户问题 + 已收集的具体现象，输出 3-5 个
  abstract candidate variable（每个 = 1 个 abstract node + 多条
  manifests_as edge），用于压缩出隐藏高维变量。

system: |
  你是一个认知引擎的 Compression 模块。
  你的唯一职责：从一组具体现象中**压缩出 3-5 个 abstract 候选变量**，
  让每个 abstract 能解释一组现象。

  规则：
  1. 每个候选是一个**隐藏的高维变量**（如"长期不确定性" / "社会竞争结构"），
     不是现象的简单分类标签
  2. 每个候选必须覆盖**至少 2 个**具体现象（覆盖 1 个就是 rename，不是 compression）
  3. 多个候选应**多角度互不冗余**，避免同义改写（不要"长期不确定性"和"未来焦虑"
     这种近义对）
  4. 每个候选的 description 必须**明确边界**（"指 X，不指 Y" 风格鼓励）
  5. 每条 coverage 的 mechanism 必须解释**为什么这个 abstract 在该现象上显现**，
     不是简单复述现象
  6. 输出严格 JSON，不要额外文字
  7. 宁缺毋滥：能想出 3 个有质量的候选就出 3 个，不要凑数到 5

user_template: |
  问题: {question}

  已收集的现象:
  {phenomena_table}

  请输出 {min_count}-{max_count} 个 abstract candidate variable。
  每个候选的 coverage 的 concrete_id 必须来自上述现象列表。
```

## Step 4: 写 scoring.yaml

`src/explain_engine/llm/prompts/scoring.yaml`:

```yaml
description: |
  Evaluation Engine 用。对单条 manifests_as edge 评估 mechanism_plausibility，
  返回 1-5 整数 + 一句 rationale。

system: |
  你是一个认知引擎的 Evaluation 模块。
  你的唯一职责：评估一条"abstract → concrete"的 manifests_as 边的
  mechanism plausibility，即"这个 abstract 真的能通过这个机制解释这个 concrete 吗？"

  评分规则（1-5 整数）：
  1 = 牵强 / 完全不成立 / 机制描述空洞
  2 = 弱关联 / 机制描述模糊
  3 = 合理但不充分 / 机制描述一般
  4 = 较强解释 / 机制描述清晰
  5 = 强解释 / 机制描述精准且有洞察

  额外约束：
  - 偏严格，不要默认 4-5（默认给 3，要 4-5 必须 mechanism 真的扎实）
  - rationale 一句话，30 字内
  - 输出严格 JSON

user_template: |
  Abstract 变量: {abstract_name}
  Abstract 定义: {abstract_description}

  Concrete 现象: {concrete_name}
  Concrete 描述: {concrete_description}

  Mechanism: {mechanism}

  请评分。
```

## Step 5: 运行 loader 测试

```bash
uv run pytest tests/test_llm_prompts_compression_loader.py tests/test_llm_prompts_scoring_loader.py -v
```
Expected: 6 PASS.

## Step 6: Commit

```bash
git add src/explain_engine/llm/prompts/compression.yaml src/explain_engine/llm/prompts/scoring.yaml tests/test_llm_prompts_compression_loader.py tests/test_llm_prompts_scoring_loader.py
git commit -m "$(cat <<'EOF'
prompts · compression.yaml + scoring.yaml

compression: 一次出 3-5 abstract candidate，每候选含 name/description/coverage
scoring: 单条 manifests_as edge 评 mechanism_plausibility 1-5

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Task 4.5: CompressionEngine.propose_candidates

**目的**: 实现 Compression Engine 的核心函数 —— 调 LLM 出 3-5 候选，校验 concrete_id，淘汰 coverage<2，灌进 graph。

**Files:**
- Create: `src/explain_engine/engines/compression.py`
- Create: `tests/test_engines_compression.py`

## Step 1: 写失败测试

`tests/test_engines_compression.py`:

```python
"""CompressionEngine.propose_candidates 测试。"""

from unittest.mock import AsyncMock

import pytest

from explain_engine.engines.compression import propose_candidates
from explain_engine.llm.client import Response
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _setup_state(n_concrete: int = 12) -> CognitiveState:
    state = CognitiveState.bootstrap("为什么年轻人不消费", budget=20)
    for i in range(1, n_concrete + 1):
        state.graph.add_node(
            VariableNode(
                id=f"p_{i:03d}",
                name=f"现象{i}",
                description=f"现象{i}描述",
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
        )
    return state


def _candidate(name: str, coverage_ids: list[str]) -> dict:
    return {
        "name": name,
        "description": f"{name}的定义",
        "coverage": [
            {"concrete_id": cid, "mechanism": f"{name} → {cid}"}
            for cid in coverage_ids
        ],
    }


def _mock_llm_response(candidates: list[dict]) -> Response:
    return Response(
        text="",
        parsed={"candidates": candidates},
        model="test",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


@pytest.mark.asyncio
class TestPropose:
    async def test_basic_5_candidates(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("长期不确定性", [f"p_{i:03d}" for i in range(1, 10)]),
            _candidate("社会竞争结构", [f"p_{i:03d}" for i in range(2, 9)]),
            _candidate("生活成本上涨", [f"p_{i:03d}" for i in range(1, 6)]),
            _candidate("传统价值观瓦解", [f"p_{i:03d}" for i in range(5, 9)]),
            _candidate("技术替代消费", [f"p_{i:03d}" for i in range(4, 7)]),
        ])

        await propose_candidates(state, llm)

        # 5 abstract nodes 灌入 graph
        abstracts = [n for n in state.graph.nodes.values() if n.abstraction_level == 1]
        assert len(abstracts) == 5
        assert all(n.id.startswith("c_") for n in abstracts)
        assert all(n.epistemic == "insight" for n in abstracts)
        assert all(n.source == "llm" for n in abstracts)
        # insight_candidates 列出 5 个 c_id
        assert sorted(state.insight_candidates) == [f"c_{i:03d}" for i in range(1, 6)]

    async def test_truncate_over_5(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate(f"abs_{i}", ["p_001", "p_002"]) for i in range(8)
        ])
        await propose_candidates(state, llm)
        abstracts = [n for n in state.graph.nodes.values() if n.abstraction_level == 1]
        assert len(abstracts) == 5

    async def test_accept_3_warn_on_low(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate(f"abs_{i}", ["p_001", "p_002"]) for i in range(3)
        ])
        await propose_candidates(state, llm)
        abstracts = [n for n in state.graph.nodes.values() if n.abstraction_level == 1]
        assert len(abstracts) == 3

    async def test_drop_coverage_below_2(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("good_1", ["p_001", "p_002"]),
            _candidate("solo", ["p_003"]),  # 只 1 个 coverage，应淘汰
            _candidate("good_2", ["p_004", "p_005"]),
        ])
        await propose_candidates(state, llm)
        abstracts = [n for n in state.graph.nodes.values() if n.abstraction_level == 1]
        assert len(abstracts) == 2
        assert {n.name for n in abstracts} == {"good_1", "good_2"}

    async def test_invalid_concrete_id_raises(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        # retry 2 次都返同样的坏数据
        llm.chat.return_value = _mock_llm_response([
            _candidate("bad", ["p_999", "p_888"]),  # 不存在的 id
        ])
        with pytest.raises(SchemaValidationError, match="concrete_id"):
            await propose_candidates(state, llm)

    async def test_coverage_overlap_allowed(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("a", ["p_001", "p_002"]),
            _candidate("b", ["p_001", "p_003"]),  # p_001 被两个 abstract 覆盖
        ])
        await propose_candidates(state, llm)
        # p_001 有 2 条 incoming manifests_as
        incoming_count = sum(
            1 for e in state.graph.edges.values()
            if e.target_node == "p_001" and e.relation_type == "manifests_as"
        )
        assert incoming_count == 2

    async def test_edges_created_with_mechanism(self) -> None:
        state = _setup_state()
        llm = AsyncMock()
        llm.chat.return_value = _mock_llm_response([
            _candidate("X", ["p_001", "p_002"]),
        ])
        await propose_candidates(state, llm)
        x_edges = [e for e in state.graph.edges.values() if e.source_node.startswith("c_")]
        assert len(x_edges) == 2
        assert all(e.relation_type == "manifests_as" for e in x_edges)
        assert all(e.mechanism_description for e in x_edges)
```

## Step 2: 运行确认失败

```bash
uv run pytest tests/test_engines_compression.py -v
```
Expected: ModuleNotFoundError on `engines.compression`.

## Step 3: 实现 CompressionEngine

`src/explain_engine/engines/compression.py`:

```python
"""Compression Engine — 把 concrete phenomena 压成 abstract candidate.

设计参考 docs/plans/2026-05-13-cognitive-engine-phase-4-design.md §3。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ValidationError, field_validator

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import LLMError, SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


class _CoverageItem(BaseModel):
    concrete_id: str
    mechanism: str

    @field_validator("mechanism")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class _CompressionCandidate(BaseModel):
    name: str
    description: str
    coverage: list[_CoverageItem]


class CompressionOutput(BaseModel):
    """compression.yaml prompt 的 structured output."""
    candidates: list[_CompressionCandidate]


async def propose_candidates(
    state: CognitiveState,
    llm: LLMClient,
    min_count: int = 3,
    max_count: int = 5,
) -> None:
    """LLM 出 3-5 个 abstract 候选，灌进 state.graph，落 state.insight_candidates。

    Side effects:
        - state.graph: 新增 N 个 level=1 VariableNode (c_001..c_00N) + 若干 edges
        - state.insight_candidates: 设为 [c_001, ..., c_00N]（未排序）

    Raises:
        LLMError: 底层调用失败
        SchemaValidationError: LLM 输出不合规（retry 1 次后仍失败，或 concrete_id 无效）
    """
    concrete_ids = {nid for nid, n in state.graph.nodes.items() if n.abstraction_level == 0}
    phenomena_table = _render_phenomena_table(state)
    prompt = load_prompt("compression")

    messages = [
        Message(role="system", content=prompt["system"]),
        Message(
            role="user",
            content=prompt["user_template"].format(
                question=state.root_question,
                phenomena_table=phenomena_table,
                min_count=min_count,
                max_count=max_count,
            ),
        ),
    ]

    output = await _call_with_retry(llm, messages, concrete_ids)

    # 截断 + 淘汰 coverage<2
    candidates = output.candidates[:max_count]
    candidates = [c for c in candidates if len(c.coverage) >= 2]

    if len(candidates) < min_count:
        logger.warning(
            "Compression 仅产出 %d 个有效候选（min=%d），但接受",
            len(candidates),
            min_count,
        )

    # 灌进 graph
    next_edge_id = _next_edge_id(state)
    state.insight_candidates = []
    for i, cand in enumerate(candidates, start=1):
        c_id = f"c_{i:03d}"
        state.graph.add_node(
            VariableNode(
                id=c_id,
                name=cand.name,
                description=cand.description,
                abstraction_level=1,
                confidence=0.7,
                epistemic="insight",
                source="llm",
            )
        )
        for item in cand.coverage:
            state.graph.add_edge(
                RelationEdge(
                    id=f"e_{next_edge_id:03d}",
                    source_node=c_id,
                    target_node=item.concrete_id,
                    relation_type="manifests_as",
                    confidence=0.7,
                    mechanism_description=item.mechanism,
                )
            )
            next_edge_id += 1
        state.insight_candidates.append(c_id)


def _render_phenomena_table(state: CognitiveState) -> str:
    lines = []
    for nid, n in state.graph.nodes.items():
        if n.abstraction_level == 0:
            lines.append(f"- {nid}: {n.name} — {n.description}")
    return "\n".join(lines)


def _next_edge_id(state: CognitiveState) -> int:
    existing = [int(eid.split("_")[1]) for eid in state.graph.edges if eid.startswith("e_")]
    return (max(existing) + 1) if existing else 1


async def _call_with_retry(
    llm: LLMClient,
    messages: list[Message],
    valid_concrete_ids: set[str],
) -> CompressionOutput:
    """调 LLM，校验 concrete_id；不合规 retry 1 次。"""
    last_exc: Exception | None = None
    for attempt in range(2):
        resp = await llm.chat(messages, schema=CompressionOutput)
        if resp.parsed is None:
            last_exc = SchemaValidationError("LLM 未返回 structured output")
            continue
        try:
            output = CompressionOutput.model_validate(resp.parsed)
        except ValidationError as exc:
            last_exc = SchemaValidationError(f"LLM 输出 schema 不合规: {exc}")
            continue
        # 校验 concrete_id
        bad = [
            item.concrete_id
            for cand in output.candidates
            for item in cand.coverage
            if item.concrete_id not in valid_concrete_ids
        ]
        if bad:
            last_exc = SchemaValidationError(f"未知 concrete_id: {bad}")
            continue
        return output
    assert last_exc is not None
    raise last_exc
```

> **注**: 这里没用 tenacity 装饰器（因为重试条件是"校验失败"不是"网络失败"，逻辑特殊）。`LLMError`（网络失败）由 provider client 直接抛，会冒泡。如果想加 tenacity 包一层处理网络重试，可在 `propose_candidates` 顶层加，但 Phase 4 简化先不加 —— provider client 已经 wrap 异常，不重试也能跑通。

## Step 4: 运行测试确认通过

```bash
uv run pytest tests/test_engines_compression.py -v
```
Expected: 7 PASS.

## Step 5: 全量回归

```bash
uv run pytest -v
```
Expected: 全 PASS.

## Step 6: Commit

```bash
git add src/explain_engine/engines/compression.py tests/test_engines_compression.py
git commit -m "$(cat <<'EOF'
engines · CompressionEngine.propose_candidates

LLM 出 3-5 abstract candidate，每候选灌 1 abstract node + N manifests_as edges。
严格校验 concrete_id，coverage<2 淘汰。retry 1 次 on schema 失败。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Task 4.6: EvaluationEngine.score_all

**目的**: 实现 Evaluation Engine —— 对每个 candidate 算 `compression_gain = representation_reduction × explanatory_preservation`，按 gain 降序重排 `state.insight_candidates`。

**Files:**
- Create: `src/explain_engine/engines/evaluation.py`
- Create: `tests/test_engines_evaluation.py`

## Step 1: 写失败测试

`tests/test_engines_evaluation.py`:

```python
"""EvaluationEngine.score_all 测试。"""

from unittest.mock import AsyncMock

import pytest

from explain_engine.engines.evaluation import score_all
from explain_engine.llm.client import Response
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _build_state(
    n_concrete: int,
    candidates: list[tuple[str, list[str]]],  # (c_id, [covered_concrete_ids])
) -> CognitiveState:
    state = CognitiveState.bootstrap("q", budget=20)
    for i in range(1, n_concrete + 1):
        state.graph.add_node(
            VariableNode(
                id=f"p_{i:03d}",
                name=f"p{i}",
                description="",
                abstraction_level=0,
                confidence=0.7,
                epistemic="observation",
            )
        )
    next_eid = 1
    for cid, covered in candidates:
        state.graph.add_node(
            VariableNode(
                id=cid,
                name=cid,
                description=f"{cid}的定义",
                abstraction_level=1,
                confidence=0.7,
                epistemic="insight",
                source="llm",
            )
        )
        for pid in covered:
            state.graph.add_edge(
                RelationEdge(
                    id=f"e_{next_eid:03d}",
                    source_node=cid,
                    target_node=pid,
                    relation_type="manifests_as",
                    confidence=0.7,
                    mechanism_description=f"{cid}->{pid}",
                )
            )
            next_eid += 1
    state.insight_candidates = [cid for cid, _ in candidates]
    return state


def _score_resp(score: int) -> Response:
    return Response(
        text="",
        parsed={"score": score, "rationale": "x"},
        model="test",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


@pytest.mark.asyncio
class TestScoreAll:
    async def test_representation_reduction_pure(self) -> None:
        """覆盖 6/12，mechanism 全 5 分 → gain = 0.5 × 1.0 = 0.5"""
        state = _build_state(12, [("c_001", [f"p_{i:03d}" for i in range(1, 7)])])
        llm = AsyncMock()
        llm.chat.return_value = _score_resp(5)

        gains = await score_all(state, llm)
        assert gains["c_001"] == pytest.approx(0.5)

    async def test_full_coverage_low_mechanism(self) -> None:
        """覆盖 12/12，mechanism 全 1 分 → gain = 1.0 × 0.2 = 0.2（空洞抽象兜底）"""
        state = _build_state(12, [("c_001", [f"p_{i:03d}" for i in range(1, 13)])])
        llm = AsyncMock()
        llm.chat.return_value = _score_resp(1)
        gains = await score_all(state, llm)
        assert gains["c_001"] == pytest.approx(0.2)

    async def test_sort_descending(self) -> None:
        state = _build_state(
            12,
            [
                ("c_001", ["p_001", "p_002", "p_003"]),  # 3/12
                ("c_002", [f"p_{i:03d}" for i in range(1, 10)]),  # 9/12
                ("c_003", ["p_001", "p_002"]),  # 2/12
            ],
        )
        llm = AsyncMock()
        llm.chat.return_value = _score_resp(4)  # 全部 mechanism=4
        gains = await score_all(state, llm)
        # 0.75 × 0.8 = 0.6 > 0.25 × 0.8 = 0.2 > 0.167 × 0.8 = 0.133
        assert state.insight_candidates == ["c_002", "c_001", "c_003"]

    async def test_invalid_score_raises(self) -> None:
        state = _build_state(12, [("c_001", ["p_001", "p_002"])])
        llm = AsyncMock()
        llm.chat.return_value = Response(
            text="",
            parsed={"score": 99, "rationale": "x"},
            model="test",
            usage={"input_tokens": 0, "output_tokens": 0},
        )
        with pytest.raises(SchemaValidationError):
            await score_all(state, llm)

    async def test_zero_coverage_skipped(self) -> None:
        """candidate 无 outgoing edge → gain=0（理论上 Compression 已淘汰，但 Evaluation 兜底）"""
        state = _build_state(12, [])
        state.graph.add_node(
            VariableNode(
                id="c_001",
                name="x",
                description="x",
                abstraction_level=1,
                confidence=0.7,
                epistemic="insight",
            )
        )
        state.insight_candidates = ["c_001"]
        llm = AsyncMock()
        gains = await score_all(state, llm)
        assert gains["c_001"] == 0.0
```

## Step 2: 运行确认失败

```bash
uv run pytest tests/test_engines_evaluation.py -v
```
Expected: ModuleNotFoundError.

## Step 3: 实现 EvaluationEngine

`src/explain_engine/engines/evaluation.py`:

```python
"""Evaluation Engine — 给 abstract candidate 算 compression_gain。

compression_gain = representation_reduction × explanatory_preservation
  representation_reduction = covered_concrete / total_concrete  (Python)
  explanatory_preservation = mean(mechanism_plausibility 1-5) / 5  (LLM)

设计参考 docs/plans/2026-05-13-cognitive-engine-phase-4-design.md §4。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field, ValidationError

from explain_engine.llm.client import LLMClient, Message
from explain_engine.llm.errors import SchemaValidationError
from explain_engine.llm.prompts._loader import load_prompt
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


class _ScoringOutput(BaseModel):
    score: int = Field(ge=1, le=5)
    rationale: str = ""


async def score_all(state: CognitiveState, llm: LLMClient) -> dict[str, float]:
    """对 state.insight_candidates 每个 candidate 算 compression_gain，
    按 gain 降序重排 state.insight_candidates。

    Returns:
        dict[candidate_id, gain]（供 HITL 2 渲染表格用）

    Raises:
        SchemaValidationError: LLM 评分返回非 1-5 整数
    """
    total_concrete = sum(
        1 for n in state.graph.nodes.values() if n.abstraction_level == 0
    )
    if total_concrete == 0:
        logger.warning("total_concrete == 0, all gains will be 0")

    gains: dict[str, float] = {}
    prompt = load_prompt("scoring")

    for cid in state.insight_candidates:
        cand = state.graph.nodes[cid]
        out_edges = [
            e for e in state.graph.edges.values()
            if e.source_node == cid and e.relation_type == "manifests_as"
        ]
        if not out_edges or total_concrete == 0:
            gains[cid] = 0.0
            continue

        covered = len(out_edges)
        representation_reduction = covered / total_concrete

        scores: list[int] = []
        for e in out_edges:
            concrete = state.graph.nodes[e.target_node]
            score = await _score_edge(
                llm,
                prompt,
                abstract_name=cand.name,
                abstract_description=cand.description,
                concrete_name=concrete.name,
                concrete_description=concrete.description,
                mechanism=e.mechanism_description,
            )
            scores.append(score)

        explanatory_preservation = (sum(scores) / len(scores)) / 5.0
        gains[cid] = representation_reduction * explanatory_preservation

    # 降序重排
    state.insight_candidates = sorted(
        state.insight_candidates, key=lambda cid: gains[cid], reverse=True
    )
    return gains


async def _score_edge(
    llm: LLMClient,
    prompt: dict,
    *,
    abstract_name: str,
    abstract_description: str,
    concrete_name: str,
    concrete_description: str,
    mechanism: str,
) -> int:
    messages = [
        Message(role="system", content=prompt["system"]),
        Message(
            role="user",
            content=prompt["user_template"].format(
                abstract_name=abstract_name,
                abstract_description=abstract_description,
                concrete_name=concrete_name,
                concrete_description=concrete_description,
                mechanism=mechanism,
            ),
        ),
    ]
    for _ in range(2):
        resp = await llm.chat(messages, schema=_ScoringOutput)
        if resp.parsed is None:
            continue
        try:
            return _ScoringOutput.model_validate(resp.parsed).score
        except ValidationError:
            continue
    raise SchemaValidationError(f"scoring 返非 1-5 整数 for {abstract_name} → {concrete_name}")
```

## Step 4: 运行测试确认通过

```bash
uv run pytest tests/test_engines_evaluation.py -v
```
Expected: 5 PASS.

## Step 5: 全量回归

```bash
uv run pytest -v
```

## Step 6: Commit

```bash
git add src/explain_engine/engines/evaluation.py tests/test_engines_evaluation.py
git commit -m "$(cat <<'EOF'
engines · EvaluationEngine.score_all (compression_gain)

compression_gain = (covered/total) × mean(mechanism_plausibility)/5
LLM-as-judge 给每条 manifests_as edge 评 1-5。按 gain 降序重排 insight_candidates。

替代退化的 compression_score (out_degree sum)。tension #1 解掉。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Task 4.7: HITL 2 review_insights

**目的**: 在 `cli_interactive.py` 加 `review_insights()` 函数 —— rich table 总览 + 逐候选 keep/edit/drop/view-full。

**Files:**
- Modify: `src/explain_engine/hitl/cli_interactive.py`（ADD `review_insights`）
- Create: `tests/test_hitl_cli_interactive_insights.py`

## Step 1: 写失败测试

`tests/test_hitl_cli_interactive_insights.py`:

```python
"""HITL 2 review_insights 测试。"""

from unittest.mock import patch

import pytest
from rich.console import Console

from explain_engine.hitl.cli_interactive import review_insights
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _build_state_with_candidates() -> tuple[CognitiveState, dict[str, float]]:
    state = CognitiveState.bootstrap("q", budget=20)
    # 2 concrete
    for i in (1, 2):
        state.graph.add_node(
            VariableNode(
                id=f"p_{i:03d}", name=f"p{i}", description="",
                abstraction_level=0, confidence=0.7, epistemic="observation",
            )
        )
    # 3 candidates
    for i, name in enumerate(["c_001", "c_002", "c_003"], start=1):
        state.graph.add_node(
            VariableNode(
                id=name, name=f"abs_{i}", description=f"def_{i}",
                abstraction_level=1, confidence=0.7, epistemic="insight",
                source="llm",
            )
        )
        state.graph.add_edge(
            RelationEdge(
                id=f"e_{i:03d}", source_node=name, target_node="p_001",
                relation_type="manifests_as", confidence=0.7,
                mechanism_description=f"mech_{i}",
            )
        )
    state.insight_candidates = ["c_001", "c_002", "c_003"]
    gains = {"c_001": 0.6, "c_002": 0.5, "c_003": 0.3}
    return state, gains


class TestReviewInsights:
    def test_keep_all(self) -> None:
        state, gains = _build_state_with_candidates()
        with patch("rich.prompt.Prompt.ask", side_effect=["k", "k", "k"]):
            review_insights(state, gains, console=Console(file=None))
        # 3 abstract 全留
        assert sum(1 for n in state.graph.nodes.values() if n.abstraction_level == 1) == 3
        # insight_candidates 已清空
        assert state.insight_candidates == []

    def test_drop_one(self) -> None:
        state, gains = _build_state_with_candidates()
        with patch("rich.prompt.Prompt.ask", side_effect=["k", "d", "k"]):
            review_insights(state, gains, console=Console(file=None))
        assert "c_002" not in state.graph.nodes
        assert "e_002" not in state.graph.edges  # 级联
        assert "c_001" in state.graph.nodes
        assert "c_003" in state.graph.nodes

    def test_drop_all(self) -> None:
        state, gains = _build_state_with_candidates()
        with patch("rich.prompt.Prompt.ask", side_effect=["d", "d", "d"]):
            review_insights(state, gains, console=Console(file=None))
        assert sum(1 for n in state.graph.nodes.values() if n.abstraction_level == 1) == 0

    def test_edit_updates_source(self) -> None:
        state, gains = _build_state_with_candidates()
        # edit: choice="e", new name, new description
        with patch(
            "rich.prompt.Prompt.ask",
            side_effect=["e", "新名称", "新描述", "k", "k"],
        ):
            review_insights(state, gains, console=Console(file=None))
        assert state.graph.nodes["c_001"].name == "新名称"
        assert state.graph.nodes["c_001"].description == "新描述"
        assert state.graph.nodes["c_001"].source == "user"

    def test_view_full_then_keep(self) -> None:
        state, gains = _build_state_with_candidates()
        # v 后回到 prompt，再 k
        with patch(
            "rich.prompt.Prompt.ask",
            side_effect=["v", "k", "k", "k"],
        ):
            review_insights(state, gains, console=Console(file=None))
        assert "c_001" in state.graph.nodes  # 被 keep
```

## Step 2: 运行确认失败

```bash
uv run pytest tests/test_hitl_cli_interactive_insights.py -v
```
Expected: ImportError on `review_insights`.

## Step 3: 实现 review_insights

在 `src/explain_engine/hitl/cli_interactive.py` 末尾加：

```python
from rich.table import Table


def review_insights(
    state: "CognitiveState",
    gains: dict[str, float],
    console: Console | None = None,
) -> None:
    """HITL 2: 逐候选 keep/edit/drop/view-full。

    Side effects:
        - drop: state.graph.remove_node(cid) 级联删 edges
        - edit: 改 candidate node.name/description + source="user"
        - 完成时: state.insight_candidates 清空
    """
    console = console or Console()

    # Step 1: 总览
    _render_insights_table(state, gains, console)

    # Step 2: 逐候选
    candidates_snapshot = list(state.insight_candidates)
    for idx, cid in enumerate(candidates_snapshot, start=1):
        if cid not in state.graph.nodes:
            continue  # 防御性：已被前一步 drop
        cand = state.graph.nodes[cid]
        gain = gains.get(cid, 0.0)
        cov = _coverage_for(state, cid)
        console.print(
            f"\n[bold cyan][{idx}/{len(candidates_snapshot)}][/bold cyan] "
            f"{cid}  {cand.name}  (gain={gain:.2f})"
        )
        console.print(f"       描述: {cand.description}", style="dim")
        console.print(f"       覆盖 {len(cov)} 条 (默认收起)", style="dim")

        while True:
            choice = Prompt.ask(
                "       [k]eep / [e]dit / [d]rop / [v]iew-full",
                choices=["k", "e", "d", "v"],
                default="k",
            )
            if choice == "k":
                break
            if choice == "d":
                state.graph.remove_node(cid)
                break
            if choice == "e":
                new_name = Prompt.ask("       新名称", default=cand.name)
                new_desc = Prompt.ask("       新描述", default=cand.description)
                # 替换 node（VariableNode 是 BaseModel，直接 mutate field）
                state.graph._nodes[cid] = cand.model_copy(
                    update={"name": new_name, "description": new_desc, "source": "user"}
                )
                break
            if choice == "v":
                for e in cov:
                    target = state.graph.nodes[e.target_node]
                    console.print(
                        f"         {target.id} {target.name}: {e.mechanism_description}",
                        style="dim",
                    )
                # 回到 prompt 重新选

    state.insight_candidates = []
    n_kept = sum(1 for n in state.graph.nodes.values() if n.abstraction_level == 1)
    if n_kept == 0:
        console.print(
            "\n[yellow][WARN] 未保留任何 insight，session 标为 done。"
            "可 explain new 重跑同问题。[/yellow]"
        )
    else:
        console.print(f"\n[green]已保留 {n_kept} 个 insight。[/green]")


def _render_insights_table(
    state: "CognitiveState",
    gains: dict[str, float],
    console: Console,
) -> None:
    table = Table(title="候选 (按 compression_gain 降序)")
    table.add_column("ID", style="cyan")
    table.add_column("名称", style="bold")
    table.add_column("描述", style="dim", max_width=40)
    table.add_column("Coverage", justify="right")
    table.add_column("Gain", justify="right", style="green")
    total = sum(1 for n in state.graph.nodes.values() if n.abstraction_level == 0)
    for cid in state.insight_candidates:
        n = state.graph.nodes[cid]
        cov_count = len(_coverage_for(state, cid))
        table.add_row(
            cid, n.name, n.description,
            f"{cov_count}/{total}", f"{gains.get(cid, 0.0):.2f}",
        )
    console.print(table)


def _coverage_for(state: "CognitiveState", cid: str) -> list["RelationEdge"]:
    from explain_engine.schema.edges import RelationEdge  # noqa: F401
    return [
        e for e in state.graph.edges.values()
        if e.source_node == cid and e.relation_type == "manifests_as"
    ]
```

文件顶部加：

```python
from explain_engine.schema.state import CognitiveState  # for type hint
```

> **注**: `state.graph._nodes[cid] = ...` 是 private member 访问。可选清洁做法：给 `ExplanationGraph` 加 `replace_node(id, new_node)` 公共方法。Phase 4 简化先 private 访问，Task 4.7 完后单独加 `replace_node` 重构（可选）。

## Step 4: 运行测试确认通过

```bash
uv run pytest tests/test_hitl_cli_interactive_insights.py -v
```
Expected: 5 PASS.

## Step 5: 全量回归 + ruff

```bash
uv run pytest -v && uv run ruff check src/ tests/
```

## Step 6: Commit

```bash
git add src/explain_engine/hitl/cli_interactive.py tests/test_hitl_cli_interactive_insights.py
git commit -m "$(cat <<'EOF'
hitl · review_insights (HITL 2 keep/edit/drop/view-full)

rich table 总览 + 逐候选交互。drop 级联删 edges，edit 升级 source="user"。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Task 4.8: CLI `explain compress` + integrate

**目的**: 整合 Compression + Evaluation + HITL 2 + session 落地，落 `explain compress <id>` 命令。处理 stage 校验 + Ctrl-C + insight_pending 重入跳过 LLM。

**Files:**
- Modify: `src/explain_engine/cli.py`
- Create: `tests/test_cli_compress.py`

## Step 1: 写失败测试

`tests/test_cli_compress.py`:

```python
"""CLI explain compress 集成测试。"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from explain_engine.cli import app
from explain_engine.llm.client import Response
from explain_engine.llm.errors import LLMError, SchemaValidationError
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState

runner = CliRunner()


def _setup_bootstrap_session(tmp_dir: Path) -> str:
    state = CognitiveState.bootstrap("为什么年轻人不消费", budget=20)
    for i in range(1, 6):
        state.graph.add_node(
            VariableNode(
                id=f"p_{i:03d}", name=f"p{i}", description=f"d{i}",
                abstraction_level=0, confidence=0.7, epistemic="observation",
            )
        )
    meta = SessionMeta.new(question="为什么年轻人不消费")
    sid = meta.session_id
    store = SessionStore(directory=tmp_dir)
    store.save(Session(meta=meta, state=state))
    return sid


def _mock_settings_to_tmp(monkeypatch: pytest.MonkeyPatch, tmp: Path) -> None:
    from explain_engine import cli as cli_mod
    from explain_engine.config import Settings

    def fake_get_store() -> SessionStore:
        return SessionStore(directory=tmp)

    monkeypatch.setattr(cli_mod, "_get_store", fake_get_store)
    monkeypatch.setattr(
        cli_mod, "Settings", lambda: Settings(
            llm_provider="claude",
            anthropic_api_key="dummy",
            llm_model="x",
            sessions_dir=tmp,
            default_budget=20,
        )
    )


def _comp_response() -> Response:
    return Response(
        text="",
        parsed={"candidates": [
            {"name": "abs_1", "description": "d1",
             "coverage": [{"concrete_id": "p_001", "mechanism": "m1"},
                          {"concrete_id": "p_002", "mechanism": "m2"}]},
            {"name": "abs_2", "description": "d2",
             "coverage": [{"concrete_id": "p_003", "mechanism": "m3"},
                          {"concrete_id": "p_004", "mechanism": "m4"}]},
        ]},
        model="t", usage={"input_tokens": 0, "output_tokens": 0},
    )


def _score_response(s: int = 4) -> Response:
    return Response(
        text="", parsed={"score": s, "rationale": "ok"},
        model="t", usage={"input_tokens": 0, "output_tokens": 0},
    )


class TestCompress:
    def test_full_flow_keep_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = _setup_bootstrap_session(tmp_path)
        _mock_settings_to_tmp(monkeypatch, tmp_path)

        fake_llm = AsyncMock()
        fake_llm.chat.side_effect = [_comp_response()] + [_score_response()] * 10

        with patch("explain_engine.cli.make_client", return_value=fake_llm), \
             patch("rich.prompt.Prompt.ask", side_effect=["k", "k"]):
            result = runner.invoke(app, ["compress", sid])
        assert result.exit_code == 0, result.output

        store = SessionStore(directory=tmp_path)
        s = store.load(sid)
        assert s.meta.stage == "done"
        assert sum(1 for n in s.state.graph.nodes.values()
                   if n.abstraction_level == 1) == 2

    def test_session_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_settings_to_tmp(monkeypatch, tmp_path)
        result = runner.invoke(app, ["compress", "s_00000000"])
        assert result.exit_code == 1

    def test_stage_done_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = _setup_bootstrap_session(tmp_path)
        _mock_settings_to_tmp(monkeypatch, tmp_path)
        store = SessionStore(directory=tmp_path)
        s = store.load(sid)
        s.meta.stage = "done"
        store.save(s)
        result = runner.invoke(app, ["compress", sid])
        assert result.exit_code == 4

    def test_resume_insight_pending_skips_llm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = _setup_bootstrap_session(tmp_path)
        _mock_settings_to_tmp(monkeypatch, tmp_path)
        # 模拟已经 compress + score 跑完，stage=insight_pending
        store = SessionStore(directory=tmp_path)
        s = store.load(sid)
        s.state.graph.add_node(VariableNode(
            id="c_001", name="abs_1", description="d",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
        s.state.insight_candidates = ["c_001"]
        s.meta.stage = "insight_pending"
        store.save(s)

        fake_llm = AsyncMock()
        # LLM 不该被调用
        with patch("explain_engine.cli.make_client", return_value=fake_llm), \
             patch("rich.prompt.Prompt.ask", side_effect=["k"]):
            result = runner.invoke(app, ["compress", sid])
        assert result.exit_code == 0
        assert fake_llm.chat.await_count == 0
        s2 = store.load(sid)
        assert s2.meta.stage == "done"

    def test_llm_error_exit_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = _setup_bootstrap_session(tmp_path)
        _mock_settings_to_tmp(monkeypatch, tmp_path)
        fake_llm = AsyncMock()
        fake_llm.chat.side_effect = LLMError("network down")
        with patch("explain_engine.cli.make_client", return_value=fake_llm):
            result = runner.invoke(app, ["compress", sid])
        assert result.exit_code == 1

    def test_schema_error_exit_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sid = _setup_bootstrap_session(tmp_path)
        _mock_settings_to_tmp(monkeypatch, tmp_path)
        fake_llm = AsyncMock()
        fake_llm.chat.side_effect = SchemaValidationError("bad output")
        with patch("explain_engine.cli.make_client", return_value=fake_llm):
            result = runner.invoke(app, ["compress", sid])
        assert result.exit_code == 2
```

## Step 2: 运行确认失败

```bash
uv run pytest tests/test_cli_compress.py -v
```
Expected: `No such command 'compress'`.

## Step 3: 实现 `compress` 命令

`src/explain_engine/cli.py` 加 import：

```python
from explain_engine.engines.compression import propose_candidates
from explain_engine.engines.evaluation import score_all
from explain_engine.hitl.cli_interactive import review_insights
```

在 `list_cmd` 之前加：

```python
@app.command()
def compress(
    session_id: str = typer.Argument(..., help="session id (s_xxxxxxxx)"),
) -> None:
    """对已 bootstrap 的 session 跑 Compression + Evaluation + HITL 2。"""
    asyncio.run(_run_compress(session_id))


async def _run_compress(session_id: str) -> None:
    store = _get_store()
    try:
        session = store.load(session_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    stage = session.meta.stage
    if stage == "done":
        console.print(f"[red]session {session_id} 已 done，不可重跑。[/red]")
        raise typer.Exit(4)
    if stage not in ("bootstrap_pending", "insight_pending"):
        console.print(f"[red]session {session_id} stage={stage}，不支持 compress。[/red]")
        raise typer.Exit(4)

    settings = Settings()
    llm = make_client(settings)

    gains: dict[str, float]

    if stage == "bootstrap_pending":
        console.print("[INFO] 调 LLM 生成 abstract 候选...")
        try:
            await propose_candidates(session.state, llm)
        except SchemaValidationError as exc:
            console.print(f"[red]LLM 输出不合规: {exc}[/red]")
            raise typer.Exit(2) from exc
        except LLMError as exc:
            console.print(f"[red]LLM 调用失败: {exc}[/red]")
            raise typer.Exit(1) from exc

        console.print(f"[INFO] 生成 {len(session.state.insight_candidates)} 个候选，开始评分...")
        try:
            gains = await score_all(session.state, llm)
        except SchemaValidationError as exc:
            console.print(f"[red]评分输出不合规: {exc}[/red]")
            raise typer.Exit(2) from exc
        except LLMError as exc:
            console.print(f"[red]评分 LLM 调用失败: {exc}[/red]")
            raise typer.Exit(1) from exc

        session.meta.stage = "insight_pending"
        try:
            store.save(session)
            console.print("[INFO] 中间状态已保存 (stage=insight_pending)。")
        except OSError as exc:
            console.print(f"[red]保存失败: {exc}[/red]")
            raise typer.Exit(3) from exc
    else:  # stage == "insight_pending"
        console.print("[INFO] 检测到 stage=insight_pending，跳过 LLM 直接进入审查。")
        # 从 graph 反推 gains（需要 score 信息）—— Phase 4 简化：重算 gain 需 LLM。
        # 妥协方案：insight_pending 时 gains 都设 0.0（用户已经看不到 gain 排序，但 graph 顺序已是降序）
        gains = {cid: 0.0 for cid in session.state.insight_candidates}

    # HITL 2
    review_insights(session.state, gains, console=console)

    session.meta.stage = "done"
    try:
        store.save(session)
    except OSError as exc:
        console.print(f"[red]保存失败: {exc}[/red]")
        raise typer.Exit(3) from exc

    console.print(f"\n[green]Session {session_id} 已完成。[/green]")
```

> **注**: insight_pending 重入时 gains 信息丢失（没在 session 里持久化 gain 字段）。Phase 4 简化：重入时 gains 都设 0.0。**Phase 5 可考虑**把 gain 持久化到 session.json，或者重入时重跑 score_all（贵）。

## Step 4: 运行测试确认通过

```bash
uv run pytest tests/test_cli_compress.py -v
```
Expected: 6 PASS.

## Step 5: 全量回归 + ruff

```bash
uv run pytest -v && uv run ruff check src/ tests/
```
Expected: 全 PASS, 0 ruff errors.

## Step 6: 检查 `compression_score` 旧方法（acceptance §10）

`src/explain_engine/schema/graph.py:45-52` 的 `compression_score()` 已被 `compression_gain` 替代。**直接删掉**（如果没有任何测试或代码引用）：

```bash
grep -rn "compression_score" src/ tests/
```

如果只剩 graph.py 内部定义和可能的旧测试引用，删方法 + 对应测试。

## Step 7: Commit

```bash
git add src/explain_engine/cli.py tests/test_cli_compress.py src/explain_engine/schema/graph.py
git commit -m "$(cat <<'EOF'
cli · explain compress (Compression + Evaluation + HITL 2)

整合 propose_candidates + score_all + review_insights。stage 校验：
- bootstrap_pending → 全跑 → insight_pending → HITL 2 → done
- insight_pending → 跳过 LLM 直接 HITL 2
- done → exit 4

顺手删退化的 compression_score (被 compression_gain 替代)。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Task 4.9: Acceptance smoke on s_e3fb6675（手动）

**目的**: 跑真实 LLM 验证 Phase 4 端到端流。**这是手动 task，不写测试，但要核对 acceptance §10 各条**。

## Step 1: 确认 s_e3fb6675 当前 stage

```bash
cd /Users/jinziguan/Desktop/explain_everything && uv run python -c "
from explain_engine.persistence.session import SessionStore
from pathlib import Path
s = SessionStore(Path('sessions')).load('s_e3fb6675')
print(s.meta.stage, len(s.state.graph.nodes))
"
```
Expected: `bootstrap_pending 12`。

> **如果不是 bootstrap_pending**（比如上次试跑变成 insight_pending 或 done），重置：
> ```bash
> uv run python -c "
> from explain_engine.persistence.session import SessionStore
> from pathlib import Path
> store = SessionStore(Path('sessions'))
> s = store.load('s_e3fb6675')
> # 删 abstract nodes + abstract edges
> for nid in list(s.state.graph.nodes):
>     if s.state.graph.nodes[nid].abstraction_level == 1:
>         s.state.graph.remove_node(nid)
> s.state.insight_candidates = []
> s.meta.stage = 'bootstrap_pending'
> store.save(s)
> "
> ```

## Step 2: 确认 `.env` 配置好 LLM provider

```bash
grep -E "^(LLM_PROVIDER|.*_API_KEY)" .env
```
Expected: 至少一个 provider key 存在。

## Step 3: 跑 `explain compress s_e3fb6675`

```bash
uv run explain compress s_e3fb6675
```

**预期交互流**：
1. console: `[INFO] 调 LLM 生成 abstract 候选...`
2. 等 ~30 秒（compression LLM 调用）
3. console: `[INFO] 生成 3-5 个候选，开始评分...`
4. 等 ~60-120 秒（5 候选 × ~7 coverage = ~35 次 scoring LLM 调用）
5. console: `[INFO] 中间状态已保存 (stage=insight_pending)。`
6. rich table 显示候选 + gain
7. 逐候选 `[k/e/d/v]` 提示
8. console: `Session s_e3fb6675 已完成。`

## Step 4: 核对 acceptance §10

逐项打勾：

- [ ] **§10.1**: 3-5 候选生成 + 按 gain 降序 + HITL 2 全 path 跑通
- [ ] **§10.2 wow check (informal)**: 候选里至少一个名字接近 "长期不确定性" / "社会竞争结构" 这类高维隐藏变量，gain ≥ 0.5，coverage ≥ 7/12
- [ ] **§10.3**: HITL 2 跑到一半 Ctrl-C 后重跑 `explain compress s_e3fb6675`，能从 insight_pending 跳过 LLM 直接审查
- [ ] **§10.4**: `uv run pytest -v` ≥ 131 PASS
- [ ] **§10.5**: Phase 0-3 96 测试不破（包含在 §10.4）
- [ ] **§10.6**: `uv run ruff check src/ tests/` 0 error
- [ ] **§10.7**: Phase 3 `_run_new` 用 LLMError / SchemaValidationError（Task 4.3 完成）
- [ ] **§10.8**: 旧 `compression_score()` 方法已删（Task 4.8 Step 6）

## Step 5: 手动验证 §10.3 Ctrl-C 重入

```bash
# 重置 session 回 bootstrap_pending（如 Step 1 的 fallback）
# 然后：
uv run explain compress s_e3fb6675
# 等候选 + 评分跑完，看到 rich table 后立即 Ctrl-C
# 然后：
uv run explain compress s_e3fb6675
# 应该 console.print "检测到 stage=insight_pending，跳过 LLM 直接进入审查。"
# 且交互立即开始（不等 LLM）
```

## Step 6: 把 session 命名标识 + 跑出来的 wow 候选记到 design doc §10

如果 wow check 通过，把实际 candidate 名字 + gain 写进 design doc §10 作为 evidence。例：

> Phase 4 acceptance run 2026-05-XX: s_e3fb6675 跑出 5 候选，gain 最高是 c_001 "长期不确定性" (gain=0.61, coverage 9/12) —— wow check PASS。

## Step 7: Commit acceptance evidence

```bash
git add docs/plans/2026-05-13-cognitive-engine-phase-4-design.md sessions/s_e3fb6675.json
git commit -m "$(cat <<'EOF'
acceptance · Phase 4 smoke pass on s_e3fb6675

跑真实 LLM 验证 explain compress 端到端流。Acceptance §10 全项 PASS。
design doc §10 加 wow evidence。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# 完成判定

- [ ] 8 个 task commit 完成（Task 4.1-4.8 各 1-2 commit + Task 4.9 acceptance commit）
- [ ] `uv run pytest -v`: ≥ 131 PASS
- [ ] `uv run ruff check src/ tests/`: 0 errors
- [ ] s_e3fb6675 跑 `explain compress` 真实 LLM 端到端通
- [ ] design doc §10 wow check informal 通过 + evidence 记录

---

# Phase 4 之后

Phase 5 brainstorm 启动条件（从 design doc §12 抄）：
- Phase 4 跑通 ≥1 真实 session（s_e3fb6675 + 至少 1 个新种子问题）
- 知道 HITL 2 实际体验如何
- 知道 cost / latency 数量级（36 次 LLM 调用是否可接受）
- 知道 mechanism scoring 离散度是否够

Phase 5 起点（Expansion + Loop）：
- `ExpansionEngine`（上溯到 driver、横向 amplifier）
- Scheduler（轮转 / attention-based）
- 完整 Reasoning Loop
- `CognitiveState.attention_map` / `reasoning_trace` 加字段
- `explain expand` / `explain run` 命令
- 多轮 Compression（同一 session 多次跑）
- 持久化 gain 到 session.json（解决 Phase 4 insight_pending 重入 gain 丢失）
