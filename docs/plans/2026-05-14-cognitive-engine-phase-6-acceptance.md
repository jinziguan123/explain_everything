# Phase 6 Acceptance — Simulation Consistency Check on 3 sessions

**日期**: 2026-05-14
**Sessions**: s_f3beb777 / s_705f0435 / s_7d491774
**LLM provider**: N/A (Phase 6 pure rule-based, **0 LLM call**)
**Tune**: 顶层 §11.4 默认值未调 (见 §7 分析)

## 跑法

```bash
.venv/bin/python -m explain_engine.cli check s_f3beb777
.venv/bin/python -m explain_engine.cli check s_705f0435
.venv/bin/python -m explain_engine.cli check s_7d491774

# 单 target 详细
.venv/bin/python -m explain_engine.cli check s_f3beb777 c_001
```

## 数据快照

### Batch consistency / essentialness 对比

| Session | n L0/L1/L2 | n manifests_as / causes | L1 Consistency | L2 Consistency | L1 Essentialness | L2 Essentialness | Hallucination flag |
|---|---|---|---|---|---|---|---|
| s_f3beb777 | 12/3/8 | 6/8 | **0.70** | **0.42** | 0.04-0.05 | 0.00 | No (Phase 5 已审) |
| s_705f0435 | 9/3/8 | 9/8 | **0.70** | **0.42** | 0.06-0.14 | 0.00 | Mixed (方向 mismatch) |
| s_7d491774 | 13/3/8 | 12/8 | **0.70** | **0.42** | 0.01-0.05 | 0.00-0.05 | **Yes** (LLM 编 L0) |

### Graph coverage 分析

| Session | L0 orphans (no incoming manifests_as) | per-c L0 覆盖范围 |
|---|---|---|
| s_f3beb777 | **50% (6/12)** | 各 c 仅覆盖 2 个 L0 (HITL 2 drop 多) |
| s_705f0435 | 0% (0/9) | 各 c 覆盖 2-4 个 L0 |
| s_7d491774 | 8% (1/13) | 各 c 覆盖 3-5 个 L0 (LLM 编时织得密) |

**关键观察**: hallucinated session 反而 graph 覆盖最完整 — 因为 LLM 编造时不像 user-审-HITL-2 那样保守 drop edges. 跟 negative control 预期相反.

## 验收 checklist

- [x] **算法 deterministic** — 同 session 跑 2 次结果完全一致 (PROPAGATION_THRESHOLD-floor 后无浮点漂移; frontier 已 sorted ↗ Task 6.2 I1 fix)
- [x] **batch perf** — < 100ms / session (实测 < 50ms, 3 session 总 < 200ms)
- [ ] ⚠️ **negative control: s_7d491774 < s_f3beb777** — **FAILED 但 root cause 不在算法** (见 §7 分析)
- [x] **MAX_ACTIVE_VARIABLES=12 不过严** — 不触发剪枝 (每 c 出 2-5 L0, 远 < 12)
- [x] **WEAK_CHAIN_THRESHOLD=0.15 合理** — L1 activation=0.7, L2 activation=0.42 都远 > 0.15, 没出 weak_chains (说明阈值不对当前 graph 失真,但合理)
- [ ] ⚠️ **essentialness 区分度 ≥ 0.2** — **FAILED**: 实测同 session 跨度 0.01-0.14 (远 < 0.2)
- [ ] ⚠️ **L0 节点 ≥ 90% reachable** — **FAILED for s_f3beb777**: 50% L0 orphan, 仅 50% 被 abstract 覆盖
- [x] **rendering**: 中文 column 对齐 / color threshold 渲染 / trace 不溢出 — 全部正常
- [x] **Phase 0-5 + Phase 6 测试** — 全 **275 PASS**, ruff 0
- [x] **ruff check** — 0 errors

10 项 7 ✅ + 3 ⚠️.

## 算法行为观察

### Why consistency_score 三 session 完全一致 (L1=0.70, L2=0.42)?

