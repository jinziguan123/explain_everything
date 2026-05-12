# Explain Agent · Phase 2.D-2 实施计划（fan_out 真并发 + 标注工具 + REPL 可观测）

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 Phase 2.D-1 的 13 分钟黑盒体验变成 5 分钟透明可观测；同时铺好漂移率标注闭环，让日常用 explain 能边用边攒 Phase 3 决策数据。

**Architecture:** LLMClient 加 `achat` async API（保留 `chat` sync 给 ingest）；9 处 graph 链路 chat 调用全切 await achat；fan_out `max_concurrency` 3→6 + `return_exceptions=True` 异常隔离；新增 `explain_annotation` 表 + `/annotate` `/stats` 两个 REPL 命令；`_build_environment` 接上现有 `on_node_event` / `on_done` / `on_round` 回调让 REPL 透明。

**Tech Stack:** Python 3.11+、Phase 2.A-2.D-1 已有的 `anthropic` / `openai` SDK（双 SDK 都有 sync + async 版本）/ `langgraph` / `pydantic` / `pytest-asyncio`。

**Phase 2.D-2 不做的：**
- ingest pipeline 改 async（tagger 仍 sync，batch 性能无关紧要）
- followup 链路加 spinner（followup 几秒出结果，价值低）
- LLM 调用次数 / 成本统计（独立可观测性话题）
- `/quiet` 命令、Rich Live progress bar
- 改 `max_rounds=10` 或 EVAL LLM 判定逻辑（L2 范围，本期不动）

**前置依赖：**
- Phase 2.D-1 完工（commit `55483f4`）
- Phase 2.D-2 设计文档（commit `65be367`，`docs/plans/2026-05-12-explain-agent-phase2d2-design.md`）
- 全部 115 单测通过

---

## 任务总览

| # | 任务 | 预估时长 |
|---|---|---|
| 1 | LLMClient 加 `achat` (Anthropic + OpenAI + 2 单测) | 50 min |
| 2 | dimension_worker 3 处 chat → achat + 测试 mock 改造 | 30 min |
| 3 | parse_question chat → achat + 测试 mock 改造 | 15 min |
| 4 | synthesizer chat → achat + 测试 mock 改造 | 15 min |
| 5 | connection_explorer 2 处 chat → achat + 测试 mock 改造 | 20 min |
| 6 | followup chat → achat + 测试 mock 改造 | 15 min |
| 7 | report_builder._call_with_retry 改 async + 测试 mock 改造 | 30 min |
| 8 | main_graph 集成测试 mock 全切 AsyncMock | 20 min |
| 9 | fan_out return_exceptions=True + 异常隔离 + max_concurrency=6 + 单测 | 30 min |
| 10 | fan_out 真并发护栏单测 | 30 min |
| 11 | 003_explain_annotation.sql migration + load_session 扩展 | 20 min |
| 12 | `/annotate` 命令实现 + 3 单测 | 90 min |
| 13 | `/stats` 命令实现 + 单测 | 40 min |
| 14 | REPL _build_environment 接 3 个进度回调 | 25 min |
| 15 | smoke + REPL 端到端验收 + README 更新 | 60 min |

**合计：约 8 小时纯开发，预计 1-2 个工作日完成。**

---

## Task 1: LLMClient 加 achat 双 API

**目标：** `AnthropicClient` / `OpenAIClient` 各加 `async def achat(...)`，内部 lazy 创建 `AsyncAnthropic` / `AsyncOpenAI`；`LLMClient` Protocol 同步升级。

**Files:**
- Modify: `src/explain_agent/llm/client.py`
- Modify: `tests/test_llm_client.py`

**Step 1: 改 Protocol + 加 achat 实现**

替换 `src/explain_agent/llm/client.py` 全文：

```python
from typing import Protocol

from anthropic import Anthropic, AsyncAnthropic
from openai import OpenAI, AsyncOpenAI

from explain_agent.config import LLMConfig


class LLMClient(Protocol):
    cfg: LLMConfig

    def chat(self, system: str, user: str, max_tokens: int = 1024) -> str: ...
    async def achat(self, system: str, user: str, max_tokens: int = 1024) -> str: ...


class AnthropicClient:
    def __init__(self, cfg: LLMConfig, sdk_client=None, async_sdk_client=None):
        self.cfg = cfg
        self.sdk = sdk_client or Anthropic(api_key=cfg.api_key, base_url=cfg.base_url)
        self._async_sdk = async_sdk_client  # lazy 创建

    def chat(self, system: str, user: str, max_tokens: int = 1024) -> str:
        resp = self.sdk.messages.create(
            model=self.cfg.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            text = getattr(block, "text", None)
            if text is not None:
                return text
        return ""

    async def achat(self, system: str, user: str, max_tokens: int = 1024) -> str:
        if self._async_sdk is None:
            self._async_sdk = AsyncAnthropic(api_key=self.cfg.api_key, base_url=self.cfg.base_url)
        resp = await self._async_sdk.messages.create(
            model=self.cfg.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            text = getattr(block, "text", None)
            if text is not None:
                return text
        return ""


class OpenAIClient:
    def __init__(self, cfg: LLMConfig, sdk_client=None, async_sdk_client=None):
        self.cfg = cfg
        self.sdk = sdk_client or OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
        self._async_sdk = async_sdk_client

    def chat(self, system: str, user: str, max_tokens: int = 1024) -> str:
        resp = self.sdk.chat.completions.create(
            model=self.cfg.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""

    async def achat(self, system: str, user: str, max_tokens: int = 1024) -> str:
        if self._async_sdk is None:
            self._async_sdk = AsyncOpenAI(api_key=self.cfg.api_key, base_url=self.cfg.base_url)
        resp = await self._async_sdk.chat.completions.create(
            model=self.cfg.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""


def make_llm_client(cfg: LLMConfig) -> LLMClient:
    if cfg.protocol == "anthropic":
        return AnthropicClient(cfg)
    if cfg.protocol == "openai":
        return OpenAIClient(cfg)
    raise ValueError(f"unknown protocol: {cfg.protocol}")
```

**Step 2: 加 2 个新单测**

在 `tests/test_llm_client.py` 末尾追加：

