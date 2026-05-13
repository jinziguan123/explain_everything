# Cognitive Engine MVP — Design Doc v0.1

**日期**: 2026-05-13
**分支**: `cognitive-engine-mvp`
**前置文档**:
- [设计哲学.md](../../设计哲学.md)
- [需求设计.md](../../需求设计.md)
- [技术设计v1.md](../../技术设计v1.md)（完整愿景）

> **Phase 4 update**: compression_score 已删除，由 compression_gain 替代（见 2026-05-13-cognitive-engine-phase-4-design.md §4）。本 doc 中所有 compression_score 引用视为 compression_gain。

---

## 0. TL;DR

把项目从"A 股板块归因 agent"重做成**通用解释引擎**。MVP 不绑领域，目标是产出**一个可演化的 Cognitive Runtime** —— 输入一个"为什么 X"问题，系统在 budget 内不断 expand / compress / evaluate，最终产出一个 `ExplanationGraph`，其核心是从乱七八糟现象中**压出一个隐藏高维变量**（compression / hidden variable discovery）。

**Wow moment**: 用户看到"房价↑ / 内卷 / 低生育 / 短视频沉迷"压缩成同一个变量"长期不确定性"。

---

## 1. WHY — 项目重定向

### 1.1 旧 agent 死亡

`master` 分支上跑到 Phase 2.D-3 的 A 股归因 agent **判定产品死亡**。原因：

| 层 | 问题 |
|---|---|
| 应用领域 | 太窄（金融垂直）|
| 系统形态 | Workflow Executor（固定 LangGraph 拓扑），不是 Cognitive Engine |
| 抽象层 | 固定 6 维归因框架（policy / industry_chain / international / sentiment / technical / capital_flow），不是动态变量发现 |
| 输出形态 | 6 维归因报告，不是多层级解释 / 反直觉洞察 |

旧 agent 不作为"代码复用源"，仅作墓碑参考。

### 1.2 新方向

参照 `设计哲学.md` 和 `技术设计v1.md`：
- 系统本质是 **World Explanation Engine** / **Explanation-Centric Cognitive System**
- 核心不是任务完成（Task Completion），是世界解释（World Explanation）
- 核心对象不是文本，是 **Variable + Relation + Dynamic Graph**

---

## 2. WHAT — MVP 目标 & 边界

### 2.1 MVP 验证 thesis（≤3 条，全部满足才算成功）

1. 系统能形成**稳定**的 `ExplanationGraph`（多次运行同一问题，graph 结构相似）
2. 系统能完成**至少一次** abstraction / compression（产生 abstraction_level ≥ 1 的节点）
3. 系统输出比普通 CoT 在**反直觉度 + 定义清晰度**两项人评上得分更高

### 2.2 MVP wow moment（产品定位）

> 从乱七八糟诸多现象里压出一个隐藏高维变量。

例：输入 `"为什么年轻人不消费"`，输出 `ExplanationGraph` 内含
```
[长期不确定性] (abstract, insight, conf=0.7)
       ↓ manifests_as
  ├─ 房价上涨
  ├─ 内卷加剧
  ├─ 短视频沉迷
  ├─ 低结婚率
  └─ 收入停滞
```

### 2.3 MVP 砍掉 / 推后的

| Feature | 状态 | 推到 |
|---|---|---|
| Reflection / meta-cognition | ❌ | v0.2 |
| Attention allocation (动态打分) | ❌（用轮转 scheduler 代替）| v0.2 |
| Embedding / clustering | ❌ | v0.2 |
| Multi-perspective graphs | ❌ | v0.3 |
| Counterfactual reasoning | ❌ | v0.3 |
| Problem reframing | ❌ | v0.2 |
| Alternative explanations | ❌ | v0.2 |
| Persistent world model | ❌ | v0.4+ |
| Web UI / 可视化 | ❌ | v0.2 |
| RAG / web search | ❌ 默认关闭 | v0.2 可选开 |

---

## 3. HOW — 核心架构

### 3.1 顶层结构

不是 pipeline，是 **Cognitive Runtime + ExplanationGraph**。

