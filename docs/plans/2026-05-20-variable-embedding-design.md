# Candidate E — Variable Embedding (Medium scope) 设计

**Date**: 2026-05-20
**Status**: brainstorm 完, 待 implementation plan
**Branch**: dev
**Related**:
- Phase 10 Persistent World Model (lexicon module, 当前 string-match canonical)
- 哲学 §9 (Theory Formation) — Phase 13 前置, embedding 解 lexicon 漂移让 motif detection 数据基础干净

## 1. Motivation

当前 Phase 10 Lexicon 用 `canonical_mechanism` string match 判同概念. LLM 不同 session 同一概念用不同措辞写入, 导致同概念 split 成多 lexicon entry:

```
Session 1: 「经济不安全感」 canonical_mech="对未来收入预期下降导致防御性储蓄上升"
Session 2: 「保守消费倾向」 canonical_mech="可支配收入预期降低引发支出收紧"
```

意思一样, string 不同 → lexicon 当 2 个独立 entry 存. 影响:

- `/lexicon` 列出重复条目, user 困惑
- 新 session bootstrap 时 Top-K prior 都列, 浪费 token
- fitness 复用次数分裂 (本来 1 entry 复用 5 次, 现在 2 entry 各 2-3 次)
- Phase 13 cross-session graph 想跑 motif detection 时, 该联通的点 split 成 2 个, 拓扑分析失真

Embedding 解法: 把 `canonical_mech` 句子转 384/1024 维 vector, cosine 相似度判同概念. 高于阈值合并成 1 entry.

## 2. Goals

1. lexicon 内部 (Wave 1+2): 新 entry 写入前 embedding 比对现有, > 0.85 cosine → 合并不新建
2. /compress dedup (Wave 3): pre-filter (prompt 塞 Top-K 让 LLM 看) + post-filter (LLM 生候选后 embedding 兜底)
3. GPU 加速: Apple Silicon MPS backend, fp16 优化
4. Backward compat: 老 lexicon 自动 lazy migration, CI / 无 GPU 环境 fallback string-match path

## 3. Non-Goals

- reflection lifecycle 用 embedding (decay/split/merge 时近邻判断) — 留 Phase 13+ (Wide scope)
- 跨模型 embedding 版本管理 (BGE-M3 升级后重 embed 全 lexicon) — 留 future polish
- LLM judge fallback (borderline cosine LLM 二次复核) — 留 future polish, MVP threshold-only
- Web / REST embedding service — local only
- Multi-vector retrieval (BGE-M3 的 sparse + colbert) — 只用 dense

## 4. Design

### 4.1 Architecture (4 component)

**A. Embedder singleton** (`src/explain_engine/embedding/bge_m3.py`, 新模块)

```python
class BGE_M3_Embedder:
    """BGE-M3 dense embedding via FlagEmbedding + MPS backend."""

    _instance: "BGE_M3_Embedder | None" = None

    def __init__(self):
        import torch
        from FlagEmbedding import BGEM3FlagModel

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model = BGEM3FlagModel(
            "BAAI/bge-m3",
            use_fp16=True,    # Apple Silicon sweet spot
            device=device,
        )

    def embed(self, texts: list[str]) -> np.ndarray:
        """Batch encode → (N, 1024) dense embeddings."""
        out = self.model.encode(texts, return_dense=True)
        return out["dense_vecs"]


def get_embedder() -> BGE_M3_Embedder:
    """Lazy singleton accessor. 首次调用 load model ~3-5s."""
    if BGE_M3_Embedder._instance is None:
        BGE_M3_Embedder._instance = BGE_M3_Embedder()
    return BGE_M3_Embedder._instance
```

- 进程级 singleton (启动一次 load 4.3 GB, 后续复用)
- Lazy init (首次 lexicon flush 或 /compress 触发, 不影响 cold REPL 启动)
- Device 自动检测: `mps` (M4 Pro) → `cpu` fallback
- fp16 默认 on (cosine 精度损失 < 0.001)

**B. Lexicon storage 升级** (`src/explain_engine/engines/lexicon.py`)

- Pydantic entry 加 `embedding: list[float] | None = None` 字段
- JSON 持久化 4 KB/entry (1024-dim × 4 byte)
- 启动时 load → `np.ndarray (N, 1024)` 在内存
- 缺 embedding 字段的 entry: lazy migration (启动 batch embed 一次性回写)

**C. Merge logic** (`src/explain_engine/engines/lexicon_merge.py`, 新文件)

