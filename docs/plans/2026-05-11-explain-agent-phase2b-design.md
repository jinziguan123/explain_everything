# Explain Agent · Phase 2.B 设计文档

> 强模型叙事 + 维度报告重写 + 数值校验 + confidence 重打分，前置 Phase 2.A 实测发现的数据兜底。

**前置：** Phase 2.A 已完工（commit `e0655df`），main graph 端到端跑通。

**Phase 2.A 实测发现的问题：**

1. `parse_question` 把"今天涨"解析成单日窗口，ClickHouse 行情库滞后 → `technical` 维度 no_data
2. `akshare_capital_flow` 偶发 `ConnectionError`，被 worker 的 `except Exception` 静默吞掉 → `capital_flow` 维度 no_data
3. `report_builder` 的 narrative 由强模型直接看 mini_summary 拼接，**没有 evidence_id 引用**，存在数字/事实不可追溯的幻觉风险（如"长电科技涨停"这种二级描述）
4. 维度报告就是 mini_summary 弱模型直出，质量不统一
5. `confidence` 打分按"高 confidence 维度数量"计算，与实际证据数量/多样性脱节

---

## 整体目标

把 main graph 的输出从"能跑通"升级到"敢给人看"：

- 6 维数据完整度 ≥ 5/6（不再因为单日窗口或单次接口失败导致整维 no_data）
- 顶部 narrative 的每个 claim 都能挂上 evidence_id，可追溯
- 维度报告由强模型统一重写，质量一致
- narrative 中的数字必须能在证据中找到精确匹配，否则整句删除
- confidence 反映真实证据基础（数量 + 来源多样性）

**不做：** 实体校验、双重 LLM 校验、永久快照、followup graph、eval framework。这些放 Phase 2.C/2.D。

---

## 任务范围（5 个并列改动）

### 任务 0 · 数据兜底（Phase 2.B-pre）

**目的：** 把 2.A 实测的 no_data 比例从 2/6 降到 ≤1/6。

- **parse_question prompt 调整：** "今天" 的语义改为"最近 5 个交易日"（保持 end=今天，start=今天-5 天），消除单日窗口
- **DimensionWorker 扩窗兜底：** 在 `_fetch_all_sources` 加一层 fallback —— 若所有 adapter 都返回空且 time_window ≤ 3 天，自动把窗口扩到 7 天再试一次
- **akshare 重试：** `AkshareCapitalFlowAdapter.query` 用 `tenacity.retry`（2 次，指数退避）包装 `ak.stock_sector_fund_flow_hist` 调用

**单测：** parse prompt 变更通过 mock 验证；worker 扩窗用 mock adapter（先返回空再返回非空）验证；akshare 重试通过 mock `ak` 函数验证。

### 任务 1 · 强模型叙事重写（带引用）

**目的：** narrative 从"自由文本"升级为"结构化 + evidence_id 引用"，可追溯。

**state.py 新增：**

```python
class NarrativeClaim(TypedDict):
    text: str
    evidence_ids: list[str]

class AttributionState(TypedDict, total=False):
    # ...原有字段
    narrative_claims: list[NarrativeClaim]
    unverified_drops: list[str]  # 被数值校验删除的句子,审计用
```

**report_builder.py：** narrative 调用改用 JSON 模式

```
输入: target / time_window / market_facts / 6 维 evidence 池（含 evidence_id）
强模型输出 JSON: {
  "claims": [
    {"text": "...", "evidence_ids": ["e_001", "e_007"]},
    ...
  ]
}
拼装: narrative = " ".join(c.text for c in claims); narrative_claims = claims
```

**Prompt 关键约束：**
- 每个 claim 必须挂 ≥1 个 evidence_id
- evidence_id 必须来自给定的证据池
- 不出现"涨停"等需要数据支撑但不在证据里的具体描述
- 总长度 80-150 字

**单测：** mock strong_llm 返回 JSON，验证 narrative_claims 长度、每个 claim 都有 evidence_ids。

### 任务 2 · 维度报告重写

**目的：** 6 维报告由强模型统一重写，替代弱模型 mini_summary 直出。

**report_builder.py 新增 `_rewrite_dim_report(dim_id, dim_result)`：**
- 输入：维度的 mini_summary（作为参考）+ 该维 evidence 池
- 强模型输出：200-400 字维度报告，**强制以"[e_xxx]"格式引用 evidence_id**
- `no_data` 维度跳过重写，保持"本维度未检索到相关证据"原文

**成本：** 6 维 × 1 次强模型调用 = 多 6 次 API。`no_data` 维度跳过，实际通常 4-6 次。

**单测：** mock strong_llm，验证 `no_data` 维度不调 LLM，其他维度调用且返回包含 `[e_` 引用标记。

### 任务 3 · 数值校验

**目的：** narrative 里的数字必须能在 evidence 中找到精确匹配，否则整句删除。

**report_builder.py 新增 `_strip_unverified_numbers(claims, evidence_pool)`：**

算法：
1. 对每个 claim.text 提取所有"看起来像数字"的 token（正则 `\d+\.?\d*%?`、`\d+亿`、`\d+万`、`\d+次`等）
2. 对每个数字 token，在 claim 引用的 evidence_ids 对应的 evidence.snippet + raw_payload 字符串化结果中做精确匹配
3. 找不到匹配的数字 → 整个 claim 删除
4. 删除的 claim 文本追加到 `unverified_drops`