```python
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_anthropic_achat_lazy_inits_async_sdk():
    """achat 首次调用时才创建 AsyncAnthropic。"""
    from explain_agent.llm.client import AnthropicClient
    from explain_agent.config import LLMConfig

    cfg = LLMConfig(protocol="anthropic", base_url="https://x", api_key="k", model="claude-x")
    client = AnthropicClient(cfg, sdk_client=MagicMock())
    assert client._async_sdk is None

    fake_async = MagicMock()
    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(text="hello")]
    fake_async.messages.create = AsyncMock(return_value=fake_resp)
    client._async_sdk = fake_async

    out = await client.achat(system="s", user="u")
    assert out == "hello"
    fake_async.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_openai_achat_returns_content():
    from explain_agent.llm.client import OpenAIClient
    from explain_agent.config import LLMConfig

    cfg = LLMConfig(protocol="openai", base_url="https://x/v1", api_key="k", model="deepseek-x")
    client = OpenAIClient(cfg, sdk_client=MagicMock())
    fake_async = MagicMock()
    fake_choice = MagicMock()
    fake_choice.message.content = "world"
    fake_async.chat.completions.create = AsyncMock(return_value=MagicMock(choices=[fake_choice]))
    client._async_sdk = fake_async

    out = await client.achat(system="s", user="u")
    assert out == "world"
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_llm_client.py -v`
Expected: 6 passed（4 老的 + 2 新的）

**Step 4: 跑全量回归**

Run: `uv run pytest`
Expected: 117 passed（115 老的 + 2 新的）

**Step 5: Commit**

```bash
git add src/explain_agent/llm/client.py tests/test_llm_client.py
git commit -m "$(cat <<'EOF'
LLMClient 加 achat 双 API（sync + async 共存）

AnthropicClient / OpenAIClient 各加 async achat，内部 lazy 创建
AsyncAnthropic / AsyncOpenAI，避免 ingest 用 sync chat 时也强制
建 async 连接。Protocol 同步升级。原 chat 行为不变，零回归。
EOF
)"
```

---

## Task 2: dimension_worker 3 处 chat → achat

**目标：** `_gen_keywords` / `_is_sufficient` / `_summarize` 三个方法改 async + await achat；`run()` 内调用处加 await；测试 mock 改 AsyncMock。

**Files:**
- Modify: `src/explain_agent/graph/dimension_worker.py`
- Modify: `tests/test_dimension_worker.py`

**Step 1: 改 dimension_worker.py 三个内部方法**

在 `src/explain_agent/graph/dimension_worker.py` 中，把以下三个方法改成 async：

```python
async def _gen_keywords(
    self, target: str, time_window: tuple[date, date],
    market_facts: dict, existing: list[Evidence],
) -> list[str]:
    existing_titles = "; ".join(e.title or e.snippet[:50] for e in existing[-5:])
    user = (
        f"维度: {self.dim['name']}\n"
        f"查询模板: {self.dim['query_template'].format(target=target, time_window=time_window)}\n"
        f"市场锚点: {market_facts.get('snippet', '')}\n"
        f"已有证据(最近 5 条): {existing_titles or '无'}"
    )
    raw = await self.llm.achat(system=QUERY_GEN_SYSTEM, user=user, max_tokens=2000)
    data = _extract_json(raw)
    return data.get("keywords", [target])[:5] if data else [target]
```

```python
async def _is_sufficient(self, evidence: list[Evidence], target: str) -> bool:
    snippets = "\n".join(f"id={e.id}: {e.snippet[:200]}" for e in evidence[:20])
    user = f"维度: {self.dim['name']}\n标的: {target}\n证据:\n{snippets}"
    raw = await self.llm.achat(system=EVAL_SYSTEM, user=user, max_tokens=2000)
    data = _extract_json(raw)
    return bool(data and data.get("sufficient", False))
```

```python
async def _summarize(self, evidence: list[Evidence], target: str, market_facts: dict) -> str:
    snippets = "\n".join(f"id={e.id}: {e.snippet[:300]}" for e in evidence)
    user = (
        f"维度: {self.dim['name']}\n标的: {target}\n"
        f"市场锚点: {market_facts.get('snippet', '')}\n证据:\n{snippets}"
    )
    return await self.llm.achat(system=SUMMARY_SYSTEM, user=user, max_tokens=2000)
```

**Step 2: 改 run() 内调用三个 helper 的地方**

```python
async def run(self, target, time_window, market_facts) -> DimensionResult:
    # ...

    for round_idx in range(1, max_rounds + 1):
        # ...
        keywords = await self._gen_keywords(target, time_window, market_facts, all_evidence)  # 加 await
        new_ev = await self._fetch_all_sources(keywords, target, time_window)
        # ...
        sufficient = await self._is_sufficient(all_evidence, target)  # 加 await
        # ...

    # ...
    summary = await self._summarize(all_evidence, target, market_facts)  # 加 await
    # ...
```

**Step 3: 改测试 mock**

打开 `tests/test_dimension_worker.py`，把所有 `fake_llm.chat = MagicMock(...)` 改为：

```python
from unittest.mock import AsyncMock, MagicMock

fake_llm = MagicMock()
fake_llm.achat = AsyncMock(side_effect=[
    ...原 side_effect 内容
])
# 如果原来用 return_value
fake_llm.achat = AsyncMock(return_value="...")
```

把所有 `fake_llm.chat.assert_called_once()` 改为 `fake_llm.achat.assert_awaited_once()`。

**Step 4: 跑测试**

Run: `uv run pytest tests/test_dimension_worker.py -v`
Expected: 2 passed

**Step 5: Commit**

```bash
git add src/explain_agent/graph/dimension_worker.py tests/test_dimension_worker.py
git commit -m "$(cat <<'EOF'
dimension_worker · 3 处 chat → await achat

_gen_keywords / _is_sufficient / _summarize 三个内部方法改 async；
run() 内调用处加 await。这是 fan_out 真并发的根因修复——sync
llm.chat 不释放 event loop, 让 6 维 semaphore 形同虚设。
EOF
)"
```

---

## Task 3: parse_question chat → achat

**Files:**
- Modify: `src/explain_agent/graph/nodes/parse_question.py`
- Modify: `tests/test_node_parse_question.py`

**Step 1: 改 parse_question.py:45**

把 `raw = llm.chat(system=SYSTEM, user=user, max_tokens=2000)` 改为：

```python
raw = await llm.achat(system=SYSTEM, user=user, max_tokens=2000)
```

**Step 2: 改测试 mock（3 处）**

打开 `tests/test_node_parse_question.py`，把每个测试里：
- `fake_llm = MagicMock()` 保留
- `fake_llm.chat.return_value = "..."` → `fake_llm.achat = AsyncMock(return_value="...")`
- `fake_llm.chat.side_effect = [...]` → `fake_llm.achat = AsyncMock(side_effect=[...])`

加 import：`from unittest.mock import AsyncMock, MagicMock`

**Step 3: 跑测试**

Run: `uv run pytest tests/test_node_parse_question.py -v`
Expected: 3 passed

