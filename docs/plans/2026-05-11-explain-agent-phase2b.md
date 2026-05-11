# Explain Agent · Phase 2.B 实施计划（强模型叙事 + 维度重写 + 校验 + confidence 重打分）

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把 main graph 输出从"能跑通"升级到"敢给人看"——narrative 每句话挂 evidence_id 可追溯、维度报告由强模型统一重写、数字必须能在证据中找到精确匹配、confidence 反映真实证据基础。

**Architecture:** 不动 graph 拓扑，所有改动集中在 `report_builder` 节点 + `state` schema 扩展 + 三处数据兜底（parse_question prompt、DimensionWorker 扩窗、akshare 重试）。

**Tech Stack:** Python 3.11+、LangGraph（已有）、tenacity（新增重试装饰器使用）、pytest-mock（已有）

**Phase 2.B 不做的：**
- 实体校验（容易误杀强模型合理推理）
- 双重 LLM 校验（成本翻倍收益边际）
- 永久快照（Phase 2.C）
- followup graph / CLI REPL（Phase 2.C）
- Eval framework（Phase 2.D）

**前置依赖：**
- Phase 2.A 已完工（commit `e0655df`）
- Phase 2.B 设计文档已就绪（commit `0a9d085`，`docs/plans/2026-05-11-explain-agent-phase2b-design.md`）
- `.env` 真实凭证已就位
- 全部 Phase 2.A 单测通过（51 passed）

---

## 任务总览

| # | 任务 | 预计时长 |
|---|---|---|
| 1 | parse_question prompt 调整："今天" → 5 交易日窗口 | 20 min |
| 2 | DimensionWorker 扩窗兜底（time_window ≤ 3 天时扩到 7 天） | 30 min |
| 3 | akshare adapter 加 tenacity 重试 | 20 min |
| 4 | State schema 扩展 (NarrativeClaim + narrative_claims + unverified_drops) | 20 min |
| 5 | report_builder · 强模型叙事重写（JSON + evidence_ids） | 60 min |
| 6 | report_builder · 数值校验 (_strip_unverified_numbers) | 50 min |
| 7 | report_builder · 维度报告重写 (_rewrite_dim_report) | 50 min |
| 8 | report_builder · confidence 重打分 | 30 min |
| 9 | 装配新 report_builder + 更新 main_graph 集成测试 | 40 min |
| 10 | 端到端 smoke 验收（同一问题对比 2.A vs 2.B） | 30 min |

**合计：约 5.5 小时纯开发，预计 1-2 个工作日完成。**

---

## Task 1: parse_question prompt 调整

**目标：** 把"今天"语义从单日改为最近 5 个交易日，消除单日窗口导致的 no_data。

**Files:**
- Modify: `src/explain_agent/graph/nodes/parse_question.py`
- Modify: `tests/test_node_parse_question.py`

**Step 1: 修改 prompt 中的时间规则**

打开 `src/explain_agent/graph/nodes/parse_question.py`，找到 `SYSTEM` 常量中关于时间窗的描述。当前规则：

```
- 时间窗：默认 end=今天，start=今天-7天；若用户提"上周"则推 7-14 天前；"今天"则 start=end=今天
```

改为：

```
- 时间窗：默认 end=今天，start=今天-7天；若用户提"上周"则推 7-14 天前
- "今天"语义：end=今天，start=今天-5天（覆盖最近 5 个自然日，包含上一个交易日）
- 若用户给出明确日期则严格按用户给定
```

**Step 2: 改造对应单测**

打开 `tests/test_node_parse_question.py`，新增一个测试：

```python
@pytest.mark.asyncio
async def test_parse_today_expands_to_five_days():
    """'今天'语义应展开为最近 5 天窗口，避免单日窗口导致 CK 查空。"""
    import json
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({
        "target": "半导体",
        "time_window_start": "2026-05-07",
        "time_window_end": "2026-05-12",
        "intent": "up",
    })
    state = new_attribution_state("为什么半导体今天涨")
    state["asked_at"] = datetime(2026, 5, 12, 15, 0)

    out = await parse_question_node(state, llm=fake_llm)
    # 关键: 窗口至少跨 5 天
    delta_days = (out["time_window"][1] - out["time_window"][0]).days
    assert delta_days >= 4
```

**Step 3: 运行新测试 + 既有测试**

Run: `uv run pytest tests/test_node_parse_question.py -v`
Expected: 3 passed（2 老的 + 1 新的）。新测试通过的关键是 mock LLM 返回符合新 prompt 期望的 JSON。

**Step 4: Commit**

```bash
git add src/explain_agent/graph/nodes/parse_question.py tests/test_node_parse_question.py
git commit -m "$(cat <<'EOF'
parse_question · "今天"语义扩展为 5 天窗口

Phase 2.A 实测发现单日窗口（仅 2026-05-11）导致 clickhouse_market
查空，进而 technical/capital_flow 维度 no_data。改 prompt 让 LLM
对"今天"输出 5 天回看窗口。
EOF
)"
```

---

## Task 2: DimensionWorker 扩窗兜底