**关键设计选择：**
- **不做"修改/重写"，只做"删除"**：模型重写又会引入新幻觉
- **不校验百分号、单位换算**：如果数字字面值不在证据里，整句删
- **校验粒度是 claim 整句，不是数字单点**：避免出现"半导体板块涨 -%（删除后）"这种破碎句

**单测：** 给定 claim "板块涨 5%"（引用 evidence 包含 "5%"）→ 保留；claim "板块涨停"（引用 evidence 不含"涨停"二字）→ 不影响（无数字）；claim "板块涨 10%"（引用 evidence 只含"5%"）→ 删除并写 unverified_drops。

### 任务 4 · confidence 重打分

**目的：** 从"高 confidence 维度数量"改为反映真实证据基础。

**新打分逻辑：**

```python
def _estimate_overall_confidence(dim_results, narrative_claims) -> str:
    cited_evidence_ids = {eid for c in narrative_claims for eid in c.evidence_ids}
    cited_count = len(cited_evidence_ids)

    source_types = set()
    for r in dim_results.values():
        for e in r["evidence"]:
            if e.id in cited_evidence_ids:
                source_types.add(e.source_type)

    if cited_count >= 8 and len(source_types) >= 3:
        return "high"
    if cited_count >= 4 and len(source_types) >= 2:
        return "medium"
    return "low"
```

**说明：**
- 只统计**被 narrative 实际引用的** evidence（而非维度池里的全部）
- `source_type` 多样性：news / market_data / capital_flow / policy 等，避免单一来源刷数量
- 阈值（8/3, 4/2）从 Phase 2.A smoke 实测的 34 citations 反推，留出 25% 缓冲

**单测：** 各阈值边界用 mock data 直接验证。

---

## 数据流变化对比

**Phase 2.A：**

```
6 维 mini_summary → strong narrative (自由文本,无引用)
                  → dim_reports = {dim_id: mini_summary}
                  → confidence (按高 confidence 维度数)
```

**Phase 2.B：**

```
6 维 mini_summary + 全量 evidence 池
  → strong narrative (JSON, 每 claim 挂 evidence_ids)
  → 数值校验 (删幻觉句, 记 unverified_drops)
  → 6 维 strong 重写 (no_data 跳过, 其他强制 [e_xxx] 引用)
  → 新 confidence (cited_count × source_type 多样性)
```

---

## State Schema 变更（兼容性）

| 字段 | Phase 2.A | Phase 2.B |
|---|---|---|
| `narrative` | str | str（保留，由 claims 拼接） |
| `narrative_claims` | — | list[NarrativeClaim] 新增 |
| `unverified_drops` | — | list[str] 新增 |
| `dimension_reports` | str（mini_summary） | str（强模型重写） |
| `confidence` | str | str（重新打分） |

**Persist 节点：** `tree_json` 自然包含新字段，无需 schema 迁移。

---

## 测试策略

### 单测
- **每个改动文件配套单测**（4 个新增 / 修改的测试文件）
- 全部用 mock LLM，覆盖正常路径 + 边界（数字校验删除句、no_data 跳过、JSON 解析失败兜底）

### 集成测试
- `test_main_graph.py` 更新 `fake_strong_llm.side_effect` 序列：synthesizer JSON → narrative JSON → 6 维重写 × 6 = **8 次强模型调用**
- 验证 `narrative_claims` 非空、`dimension_reports` 含 `[e_` 标记、`confidence` 计算正确

### 端到端 smoke
- 复用 `scripts/run_main_graph_smoke.py`，跑同一个问题"为什么半导体板块今天涨"
- **验收标准：**
  - no_data 维度 ≤ 1/6
  - narrative 每句话都有 ≥1 个 evidence_id
  - 维度报告中可见 `[e_xxx]` 引用标记
  - `unverified_drops` 至少触发过一次（说明校验有动作；若 0 次说明强模型已经很守规矩）
  - MySQL `explain_evidence_tree.tree_json` 包含 `narrative_claims` 和 `unverified_drops`

---

## 失败模式与回退

- **强模型 JSON 输出格式错乱：** `_extract_json` 解析失败 → 回退到 Phase 2.A 的"自由文本 narrative + 无 claims"，记 errors
- **数值校验过激（误删过多句）：** 单测覆盖；端到端 smoke 若发现 narrative 被删空，回退原始 narrative
- **维度重写超时/异常：** 单维度异常 fallback 到原 mini_summary，不阻塞整个 report_builder

---

## 任务总览

| # | 任务 | 预计 |
|---|---|---|
| 0 | 数据兜底（parse prompt + worker 扩窗 + akshare 重试） | 30 min |
| 1 | 强模型叙事重写（含 state schema 扩展） | 1.5 h |
| 2 | 维度报告重写 | 1.5 h |
| 3 | 数值校验 | 1 h |
| 4 | confidence 重打分 | 30 min |
| 5 | 集成测试 + 端到端 smoke 验收 | 1 h |

**合计：约 6 h 纯开发，预计 1-2 个工作日完成。**

---

## 下一步

由 `superpowers:writing-plans` 把本设计文档拆成 task-by-task 的实施计划，落到
`docs/plans/2026-05-11-explain-agent-phase2b.md`。