**Step 4: Commit**

```bash
git add src/explain_agent/graph/nodes/parse_question.py tests/test_node_parse_question.py
git commit -m "parse_question · chat → await achat + 测试 mock 改 AsyncMock"
```

---

## Task 4: synthesizer chat → achat

**Files:**
- Modify: `src/explain_agent/graph/nodes/synthesizer.py`
- Modify: `tests/test_node_synthesizer.py`

**Step 1: 改 synthesizer.py:50**

```python
raw = await llm.achat(system=SYSTEM, user=user, max_tokens=4000)
```

**Step 2: 改测试 mock（3 处）**

同 Task 3 模式，把 `fake_llm.chat.*` 全换成 `fake_llm.achat = AsyncMock(...)`。

**Step 3: 跑测试**

Run: `uv run pytest tests/test_node_synthesizer.py -v`
Expected: 3 passed

**Step 4: Commit**

```bash
git add src/explain_agent/graph/nodes/synthesizer.py tests/test_node_synthesizer.py
git commit -m "synthesizer · chat → await achat + 测试 mock 改 AsyncMock"
```

---

## Task 5: connection_explorer chat → achat

**Files:**
- Modify: `src/explain_agent/graph/nodes/connection_explorer.py`
- Modify: `tests/test_node_connection_explorer.py`

**Step 1: 改 connection_explorer.py 两处**

把 line 81 `raw = llm.chat(...)` → `raw = await llm.achat(...)`
把 line 147 `content = llm.chat(...)` → `content = await llm.achat(...)`

**Step 2: 改测试 mock（5 个测试）**

打开 `tests/test_node_connection_explorer.py`，每个测试的 `fake_llm.chat.side_effect = [...]` 改为：

```python
fake_llm.achat = AsyncMock(side_effect=[...])
```

加 import：`from unittest.mock import AsyncMock, MagicMock`

**Step 3: 跑测试**

Run: `uv run pytest tests/test_node_connection_explorer.py -v`
Expected: 5 passed

**Step 4: Commit**

```bash
git add src/explain_agent/graph/nodes/connection_explorer.py tests/test_node_connection_explorer.py
git commit -m "connection_explorer · 2 处 chat → await achat + 测试 mock 改 AsyncMock"
```

---

## Task 6: followup chat → achat

**Files:**
- Modify: `src/explain_agent/graph/followup.py`
- Modify: `tests/test_followup.py`

**Step 1: 改 followup.py:61**

```python
answer = await llm.achat(system=FOLLOWUP_SYSTEM, user=user, max_tokens=2000)
```

**Step 2: 改测试 mock（3 个测试）**

同 Task 3 模式。

**Step 3: 跑测试**

Run: `uv run pytest tests/test_followup.py -v`
Expected: 3 passed

**Step 4: Commit**

```bash
git add src/explain_agent/graph/followup.py tests/test_followup.py
git commit -m "followup · chat → await achat + 测试 mock 改 AsyncMock"
```

---

## Task 7: report_builder._call_with_retry 改 async

**目标：** `_call_with_retry` 改 async def + `time.sleep` → `await asyncio.sleep`；`report_builder_node` 内 3 处调用加 await；测试 mock 改造。

**Files:**
- Modify: `src/explain_agent/graph/nodes/report_builder.py`
- Modify: `tests/test_node_report_builder.py`

**Step 1: 改 _call_with_retry**

把 [report_builder.py:9-22](src/explain_agent/graph/nodes/report_builder.py:9) 改为：

```python
import asyncio


async def _call_with_retry(
    llm: LLMClient, system: str, user: str, max_tokens: int = 4000,
    attempts: int = 3, backoff: float = 2.0,
) -> str:
    """对 LLM 调用做 attempts 次重试（指数退避），全部失败时返回 ''。"""
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            return await llm.achat(system=system, user=user, max_tokens=max_tokens)
        except Exception as e:
            last_err = e
            if i < attempts - 1:
                await asyncio.sleep(backoff ** i)
    return ""
```

删除文件顶部的 `import time`（不再用 time.sleep）。

**Step 2: 改 _rewrite_dim_report**

它内部调 `_call_with_retry`，本身不是 async。改为：

```python
async def _rewrite_dim_report(
    dim_id: str, dim_result, target: str, market_facts: dict, llm: LLMClient,
) -> str:
    if dim_result["no_data"] or not dim_result["evidence"]:
        return dim_result["mini_summary"]
    evidence_dump = [
        {"id": e.id, "source_type": e.source_type, "snippet": e.snippet[:300]}
        for e in dim_result["evidence"]
    ]
    user = (
        f"维度: {dim_id}\n"
        f"标的: {target}\n"
        f"市场锚点: {market_facts.get('snippet', '')}\n"
        f"该维度证据池:\n{json.dumps(evidence_dump, ensure_ascii=False)}"
    )
    out = await _call_with_retry(llm, DIM_REPORT_SYSTEM, user, max_tokens=4000)
    return out if out else dim_result["mini_summary"]
```

**Step 3: 改 report_builder_node 内部**

找到原 `dim_reports = {dim_id: _rewrite_dim_report(...) for ...}` 字典推导，改为顺序 await：

```python
dim_reports: dict[str, str] = {}
for dim_id, r in dim_results.items():
    dim_reports[dim_id] = await _rewrite_dim_report(
        dim_id, r, state["target"], state["market_facts"], llm
    )
```

找到原 narrative 调用 `raw = _call_with_retry(llm, NARRATIVE_SYSTEM, user, max_tokens=4000)`，改为：

```python
raw = await _call_with_retry(llm, NARRATIVE_SYSTEM, user, max_tokens=4000)
```

**Step 4: 改测试 mock（13 处）**

打开 `tests/test_node_report_builder.py`，把所有 `fake_llm.chat.return_value` / `fake_llm.chat.side_effect` 改为 `fake_llm.achat = AsyncMock(...)`。

加 import：`from unittest.mock import AsyncMock, MagicMock`

**Step 5: 跑测试**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: 13 passed

**Step 6: Commit**

```bash
git add src/explain_agent/graph/nodes/report_builder.py tests/test_node_report_builder.py
git commit -m "$(cat <<'EOF'
report_builder · _call_with_retry 改 async + time.sleep → asyncio.sleep

_rewrite_dim_report 改 async，report_builder_node 内 6 维 dim 报告
顺序 await（不能并发——每个 dim 都用 strong model，并发会被 rate
limit）。narrative 调用加 await。测试 mock 全切 AsyncMock。
EOF
)"
```

---

## Task 8: main_graph 集成测试 mock 全切 AsyncMock

