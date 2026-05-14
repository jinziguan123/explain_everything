# Phase 5 Acceptance — s_f3beb777

**日期**: 2026-05-14
**Session**: s_f3beb777 — "为什么宗教战争是最血腥的战争"
**Phase 4 入口**: stage=done, 3 candidate (c_001 / c_003 / c_004 — Phase 4 HITL 2 drop 了 c_002 / c_005)
**Phase 5 出口**: stage=converged, 9 driver 节点 (3 per frontier), 3 tick reasoning_trace

## 跑法

```bash
explain run s_f3beb777 --budget 15
explain show s_f3beb777 --trace
```

LLM provider:
- `LLM_PROTOCOL=openai`
- `LLM_BASE_URL=https://api.deepseek.com/v1`
- `LLM_MODEL=deepseek-chat`
- `LLM_STRUCTURED_OUTPUT_MODE=json_object`

> 备注: 一开始用 `LLM_PROTOCOL=anthropic` + `LLM_BASE_URL=https://api.deepseek.com/anthropic`
> 失败 (DeepSeek anthropic endpoint 内部映射到 deepseek-reasoner, 不支持 tool_choice forced)。
> 切到 DeepSeek openai endpoint + json_object mode 跑通。Wave A Provider 重构的双 endpoint
> 兼容性在此被实战验证。

## 数据快照

- **tick**: 3 (tick 0/1/2 expand c_001/c_003/c_004，tick 3 stop check)
- **budget_remaining (终态)**: 12 / 15 (剩 12,实际只用了 3 — frontier 在 tick 2 后空)
- **stop reason**: `no_frontier_remaining` (3 frontier 一轮 expand 完)
- **graph 终态**: 12 concrete (p_001-p_012) + 3 abstract (c_001/c_003/c_004) + 9 driver (d_001-d_009) = **24 nodes**
- **edges 终态**: 6 manifests_as (Phase 4) + 9 causes (Phase 5) = **15 edges**
- **LLM call total**: 3 (每 expand 1 call, evaluate 0 call — K=4 但 tick 3 时就 no_frontier，evaluate 没轮到)

## Driver candidates

| frontier (c_NNN)          | driver (d_NNN)                | mean plausibility / 5 | gain |
|---------------------------|-------------------------------|-----------------------|------|
| c_001 绝对化价值框架      | d_001 超验权威背书            | ~4.35                 | 0.87 |
| c_001                     | d_002 群体身份与救赎绑定      | (同 expand call)      | (同) |
| c_001                     | d_003 教义系统内闭环逻辑      | (同 expand call)      | (同) |
| c_003 非人化认知建构      | d_004 神圣化二元对立教义      | ~4.35                 | 0.87 |
| c_003                     | d_005 宗教领袖的煽动性诠释    | (同 expand call)      | (同) |
| c_003                     | d_006 殉教与天堂奖励叙事      | (同 expand call)      | (同) |
| c_004 跨层次动员网络      | d_007 非线性传播与反馈机制    | ~4.35                 | 0.87 |
| c_004                     | d_008 跨层次耦合的决策强化    | (同 expand call)      | (同) |
| c_004                     | d_009 虚幻共情与身份融合      | (同 expand call)      | (同) |

## 验收点

- [x] stage = "converged"
- [x] ≥3 d_NNN driver (实际 9)
- [x] reasoning_trace ≥3 entry (实际 3, 每 tick 1 entry — frontier 在 tick 2 后空所以没到 evaluate)
- [x] stop reason ∈ {budget_exhausted, no_gain_for_3_ticks, no_frontier_remaining} (实际 no_frontier_remaining)
- [x] driver 名字定性扎实 — 9/9 全部是可检验的机制变量
- [x] 没有 cosmic 哲学名词 (无"熵增"/"进化"/"宇宙真理"/"人性")
- [x] Phase 0-4 测试 (159) 不破
- [x] Wave A-D 新增测试 ≥66 PASS (实际 73)
- [x] ruff check 0 error
- [x] Provider 重构后 LLM_PROTOCOL=openai + DeepSeek base_url 跑通 (anthropic endpoint 不支持 forced tool_choice, 切 openai+json_object 成功)

## 观察 (人评)

**Driver 定性的层级跃迁明显**: 比 Phase 4 abstract 真"深一层":

