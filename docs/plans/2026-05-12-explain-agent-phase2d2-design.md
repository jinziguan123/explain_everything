# Explain Agent · Phase 2.D-2 设计文档

> 把 Phase 2.D-1 跑出来的 13 分钟黑盒体验，改成 5 分钟透明可观测；同时加漂移率标注工具，为 Phase 3 决策积累真实数据。

**前置：** Phase 2.D-1 已完工（commit `55483f4`），`fan_out` 名义并发实际串行（306s = 6 维耗时简单相加），REPL 完全黑盒。

**Phase 2.D-2 = 三件互不依赖的工作打包：**
- **C：fan_out 真并发化**（核心，改 LLMClient + 全链路 await）
- **A：漂移率标注工具**（轻量，schema + 2 个 REPL 命令）
- **O：REPL 进度可观测性**（3 行回调，让用户看到系统在干什么）

---

## 设计哲学

三件事**共享同一个底层动机：让用户愿意日常用**。
- 性能不优化 → 13 分钟没人愿等
- 黑盒 → 等 13 分钟看不到进度，崩溃
- 没标注工具 → 用了也白用，攒不到漂移率数据，触发不了 Phase 3

三件事打包成一个 sprint，共享一次 smoke 验收。

---

## C 部分：fan_out 真并发化

### C.1 根因诊断

[fan_out_dimensions.py:15-27](../../src/explain_agent/graph/nodes/fan_out_dimensions.py:15) 写了 `Semaphore(3)` + `asyncio.gather`，但实际全部串行：

**证据**（Phase 2.D-1 smoke `s_17f25967`）：6 维耗时 `196+23+22+23+28+14 = 306s` ≈ fan_out 总耗时 `306.4s`。

**根因**：[dimension_worker.py:159](../../src/explain_agent/graph/dimension_worker.py:159) `raw = self.llm.chat(...)` 是 sync HTTP 阻塞调用，没有 await。整条 `worker.run` 虽挂 `async def`，但内部全 sync。第一个协程进入 semaphore 后 event loop 完全被它占住，其他 5 个无法插入。Semaphore 形同虚设。

### C.2 LLMClient 双 API 设计

**Protocol 升级**（[client.py:9](../../src/explain_agent/llm/client.py:9)）：

```python
class LLMClient(Protocol):
    cfg: LLMConfig
    def chat(self, system: str, user: str, max_tokens: int = 1024) -> str: ...
    async def achat(self, system: str, user: str, max_tokens: int = 1024) -> str: ...
```

**AnthropicClient / OpenAIClient 改造**：

```python
class AnthropicClient:
    def __init__(self, cfg, sdk_client=None, async_sdk_client=None):
        self.cfg = cfg
        self.sdk = sdk_client or Anthropic(api_key=cfg.api_key, base_url=cfg.base_url)
        self._async_sdk = async_sdk_client  # lazy

    def chat(self, ...) -> str:  # 原实现不变
        ...

    async def achat(self, ...) -> str:
        if self._async_sdk is None:
            self._async_sdk = AsyncAnthropic(api_key=..., base_url=...)
        resp = await self._async_sdk.messages.create(...)
        ...
```

OpenAI 同形（用 `AsyncOpenAI`）。

**关键决策**：

| 决策 | 值 | 理由 |
|---|---|---|
| sync / async 是否共享底层 HTTP client | 不共享 | Anthropic / OpenAI 的 sync 和 async SDK 是独立类 |
| async client 何时创建 | lazy | 避免 ingest 用 LLMClient 时也强制建 async 连接 |
| 重试 / 错误处理 | 不在 client 层，留给 caller | 现状 `_call_with_retry` 已有 retry，重复加冲突 |
| 命名 | `chat` (sync) / `achat` (async) | Python 社区标准（`redis-py` / `httpx` 同款） |

### C.3 9 个 caller 改造清单