```python
def find_duplicate(
    new_emb: np.ndarray,            # shape (1024,)
    existing_matrix: np.ndarray,    # shape (N, 1024)
    threshold: float = 0.85,
) -> int | None:
    """返 max sim entry index (若 > threshold), 否则 None."""
    if existing_matrix.shape[0] == 0:
        return None
    # Cosine batch
    norms = np.linalg.norm(existing_matrix, axis=1) * np.linalg.norm(new_emb)
    sims = (existing_matrix @ new_emb) / np.maximum(norms, 1e-9)
    idx = int(np.argmax(sims))
    return idx if sims[idx] > threshold else None
```

- 用 numpy batch cosine (~µs 级 for 100 entry)
- threshold 0.85 hard-coded MVP (`LEXICON_MERGE_THRESHOLD` 常量)
- 命中: 不新建 entry, append evidence_ids + reuse_count++
- 不命中: 新建 entry + embedding 存 JSON
- Audit log: 每次 merge 写 `logs/lexicon_merge_<date>.jsonl` 含 `{merged_into, merged_from, sim, timestamp, evidence_ids}` 便于事后审计

**D. Compress dedup** (`src/explain_engine/engines/compression.py` + prompt 改)

- **Pre-filter**: `propose_candidates` prompt 加 "已有概念 (Top-K=20)" section, 塞 lexicon Top-K (按 fitness) 的 `canonical_mech` 文本 + 显式约束 "若新候选语义重复其中之一, 复用 ID 而非新生"
- **Post-filter**: LLM 生完候选, 逐个 embed → batch cosine 跟现有 lexicon embeddings → > 0.85 silently 改 reuse (append evidence_ids), UI 显 `compress: X candidates → Y reused (embedding) / Z new`

### 4.2 Data flow

**写入路径** (`session done → flush_to_lexicon`):
```
graph L1/L2 nodes
  → 提 canonical_mech (现有逻辑)
  → embedder.embed([canonical_mech])  ← 新增
  → find_duplicate(emb, existing_matrix)  ← 新增
       ├─ 命中: append evidence_ids, reuse_count++, audit log
       └─ 不命中: 新建 entry (含 embedding) + 存 JSON
```

**读取路径** (`bootstrap_phenomena`):
```
新 session 启动
  → load lexicon.json
  → embeddings → np.ndarray 放内存
  → Top-K by fitness (现有逻辑)
  → 塞 prompt 给 LLM 作 prior
```

**/compress 路径**:
```
LLM propose_candidates
  → prompt 塞 Top-K lexicon canonical_mech (pre-filter)
  → LLM 生候选 list
  → embedder.embed(候选 list) batch
  → 逐个 cosine vs lexicon embeddings (post-filter)
       ├─ > 0.85: reuse 现有 entry (合 evidence_ids)
       └─ ≤ 0.85: 新建 c_xx + embedding 进 JSON
  → 入 graph (只剩真正新的 c_xx)
```

### 4.3 Migration (老 lexicon 升级)

启动时 `load_lexicon`:
1. 检查任意 entry 缺 `embedding` 字段?
2. 缺 → Rich console.status() 进度提示 "首次升级 lexicon embedding: N entries, ~Xs"
3. Batch embed 所有缺的 canonical_mech (单批最多 32)
4. 回写 JSON
5. 后续启动跳过 (所有 entry 有 embedding)

### 4.4 Fallback / 错误处理

- Model load 失败 (e.g. CI 无 MPS + 不 download 模型): 走 `EXPLAIN_EMBEDDING_DISABLED=1` env 短路, 全 lexicon path 回退 string match
- 单个 entry embed 失败 (空文本 / OOM): 保 `embedding=None`, 该 entry 走 string match fallback, 不阻塞 flush
- Audit log 写 fail: warning, 不 crash
- Embed-test 路径 隔离: 加 `@pytest.mark.embedding` 默认 skip CI

### 4.5 新依赖

`pyproject.toml`:
```toml
"FlagEmbedding>=1.3",   # BGE-M3 official wrapper
"torch>=2.1",           # MPS backend (Apple Silicon)
"numpy>=1.26",          # 应已 transitive (networkx)
```

venv 涨 ~3 GB (含 torch + FlagEmbedding C++ exts). BGE-M3 4.3 GB 在 `~/.cache/huggingface/` 已就位.

### 4.6 Backward compat

- 旧 lexicon.json 无 `embedding` 字段: 自动 lazy migration
- `/lexicon` cli + slash 接口不动 (内部 dedup, user 不感知)
- 旧 session 老 graph 不动 (embedding 只影响 lexicon, 不影响 graph)
- Embedder 不可用: fallback string-match path, warning 不 crash

