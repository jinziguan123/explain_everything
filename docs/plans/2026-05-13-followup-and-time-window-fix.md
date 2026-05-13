# Followup 放宽 + 时段语义修复 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修两个 REPL 实际使用暴露的小问题：(1) followup 不再因"超主题"误拒金融相关追问；(2) parse 加 `intent_qualifier` 让 narrative + dim_report 自约束引用证据时段，"今天上午"不再带 5 天前事件混进叙事。

**Architecture:** 改 4 个文件、加 6 个单测。`state.py` 先加 `intent_qualifier` 字段；`parse_question.py` 解析输出；`report_builder.py` 写到 narrative + dim_report prompt；`followup.py` 改 system prompt 放宽。零 schema 改动、零 graph 拓扑改动、零 adapter 改动。

**Tech Stack:** 沿用 Phase 2.A-2.D-3 已有的 langgraph / pydantic / pytest-asyncio。

**前置依赖：**
- Phase 2.D-3 完工（commit `7235410`），143 单测通过
- 设计文档（commit `66899e2`，`docs/plans/2026-05-13-followup-and-time-window-fix-design.md`）

**不做的事：**
- ❌ 改 schema 用 datetime 元组（推到独立 sprint 决定）
- ❌ 改 dimension_worker 的 7 天 fallback（保留兜底）
- ❌ followup 加 web_search 拉实时数据（独立 feature）
- ❌ Q1 / Q4（market_overview framework + 热点新闻主动获取，推到 Phase 2.D-4）

---

## 任务总览

| # | 任务 | 预估 |
|---|---|---|
| 1 | state.py 加 intent_qualifier 字段 + 1 单测 | 10 min |
| 2 | parse_question.py 加 intent_qualifier 输出 + 3 单测 | 30 min |
| 3 | report_builder.py 加时段约束（NARRATIVE + DIM_REPORT prompt）+ 1 单测 | 30 min |
| 4 | followup.py prompt 放宽 + 1 单测 | 20 min |
| 5 | 全量回归 + 端到端 REPL smoke + push | 30 min |

**合计：约 2 小时。**

---

## Task 1: state.py 加 intent_qualifier 字段

**目标：** AttributionState 加 `intent_qualifier` 可选字段。

**Files:**
- Modify: `src/explain_agent/graph/state.py`
- Modify: `tests/test_graph_state.py`

**Step 1: 改 state.py**

打开 `src/explain_agent/graph/state.py`，在 `AttributionState` class 中找到 `intent: Literal["up", "down", "volatile", "general"]` 那一行（应在 parse 块附近），紧随其后加：

```python
    intent_qualifier: str  # "上午" / "下午" / "今天" / "本周" / "近期" / "" — Phase 2.D-3 后置补丁
```

`new_attribution_state` 默认值字典**不需要**初始化（保持 optional）。

**Step 2: 写测试**

在 `tests/test_graph_state.py` 末尾追加：

```python
def test_attribution_state_supports_intent_qualifier():
    """intent_qualifier 是 AttributionState 合法字段（默认不在 dict 中）。"""
    from explain_agent.graph.state import AttributionState
    s: AttributionState = new_attribution_state(raw_question="test")
    assert "intent_qualifier" not in s
    s["intent_qualifier"] = "上午"
    assert s["intent_qualifier"] == "上午"
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_graph_state.py -v`
Expected: 10 passed（9 老的 + 1 新的）

**Step 4: 跑全量回归**

Run: `uv run pytest`
Expected: 144 passed

**Step 5: Commit**

```bash
git add src/explain_agent/graph/state.py tests/test_graph_state.py
git commit -m "$(cat <<'EOF'
State schema · 加 intent_qualifier 字段

为 narrative / dim_report prompt 注入用户时段意图做准备。
"上午" / "下午" / "今天" / "本周" / "近期" / "" 五种值。
optional 字段, 旧 session 没有时降级 "近期"。
EOF
)"
```

---

## Task 2: parse_question.py 加 intent_qualifier 输出

**目标：** parse_question_node 解析输出 intent_qualifier，写入 state。

**Files:**
- Modify: `src/explain_agent/graph/nodes/parse_question.py`
- Modify: `tests/test_node_parse_question.py`

**Step 1: 改 parse_question.py 的 SYSTEM prompt**

打开 `src/explain_agent/graph/nodes/parse_question.py`，把 `SYSTEM` 字符串替换为：

