# Explain Agent · Phase 2.A 实施计划（LangGraph 骨架 + Main Graph）

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 搭建 LangGraph 主流程骨架，把 Phase 1 的 4 个 P0 Adapter 串成端到端归因 pipeline，能对"为什么半导体板块涨停"这类问题输出一份 6 维归因报告。

**Architecture:** 用 LangGraph 的 `StateGraph` 编排节点。State 是 TypedDict，节点是 async 函数。6 维 worker 通过 `asyncio.Semaphore(3)` 受控并发。报告生成在 Phase 2.A 是**简化版**（直接拼接 mini_summary），Phase 2.B 再用 strong model 改写叙事 + 五层幻觉防控。

**Tech Stack:** Python 3.11+、`langgraph`（新增依赖）、`pyyaml`（YAML 解析）、Phase 1 已有的 sqlalchemy/clickhouse-connect/qdrant-client/anthropic/openai/sentence-transformers/akshare/pytest-asyncio

**Phase 2.A 不做的：**
- Strong model 叙事段 + 五层幻觉防控（Phase 2.B）
- CLI REPL + Followup Graph（Phase 2.C）
- Lazy News Ingest（Phase 2.C）
- Eval Framework（Phase 2.D）

**前置依赖：**
- Phase 1 已完工（commit `35f7804`）
- 用户已配置好 `.env` 真实凭证
- BGE-M3 模型已缓存（Phase 1 已用过）
- Qdrant 已有部分 news 语料（用户后台 ingest 中）

---

## 任务总览

| # | 任务 | 预计时长 |
|---|---|---|
| 1 | LangGraph 依赖 + 目录结构 | 15 min |
| 2 | AttributionState schema + 辅助类型 | 30 min |
| 3 | Framework YAML 配置 + Loader | 40 min |
| 4 | 节点 · parse_question | 45 min |
| 5 | 节点 · domain_router | 20 min |
| 6 | 节点 · fetch_market_facts | 40 min |
| 7 | 单维 worker · mini ReAct 内核 | 90 min |
| 8 | 节点 · fan_out_dimensions（Semaphore=3） | 50 min |
| 9 | 节点 · evidence_synthesizer | 50 min |
| 10 | 节点 · dynamic_subbranches | 30 min |
| 11 | 节点 · report_builder（Phase 2.A 简化版） | 40 min |
| 12 | 节点 · persist | 40 min |
| 13 | Main Graph 装配 + LangGraph 集成测试 | 60 min |
| 14 | 端到端真实案例验收（手动） | 45 min |

**合计：约 9.5 小时纯开发，预计 2-3 个工作日完成。**

---

## Task 1: LangGraph 依赖 + 目录结构

**目标：** 安装 `langgraph` 和 `pyyaml`，创建 graph 模块目录骨架。

**Files:**
- Modify: `pyproject.toml`（uv 会自动改）
- Create: `src/explain_agent/graph/__init__.py`（空）
- Create: `src/explain_agent/graph/nodes/__init__.py`（空）
- Create: `frameworks/.gitkeep`

**Step 1: 添加依赖**

Run:
```bash
uv add langgraph pyyaml
```
Expected: 成功安装 langgraph 0.4+ 和 pyyaml，写入 pyproject.toml 和 uv.lock。

**Step 2: 创建目录骨架**

```bash
mkdir -p src/explain_agent/graph/nodes
mkdir -p frameworks
touch src/explain_agent/graph/__init__.py
touch src/explain_agent/graph/nodes/__init__.py
touch frameworks/.gitkeep
```

**Step 3: 验证 langgraph 可 import**

Run:
```bash
uv run python -c "from langgraph.graph import StateGraph; print('OK')"
```
Expected: 打印 `OK`，无 import error。

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock src/explain_agent/graph/ frameworks/
git commit -m "新增 LangGraph 依赖与 graph 模块骨架

为 Phase 2.A 编排层做准备。frameworks/ 目录预留给领域 YAML 配置。"
```

---

## Task 2: AttributionState schema + 辅助类型

**目标：** 定义 `AttributionState` TypedDict 与 `DimensionResult`、`SubBranchSpec`、`Citation` 等辅助类型。

**Files:**
- Create: `src/explain_agent/graph/state.py`
- Create: `tests/test_graph_state.py`

**Step 1: 写测试**

`tests/test_graph_state.py`：

```python
from datetime import date, datetime
from explain_agent.graph.state import (
    AttributionState, DimensionResult, SubBranchSpec, Citation,
    new_attribution_state,
)


def test_new_state_has_required_defaults():
    s = new_attribution_state(raw_question="为什么半导体涨停", session_id="s1")
    assert s["raw_question"] == "为什么半导体涨停"
    assert s["session_id"] == "s1"
    assert isinstance(s["asked_at"], datetime)
    assert s["dimension_results"] == {}
    assert s["subbranches"] == []
    assert s["needs_subbranch"] is False
    assert s["citations"] == []
    assert s["errors"] == []
    assert s["llm_calls"] == {}
    assert s["total_cost"] == 0.0


def test_dimension_result_typeddict():
    r: DimensionResult = {
        "evidence": [],
        "mini_summary": "本维度无相关证据",
        "retry_count": 3,
        "no_data": True,
        "confidence": "low",
    }
    assert r["no_data"] is True


def test_subbranch_spec():
    spec: SubBranchSpec = {
        "name": "美国 HBM 制裁",
        "query_hints": ["BIS", "HBM"],
    }
    assert spec["name"] == "美国 HBM 制裁"


def test_citation():
    c: Citation = {
        "evidence_id": "e_001",
        "url": "https://example.com",
        "snapshot_id": "s_001",
        "source_type": "news",
    }
    assert c["evidence_id"] == "e_001"
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_graph_state.py -v`
Expected: ImportError（state 模块不存在）。

**Step 3: 实现 state.py**

`src/explain_agent/graph/state.py`：

```python
from datetime import date, datetime
from typing import Literal, TypedDict
from uuid import uuid4
from explain_agent.core.types import Evidence


class DimensionResult(TypedDict):
    evidence: list[Evidence]
    mini_summary: str
    retry_count: int
    no_data: bool
    confidence: Literal["high", "medium", "low"]


class SubBranchSpec(TypedDict):
    name: str
    query_hints: list[str]


class Citation(TypedDict):
    evidence_id: str
    url: str | None
    snapshot_id: str | None
    source_type: str


class AttributionState(TypedDict, total=False):
    # 输入
    raw_question: str
    asked_at: datetime
    session_id: str

    # parse
    target: str
    time_window: tuple[date, date]
    intent: Literal["up", "down", "volatile", "general"]

    # router/framework
    domain_id: str
    framework: dict

    # 客观锚点
    market_facts: dict

    # 维度结果
    dimension_results: dict[str, DimensionResult]

    # 动态扩展
    needs_subbranch: bool
    subbranches: list[SubBranchSpec]
    subbranch_results: dict[str, DimensionResult]

    # 最终输出
    narrative: str
    dimension_reports: dict[str, str]
    citations: list[Citation]
    confidence: Literal["high", "medium", "low"]

    # 元数据
    llm_calls: dict[str, int]
    total_cost: float
    errors: list[str]


def new_attribution_state(raw_question: str, session_id: str | None = None) -> AttributionState:
    return {
        "raw_question": raw_question,
        "asked_at": datetime.now(),
        "session_id": session_id or f"s_{uuid4().hex[:8]}",
        "dimension_results": {},
        "subbranches": [],
        "subbranch_results": {},
        "needs_subbranch": False,
        "narrative": "",
        "dimension_reports": {},
        "citations": [],
        "confidence": "medium",
        "llm_calls": {},
        "total_cost": 0.0,
        "errors": [],
    }
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_graph_state.py -v`
Expected: 4 passed。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/state.py tests/test_graph_state.py
git commit -m "定义 AttributionState schema 与辅助类型

DimensionResult/SubBranchSpec/Citation 都用 TypedDict。
new_attribution_state 提供合理默认值，便于节点逐步填充。"
```