**目标：** 在 `_fetch_all_sources` 加一层 fallback——若所有 adapter 都返回空且 time_window ≤ 3 天，自动把窗口扩到 7 天再试一次。

**Files:**
- Modify: `src/explain_agent/graph/dimension_worker.py`
- Modify: `tests/test_dimension_worker.py`

**Step 1: 写测试（先 fail）**

在 `tests/test_dimension_worker.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_worker_auto_expands_window_on_empty():
    """单日窗口查空时自动扩到 7 天再试。"""
    import json
    fake_llm = MagicMock()
    fake_llm.chat.side_effect = [
        json.dumps({"keywords": ["k1"]}),
        json.dumps({"sufficient": True, "relevant_ids": ["e1"]}),
        "summary",
    ]

    call_windows = []

    async def adapter_query(q):
        call_windows.append((q.time_window[0], q.time_window[1]))
        # 第一次窄窗口返回空,第二次扩窗后返回非空
        if (q.time_window[1] - q.time_window[0]).days <= 3:
            return []
        return [make_evidence("e1")]

    mock_adapter = MagicMock()
    mock_adapter.query = adapter_query

    worker = DimensionWorker(
        dimension_config={"id": "x", "name": "X", "data_sources": ["news_corpus"],
                          "query_template": "t"},
        worker_config={"max_rounds": 1, "soft_terminate_no_gain_rounds": 99},
        llm=fake_llm, adapter_registry={"news_corpus": mock_adapter},
    )
    result = await worker.run(
        target="X",
        time_window=(date(2026, 5, 11), date(2026, 5, 11)),  # 单日
        market_facts={},
    )
    # 验证至少调了两次,且第二次窗口被扩了
    assert len(call_windows) >= 2
    expanded = call_windows[-1]
    assert (expanded[1] - expanded[0]).days >= 6
    assert result["no_data"] is False
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_dimension_worker.py::test_worker_auto_expands_window_on_empty -v`
Expected: FAIL（当前实现单窗口查空就放弃，不会扩窗）。

**Step 3: 实现扩窗逻辑**

修改 `src/explain_agent/graph/dimension_worker.py` 的 `_fetch_all_sources` 方法。当前实现是直接调每个 adapter；改造为：

```python
async def _fetch_all_sources(
    self, keywords: list[str], target: str, time_window: tuple[date, date],
) -> list[Evidence]:
    out = await self._fetch_once(keywords, target, time_window)
    # 兜底: 窄窗口(≤3 天)查空时自动扩到 7 天再试一次
    if not out and (time_window[1] - time_window[0]).days <= 3:
        from datetime import timedelta
        expanded = (time_window[1] - timedelta(days=6), time_window[1])
        out = await self._fetch_once(keywords, target, expanded)
    return out

async def _fetch_once(
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
```

注意：把原来的 `_fetch_all_sources` 内部循环逻辑抽到新方法 `_fetch_once`，外层方法负责"先试原窗口，空就扩窗"。

**Step 4: 运行所有 worker 测试**

Run: `uv run pytest tests/test_dimension_worker.py -v`
Expected: 4 passed（3 老的 + 1 新的）。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/dimension_worker.py tests/test_dimension_worker.py
git commit -m "$(cat <<'EOF'
DimensionWorker · 单日窗口查空时自动扩到 7 天

Phase 2.A 实测发现 clickhouse_market 数据滞后到 2026-05-06，但用户问
"今天"时窗口被 parse 成 2026-05-11 单日，导致 CK 查空。worker 层加
fallback：所有 adapter 都返回空且窗口 ≤3 天时，自动扩到 7 天再试一次。
EOF
)"
```

---

## Task 3: akshare adapter 加 tenacity 重试

**目标：** `AkshareCapitalFlowAdapter.query` 用 tenacity 包装 `ak.stock_sector_fund_flow_hist` 调用，缓解 ConnectionError 偶发问题。

**Files:**
- Modify: `src/explain_agent/adapters/akshare_capital_flow.py`
- Modify: `tests/test_akshare_capital_flow_adapter.py`

**Step 1: 写测试**

在 `tests/test_akshare_capital_flow_adapter.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_akshare_retries_on_connection_error(monkeypatch):
    """首次 ConnectionError 后重试成功,不应直接返回空。"""
    import pandas as pd
    from datetime import date
    from explain_agent.adapters.akshare_capital_flow import AkshareCapitalFlowAdapter
    from explain_agent.core.types import AdapterQuery

    call_count = {"n": 0}
    good_df = pd.DataFrame({
        "日期": [date(2026, 5, 10)],
        "主力净流入-净额": [1e9],
        "主力净流入-净占比": [5.0],
    })

    def flaky(symbol):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ConnectionError("flaky")
        return good_df

    import akshare as ak
    monkeypatch.setattr(ak, "stock_sector_fund_flow_hist", flaky)

    adapter = AkshareCapitalFlowAdapter()
    out = await adapter.query(AdapterQuery(
        keywords=[], time_window=(date(2026, 5, 5), date(2026, 5, 12)),
        target="半导体",
    ))
    assert call_count["n"] == 2  # 重试了一次
    assert len(out) == 1
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_akshare_capital_flow_adapter.py::test_akshare_retries_on_connection_error -v`
Expected: FAIL（当前实现 except 直接吞掉，不会重试）。

**Step 3: 实现重试**

修改 `src/explain_agent/adapters/akshare_capital_flow.py`：

```python
from datetime import datetime
from uuid import uuid4
import akshare as ak
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from explain_agent.core.types import AdapterQuery, Evidence


