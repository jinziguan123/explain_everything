# Phase 16: Theory Formation 设计

**Date**: 2026-05-21
**Status**: brainstorm 完, 待 implementation plan
**Branch**: dev
**Related**:
- Phase 10 (Persistent World Model — 跨 session Variable Lexicon)
- Phase 13 (Variable Embedding — BGE-M3 cosine merge)
- Phase 14 (Chat Stage Flow + Hints — 5 slash 推 stage)
- Phase 15 (Chat REPL 去技术化 — `chat_copy.py` 文案 single source)
- 最终哲学.md §9 (Theory Formation Philosophy) / §13 (Theory Formation Engine)
- 技术设计v2.md §5 (Persistent World Model) / §13 (Variable Evolution)
- JEPA 调研 (LeCun 2022 + I-JEPA / V-JEPA / V-JEPA 2)

## 1. Motivation

Phase 0-15 落地了:
- 单 session 的 explanation graph 推理 (Phase 5/7/8)
- chat REPL 体验 (Phase 9/11/12/14/15)
- 跨 session lexicon 累积 (Phase 10)
- variable 语义级 dedup (Phase 13 embedding)

但 lexicon 只是"概念库", **不是 "world model"**. 哲学v2 §5:
> V1 graph: session-local. V2 graph: persistent world model. 系统开始长期累积 variables / **relations / mechanisms / abstractions**.

当前**缺**:
- **跨 session 因果关系**反复出现的识别 — lexicon 只存 variable, 不存"模式"
- "theory" 概念的 reify — 哲学 §9.1: theory 不是答案, 是**稳定生成结构**
- **可证伪的预测能力** — 哲学 §9.4: theory 必须可失败, 否则系统神学化
- **多样性保护** — V2 §6 防 variable / theory collapse

Phase 16 目标: 把 lexicon 升级为真"world model" — 跨 session graph 中 emergent 出 recurring motif, 抽成 stable theory, 接到 reasoning loop 做 predictive prior。

## 2. Goals

1. **Cross-session motif detection**: 跨 N session graph union, 找 frequent 因果结构 (chain / star / cycle).
2. **Theory dataclass + persistence**: `theories.json` sidecar 跟 lexicon 同目录, atomic write, lazy cached.
3. **Falsifiability scoring**: leave-one-session-out predict — theory 能否 predict 未见 session 的现象 (JEPA 启示 a, echo 哲学 §9.4).
4. **Stable / Tentative bank**: 跨 stability window 一致才 promote 到 stable, 防 single-session 极端 reasoning 带偏 (JEPA 启示 b).
5. **MMR diversity ranking**: top-K 防同 theme 重复 paraphrase (JEPA 启示 c).
6. **Bootstrap inject**: chat REPL 输自然语言新问题时, prompt 加 stable theory 段落, 软引导 LLM 复用已知 mechanism.
7. **User-facing inspection + feedback**: chat slash `/theories` `/theory <id>` `/theory <id> reject`, cli `explain theories`.

## 3. Non-Goals

- **重设 L0/L1/L2 语义** (JEPA 启示 d 的 H-JEPA hierarchical 时间尺度对齐) — defer Phase 17.
- **Theory competition** (哲学 §9.3) — 多 theory 间互斥竞争, scope 大, defer.
- **Multi-perspective theory** (技术v2 §7) — perspective dimension 是另一 phase.
- **真正 Self-supervised continuous learning** — JEPA 是 NN, 我们是 LLM-driven symbolic, paradigm 不同.
- **Theory editing UI** (用户改 theory 内容) — 只支持 reject, 不支持 edit.
- **Theory 跨 project 共享** — 当前 scope per-project, project 间隔离.
- **gSpan 学术完整实现** (cycle detection 复杂 trick) — MVP 简化为 chain/star + 基本 cycle.

## 4. JEPA 调研启示采纳

JEPA (Joint Embedding Predictive Architecture, LeCun 2022) 是 self-supervised continuous-latent 路线, 跟 explain engine (LLM-driven symbolic-graph) 底层 paradigm 完全相反. 但**深层 design philosophy 同源**: 都拒绝在 surface space 复读, 都信"压出 hidden 变量才是 intelligence".