**目标：** `test_main_graph.py` 的 fake_strong_llm / fake_weak_llm 用 AsyncMock 包 achat；同时保持 chat 不破其他测试。

**Files:**
- Modify: `tests/test_main_graph.py`

**Step 1: 改 fake_weak_llm + fake_strong_llm**

把：
```python
fake_weak_llm = MagicMock()
fake_weak_llm.chat.side_effect = lambda **kwargs: json.dumps({...})
```

改为：
```python
fake_weak_llm = MagicMock()
fake_weak_llm.achat = AsyncMock(return_value=json.dumps({
    "target": "半导体",
    "time_window_start": "2026-05-05",
    "time_window_end": "2026-05-12",
    "intent": "up",
}))
```

把：
```python
fake_strong_llm = MagicMock()
fake_strong_llm.chat.side_effect = [...]
```

改为：
```python
fake_strong_llm = MagicMock()
fake_strong_llm.achat = AsyncMock(side_effect=[
    json.dumps({"needs_subbranch": False, "subbranches": []}),
    json.dumps({
        "claims": [
            {"text": "测试叙事甲", "evidence_ids": ["e1"]},
            {"text": "测试叙事乙", "evidence_ids": ["e1"]},
        ],
    }),
    "维度甲重写报告 [e1]",
    "维度乙重写报告 [e1]",
    "维度丙重写报告 [e1]",
    "维度丁重写报告 [e1]",
    "维度戊重写报告 [e1]",
    "维度己重写报告 [e1]",
])
```

加 import：`from unittest.mock import AsyncMock, MagicMock`（替换原来的 MagicMock 单 import）

**Step 2: 跑测试**

Run: `uv run pytest tests/test_main_graph.py -v`
Expected: 1 passed

**Step 3: 跑全量回归**

Run: `uv run pytest`
Expected: 117 passed（与 Task 1 终态一致，主要是 8 个文件全切完）

**Step 4: Commit**

```bash
git add tests/test_main_graph.py
git commit -m "main_graph 集成测试 · fake_llm.chat → fake_llm.achat (AsyncMock)"
```

---

## Task 9: fan_out 异常隔离 + max_concurrency=6

**目标：** `return_exceptions=True` + 异常 worker 落 `no_data=True` 不中断；framework YAML `max_concurrency: 3 → 6`；新增异常隔离单测。

**Files:**
- Modify: `src/explain_agent/graph/nodes/fan_out_dimensions.py`
- Modify: `frameworks/cn_equity_sector_attribution.yaml`
- Modify: `tests/test_node_fan_out.py`

**Step 1: 改 fan_out**

替换 `src/explain_agent/graph/nodes/fan_out_dimensions.py` 全文：

```python
import asyncio
from typing import Callable

from explain_agent.graph.state import AttributionState, DimensionResult
from explain_agent.graph.dimension_worker import DimensionWorker


async def fan_out_dimensions_node(
    state: AttributionState,
    worker_factory: Callable[..., DimensionWorker],
) -> dict:
    framework = state["framework"]
    dims = framework["dimensions"]
    worker_cfg = framework["worker_config"]
    sem = asyncio.Semaphore(worker_cfg.get("max_concurrency", 6))

    async def run_one(dim_cfg: dict) -> tuple[str, DimensionResult]:
        async with sem:
            worker = worker_factory(dimension_config=dim_cfg, worker_config=worker_cfg)
            r = await worker.run(
                target=state["target"],
                time_window=state["time_window"],
                market_facts=state["market_facts"],
            )
            return dim_cfg["id"], r

    results_or_errors = await asyncio.gather(
        *[run_one(d) for d in dims], return_exceptions=True
    )

    results: list[tuple[str, DimensionResult]] = []
    for dim_cfg, r in zip(dims, results_or_errors):
        if isinstance(r, BaseException):
            results.append((dim_cfg["id"], DimensionResult(
                evidence=[], mini_summary=f"维度 worker 失败: {r!r}",
                retry_count=0, no_data=True, confidence="low",
            )))
        else:
            results.append(r)
    return {"dimension_results": dict(results)}
```

**Step 2: 改 framework YAML**

打开 `frameworks/cn_equity_sector_attribution.yaml`，把：
```yaml
worker_config:
  max_rounds: 10
  soft_terminate_no_gain_rounds: 2
  max_concurrency: 3
```
改为：
```yaml
worker_config:
  max_rounds: 10
  soft_terminate_no_gain_rounds: 2
  max_concurrency: 6
```

**Step 3: 加异常隔离单测**

打开 `tests/test_node_fan_out.py`，末尾追加：

```python
@pytest.mark.asyncio
async def test_fan_out_isolates_dimension_failure():
    """1 个 worker 抛异常时其他 5 个继续完成, 失败维度落 no_data=True。"""
    from explain_agent.graph.nodes.fan_out_dimensions import fan_out_dimensions_node
    from explain_agent.graph.state import new_attribution_state, DimensionResult
    from unittest.mock import AsyncMock, MagicMock

    framework = {
        "dimensions": [
            {"id": f"dim_{i}", "name": f"维{i}", "data_sources": []}
            for i in range(6)
        ],
        "worker_config": {"max_rounds": 10, "max_concurrency": 6},
    }

    call_counter = {"n": 0}

    def make_worker(dimension_config, worker_config):
        worker = MagicMock()
        async def fake_run(**kw):
            call_counter["n"] += 1
            if dimension_config["id"] == "dim_2":
                raise RuntimeError("故意挂掉")
            return DimensionResult(
                evidence=[], mini_summary=f"ok {dimension_config['id']}",
                retry_count=1, no_data=False, confidence="medium",
            )
        worker.run = fake_run
        return worker

    state = new_attribution_state("test")
    state["framework"] = framework
    state["target"] = "X"
    state["time_window"] = None
    state["market_facts"] = {}

    out = await fan_out_dimensions_node(state, worker_factory=make_worker)
    assert len(out["dimension_results"]) == 6
    assert out["dimension_results"]["dim_2"]["no_data"] is True
    assert "故意挂掉" in out["dimension_results"]["dim_2"]["mini_summary"]
    for i in [0, 1, 3, 4, 5]:
        assert out["dimension_results"][f"dim_{i}"]["no_data"] is False
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_node_fan_out.py -v`
Expected: 3 passed（2 老的 + 1 新的）

**Step 5: Commit**

```bash
git add src/explain_agent/graph/nodes/fan_out_dimensions.py frameworks/cn_equity_sector_attribution.yaml tests/test_node_fan_out.py
git commit -m "$(cat <<'EOF'
fan_out · 异常隔离 + max_concurrency=6

return_exceptions=True 让 1 个维度抛异常不中断整个 fan_out；失败
维度落 no_data=True 进入后续 report。max_concurrency 3 → 6（6 维
真并发上限）。新增 test_fan_out_isolates_dimension_failure 护栏。
EOF
)"
```

