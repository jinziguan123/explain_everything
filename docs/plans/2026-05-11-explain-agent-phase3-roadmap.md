# Explain Agent · Phase 3 路线图（广度扩展）

> 这是占位 backlog 文档，不是完整设计。Phase 2.C/2.D/2.E 跑完、积累 20-30 个真实案例后，再做完整的 brainstorm + design + plan。

## 背景

Phase 1-2 把项目做成了"A 股板块/主题异动归因"的垂直 agent。但 Phase 2.C brainstorm 期间（2026-05-11）确认了产品演化方向：**从"金融板块归因"扩展为"广度大的通用解释型 agent"**。

当前架构有两层"硬框定"阻碍广度：
1. `domain_router` → 一个 framework YAML（目前只有 `cn_equity_sector_attribution`）
2. YAML 写死 6 维 + query_template + data_sources

需要保持垂直专业度的同时引入广度。

## 演化路径（按"小步快跑"分阶段）

### Phase 3-A：connection_explorer 节点（无先验自由发散）

**目标**：在不动现有架构的前提下加广度。

**做法**：
- `synthesizer` 之后新增 `connection_explorer` 节点
- 强模型阅读 6 维 evidence 树后，主动提 ≤3 个"延伸议题"：
  - "证据反复出现 ___ 实体，但没在 6 维内被解释"
  - "这个事件让我联想到 ___ 的历史/类比案例"
  - "顺着 ___ 这条线再问一层，会找到 ___"
- 每个延伸议题触发 1 次轻量证据收集（web search + 现有 adapter）
- narrative 末尾追加"**延伸思考**"段落

**收敛门控**：
- 强模型自评 confidence（1-5），<3 砍
- 检查"是否已被 6 维核心覆盖"，重复砍
- 限制深度=1（不递归追问）

**风险**：完全靠 LLM 自由发挥，可能漂移到段子。

**验证方式**：跑 20-30 个真实案例（含金融、政策、科技、加密、地缘事件等），人工标注每个延伸思考的质量：

| 标签 | 含义 |
|---|---|
| 🟢 真知灼见 | 用户没想到但合理 |
| 🟡 合理但平庸 | 没新意 |
| 🔴 漂移 | 跑题或段子 |

如果 🔴 占比 < 20%，Phase 3-A 就够用，不必上 3-B。

### Phase 3-B：学科正交度地图（先验引导发散）

**触发条件**：Phase 3-A 实测发现 LLM 自由发散漂移严重（🔴 占比 ≥ 30%）。

**做法**：
- 定义学科分类体系（30 个左右）：经济学、金融学、政治学、心理学、社会学、物理学、生物学、计算机科学、历史学……
- 用 LLM-as-judge 一次性给所有学科两两评分（435 对 × ~$0.01 ≈ $5 一次性成本），生成正交度矩阵，落盘缓存
- `connection_explorer` 改造：
  1. 主学科识别：LLM 判断当前议题属于哪 1-2 个主学科
  2. 邻接学科候选：从正交度矩阵取 ≤2 个最相关的低正交度学科
  3. 在每个邻接学科生成一个延伸假设
  4. 收敛门控（同 3-A）

**为什么不在 3-A 直接做**：
- 在没有"无先验基线"对比的情况下加学科正交度，无法验证它真的有用
- KISS：如果 3-A 已经够好，3-B 是过度工程

### Phase 3-C（远期）：图谱探索 / framework 库

**触发条件**：3-A/3-B 跑久了发现"维度"概念本身限制太大。

**候选做法**（择其一）：

**c1. Framework 库**
- 建一个 framework 仓库：`cn_crypto_attribution`、`tech_event_explanation`、`policy_decode`、`history_explanation`...
- `domain_router` 升级为"框架选择器"：可选已有 + 即时生成 YAML
- 每个 framework 仍预定义 5-7 维度，保持专业度

**c2. 图谱探索**
- 放弃"维度"，改为"实体-事件-概念"图
- 节点扩展：因果 / 相关 / 类比 / 时序边
- worker 不按维度分工，按"节点扩展路径"探索
- 输出形态从"6 维 + narrative"变为"叙事 + 实体图谱"

**c2 是大改造**，需要图数据库 / 图状态管理 / 新输出形态。先观察 c1 是否够用。

## 与现有 framework 系统的兼容

任何 Phase 3 改动都不能破坏 `cn_equity_sector_attribution` 的 6 维深度归因。两条路保留：

1. **垂直深度路**：保留 framework YAML + DimensionWorker，金融问题走这条
2. **广度发散路**：connection_explorer + (可选) 学科正交度 + (可选) 通用 framework，跨议题走这条

`domain_router` 决定走哪条，或者两条都走（核心 + 卫星模型）。

## 不在 Phase 3 范畴的

- **eval framework**：原 Phase 2.D 候选项，但 Phase 3 才真的需要它（无 eval 没法对比 3-A vs 3-B 漂移率）
- **用户偏好长期记忆**：跨 session 学习用户兴趣，Phase 4+
- **多语言 / 多市场归因**（如美股、港股、欧股）：可作为新 framework，但本质是 c1 的应用

## 启动条件

- Phase 2.C 落地（CLI REPL + Followup Graph）
- Phase 2.D 落地（lazy ingest + fan_out 优化 + 思路 A 的 connection_explorer 节点骨架）
- Phase 2.E 完工或砍掉
- 用户在 REPL 中跑过 20-30 个跨议题的真实案例，留下了反馈标注

到时再启动 Phase 3 完整 brainstorm：
- `docs/plans/YYYY-MM-DD-explain-agent-phase3-design.md`
- `docs/plans/YYYY-MM-DD-explain-agent-phase3.md`

## 决策记录

| 时间 | 决策 |
|---|---|
| 2026-05-11 | 产品方向确认：从"金融垂直 agent"演化为"通用解释 agent"，但保留垂直深度路径 |
| 2026-05-11 | Phase 3 三阶段拆分（3-A connection_explorer / 3-B 学科正交度 / 3-C framework 库或图谱）确认 |
| 2026-05-11 | Phase 2.D 引入 connection_explorer 节点骨架（思路 A）作为 Phase 3 前置 |
