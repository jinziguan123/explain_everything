# Phase 10 — Persistent World Model (Variable Lexicon) Design

> 顶层设计文档参考: [最终哲学以及技术实现相关设计.md](../../最终哲学以及技术实现相关设计.md) §5.3 / §8.1 / §8.2 / §9.2
> 上一 phase: [Phase 9 chat REPL prompt_toolkit 升级](2026-05-18-chat-repl-prompt-toolkit-design.md)
> 当前 HEAD: `afc9234` (679 PASS, ruff 0)

**日期**: 2026-05-18
**分支**: `dev`

---

## 0. TL;DR

**Phase 10 主题**: 把 `~/.explain/projects/<proj>/knowledge/` 从 Phase 9 占位空目录变成跨 session **Variable Lexicon**. 单 session 高 fitness L1/L2 节点累积成 reusable abstractions, 新 session bootstrap 时 LLM 看 lexicon 作 prior, 自主决定是否引用历史 abstraction.

**核心哲学落地**: §5.3 Persistent World Model (而非 isolated session outputs) + §8.1 Structural Memory + §8.2 Memory Consolidation 离线 phase (graph compression + abstraction merge).

**Scope**: Lexicon CRUD + fitness-based Top-K retrieval + render cap (~2k token). **不做**: semantic embedding (Candidate E), mechanism catalog (Candidate B 起点), multi-perspective scope (Candidate C), auto theory formation (Candidate B 完整).

**Storage**: 维持 local JSON (`knowledge/variables.json`, atomic `.tmp` + rename). 远程存储 (Neo4j / pgvector / PostgreSQL) 留 Phase 11+ 与 Candidate E 再评估.

**总: ~10-15 task, 4-5 Wave, +30-40 tests, 2-3 周.**

---

## 1. 背景与动机

### 1.1 Phase 9 留下的 cross-session 缺口

Phase 9 落 `~/.explain/projects/<project_id>/sessions/<sid>/` 5 sidecar files + `knowledge/` 占位**空**目录. session-level 数据齐全, 但**所有 insights 仍是 isolated session outputs** — 用户 session 1 学到 "长期不确定性 → 风险规避", session 2 问类似 question 时 graph 从头重新 bootstrap, 无任何 reuse.

### 1.2 顶层设计 §5.3 + §8.2 直接指向

设计 §5.3 Persistent World Model:
> 系统长期形成: Persistent World Model
> 而不是: isolated session outputs

设计 §8.2 Memory Consolidation offline phase:
- graph compression
- relation stabilization
- **abstraction merge**
- theory reinforcement

设计 §5.2 Structural Memory:
- 高生成力变量
- 稳定机制
- recurring structures
- reusable abstractions

→ Phase 10 落 **"高生成力变量 + reusable abstractions"** 子集. mechanism / theory 留后续 phase.

### 1.3 Phase 9 chat /new + /resume 已铺路

Phase 9 chat REPL 增强 (`/new` + `/resume` slash + prompt_toolkit) 解决了"用户在 chat 内多 session 切换"的 UX. 但**单 session graph 仍互不通气**. Phase 10 是 cross-session 数据层. 完成后 chat 内 `/new` 切到的新 session 自动看到老 session 的 abstractions.

### 1.4 第一性原理: 为什么 Variable 为单位而非 Mechanism / Theory

- **Variable lexicon** (本 phase): 单 entry = 一个高 fitness L1/L2 节点. 跨 session merge by name. 简单, 直接对应 §5.2 "高生成力变量".
- **Mechanism catalog** (Phase 11 候选 B 起点): 单 entry = `(source_var → target_var, relation_type, mechanism)`. 需 variable 层先稳定.
- **Theory candidates** (Phase 11+ 完整 B): 单 entry = `(variables, mechanisms, supporting_graphs)`. §9.2 "Theory 应 emergent from graph dynamics 不应由 prompt 直接生成", **必须等 mechanism 层 + cross-session motif detection 跑了才能 emergent**.

Phase 10 = "lexicon"; Phase 11 = "lexicon + catalog + theory pipeline".

---

