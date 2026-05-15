# Phase 7 Acceptance — Confidence + Forward Prediction + Reflection

**日期**: 2026-05-15
**Sessions**:
  - Wave A rescore + check: s_f3beb777 (clean) / s_705f0435 (mismatch) / s_7d491774 (hallucinated)
  - Wave B predict/counterfactual smoke: s_e8a2a3a2 (后现代主义)
  - Wave C reflection 闭环 smoke: s_4c5f717d (存储成为热点)
**LLM provider**: 见 `.env` 实跑配置 (LLM_PROTOCOL/LLM_BASE_URL/LLM_MODEL); rescore + Wave B/C smoke 均走真实 LLM call
**Tune**: 顶层 §11.4 默认值未调 (见 §Tune 决策, Wave A 数学差距小不动 fixture)

## 跑法

```bash
# Wave A — rescore + check 3 session (real LLM)
.venv/bin/python -m explain_engine.cli rescore s_f3beb777
.venv/bin/python -m explain_engine.cli rescore s_705f0435
.venv/bin/python -m explain_engine.cli rescore s_7d491774

.venv/bin/python -m explain_engine.cli check s_f3beb777
.venv/bin/python -m explain_engine.cli check s_705f0435
.venv/bin/python -m explain_engine.cli check s_7d491774

# Wave B — predict / counterfactual smoke (real LLM)
.venv/bin/python -m explain_engine.cli predict s_e8a2a3a2 \
    "后现代主义的蔓延对于下一代年轻人会有什么样的影响"
.venv/bin/python -m explain_engine.cli predict s_e8a2a3a2 \
    "后现代主义对于经济会有什么样的影响"
.venv/bin/python -m explain_engine.cli predict s_e8a2a3a2 \
    "其未来可能会发展出什么新的哲学或者心理学思潮吗"

# Wave C — reflection 闭环 smoke (real LLM, 多轮 explain run)
.venv/bin/python -m explain_engine.cli run s_4c5f717d
```

## Wave A 数据 — Rescore 后 simulation 真信号化

### 三 session 整体对比 (after rescore)

| Session | 类型 | L1 avg | L2 avg | 总 avg | Weak chains |
|---|---|---|---|---|---|
| s_f3beb777 | clean (宗教战争) | 0.467 | 0.293 | **0.340** | p_006, p_010 (2) |
| s_705f0435 | mismatch (特朗普访华) | 0.560 | 0.359 | **0.414** | (无, 0) |
| s_7d491774 | hallucinated (A股) | 0.397 | 0.269 | **0.304** | p_005, p_007, p_013 (3) |

跟 Phase 6 placeholder L1=0.70 / L2=0.42 整齐对照: rescore 后跨度 0.18-0.65, 不再 placeholder.

### s_f3beb777 (clean) per-target

```
c_001 绝对化价值框架   L1   0.50    essentialness=0.03
c_003 非人化认知建构   L1   0.30    essentialness=0.04
c_004 跨层次动员网络   L1   0.60    essentialness=0.04
d_001 文本原教旨主义   L2   0.30    essentialness=0.00
d_002 殉道与彼岸奖励   L2   0.30    essentialness=0.00
d_003 群体身份与异教徒  L2   0.30    essentialness=0.00
d_004 神圣化道德二元论 L2   0.18    essentialness=0.00
d_005 教义豁免机制     L2   0.18    essentialness=0.00
d_006 超验性普世使命   L2   0.36    essentialness=0.00
d_007 圣战与叛教者     L2   0.36    essentialness=0.00
d_008 仪式化暴力记忆   L2   0.36    essentialness=0.00

Lowest consistency: d_004 (0.18)
Weak chains: p_006, p_010
```

### s_705f0435 (mismatch) per-target

```
c_001 贸易谈判不确定性    L1   0.53    essentialness=0.09
c_002 技术竞争与人才壁垒   L1   0.50    essentialness=0.06
c_003 军事与地缘施压      L1   0.65    essentialness=0.12
d_001 中美双方战略目标分歧  L2   0.32    essentialness=0.00
d_002 前期关税与反制措施    L2   0.32    essentialness=0.00
d_003 美国国内政治周期     L2   0.32    essentialness=0.00
d_004 美国国家安全机构     L2   0.30    essentialness=0.00
d_005 美国高科技产业利益    L2   0.30    essentialness=0.00
d_006 中美经济脱钩共识     L2   0.40    essentialness=0.00
d_007 中美印太地缘竞争     L2   0.39    essentialness=0.00
d_008 美国国防战略转向     L2   0.52    essentialness=0.00

Lowest consistency: d_004 (0.30)
Weak chains: (无)
```