### 采纳 (a)(b)(c), defer (d)

| JEPA 启示 | 本 phase 落地 | Echo 项目哲学 |
|---|---|---|
| **(a) prediction-as-loss** | `Theory.predictive_power` 字段 + leave-one-session-out evaluation + ranking 公式占权重 0.45 | 哲学 §9.4 可证伪性 |
| **(b) slow-fast / EMA target encoder** | `theories.json` 拆 `stable_theories` (跨 K=5 session window 持续) + `tentative_theories`, bootstrap inject 只用 stable | 防 V2 §6.3 "variable collapse" 的 theory 版 |
| **(c) VICReg variance** | ranking 加 MMR diversity penalty (λ=0.7), 防 top-K 同 theme paraphrase | V2 §10.3 "防 variable collapse" |
| (d) H-JEPA hierarchical | **defer Phase 17** (改 L2 语义需重设 expansion engine + acceptance + lifecycle, scope 过大) | 哲学 §9.1 |

### 根本异点 (保留 symbolic 护城河)

JEPA 的 latent 不可被人解释 / 不可被人编辑. Explain engine 比 JEPA 多了"可向人解释" + "可被人 reject" 两条 — 这是 symbolic 路线的护城河, paradigm 不换. 借的是 design pattern, 不是 NN 实现.

## 5. Design

### 5.1 Architecture overview

```
新 module: src/explain_engine/engines/theory/
├── __init__.py
├── theory.py              # Theory + Theme dataclass
├── clustering.py          # Phase 13 embedding cluster → theme groups
├── gspan.py               # 自实现 simplified gSpan (~300 行)
├── motif_mining.py        # per-theme subgraph + 调 gspan_mine
├── falsifiability.py      # leave-one-session-out predictive_power
├── ranking.py             # compute_score + MMR + promote stable/tentative
├── cache.py               # get_active_theories + reject_theory
├── recompute.py           # _recompute_all 7-step orchestrator
└── loader.py              # load_all_session_graphs

新 persistence:
~/.explain/projects/<proj>/knowledge/theories.json   ← cache + rejected list

改 chat (slash_commands.py + chat_copy.py + ephemeral.py):
+ _handle_theories / _handle_theory + 2 SlashCommand 注册
+ COMMAND_DESCRIPTIONS / HELP_GROUPS_ZH / STATUS_*/msg_*/err_* entries
+ promote_to_persistent 加 theory inject

改 cli (cli.py): + theories() subcommand

改 engines (bootstrap.py): propose_phenomena 加 theories 参数
```

**核心数据流**:
```
session 1,2,3,... sidecar (graph.json)
   ↓ (lazy 按需加载)
clustering.cluster_lexicon_themes(embedder, lexicon)
   ↓
N theme (cosine ≥ 0.85)
   ↓ (per theme)
motif_mining.find_motifs_per_theme + gspan_mine
   ↓
raw motif list (chain / star / cycle)
   ↓
falsifiability.evaluate_predictive_power (leave-one-out)
   ↓
ranking.maybe_promote_to_stable + rank_topk_with_mmr
   ↓
Theory list (cache to theories.json)
   ↓
bootstrap prompt inject (stable only) / /theories display
```

### 5.2 Data Model

#### `Theory` dataclass (`engines/theory/theory.py`)

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class Theme:
    id: str                          # e.g. "th_001"
    name: str                        # 取 cluster 内 centroid 最近 variable.name
    member_global_ids: tuple[str, ...]
    centroid_summary: str

@dataclass(frozen=True)
class Theory:
    id: str                          # 稳定 hash, 见 _compute_theory_id
    motif_type: Literal["chain", "star", "cycle"]
    theme_ids: tuple[str, ...]
    node_ids: tuple[str, ...]        # lexicon variable global_ids
    edges: tuple[tuple[str, str, str], ...]  # (src_gid, tgt_gid, relation_type)
    supporting_sessions: tuple[str, ...]
    natural_language_summary: str    # " → ".join(theme_names)
    structure_complexity: int        # node count (3-5)
    first_seen_session: str
    last_seen_session: str
    # JEPA (a) — falsifiability
    predictive_power: float = 0.0
    """leave-one-session-out 评估: 用 N-1 session 构造 theory, 看 motif nodes
    在第 N session L0 phenomena 中能否找到 cosine ≥ 0.85 match.
    predictive_power = 命中 session count / |supporting_sessions|"""
    # JEPA (b) — slow-fast
    stability_status: Literal["tentative", "stable"] = "tentative"
    stable_promoted_at_session: str | None = None