```python
SYSTEM = """你是金融归因 agent 的问题解析器。读完用户输入，输出 JSON：
{
  "target": "标的（板块/行业/主题名）",
  "time_window_start": "YYYY-MM-DD",
  "time_window_end": "YYYY-MM-DD",
  "intent": "up|down|volatile|general",
  "intent_qualifier": "上午|下午|今天|本周|近期|"
}

规则：
- target 提取板块/主题词（如"半导体"、"光伏"、"白酒"），无明确则用整句话
- 时间窗：默认 end=今天，start=今天-7天；若用户提"上周"则推 7-14 天前
- "今天"语义：end=今天，start=今天-5天（覆盖最近 5 个自然日，包含上一个交易日），避免单日窗口导致行情库查空
- 若用户给出明确日期则严格按用户给定
- intent：涨/涨停=up，跌/大跌=down，波动/异动=volatile，其它=general
- intent_qualifier：用户提"今天上午"→"上午", "今天下午"→"下午", "今天/今日"→"今天",
  "本周/这周"→"本周", "最近/近期/没说时间"→"近期", 其他无明确时段意图→""
只输出 JSON。
"""
```

**Step 2: 改 parse_question_node 把 intent_qualifier 写入返回**

把 `parse_question_node` 函数的 return 改为：

```python
    return {
        "target": data.get("target", state["raw_question"][:50]),
        "time_window": (start, end),
        "intent": data.get("intent", "general"),
        "intent_qualifier": data.get("intent_qualifier", "近期") or "近期",
    }
```

并把 fallback path（data is None 那一支）的 return 也加上：

```python
    if data is None:
        return {
            "target": state["raw_question"][:50],
            "time_window": (today - timedelta(days=7), today),
            "intent": "general",
            "intent_qualifier": "近期",
        }
```

**Step 3: 写 3 个新测试**

在 `tests/test_node_parse_question.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_parse_today_morning_qualifier():
    """用户问"今天上午"应解析出 intent_qualifier=上午。"""
    from datetime import date
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "target": "上午行情",
        "time_window_start": "2026-05-13",
        "time_window_end": "2026-05-13",
        "intent": "general",
        "intent_qualifier": "上午",
    }))
    state = new_attribution_state("总结一下今天上午的行情")
    state["asked_at"] = datetime(2026, 5, 13, 10, 0, 0)
    out = await parse_question_node(state, llm=fake_llm)
    assert out["intent_qualifier"] == "上午"
    assert out["time_window"] == (date(2026, 5, 13), date(2026, 5, 13))


@pytest.mark.asyncio
async def test_parse_recent_qualifier_default():
    """用户没说时间, intent_qualifier 默认 '近期'。"""
    from datetime import date
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "target": "半导体",
        "time_window_start": "2026-05-06",
        "time_window_end": "2026-05-13",
        "intent": "up",
        "intent_qualifier": "近期",
    }))
    state = new_attribution_state("半导体板块为什么涨")
    state["asked_at"] = datetime(2026, 5, 13, 10, 0, 0)
    out = await parse_question_node(state, llm=fake_llm)
    assert out["intent_qualifier"] == "近期"


@pytest.mark.asyncio
async def test_parse_qualifier_falls_back_to_recent_when_missing():
    """LLM 没输出 intent_qualifier 字段时, fallback 到 '近期'。"""
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "target": "半导体",
        "time_window_start": "2026-05-06",
        "time_window_end": "2026-05-13",
        "intent": "up",
    }))
    state = new_attribution_state("半导体涨")
    state["asked_at"] = datetime(2026, 5, 13, 10, 0, 0)
    out = await parse_question_node(state, llm=fake_llm)
    assert out["intent_qualifier"] == "近期"
```

注意：测试文件顶部应已有 `from datetime import datetime`、`from unittest.mock import AsyncMock, MagicMock`、`import json`、`from explain_agent.graph.state import new_attribution_state`、`from explain_agent.graph.nodes.parse_question import parse_question_node`、`import pytest`。如果缺失任一 import 就补上。

**Step 4: 跑测试**

Run: `uv run pytest tests/test_node_parse_question.py -v`
Expected: 6 passed（3 老的 + 3 新的）

**Step 5: 跑全量回归**

Run: `uv run pytest`
Expected: 147 passed

**Step 6: Commit**

```bash
git add src/explain_agent/graph/nodes/parse_question.py tests/test_node_parse_question.py
git commit -m "$(cat <<'EOF'
parse_question · 加 intent_qualifier 解析

SYSTEM prompt 输出新增 intent_qualifier 字段, 区分"上午/下午/
今天/本周/近期"五种语义。parse_question_node return 写到 state,
LLM 没输出该字段时 fallback "近期"。time_window 不变（仍按
"今天"=5 天兜底, dimension_worker 7 天 fallback 不变）。
EOF
)"
```