---

## Task 3: Framework YAML 配置 + Loader

**目标：** 写 `cn_equity_sector_attribution.yaml`（6 维配置），写 `framework_loader.py`（加载 + 校验）。

**Files:**
- Create: `frameworks/cn_equity_sector_attribution.yaml`
- Create: `src/explain_agent/graph/framework_loader.py`
- Create: `tests/test_framework_loader.py`

**Step 1: 写 YAML**

`frameworks/cn_equity_sector_attribution.yaml`：

```yaml
domain_id: cn_equity_sector_attribution
display_name: A股板块/主题异动归因

match_patterns:
  - "为什么.*(板块|主题|概念|行业).*(涨|跌|异动|涨停|大跌)"
  - "(板块|行业).*怎么了"

dimensions:
  - id: policy
    name: 政策/宏观
    priority: 1
    data_sources:
      - news_corpus
    query_template: "最近 {time_window} 内与 {target} 相关的政策、监管表态、高层讲话"
    evidence_filter:
      tag_filter:
        policy_type: not_null
      min_relevance: 0.55

  - id: industry_chain
    name: 产业链/基本面
    priority: 1
    data_sources:
      - mysql_fundamentals
      - clickhouse_market
      - news_corpus
    query_template: "{target} 产业链上下游近期变化：原料、需求、订单、产能、龙头公司动态"
    needs_market_context: true

  - id: capital_flow
    name: 资金面
    priority: 2
    data_sources:
      - akshare_capital_flow
      - clickhouse_market
    query_template: "{time_window} 内 {target} 资金流向、龙虎榜、ETF 申赎、北向"

  - id: sentiment_event
    name: 情绪/事件
    priority: 2
    data_sources:
      - news_corpus
    query_template: "{time_window} 内 {target} 相关的突发新闻、社交热度、关联概念联动"
    evidence_filter:
      tag_filter:
        event_type_in: ["事件", "产业链"]

  - id: international
    name: 国际映射
    priority: 3
    data_sources:
      - news_corpus
    query_template: "{time_window} 内海外同行业表现、地缘事件、商品价格对 {target} 的潜在影响"
    evidence_filter:
      tag_filter:
        event_type: 海外

  - id: technical
    name: 技术面
    priority: 3
    data_sources:
      - clickhouse_market
    query_template: "{target} 板块及其龙头股的技术形态、量价关系、近 N 日突破/支撑"

worker_config:
  max_rounds: 10
  soft_terminate_no_gain_rounds: 2
  max_concurrency: 3

models:
  parse: weak
  intent_classifier: weak
  query_gen: weak
  evidence_eval: weak
  mini_summary: weak
  synthesizer: strong
  narrative: strong
  dimension_report: weak
```

**Step 2: 写 Loader 测试**

`tests/test_framework_loader.py`：

```python
import pytest
from pathlib import Path
from explain_agent.graph.framework_loader import load_framework, FrameworkError


def test_load_cn_equity_framework():
    fw = load_framework("cn_equity_sector_attribution")
    assert fw["domain_id"] == "cn_equity_sector_attribution"
    assert len(fw["dimensions"]) == 6
    dim_ids = {d["id"] for d in fw["dimensions"]}
    assert dim_ids == {"policy", "industry_chain", "capital_flow",
                       "sentiment_event", "international", "technical"}
    assert fw["worker_config"]["max_rounds"] == 10
    assert fw["worker_config"]["max_concurrency"] == 3


def test_load_unknown_domain_raises():
    with pytest.raises(FrameworkError):
        load_framework("nonexistent_domain")


def test_load_from_explicit_path(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("""
domain_id: test
display_name: test
match_patterns: []
dimensions:
  - id: a
    name: A
    priority: 1
    data_sources: [foo]
    query_template: "test"
worker_config:
  max_rounds: 5
  soft_terminate_no_gain_rounds: 1
  max_concurrency: 1
models:
  parse: weak
""", encoding="utf-8")
    fw = load_framework("test", search_paths=[tmp_path])
    assert fw["domain_id"] == "test"
```

**Step 3: 跑测试确认失败**

Run: `uv run pytest tests/test_framework_loader.py -v`
Expected: ImportError。

**Step 4: 实现 loader**

`src/explain_agent/graph/framework_loader.py`：

```python
from pathlib import Path
import yaml


class FrameworkError(RuntimeError):
    pass


_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "frameworks"


def load_framework(domain_id: str, search_paths: list[Path] | None = None) -> dict:
    paths = search_paths or [_DEFAULT_DIR]
    for p in paths:
        candidate = p / f"{domain_id}.yaml"
        if candidate.exists():
            with candidate.open(encoding="utf-8") as f:
                fw = yaml.safe_load(f)
            _validate(fw)
            return fw
    raise FrameworkError(f"framework not found: {domain_id}")


def _validate(fw: dict) -> None:
    for key in ("domain_id", "dimensions", "worker_config", "models"):
        if key not in fw:
            raise FrameworkError(f"missing key in framework: {key}")
    for dim in fw["dimensions"]:
        for k in ("id", "name", "priority", "data_sources", "query_template"):
            if k not in dim:
                raise FrameworkError(f"dimension {dim} missing key: {k}")
```

**Step 5: 跑测试**

Run: `uv run pytest tests/test_framework_loader.py -v`
Expected: 3 passed。

**Step 6: Commit**

```bash
git add frameworks/ src/explain_agent/graph/framework_loader.py tests/test_framework_loader.py
git commit -m "新增金融领域框架 YAML 与 Loader

6 维配置：policy/industry_chain/capital_flow/sentiment_event/
international/technical，含 evidence_filter 与 worker_config。"
```

---

## Task 4: 节点 · parse_question

**目标：** 用弱模型从 raw_question 提取 target / time_window / intent。

**Files:**
- Create: `src/explain_agent/graph/nodes/parse_question.py`
- Create: `tests/test_node_parse_question.py`

**Step 1: 写测试**

`tests/test_node_parse_question.py`：

```python
import json
from datetime import datetime, date
from unittest.mock import MagicMock
import pytest
from explain_agent.graph.state import new_attribution_state
from explain_agent.graph.nodes.parse_question import parse_question_node


@pytest.mark.asyncio
async def test_parse_returns_target_time_intent():
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({
        "target": "半导体",
        "time_window_start": "2026-05-05",
        "time_window_end": "2026-05-12",
        "intent": "up",
    })
    state = new_attribution_state("为什么半导体最近一周涨停")
    state["asked_at"] = datetime(2026, 5, 12, 15, 0)

    out = await parse_question_node(state, llm=fake_llm)
    assert out["target"] == "半导体"
    assert out["time_window"] == (date(2026, 5, 5), date(2026, 5, 12))
    assert out["intent"] == "up"


@pytest.mark.asyncio
async def test_parse_falls_back_to_today_when_llm_returns_bad_json():
    fake_llm = MagicMock()
    fake_llm.chat.return_value = "no json here"
    state = new_attribution_state("半导体")
    state["asked_at"] = datetime(2026, 5, 12, 15, 0)

    out = await parse_question_node(state, llm=fake_llm)
    assert out["target"] == "半导体"  # 用 raw_question 兜底
    assert out["time_window"][1] == date(2026, 5, 12)
    assert out["intent"] == "general"
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_node_parse_question.py -v`
Expected: ImportError。

**Step 3: 实现**

`src/explain_agent/graph/nodes/parse_question.py`：