class AkshareCapitalFlowAdapter:
    name = "akshare_capital_flow"

    async def query(self, q: AdapterQuery) -> list[Evidence]:
        return self._industry_main_flow(q)

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
        retry=retry_if_exception_type(ConnectionError),
        reraise=False,
    )
    def _fetch_df(self, target: str) -> pd.DataFrame | None:
        try:
            return ak.stock_sector_fund_flow_hist(symbol=target)
        except ConnectionError:
            raise  # 让 tenacity 重试
        except Exception:
            return None

    def _industry_main_flow(self, q: AdapterQuery) -> list[Evidence]:
        try:
            df = self._fetch_df(q.target)
        except Exception:
            return []
        if df is None or df.empty:
            return []
        # ...保持原有处理逻辑不变
```

注意：把 `ak.stock_sector_fund_flow_hist` 调用抽到 `_fetch_df` 方法，加 tenacity 装饰器。其他逻辑保持不变。

**Step 4: 运行测试**

Run: `uv run pytest tests/test_akshare_capital_flow_adapter.py -v`
Expected: 全部通过（原有 N 个 + 新 1 个）。

**Step 5: Commit**

```bash
git add src/explain_agent/adapters/akshare_capital_flow.py tests/test_akshare_capital_flow_adapter.py
git commit -m "$(cat <<'EOF'
akshare adapter · ConnectionError 加 tenacity 重试

Phase 2.A 实测发现东方财富接口偶发 ConnectionError 直接被吞掉，
导致 capital_flow 维度 no_data。加 2 次指数退避重试。
EOF
)"
```

---

## Task 4: State schema 扩展

**目标：** 加 `NarrativeClaim` 类型 + `narrative_claims` 和 `unverified_drops` 字段。

**Files:**
- Modify: `src/explain_agent/graph/state.py`
- Modify: `tests/test_graph_state.py`

**Step 1: 写测试**

在 `tests/test_graph_state.py` 末尾追加：

```python
def test_narrative_claim_typeddict():
    from explain_agent.graph.state import NarrativeClaim
    c: NarrativeClaim = {
        "text": "半导体板块上涨 5%",
        "evidence_ids": ["e_001", "e_007"],
    }
    assert c["evidence_ids"] == ["e_001", "e_007"]


def test_new_state_has_narrative_claims_and_drops():
    s = new_attribution_state(raw_question="test")
    assert s["narrative_claims"] == []
    assert s["unverified_drops"] == []
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_graph_state.py -v`
Expected: FAIL（NarrativeClaim 不存在 + new_attribution_state 没初始化新字段）。

**Step 3: 修改 state.py**

打开 `src/explain_agent/graph/state.py`：

```python
# 在 Citation 类型旁边新增
class NarrativeClaim(TypedDict):
    text: str
    evidence_ids: list[str]


# 在 AttributionState 中新增字段（在 narrative 旁边）
class AttributionState(TypedDict, total=False):
    # ...已有字段不动
    narrative: str
    narrative_claims: list[NarrativeClaim]  # 新增
    unverified_drops: list[str]  # 新增
    # ...其余不动
```

更新 `new_attribution_state` 的默认值字典：

```python
def new_attribution_state(raw_question: str, session_id: str | None = None) -> AttributionState:
    return {
        # ...已有默认值
        "narrative": "",
        "narrative_claims": [],   # 新增
        "unverified_drops": [],   # 新增
        # ...其余不动
    }
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_graph_state.py -v`
Expected: 6 passed（4 老的 + 2 新的）。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/state.py tests/test_graph_state.py
git commit -m "$(cat <<'EOF'
State schema 扩展 · NarrativeClaim + narrative_claims + unverified_drops

为 Phase 2.B 强模型叙事重写做准备：每个 claim 必须挂 evidence_ids，
被数值校验删掉的句子记录到 unverified_drops 便于审计。
EOF
)"
```

---

## Task 5: report_builder · 强模型叙事重写（JSON + evidence_ids）

**目标：** narrative 调用改用 JSON 输出格式，每个 claim 必须挂 ≥1 个 evidence_id。

**Files:**
- Modify: `src/explain_agent/graph/nodes/report_builder.py`
- Modify: `tests/test_node_report_builder.py`

**Step 1: 写测试**

替换 `tests/test_node_report_builder.py` 现有的测试 + 追加新测试：