---

## Task 10: fan_out 真并发护栏单测

**目标：** 加一个回归测试，强制 fan_out 必须真并发——6 个 worker 各 sleep 0.5s，总耗时必须 < 1.5s 而非 3s。这是本期重构的核心护栏，防止以后退回 sync 阻塞。

**Files:**
- Modify: `tests/test_node_fan_out.py`

**Step 1: 加并发护栏测试**

在 `tests/test_node_fan_out.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_fan_out_runs_dimensions_concurrently():
    """6 维 worker 各 sleep 0.5s, 总耗时必须 < 1.5s（真并发）, 而非 3s（串行）。"""
    import asyncio, time
    from explain_agent.graph.nodes.fan_out_dimensions import fan_out_dimensions_node
    from explain_agent.graph.state import new_attribution_state, DimensionResult

    sleep_per_dim = 0.5
    framework = {
        "dimensions": [
            {"id": f"dim_{i}", "name": f"维{i}", "data_sources": []}
            for i in range(6)
        ],
        "worker_config": {"max_rounds": 10, "max_concurrency": 6},
    }

    def make_worker(dimension_config, worker_config):
        class FakeWorker:
            async def run(self, target, time_window, market_facts):
                await asyncio.sleep(sleep_per_dim)
                return DimensionResult(
                    evidence=[], mini_summary="ok",
                    retry_count=1, no_data=False, confidence="medium",
                )
        return FakeWorker()

    state = new_attribution_state("test")
    state["framework"] = framework
    state["target"] = "X"
    state["time_window"] = None
    state["market_facts"] = {}

    t0 = time.perf_counter()
    out = await fan_out_dimensions_node(state, worker_factory=make_worker)
    elapsed = time.perf_counter() - t0

    assert len(out["dimension_results"]) == 6
    # 真并发应在 sleep_per_dim ~= 0.5s 略多一点完成
    # 这里给到 1.5s 是宽容上限（CI 慢时也能过），但远小于串行的 3s
    assert elapsed < sleep_per_dim * 3, (
        f"fan_out 看起来是串行运行的: 耗时 {elapsed:.2f}s, "
        f"预期 < {sleep_per_dim * 3:.2f}s。检查 worker 是否真 async, "
        f"以及 LLM client 是否用了 await achat。"
    )
```

**Step 2: 跑测试**

Run: `uv run pytest tests/test_node_fan_out.py::test_fan_out_runs_dimensions_concurrently -v`
Expected: PASS（耗时约 0.5-0.6s）

**Step 3: 跑全量回归**

Run: `uv run pytest`
Expected: 119 passed（117 + 1 异常隔离 + 1 并发护栏）

**Step 4: Commit**

```bash
git add tests/test_node_fan_out.py
git commit -m "$(cat <<'EOF'
fan_out 真并发护栏 · test_fan_out_runs_dimensions_concurrently

6 个 worker 各 sleep 0.5s, 总耗时必须 < 1.5s（真并发）, 而非 3s
（串行）。失败时 assert 消息提示用户检查 await achat 是否落实。
这是本期 async 重构的核心回归保护。
EOF
)"
```

---

## Task 11: 003 migration + load_session 扩展

**目标：** 新增 `explain_annotation` 表 schema；扩展 `load_session` 返回 connection_threads，让 /annotate 能拿到 thread 数据。

**Files:**
- Create: `migrations/mysql/003_explain_annotation.sql`
- Modify: `src/explain_agent/cli/repl/state.py`

**Step 1: 创建 migration**

`migrations/mysql/003_explain_annotation.sql`：

```sql
USE explain_agent;

CREATE TABLE IF NOT EXISTS explain_annotation (
  annotation_id    VARCHAR(64)  NOT NULL,
  session_id       VARCHAR(64)  NOT NULL,
  thread_index     INT          NOT NULL,
  thread_title     VARCHAR(256) NOT NULL,
  label            VARCHAR(16)  NOT NULL,
  note             TEXT         DEFAULT NULL,
  created_at       DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (annotation_id),
  KEY idx_session (session_id),
  KEY idx_label (label),
  UNIQUE KEY uk_session_thread (session_id, thread_index)
) ENGINE=InnoDB;
```

**Step 2: 跑 migration**

Run: `uv run python scripts/run_migrations.py`
Expected: `applying 003_explain_annotation.sql...` + `migrations done`

**Step 3: 扩展 load_session**

打开 `src/explain_agent/cli/repl/state.py`，在 `load_session` 返回字典里追加：

```python
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
    "connection_threads": tree.get("connection_threads", []),  # 新增
    "connection_section": tree.get("connection_section", ""),  # 新增
}
```

**Step 4: 跑现有 state 测试**

Run: `uv run pytest tests/test_repl_state.py -v`
Expected: 4 passed（现有测试不破）

**Step 5: Commit**

```bash
git add migrations/mysql/003_explain_annotation.sql src/explain_agent/cli/repl/state.py
git commit -m "$(cat <<'EOF'
新增 explain_annotation 表 + load_session 返回 connection_threads

为 /annotate 命令做准备。UNIQUE (session_id, thread_index) 保证
单 thread 唯一标签。load_session 扩展返回 connection_threads 与
connection_section（来自 tree_json）。
EOF
)"
```

---

## Task 12: /annotate 命令实现

**目标：** REPL `/annotate` 命令：列出当前 session 的 connection_threads，依次让用户打 🟢/🟡/🔴，写入 explain_annotation 表。

**Files:**
- Modify: `src/explain_agent/cli/repl/commands.py`
- Modify: `src/explain_agent/cli/repl/loop.py`
- Create: `tests/test_repl_annotate.py`

**Step 1: 先写测试**

`tests/test_repl_annotate.py`：