```python
import json
import re
from datetime import date, timedelta
from explain_agent.graph.state import AttributionState
from explain_agent.llm import LLMClient, get_weak_llm


SYSTEM = """你是金融归因 agent 的问题解析器。读完用户输入，输出 JSON：
{
  "target": "标的（板块/行业/主题名）",
  "time_window_start": "YYYY-MM-DD",
  "time_window_end": "YYYY-MM-DD",
  "intent": "up|down|volatile|general"
}

规则：
- target 提取板块/主题词（如"半导体"、"光伏"、"白酒"），无明确则用整句话
- 时间窗：默认 end=今天，start=今天-7天；若用户提"上周"则推 7-14 天前；"今天"则 start=end=今天
- intent：涨/涨停=up，跌/大跌=down，波动/异动=volatile，其它=general
只输出 JSON。
"""


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def parse_question_node(
    state: AttributionState,
    llm: LLMClient | None = None,
) -> dict:
    llm = llm or get_weak_llm()
    today = state["asked_at"].date()
    user = f"今天: {today}\n用户输入: {state['raw_question']}"

    raw = llm.chat(system=SYSTEM, user=user, max_tokens=2000)
    data = _extract_json(raw)
    if data is None:
        return {
            "target": state["raw_question"][:50],
            "time_window": (today - timedelta(days=7), today),
            "intent": "general",
        }

    try:
        start = date.fromisoformat(data["time_window_start"])
        end = date.fromisoformat(data["time_window_end"])
    except (KeyError, ValueError):
        start, end = today - timedelta(days=7), today

    return {
        "target": data.get("target", state["raw_question"][:50]),
        "time_window": (start, end),
        "intent": data.get("intent", "general"),
    }
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_node_parse_question.py -v`
Expected: 2 passed。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/nodes/parse_question.py tests/test_node_parse_question.py
git commit -m "新增 parse_question 节点

弱模型解析 target/time_window/intent，JSON 解析失败时优雅降级到
默认值（7 天窗口、general intent）。"
```

---

## Task 5: 节点 · domain_router

**目标：** 根据 parsed question 路由到领域 ID。MVP 只有一个领域。

**Files:**
- Create: `src/explain_agent/graph/nodes/domain_router.py`
- Create: `tests/test_node_domain_router.py`

**Step 1: 写测试**

`tests/test_node_domain_router.py`：

```python
import re
import pytest
from datetime import date
from explain_agent.graph.state import new_attribution_state
from explain_agent.graph.nodes.domain_router import domain_router_node


@pytest.mark.asyncio
async def test_router_matches_attribution_pattern():
    state = new_attribution_state("为什么半导体板块今天涨停")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["intent"] = "up"

    out = await domain_router_node(state)
    assert out["domain_id"] == "cn_equity_sector_attribution"


@pytest.mark.asyncio
async def test_router_falls_back_when_no_match():
    state = new_attribution_state("今天天气真好")
    state["target"] = "天气"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["intent"] = "general"

    out = await domain_router_node(state)
    # MVP 阶段无匹配也兜底为唯一领域
    assert out["domain_id"] == "cn_equity_sector_attribution"
```

**Step 2: 实现**

`src/explain_agent/graph/nodes/domain_router.py`：

```python
import re
from explain_agent.graph.state import AttributionState
from explain_agent.graph.framework_loader import load_framework


_KNOWN_DOMAINS = ["cn_equity_sector_attribution"]


async def domain_router_node(state: AttributionState) -> dict:
    raw = state["raw_question"]
    for domain_id in _KNOWN_DOMAINS:
        fw = load_framework(domain_id)
        for pattern in fw.get("match_patterns", []):
            if re.search(pattern, raw):
                return {"domain_id": domain_id}
    return {"domain_id": _KNOWN_DOMAINS[0]}
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_node_domain_router.py -v`
Expected: 2 passed。

**Step 4: Commit**

```bash
git add src/explain_agent/graph/nodes/domain_router.py tests/test_node_domain_router.py
git commit -m "新增 domain_router 节点

按 framework YAML 中的 match_patterns 路由。MVP 唯一领域，
未匹配时兜底为 cn_equity_sector_attribution。"
```

---

## Task 6: 节点 · fetch_market_facts

**目标：** 在 6 维 worker 之前先调 `clickhouse_market` 拿客观锚点（涨跌幅、龙头股）作为只读上下文。

**Files:**
- Create: `src/explain_agent/graph/nodes/fetch_market_facts.py`
- Create: `tests/test_node_fetch_market_facts.py`

**Step 1: 写测试**

`tests/test_node_fetch_market_facts.py`：

```python
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from explain_agent.core.types import Evidence
from explain_agent.graph.state import new_attribution_state
from explain_agent.graph.nodes.fetch_market_facts import fetch_market_facts_node


@pytest.mark.asyncio
async def test_fetch_market_facts_calls_clickhouse_market_adapter():
    mock_adapter = MagicMock()
    mock_adapter.query = AsyncMock(return_value=[
        Evidence(
            id="e1",
            source="clickhouse_market",
            source_type="market_data",
            snippet="半导体板块龙头股: symbol_id=2332 涨跌=92.52%",
            raw_payload={"rows": [(2332, 100.0, 92.52, 1e9), (1001, 50.0, 60.20, 5e8)]},
            timestamp=datetime.now(),
            metadata={"target": "半导体", "kind": "industry_leaders"},
        )
    ])
    state = new_attribution_state("为什么半导体涨")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))

    out = await fetch_market_facts_node(state, market_adapter=mock_adapter)
    assert "raw_payload" in out["market_facts"]
    assert out["market_facts"]["target"] == "半导体"
    assert out["market_facts"]["snippet"].startswith("半导体板块龙头股")


@pytest.mark.asyncio
async def test_fetch_market_facts_handles_empty():
    mock_adapter = MagicMock()
    mock_adapter.query = AsyncMock(return_value=[])
    state = new_attribution_state("为什么 X 涨")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))

    out = await fetch_market_facts_node(state, market_adapter=mock_adapter)
    assert out["market_facts"]["raw_payload"] is None
    assert out["market_facts"]["snippet"] == ""
```

**Step 2: 实现**

`src/explain_agent/graph/nodes/fetch_market_facts.py`：

```python
from explain_agent.core.types import AdapterQuery
from explain_agent.graph.state import AttributionState


async def fetch_market_facts_node(
    state: AttributionState,
    market_adapter,
) -> dict:
    q = AdapterQuery(
        keywords=[],
        time_window=state["time_window"],
        target=state["target"],
    )
    evidences = await market_adapter.query(q)
    if not evidences:
        return {
            "market_facts": {
                "target": state["target"],
                "time_window": list(state["time_window"]),
                "snippet": "",
                "raw_payload": None,
            }
        }
    e = evidences[0]
    return {
        "market_facts": {
            "target": state["target"],
            "time_window": list(state["time_window"]),
            "snippet": e.snippet,
            "raw_payload": e.raw_payload,
        }
    }
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_node_fetch_market_facts.py -v`
Expected: 2 passed。

**Step 4: Commit**

```bash
git add src/explain_agent/graph/nodes/fetch_market_facts.py tests/test_node_fetch_market_facts.py
git commit -m "新增 fetch_market_facts 节点

调 clickhouse_market Adapter 拿板块涨跌/龙头股快照作为客观锚点，
注入到后续 6 维 worker 的只读上下文。"
```

---

## Task 7: 单维 worker · mini ReAct 内核

**目标：** 实现 `DimensionWorker` 类，封装单个维度的"生成关键词 → 调 Adapter → 评估 → 精化"循环，含 10 轮上限 + 软终止 + no_data 处理。

**Files:**
- Create: `src/explain_agent/graph/dimension_worker.py`
- Create: `tests/test_dimension_worker.py`

**Step 1: 写测试**

`tests/test_dimension_worker.py`：

```python
import json
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from explain_agent.core.types import Evidence
from explain_agent.graph.dimension_worker import DimensionWorker


def make_evidence(id: str, score: float = 0.8) -> Evidence:
    return Evidence(
        id=id, source="news_corpus", source_type="news",
        snippet=f"证据 {id}", timestamp=datetime.now(),
        metadata={"score": score},
    )