## 2. Scope

### 2.1 本设计内

- 新 module `engines/lexicon.py` (CRUD + fitness select + prompt render)
- 新 schema `knowledge/variables.json`
- 改 `engines/bootstrap.py` 接 lexicon 参数
- 改 `cli._run_new` / `cli._run_compress` / `cli._run_chat_repl_async` 三个 flush 触发点
- 新 cli `explain lexicon` (列 / dump JSON)
- ~30-40 tests (engine unit + bootstrap integration + cli)

### 2.2 本设计外 (留 future)

- **Semantic embedding retrieval** (Candidate E) — sentence-transformer + cosine sim. Phase 10 用 fitness Top-K, 不算 query relevance.
- **Mechanism catalog** (Candidate B 起点) — edge-level cross-session pattern.
- **Theory formation pipeline** (完整 B) — motif detect + falsifiability.
- **Multi-perspective scope** (Candidate C) — perspective-tagged lexicon entries.
- **Cognitive Energy/Entropy 量化** (Candidate D).
- **远程存储** (Neo4j / pgvector / PostgreSQL) — Phase 10 scale 不需要; Phase 11+ 再评估.
- **跨 project lexicon 共享** — 不同 project_id 互不通气 (Phase 9 isolation 设计).

---

## 3. 总体方案

### 3.1 数据流

```
session graph (state.graph)
    ↓ (flush_to_lexicon trigger: compress done / chat aclose)
filter (L1+L2 only, active only, activation > 0.5)
    ↓
upsert (by global_id = sha8(name + canonical_mechanism))
    ↓
knowledge/variables.json (atomic write)
    ↓ (load on next session bootstrap)
filter (Top-K by composite fitness)
    ↓
render (per-var ~80 token, total cap ~2k)
    ↓
attach to bootstrap_phenomena LLM prompt as prior section
    ↓
LLM 自主决定是否在新 graph 引用历史 abstraction
```

### 3.2 关键不变量

1. **同 sid 多次 flush 幂等**: 用 `source_sessions` 列表去重 — 含 sid 就 update fitness avg 但不 `reuse_count++`.
2. **global_id 稳定**: `sha8(name + canonical_mech)`, name 或 mech 改 → 新 global_id (视为新 var, 不破坏 existing). 这是 conservative: 宁可重复存储, 不要意外 merge 两个 不同 var.
3. **Top-K 是 global rank** (Phase 10 第一版), **不依赖** question. Candidate E 上来后改 query-relevance.
4. **Lexicon load 失败不阻塞 bootstrap**: try/except + fallback empty list. lexicon 是 nice-to-have, 不该让新 session 起不来.

---

## 4. Schema — `knowledge/variables.json`

```json
{
  "version": 1,
  "updated_at": "2026-05-18T15:30:00Z",
  "variables": [
    {
      "global_id": "v_a3f2c891",
      "name": "长期不确定性",
      "description": "对未来 5-10 年环境的稳定预期缺失, 由社会经济结构不确定性诱发",
      "abstraction_level": 2,
      "epistemic": "insight",
      "fitness": {
        "reuse_count": 3,
        "avg_essentialness": 0.82,
        "avg_consistency": 0.91,
        "first_seen_at": "2026-05-13T10:20:00Z",
        "last_seen_at": "2026-05-18T15:30:00Z"
      },
      "canonical_mechanism": "通常 cause 风险规避; 由社会经济压力 cause",
      "source_sessions": ["s_xxx", "s_yyy", "s_zzz"]
    }
  ]
}
```

### 4.1 字段说明