| # | 文件 | 行 | 改动 |
|---|---|---|---|
| 1-3 | dimension_worker.py | 159 / 192 / 202 | `_gen_keywords` / `_is_sufficient` / `_summarize` 改 async + await achat |
| 4-5 | connection_explorer.py | 81 / 147 | `chat` → `await achat` |
| 6 | followup.py | 61 | `chat` → `await achat` |
| 7 | report_builder.py | 17 | `_call_with_retry` 改 async + 内部 `time.sleep` → `await asyncio.sleep` |
| 8 | parse_question.py | 45 | `chat` → `await achat` |
| 9 | synthesizer.py | 50 | `chat` → `await achat` |

**framework YAML**：[cn_equity_sector_attribution.yaml:66](../../frameworks/cn_equity_sector_attribution.yaml:66) `max_concurrency: 3 → 6`。6 维已是天然上限。

**保持 max_rounds=10 不变**：本期只解 async bug，不动判定逻辑。

### C.4 fan_out 异常隔离

[fan_out_dimensions.py:27](../../src/explain_agent/graph/nodes/fan_out_dimensions.py:27) `return_exceptions=False` → `True`，并加异常处理：

```python
results_or_errors = await asyncio.gather(*[run_one(d) for d in dims], return_exceptions=True)
results = []
for dim_cfg, r in zip(dims, results_or_errors):
    if isinstance(r, Exception):
        results.append((dim_cfg["id"], DimensionResult(
            evidence=[], mini_summary=f"维度 worker 失败: {r!r}",
            retry_count=0, no_data=True, confidence="low",
        )))
    else:
        results.append(r)
```

修复 Phase 2.D-1 隐藏 bug：1 个维度抛异常会让整个 fan_out 崩溃。

---

## A 部分：漂移率标注工具

### A.1 数据 schema

新增 `migrations/mysql/003_explain_annotation.sql`：

```sql
USE explain_agent;

CREATE TABLE IF NOT EXISTS explain_annotation (
  annotation_id    VARCHAR(64)  NOT NULL,
  session_id       VARCHAR(64)  NOT NULL,
  thread_index     INT          NOT NULL,           -- 数组下标 (0/1/2)
  thread_title     VARCHAR(256) NOT NULL,           -- 冗余存便于查询
  label            VARCHAR(16)  NOT NULL,           -- 'green' | 'yellow' | 'red'
  note             TEXT         DEFAULT NULL,
  created_at       DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (annotation_id),
  KEY idx_session (session_id),
  KEY idx_label (label),
  UNIQUE KEY uk_session_thread (session_id, thread_index)
) ENGINE=InnoDB;
```

**关键决策**：
- `UNIQUE (session_id, thread_index)`：单 thread 唯一标签，避免重复标扭曲统计
- `thread_index` 而非稳定 `thread_id`：connection_threads 是数组，用下标既简单又能去重
- `thread_title` 冗余：让 stats 查询不必 JOIN tree_json

### A.2 `/annotate` 命令

**交互流程**：
```
explain> /annotate

▎ 当前 session: s_17f25967
▎ Connection threads (2 条):

[1/2] 玻璃基板封装技术商业化加速与国产供应链机遇
      source=web, confidence=4
      内容: 当前玻璃基板封装商业化正在加速...
      标签? (g=🟢真知灼见 / y=🟡合理但平庸 / r=🔴漂移 / s=skip): g
      备注 (回车跳过): 联想到了 TGV 论坛, 之前不知道
      ✓ 已标记

[2/2] 汽车半导体高景气下国产车规级芯片放量持续性验证
      ...
      标签? (g/y/r/s): y
      ✓ 已标记

完成。session s_17f25967: 1 🟢 / 1 🟡 / 0 🔴
```

**逻辑**：
1. 读 `state.current_session` 拿 connection_threads
2. 查 `explain_annotation` 看哪些已标过 → 跳过
3. 依次提示，单条 INSERT 实时落库（断网不丢）
4. 无 active session 时报错并返回