**因为 Phase 0-5 流程把所有新 edge confidence 都设成 default 值** (manifests_as conf=0.7, causes conf=0.6 — 见 Phase 4 compression + Phase 5 expansion 默认值). Propagation 算法精确实现了 design §11.5 stability regularization (`act × confidence` 乘性衰减), 但当所有 edge conf 一致时:

- L1 target: `propagate({c}) → reachable_L0 都 = c.activation × 0.7 = 0.70`
- L2 target: `propagate({d}) → 经 causes (0.6) → manifests_as (0.7) = 0.42`

**consistency_score = mean(reachable_L0) = 0.70 (L1) / 0.42 (L2)**, 与 graph 内容无关, 只反映 default confidence 数值的几何性质.

### Why essentialness ≈ 0?

**Noisy-OR 多路径合并 + 多 driver 全连同组 L1 → 任一 driver 的 marginal contribution 近 0**:

- 8 个 driver 都通过 causes (conf=0.6) 连到 L1, noisy-OR 后 c_NNN 的总 activation 接近 1.0
- 删任一 driver, 剩余 7 个 driver 仍能 noisy-OR 把 c_NNN 拉到接近 1.0
- → essentialness_score 趋零

这反映了 **Phase 5 driver-layer 是 saturated** 的事实 — 多个 driver 互相冗余, 没有 "唯一不可替代" 的 driver.

### Why L1 essentialness 也低 (0.01-0.14)?

L1 之间通常不重叠 (c_001/c_003/c_004 解释不同 L0 子集), 所以删一个 c_NNN 会让对应 L0 完全 unreachable. 但 essentialness 公式分母是 `|L0|` (全 L0), 不是 `|reachable_L0|`, 所以小 c (只覆盖 2 个 L0 of 12) 对全 graph 贡献被稀释成 ~0.04.

s_705f0435 c_003 essentialness=0.14 较高 — 因为它覆盖 4 个 L0 (单 c 最广), 删它伤害更大.

## §7 分析: Negative control failed 的 root cause

**Design §7.3 期望**: "s_7d491774 (hallucinated) avg consistency < s_f3beb777 (clean) avg consistency".

**实测**: 两个都是 L1=0.70, L2=0.42. 没区别.

**为什么 failed**: 算法 propagate 沿 graph 已有 edges, 沿用其 edge.confidence. **Phase 0-5 default confidence (0.7/0.6) 是 placeholder, 不是真实质量信号**. 算法忠实反映了这点 —— hallucinated session 的 edge confidence 跟 clean session 一样高, 算法无从区分.

**这不是算法 bug, 是数据 issue**. Algorithm 仍正确实现了 design §11.1/§11.5 的数学语义.

**Phase 7+ 必须解决的问题**: confidence 信号化. 几个候选:
1. **Phase 7 expansion 时 LLM 自评 plausibility → 写进 edge.confidence** (不是默认 0.6)
2. **Phase 7 Reflection Engine 看 consistency_score 决定是否重审 graph**
3. **Phase 8 Theory Formation 用 simulation_consistency 做 theory 评估**
4. (长期) Variable Lifecycle (§9.1) 用 stability_score 跑 Decay / Death

### Discovery 价值

虽然 negative control failed, **Phase 6 仍提供了重要诊断信号**:

1. **暴露了 graph 数据稀疏** — s_f3beb777 50% L0 orphan, HITL 2 用户审太严. Phase 7+ 应当 review prompt / HITL UX, 引导用户保留更多有意义的 edge.
2. **暴露了 Phase 5 driver-layer 冗余** — 8 个 driver 互相 noisy-OR 后没有 "unique" driver, essentialness 趋零. Phase 7+ expansion 时应当强制 driver 多样性 (e.g. 跨 perspective).
3. **暴露了 confidence placeholder 问题** — 这是 Phase 7+ 最优先解决的 schema issue.

## Tune 决策

**不调任何常量**. 理由:

- `PROPAGATION_THRESHOLD=0.05`: 实际 propagation 都 > 0.4, 远 > 0.05. 阈值未触发, 改了也没用.
- `MAX_DEPTH=4`: 当前 graph 最深 driver → abstract → concrete 仅 2 hop, 远 < 4. 改了无影响.
- `MAX_ACTIVE_VARIABLES=12`: 单层最多 5 节点 (per-c L0), 远 < 12. 改了无影响.
- `WEAK_CHAIN_THRESHOLD=0.15`: 实际 reachable_L0 activation 都 ≥ 0.42, 全 > 0.15. weak_chains 永远空. 阈值合理, 但需要更深 chain 才有意义.

**结论**: 顶层 §11.4 默认值在当前 graph 数据上**未被压力测试**. Phase 7+ 当 confidence 真正变化时再 tune.

## Phase 7 起点

Phase 6 完工后系统具备:

1. **Propagation algorithm production-ready** — Phase 7 forward prediction 0 重写直接复用 `propagate(graph, sources)`
2. **Variable-level "structural quality" 信号** — consistency / essentialness 现可供 Reflection Engine 调度 (但需要 Phase 7 先把 confidence 信号化, 否则信号失效)
3. **3 session 跨议题 baseline 数据点** — 给 Phase 7+ 验证 tune 用

### Phase 7 推荐方向 (按 ROI 排序)

**优先级 1: Confidence 信号化** (基础设施修复)
- expansion 时 LLM 写 plausibility 进 edge.confidence (不是 default 0.6)
- compression 时 evaluation 的 score → edge.confidence
- 当 confidence 真实变化时, Phase 6 simulation 才能 differentiate session quality

**优先级 2: Forward Prediction + Counterfactual** (跟 design §9 一致)
- intervention → propagated effects (LLM 生新 predicted L0)
- Counterfactual: Remove/Substitute variable, 跟 A 共 80% mechanics

**优先级 3: Reflection Engine** (跟顶层 §12 一致)
- 用 Phase 6 consistency / essentialness 调度 reasoning loop
- "consistency < threshold → re-expand" / "essentialness=0 → prune"

**优先级 4: HITL UX / Prompt 改进** (data quality)
- s_f3beb777 50% L0 orphan 反映 HITL 2 user drop 过严
- compression prompt 可加 "请保留至少 60% L0 覆盖" 约束

---

## 附录 A: 完整 CLI 输出

(全部在 `/tmp/check_*.txt`, 这里贴 c_001 单 target 详细作样例)

```
ConsistencyReport: s_f3beb777 → c_001 绝对化价值框架 (L1)

  consistency_score:    0.70   (mean activation over reachable L0)
  essentialness_score:  0.04   (Σ contribution / |L0|)
  reachable L0:         2   ['p_001', 'p_010']

Contribution Breakdown (baseline - without_target):
  p_010 世俗事务神圣化     0.25
  p_001 神圣不可妥协性     0.25
  (其他 10 个 L0 contribution 全为 0.00)

Decay Trace (top 8 by activation_after):
  depth=0  c_001 → p_001  edge=e_001  conf=0.70  act 1.00 → 0.70
  depth=0  c_001 → p_010  edge=e_002  conf=0.70  act 1.00 → 0.70
```

## 附录 B: Phase 6 工程总结

- **5 task, 单 Wave A, 线性依赖**
- **commits (按 task)**:
  - Task 6.1: `e174456` outgoing_edges helper
  - Task 6.2: `1368d1e` + `0b29ff6` (fix: deterministic trace + cross-layer merge test)
  - Task 6.3: `bd7a282` + `6fe1b4d` (fix: short-circuit baseline + frozen note)
  - Task 6.4: `5dc3c08` + `11ecd08` (fix: WEAK_CHAIN_THRESHOLD import + --trace-all test)
- **新增测试**: 275 PASS (Phase 0-5: 232 + Phase 6: +43)
  - graph.outgoing_edges: 3
  - _propagation algorithm: 15
  - simulation API: 17
  - cli check: 8
- **总代码**: ~700 LOC 新增 (含测试)
- **LLM cost**: $0 (pure rule-based, 全 phase)
- **ruff**: 0 errors throughout