---

## Task 3: report_builder.py 加时段约束

**目标：** NARRATIVE_SYSTEM + DIM_REPORT_SYSTEM 末尾加时段约束规则；user prompt 注入 `用户时段意图:` 行。

**Files:**
- Modify: `src/explain_agent/graph/nodes/report_builder.py`
- Modify: `tests/test_node_report_builder.py`

**Step 1: 改 NARRATIVE_SYSTEM prompt**

打开 `src/explain_agent/graph/nodes/report_builder.py`，找到 `NARRATIVE_SYSTEM = """..."""` 字符串定义。在 `只输出 JSON。` 之前（即所有规则的最后一条之后）追加：

```
- 时段约束：用户问的是 "{intent_qualifier}"（如"上午"/"今天"等）。仅引用证据
  时间戳与该时段匹配的 evidence。证据池中可能包含其他时段的内容（因检索时间窗
  扩展所致），这些只能作为背景知识理解趋势，不能写进 narrative 当作"该时段事件"。
  若严格匹配该时段的 evidence 不足以构成完整叙事，narrative 中明确说明"该时段
  的可用证据有限"。
```

注意：`{intent_qualifier}` 是字面量字符串（在 user prompt 里实际填值），prompt 里只是提示 LLM 这个变量名。**不要**用 Python f-string 替换。

**Step 2: 改 DIM_REPORT_SYSTEM prompt**

类似地，在 `DIM_REPORT_SYSTEM` 字符串末尾（在 `直接输出维度报告文本(...)。` 之前）追加同样的时段约束规则（复制粘贴上一条）。

**Step 3: 改 report_builder_node 把 intent_qualifier 注入 narrative user**

找到 `report_builder_node` 函数中拼 narrative `user` 的代码：

```python
    user = (
        f"标的: {state['target']}\n"
        f"时间窗: {state['time_window'][0]} ~ {state['time_window'][1]}\n"
        f"市场锚点: {state['market_facts'].get('snippet', '')}\n"
        f"证据池:\n{json.dumps(evidence_dump, ensure_ascii=False)}"
    )
```

改为：

```python
    user = (
        f"标的: {state['target']}\n"
        f"时间窗: {state['time_window'][0]} ~ {state['time_window'][1]}\n"
        f"用户时段意图: {state.get('intent_qualifier') or '近期'}\n"
        f"市场锚点: {state['market_facts'].get('snippet', '')}\n"
        f"证据池:\n{json.dumps(evidence_dump, ensure_ascii=False)}"
    )
```

**Step 4: 改 _rewrite_dim_report 把 intent_qualifier 注入 user**

找到 `_rewrite_dim_report` 函数。当前签名：

```python
async def _rewrite_dim_report(
    dim_id: str,
    dim_result,
    target: str,
    market_facts: dict,
    llm: LLMClient,
) -> str:
```

加一个 `intent_qualifier: str = "近期"` 参数：

```python
async def _rewrite_dim_report(
    dim_id: str,
    dim_result,
    target: str,
    market_facts: dict,
    llm: LLMClient,
    intent_qualifier: str = "近期",
) -> str:
```

函数体里 user 拼接处加 `用户时段意图:` 行：

```python
    user = (
        f"维度: {dim_id}\n"
        f"标的: {target}\n"
        f"用户时段意图: {intent_qualifier}\n"
        f"市场锚点: {market_facts.get('snippet', '')}\n"
        f"该维度证据池:\n{json.dumps(evidence_dump, ensure_ascii=False)}"
    )
```

回到 `report_builder_node`，找到调用 `_rewrite_dim_report` 的地方（Phase 2.D-2 后是 `asyncio.create_task` 内）：

```python
    dim_tasks = [
        asyncio.create_task(_rewrite_dim_report(
            dim_id, dim_results[dim_id], state["target"], state["market_facts"], llm
        ))
        for dim_id in dim_ids
    ]
```

改为传入 `intent_qualifier`：

```python
    intent_qualifier = state.get("intent_qualifier") or "近期"
    dim_tasks = [
        asyncio.create_task(_rewrite_dim_report(
            dim_id, dim_results[dim_id], state["target"], state["market_facts"], llm,
            intent_qualifier=intent_qualifier,
        ))
        for dim_id in dim_ids
    ]
```

**Step 5: 写 1 个测试**