```
User Question
    ↓
Bootstrap (HITL 1: 用户审 seed phenomena)
    ↓
┌─────────── Reasoning Loop ───────────┐
│  while not should_stop(state):       │
│    action = scheduler.pick(state)    │
│    expand / compress / evaluate      │
│    budget -= 1                       │
└──────────────────────────────────────┘
    ↓
Finalize (HITL 2: 用户在 insight candidates 间选)
    ↓
Render → explanation.json + explanation.md
```

### 3.2 Schema v0.1

```python
class VariableNode:
    id: str
    name: str                            # "长期不确定性"
    description: str                     # 明确定义边界，防止变量含义漂移
    abstraction_level: int               # 0=concrete, 1=mid, 2=abstract
    confidence: float                    # 0-1
    epistemic: Literal[
        "fact",                          # 可验证事实
        "observation",                   # 主观可观察
        "inference",                     # 基于已知关系的推断
        "insight",                       # 抽象跃迁后的解释变量
        "speculation",                   # 弱推断
    ]
    evidence_ids: list[str]              # 关联到的 evidence（v0.1 可空）


class RelationEdge:
    id: str
    source_node: str
    target_node: str
    relation_type: Literal[
        "causes",          # X 生成 Y
        "amplifies",       # X 加剧 Y
        "suppresses",      # X 抑制 Y
        "constrains",      # X 限制 Y
        "manifests_as",    # X 在具体层表现为 Y（抽象→具体专用）
    ]
    confidence: float
    mechanism_description: str           # 一句话解释为什么这条边成立


class ExplanationGraph:
    """networkx.DiGraph 的包装。"""
    nodes: dict[str, VariableNode]
    edges: dict[str, RelationEdge]
    root_question: str

    # 派生
    def compression_score(self) -> float: ...   # high-abstraction node 覆盖多少 concrete
    def coverage_score(self) -> float: ...      # 总 concrete 中被任意 abstract 解释的比例
    def frontier(self) -> list[str]: ...        # 没有 outgoing edge 的 high-abstraction 节点


class CognitiveState:
    graph: ExplanationGraph
    active_frontier: list[str]               # 当前 tick 要扩展的节点 id
    budget_remaining: int
    root_question: str
    insight_candidates: list[str]            # 已生成的 abstract node id（供 HITL 2 选）
    tick: int
    last_gain_tick: int                      # 上一次 explanatory gain > 0 的 tick
```

**砍掉的 v1.md 字段**（v0.2+ 加）:
`embedding`, `salience_score`, `explanatory_power`, `parent_variables`, `child_variables`, `temporal_direction`, `bidirectional`, `perspective_scope`, `explanatory_gain`, `causal_strength`, `attention_map`, `uncertainty_map`, `reasoning_trace`

### 3.3 Reasoning Loop

```python
def run(question: str, budget: int = 20) -> ExplanationGraph:
    state = bootstrap(question)              # Variable Extraction Protocol + HITL 1
    while not should_stop(state):
        action = scheduler.pick(state)
        match action:
            case "expand":   expansion.expand_one_frontier(state)
            case "compress": compression.try_compress(state)
            case "evaluate": evaluation.score(state)
        state.budget_remaining -= 1
        state.tick += 1
    return finalize(state)                   # HITL 2
```

**`should_stop` signals**（v1.md §9.2 砍剩 2 个）:
- `budget_remaining <= 0`
- `state.tick - state.last_gain_tick >= 3`（连续 3 tick 无 explanatory gain）

**Scheduler（MVP 简化）**: 轮转 `expand → compress → evaluate`，不算 attention score。

### 3.4 Engine 拆分

| Engine | v0.1 责任 | v0.1 砍法 |
|---|---|---|
| **Bootstrap** | 用 question 调 Variable Extraction Protocol，生成 8-15 个 seed concrete phenomena | 强制 HITL 1（用户审 / 加 / 减 / 改） |
| **Expansion** | 取一个 frontier 节点，调 Causal Expansion Protocol，找上游 cause / 同层 amplifier | 只支持 1 种 expansion type: `find_higher_abstraction` |
| **Compression** | 检测 concrete node cluster（≥3 个未被任何 abstract 节点覆盖），调 Compression Protocol，生成一个 abstract node + 5 条 `manifests_as` edge | 只做 Variable Compression（v1.md §8.2 的 4 种压缩里取 1）|
| **Evaluation** | 算 compression_score / coverage_score，记录 explanatory gain delta | 不算 attention / novelty / centrality |
| **Convergence** | 检查 stop signals | 只用 budget + signal 1 |
| **Scheduler** | 决定下一 action | 轮转，无 attention score |
| **Reflection** | — | ❌ v0.2 加 |