```python
import json
from datetime import date, datetime
from unittest.mock import MagicMock
import pytest
from explain_agent.core.types import Evidence
from explain_agent.graph.state import new_attribution_state, DimensionResult
from explain_agent.graph.nodes.report_builder import report_builder_node


def make_ev(id: str, snippet: str = "snip", source_type: str = "news", url: str | None = "http://a.com") -> Evidence:
    return Evidence(
        id=id, source="x", source_type=source_type,
        url=url, snippet=snippet, timestamp=datetime.now(),
    )


@pytest.mark.asyncio
async def test_narrative_returns_structured_claims():
    """强模型返回 JSON,每个 claim 都有 evidence_ids。"""
    fake_llm = MagicMock()
    # 第 1 次: narrative JSON
    fake_llm.chat.return_value = json.dumps({
        "claims": [
            {"text": "半导体板块上涨主因是政策支持。", "evidence_ids": ["e1"]},
            {"text": "存储芯片涨价拉动设备需求。", "evidence_ids": ["e2", "e3"]},
        ],
    })
    state = new_attribution_state("test")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": "板块涨 5%"}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", "政策支持")], mini_summary="政策维",
            retry_count=1, no_data=False, confidence="high",
        ),
        "industry_chain": DimensionResult(
            evidence=[make_ev("e2", "存储涨价"), make_ev("e3", "设备需求")],
            mini_summary="产业链维", retry_count=1, no_data=False, confidence="high",
        ),
    }

    out = await report_builder_node(state, llm=fake_llm)
    assert len(out["narrative_claims"]) == 2
    assert all(c["evidence_ids"] for c in out["narrative_claims"])
    assert "半导体板块上涨主因是政策支持" in out["narrative"]
    assert "存储芯片涨价" in out["narrative"]


@pytest.mark.asyncio
async def test_narrative_falls_back_when_json_invalid():
    """JSON 解析失败时回退到纯文本 narrative, claims 为空。"""
    fake_llm = MagicMock()
    fake_llm.chat.return_value = "no json here, just text"
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {}

    out = await report_builder_node(state, llm=fake_llm)
    assert out["narrative"] == "no json here, just text"
    assert out["narrative_claims"] == []
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: FAIL（当前实现 narrative 是纯文本，没 narrative_claims 字段输出）。

**Step 3: 改造 report_builder.py**

打开 `src/explain_agent/graph/nodes/report_builder.py`，替换 `NARRATIVE_SYSTEM` 和重写 narrative 调用逻辑：

```python
import json
import re

from explain_agent.graph.state import AttributionState, Citation, NarrativeClaim
from explain_agent.llm import LLMClient, get_strong_llm


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


async def report_builder_node(
    state: AttributionState,
    llm: LLMClient | None = None,
) -> dict:
    llm = llm or get_strong_llm()
    dim_results = state["dimension_results"]
    sub_results = state.get("subbranch_results", {})

    # 收集全部 evidence 作为强模型的引用池
    all_evidence: list = []
    for r in list(dim_results.values()) + list(sub_results.values()):
        all_evidence.extend(r["evidence"])

    evidence_dump = [
        {"id": e.id, "source_type": e.source_type, "snippet": e.snippet[:300]}
        for e in all_evidence
    ]

    user = (
        f"标的: {state['target']}\n"
        f"时间窗: {state['time_window'][0]} ~ {state['time_window'][1]}\n"
        f"市场锚点: {state['market_facts'].get('snippet', '')}\n"
        f"证据池:\n{json.dumps(evidence_dump, ensure_ascii=False)}"
    )
    raw = llm.chat(system=NARRATIVE_SYSTEM, user=user, max_tokens=4000)
    data = _extract_json(raw)

    if not data or "claims" not in data:
        # 回退: 直接用 raw 作为 narrative,claims 为空
        narrative = raw
        narrative_claims: list[NarrativeClaim] = []
    else:
        claims_raw = data.get("claims", [])
        narrative_claims = [
            NarrativeClaim(text=c.get("text", ""), evidence_ids=c.get("evidence_ids", []))
            for c in claims_raw
            if c.get("text") and c.get("evidence_ids")
        ]
        narrative = " ".join(c["text"] for c in narrative_claims)

    # 维度报告: Phase 2.A 行为暂时保留(下一 task 改造)
    dim_reports = {dim_id: r["mini_summary"] for dim_id, r in dim_results.items()}

    # citations
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

    # confidence: Phase 2.A 行为保留(下一 task 改造)
    high_count = sum(1 for r in dim_results.values() if r["confidence"] == "high")
    if high_count >= 3:
        confidence = "high"
    elif high_count >= 1:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "narrative": narrative,
        "narrative_claims": narrative_claims,
        "dimension_reports": dim_reports,
        "citations": citations,
        "confidence": confidence,
    }
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: 2 passed（覆盖 JSON 路径 + 回退路径）。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/nodes/report_builder.py tests/test_node_report_builder.py
git commit -m "$(cat <<'EOF'
report_builder · 强模型叙事改 JSON 输出 + evidence_id 引用