- **version**: schema version int. Phase 10 初版 = 1. 后续 schema 改时 +1, `_load_lexicon` 检查 version mismatch 走 migration path.
- **updated_at**: ISO8601, 每次 save 更新.
- **variables**: list of var entries.
  - **global_id**: `"v_" + sha256(name + "::" + canonical_mechanism)[:8]`. 8 hex char 碰撞概率 ~1/4B, 实际 scale 安全.
  - **name / description / abstraction_level / epistemic**: 直接 copy from `VariableNode`.
  - **fitness.reuse_count**: 出现在多少 distinct session.
  - **fitness.avg_essentialness / avg_consistency**: 跨 session 平均, 从 `state.last_acceptance_report` 取该 node 的 per_l1 / 等 score (若有).
  - **fitness.first_seen_at / last_seen_at**: ISO8601.
  - **canonical_mechanism**: 1-line summary "通常 cause X; 由 Y cause". 由 `flush_to_lexicon` 时 LLM 生 (在线一次), 或 fallback 取 node.description 头一行.
  - **source_sessions**: list of sid 出现过的. 去重用.

### 4.2 atomic write

```python
def _save_lexicon(path: Path, lexicon: dict) -> None:
    """写 .tmp 后 rename, 同 StorageV2._write_atomic pattern."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(lexicon, indent=2, ensure_ascii=False))
    os.replace(tmp, path)
```

---

## 5. 写入: `flush_to_lexicon`

### 5.1 API

```python
# engines/lexicon.py
def flush_to_lexicon(
    session: Session,
    storage: StorageV2,
    llm: LLMClient | None = None,
) -> int:
    """Promote 高 fitness var 进 lexicon. 返 promoted count (新 var + reused).

    Idempotent w.r.t. session_id: 同 sid 多次调只 update fitness avg, 不 ++ count.

    Args:
        session: 当前 session
        storage: StorageV2 (拿 knowledge_dir 路径)
        llm: 可选; 若传则用 LLM 生 canonical_mechanism. None 则 fallback to
             node.description 头一行 + 简单 incoming/outgoing edge summary.
    """
    path = storage.knowledge_dir() / "variables.json"
    lexicon = _load_lexicon(path)
    promoted = 0
    for nid, node in session.state.graph.nodes.items():
        if not _should_promote(node):
            continue
        canonical_mech = await _build_canonical_mechanism(node, session, llm)
        _upsert_var(lexicon, node, canonical_mech, session.meta.session_id)
        promoted += 1
    lexicon["updated_at"] = _now_iso()
    _save_lexicon(path, lexicon)
    return promoted


def _should_promote(node: VariableNode) -> bool:
    """Phase 10 第一版 filter."""
    return (
        node.abstraction_level >= 1  # skip L0 observations
        and node.lifecycle_state == "active"  # skip stale/decayed
        and node.activation >= 0.5  # 简单 fitness threshold
    )
```

### 5.2 `_upsert_var` 去重逻辑

```python
def _upsert_var(
    lexicon: dict,
    node: VariableNode,
    canonical_mech: str,
    sid: str,
) -> None:
    global_id = _compute_global_id(node.name, canonical_mech)
    entries = lexicon["variables"]
    existing = next((v for v in entries if v["global_id"] == global_id), None)

    if existing is None:
        # 新 var
        entries.append({
            "global_id": global_id,
            "name": node.name,
            "description": node.description,
            "abstraction_level": node.abstraction_level,
            "epistemic": node.epistemic,
            "fitness": {
                "reuse_count": 1,
                "avg_essentialness": 0.0,  # TODO: 从 session.state.last_acceptance_report 取
                "avg_consistency": 0.0,
                "first_seen_at": _now_iso(),
                "last_seen_at": _now_iso(),
            },
            "canonical_mechanism": canonical_mech,
            "source_sessions": [sid],
        })
        return

    # 已有 — 检查 sid 去重
    if sid in existing["source_sessions"]:
        # 同 session 重复 flush: update last_seen + fitness avg, 不 ++ count
        existing["fitness"]["last_seen_at"] = _now_iso()
        # avg_* 暂不更新 (single session 内 no new data)
        return

    # 新 session 引用: ++ count
    existing["source_sessions"].append(sid)
    existing["fitness"]["reuse_count"] += 1
    existing["fitness"]["last_seen_at"] = _now_iso()
    # TODO Phase 10.x: avg fitness 算法 (running avg using count)
```

### 5.3 触发点

3 处 in cli.py:

1. **`_run_compress` 完成** (`cli.py:269` 之后): set stage='done' + save 后, `await flush_to_lexicon(session, storage, llm)`
2. **`_run_chat_repl_async` finally aclose 后**: 拿 `chat_session._session` + storage, 调 flush. (chat 模式不切 stage, 但 graph 可能有新 var, 退出时 flush)
3. **`_run_new --no-chat=False` 后** (默认进 chat): chat aclose 路径已 cover, 不重复.

`_run_new --no-chat=True`: bootstrap 完直接退出, graph 只有 L0 现象, 没 L1/L2 — `_should_promote` 全 reject, 不 flush 也无所谓. 但仍可调一次保 invariant (即使 0 promote 也无害, 只触发 lexicon load + 重写 updated_at).

### 5.4 `canonical_mechanism` 生成

**有 LLM**: 1 prompt 问 "这个 variable 通常 cause 什么 / 由什么 cause? 用 1 句话总结":
```
Variable: 长期不确定性 (L2)
Description: 对未来 5-10 年环境的稳定预期缺失
Incoming edges (causes by): 社会经济压力, 信任崩塌
Outgoing edges (causes): 风险规避, 储蓄率上升

请用 1 句中文总结它的 canonical mechanism, 格式: "通常 cause X; 由 Y cause".
```

成本: 1 var 1 prompt × promoted var count. 一个 session 通常 promote 1-5 var, 每个 ~200 token cost. 可接受.

**无 LLM** (test / fallback): 直接拼 `"通常 cause [outgoing edge target names]; 由 [incoming edge source names] cause"`. 不优雅但可用.

---

## 6. 读取: bootstrap + Top-K render cap

### 6.1 API

```python
# engines/bootstrap.py 改
async def bootstrap_phenomena(
    question: str,
    llm: LLMClient,
    lexicon: list[dict] | None = None,  # NEW
    lexicon_top_k: int = 20,  # NEW
) -> list[VariableNode]:
    prior_section = ""
    if lexicon:
        top_k_vars = _select_top_k_vars(lexicon, k=lexicon_top_k)
        prior_section = _render_lexicon_for_prompt(top_k_vars)
    prompt = _build_phenomena_prompt(question, prior_section)
    ...
```

### 6.2 `_select_top_k_vars`

```python
# engines/lexicon.py
def _select_top_k_vars(lexicon: dict, k: int = 20) -> list[dict]:
    """Composite fitness rank: reuse_count × avg_essentialness."""
    variables = lexicon.get("variables", [])
    def _score(v: dict) -> float:
        f = v["fitness"]
        # Phase 10 第一版: reuse_count 是主导 + essentialness 加权
        return f["reuse_count"] * (f["avg_essentialness"] + 0.1)
    return sorted(variables, key=_score, reverse=True)[:k]
```

`+0.1` 防 essentialness=0 (新 var 没跑过 acceptance) 时 score 全 0 排不上.

### 6.3 `_render_lexicon_for_prompt`

```python
def _render_lexicon_for_prompt(vars: list[dict]) -> str:
    if not vars:
        return ""
    lines = [
        "# 已知 reusable abstractions (来自历史 session, 仅供参考)",
        "",
    ]
    for v in vars:
        level = "L" + str(v["abstraction_level"])
        reuse = v["fitness"]["reuse_count"]
        # 单 var ~80 token: name + level + reuse + 1-line desc + 1-line mech
        desc = v["description"][:80]  # cap 80 char ~ 30 中文字
        mech = v["canonical_mechanism"][:60]
        lines.append(
            f"- {v['global_id']} 「{v['name']}」({level}, reused {reuse}x): {desc} — {mech}"
        )
    lines.append("")
    lines.append("(若新问题涉及上述抽象, expand/compress 阶段可引用. 不强制使用.)")
    return "\n".join(lines)
```

20 var × 80 token ≈ 1.6k token. + 2-3 header/footer line ≈ 1.7k token. Safe 在 2k 内.

### 6.4 CLI flag

`cli.py new` 加:
```python
lexicon_top_k: int = typer.Option(
    20, "--lexicon-top-k",
    help="bootstrap 时拉 top-K lexicon var 作 prior (默认 20)",
),
```