### 3.5 Prompt Protocol（保留 v1.md §10.3 的拆分）

每个 Engine 独立 prompt 文件，强制 structured output:

```
src/explain_engine/llm/prompts/
├── variable_extraction.yaml   # Bootstrap 用 + Expansion 提取新节点用
├── relation_construction.yaml # Expansion 建立 edge 用
├── compression.yaml           # Compression Engine 用
└── scoring.yaml               # Evaluation Engine 的反直觉度 / 定义清晰度 LLM-as-judge
```

每个 prompt：
- 描述本 Engine 唯一职责
- 输入 schema（来自上一步状态）
- 输出 schema（pydantic class 的 JSON schema）

---

## 4. 工程边界

### 4.1 分支策略

- **`master`**：金融 agent 墓碑，冻结
- **`cognitive-engine-mvp`**：新分支，删旧代码，从 0 开发
- 删除范围（在第一个 implementation commit 里执行）:
  - `src/explain_agent/`（整个）
  - `tests/`
  - `migrations/`
  - `frameworks/`
  - `data/`
  - `scripts/`
- 保留:
  - `.env.example`, `.gitignore`, `pyproject.toml`（要改 deps）
  - `docs/plans/`（含本 doc 及旧 plans 作历史参考）
  - 顶层 4 份 .md（设计哲学 / 需求设计 / 技术设计 / 技术设计v1）
  - `README.md`（要重写）

### 4.2 LLM 多 provider 抽象

```python
# src/explain_engine/llm/client.py
class LLMClient(Protocol):
    async def chat(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        model: str = "default",
    ) -> Response: ...

# 三个 provider 适配 structured output:
#   Claude   → tools(input_schema=schema.model_json_schema())
#   OpenAI   → response_format={"type": "json_schema", "schema": ...}
#   DeepSeek → response_format={"type": "json_object"} + prompt 注入 schema 描述
```

provider 通过 `.env` 配置选择：
```
LLM_PROVIDER=claude|openai|deepseek
CLAUDE_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
LLM_MODEL=claude-opus-4-7   # 默认
```

### 4.3 项目结构

```
src/explain_engine/
├── schema/
│   ├── nodes.py              VariableNode
│   ├── edges.py              RelationEdge
│   ├── graph.py              ExplanationGraph (networkx.DiGraph 包装)
│   └── state.py              CognitiveState
├── runtime/
│   ├── runtime.py            main reasoning loop
│   ├── scheduler.py          轮转 scheduler
│   └── budget.py             budget + stop signals
├── engines/
│   ├── bootstrap.py
│   ├── expansion.py
│   ├── compression.py
│   ├── evaluation.py
│   └── convergence.py
├── llm/
│   ├── client.py             LLMClient Protocol
│   ├── claude.py
│   ├── openai.py
│   ├── deepseek.py
│   └── prompts/*.yaml
├── hitl/
│   └── cli_interactive.py    HITL 1 + HITL 2 的 CLI 交互
├── persistence/
│   └── session.py            JSON 磁盘落地 / load / list
└── cli.py                    `explain new / continue / show / list`
seeds/                        10 个种子问题（plain text）
sessions/                     运行时 session-{id}.json
tests/                        pytest
```

### 4.4 CLI 形态

```bash
explain new "为什么年轻人不消费"
# → 启动 session，跑 Bootstrap，进入 HITL 1
# → 退出，session.json 落地

explain continue <session_id>
# → 读 session.json，根据 stage 进入对应 HITL or 继续 loop
# → 跑完整个 loop 后进入 HITL 2，再退出

explain show <session_id>
# → 显示当前 graph 状态（文字 / ASCII art / 或 graphviz dot）

explain list
# → 列出所有 sessions
```

---

## 5. 评估方式

### 5.1 三层评估

| 层 | 方式 | 评估对象 |
|---|---|---|
| L1 schema 合法性 | 自动（pytest）| 输出是否 fit pydantic type |
| L2 量化指标 | 自动 | compression_score / coverage_score / final_node_count |
| L3 主观质量 | 手动（3-5 人， 1-5 分）| 反直觉度 / 定义清晰度 / "是否让我重新看世界" |