在 `tests/test_node_report_builder.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_narrative_user_prompt_contains_intent_qualifier():
    """report_builder 的 narrative + dim_report user prompt 都含'用户时段意图:'行。"""
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(side_effect=[
        json.dumps({"claims": [{"text": "测试", "evidence_ids": ["e1"]}]}),
        "policy 重写报告",
    ])
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 13), date(2026, 5, 13))
    state["intent_qualifier"] = "上午"
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1")], mini_summary="",
            retry_count=1, no_data=False, confidence="high",
        ),
    }

    await report_builder_node(state, llm=fake_llm)

    # 验证两个 LLM 调用的 user prompt 都含"用户时段意图: 上午"
    user_args = [c.kwargs.get("user", "") for c in fake_llm.achat.call_args_list]
    assert len(user_args) == 2
    for u in user_args:
        assert "用户时段意图: 上午" in u
```

**Step 6: 跑测试**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: 16 passed（15 老的 + 1 新的）

**Step 7: 跑全量回归**

Run: `uv run pytest`
Expected: 148 passed

**Step 8: Commit**

```bash
git add src/explain_agent/graph/nodes/report_builder.py tests/test_node_report_builder.py
git commit -m "$(cat <<'EOF'
report_builder · 加时段约束 + intent_qualifier 注入

NARRATIVE_SYSTEM + DIM_REPORT_SYSTEM 末尾加规则: 仅引用证据时间
戳与"用户时段意图"匹配的 evidence, 其他时段证据只作背景。
report_builder_node 把 state.intent_qualifier 注入 narrative
+ 6 维 dim_report 的 user prompt。这样 LLM 自约束: "今天上午"
不再混入 5 天前事件。
EOF
)"
```

---

## Task 4: followup.py prompt 放宽

**目标：** 把"超主题就拒"改成"先尝试推测 + 标注证据局限"。

**Files:**
- Modify: `src/explain_agent/graph/followup.py`
- Modify: `tests/test_followup.py`

**Step 1: 改 FOLLOWUP_SYSTEM prompt**

打开 `src/explain_agent/graph/followup.py`，把 `FOLLOWUP_SYSTEM` 字符串替换为：

```python
FOLLOWUP_SYSTEM = """你是金融归因 agent 的追问回答器。基于已生成的 6 维归因报告
和历史追问，针对用户的新问题给出 150-400 字的回答。

要求:
- 优先基于已有 dimension_reports + citations 中的 evidence 回答, 不要编造新数据。
- 用 [e_xxx] 格式标注引用的 evidence_id。
- 如果用户追问的事件（如"X 政策"、"Y 访华"、"Z 突发新闻"）在已有证据中没有
  直接覆盖, 先尝试基于现有 evidence 推测其可能影响（明确标注"基于现有 6 维证据
  推测"）, 再说明"若需该事件的实时数据/详细分析, 建议用 /new 开新会话"。
- 仅当问题完全跨标的（如用户已问半导体股票却来问"我的健康问题"等非金融场景）
  才直接拒绝并提示 /new。
- 不预测/不推荐操作。

直接输出回答文本。
"""
```

**Step 2: 写 1 个测试**

在 `tests/test_followup.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_followup_attempts_reasoning_for_cross_event_question():
    """跨事件追问（如特朗普访华）应基于现有 evidence 推测, 不直接拒绝。"""
    fake_llm = MagicMock()
    # 模拟强模型按新 prompt 行为：先推测, 末尾建议 /new
    fake_llm.achat = AsyncMock(return_value=(
        "基于现有 international 维度的 [e_xxx] 证据推测, 该事件可能加剧中美科技战, "
        "对半导体板块情绪有阶段性扰动。若需该事件的实时数据/详细分析, "
        "建议用 /new 开新会话。"
    ))

    session = {
        "session_id": "s_test",
        "target": "半导体",
        "narrative": "...",
        "dimension_reports": {"international": "中美科技战 ..."},
        "citations": [{"evidence_id": "e1", "source_type": "news", "snippet": "..."}],
        "market_facts": {"snippet": ""},
        "time_window": "2026-05-08 to 2026-05-13",
    }
    out = await run_followup(
        session=session, history=[], question="特朗普今天访华影响",
        llm=fake_llm, engine=MagicMock(),
    )
    assert "推测" in out["answer"] or "/new" in out["answer"]
    # 严格断言: 不应包含旧 prompt 的"超出当前会话的 X 主题"措辞
    assert "超出当前会话" not in out["answer"]


def test_followup_system_prompt_encourages_evidence_reasoning():
    """FOLLOWUP_SYSTEM prompt 应明确包含"先尝试基于现有 evidence 推测"。"""
    from explain_agent.graph.followup import FOLLOWUP_SYSTEM
    assert "推测" in FOLLOWUP_SYSTEM
    assert "/new" in FOLLOWUP_SYSTEM
    # 不应再有旧的"超出 target 范围就拒"措辞
    assert "完全跳出当前 target 的范围" not in FOLLOWUP_SYSTEM
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_followup.py -v`
Expected: 5 passed（3 老的 + 2 新的；注意 plan 设计里写的是 +1, 但实际拆成 2 个更清晰: 行为单测 + prompt 内容单测）