`--lexicon-top-k 0` → 不拉 lexicon. Power user override.

---

## 7. Module 改动汇总

| 文件 | 新/改 | 内容 |
|---|---|---|
| `engines/lexicon.py` | 新 | `flush_to_lexicon` / `_load_lexicon` / `_save_lexicon` / `_upsert_var` / `_compute_global_id` / `_select_top_k_vars` / `_render_lexicon_for_prompt` / `_should_promote` / `_build_canonical_mechanism` |
| `engines/bootstrap.py` | 改 | `bootstrap_phenomena` 加 `lexicon` + `lexicon_top_k` 参数, 注入 prior section |
| `engines/bootstrap.py` prompt template | 改 | 在 question prompt 后加 `{prior_section}` slot |
| `cli.py _run_new` | 改 | bootstrap 前 `lexicon = _load_lexicon(storage.knowledge_dir() / "variables.json")` 传入; `--lexicon-top-k` flag 接受 |
| `cli.py _run_compress` | 改 | `session.meta.stage = "done"` 后 `await flush_to_lexicon(session, storage, llm)` |
| `cli.py _run_chat_repl_async` finally | 改 | `await chat_session.aclose()` 之前/之后 `await flush_to_lexicon(chat_session._session, storage, llm)` |
| `cli.py` 新 cmd | 新 | `@app.command()` `lexicon`: 列 lexicon (Rich Table) / `--dump-json` 输 raw / `--top-k N` 限数 |
| `chat/session.py` ChatSession | 加 method | (可选) `flush_lexicon()` proxy 让 chat aclose 内调. 简化用 |

---

## 8. 测试 plan

### 8.1 单测 (~25-30 个)

**`tests/test_engines_lexicon.py`**:
- `_compute_global_id`: 同 name + mech → 同 id; name 或 mech 改 → 新 id; 算法 stable
- `_load_lexicon`: 空目录 → empty schema; 损坏 JSON → 抛 + 错误信息含 path; 老版本 → migration (Phase 10 仅 version=1, 留 TODO)
- `_save_lexicon`: atomic (.tmp + rename); .tmp 残留 cleanup (write 失败时)
- `_should_promote`: L0 拒; non-active 拒; activation < 0.5 拒; L1+ active + activation >= 0.5 接
- `_upsert_var`: 新 var 加入 + reuse_count=1; 已有 + 新 sid → ++ count + 加 sid; 已有 + 同 sid → update last_seen 不 ++ count; 多次同 sid flush 幂等
- `_select_top_k_vars`: k > total → 全返; k=0 → empty; composite score desc 排序; essentialness=0 不爆零
- `_render_lexicon_for_prompt`: empty → ""; single → 正常; 长 desc/mech cap; 中文不乱码
- `_build_canonical_mechanism` (with mock llm): LLM 返简单 string; LLM 抛错 → fallback to edge-based; fallback 含 incoming + outgoing
- `flush_to_lexicon` 全流: graph 含 L0+L1+L2, mix active+decayed → 仅 L1+L2 active 进 lexicon

**`tests/test_engines_bootstrap.py`** 扩:
- `lexicon=None`: 行为不变 (backward compat)
- `lexicon=[]`: 行为不变 (空 list 等同 None)
- `lexicon=[v1, v2]`: prompt 含 prior section + global_id + name; top-K 生效

### 8.2 集成测 (~5 个)

**`tests/test_cli_lexicon.py`** (新):
- `explain lexicon` 空 lexicon → "lexicon 暂无变量" 友好提示
- `explain lexicon` 有 var → Rich Table 列 name / level / reuse / last_seen
- `explain lexicon --dump-json` → stdout 输 raw JSON
- `explain lexicon --top-k 5` → 仅显 5 行
- `explain new --lexicon-top-k 0 ...` → bootstrap LLM prompt 不含 prior section (mock + verify)

**`tests/test_cli_compress.py`** 扩 (~2 个):
- compress 完毕后 `knowledge/variables.json` 创建 + 含本 session promote 的 var
- compress 同 session 跑 2 次 → reuse_count 不 ++ (幂等)