每个 claim 必须挂 ≥1 个 evidence_id，便于后续数值校验与可追溯性。
JSON 解析失败时回退到纯文本 narrative，确保不阻塞 graph。
EOF
)"
```

---

## Task 6: report_builder · 数值校验

**目标：** 实现 `_strip_unverified_numbers`——删除 narrative 中数字找不到精确匹配的句子。

**Files:**
- Modify: `src/explain_agent/graph/nodes/report_builder.py`
- Modify: `tests/test_node_report_builder.py`

**Step 1: 写测试**

在 `tests/test_node_report_builder.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_strip_unverified_numbers_keeps_verified():
    """claim 中的数字在证据中能找到 → 保留。"""
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({
        "claims": [
            {"text": "板块涨 5% 受政策推动", "evidence_ids": ["e1"]},
        ],
    })
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", "今日板块涨幅 5%")],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert "板块涨 5%" in out["narrative"]
    assert out["unverified_drops"] == []


@pytest.mark.asyncio
async def test_strip_unverified_numbers_drops_hallucinated():
    """claim 中的数字在证据中找不到 → 整句删除并记 unverified_drops。"""
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({
        "claims": [
            {"text": "政策利好推动情绪修复", "evidence_ids": ["e1"]},
            {"text": "成交额放大至 200 亿", "evidence_ids": ["e1"]},  # 200 亿不在证据里
        ],
    })
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", "证监会发文支持半导体产业")],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert "政策利好推动情绪修复" in out["narrative"]
    assert "200 亿" not in out["narrative"]
    assert len(out["unverified_drops"]) == 1
    assert "200 亿" in out["unverified_drops"][0]


@pytest.mark.asyncio
async def test_strip_keeps_claims_without_numbers():
    """无数字的 claim 不受校验影响。"""
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({
        "claims": [{"text": "市场情绪偏暖", "evidence_ids": ["e1"]}],
    })
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", "无具体数字的证据")],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert "市场情绪偏暖" in out["narrative"]
    assert out["unverified_drops"] == []
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: 部分 FAIL（特别是 drop 测试，因为还没实现校验）。

**Step 3: 实现 _strip_unverified_numbers**

在 `src/explain_agent/graph/nodes/report_builder.py` 中新增辅助函数：

```python
_NUM_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:%|亿|万|千|百|次|倍|个|台|条)?")


def _extract_numbers(text: str) -> list[str]:
    """提取所有'看起来像数字'的 token。"""
    return _NUM_PATTERN.findall(text)


def _normalize(token: str) -> str:
    """归一化：去掉空格，便于在证据文本中做子串匹配。"""
    return token.replace(" ", "")


def _strip_unverified_numbers(
    claims: list[NarrativeClaim],
    evidence_pool: list,
) -> tuple[list[NarrativeClaim], list[str]]:
    """对每个 claim 检查其包含的数字 token 是否能在引用证据的 snippet+raw_payload 中找到。

    找不到 → 整 claim 删除并加入 unverified_drops。
    无数字的 claim 直接保留。
    """
    evidence_by_id = {e.id: e for e in evidence_pool}
    kept: list[NarrativeClaim] = []
    dropped: list[str] = []

    for claim in claims:
        numbers = _extract_numbers(claim["text"])
        if not numbers:
            kept.append(claim)
            continue

        # 拼接引用证据的全部可搜索文本
        haystacks: list[str] = []
        for eid in claim["evidence_ids"]:
            e = evidence_by_id.get(eid)
            if e is None:
                continue
            haystacks.append(e.snippet or "")
            if e.raw_payload is not None:
                haystacks.append(json.dumps(e.raw_payload, ensure_ascii=False, default=str))
        haystack = _normalize("\n".join(haystacks))

        # 所有数字都要能找到; 任一找不到则整 claim 删
        all_found = all(_normalize(n) in haystack for n in numbers)
        if all_found:
            kept.append(claim)
        else:
            dropped.append(claim["text"])

    return kept, dropped
```

然后在 `report_builder_node` 中接入校验，紧跟在 narrative_claims 构造之后：

```python
# narrative_claims 构造完之后:
narrative_claims, unverified_drops = _strip_unverified_numbers(narrative_claims, all_evidence)
narrative = " ".join(c["text"] for c in narrative_claims) if narrative_claims else narrative

# return 时新增 unverified_drops 字段:
return {
    "narrative": narrative,
    "narrative_claims": narrative_claims,
    "unverified_drops": unverified_drops,
    "dimension_reports": dim_reports,
    "citations": citations,
    "confidence": confidence,
}
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: 5 passed（原 2 个 + 新 3 个）。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/nodes/report_builder.py tests/test_node_report_builder.py
git commit -m "$(cat <<'EOF'
report_builder · 数值校验 _strip_unverified_numbers

对每个 narrative claim 提取数字 token，必须能在引用证据的
snippet+raw_payload 中找到精确匹配，否则整句删除并记入
unverified_drops 便于审计。无数字的 claim 不受影响。
EOF
)"
```

---

## Task 7: report_builder · 维度报告重写

**目标：** 6 个有数据的维度都用强模型重写报告，强制以 `[e_xxx]` 格式引用 evidence_id；`no_data` 维度保持原文。

**Files:**
- Modify: `src/explain_agent/graph/nodes/report_builder.py`
- Modify: `tests/test_node_report_builder.py`

**Step 1: 写测试**