```python
from unittest.mock import MagicMock
from datetime import datetime

import pytest

from explain_agent.cli.repl.commands import handle_annotate
from explain_agent.cli.repl.state import ReplState


def _make_session(threads):
    return {
        "session_id": "s_x",
        "target": "半导体",
        "connection_threads": threads,
        "connection_section": "",
    }


def test_annotate_lists_threads_and_writes_db():
    """两个 thread 全部打标, INSERT 调用 2 次。"""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    # 第一次查已有标注 → 空
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = []
    console = MagicMock()
    state = ReplState()
    state.current_session_id = "s_x"
    state.current_session = _make_session([
        {"title": "T1", "hypothesis": "h", "content": "c1",
         "evidence_ids": ["e1"], "source": "web", "confidence": 4},
        {"title": "T2", "hypothesis": "h", "content": "c2",
         "evidence_ids": ["e2"], "source": "local", "confidence": 3},
    ])

    inputs = iter(["g", "联想到了 TGV", "y", ""])
    handle_annotate(engine=mock_engine, console=console, state=state,
                    prompt_fn=lambda _: next(inputs))

    insert_calls = [c for c in mock_conn.exec_driver_sql.call_args_list
                    if "INSERT INTO explain_agent.explain_annotation" in c.args[0]]
    assert len(insert_calls) == 2
    params0 = insert_calls[0].args[1]
    assert params0[1] == "s_x"
    assert params0[2] == 0           # thread_index
    assert params0[3] == "T1"        # thread_title
    assert params0[4] == "green"
    assert params0[5] == "联想到了 TGV"


def test_annotate_skips_already_annotated():
    """已标过的 thread 跳过, 只标新的。"""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    # 假装 thread_index=0 已标过
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = [(0,)]
    console = MagicMock()
    state = ReplState()
    state.current_session_id = "s_x"
    state.current_session = _make_session([
        {"title": "T1", "hypothesis": "", "content": "", "evidence_ids": [],
         "source": "local", "confidence": 4},
        {"title": "T2", "hypothesis": "", "content": "", "evidence_ids": [],
         "source": "local", "confidence": 4},
    ])

    inputs = iter(["r", "跑题了"])
    handle_annotate(engine=mock_engine, console=console, state=state,
                    prompt_fn=lambda _: next(inputs))

    insert_calls = [c for c in mock_conn.exec_driver_sql.call_args_list
                    if "INSERT INTO explain_agent.explain_annotation" in c.args[0]]
    assert len(insert_calls) == 1
    params = insert_calls[0].args[1]
    assert params[2] == 1            # thread_index 1 (跳过 0)
    assert params[4] == "red"


def test_annotate_no_active_session_prints_warning():
    mock_engine = MagicMock()
    console = MagicMock()
    state = ReplState()
    state.current_session = None

    handle_annotate(engine=mock_engine, console=console, state=state,
                    prompt_fn=lambda _: "")
    console.print.assert_called_once()
    msg = console.print.call_args.args[0]
    assert "session" in msg.lower() or "active" in msg.lower()


def test_annotate_no_threads_prints_warning():
    mock_engine = MagicMock()
    console = MagicMock()
    state = ReplState()
    state.current_session_id = "s_x"
    state.current_session = _make_session([])

    handle_annotate(engine=mock_engine, console=console, state=state,
                    prompt_fn=lambda _: "")
    console.print.assert_called_once()
    msg = console.print.call_args.args[0]
    assert "connection_threads" in msg or "无" in msg
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_repl_annotate.py -v`
Expected: ImportError（handle_annotate 不存在）

**Step 3: 实现 handle_annotate**

在 `src/explain_agent/cli/repl/commands.py` 末尾追加（同时确保 `_KNOWN` 集合也加 "annotate" / "stats"）：

```python
from uuid import uuid4


_KNOWN = {"new", "sessions", "load", "clear", "help", "quit", "annotate", "stats"}


_LABEL_MAP = {"g": "green", "y": "yellow", "r": "red"}


def handle_annotate(engine, console: Console, state: ReplState, prompt_fn=input) -> None:
    if state.current_session is None:
        console.print("[red]当前没有 active session, 用 /load <id> 或先跑一个新问题[/red]")
        return
    threads = state.current_session.get("connection_threads") or []
    if not threads:
        console.print("[yellow]当前 session 无 connection_threads, 无需标注[/yellow]")
        return

    session_id = state.current_session_id
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT thread_index FROM explain_agent.explain_annotation WHERE session_id = %s",
            (session_id,),
        ).fetchall()
        annotated_indices = {r[0] for r in rows}

    console.print(f"\n▎ 当前 session: [bold]{session_id}[/bold]")
    console.print(f"▎ Connection threads ({len(threads)} 条):\n")

    counts = {"green": 0, "yellow": 0, "red": 0}

    for idx, t in enumerate(threads):
        if idx in annotated_indices:
            console.print(f"[{idx+1}/{len(threads)}] {t.get('title', '')}  [dim](已标注, 跳过)[/dim]")
            continue
        console.print(f"\n[{idx+1}/{len(threads)}] [cyan]{t.get('title', '')}[/cyan]")
        console.print(f"      source={t.get('source')}, confidence={t.get('confidence')}")
        console.print(f"      内容: {(t.get('content') or '')[:200]}")
        label_raw = prompt_fn("      标签? (g=🟢真知灼见 / y=🟡合理但平庸 / r=🔴漂移 / s=skip): ").strip().lower()
        if label_raw == "s" or label_raw == "":
            console.print("      [dim]skip[/dim]")
            continue
        label = _LABEL_MAP.get(label_raw)
        if label is None:
            console.print(f"      [red]非法标签 {label_raw!r}, skip[/red]")
            continue
        note = prompt_fn("      备注 (回车跳过): ").strip() or None

        annotation_id = f"ann_{uuid4().hex[:16]}"
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    """
                    INSERT INTO explain_agent.explain_annotation
                      (annotation_id, session_id, thread_index, thread_title, label, note)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (annotation_id, session_id, idx, t.get("title", ""), label, note),
                )
            counts[label] += 1
            console.print("      [green]✓ 已标记[/green]")
        except Exception as e:
            console.print(f"      [red]标注落库失败: {e!r}[/red]")

    summary = f"完成。session {session_id}: {counts['green']} 🟢 / {counts['yellow']} 🟡 / {counts['red']} 🔴"
    console.print(f"\n{summary}")
```

**Step 4: 把 /annotate dispatch 接到 loop.py**

打开 `src/explain_agent/cli/repl/loop.py`，找到 `_run_slash`，在 `quit` 分支后加：

```python
    elif cmd.name == "annotate":
        from explain_agent.cli.repl.commands import handle_annotate
        handle_annotate(engine=env.engine, console=env.console, state=state)
```

**Step 5: 跑测试**

Run: `uv run pytest tests/test_repl_annotate.py -v`
Expected: 4 passed

**Step 6: Commit**

```bash
git add src/explain_agent/cli/repl/commands.py src/explain_agent/cli/repl/loop.py tests/test_repl_annotate.py
git commit -m "$(cat <<'EOF'
REPL · 新增 /annotate 命令（漂移率标注闭环）

依次列出当前 session 的 connection_threads, 提示 g/y/r/s 输入,
实时单条 INSERT 落 explain_annotation 表。已标过的自动跳过, 断网
Ctrl-C 不丢已标的数据。无 active session 或无 threads 时给清晰
提示。prompt_fn 参数让单测可注入 fake input。
EOF
)"
```