```

#### `_compute_theory_id` (稳定 hash)

```python
def _compute_theory_id(motif_type: str, edges: tuple) -> str:
    canonical = f"{motif_type}:{tuple(sorted(edges))}"
    return "t_" + hashlib.sha256(canonical.encode()).hexdigest()[:10]
```

由 `(motif_type, sorted_edges)` 决定, 跨 recompute 稳定 — `rejected_theory_ids` 用 id 引用不失效.

#### `theories.json` schema

```json
{
  "version": "1.0",
  "computed_at": "2026-05-21T15:30:00Z",
  "session_ids_snapshot": ["s_aaa00001", "s_bbb00002", ...],
  "cold_start_threshold": 3,        // = max(3, N // 3)
  "stability_window_size": 5,       // K = 跨多少 session 看 stable

  "themes": [
    {"id": "th_001", "name": "不确定性",
     "member_global_ids": ["v_aaaaaaaa", "v_bbbbbbbb"],
     "centroid_summary": "..."}
  ],

  "tentative_theories": [
    {
      "id": "t_xxx", "motif_type": "chain",
      "theme_ids": ["th_001", "th_002"], "node_ids": [...], "edges": [...],
      "supporting_sessions": ["s_aaa", "s_bbb"],
      "natural_language_summary": "...",
      "structure_complexity": 3,
      "first_seen_session": "s_aaa", "last_seen_session": "s_bbb",
      "predictive_power": 0.45,
      "stability_status": "tentative"
    }
  ],

  "stable_theories": [
    {... "predictive_power": 0.82, "stability_status": "stable",
     "stable_promoted_at_session": "s_eee00005"}
  ],

  "rejected_theory_ids": ["t_zzz"]
}
```

设计:
- `themes` 跟 `theories` 同 file: 一次读一致 snapshot, 不需 cross-file 同步
- 数据量 < 50 KB (跟 lexicon 同量级)
- Atomic write: 临时文件 + rename (跟 Phase 10 lexicon 一致)

### 5.3 Algorithm Pipeline

#### Step 1: 加载 cross-session graphs (`loader.py`)

```python
def load_all_session_graphs(sids: list[str], storage: StorageV2) -> dict[str, ExplanationGraph]:
    """5 session < 1s, 50 session ~5s. lazy 平摊到 /theories 首次调用."""
    return {sid: SessionStore().load(sid).state.graph for sid in sids}
```

#### Step 2: Theme clustering (`clustering.py`)

```python
def cluster_lexicon_themes(
    lexicon: dict,
    embedder: BGE_M3_Embedder,
    cosine_threshold: float = 0.85,  # 跟 Phase 13 merge threshold 一致
) -> list[Theme]:
    """Union-find agglomerative clustering, O(N²) for N ≤ 100 var.
    Theme name = cluster centroid 最近 variable.name."""
```

复用 Phase 13 `BGE_M3_Embedder` singleton, 0 新依赖.

#### Step 3-4: Per-theme subgraph + motif mining (`motif_mining.py` + `gspan.py`)

```python
def find_motifs_per_theme(
    sessions: dict[str, ExplanationGraph],
    theme: Theme,
    min_freq: int,
) -> list[RawMotif]:
    # a) 抽 per-session theme subgraph (含 theme nodes + 一跳邻居)
    per_session_subgraph = [...]
    # b) 跑 gspan
    frequent = gspan_mine(
        graphs=per_session_subgraph,
        min_support=min_freq,
        min_size=2, max_size=5, is_directed=True,
    )
    # c) gSpan output → RawMotif (含 motif_type 分类: chain / star / cycle)
    return [...]