在 `tests/test_node_report_builder.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_dim_reports_rewritten_by_strong_llm():
    """有数据的维度由强模型重写,no_data 维度保持原文。"""
    fake_llm = MagicMock()
    # 序列: [narrative JSON, 维度 1 重写, 维度 2 重写]
    fake_llm.chat.side_effect = [
        json.dumps({"claims": [{"text": "test claim", "evidence_ids": ["e1"]}]}),
        "[政策维度重写] 见 [e1] 政策利好。",
        # technical 维度 no_data,不调 LLM
    ]
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", "政策证据")],
            mini_summary="弱模型版", retry_count=1, no_data=False, confidence="high",
        ),
        "technical": DimensionResult(
            evidence=[], mini_summary="本维度未检索到相关证据",
            retry_count=10, no_data=True, confidence="low",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert "[政策维度重写]" in out["dimension_reports"]["policy"]
    assert "[e1]" in out["dimension_reports"]["policy"]
    # no_data 维度不调 LLM,保持原 mini_summary
    assert out["dimension_reports"]["technical"] == "本维度未检索到相关证据"


@pytest.mark.asyncio
async def test_dim_reports_skip_llm_when_no_data():
    """no_data 维度不应触发任何 strong LLM 调用 (除 narrative)。"""
    fake_llm = MagicMock()
    fake_llm.chat.side_effect = [
        json.dumps({"claims": []}),  # narrative
        # 后续不应再被调用
    ]
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "d1": DimensionResult(evidence=[], mini_summary="无数据", retry_count=1, no_data=True, confidence="low"),
        "d2": DimensionResult(evidence=[], mini_summary="无数据", retry_count=1, no_data=True, confidence="low"),
    }
    await report_builder_node(state, llm=fake_llm)
    assert fake_llm.chat.call_count == 1  # 只调了 narrative
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: FAIL（当前还在用 mini_summary 直出）。

**Step 3: 实现 _rewrite_dim_report**

在 `src/explain_agent/graph/nodes/report_builder.py` 中：

```python
DIM_REPORT_SYSTEM = """你是该维度的资深分析师。基于给定证据池，写一段 200-400 字的维度内归因报告。

要求:
- 每个关键论点后用 [e_xxx] 标注证据 id (例如: "存储芯片涨价 [e_001] [e_002]")
- evidence_id 必须来自给定证据池
- 不出现未在证据中出现的数字或具体描述
- 论述结构清晰、可读
- 不预测,不推荐操作

直接输出维度报告文本(不要 JSON, 不要标题, 不要前缀)。
"""


def _rewrite_dim_report(
    dim_id: str,
    dim_result,
    target: str,
    market_facts: dict,
    llm: LLMClient,
) -> str:
    """单个维度的强模型重写。no_data 维度直接返回原 mini_summary。"""
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
    try:
        return llm.chat(system=DIM_REPORT_SYSTEM, user=user, max_tokens=4000)
    except Exception:
        # 单维度失败 fallback 到原 mini_summary,不阻塞整个 graph
        return dim_result["mini_summary"]
```

然后在 `report_builder_node` 中替换 `dim_reports` 的构造：

```python
# 旧的:
# dim_reports = {dim_id: r["mini_summary"] for dim_id, r in dim_results.items()}

# 新的:
dim_reports = {
    dim_id: _rewrite_dim_report(dim_id, r, state["target"], state["market_facts"], llm)
    for dim_id, r in dim_results.items()
}
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: 7 passed（原 5 + 新 2）。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/nodes/report_builder.py tests/test_node_report_builder.py
git commit -m "$(cat <<'EOF'
report_builder · 维度报告强模型重写 + [e_xxx] 引用

