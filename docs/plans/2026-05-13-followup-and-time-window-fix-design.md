# Followup 放宽 + 时段语义修复 设计文档

> 用户实际使用 REPL 时暴露的两个真实痛点：(1) 追问"特朗普访华"被强模型按"超主题"直接拒绝；(2) 问"今天上午"得到 5 天前的事件混入 narrative。本文档定位"轻量打补丁"，不做产品形态级重构。

**前置：** Phase 2.D-3 完工（commit `7235410`），143 单测通过。

**修复 = 两个互不依赖的小改动：**
- **F1：followup prompt 放宽**（删"超主题就拒"，改成"先尝试推测 + 标注证据局限"）
- **F2：parse 加 intent_qualifier + narrative 时段约束**（让 LLM 知道用户问的是"今天上午" / "近期"，不再把 7 天前事件写进"今天上午"叙事）

---

## 背景：4 个用户问题指向同一深层问题

用户跑 REPL 问 "总结一下今天上午的行情" + 追问 "特朗普访华影响"，暴露 4 个问题：

| # | 问题 | 根因 |
|---|---|---|
| Q1 | technical / capital_flow no_data | 这两 adapter 强依赖 target 是有效行业名（IndustryResolver 在 sw_l1/l2/l3 字典找）。target = "上午行情" 这种宽泛词查不到 → 返回空 |
| Q2 | "今天上午"带出 5.11 数据 | parse 兜底规则 "今天" → 5 天窗口（Phase 2.B 防空数据加的），narrative 不知道用户原意是"今天上午" |
| Q3 | "特朗普访华"追问被拒 | followup prompt 太严格："超出 target 主题就拒"被严格执行 |
| Q4 | 不支持热点新闻主动获取 | 当前所有 ingest 都是被动 + lazy，没有主动热点发现 |

**Q1 + Q4 是产品形态级问题**（当前 framework 是"问 X 板块为什么涨"型，用户实际想要"今天市场综述"型），需要单独 brainstorm 决定是否新做 `market_overview` framework。

**Q2 + Q3 是 prompt / state 级别的小问题**，本期一并修复。

---

## F1：followup prompt 放宽

### 当前问题

[`followup.py:14-18`](../../src/explain_agent/graph/followup.py:14)：

```
- 如果问题完全跳出当前 target 的范围（如用户已问半导体却来问光伏），
  在回答开头明确提示："此问题已超出当前会话的 {target} 主题, 建议 /new 开新会话"
```

强模型严格执行，"特朗普访华"被判定 ⊄ "今天上午行情"主题 → 直接拒绝。但地缘事件对全市场有影响，应该能基于现有 6 维 evidence 推测。

### 修改

把 prompt 改为：

```
- 优先基于已有 dimension_reports + citations 中的 evidence 回答。
- 如果用户追问的事件（如"X 政策"、"Y 访华"）在已有证据中没有直接覆盖,
  先尝试基于现有 evidence 推测其可能影响（明确标注"基于现有 6 维证据推测"）,
  再说明"若需该事件的实时数据/详细分析, 建议用 /new 开新会话"。
- 仅当问题完全跨标的（如用户已问半导体股票却来问"我的健康问题"）才直接拒绝。
```

### 行为变化

- 用户追问 "特朗普访华影响" → 强模型基于 international 维度 evidence 推测可能影响 + 提示需 /new 拿实时数据
- 用户追问 "我的健康问题" → 仍然拒绝（避开非金融场景）
- 用户追问 "光伏板块" → 不拒绝，尝试基于现有评论推测，并提示 /new 做完整归因

---

## F2：parse 加 intent_qualifier + narrative 时段约束

### 设计哲学

`time_window` 类型是 `tuple[date, date]`，不能精确到时分秒。改 schema 用 datetime 是大改动（5+ adapter / SQL / 测试），ROI 差。

最小可行方案：**parse 解析出"用户的时段意图"作为 narrative_hint，让 LLM 自己处理 evidence 时段 mismatch。**

### F2.a parse_question.py 改造

`SYSTEM` prompt 加 `intent_qualifier` 字段输出：

```
{
  "target": "...",
  "time_window_start": "YYYY-MM-DD",
  "time_window_end": "YYYY-MM-DD",
  "intent": "up|down|volatile|general",
  "intent_qualifier": "上午|下午|今天|本周|近期|null"
}
```

规则：
| 用户输入提到 | time_window | intent_qualifier |
|---|---|---|
| "今天上午" | (今日, 今日) | "上午" |
| "今天下午" | (今日, 今日) | "下午" |
| "今天" | (今日, 今日) | "今天" |
| "本周" | (本周一, 今日) | "本周" |
| "最近 / 近期 / 没说时间" | (今日-7, 今日) | "近期" |

注意：parse 设单日窗口时，[`dimension_worker.py:167`](../../src/explain_agent/graph/dimension_worker.py:167) 已有的 7 天 fallback 仍生效——避免单日空数据。但 narrative 通过 intent_qualifier 知道用户原意，自行约束引用。

### F2.b state.py 加字段