```

#### gSpan 自实现 (`gspan.py`)

**为什么自实现** (不用 `gspan-mining` 包):
- 实测 pandas 2.x 兼容性 `DataFrame.append()` 已删除 → run() 直接 crash
- API 是 CLI 设计 (传 file path), output 靠 print stdout, 拿不到 structured data
- 2020 后无 maintenance, 16 open issue 未处理
- 用户原意是 "用 gSpan algorithm" (合理), 不是 "用 betterenvi/gSpan 包". 自实现 ~300 行跟项目风格一致 (Phase 5/7 runtime 都自实现).

**简化** (vs 完整 gSpan):
- 仅支持 directed (我们 explanation graph 是 directed manifests_as/causes)
- in-memory API (不读 file)
- 不支持 disconnected motif / weighted edge

**算法纲要**:
```
def gspan_mine(graphs, min_support, min_size, max_size, is_directed):
    freq_1edges = _count_frequent_edges(graphs, min_support)
    all_frequent = []
    for seed_edge in freq_1edges:
        initial_code = [DFSEdge(0, 1, seed_edge)]
        _dfs_extend(initial_code, graphs, min_support, max_size, all_frequent)
    return [_decode_to_subgraph(code, graphs) for code in all_frequent]

def _dfs_extend(current_code, graphs, min_support, max_size, output):
    if not _is_minimum_dfs_code(current_code):
        return  # canonical check, 防重复枚举
    if _subgraph_size(current_code) >= min_size:
        output.append(current_code)
    if _subgraph_size(current_code) >= max_size:
        return
    candidates = _enumerate_rightmost_extensions(current_code, graphs)
    for ext in candidates:
        new_code = current_code + [ext]
        if _count_support(new_code, graphs) >= min_support:
            _dfs_extend(new_code, graphs, min_support, max_size, output)
```

实现要点:
- `DFSCode`: list of `(from_idx, to_idx, from_label, edge_label, to_label)` tuples
- `_is_minimum_dfs_code`: 跟所有 isomorphic DFS code 比, 取字典序最小 (Yan & Han 2002 §4.1)
- `_enumerate_rightmost_extensions`: 只扩 rightmost path 节点 (gSpan §4.2)
- `_count_support`: networkx `GraphMatcher` 跑子图同构

#### Step 5: Falsifiability evaluation (`falsifiability.py`) — JEPA (a)

```python
def evaluate_predictive_power(
    motif: RawMotif,
    all_sessions: dict[str, ExplanationGraph],
    embedder: BGE_M3_Embedder,
    match_threshold: float = 0.85,
) -> float:
    """对每个 supporting session s, 用其余构造 theory, 看 s 的 L0 phenomena
    能否被 motif nodes match (cosine ≥ 0.85). 命中至少 1 个 → 算 predict 成功.
    predictive_power = 命中 / |supporting_sessions|.
    """