6 个有数据的维度统一用强模型重写到 200-400 字，强制引用 evidence_id。
no_data 维度跳过 LLM 调用，保留"本维度未检索到相关证据"原文。
单维度失败 fallback 到弱模型 mini_summary，不阻塞 report_builder。
EOF
)"
```

---

## Task 8: report_builder · confidence 重打分

**目标：** confidence 从"高 confidence 维度数量"改为"被引用 evidence 数 × source_type 多样性"。

**Files:**
- Modify: `src/explain_agent/graph/nodes/report_builder.py`
- Modify: `tests/test_node_report_builder.py`

**Step 1: 写测试**

在 `tests/test_node_report_builder.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_confidence_high_with_diverse_sources():
    """≥8 个被引用 evidence 且 source_type 多样性 ≥3 → high。"""
    fake_llm = MagicMock()
    eids = [f"e{i}" for i in range(10)]
    fake_llm.chat.side_effect = [
        json.dumps({"claims": [{"text": f"claim{i}", "evidence_ids": eids[i:i+2]} for i in range(8)]}),
        "policy dim report",
        "industry_chain dim report",
        "capital_flow dim report",
    ]
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev(eids[i], source_type="news") for i in range(4)],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
        "industry_chain": DimensionResult(
            evidence=[make_ev(eids[i], source_type="market_data") for i in range(4, 7)],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
        "capital_flow": DimensionResult(
            evidence=[make_ev(eids[i], source_type="capital_flow") for i in range(7, 10)],
            mini_summary="", retry_count=1, no_data=False, confidence="medium",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert out["confidence"] == "high"


@pytest.mark.asyncio
async def test_confidence_low_with_few_citations():
    """被引用 evidence 数 < 4 → low。"""
    fake_llm = MagicMock()
    fake_llm.chat.side_effect = [
        json.dumps({"claims": [{"text": "single", "evidence_ids": ["e1"]}]}),
        "policy dim report",
    ]
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", source_type="news")],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert out["confidence"] == "low"


@pytest.mark.asyncio
async def test_confidence_medium_with_4_citations_2_sources():
    """4 个 evidence,2 种 source_type → medium。"""
    fake_llm = MagicMock()
    eids = [f"e{i}" for i in range(4)]
    fake_llm.chat.side_effect = [
        json.dumps({"claims": [{"text": "c1", "evidence_ids": eids}]}),
        "policy dim report",
        "industry_chain dim report",
    ]
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e0", source_type="news"), make_ev("e1", source_type="news")],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
        "industry_chain": DimensionResult(
            evidence=[make_ev("e2", source_type="market_data"), make_ev("e3", source_type="market_data")],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert out["confidence"] == "medium"
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: FAIL（旧打分逻辑只看高 confidence 维度数）。

**Step 3: 实现新打分**

在 `src/explain_agent/graph/nodes/report_builder.py` 替换 confidence 计算逻辑：

```python
def _estimate_overall_confidence(
    dim_results: dict,
    narrative_claims: list[NarrativeClaim],
) -> str:
    cited_ids: set[str] = set()
    for c in narrative_claims:
        cited_ids.update(c["evidence_ids"])

    # 统计被引用 evidence 的 source_type 多样性
    source_types: set[str] = set()
    for r in dim_results.values():
        for e in r["evidence"]:
            if e.id in cited_ids:
                source_types.add(e.source_type)

    cited_count = len(cited_ids)
    type_count = len(source_types)

    if cited_count >= 8 and type_count >= 3:
        return "high"
    if cited_count >= 4 and type_count >= 2:
        return "medium"
    return "low"
```

在 `report_builder_node` 中替换原打分代码:

```python
# 旧的: high_count = sum(...) + 3 个 if 分支
# 新的:
confidence = _estimate_overall_confidence(dim_results, narrative_claims)
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: 10 passed（原 7 + 新 3）。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/nodes/report_builder.py tests/test_node_report_builder.py
git commit -m "$(cat <<'EOF'
report_builder · confidence 改按被引用 evidence 数 × 来源多样性打分

旧逻辑只看"高 confidence 维度数量"，与实际证据基础脱节。
新逻辑统计 narrative 实际引用的 evidence_id 数 + 它们的 source_type
多样性。阈值 ≥8/≥3 = high，≥4/≥2 = medium，否则 low。
EOF
)"
```

---

## Task 9: 更新 main_graph 集成测试

**目标：** `test_main_graph.py` 的 `fake_strong_llm.side_effect` 适配新调用序列：synthesizer + narrative + 6 维重写。

**Files:**
- Modify: `tests/test_main_graph.py`

**Step 1: 读现有集成测试**

打开 `tests/test_main_graph.py`，定位到 `fake_strong_llm.chat.side_effect`。当前是 2 个返回值（synthesizer + narrative）。

**Step 2: 改造 strong_llm side_effect**

替换为 8 个返回值（synthesizer + narrative + 6 维重写）：

```python
fake_strong_llm = MagicMock()
import json as _json
fake_strong_llm.chat.side_effect = [
    _json.dumps({"needs_subbranch": False, "subbranches": []}),  # synthesizer
    _json.dumps({  # narrative
        "claims": [
            {"text": "测试叙事 claim 1", "evidence_ids": ["e1"]},
            {"text": "测试叙事 claim 2", "evidence_ids": ["e1"]},
        ],
    }),
    "维度 1 重写报告 [e1]",
    "维度 2 重写报告 [e1]",
    "维度 3 重写报告 [e1]",
    "维度 4 重写报告 [e1]",
    "维度 5 重写报告 [e1]",
    "维度 6 重写报告 [e1]",
]
```

补充断言以验证 Phase 2.B 新行为：

```python
result = await graph.ainvoke(state)
# 原有断言保留
assert result["target"] == "半导体"
assert result["domain_id"] == "cn_equity_sector_attribution"
assert len(result["dimension_results"]) == 6
# Phase 2.B 新增断言:
assert len(result["narrative_claims"]) >= 1
assert all(c["evidence_ids"] for c in result["narrative_claims"])
assert all("维度" in v and "重写" in v for v in result["dimension_reports"].values())
```

**Step 3: 跑集成测试 + 全量回归**

Run: `uv run pytest tests/test_main_graph.py -v`
Expected: 1 passed。

Run: `uv run pytest`
Expected: 全部通过（Phase 2.A 51 + 本期新增 ~13 = 64 左右）。

**Step 4: Commit**

```bash
git add tests/test_main_graph.py
git commit -m "$(cat <<'EOF'
main_graph 集成测试适配 Phase 2.B 调用序列

strong_llm 调用增加为 8 次：synthesizer + narrative JSON + 6 维重写。
新增断言验证 narrative_claims 非空且每个 claim 都有 evidence_ids，
dimension_reports 来自强模型重写。
EOF
)"
```

---

## Task 10: 端到端 smoke 验收

**目标：** 用真实 .env 跑同一问题，对比 2.A vs 2.B 的 narrative 质量、引用完整度、no_data 比例。

**Files:**
- 无新文件
- 复用 `scripts/run_main_graph_smoke.py`

**Step 1: 修改 smoke 脚本打印更多信息**

打开 `scripts/run_main_graph_smoke.py`，在打印 narrative 之后追加：

```python
# 在 narrative 打印之后:
claims = result.get("narrative_claims", [])
console.print(f"\n[bold]Narrative claims ({len(claims)} 条):[/bold]")
for i, c in enumerate(claims, 1):
    console.print(f"  {i}. [cyan]{c['text']}[/cyan]  → {c['evidence_ids']}")

drops = result.get("unverified_drops", [])
if drops:
    console.print(f"\n[bold red]Unverified drops ({len(drops)} 条):[/bold red]")
    for d in drops:
        console.print(f"  - {d}")
else:
    console.print("\n[dim]Unverified drops: 0 (强模型表现良好或无数字声明)[/dim]")
```

**Step 2: 跑 smoke**

```bash
uv run python scripts/run_main_graph_smoke.py "为什么半导体板块今天涨"
```

**验收标准：**
- ✅ no_data 维度 ≤ 1/6（Phase 2.A 是 2/6）
- ✅ 每个 narrative claim 都有 ≥1 个 evidence_id
- ✅ 至少一个维度报告中出现 `[e_` 引用标记
- ✅ `unverified_drops` 字段存在（0 或非 0 都行；0 说明强模型守规矩）
- ✅ MySQL `explain_evidence_tree.tree_json` 包含 `narrative_claims` 和 `unverified_drops`
- ✅ 报告**可读、连贯，比 Phase 2.A 更扎实**

**Step 3: 验证 MySQL 落库**

```bash
uv run python -c "
import json
from sqlalchemy import text
from explain_agent.db.mysql import get_engine
with get_engine('explain').connect() as c:
    row = c.execute(text(
        'SELECT s.session_id, t.confidence, LEFT(t.tree_json, 2000) '
        'FROM explain_session s JOIN explain_evidence_tree t USING (session_id) '
        'ORDER BY s.created_at DESC LIMIT 1'
    )).fetchone()
    print('session:', row[0])
    print('confidence:', row[1])
    # 检查 tree_json 中是否包含新字段
    tree_text = row[2]
    print('has narrative_claims:', 'narrative_claims' in tree_text or 'claims' in tree_text)
"
```

Expected: 打印 session_id、confidence、`has narrative_claims: True`。

**Step 4: 更新 README**

在 README 末尾追加 Phase 2.B 验收清单：

```markdown

## Phase 2.B 验收状态

- [x] 数据兜底：parse "今天" → 5 天窗口；DimensionWorker 单日空时扩到 7 天；akshare 重试
- [x] State schema 扩展：NarrativeClaim、narrative_claims、unverified_drops
- [x] 强模型叙事：JSON 输出 + 每 claim 挂 evidence_ids
- [x] 数值校验：narrative 中数字必须能在证据中精确匹配，否则整句删除
- [x] 维度报告重写：6 维 strong model 重写 + [e_xxx] 引用
- [x] confidence 重打分：cited_count × source_type 多样性
- [x] 全部单测通过（含 Phase 2.A 回归）
- [x] 端到端 smoke：no_data 维度 ≤ 1/6
```

**Step 5: Commit**

```bash
git add scripts/run_main_graph_smoke.py README.md
git commit -m "$(cat <<'EOF'
Phase 2.B 端到端 smoke 验收 + 输出 claims/drops + README 清单

smoke 脚本额外打印 narrative_claims 与 unverified_drops，
便于人工验收"每句话挂证据 + 幻觉数字被校验删除"的效果。
EOF
)"
```

---

## 完成后

Phase 2.B 完成后的产出：

- ✅ 三处数据兜底（parse prompt + worker 扩窗 + akshare 重试），no_data 比例下降
- ✅ State schema：NarrativeClaim、narrative_claims、unverified_drops
- ✅ report_builder：JSON 输出 narrative、数值校验、维度报告重写、confidence 重打分
- ✅ 单测覆盖每个改动 + main_graph 集成测试更新
- ✅ 端到端 smoke 验收通过

**下一步：进入 Phase 2.C 实施计划**，重点是：

- CLI REPL（带状态的对话式交互）
- Followup Graph（基于已有 session 的追问）
- Lazy News Ingest（用户问到的板块/主题如果新闻语料不足，按需 ingest）
- 永久快照（snapshot_id 关联，对 citations.url 加 snapshot 兜底）

待 Phase 2.B 跑通后，再做 Phase 2.C 的 brainstorm 细化（看 2.B 实测发现的问题）。