**Step 4: 跑全量回归**

Run: `uv run pytest`
Expected: 150 passed（148 + 2）

**Step 5: Commit**

```bash
git add src/explain_agent/graph/followup.py tests/test_followup.py
git commit -m "$(cat <<'EOF'
followup · prompt 放宽, 不再因"超主题"误拒金融追问

旧 prompt: "超出 target 范围就拒 + 建议 /new", 导致用户问完
今天上午行情后追问"特朗普访华"被直接拒绝, 体验差。
新 prompt: "先尝试基于现有 6 维 evidence 推测可能影响 + 标注
信息局限 + 建议 /new"。仅完全跨标的的非金融问题（健康问题等）
才拒。
EOF
)"
```

---

## Task 5: 全量回归 + 端到端 REPL smoke + push

**目标：** 跑全量单测 + REPL 真实问 2 个问题验证行为 + push origin。

**Files:**
- 跑 pytest + REPL，无代码改动
- 最后 push

**Step 1: 全量回归**

Run: `uv run pytest`
Expected: 150 passed

如果失败，停下报告（不要瞎修）。

**Step 2: REPL smoke 验证（手动）**

启动 REPL：
```bash
uv run explain
```

依次输入：

**Q1**：`总结一下今天上午的行情`
- 等 5-7 分钟出报告
- **判定标准**：
  - log 里 `parse` 节点输出含 `intent_qualifier=上午`（或类似日志，可由 on_node_event 不直接显示）
  - **narrative 段落不出现** "5月11日" / "5月12日" 等非今日（2026-05-13）作为"上午"的事件描述
  - 如确实今日上午 evidence 不足，narrative 应明确说"该时段可用证据有限"

如果 narrative 仍然把 5.11 涨停写成"今天上午"事件，停下 — 说明 LLM 没遵守新 prompt，需要进一步调 prompt。

**Q2**：在同一会话中追问 `特朗普今天访华，可能对A股市场带来什么影响`
- **判定标准**：
  - 回答**不以**"此问题已超出当前会话的 X 主题"开头
  - 回答**包含**"基于现有 evidence 推测" / "推测" 等措辞
  - 回答末尾建议 `/new` 开新会话拿实时数据
  - 引用至少 1 条 international 维度的 [e_xxx]

如果回答仍然是直接拒绝，停下 — 说明 LLM 没遵守新 prompt。

**Step 3: README 更新**

在 README 末尾追加（在 Phase 2.D-3 验收清单之后）：

```markdown

## Phase 2.D-3 后续小补丁（2026-05-13）

REPL 实际使用暴露的两个问题修复：
- [x] followup prompt 放宽：金融相关追问不再因"超主题"被直接拒
- [x] parse 加 intent_qualifier（上午/下午/今天/本周/近期），narrative + dim_report
  prompt 自约束引用 evidence 时段，"今天上午"不再带 5 天前事件
- [x] 全部 150 单测通过
- [x] REPL 验收：跑 "总结今天上午行情" + 追问 "特朗普访华影响" 均符合预期
```

**Step 4: Commit + push**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
README · followup + 时段语义补丁完工

REPL 实际使用暴露的两个体验问题已修复, 单测 143 → 150,
2 小时内完成。Q1 / Q4 (technical no_data + 热点新闻主动获取)
推到 Phase 2.D-4 "全市场综述形态" brainstorm。
EOF
)"

git push origin master
```

**Step 5: 完工汇报**

控制台输出本期 commits + 单测数 + REPL 验收点。

---

## 完成后

- ✅ followup 不再误拒金融相关追问；基于现有 evidence 推测 + 提示 /new
- ✅ "今天上午" / "今天" / "本周" 等时段意图通过 intent_qualifier 传给 narrative + dim_report
- ✅ LLM 自约束引用证据时段，narrative 不再混入非该时段事件
- ✅ 150 单测通过
- ✅ 真实 REPL 体验改善

**仍未解决（推到 Phase 2.D-4 brainstorm）**：
- Q1：technical / capital_flow 对宽泛 target 无结果
- Q4：热点新闻主动获取
- 这两件事建议合并 brainstorm "全市场综述形态"再决定（是否新增 `market_overview` framework + `hot_news` 节点）