```

支持 < 2 session → 返 0.0 (不能 leave-one-out). cost: O(supporting × motif_nodes × held_l0) cosine, 5 session × 4 node × 10 L0 ≈ 几百次 cosine, < 100ms (embedding cached).

#### Step 6: Promote stable/tentative (`ranking.py`) — JEPA (b)

```python
def maybe_promote_to_stable(theory: Theory, all_sessions: list[str], window_size: int) -> bool:
    """跨最近 window_size session 至少 ⌈window/2⌉+1 个有此 theory → stable."""
    recent_window = set(all_sessions[-window_size:])
    overlap = recent_window & set(theory.supporting_sessions)
    return len(overlap) >= (window_size // 2 + 1)
```

简化版 (真 EMA 在 NN 才有, symbolic 用 window 即可).

#### Step 7: MMR ranking (`ranking.py`) — JEPA (c)

```python
def compute_score(theory: Theory, n_sessions_total: int) -> float:
    freq = len(theory.supporting_sessions) / n_sessions_total
    complexity = min(theory.structure_complexity, 5) / 5.0
    return (
        0.35 * freq                          # 频次
      + 0.20 * complexity                    # 结构 (3-node > 2-node)
      + 0.45 * theory.predictive_power       # JEPA (a) falsifiability 最重
    )

def rank_topk_with_mmr(theories, k=20, λ=0.7) -> list[Theory]:
    """JEPA (c) — VICReg-style diversity. λ=0.7 偏 relevance, 0.3 偏 diversity."""
    selected = []
    pool = sorted(theories, key=lambda t: -compute_score(t, ...))
    while len(selected) < k and pool:
        if not selected:
            selected.append(pool.pop(0))
            continue
        best = max(pool, key=lambda t:
            λ * compute_score(t, ...)
          - (1-λ) * max(theme_overlap(t, s) for s in selected)
        )
        selected.append(best); pool.remove(best)
    return selected

def theme_overlap(t1: Theory, t2: Theory) -> float:
    s1, s2 = set(t1.theme_ids), set(t2.theme_ids)
    return len(s1 & s2) / max(len(s1 | s2), 1)
```

### 5.4 Cache / Persistence / Cold start

#### Cache API (`cache.py`)

```python
def get_active_theories(
    storage: StorageV2,
    embedder: BGE_M3_Embedder | None,
    *, force_recompute: bool = False,
) -> TheoriesCache:
    """Single source 入口 — chat / cli / bootstrap inject 都调这个.

    流程:
      1. 读 theories.json (无 → empty cache)
      2. compare cache.session_ids_snapshot 跟 SessionStore().list() set
      3. 不一致 OR force_recompute → _recompute_all() + atomic write back
      4. embedder=None + cache miss → 返 stale cache (degraded mode, bootstrap inject 用)
      5. 返 cache (caller 自决怎么处理 rejected_theory_ids)
    """

def reject_theory(storage: StorageV2, theory_id: str) -> bool:
    """加入 rejected_theory_ids, 写回. idempotent. theory 不删, 仍存 cache 但 mark."""
```

#### Recompute orchestrator (`recompute.py`)

```python
def _recompute_all(sessions, storage, embedder, preserve_rejected) -> TheoriesCache:
    cold_start = max(3, len(sessions) // 3)
    window_size = 5

    if len(sessions) < cold_start:
        return _empty_cache(sessions, cold_start, window_size, preserve_rejected)

    # 7-step pipeline (cluster → motif → predict → promote → rank)
    session_graphs = load_all_session_graphs(sessions, storage)
    themes = cluster_lexicon_themes(_load_lexicon(storage), embedder)
    raw_motifs = [m for theme in themes
                  for m in find_motifs_per_theme(session_graphs, theme, cold_start)]
    enriched = [Theory(..., predictive_power=evaluate_predictive_power(m, ...)) for m in raw_motifs]
    tentative, stable = [], []
    for t in enriched:
        (stable if maybe_promote_to_stable(t, sessions, window_size) else tentative).append(t)
    stable = rank_topk_with_mmr(stable, k=20, λ=0.7)
    tentative = sorted(tentative, key=...)[:20]

    return TheoriesCache(themes, tentative, stable, preserve_rejected,
                         sessions, cold_start, window_size, now_iso())
```

#### Atomic write (跟 Phase 10 lexicon 一致)

```python
def _atomic_write_cache(cache, path):
    tmp = path.with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(_cache_to_dict(cache), indent=2, ensure_ascii=False))
    tmp.replace(path)
```

#### Performance characteristic

| N (session) | recompute 耗时 | 主要 cost |
|---|---|---|
| 5 | < 2s | 加载 + cluster < 100 var |
| 20 | ~ 5s | gSpan 多 theme scale |
| 50 | ~ 15s | leave-one-out 50 次 |
| 100 | ~ 60s | 撞瓶颈 → Phase 17 优化 |

MVP 期望: 5-20 session, < 10s. spinner "正在分析跨 session 模式..." 几秒可接受.

#### Cold start UX

- N < cold_start: `/theories` 显 "需累积 ≥ 3 个 session 才能形成 theory. 当前: 2/3."
- N ≥ cold_start 但所有 motif freq < cold_start: 显 "已分析 N 个 session, 未发现重复出现的因果模式."
- N ≥ window_size 但无 stable: 显 tentative + "暂无稳定 theory, 跑更多 session 让 tentative 升级"
- 正常 case: `/theories` 默 stable, `--all` flag 加 tentative

bootstrap inject 在 cold start / 无 stable 时**直接跳** (`theories=None`), 不阻塞 bootstrap 正常流.

### 5.5 Bootstrap inject integration

#### `engines/bootstrap.py` 加 `theories` 参数

```python
async def propose_phenomena(
    question: str, llm: LLMClient,
    *, lexicon: dict | None = None,
    theories: list[Theory] | None = None,   # ★ Phase 16
) -> list[VariableNode]:
    """Phase 16: theories 非空时, prompt 加段引导 LLM 在已知 theory 框架生现象."""
```

#### Prompt 模板 (`_build_theories_prompt_section`)

```
## 跨 session 已发现的稳定因果模式 (仅供参考, 不必强求新问题 match)

你过去分析的多个 session 中, 以下因果模式反复出现:

1. 不确定性 → 风险规避 → 行为退缩 (出现 4/5 session, 预测准确度 75%)
2. 不确定性 → 生活退缩 (出现 2/5 session, 预测准确度 50%)
...

**注意**:
- 这些是历史 pattern, 新问题可能跟它们相关也可能完全无关
- 若现象自然映射到上述模式, 可复用其涉及的术语 (e.g. "风险规避")
- 不要强行套, 若新问题领域不同就忽略
```

设计:
- 仅 top-5 theory 进 prompt (防 prompt 爆)
- 中文段落 (跟 Phase 15 chat 中文化一致)
- 软引导 "仅供参考, 不必强求 match" (防 LLM 过拟合到老 theory)
- 只 inject stable, 跳 tentative (防 single-session 极端 reasoning 带偏)

#### `ephemeral.py promote_to_persistent` 改动

```python
async def promote_to_persistent(self, question: str) -> str:
    # ★ Phase 16: 取 stable theories
    try:
        cache = get_active_theories(storage, embedder=self.embedder, force_recompute=False)
        stable_theories = [t for t in cache.stable_theories
                           if t.id not in cache.rejected_theory_ids]
    except Exception as exc:
        logging.warning(f"theory cache load failed: {type(exc).__name__}: {exc}")
        stable_theories = []  # 失败不阻塞 bootstrap

    phenomena = await propose_phenomena(
        question, llm, lexicon=lexicon,
        theories=stable_theories,  # ★
    )
    # ... 其余 review_phenomena_async / bootstrap / save 不变
```

#### Defensive design

- 加载失败 → `theories=None`, bootstrap 跳 inject 正常跑
- stable 空 → prompt 不加 section, 完全 backward compat
- rejected 过滤在 caller (cache 内是 mark, 让 /theories UI 仍能显)

### 5.6 UI Commands

#### chat slash (`slash_commands.py`)

```python
DEFAULT_COMMANDS = (
    ...,
    SlashCommand("theories", COMMAND_DESCRIPTIONS["theories"], _handle_theories),
    SlashCommand("theory",   COMMAND_DESCRIPTIONS["theory"],   _handle_theory),
)

# COMMAND_DESCRIPTIONS 加
"theories": "查看跨 session 发现的稳定因果模式",
"theory":   "看某个 theory 的详情 / 拒绝它 (/theory <id> [reject])",

# HELP_GROUPS_ZH 加 theories/theory 到 "管理 session" 组
```

`/theories [--all] [--limit=N]`:
- 表格: ID / 类型 / 模式 / 覆盖 session / 预测准确度 / 状态
- 类型: 因果链 / 星型 / 环路
- 状态: 已稳定 / 待观察 (rejected 加 "(已拒绝)")

`/theory <id> [reject]`:
- 无 reject sub-arg: 显详情 (自然语言描述 + theme list + 因果结构 + supporting session breakdown)
- 含 reject: 加入 rejected_theory_ids, 持久化, 后续不再 bootstrap inject

#### cli (`cli.py`)

```python
@app.command()
def theories(
    project: str | None = typer.Option(None),
    show_all: bool = typer.Option(False, "--all"),
    limit: int = typer.Option(10, "--limit", min=1, max=50),
    force_recompute: bool = typer.Option(False, "--force"),
) -> None:
    """显跨 session 因果模式 (cross-session inspect)."""
```

跟 `/theories` slash 复用渲染逻辑 (`_render_theories_table`).

#### `chat_copy.py` 新加文案

```python
# COMMAND_DESCRIPTIONS: theories, theory (上面已列)

STATUS_THEORIES_COMPUTE = "[bold green]正在分析跨 session 模式...[/bold green]"

def msg_theories_cold_start(current: int, needed: int) -> str:
    return f"需累积 ≥ {needed} 个 session 才能形成 theory. 当前: {current}/{needed}."

def msg_theories_no_motif_found(n_sessions: int) -> str:
    return f"已分析 {n_sessions} 个 session, 未发现重复出现的因果模式. 跑更多 session 试试."

def msg_theory_rejected(theory_id: str) -> str:
    return f"已拒绝 theory {theory_id}, 后续不再用于 bootstrap inject."

def err_theory_not_found(theory_id: str) -> str:
    return f"theory {theory_id} 不存在, 可能 cache 已 invalidate. 先跑 /theories 看当前 list."
```

跟 Phase 15 chat_copy.py single source 一致 (全引模板).

## 6. Testing Strategy

### 新 test 文件 (47 case)

| 文件 | 测试 | case |
|---|---|---|
| `test_engines_theory_clustering.py` | 2 高 cos → 1 cluster; 空 lexicon → empty | 5 |
| `test_engines_theory_gspan.py` | 自实现 gSpan 8 经典 case (chain / star / noise prune / min_support gate) | 8 |
| `test_engines_theory_motif_mining.py` | freq=3 motif 找到; cross-theme edges; min_freq gate | 5 |
| `test_engines_theory_falsifiability.py` | leave-one-out hit; match_threshold; supporting<2 → 0 | 4 |
| `test_engines_theory_ranking.py` | score 权重; MMR 防同 theme; promote stable | 4 |
| `test_engines_theory_cache.py` | hit/miss/recompute/atomic; reject idempotent | 6 |
| `test_chat_slash_theories.py` | cold start / 正常 / --all / 中文 | 5 |
| `test_chat_slash_theory.py` | 详情 / not found / reject / reject idempotent | 4 |
| `test_cli_theories.py` | 命令存在 / --force / --project | 3 |
| `test_chat_ephemeral_theory_inject.py` | inject 进 prompt; fail fallback; rejected 过滤 | 3 |

### 改既有 test (~28 行)

| 文件 | 改 |
|---|---|
| `test_chat_ephemeral.py::test_promote_to_persistent` | mock theory cache |
| `test_chat_copy.py` | 新 COMMAND_DESCRIPTIONS / STATUS / msg/err entries 的 test |
| `test_chat_slash_commands.py::TestSlashRegistryUsesChineseDescriptions` | 含 theories/theory |
| `test_chat_slash_commands.py::TestHelpGroupingChinese` | "管理 session" 含 theories |

### 测试风险点

1. **gSpan canonical labeling** 容易写错 minimum DFS code 判断. 用 5 论文经典 case fixture 防 regression.
2. **BGE-M3 embedding 不可重现**: clustering test 用 fake embedder, 不真调 BGE-M3. 跟 Phase 13 一致 (env `EXPLAIN_EMBEDDING_DISABLED=1`).
3. **session loading IO**: cache test 用 tmp project + 手工 build 2-3 fake session sidecar.

## 7. Risks / Trade-offs

| 风险 | 严重度 | 缓解 |
|---|---|---|
| gSpan 自实现 bug (minimum DFS code 难写) | 高 | 8 unit test 含论文经典 case, 5-pass 才认 ready |
| Cache race (多终端同时 /theories) | 低 | atomic rename, 多用户写 race 不在 MVP scope |
| Theory 过拟合 (LLM 总用老 theory) | 中 | prompt 明示 "仅供参考", MMR diversity, 后续看效果 |
| Cold start UX (新用户没数据看不到 value) | 中 | UI 明示 "需累积 N session" + tentative 提示 |
| Phase 13 embedding singleton load 慢 (~30s 首次) | 低 | Phase 13 已 lazy, recompute 时 spinner 显示 |
| theories.json schema 变动 → 老 cache 不兼容 | 低 | 加 `version` 字段, 读取时 check, 不匹配则 force recompute |
| Bootstrap inject 让生成现象同质化 | 中 | 软引导 prompt + 仅 top-5 + JEPA defer (d) 不强约束 |
| gSpan 大 graph 超时 (100+ var) | 低 | MVP scope 5-20 session, Phase 17 加 max time + 优化 |
| LLM API 失败时 theory 评估如何 | 低 | falsifiability 用 embedding 不调 LLM, 不影响 |

## 8. Out-of-scope (defer)

- Hierarchical L0/L1/L2 跨 session predictive 重设 (JEPA d, Phase 17)
- Theory competition (哲学 §9.3) — 多 theory 互斥竞争
- Multi-perspective theory (技术v2 §7)
- 真 self-supervised continuous learning (NN paradigm)
- Theory editing (只支持 reject)
- 跨 project theory 共享
- gSpan 加速 trick (anti-monotone 完整 / DFS code 全 canonical / max-size pruning)
- Background recompute (lazy on read 已够)
- Theory provenance trail (谁拒绝过, 什么时候) — only id list MVP

## 9. Rollout

Plan estimate: **~3 周** (含 weekend buffer). 14 task TDD:

```
Task 1:  chat_copy.py 新增 (COMMAND_DESCRIPTIONS / STATUS / msg/err) + test       0.5d
Task 2:  engines/theory/ scaffold + Theory/Theme dataclass + clustering test     0.5d
Task 3:  engines/theory/clustering.py                                            1.0d
Task 4:  engines/theory/gspan.py (~300 行) + 8 unit test                         2.0d
Task 5:  engines/theory/motif_mining.py + test                                   1.0d
Task 6:  engines/theory/falsifiability.py + test (JEPA a)                        1.0d
Task 7:  engines/theory/ranking.py (score + MMR + promote) + test (JEPA b/c)     1.0d
Task 8:  engines/theory/cache.py + recompute.py + test                           1.5d
Task 9:  chat slash _handle_theories + _handle_theory + test                     1.0d
Task 10: cli `explain theories` subcommand + test                                0.5d
Task 11: ephemeral.py bootstrap inject + bootstrap.py propose_phenomena 改       1.0d
Task 12: 改既有 test (ephemeral / chat_copy / slash_commands)                    0.5d
Task 13: Acceptance smoke doc + 跑 5 真 session 验证                             1.0d
Task 14: 全量 pytest + ruff + README update                                      0.5d
```

每 task TDD: 失败 test → 实装 → green → commit. 每 task commit 跑 `pytest + ruff`, 0 失败 0 警告才 commit.

## 10. Follow-ups (本 phase 外)

| F# | 内容 |
|---|---|
| F-1 | Hierarchical L0/L1/L2 跨 session predictive 重设 (JEPA d) — Phase 17 候选 |
| F-2 | Theory competition (哲学 §9.3) — 多 theory 互斥, 强者吞弱 |
| F-3 | Multi-perspective theory (v2 §7) — 跨 perspective 形成 theory |
| F-4 | Background recompute on `/run` end — 不阻塞 bootstrap |
| F-5 | Theory editing UI — 允许用户调 theory 描述 / 增删 node |
| F-6 | gSpan 加速 (anti-monotone full / pattern pruning) — scale > 50 var |
| F-7 | Theory provenance trail — reject 历史 + 原因 (audit) |
| F-8 | Cross-project theory 共享 — knowledge graph 横向 reuse |
| F-9 | LLM judge fallback for theory naming — 现在 theme name 是 centroid var.name, 可让 LLM 给更抽象命名 |
| F-10 | Theory effectiveness tracking — 记录 inject 后 bootstrap 是否真利于 LLM 提速 |

## 11. Skills Used

- `superpowers:brainstorming` (本 design doc 生成)
- `superpowers:writing-plans` (待 transition)
- `superpowers:executing-plans` (实施时)
- `superpowers:systematic-debugging` (撞 bug 时)
- `superpowers:test-driven-development` (每 task)
- `superpowers:verification-before-completion` (Task 14)