**`tests/test_cli_new.py`** 扩 (~1 个):
- explain new 1st session done → lexicon 有 var
- explain new 2nd session → bootstrap LLM mock prompt 含 prior section + 含 1st session promote 的 var name

### 8.3 Manual smoke

不写 acceptance doc 单独, 折叠进 plan doc 最后:

1. `rm -rf ~/.explain/projects/<proj>/knowledge/`
2. `explain new "为什么年轻人不消费" --no-chat`  (跑 bootstrap + HITL accept all)
3. `explain compress <sid1>` → 走 HITL 2 review → stage=done
4. `cat ~/.explain/projects/<proj>/knowledge/variables.json` → 验有 1-3 个 var
5. `explain lexicon` → Rich Table 显示
6. `explain new "为什么年轻人不结婚" --no-chat` → 看 bootstrap LLM 调用前的 console log 含 "已知 reusable abstractions" prior section
7. (可选) compress session 2, 看 lexicon 是否 ++ reuse_count for 重叠 var

---

## 9. 风险与 open issues

### 9.1 fitness avg 算法 (Phase 10.x)

第一版 `_upsert_var` 简化处理 avg_essentialness — TODO 评 (1) running avg `new_avg = old_avg + (new_value - old_avg) / count`, (2) 用 source_sessions 的 latest acceptance report 重算. 起步用 running avg, 收集真数据再调.

### 9.2 canonical_mechanism 长度漂移

LLM 生 1-line 可能产 5 行长 response. 加 cap `[:80]` 在渲染时再 cap. 但 storage 存全文 (节省 LLM call). retrieval 时 cap.

### 9.3 cross-session name 冲突 (同 name 不同 mech)

设计上 `global_id = sha8(name + canonical_mech)` 让 name + mech 都同才 merge. 但 LLM 生 canonical_mech 不一定 deterministic — 同 var 两 session 生不同 mech → 两个 global_id. 这是 **conservative split** (宁可重复, 不要 wrong merge). 长期 (Candidate E) 用 embedding similarity merge.

### 9.4 lexicon 不可信内容 (LLM hallucinate)

session N 生的 var 可能是 hallucination (HITL 用户审过, 但 mechanism 不一定准). 进 lexicon 影响 session N+1 bootstrap. 缓解: lexicon 是 nice-to-have, LLM 自主决定是否引用 (prompt 措辞: "仅供参考, 不强制"). 重 bias 时用 `--lexicon-top-k 0` 关.

### 9.5 chat aclose flush 性能

chat aclose 现已 await background tasks. 加 flush_to_lexicon (含 LLM call build canonical_mech) 可能慢 — 用户 /quit 后等几秒. 缓解: (a) flush 在 finally 内, **可能**用 `asyncio.shield(...)` 防止 ctrl+c 中断; (b) canonical_mech LLM call 是 best-effort, 失败 fallback edge-based, 不阻塞.

### 9.6 storage_v2 EXPLAIN_PROJECT_ID 切换

不同 project_id 的 lexicon 互不通气 (Phase 9 隔离设计). 用户跨 project 工作时 (e.g. 切到不同 cwd) lexicon 不共享. 这是 expected (相当于不同"知识库"), 不修.

### 9.7 远程存储 deferred

设计 §15 工程目录提到 `memory/structural_memory.py` + `consolidation_engine.py`. Phase 10 用 single JSON 起步. Phase 11 Theory Formation 真启动时, motif detect on 大 cross-session graph 可能值得 Neo4j; Candidate E embedding 上来时值得 pgvector. **Phase 10 不引入** (user 给了 affordance 但量化无大幅提升).

---

## 10. 决策摘要 (brainstorming Q&A)