---

## Task 13: /stats 命令

**目标：** `/stats` 命令查 `explain_annotation` 表统计漂移率，按 Phase 3 路线图阈值给出建议。

**Files:**
- Modify: `src/explain_agent/cli/repl/commands.py`
- Modify: `src/explain_agent/cli/repl/loop.py`
- Modify: `tests/test_repl_annotate.py`

**Step 1: 写测试**

在 `tests/test_repl_annotate.py` 末尾追加：

```python
def test_stats_groups_by_label_with_phase3_recommendation():
    from explain_agent.cli.repl.commands import handle_stats

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    # 3 个 label 分布 + 5 个最近 red
    mock_conn.exec_driver_sql.side_effect = [
        MagicMock(fetchall=lambda: [("green", 9), ("yellow", 11), ("red", 3)]),
        MagicMock(fetchone=lambda: (12,)),  # session_count
        MagicMock(fetchall=lambda: [
            ("s_a", "区块链与白酒"),
            ("s_b", "ESG 与煤炭"),
        ]),
    ]
    console = MagicMock()

    handle_stats(engine=mock_engine, console=console)
    # 验证至少打印了主表 + 建议
    calls = "\n".join(str(c) for c in console.print.call_args_list)
    assert "23" in calls   # 总数
    assert "39" in calls or "39.1" in calls   # green pct
    # 13% red → 应推荐"不必启动 3-B"
    assert "3-B" in calls or "20%" in calls or "Phase 3" in calls


def test_stats_empty_db():
    from explain_agent.cli.repl.commands import handle_stats

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.side_effect = [
        MagicMock(fetchall=lambda: []),
    ]
    console = MagicMock()
    handle_stats(engine=mock_engine, console=console)
    calls = "\n".join(str(c) for c in console.print.call_args_list)
    assert "无" in calls or "0" in calls or "empty" in calls.lower()
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_repl_annotate.py::test_stats_groups_by_label_with_phase3_recommendation -v`
Expected: FAIL（handle_stats 不存在）

**Step 3: 实现 handle_stats**

在 `src/explain_agent/cli/repl/commands.py` 末尾追加：

```python
def handle_stats(engine, console: Console) -> None:
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            """
            SELECT label, COUNT(*) AS cnt
            FROM explain_agent.explain_annotation
            GROUP BY label
            """
        ).fetchall()
        if not rows:
            console.print("[dim]explain_annotation 无标注数据。先 /annotate 一些 session。[/dim]")
            return
        counts = {r[0]: r[1] for r in rows}
        total = sum(counts.values())
        session_count = conn.exec_driver_sql(
            "SELECT COUNT(DISTINCT session_id) FROM explain_agent.explain_annotation"
        ).fetchone()[0]
        recent_reds = conn.exec_driver_sql(
            """
            SELECT session_id, thread_title FROM explain_agent.explain_annotation
            WHERE label='red' ORDER BY created_at DESC LIMIT 5
            """
        ).fetchall()

    green = counts.get("green", 0)
    yellow = counts.get("yellow", 0)
    red = counts.get("red", 0)
    green_pct = green / total * 100
    yellow_pct = yellow / total * 100
    red_pct = red / total * 100

    console.print(f"\n▎ Connection threads 漂移率统计 (基于 {total} 个已标注 thread):")
    console.print()
    console.print(f"  🟢 真知灼见     {green:>3}   {green_pct:5.1f}%")
    console.print(f"  🟡 合理但平庸   {yellow:>3}   {yellow_pct:5.1f}%")
    console.print(f"  🔴 漂移         {red:>3}   {red_pct:5.1f}%")
    console.print()
    console.print(f"  涉及 session 数: {session_count}")

    if recent_reds:
        console.print(f"  最近 {len(recent_reds)} 个标记的 🔴 议题:")
        for sid, title in recent_reds:
            console.print(f"    - {sid}: \"{title}\"")

    console.print()
    if red_pct < 20:
        console.print("[green]判断: 🔴 < 20% → Phase 3-A 接受度高, 不必启动 3-B (学科正交度地图)[/green]")
    elif red_pct < 30:
        console.print("[yellow]判断: 🔴 在 20-30% 之间 → Phase 3-A 可用但有改进空间, 暂不启动 3-B[/yellow]")
    else:
        console.print("[red]判断: 🔴 ≥ 30% → 启动 Phase 3-B (学科正交度地图引导发散)[/red]")
```

**Step 4: dispatch 接 /stats**

打开 `src/explain_agent/cli/repl/loop.py`，找到刚加的 annotate 分支后加：

```python
    elif cmd.name == "stats":
        from explain_agent.cli.repl.commands import handle_stats
        handle_stats(engine=env.engine, console=env.console)
```

**Step 5: 跑测试**

Run: `uv run pytest tests/test_repl_annotate.py -v`
Expected: 6 passed（4 + 2）

**Step 6: 跑全量回归**

Run: `uv run pytest`
Expected: 125 passed（119 + 4 annotate + 2 stats）

**Step 7: Commit**

```bash
git add src/explain_agent/cli/repl/commands.py src/explain_agent/cli/repl/loop.py tests/test_repl_annotate.py
git commit -m "$(cat <<'EOF'
REPL · 新增 /stats 命令（漂移率统计 + Phase 3 决策建议）

按 label group by 统计已标注 thread, 输出 🟢/🟡/🔴 占比 + 涉及
session 数 + 最近 5 个 red。按 Phase 3 路线图阈值（< 20% / 20-30%
/ ≥ 30%）给建议是否启动 Phase 3-B。
EOF
)"
```

---

## Task 14: REPL _build_environment 接进度回调

**目标：** REPL 接上 smoke 已有的 `on_node_event` / `on_dim_done` / `on_dim_round` 三个回调，让用户能看到 6 维并行进度。

**Files:**
- Modify: `src/explain_agent/cli/repl/loop.py`

**Step 1: 改 _build_environment**

打开 `src/explain_agent/cli/repl/loop.py`，在 `_build_environment()` 内部，把 `worker_factory` 和 `build_main_graph` 调用之间的代码改为：