@pytest.mark.asyncio
async def test_worker_terminates_when_evidence_sufficient():
    fake_llm = MagicMock()
    fake_llm.chat.side_effect = [
        json.dumps({"keywords": ["半导体", "涨停"]}),
        json.dumps({"sufficient": True, "relevant_ids": ["e1", "e2"]}),
        "本维度共找到 2 条政策类证据,主因是 ...",
    ]
    mock_adapter = MagicMock()
    mock_adapter.query = AsyncMock(return_value=[make_evidence("e1"), make_evidence("e2")])
    registry = {"news_corpus": mock_adapter}

    worker = DimensionWorker(
        dimension_config={
            "id": "policy", "name": "政策/宏观", "data_sources": ["news_corpus"],
            "query_template": "{target} 相关政策",
        },
        worker_config={"max_rounds": 10, "soft_terminate_no_gain_rounds": 2},
        llm=fake_llm, adapter_registry=registry,
    )
    result = await worker.run(
        target="半导体",
        time_window=(date(2026, 5, 5), date(2026, 5, 12)),
        market_facts={"snippet": "板块涨 5%"},
    )
    assert len(result["evidence"]) == 2
    assert result["no_data"] is False
    assert result["retry_count"] == 1
    assert result["confidence"] in ("high", "medium", "low")


@pytest.mark.asyncio
async def test_worker_marks_no_data_when_adapter_always_empty():
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({"keywords": ["x"]})
    mock_adapter = MagicMock()
    mock_adapter.query = AsyncMock(return_value=[])

    worker = DimensionWorker(
        dimension_config={"id": "policy", "name": "政策", "data_sources": ["news_corpus"],
                          "query_template": "test"},
        worker_config={"max_rounds": 3, "soft_terminate_no_gain_rounds": 2},
        llm=fake_llm, adapter_registry={"news_corpus": mock_adapter},
    )
    result = await worker.run(
        target="X", time_window=(date(2026, 5, 5), date(2026, 5, 12)), market_facts={},
    )
    assert result["no_data"] is True
    assert result["evidence"] == []
    assert result["mini_summary"] == "本维度未检索到相关证据"


@pytest.mark.asyncio
async def test_worker_respects_max_rounds():
    fake_llm = MagicMock()
    fake_llm.chat.side_effect = [
        json.dumps({"keywords": [f"k{i}"]}) if i % 2 == 0 else json.dumps({"sufficient": False})
        for i in range(20)
    ]
    mock_adapter = MagicMock()
    mock_adapter.query = AsyncMock(return_value=[make_evidence("e_x")])

    worker = DimensionWorker(
        dimension_config={"id": "policy", "name": "政策", "data_sources": ["news_corpus"],
                          "query_template": "test"},
        worker_config={"max_rounds": 3, "soft_terminate_no_gain_rounds": 99},  # 不软终止
        llm=fake_llm, adapter_registry={"news_corpus": mock_adapter},
    )
    result = await worker.run(
        target="X", time_window=(date(2026, 5, 5), date(2026, 5, 12)), market_facts={},
    )
    assert result["retry_count"] == 3  # 硬上限
```

**Step 2: 实现**

`src/explain_agent/graph/dimension_worker.py`：

```python
import json
import re
from datetime import date
from typing import Any
from explain_agent.core.types import AdapterQuery, Evidence
from explain_agent.graph.state import DimensionResult
from explain_agent.llm import LLMClient


QUERY_GEN_SYSTEM = """你是金融归因 agent 的查询关键词生成器。
基于维度配置 + 已有证据，生成下一轮要查询的关键词。

输出 JSON: {"keywords": ["k1", "k2", ...]}
只输出 JSON，最多 5 个关键词。
"""

EVAL_SYSTEM = """你是证据评估器。判断当前已有证据是否足够回答该维度的问题。

输出 JSON: {"sufficient": true|false, "relevant_ids": ["id1", ...]}
- sufficient=true 表示证据已经能支撑该维度的归因
- relevant_ids 是相关证据的 id 列表（其余视为噪声）
只输出 JSON。
"""