### 5.2 种子问题集（v0.1, 10 题）

跨领域，验证 schema 不局限于某领域:

1. 为什么年轻人不消费？
2. 为什么生育率下降？
3. 为什么我执行力差？
4. 为什么硅谷的失败容忍度高？
5. 为什么短视频让人上瘾？
6. 为什么传统医生不爱用 AI？
7. 为什么会议越开越多但效率越低？
8. 为什么有些技术成熟但没普及？
9. 为什么"找对象"在大城市这么难？
10. 为什么很多人下班后还工作？

每题跑 → `sessions/seed-NN-{slug}.json` + `seeds/expected/seed-NN.md`（人评后填）

### 5.3 baseline 对比

每题同时让"普通 CoT"（单次 LLM 调用，不走 runtime）跑一遍，存对照。L3 人评时双盲打分。

---

## 6. Error handling & 已知风险

### 6.1 Error handling

| 错误 | 处理 |
|---|---|
| LLM 输出不 fit schema | 自动 retry 1 次 + structured output 校验失败回退到 prompt 修复 |
| 用户 Ctrl-C | 当前 state 落 session.json，下次 `explain continue` 恢复 |
| budget exhausted but no abstract node | finalize 时把得分最高的 concrete cluster 标记为"未成功压缩"，render 时如实告知 |
| LLM hallucination | 每条 edge 必有 `mechanism_description`；每个 node 必有 `epistemic` |
| 多次运行同问题结果不一致 | MVP 不强求 deterministic；评估时跑 3 次取最稳定一次 |

### 6.2 已知风险

1. **Runtime loop 死循环 / 振荡**：expand 完又被 compress 删，无限往返
   - 缓解：budget 硬上限 + `last_gain_tick` 检测 + 节点删除不可逆

2. **空洞抽象**（v1.md Reflection 检查项之一）：LLM 生成的 abstract variable 名字漂亮但实质空
   - 缓解：HITL 2 强制人评 + `scoring.yaml` 的"定义清晰度"prompt

3. **过早收敛**：budget 20 太小，没充分扩展就停
   - 缓解：MVP 阶段试不同 budget（10/20/40），看 graph 稳定 budget

4. **LLM provider 之间结果差异大**：Claude / OpenAI / DeepSeek 对同一 prompt 输出风格差异
   - 缓解：MVP 阶段固定一家（默认 Claude）跑 baseline，多 provider 仅作可切换，不在 MVP 评估范围

### 6.3 Open Questions（写入 plans，不阻断 MVP）

- Q1: `abstraction_level` 是否要更细（3 档够吗？v1.md 只列了 3 档示例，但实际跑可能要更多）
- Q2: HITL 1 和 HITL 2 之外，是否需要 mid-loop HITL（用户中途介入 reasoning）
- Q3: 同一个 abstract variable 多次 compression 时如何合并 / 不合并
- Q4: 如何让用户能 "challenge" 一个 edge（说"我不同意这条因果"）—— MVP 不做，v0.2 加
- Q5: budget=20 是否合理 —— 实测后定

---

## 7. MVP 验收标准

满足以下全部条件才算 v0.1 完工:

- [ ] 10 个种子问题全部能跑通（不崩 / 输出 fit schema）
- [ ] 10 题中 ≥7 题产生至少 1 个 abstract node（compression 成功率 ≥70%）
- [ ] L3 主观评分（5 人均值）反直觉度 + 定义清晰度 ≥ 3.5/5
- [ ] 与 baseline CoT 双盲对比，runtime 输出在"反直觉度"上胜率 ≥ 60%
- [ ] HITL 体验：用户能在 5 分钟内审完一题（HITL 1 + HITL 2 总耗时）
- [ ] 测试覆盖：schema / scheduler / convergence / 各 Engine 都有单测

---

## 8. 下一步

写 implementation plan（writing-plans skill），把本 doc 拆成 bite-sized 任务（建议 10-15 个）:
- Phase 0: 清理 + scaffold
- Phase 1: schema + persistence
- Phase 2: LLM client + 一个 provider
- Phase 3: Bootstrap engine + HITL 1 + CLI 入口
- Phase 4: 其他 Engine + Runtime loop
- Phase 5: Finalize + render + HITL 2
- Phase 6: 种子问题跑 + 评估脚手架