```python
    def on_dim_done(dim_id, duration, no_data, retry_count, evidence_count):
        flag = "[red]no_data[/red]" if no_data else f"[green]ok[/green] ev={evidence_count}"
        console.print(
            f"  └─ {flag} [bold]{dim_id}[/bold] "
            f"[dim]rounds={retry_count} ({duration:.1f}s)[/dim]"
        )

    def on_dim_round(dim_id, round_idx, max_rounds, keywords, new_count,
                     total_count, duration, reason):
        kw_preview = ",".join(keywords) if keywords else "-"
        console.print(
            f"    [dim cyan]·[/dim cyan] [bold]{dim_id}[/bold] "
            f"[dim]round {round_idx}/{max_rounds}[/dim] kw=[{kw_preview}] "
            f"new={new_count} total={total_count} [dim]({duration:.1f}s, {reason})[/dim]"
        )

    def on_node_event(event, name, *args):
        if event == "start":
            console.print(f"[dim]▶[/dim] [bold]{name}[/bold] ...")
        elif event == "end":
            dur = args[0]
            console.print(f"[green]✓[/green] [bold]{name}[/bold] [dim]({dur:.1f}s)[/dim]")
        elif event == "error":
            dur, err = args
            console.print(f"[red]✗[/red] [bold]{name}[/bold] [dim]({dur:.1f}s)[/dim] {err}")

    def worker_factory(dimension_config, worker_config):
        return DimensionWorker(
            dimension_config=dimension_config, worker_config=worker_config,
            llm=weak, adapter_registry=registry,
            on_done=on_dim_done, on_round=on_dim_round,
        )
    graph = build_main_graph(
        market_adapter=registry["clickhouse_market"],
        worker_factory=worker_factory,
        weak_llm=weak, strong_llm=strong, engine=explain_engine,
        adapter_registry=registry,
        on_node_event=on_node_event,
    )
```

**Step 2: 验证现有 REPL 测试不破**

Run: `uv run pytest tests/test_repl_loop.py tests/test_repl_integration.py -v`
Expected: 5 passed

**Step 3: 跑全量回归**

Run: `uv run pytest`
Expected: 125 passed（同上）

**Step 4: Commit**

```bash
git add src/explain_agent/cli/repl/loop.py
git commit -m "$(cat <<'EOF'
REPL 接进度回调 · 让用户看到 6 维并行的"涌现感"

_build_environment 加 on_node_event / on_dim_done / on_dim_round
三个回调（直接复用 smoke 同款实现）, 传给 worker_factory 与
build_main_graph。fan_out 期间 6 维 round 1 几乎同时打印, 用户
能直观感受到真并发。
EOF
)"
```

---

## Task 15: 端到端 smoke + REPL 验收 + README 更新

**目标：** 跑同样的"为什么半导体板块今天涨"验证 fan_out ≤ 120s + 总耗时 ≤ 5min；REPL 跑一次手动标 2 个 thread + /stats；更新 README 加 Phase 2.D-2 验收清单。

**Files:**
- Modify: `README.md`
- 跑 smoke + REPL 手动验收

**Step 1: 跑 smoke**

Run:
```bash
PYTHONUNBUFFERED=1 uv run python -u scripts/run_main_graph_smoke.py "为什么半导体板块今天涨" 2>&1 | tee /tmp/smoke_2d2.log
```
Expected:
- fan_out 总耗时 ≤ 120s（log 末尾 "✓ fan_out (...)"）
- 总耗时 ≤ 5min
- 6 维 round 1 几乎同时打印（log 中相邻 round 1 行时间戳间隔 ≤ 2s）
- connection_threads ≥ 1
- narrative_claims ≥ 3

如果 fan_out 仍 > 120s，停下报告原因，不要继续。

**Step 2: REPL 手动验收**

Run:
```bash
uv run explain
```
在 prompt 里跑：
- 输入 `为什么半导体板块今天涨`（等约 5 分钟，期间能看到节点 + 维度进度）
- 完成后 `/annotate`，给 2 个 thread 分别打 `g` 和 `y` + 各写一行备注
- `/stats` 看输出，应显示 1 🟢 / 1 🟡 / 0 🔴
- DB 验证：`SELECT * FROM explain_agent.explain_annotation` 应有 2 条
- `/quit` 退出

**Step 3: 更新 README**

打开 `README.md`，在 Phase 2.D-1 验收清单后追加：

```markdown

## Phase 2.D-2 验收状态

- [x] LLMClient 双 API（sync `chat` + async `achat`，AsyncAnthropic / AsyncOpenAI lazy 创建）
- [x] 9 处 graph 链路 chat → await achat（dimension_worker / parse / synth / followup / connection_explorer / report_builder）
- [x] fan_out `return_exceptions=True` + 异常隔离 + max_concurrency=6
- [x] fan_out 真并发护栏单测 `test_fan_out_runs_dimensions_concurrently`
- [x] `/annotate` + `/stats` 漂移率标注闭环
- [x] REPL 进度可观测（接上 on_node_event / on_done / on_round 回调）
- [x] 全部单测通过（125 passed，Phase 2.A/2.B/2.C/2.D-1 全部回归不破）
- [x] 端到端 smoke：fan_out ≤ 120s，总耗时 ≤ 5min（vs 2.D-1 的 306s / 13min）
```

**Step 4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
Phase 2.D-2 完工 · README 加验收清单

fan_out 306s → ≤120s, 总耗时 13min → ≤5min。LLMClient 双 API、
异常隔离、并发护栏、漂移率标注闭环、REPL 透明全部就绪。下一步
是日常用 explain 攒 20-30 case 触发 Phase 3-A/3-B 决策。
EOF
)"
```

**Step 5: Push 到 origin**

```bash
git push origin master
```

---

## 完成后

Phase 2.D-2 完成后的产出：

- ✅ `LLMClient.achat` (Anthropic + OpenAI 双协议)
- ✅ 9 处 graph 节点全 await achat
- ✅ fan_out 真并发（6 维 max_concurrency=6 + 异常隔离）
- ✅ `test_fan_out_runs_dimensions_concurrently` 护栏防退化
- ✅ `explain_annotation` 表 + `/annotate` + `/stats`
- ✅ REPL 接 3 个进度回调（节点级 + 维度级 + 维度轮次级）
- ✅ 端到端 13min → 5min，达到日常用的可接受水平

**下一步**：用 explain 日常跑 10-20 个真实问题（不同领域：半导体 / 新能源 / 政策 / 加密 / 地缘 / 跨学科），边跑边 `/annotate` 给 connection_threads 打标。累计 20-30 个标签后跑 `/stats` 看 🔴 占比：
- < 20% → 不必启动 Phase 3-B，直接做下一个垂直领域（医药 / 加密等）的 framework YAML 复制
- 20-30% → 调 connection_explorer prompt 优化质量再观察
- ≥ 30% → 启动 Phase 3-B（学科正交度地图引导发散）