```python
class AttributionState(TypedDict, total=False):
    # ...已有
    intent_qualifier: str  # "上午" / "下午" / "今天" / "本周" / "近期" / ""
```

字段 optional，旧 session 没有此字段时降级为 "近期"。

### F2.c narrative + dim_report prompt 加约束

[`report_builder.py NARRATIVE_SYSTEM`](../../src/explain_agent/graph/nodes/report_builder.py:38) 末尾加一条规则：

```
- 时段约束：用户问的是 "{intent_qualifier}"（如"上午"/"今天"等）。仅引用证据
  时间戳与该时段匹配的 evidence。证据池中可能包含其他时段的内容（因检索时间窗
  扩展所致），这些只能作为背景知识理解趋势，不能写进 narrative 当作"该时段事件"。
  若严格匹配该时段的 evidence 不足以构成完整叙事，narrative 中明确说明"该时段
  的可用证据有限"。
```

`report_builder_node` 在拼 narrative user prompt 时加 `intent_qualifier` 行：

```python
user = (
    f"标的: {state['target']}\n"
    f"时间窗: {state['time_window'][0]} ~ {state['time_window'][1]}\n"
    f"用户时段意图: {state.get('intent_qualifier') or '近期'}\n"
    f"市场锚点: {state['market_facts'].get('snippet', '')}\n"
    f"证据池:\n{json.dumps(evidence_dump, ensure_ascii=False)}"
)
```

dim_reports 重写也同步加（DIM_REPORT_SYSTEM + `_rewrite_dim_report` 的 user）。

---

## 失败模式与回退

| 故障 | 回退 |
|---|---|
| parse 输出没有 intent_qualifier 字段（旧 prompt cache 等） | parse_question_node 默认填 "近期" |
| narrative 拿到 intent_qualifier 但忽略 | LLM 行为，不可强制；prompt 已尽力 |
| dim_reports 用旧 LLM 缓存 | 同上，prompt 已加约束 |
| followup 用户问真不相关问题 | 仍拒绝（健康问题等），prompt 保留兜底 |

---

## 测试策略

| 测试文件 | 改动 | 用例 |
|---|---|---|
| `tests/test_node_parse_question.py` | +3 | "今天上午"→qualifier="上午"+单日 / "近期"→qualifier="近期"+7天 / "本周"→qualifier="本周"+周一到今日 |
| `tests/test_followup.py` | +1 | followup prompt 中含"基于现有 evidence 推测"关键词；模拟跨主题追问 → 回答含"推测"而非"超出..." |
| `tests/test_node_report_builder.py` | +1 | 验证 narrative + dim_report user prompt 含"用户时段意图:" |
| `tests/test_graph_state.py` | +1 | AttributionState 支持 intent_qualifier 字段 |

新单测 +6，全量 143 → 149。

---

## 验收标准

### 单测

- 全部 149 passed
- 含 4 个新测试覆盖 F1 + F2 关键路径

### 端到端 REPL 验收

跑两个真实问题：

1. `总结一下今天上午的行情`
   - **预期**：narrative 不出现 "5.11" / "5.12" 等非今日事件作为"上午"叙事；如真有今日上午无足够 evidence，narrative 里说明"该时段可用证据有限"
   - **判定**：log 里 narrative 中"今天上午"段落只引用今日 evidence

2. 跑完上面后追问 `特朗普今天访华，可能对A股市场带来什么影响`
   - **预期**：得到基于已有 evidence 的推测（如"地缘事件可能加剧 international 维度提到的中美科技战压力，影响半导体板块"），并提示需 /new 拿实时数据
   - **判定**：回答中含"推测"或"基于现有 6 维证据"等关键词，不是直接 "超出主题..."

---

## 任务总览

| # | 任务 | 预估 |
|---|---|---|
| 1 | parse_question.py 加 intent_qualifier 输出 + 3 单测 | 30 min |
| 2 | state.py 加 intent_qualifier 字段 + 1 单测 | 10 min |
| 3 | followup.py prompt 改写 + 1 单测 | 20 min |
| 4 | report_builder.py NARRATIVE_SYSTEM + DIM_REPORT_SYSTEM 加时段约束 + user prompt 注入 + 1 单测 | 30 min |
| 5 | 端到端 REPL smoke 验证（2 个问题）+ commit + push | 30 min |

**合计：约 2 小时纯开发。**

---

## 完成后

- ✅ followup 不再因"超主题"误拒；金融相关追问能基于已有 evidence 推测 + 提示 /new
- ✅ "今天上午"等时段意图通过 intent_qualifier 传给 narrative，LLM 自己约束引用
- ✅ 全部单测通过（149 passed）
- ✅ 真实使用体验改善

**仍未解决（推后）**：
- Q1（technical / capital_flow no_data）：需要 `market_overview` 类 framework 或 IndustryResolver 兜底
- Q4（热点新闻主动获取）：需要新设计 `hot_news` 节点 + thematic clustering
- 这两件事建议合并 Phase 2.D-4 brainstorm "全市场综述形态"再决定