## 5. Test Plan

### 5.1 Unit (~30-40 test)

- `test_embedder.py`: load smoke (mps/cpu auto-detect), embed shape (N, 1024), dtype float32, fp16 on
- `test_lexicon_merge.py`: cosine math, threshold 边界 (0.85±0.001), empty matrix 短路, sim max 选 argmax 正确
- `test_lexicon_migration.py`: 老 entry 缺 embedding lazy embed + 回写, batch 32 切分
- `test_compress_prefilter.py`: prompt 含 Top-K 验, K=0 时 prompt 不带 "已有概念" section
- `test_compress_postfilter.py`: 全 dup case → graph 0 新节点 + reuse 全部, 部分 dup → 命中 reuse 不命中新建, threshold 边界

### 5.2 Integration (~10 test)

- `test_flush_to_lexicon_dedup.py`: 2 个 session 同主题 flush, 验 lexicon entry 不重复 (cosine merge 命中)
- `test_compress_with_lexicon.py`: /compress 在含 lexicon 的 session 跑, 验 pre-filter prompt 塞了 Top-K + post-filter 命中改 reuse
- `test_migration_e2e.py`: 启动时老 lexicon (无 embedding 字段) → 自动 batch embed → 回写后 reload 验

### 5.3 E2E manual smoke

- 跑 2 个不同 session 同主题 ("年轻人消费少" / "Gen Z 不买东西"), 验 lexicon 不出现明显重复 entry
- `/lexicon` 看 reuse_count 真累加 (老 entry 而非新生)
- /compress 输 'Y reused (embedding) / Z new'

### 5.4 CI fallback

- 加 `EXPLAIN_EMBEDDING_DISABLED=1` env var, CI 启用 → 全 lexicon path string-match 兜底
- 跟 embedding 相关 test 标 `@pytest.mark.embedding` 默认 skip
- Local dev 跑 `.venv/bin/python -m pytest -m embedding` 显式开

## 6. Risks + Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Model load 启动 3-5s 卡顿 | Low | Lazy load (首次 lexicon flush 触发), Rich console.status() spinner |
| BGE-M3 4.3 GB venv 大 / cold-start | Med | model 已 cache (验证), 文档说明首次会进度提示 |
| threshold 0.85 不准 (false merge/split) | Med | MVP hard-coded + audit log `logs/lexicon_merge_<date>.jsonl`; future Phase /lexicon revert |
| /compress prompt 长度涨 (~2k token Top-K) | Low | deepseek-v4-pro 128k context 远够, monitor cost trend |
| MPS 某 op 不支持 | Low | torch ≥ 2.1 BGE-M3 ops 全支持; fallback CPU 自动 |
| Migration fail (老 entry 文本异常) | Med | embed fail → `embedding=None`, 走 string-match fallback; 不 crash |
| /compress 全 dup case UX 突兀 | Low | UI 显式 `Y reused`, 透明可解释 |

## 7. Effort & Scope

**实工**: ~2-2.5 周
- Wave 1 (Embedder + Lexicon storage): 3-5 天
- Wave 2 (Merge logic + lexicon dedup): 3-4 天
- Wave 3 (/compress pre+post filter): 3-4 天

**Buffer**: ~0.5 周

**Total**: ~3 周

**Out of scope (future)**:
- Reflection lifecycle 用 embedding (Wide scope, Phase 13+)
- 多模型版本管理
- LLM judge fallback
- 多 vector retrieval (sparse / colbert)

## 8. Open Questions (resolved during brainstorm)

| Q | A |
|---|---|
| Scope (narrow/medium/wide)? | Medium (lexicon + /compress dedup) |
| Embedding model? | BGE-M3 via FlagEmbedding, fp16, MPS |
| Vector storage? | Persist to lexicon JSON (embedding field per entry) |
| Merge threshold + decision? | Threshold-only 0.85 MVP, audit log |
| /compress strategy? | Pre+post filter (LLM 看 Top-K + cosine 兜底) |
| GPU 加速? | Apple Silicon MPS backend + fp16 (M4 Pro 24GB) |
| Migration? | Lazy on startup, batch embed 缺字段 entry, 一次性回写 |
| CI / 无 GPU fallback? | `EXPLAIN_EMBEDDING_DISABLED=1` env + `@pytest.mark.embedding` skip |

设计已对齐, 进 implementation plan.