SUMMARY_SYSTEM = """你是该维度的 mini 摘要器。基于相关证据写一段 150-300 字的维度内归因。
不要编造没有证据的结论。
"""


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class DimensionWorker:
    def __init__(self, dimension_config: dict, worker_config: dict,
                 llm: LLMClient, adapter_registry: dict[str, Any]):
        self.dim = dimension_config
        self.cfg = worker_config
        self.llm = llm
        self.registry = adapter_registry

    async def run(
        self,
        target: str,
        time_window: tuple[date, date],
        market_facts: dict,
    ) -> DimensionResult:
        max_rounds = self.cfg.get("max_rounds", 10)
        soft_terminate = self.cfg.get("soft_terminate_no_gain_rounds", 2)

        all_evidence: list[Evidence] = []
        rounds = 0
        no_gain_count = 0

        for round_idx in range(1, max_rounds + 1):
            rounds = round_idx
            keywords = self._gen_keywords(target, time_window, market_facts, all_evidence)
            new_ev = await self._fetch_all_sources(keywords, target, time_window)
            new_ids = {e.id for e in new_ev} - {e.id for e in all_evidence}
            all_evidence.extend([e for e in new_ev if e.id in new_ids])

            if not new_ids:
                no_gain_count += 1
                if no_gain_count >= soft_terminate:
                    break
                continue
            no_gain_count = 0

            if self._is_sufficient(all_evidence, target):
                break

        if not all_evidence:
            return DimensionResult(
                evidence=[], mini_summary="本维度未检索到相关证据",
                retry_count=rounds, no_data=True, confidence="low",
            )

        summary = self._summarize(all_evidence, target, market_facts)
        confidence = self._estimate_confidence(all_evidence)
        return DimensionResult(
            evidence=all_evidence, mini_summary=summary,
            retry_count=rounds, no_data=False, confidence=confidence,
        )

    def _gen_keywords(
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
        raw = self.llm.chat(system=QUERY_GEN_SYSTEM, user=user, max_tokens=2000)
        data = _extract_json(raw)
        return data.get("keywords", [target])[:5] if data else [target]

    async def _fetch_all_sources(
        self, keywords: list[str], target: str, time_window: tuple[date, date],
    ) -> list[Evidence]:
        out: list[Evidence] = []
        for src_name in self.dim["data_sources"]:
            if src_name not in self.registry:
                continue
            adapter = self.registry[src_name]
            q = AdapterQuery(
                keywords=keywords, time_window=time_window, target=target, limit=20,
            )
            try:
                out.extend(await adapter.query(q))
            except Exception:
                continue
        return out

    def _is_sufficient(self, evidence: list[Evidence], target: str) -> bool:
        snippets = "\n".join(f"id={e.id}: {e.snippet[:200]}" for e in evidence[:20])
        user = f"维度: {self.dim['name']}\n标的: {target}\n证据:\n{snippets}"
        raw = self.llm.chat(system=EVAL_SYSTEM, user=user, max_tokens=2000)
        data = _extract_json(raw)
        return bool(data and data.get("sufficient", False))

    def _summarize(self, evidence: list[Evidence], target: str, market_facts: dict) -> str:
        snippets = "\n".join(f"id={e.id}: {e.snippet[:300]}" for e in evidence)
        user = (
            f"维度: {self.dim['name']}\n标的: {target}\n"
            f"市场锚点: {market_facts.get('snippet', '')}\n证据:\n{snippets}"
        )
        return self.llm.chat(system=SUMMARY_SYSTEM, user=user, max_tokens=2000)

    def _estimate_confidence(self, evidence: list[Evidence]) -> str:
        n = len(evidence)
        if n >= 5:
            return "high"
        if n >= 2:
            return "medium"
        return "low"
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_dimension_worker.py -v`
Expected: 3 passed。

**Step 4: Commit**

```bash
git add src/explain_agent/graph/dimension_worker.py tests/test_dimension_worker.py
git commit -m "新增 DimensionWorker mini ReAct 内核

封装单维度的查询生成 → Adapter 拉取 → 充足性评估 → 摘要循环，
含硬上限 10 轮、软终止、no_data 合法状态、confidence 估算。"
```

---

## Task 8: 节点 · fan_out_dimensions（Semaphore=3）

**目标：** 并行调度 6 维 worker，受 `asyncio.Semaphore(3)` 限制。

**Files:**
- Create: `src/explain_agent/graph/nodes/fan_out_dimensions.py`
- Create: `tests/test_node_fan_out.py`

**Step 1: 写测试**

`tests/test_node_fan_out.py`：

```python
import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock
import pytest
from explain_agent.graph.state import new_attribution_state, DimensionResult
from explain_agent.graph.nodes.fan_out_dimensions import fan_out_dimensions_node


@pytest.mark.asyncio
async def test_fan_out_runs_all_dimensions():
    framework = {
        "dimensions": [
            {"id": f"d{i}", "name": f"D{i}", "data_sources": ["x"], "query_template": "t"}
            for i in range(6)
        ],
        "worker_config": {"max_rounds": 1, "soft_terminate_no_gain_rounds": 1, "max_concurrency": 3},
    }
    state = new_attribution_state("test")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["framework"] = framework
    state["market_facts"] = {"snippet": ""}

    fake_worker_factory = MagicMock()
    fake_worker_instance = MagicMock()
    fake_worker_instance.run = AsyncMock(return_value=DimensionResult(
        evidence=[], mini_summary="", retry_count=1, no_data=True, confidence="low",
    ))
    fake_worker_factory.return_value = fake_worker_instance

    out = await fan_out_dimensions_node(state, worker_factory=fake_worker_factory)
    assert set(out["dimension_results"].keys()) == {"d0", "d1", "d2", "d3", "d4", "d5"}


@pytest.mark.asyncio
async def test_fan_out_semaphore_limits_concurrency():
    framework = {
        "dimensions": [
            {"id": f"d{i}", "name": f"D{i}", "data_sources": [], "query_template": "t"}
            for i in range(6)
        ],
        "worker_config": {"max_rounds": 1, "soft_terminate_no_gain_rounds": 1, "max_concurrency": 2},
    }
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["framework"] = framework
    state["market_facts"] = {}

    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def slow_run(*args, **kwargs):
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return DimensionResult(
            evidence=[], mini_summary="", retry_count=1, no_data=True, confidence="low",
        )

    fake_worker_factory = MagicMock()
    fake_worker = MagicMock()
    fake_worker.run = slow_run
    fake_worker_factory.return_value = fake_worker

    await fan_out_dimensions_node(state, worker_factory=fake_worker_factory)
    assert peak <= 2  # Semaphore 上限
```

**Step 2: 实现**

`src/explain_agent/graph/nodes/fan_out_dimensions.py`：

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
    sem = asyncio.Semaphore(worker_cfg.get("max_concurrency", 3))

    async def run_one(dim_cfg: dict) -> tuple[str, DimensionResult]:
        async with sem:
            worker = worker_factory(dimension_config=dim_cfg, worker_config=worker_cfg)
            r = await worker.run(
                target=state["target"],
                time_window=state["time_window"],
                market_facts=state["market_facts"],
            )
            return dim_cfg["id"], r

    results = await asyncio.gather(*[run_one(d) for d in dims], return_exceptions=False)
    return {"dimension_results": dict(results)}
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_node_fan_out.py -v`
Expected: 2 passed。

**Step 4: Commit**

```bash
git add src/explain_agent/graph/nodes/fan_out_dimensions.py tests/test_node_fan_out.py
git commit -m "新增 fan_out_dimensions 节点

asyncio.Semaphore 受控并发调度 6 维 worker，
factory 接口便于测试注入 mock。"
```

---

## Task 9: 节点 · evidence_synthesizer

**目标：** 用强模型看完整证据树，决定是否触发动态子分支（≤2 个）。

**Files:**
- Create: `src/explain_agent/graph/nodes/synthesizer.py`
- Create: `tests/test_node_synthesizer.py`

**Step 1: 写测试**

`tests/test_node_synthesizer.py`：

```python
import json
from datetime import date, datetime
from unittest.mock import MagicMock
import pytest
from explain_agent.core.types import Evidence
from explain_agent.graph.state import new_attribution_state, DimensionResult
from explain_agent.graph.nodes.synthesizer import synthesizer_node


def make_ev(id: str, snippet: str) -> Evidence:
    return Evidence(
        id=id, source="x", source_type="news",
        snippet=snippet, timestamp=datetime.now(),
    )


@pytest.mark.asyncio
async def test_synthesizer_decides_subbranches():
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({
        "needs_subbranch": True,
        "subbranches": [{"name": "HBM 制裁影响", "query_hints": ["BIS", "HBM"]}],
    })
    state = new_attribution_state("test")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", "美国 BIS 制裁 HBM")],
            mini_summary="...", retry_count=1, no_data=False, confidence="high",
        ),
    }

    out = await synthesizer_node(state, llm=fake_llm)
    assert out["needs_subbranch"] is True
    assert len(out["subbranches"]) == 1
    assert out["subbranches"][0]["name"] == "HBM 制裁影响"


@pytest.mark.asyncio
async def test_synthesizer_caps_subbranches_at_2():
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({
        "needs_subbranch": True,
        "subbranches": [
            {"name": "a", "query_hints": []},
            {"name": "b", "query_hints": []},
            {"name": "c", "query_hints": []},
            {"name": "d", "query_hints": []},
        ],
    })
    state = new_attribution_state("test")
    state["target"] = "X"
    state["dimension_results"] = {}
    out = await synthesizer_node(state, llm=fake_llm)
    assert len(out["subbranches"]) == 2


@pytest.mark.asyncio
async def test_synthesizer_no_subbranches_when_llm_says_no():
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({"needs_subbranch": False, "subbranches": []})
    state = new_attribution_state("test")
    state["target"] = "X"
    state["dimension_results"] = {}
    out = await synthesizer_node(state, llm=fake_llm)
    assert out["needs_subbranch"] is False
    assert out["subbranches"] == []
```

**Step 2: 实现**

`src/explain_agent/graph/nodes/synthesizer.py`：

```python
import json
import re
from explain_agent.graph.state import AttributionState
from explain_agent.llm import LLMClient, get_strong_llm


SYSTEM = """你是金融归因 agent 的证据合成器。

任务：浏览 6 维证据，判断是否有"反复出现但未被框架维度覆盖"的实体/事件/政策值得开动态子分支。

判断标准：
- 该实体/事件至少在 3 条证据中出现
- 它代表一个独立主题（不是某一维的细分）
- 进一步检索能带来增量信息

输出 JSON:
{
  "needs_subbranch": true|false,
  "subbranches": [
    {"name": "...", "query_hints": ["关键词1", "关键词2"]},
    ...  // 最多 2 个
  ]
}

只输出 JSON。
"""


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def synthesizer_node(
    state: AttributionState,
    llm: LLMClient | None = None,
) -> dict:
    llm = llm or get_strong_llm()
    evidence_dump = []
    for dim_id, r in state["dimension_results"].items():
        for e in r["evidence"][:10]:
            evidence_dump.append({"dim": dim_id, "id": e.id, "snippet": e.snippet[:300]})

    user = f"target: {state['target']}\nevidence:\n{json.dumps(evidence_dump, ensure_ascii=False)}"
    raw = llm.chat(system=SYSTEM, user=user, max_tokens=4000)
    data = _extract_json(raw)
    if not data:
        return {"needs_subbranch": False, "subbranches": []}
    needs = bool(data.get("needs_subbranch", False))
    branches = data.get("subbranches", [])[:2]
    return {"needs_subbranch": needs and bool(branches), "subbranches": branches}
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_node_synthesizer.py -v`
Expected: 3 passed。

**Step 4: Commit**

```bash
git add src/explain_agent/graph/nodes/synthesizer.py tests/test_node_synthesizer.py
git commit -m "新增 evidence_synthesizer 节点

强模型判断是否触发动态子分支扩展，硬上限 ≤2 个。"
```

---

## Task 10: 节点 · dynamic_subbranches

**目标：** 如果 synthesizer 触发了子分支，复用 DimensionWorker 跑子分支（维度名是动态的）。

**Files:**
- Create: `src/explain_agent/graph/nodes/dynamic_subbranches.py`
- Create: `tests/test_node_dynamic_subbranches.py`

**Step 1: 写测试**

`tests/test_node_dynamic_subbranches.py`：

```python
from datetime import date
from unittest.mock import AsyncMock, MagicMock
import pytest
from explain_agent.graph.state import new_attribution_state, DimensionResult
from explain_agent.graph.nodes.dynamic_subbranches import dynamic_subbranches_node


@pytest.mark.asyncio
async def test_dynamic_subbranches_runs_each_spec():
    state = new_attribution_state("test")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["framework"] = {
        "worker_config": {"max_rounds": 1, "soft_terminate_no_gain_rounds": 1, "max_concurrency": 2}
    }
    state["needs_subbranch"] = True
    state["subbranches"] = [
        {"name": "HBM 制裁", "query_hints": ["BIS", "HBM"]},
        {"name": "国产替代", "query_hints": ["晶圆"]},
    ]

    fake_worker = MagicMock()
    fake_worker.run = AsyncMock(return_value=DimensionResult(
        evidence=[], mini_summary="子分支结果", retry_count=1, no_data=False, confidence="medium",
    ))
    fake_factory = MagicMock(return_value=fake_worker)

    out = await dynamic_subbranches_node(state, worker_factory=fake_factory)
    assert set(out["subbranch_results"].keys()) == {"HBM 制裁", "国产替代"}


@pytest.mark.asyncio
async def test_dynamic_subbranches_skips_when_not_needed():
    state = new_attribution_state("test")
    state["needs_subbranch"] = False
    state["subbranches"] = []
    state["framework"] = {"worker_config": {}}

    fake_factory = MagicMock()
    out = await dynamic_subbranches_node(state, worker_factory=fake_factory)
    assert out == {"subbranch_results": {}}
    fake_factory.assert_not_called()
```

**Step 2: 实现**

`src/explain_agent/graph/nodes/dynamic_subbranches.py`：

```python
import asyncio
from typing import Callable
from explain_agent.graph.state import AttributionState, DimensionResult
from explain_agent.graph.dimension_worker import DimensionWorker


async def dynamic_subbranches_node(
    state: AttributionState,
    worker_factory: Callable[..., DimensionWorker],
) -> dict:
    if not state.get("needs_subbranch") or not state.get("subbranches"):
        return {"subbranch_results": {}}

    worker_cfg = state["framework"].get("worker_config", {})
    sem = asyncio.Semaphore(worker_cfg.get("max_concurrency", 2))

    async def run_one(spec: dict) -> tuple[str, DimensionResult]:
        async with sem:
            dim_cfg = {
                "id": f"sub_{spec['name']}",
                "name": spec["name"],
                "data_sources": ["news_corpus"],
                "query_template": f"{spec['name']} " + " ".join(spec.get("query_hints", [])),
            }
            worker = worker_factory(dimension_config=dim_cfg, worker_config=worker_cfg)
            r = await worker.run(
                target=state["target"],
                time_window=state["time_window"],
                market_facts=state["market_facts"],
            )
            return spec["name"], r

    results = await asyncio.gather(*[run_one(s) for s in state["subbranches"]])
    return {"subbranch_results": dict(results)}
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_node_dynamic_subbranches.py -v`
Expected: 2 passed。

**Step 4: Commit**

```bash
git add src/explain_agent/graph/nodes/dynamic_subbranches.py tests/test_node_dynamic_subbranches.py
git commit -m "新增 dynamic_subbranches 节点

按 synthesizer 输出的子分支 spec 复用 DimensionWorker，
data_sources 固定为 news_corpus（动态主题主要靠新闻语料）。"
```

---

## Task 11: 节点 · report_builder（Phase 2.A 简化版）

**目标：** 简单拼接报告（叙事段 = 强模型的一段总结，维度报告 = 各维 mini_summary）。**Phase 2.A 不做五层幻觉防控**，仅保证基础形态。

**Files:**
- Create: `src/explain_agent/graph/nodes/report_builder.py`
- Create: `tests/test_node_report_builder.py`

**Step 1: 写测试**

`tests/test_node_report_builder.py`：

```python
from datetime import date, datetime
from unittest.mock import MagicMock
import pytest
from explain_agent.core.types import Evidence
from explain_agent.graph.state import new_attribution_state, DimensionResult
from explain_agent.graph.nodes.report_builder import report_builder_node


def make_ev(id: str, source_type: str = "news", url: str | None = "http://a.com") -> Evidence:
    return Evidence(
        id=id, source="x", source_type=source_type,
        url=url, snippet=f"snip {id}", timestamp=datetime.now(),
    )


@pytest.mark.asyncio
async def test_report_assembles_narrative_and_dim_reports():
    fake_llm = MagicMock()
    fake_llm.chat.return_value = "半导体板块今日上涨主因是 ... (强模型生成的简短叙事)"

    state = new_attribution_state("为什么半导体涨")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": "板块涨 5%"}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1")], mini_summary="政策维 mini",
            retry_count=1, no_data=False, confidence="high",
        ),
        "technical": DimensionResult(
            evidence=[], mini_summary="本维度未检索到相关证据",
            retry_count=10, no_data=True, confidence="low",
        ),
    }

    out = await report_builder_node(state, llm=fake_llm)
    assert "半导体板块今日上涨主因" in out["narrative"]
    assert "政策维 mini" in out["dimension_reports"]["policy"]
    assert "未检索到" in out["dimension_reports"]["technical"]
    assert any(c["evidence_id"] == "e1" for c in out["citations"])
    assert out["confidence"] in ("high", "medium", "low")
```

**Step 2: 实现**

`src/explain_agent/graph/nodes/report_builder.py`：

```python
from explain_agent.graph.state import AttributionState, Citation
from explain_agent.llm import LLMClient, get_strong_llm


NARRATIVE_SYSTEM = """你是审慎的金融研究员。基于以下六维归因证据，
写一段 80-150 字的归因叙事段。

要求:
- 用客观语气总结主因
- 不要预测/推荐操作
- 没有证据的论点直接砍掉
- 不要列点,要连贯叙事
"""


async def report_builder_node(
    state: AttributionState,
    llm: LLMClient | None = None,
) -> dict:
    llm = llm or get_strong_llm()
    dim_results = state["dimension_results"]
    sub_results = state.get("subbranch_results", {})

    # 强模型生成叙事
    summaries = []
    for dim_id, r in dim_results.items():
        summaries.append(f"[{dim_id}] {r['mini_summary']}")
    for sub_name, r in sub_results.items():
        summaries.append(f"[子分支:{sub_name}] {r['mini_summary']}")

    user = (
        f"标的: {state['target']}\n"
        f"时间窗: {state['time_window'][0]} ~ {state['time_window'][1]}\n"
        f"市场锚点: {state['market_facts'].get('snippet', '')}\n"
        f"维度摘要:\n" + "\n\n".join(summaries)
    )
    narrative = llm.chat(system=NARRATIVE_SYSTEM, user=user, max_tokens=4000)

    # 维度报告：直接用 mini_summary
    dim_reports = {dim_id: r["mini_summary"] for dim_id, r in dim_results.items()}

    # citations：从所有 evidence 中提取
    citations: list[Citation] = []
    seen_ids: set[str] = set()
    for r in list(dim_results.values()) + list(sub_results.values()):
        for e in r["evidence"]:
            if e.id in seen_ids:
                continue
            seen_ids.add(e.id)
            citations.append(Citation(
                evidence_id=e.id, url=e.url,
                snapshot_id=e.snapshot_id, source_type=e.source_type,
            ))

    # confidence 估算：看高 confidence 维度的数量
    high_count = sum(1 for r in dim_results.values() if r["confidence"] == "high")
    if high_count >= 3:
        confidence = "high"
    elif high_count >= 1:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "narrative": narrative,
        "dimension_reports": dim_reports,
        "citations": citations,
        "confidence": confidence,
    }
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: 1 passed。

**Step 4: Commit**

```bash
git add src/explain_agent/graph/nodes/report_builder.py tests/test_node_report_builder.py
git commit -m "新增 report_builder 节点 (Phase 2.A 简化版)

直接拼接 6 维 mini_summary，强模型写顶部叙事段。
Phase 2.B 再上五层幻觉防控（强制 ground-in-evidence、数值校验等）。"
```

---

## Task 12: 节点 · persist

**目标：** 把会话和证据树写入 MySQL `explain_session` + `explain_evidence_tree`。

**Files:**
- Create: `src/explain_agent/graph/nodes/persist.py`
- Create: `tests/test_node_persist.py`

**Step 1: 写测试**

`tests/test_node_persist.py`：

```python
import json
from datetime import date, datetime
from unittest.mock import MagicMock
import pytest
from explain_agent.graph.state import new_attribution_state
from explain_agent.graph.nodes.persist import persist_node


@pytest.mark.asyncio
async def test_persist_writes_session_and_tree():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    state = new_attribution_state("为什么半导体涨", session_id="s_test_123")
    state["domain_id"] = "cn_equity_sector_attribution"
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["narrative"] = "半导体涨停因 ..."
    state["dimension_reports"] = {"policy": "...", "industry_chain": "..."}
    state["citations"] = []
    state["confidence"] = "high"
    state["total_cost"] = 1.5

    out = await persist_node(state, engine=mock_engine)
    assert out["session_id"] == "s_test_123"
    # 应该至少调过 exec_driver_sql 两次（insert session + insert evidence_tree）
    assert mock_conn.exec_driver_sql.call_count >= 2
```

**Step 2: 实现**

`src/explain_agent/graph/nodes/persist.py`：

```python
import json
from explain_agent.core.types import Evidence
from explain_agent.graph.state import AttributionState


def _evidence_to_dict(e: Evidence) -> dict:
    d = e.model_dump()
    if d.get("timestamp"):
        d["timestamp"] = d["timestamp"].isoformat()
    return d


async def persist_node(state: AttributionState, engine) -> dict:
    session_id = state["session_id"]

    tree = {
        "target": state.get("target"),
        "time_window": [state["time_window"][0].isoformat(), state["time_window"][1].isoformat()]
            if "time_window" in state else None,
        "market_facts": state.get("market_facts", {}),
        "dimension_results": {
            dim_id: {
                "mini_summary": r["mini_summary"],
                "confidence": r["confidence"],
                "no_data": r["no_data"],
                "retry_count": r["retry_count"],
                "evidence": [_evidence_to_dict(e) for e in r["evidence"]],
            }
            for dim_id, r in state.get("dimension_results", {}).items()
        },
        "subbranch_results": {
            name: {
                "mini_summary": r["mini_summary"],
                "confidence": r["confidence"],
                "no_data": r["no_data"],
                "evidence": [_evidence_to_dict(e) for e in r["evidence"]],
            }
            for name, r in state.get("subbranch_results", {}).items()
        },
        "dimension_reports": state.get("dimension_reports", {}),
        "citations": state.get("citations", []),
    }

    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            INSERT INTO explain_agent.explain_session
              (session_id, raw_question, domain_id, target,
               time_window_start, time_window_end, status, total_cost,
               created_at, finished_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              status='completed', total_cost=VALUES(total_cost),
              finished_at=VALUES(finished_at)
            """,
            (
                session_id, state["raw_question"], state.get("domain_id"),
                state.get("target"),
                state["time_window"][0] if "time_window" in state else None,
                state["time_window"][1] if "time_window" in state else None,
                "completed", state.get("total_cost", 0.0),
                state["asked_at"], state["asked_at"],
            ),
        )
        conn.exec_driver_sql(
            """
            INSERT INTO explain_agent.explain_evidence_tree
              (session_id, tree_json, narrative, confidence, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              tree_json=VALUES(tree_json),
              narrative=VALUES(narrative),
              confidence=VALUES(confidence)
            """,
            (
                session_id,
                json.dumps(tree, ensure_ascii=False, default=str),
                state.get("narrative", ""),
                state.get("confidence", "medium"),
                state["asked_at"],
            ),
        )

    return {"session_id": session_id}
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_node_persist.py -v`
Expected: 1 passed。

**Step 4: Commit**

```bash
git add src/explain_agent/graph/nodes/persist.py tests/test_node_persist.py
git commit -m "新增 persist 节点

把 session + 完整证据树（含 dimension/subbranch/citations）落
MySQL，Evidence 用 model_dump + ISO 时间序列化。"
```

---

## Task 13: Main Graph 装配 + LangGraph 集成测试

**目标：** 用 `langgraph.graph.StateGraph` 把 7 个节点串起来，编译成可执行图。

**Files:**
- Create: `src/explain_agent/graph/main_graph.py`
- Create: `tests/test_main_graph.py`

**Step 1: 写测试**

`tests/test_main_graph.py`：

```python
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from explain_agent.core.types import Evidence
from explain_agent.graph.state import new_attribution_state, DimensionResult


@pytest.mark.asyncio
async def test_main_graph_compiles_and_runs_with_mocks(monkeypatch):
    """端到端 mock 全部节点，确认 graph 拓扑能跑通。"""
    from explain_agent.graph.main_graph import build_main_graph

    fake_market_adapter = MagicMock()
    fake_market_adapter.query = AsyncMock(return_value=[
        Evidence(id="m1", source="clickhouse_market", source_type="market_data",
                 snippet="半导体涨 5%", raw_payload={}, timestamp=datetime.now())
    ])

    fake_worker = MagicMock()
    fake_worker.run = AsyncMock(return_value=DimensionResult(
        evidence=[Evidence(id="e1", source="x", source_type="news",
                           snippet="snip", timestamp=datetime.now())],
        mini_summary="某维 mini", retry_count=1, no_data=False, confidence="medium",
    ))
    fake_worker_factory = MagicMock(return_value=fake_worker)

    fake_weak_llm = MagicMock()
    import json
    fake_weak_llm.chat.side_effect = lambda **kwargs: json.dumps({
        "target": "半导体",
        "time_window_start": "2026-05-05",
        "time_window_end": "2026-05-12",
        "intent": "up",
    })

    fake_strong_llm = MagicMock()
    fake_strong_llm.chat.side_effect = [
        json.dumps({"needs_subbranch": False, "subbranches": []}),  # synthesizer
        "强模型生成的叙事段",  # narrative
    ]

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    graph = build_main_graph(
        market_adapter=fake_market_adapter,
        worker_factory=fake_worker_factory,
        weak_llm=fake_weak_llm,
        strong_llm=fake_strong_llm,
        engine=mock_engine,
    )
    state = new_attribution_state("为什么半导体板块今天涨停")

    result = await graph.ainvoke(state)
    assert result["target"] == "半导体"
    assert result["domain_id"] == "cn_equity_sector_attribution"
    assert "market_facts" in result
    assert len(result["dimension_results"]) == 6
    assert "narrative" in result
    assert result["confidence"] in ("high", "medium", "low")
```

**Step 2: 实现**

`src/explain_agent/graph/main_graph.py`：

```python
from langgraph.graph import StateGraph, END
from explain_agent.graph.state import AttributionState
from explain_agent.graph.nodes.parse_question import parse_question_node
from explain_agent.graph.nodes.domain_router import domain_router_node
from explain_agent.graph.nodes.fetch_market_facts import fetch_market_facts_node
from explain_agent.graph.nodes.fan_out_dimensions import fan_out_dimensions_node
from explain_agent.graph.nodes.synthesizer import synthesizer_node
from explain_agent.graph.nodes.dynamic_subbranches import dynamic_subbranches_node
from explain_agent.graph.nodes.report_builder import report_builder_node
from explain_agent.graph.nodes.persist import persist_node
from explain_agent.graph.framework_loader import load_framework


def build_main_graph(
    market_adapter,
    worker_factory,
    weak_llm,
    strong_llm,
    engine,
):
    g = StateGraph(AttributionState)

    async def _parse(state):
        return await parse_question_node(state, llm=weak_llm)

    async def _router(state):
        return await domain_router_node(state)

    async def _load_fw(state):
        return {"framework": load_framework(state["domain_id"])}

    async def _facts(state):
        return await fetch_market_facts_node(state, market_adapter=market_adapter)

    async def _fan_out(state):
        return await fan_out_dimensions_node(state, worker_factory=worker_factory)

    async def _synth(state):
        return await synthesizer_node(state, llm=strong_llm)

    async def _sub(state):
        return await dynamic_subbranches_node(state, worker_factory=worker_factory)

    async def _report(state):
        return await report_builder_node(state, llm=strong_llm)

    async def _persist(state):
        return await persist_node(state, engine=engine)

    g.add_node("parse", _parse)
    g.add_node("router", _router)
    g.add_node("load_framework", _load_fw)
    g.add_node("market_facts", _facts)
    g.add_node("fan_out", _fan_out)
    g.add_node("synth", _synth)
    g.add_node("dynamic_sub", _sub)
    g.add_node("report", _report)
    g.add_node("persist", _persist)

    g.set_entry_point("parse")
    g.add_edge("parse", "router")
    g.add_edge("router", "load_framework")
    g.add_edge("load_framework", "market_facts")
    g.add_edge("market_facts", "fan_out")
    g.add_edge("fan_out", "synth")
    g.add_edge("synth", "dynamic_sub")
    g.add_edge("dynamic_sub", "report")
    g.add_edge("report", "persist")
    g.add_edge("persist", END)

    return g.compile()
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_main_graph.py -v`
Expected: 1 passed。

**Step 4: 运行所有单测确认整体无回归**

Run: `uv run pytest`
Expected: 全部通过（不含 integration/eval/slow）。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/main_graph.py tests/test_main_graph.py
git commit -m "Main Graph 装配 (parse → router → framework → facts → fan_out → synth → sub → report → persist)

build_main_graph 通过依赖注入接收 adapters/llm/engine,
便于测试与生产配置切换。"
```

---

## Task 14: 端到端真实案例验收（手动）

**目标：** 用真实 .env 跑一遍 main graph，输出半导体板块归因报告，人工验收。

**Files:**
- Create: `scripts/run_main_graph_smoke.py`
- Modify: `README.md`（追加 Phase 2.A 验收清单）

**Step 1: 写 smoke 脚本**

`scripts/run_main_graph_smoke.py`：

```python
import asyncio
import json
from rich.console import Console
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
from explain_agent.llm import get_strong_llm, get_weak_llm


console = Console()


def main(question: str = "为什么半导体板块今天涨"):
    quant_engine = get_engine("quant")
    explain_engine = get_engine("explain")
    resolver = IndustryResolver(quant_engine)

    market = ClickHouseMarketAdapter(ch_client(), resolver)
    fund = MySQLFundamentalsAdapter(quant_engine, resolver)
    flow = AkshareCapitalFlowAdapter()
    news = NewsCorpusAdapter(
        qdrant=get_qdrant_client(), embedder=get_embedder(), engine=explain_engine,
    )
    registry = {
        "clickhouse_market": market,
        "mysql_fundamentals": fund,
        "akshare_capital_flow": flow,
        "news_corpus": news,
    }

    weak = get_weak_llm()
    strong = get_strong_llm()

    def worker_factory(dimension_config, worker_config):
        return DimensionWorker(
            dimension_config=dimension_config,
            worker_config=worker_config,
            llm=weak,
            adapter_registry=registry,
        )

    graph = build_main_graph(
        market_adapter=market,
        worker_factory=worker_factory,
        weak_llm=weak,
        strong_llm=strong,
        engine=explain_engine,
    )

    state = new_attribution_state(question)
    console.print(f"[bold cyan]Question:[/bold cyan] {question}")
    result = asyncio.run(graph.ainvoke(state))

    console.print(f"\n[bold]Target:[/bold] {result.get('target')}")
    console.print(f"[bold]Time window:[/bold] {result.get('time_window')}")
    console.print(f"[bold]Domain:[/bold] {result.get('domain_id')}")
    console.print(f"[bold]Confidence:[/bold] {result.get('confidence')}")

    console.print(f"\n[bold yellow]Narrative:[/bold yellow]\n{result.get('narrative')}")

    console.print("\n[bold yellow]Dimension Reports:[/bold yellow]")
    for dim_id, summary in result.get("dimension_reports", {}).items():
        console.print(f"\n[bold]▎ {dim_id}[/bold]")
        console.print(summary[:500])

    if result.get("subbranch_results"):
        console.print("\n[bold yellow]Subbranches:[/bold yellow]")
        for name, r in result["subbranch_results"].items():
            console.print(f"\n[bold]▎ 子分支: {name}[/bold]")
            console.print(r["mini_summary"][:500])

    console.print(f"\n[bold]Citations:[/bold] {len(result.get('citations', []))} 条")
    console.print(f"[bold]Session ID:[/bold] {result.get('session_id')}")


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "为什么半导体板块今天涨"
    main(q)
```

**Step 2: 跑 smoke**

```bash
uv run python scripts/run_main_graph_smoke.py "为什么半导体板块今天涨"
```

Expected: 输出
- Target、Time window、Domain 正确
- 6 维 dimension_reports 都有内容（即使某维 no_data 也应该有"未检索到..."字样）
- 顶部 narrative 至少 80 字
- 至少 1 条 citation
- 报告**可读、连贯、没有明显幻觉**

如果某维 LLM 调用挂掉、Adapter 报错，把错误抓回来分析。允许的"不完美"：individual 维度 no_data（但全部 no_data 不行）。

**Step 3: 验证 MySQL 已经存了 session + tree**

```bash
uv run python -c "
from sqlalchemy import text
from explain_agent.db.mysql import get_engine
with get_engine('explain').connect() as c:
    s = c.execute(text('SELECT session_id, target, confidence, narrative FROM explain_session JOIN explain_evidence_tree USING (session_id) ORDER BY created_at DESC LIMIT 1')).fetchone()
    print(s)
"
```
Expected: 看到刚刚跑的 session 数据。

**Step 4: 在 README 追加 Phase 2.A 验收清单**

读 README，在末尾追加：

```markdown

## Phase 2.A 验收状态

- [x] LangGraph 状态机骨架（parse → router → framework → market_facts → fan_out → synth → sub → report → persist）
- [x] AttributionState schema + 框架 YAML 配置
- [x] DimensionWorker mini ReAct 内核（10 轮上限 + 软终止 + no_data 合法）
- [x] Semaphore(3) 受控并发
- [x] 节点单测全部通过（L1）
- [x] 维度 worker 单测全部通过（L2）
- [x] main_graph 集成测试通过（mock 节点）
- [x] 真实端到端 smoke：能对"为什么半导体板块今天涨"输出 6 维归因报告
```

**Step 5: Commit**

```bash
git add scripts/run_main_graph_smoke.py README.md
git commit -m "Phase 2.A 端到端 smoke 脚本 + 验收清单

scripts/run_main_graph_smoke.py 用真实 adapter/llm/engine 跑一遍 main graph
并 Rich 打印结果，便于手动验收。"
```

---

## 完成后

Phase 2.A 完成后的产出：

- ✅ 完整 LangGraph 主 graph（parse → router → framework → facts → fan_out → synth → sub → report → persist）
- ✅ DimensionWorker mini ReAct 内核 + Semaphore 并发控制
- ✅ Framework YAML 配置（6 维定义 + worker_config）
- ✅ Persistence 到 MySQL（session + 完整证据树 JSON）
- ✅ 节点单测 + 维度 worker 单测 + main graph 集成测试
- ✅ 真实端到端 smoke 脚本

**下一步：进入 Phase 2.B 实施计划**，重点是：

- Strong model 叙事段（强制 ground-in-evidence + JSON schema 输出）
- 五层幻觉防控（数值校验、双重校验、confidence、永久快照）
- 维度报告改为 strong model 重写（而不是直接用 mini_summary）

待 Phase 2.A 跑通后，再做 Phase 2.B 的 brainstorm 细化（看 2.A 实测发现的问题）。