### s_7d491774 (hallucinated) per-target

```
c_001 宏观经济基本面疲弱   L1   0.56    essentialness=0.10
c_002 市场避险情绪蔓延     L1   0.30    essentialness=0.04
c_003 政策与外部不确定性   L1   0.33    essentialness=0.04
d_001 房地产行业持续低迷   L2   0.45    essentialness=0.00
d_002 地方政府债务压力     L2   0.34    essentialness=0.00
d_003 人口结构与劳动力     L2   0.34    essentialness=0.00
d_004 海外货币政策紧缩     L2   0.18    essentialness=0.00
d_005 国内经济数据不及预期 L2   0.24    essentialness=0.00
d_006 全球地缘政治对抗     L2   0.20    essentialness=0.00
d_007 国内政策制定周期     L2   0.20    essentialness=0.00
d_008 外部货币政策分化     L2   0.20    essentialness=0.00

Lowest consistency: d_004 (0.18)
Weak chains: p_005, p_007, p_013
```

### Multi-signal 跨 session 对比

| Signal | clean (s_f3beb777) | mismatch (s_705f0435) | hallucinated (s_7d491774) |
|---|---|---|---|
| avg consistency | 0.340 | 0.414 | 0.304 |
| L1 essentialness 跨度 | 0.03-0.04 (0.05) | 0.06-0.12 (0.12) | 0.04-0.10 (0.10) |
| weak_chains count | 2 | 0 | **3** |
| lowest L1 consistency | 0.30 | 0.50 | 0.30 (tied with clean) |
| lowest L2 consistency | 0.18 | 0.30 | 0.18 (tied with clean) |

**关键观察**: avg consistency 单一指标不足以拉开 hallucinated vs clean (0.304 vs 0.340, 差 0.036). 但 multi-signal 中 weak_chains count (3 > 2 > 0) 对方向区分非常清晰, 且 mismatch 的高 essentialness 跨度 (0.12) 反映 driver 之间分化大 — 暴露 mismatch 是另一种 failure mode.

## Wave B 数据 — s_e8a2a3a2 forward predict smoke

Session root: "为什么后现代主义在最近两年逐渐兴起". 三个 query 跑法:

### Query 1: "后现代主义的蔓延对于下一代年轻人会有什么样的影响"

- Parser: existing_refs=[], new_concepts=[c_005 后现代主义蔓延 (L1), c_006 年轻一代的认知与行为变迁 (L1)]
- Generation: 5 predicted L0 (p_017-p_021)
  - "现实转移社群 / 后讽刺沟通 / 自助式灵性 / 表演式激进 / 末日乐观表情包"
- HITL: 4/5 accepted (p_023 rejected)
- ✅ Wave B B3 generative path 完整跑通

### Query 2: "后现代主义对于经济会有什么样的影响"

- Parser: existing_refs=[c_005], new_concepts=[]
- B1 退化路径 — propagate from c_005 only, 下游 propagation 全 speculation 被 filter
- 输出 fallback message (Wave B fix2): "无明显 effect, parser 识别 c_005 但下游全被 filter"
- ✅ Wave B fix2 (parser transparency + 空结果 fallback) 工作

### Query 3: "其未来可能会发展出什么新的哲学或者心理学思潮吗"

- Parser: existing_refs=[c_005], new_concepts=[c_007 后后现代主义 (L1)]
- Generation: 5 predicted L0 (p_022-p_026)
  - "新真诚文学 / 科学共识可视化平台 / 跨代际家庭公社 / 真实性验证徽章 / 意义疗法 APP"
- ✅ Parser smart 识别 (拒收 L0) + 生成 forward concept

**Counterfactual smoke** (额外验证, 跟 ForwardPrediction 共 80% mechanics): 从 Wave B.3 commit 测试 fixture 覆盖 substitute / remove 两种 mode, user-facing CLI `explain counterfactual` 跑通无副作用.

## Wave C 数据 — s_4c5f717d reflection 闭环演进

Session root: "说一下存储成为热点之后对于上游产业的影响". 用户跑 `explain run` 多轮观察 reflection action 演进.

### 第 1 轮 (Wave C.2 落地后, 无 anti-thrash)

- tick 0-2: expand c_001/c_002/c_003 ✓
- tick 3-13: reflect 11 次连续 re-expand c_003
- root cause: re_expand 加 incoming causes 不修 outgoing manifests_as 测的 consistency → 同 target 反复触发

### 第 2 轮 (同 v0)