- c_001 "绝对化价值框架" → d_001-d_003 给出的是"为什么会绝对化"的机制 (超验权威 / 群体身份救赎绑定 / 教义闭环逻辑) —— 不是简单同义改写。
- c_003 "非人化认知建构" → d_004-d_006 揭示了非人化的具体执行机制 (二元对立教义 / 领袖煽动诠释 / 殉教天堂叙事)。
- c_004 "跨层次动员网络" → d_007-d_009 给出了网络的传播-反馈-身份融合三个角度,不冗余。

**gain 全部 0.87 — LLM 自评比较保守一致**:
- mean plausibility ≈ 4.35/5 (4 或 5 的混合)
- 3 个 expand call 都返了 3 driver 且都自评 4-5 (而非 1-3),说明 prompt 引导有效。
- 跨 frontier gain 相同 (0.87) 可能是 mock 化倾向 —— Phase 6 看是否需要更 calibrated 的 plausibility scoring。

**stop signal 触发合理**:
- 3 frontier 一轮 expand 完即 no_frontier_remaining,这是 Phase 5 cap (level==1 才算 frontier, d_NNN level=2 不再 expand) 直接决定的。
- 没有触发 budget_exhausted (budget=15 实际只用 3) 或 no_gain_for_3_ticks (gain=0.87 ≥ 0.1)。

**意外行为**:
- DeepSeek anthropic endpoint (`/anthropic`) 强制把模型映射到 `deepseek-reasoner`,而 deepseek-reasoner 不支持 `tool_choice: {type: tool, name: ...}` 强制。这跟 plan §11 design risk #3 的 "DeepSeek 通过 anthropic endpoint 支持 tools API" 假设不符 —— anthropic endpoint 暴露给客户但 backend 模型受限。
- 解法是 Wave A Provider 重构里加的 `LLM_STRUCTURED_OUTPUT_MODE=json_object` 兜底 —— 切 openai endpoint 即可,**Wave A 的 Provider 抽象层在 acceptance 阶段被实战验证有效**。

## Phase 6 起点 (Q7=B observe-then-act 决策)

**Compression coverage 是否随 driver 加入提升?**
- Phase 5 不加新 concrete,所以 `graph.coverage_score()` 不变 (仍是 Phase 4 末尾的覆盖率)。
- driver 加入是垂直深化 (L2 → L1 → L0),不是水平扩张。
- Phase 6 如果加 "driver → 新 concrete" 路径 (testable prediction generation),才会动 coverage。

**是否需要 Phase 5 末尾 prompt iteration?**
- Driver 质量本次 acceptance 看着够好 (人评 9/9 通过)。
- 但 3 个 frontier 都返同 gain=0.87 反映 plausibility 自评可能 over-cluster 到 4-5。Phase 6 跑更多 question 后再 tune。
- prompt 里关于"不重复 existing_drivers"的指令在本 session 内每个 expand call 都看到了前面的 driver (从 trace 看 c_001 expand 时 existing=[], c_003 expand 时看 c_001 的 3 个 driver, c_004 看 6 个),没出现明显重复。

**K=4 / GAIN_THRESHOLD=0.1 是否合理?**
- K=4 在本次 acceptance 没真触发 — 3 frontier tick 3 就 no_frontier，没进 evaluate phase。
- GAIN_THRESHOLD=0.1 没被触发 — gain 全 0.87 远超。
- 这两个参数在 frontier 更多 (≥5) 的 session 才能压力测试。Phase 6 增加 frontier-rich question (e.g. 多 concrete + 多 abstract) 再 tune。

**待改进项**:
1. plausibility 应该可视化保存到 graph (e.g. RelationEdge.metadata 或 VariableNode.meta dict),便于事后 audit。当前只 in-memory + reasoning_trace.gain_delta 反推。
2. expansion.yaml prompt 里没要求 LLM 显式说"为什么不重复 existing_drivers" — 可加 rationale 字段强制 LLM 写明,提高 audit trail。
3. driver 跨 frontier 互动 (e.g. d_001 超验权威背书 vs d_006 殉教叙事 是否相关?) 在 Phase 5 schema 内不可表达 (没有 driver-driver edge)。Phase 6 考虑 cross-driver causes/correlates edge。