### A.3 `/stats` 命令

```
explain> /stats

▎ Connection threads 漂移率统计 (基于 23 个已标注 thread):

  🟢 真知灼见       9   39.1%
  🟡 合理但平庸     11  47.8%
  🔴 漂移          3   13.0%

  涉及 session 数: 12
  最近 5 个标记的 🔴 议题:
    - s_a1b2: "区块链与白酒消费的相关性"
    - ...

判断: 🔴 < 20% → Phase 3-A 不必启动 Phase 3-B
```

**Phase 3 路线图阈值**：🔴 < 20% / 20-30% / >30%（来自 [phase3-roadmap.md](2026-05-11-explain-agent-phase3-roadmap.md)）。

---

## O 部分：REPL 进度可观测性

### O.1 现状

钩子早就有，REPL 只是没接：

| 组件 | 钩子 | smoke | REPL |
|---|---|---|---|
| `build_main_graph` | `on_node_event` | ✅ | ❌ |
| `DimensionWorker` | `on_done` / `on_round` | ✅ | ❌ |

[scripts/run_main_graph_smoke.py:73-89](../../scripts/run_main_graph_smoke.py:73) 把回调全填了，所以 smoke 透明；[loop.py:_build_environment](../../src/explain_agent/cli/repl/loop.py:84) 完全没传，所以 REPL 黑盒。

### O.2 改动

**唯一改动**：`_build_environment()` 加 3 个回调（直接复用 smoke 实现），传给 `worker_factory` / `build_main_graph`。

```python
def on_dim_done(dim_id, duration, no_data, retry_count, evidence_count): ...
def on_dim_round(dim_id, round_idx, max_rounds, keywords, ...): ...
def on_node_event(event, name, *args): ...

def worker_factory(...):
    return DimensionWorker(..., on_done=on_dim_done, on_round=on_dim_round)

graph = build_main_graph(..., on_node_event=on_node_event)
```

### O.3 用户看到的样子

fan_out 优化后，**6 维 round 1 几乎同时打印（≤2 秒间隔）** —— 这正是真并发的可见证据。

### O.4 不做的事

| 想法 | 为啥不做 |
|---|---|
| `/quiet` 命令切换 verbose | YAGNI。优化到 5min 后 verbose 仍是合理默认 |
| Rich Live progress / spinner | 抹平了 6 维并行的"涌现感" |
| followup 加 spinner | followup 几秒出结果，价值低 |
| 实时显示 mini_summary | 截获要破坏抽象 |
| LLM 调用次数 / 估算成本 | 独立可观测性话题，留给单独 sprint |

---

## State Schema 变更

无（A 部分新增表 `explain_annotation` 是独立表，不动 state）。

---

## 失败模式与回退

| 故障 | 回退 |
|---|---|
| `AsyncAnthropic / AsyncOpenAI` 调用失败 | 抛异常给 caller。Caller 已有 try/except 兜底 |
| `_call_with_retry` 3 次都失败 | 返回 `""`（同当前），report_builder 走 fallback |
| fan_out 1-2 个 worker 抛异常 | 改 `return_exceptions=True` + 异常 worker 设 `no_data=True`，不中断 fan_out |
| 标注 INSERT 失败 | 单条 try/except，打印错误后继续下一条 |
| `/annotate` 时无 connection_threads | 提示后直接返回 |
| `/annotate` 时 Ctrl-C | 已标的不回滚（实时 INSERT），下次 `/annotate` 自动跳过 |

---

## 测试策略

### C 部分

**改造**：35 处 `fake_llm.chat` 测试 mock → `AsyncMock(achat)`，分布在 9 个测试文件（`test_node_*.py` / `test_followup.py` / `test_dimension_worker.py` / `test_main_graph.py`）。