- tick 0-4: 仍 reflect re-expand c_003 (5 次)
- 共 17 连续 re-expand 同一 target → graph 膨胀到 39 d_NNN

### 第 3 轮 (Wave C 补丁 1: concurrent score_all + 补丁 2 v1 anti-thrash consecutive)

- tick 0-4: prune d_001 ✓ / re-expand c_003 / re-expand c_003 / prune d_002 ✓ / re-expand c_003
- 3/5 仍 leak (consecutive 语义被 prune 重置, anti-thrash 逻辑 false negative)
- 补丁 1 实测: mock test 测 5x speedup on compress (并发 score_all)

### 第 4 轮 (Wave C 补丁 2 v2 anti-thrash occurrence-window)

- tick 0-4: prune d_004 ✓ / prune d_006 ✓ / prune d_007 ✓ / re-expand c_003 (1/5 leak, acceptable) / prune d_008 ✓
- 4/5 productive cleanup, leak rate 从 100% → 20%

✅ Wave C reflection 完整闭环触发: re-expand / prune / stop signal 全部触发过. anti-thrash occurrence-window 语义有效.

## Wave D 数据 — 测试 / 稳定性 / 兼容性

| 指标 | Phase 6 baseline | Phase 7 实测 | Verdict |
|---|---|---|---|
| 测试通过 | 276 | **389 PASS** (+113) | ✅ |
| ruff errors | 0 | 0 (throughout) | ✅ |
| 向后兼容 (Phase 5/6/7 中间产物 session JSON) | n/a | 8 个 session 全部加载正常 | ✅ |
| LLM cost | 0 (Phase 6 pure rule-based) | rescore ~25 call/session × 3 + Wave B/C smoke ~50 call ≈ 125 call total | OK |

## Acceptance verdict — PARTIAL PASS

跟 design §7.3.2 criterion 对照:

| Criterion | 设计期望 | 实测 | Verdict |
|---|---|---|---|
| Wave A 真信号化 (不再 placeholder 0.70/0.42) | 是 | 是 (跨度 0.18-0.65) | ✅ |
| s_7d491774 < s_f3beb777 avg 方向对 | 是 | 0.304 < 0.340 | ✅ |
| avg consistency 差 ≥ 0.15 | 0.15 | 0.036 | ❌ FAIL (strict), ✅ direction |
| Multi-signal weak_chains count 区分 | hallucinated 最多 | 3 > 2 > 0 | ✅ |
| Multi-signal lowest L1 区分 | hallucinated 最低 | tied (clean=hall=0.30) | ⚠️ tied |
| Essentialness 跨度 ≥ 0.1 (per session) | 0.1 | 0.05 / 0.12 / 0.10 | ⚠️ Mixed (1 fail / 1 pass / 1 borderline) |
| Wave B predict / counterfactual smoke | 跑通 + ≥1 accept | 4-5/5 accept | ✅ |
| Wave C reflection action ≠ continue | ≥ 1 触发 | re-expand × N + prune × N + stop | ✅ |
| Wave C anti-thrash 防死循环 | 不 infinite loop | 17→20% leak | ✅ |
| Tests 350+ PASS | 350+ | 389 PASS | ✅ |
| ruff 0 | 0 | 0 | ✅ |
| 向后兼容 | 不破 | 8 session 加载 OK | ✅ |

12 项: 9 ✅ + 2 ⚠️ + 1 ❌ → **PARTIAL PASS**

**关键 finding**: Wave A 数学差距小于设计阈值 (0.036 < 0.15) 是 root issue. 单 LLM-mechanism-scoring signal 不够强区分 hallucinated vs clean — LLM 评分偏 generous, 集中 3-4 区间. Multi-signal validation (weak_chains count + spread) 工作良好, 应作为 Phase 8 official acceptance signal.

## 反直觉发现 — Mismatch session 反而 avg consistency 最高

s_705f0435 (特朗普访华, mismatch 议题) avg = **0.414**, 高于 clean 跟 hallucinated.

**根因**: 强行走 backward-explanation pipeline 时 graph 内部 mechanism coherent — LLM 觉得 "driver→abstract→concrete" 链条说得通, 但 graph 整体在 "回答错的问题". 单 mechanism scoring 检测 "weak mechanism" 有效, 检测 "wrong question" 类型的 mismatch 无效.

**Phase 8 motivation**: 需要 question-graph alignment signal — 例如 root_question semantic embedding vs graph centrality, 或 root → leaves 的 chain coherence reduction. 不能仅靠 mechanism plausibility.

## Tune 决策

**不改阈值或 prompt** — Wave D acceptance fixture 不动. 理由:

1. **Wave A 数学差距小 (0.036) 是 LLM 评分 generous 问题, 不是阈值问题**. 调阈值 (e.g. WEAK_CHAIN_THRESHOLD ↑) 只是 cosmetic, 不解决 root cause.
2. **Multi-signal 已 work** — weak_chains count 给了 3>2>0 干净信号. Phase 8 应当把 multi-signal 写进 acceptance criterion, 而不是 tune 单一 avg consistency.
3. **反直觉 mismatch finding 跟 tuning 正交** — question-graph alignment 是 schema 层新增 signal, 不是 propagation 阈值的 tweak.

## Phase 8 起点 — 4 个具体方向

按 ROI 排序:

**方向 1: Multi-signal acceptance** — 把 weak_chains count + lowest L1 spread + essentialness spread 写进 simulation report 的 verdict, acceptance criterion 不再仅看 avg consistency. 当前 §7.3.2 单指标设计需 revise.

**方向 2: Question-graph alignment signal** — 加 root_question embedding vs graph (leaves / centrality) similarity 度量, 检测 mismatch (s_705f0435 case). 可能复用 Phase 5 LLM client 做 embedding, 或加 LLM-self-judge "graph 是否回答了 root question".

**方向 3: Reflect 重设计** — 当前 re_expand 加 incoming causes 不修 outgoing manifests_as 测的 consistency. 应分两 mode:
- "outgoing leak" (consistency 低): re-generate manifests_as L0 children
- "incoming missing" (essentialness 低): 加 driver L2 (现行为)
否则 re-expand 进死循环 (Wave C 第 1-2 轮 17 连续 case).

**方向 4: Variable lifecycle 持久化** — design §9.1 lifecycle (Active / Decay / Death / Rebirth), 当前 prune 只删 graph, 不留 stability_score 历史. Phase 8 应加 variable.lifecycle field + 跨 session/round persistence, 让 Reflection Engine 看历史决定 prune.

---

## 附录 A: Wave C 补丁 1+2 总结

### 补丁 1 — evaluation.score_all 并发 + CLI 进度 logging (commit 528c994)

- score_all 改 ThreadPoolExecutor 并发评分 (5x speedup on compress, mock test 验证)
- CLI 加进度 logging (long compress 不再静默)

### 补丁 2 v1 — reflect re-expand anti-thrash consecutive (commit 480a609)

- 同 target 连续 re_expand 超阈值 → fallback prune
- v1 用 consecutive 语义 → prune 重置计数器, 真实场景下漏触发 (3/5 leak)

### 补丁 2 v2 — anti-thrash occurrence-in-window (commit 769972a)

- 改 occurrence-in-recent-N 语义, prune 不重置
- 实测 leak 从 100% → 20%, 4/5 productive cleanup

## 附录 B: Phase 7 工程总结

### Commits (按 Wave / Task)

- Wave A: 143d0e6 (rescore CLI + engine), c5f2950 (rescore real-engine integration tests + TODO + save-fail test)
- Wave B: e790759 (B.1 IntentParser), 05b527d (B.2 ForwardPrediction + CLI + HITL), e790759 (B.2 fix), 904a623 (B.3 CounterfactualEngine + CLI), 2087d33 (B.4 shared helpers), 47eb561 (B fix mutation snapshot + names + defensive branch), 9bd083c (B fix2 parser 拒 L0 + transparency + fallback)
- Wave C: 74a3294 (C.1 ReflectionEngine.reflect), 19ca31b (C.1 fix prune symmetry tests), be0c2ff (C.2 re_expand + scheduler reflect + runtime branch), 31d08ca (C.3 stop signal + C.2 follow-ups), 528c994 (补丁 1 并发), 480a609 (补丁 2 v1), 769972a (补丁 2 v2)
- Wave D: (本 commit) acceptance evidence + README

### 测试增量

- Phase 6 baseline: 276 PASS
- Phase 7 final: **389 PASS** (+113)
  - IntentParser: ~10
  - ForwardPredictionEngine + HITL: ~25
  - CounterfactualEngine: ~20
  - ReflectionEngine + reflect/scheduler/runtime: ~30
  - RescoreEngine + CLI integration: ~15
  - Shared propagation helpers + 杂项: ~13
- ruff: 0 errors throughout

### LLM cost

- rescore: ~25 call/session × 3 session = 75 call
- Wave B predict smoke: 3 query × ~10 call ≈ 30 call
- Wave C reflection smoke: ~20 call (4 轮)
- Total: **≈ 125 LLM call**, 跟 Phase 6 (0 call) 对照, Phase 7 是首次大规模 LLM-driven acceptance