| Q | 选项 | 决策 |
|---|---|---|
| Phase 10 主线 | A WorldModel / B Theory / C Multi-Persp / D Energy / E Embedding | **A** (Persistent World Model — Phase 9 已铺路, 是 B/C/E 前置) |
| Knowledge 存储单位 | Variable Lexicon / Mechanism Catalog / Hybrid / Theory Candidates | **Variable Lexicon** (§5.2 直接落地, mechanism/theory 留 Phase 11) |
| Variable 写入时机 | Session done auto / 每 N turn / /promote 手动 / mixed | **Session done auto-flush** (compress 末 + chat aclose) |
| Knowledge 读取时机 | Bootstrap 看全库 / chat tool / both / reflection trigger | **Bootstrap 看 (Top-K)** (chat tool / reflection 留 future) |
| Lexicon token cap 策略 | Top-K by fitness + render cap / 两阶段 LLM 自筛 / Embedding / Top-K + chat tool | **Top-K by fitness + render cap** (deterministic, 0 新 dep, 2k token ceiling) |
| Storage backend | Local JSON / Phase 10 fold pgvector / Phase 10 上 Neo4j 预热 | **Local JSON** (Phase 10 scale 不需要; Phase 11+ 再评估远程存储) |

---

## 11. 落地顺序 (waves)

预估 4-5 wave, 每 wave 独立可测:

1. **Wave 1 — Lexicon schema + CRUD** (~2-3 task)
   - `engines/lexicon.py`: schema dataclass + `_load_lexicon` / `_save_lexicon` (atomic) / `_compute_global_id` / `_upsert_var` / `_should_promote`
   - ~10 unit test

2. **Wave 2 — `flush_to_lexicon` + `_build_canonical_mechanism`** (~2 task)
   - flush 主函数 + canonical_mech LLM call + edge-based fallback
   - ~5 unit test (mock LLM + 真 graph fixture)

3. **Wave 3 — bootstrap_phenomena + lexicon prior** (~1-2 task)
   - bootstrap signature 加 `lexicon` + `lexicon_top_k`
   - `_select_top_k_vars` + `_render_lexicon_for_prompt`
   - prompt template 加 prior section slot
   - ~5 unit test + ~3 integration test (mock LLM)

4. **Wave 4 — CLI 集成** (~2 task)
   - `_run_new` / `_run_compress` / `_run_chat_repl_async` 接 flush + load
   - `--lexicon-top-k` flag
   - 新 `explain lexicon` 命令 (含 `--dump-json` / `--top-k`)
   - ~10 cli test

5. **Wave 5 — manual smoke + acceptance** (~0.5 task)
   - 跑 2 session 验 cross-session prior
   - lexicon token 占用实测 (Top-K=20 时 ~1.7k OK)
   - 文档更新 (README + 本 plan 末尾 acceptance evidence)

预估总: ~3-5 task per wave × 5 wave = 15-25 task; +30-40 test; 2-3 周.

---

## 12. 关联文档 + 长期 roadmap

### 12.1 当前依赖文档

- 顶层: [最终哲学以及技术实现相关设计.md](../../最终哲学以及技术实现相关设计.md) §5 + §8 + §9.2
- 上一 phase: [Phase 9 design](2026-05-17-conversational-cognitive-engine-design.md)
- Phase 9 chat 增强: [chat /new + /resume design](2026-05-18-chat-new-resume-slash-design.md), [chat prompt_toolkit design](2026-05-18-chat-repl-prompt-toolkit-design.md)

### 12.2 长期 phase roadmap (Phase 10 fold 后 next)

| Phase | 主题 | 触发引入的远程存储 |
|---|---|---|
| **Phase 11** | Theory Formation Engine 起步 — motif detection on cross-session graph | 可能 Neo4j (图模式匹配) — Phase 11 brainstorming 时验证比 networkx in-Python 真大幅快 |
| **Candidate E** (any phase) | Variable Embedding + semantic anchoring | pgvector / Qdrant — embedding 必需 vector DB |
| **Phase 12** | Multi-Perspective Runtime | 可能 PostgreSQL 关系 table (perspective-tagged var aggregate) |
| **Phase 13+** | Theory competition + falsifiability tracking | TBD |

Phase 10 完成后, lexicon 已为以上每 phase 提供基础数据层. 不引入远程存储但留好接口 (lexicon dict-based, 易于后续抽象成 storage backend).

---