**新增**：
- `test_llm_client.py` +2：`achat` lazy init + AsyncOpenAI 同形
- `test_node_fan_out.py` +1：`test_fan_out_runs_dimensions_concurrently`（6 维 sleep 0.5s，总耗时 < 1.5s 而非 3s）— **真并发护栏**
- `test_node_fan_out.py` +1：`test_fan_out_isolates_dimension_failure` — **异常隔离护栏**

### A 部分

`test_repl_commands.py` +3：
- `test_annotate_lists_threads_and_writes_db`
- `test_annotate_skips_already_annotated`
- `test_stats_groups_by_label_with_phase3_recommendation`

### O 部分

无新增（回调由 smoke 验收）。

### 总测试数

```
Phase 2.D-1:  115 passed
本期改造:    9 处文件保持
本期新增:    +2 (achat) +2 (fan_out 护栏) +3 (annotate)
预期终态:    ~122 passed
```

---

## 验收标准

### 单测

全部通过（≥122 passed），含 fan_out 真并发护栏 + 异常隔离护栏。

### 端到端 smoke

跑 `"为什么半导体板块今天涨"`：

| 指标 | 2.D-1 基线 | 2.D-2 目标 |
|---|---|---|
| fan_out 总耗时 | 306s | **≤ 120s** |
| 总耗时 | ~13 min | **≤ 5 min** |
| 6 维 evidence 数 | 不退化 | 同等量级（±10%） |
| connection_threads 数 | 2 | 不退化 |
| narrative_claims 数 | 4 | 不退化 |

### REPL 验收

`uv run explain` 跑同样的问题，能看到节点级 + 维度级进度。**关键**：fan_out 期间 6 维 round 1 几乎同时打印（≤2 秒间隔），证明真并发。

### 标注工具手动验收

跑完 smoke 后在 REPL 里 `/annotate` 给 2 个 thread 打标 → `/stats` 看输出 → DB 查到 2 条 `explain_annotation` 记录。

---

## 任务总览

| # | 任务 | 预估 |
|---|---|---|
| 1 | LLMClient 加 achat（AnthropicClient + OpenAIClient + 2 单测） | 50 min |
| 2 | dimension_worker 3 处 chat → achat + 测试 mock 改造 | 40 min |
| 3 | parse_question / synthesizer / followup / connection_explorer / report_builder 5 处 chat → achat + 测试 mock 改造 | 60 min |
| 4 | report_builder._call_with_retry 改 async + sleep 改 asyncio.sleep | 20 min |
| 5 | fan_out 改 `return_exceptions=True` + 异常隔离 + 单测 | 30 min |
| 6 | framework YAML max_concurrency 3 → 6 | 5 min |
| 7 | fan_out 真并发护栏单测 | 30 min |
| 8 | 003_explain_annotation.sql migration | 15 min |
| 9 | `/annotate` 命令实现 + 3 单测 | 90 min |
| 10 | `/stats` 命令实现 + 测试 | 40 min |
| 11 | REPL `_build_environment` 加 3 个回调 | 25 min |
| 12 | smoke + REPL 端到端验收 + README 更新 | 60 min |

**合计：约 7 小时纯开发，预计 1 个工作日完成。**

---

## 完成后

Phase 2.D-2 完成后的产出：

- ✅ fan_out 真并发，6 维同时跑（306s → ≤120s）
- ✅ 总耗时从 13min → ≤5min，达到日常用的可接受水平
- ✅ REPL 透明可观测，6 维并发能看到"涌现感"
- ✅ `LLMClient` 双 API（sync 留给 ingest，async 给 graph 链路）
- ✅ fan_out 异常隔离（修隐藏 bug）
- ✅ 漂移率标注闭环（`/annotate` + `/stats`）

**下一步**：日常用 explain 攒 20-30 个 case 的 connection_threads 漂移率数据 → 触发 Phase 3-A（接受度高）或 Phase 3-B（漂移率 ≥ 30% 时启用学科正交度地图引导）。
