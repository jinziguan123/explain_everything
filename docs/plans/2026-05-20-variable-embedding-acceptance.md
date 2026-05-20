# Phase 13 Variable Embedding (Candidate E) Acceptance Smoke

**Date**: 2026-05-20
**Phase**: 13 (Candidate E, Medium scope)
**HEAD**: (Wave 4 终结 commit SHA after this task lands)

## 前置

- macOS Apple Silicon (M-series) + Full Disk Access for Claude Code/Terminal
- BGE-M3 model cache present (`~/.cache/huggingface/hub/models--BAAI--bge-m3/`, ~4.3 GB)
- `dot` binary still available (Phase 12 /graph dep)
- 项目 venv 已 `uv sync` 装 torch + FlagEmbedding

## Step 1: 装新 deps + 验 BGE-M3 + MPS

```bash
cd /Users/jinziguan/Desktop/explain_everything
uv sync
.venv/bin/python -c "
import torch
from FlagEmbedding import BGEM3FlagModel
print(f'torch {torch.__version__}')
print(f'mps: {torch.backends.mps.is_available()}')
model = BGEM3FlagModel('BAAI/bge-m3', use_fp16=True, device='mps')
out = model.encode(['测试'], return_dense=True)
print(f'shape: {out[\"dense_vecs\"].shape}')
"
```

Expected:
- `torch 2.x.x`
- `mps: True`
- `shape: (1, 1024)`

## Step 2: Lazy migration

First lexicon-touching command (e.g. `explain` REPL → `/lexicon`):

```bash
.venv/bin/python -m explain_engine.cli
> /lexicon
```

Validation:
- Rich console 显 "首次升级 lexicon embedding: N entries..." spinner (if N > 0)
- 跑完后 `cat ~/.explain/projects/<proj>/knowledge/variables.json | jq '.variables[0].embedding | length'` 返 1024

第 2 次跑同命令 — 应无 migration 提示 (所有 entry 已有 embedding).

## Step 3: 同概念跨 session merge

跑 2 个语义相近主题的 session, 验 lexicon 不出现重复 entry:

```bash
.venv/bin/python -m explain_engine.cli
> /new
question: 为什么年轻人不消费
... (HITL bootstrap)
> /compress
... (LLM proposes c_001..c_00X 候选)
> /quit

.venv/bin/python -m explain_engine.cli
> /new
question: Gen Z 消费保守的原因
... (HITL bootstrap)
> /compress
... (LLM proposes c_001..c_00X 候选)
> /quit
```

验证:
- 第 2 个 session compress 输出末尾应显 `compress dedup: X candidates → Y reused / Z new` 中 Y > 0 (因为 session 1 已写过相关概念)
- `cat ~/.explain/projects/<proj>/knowledge/variables.json | jq '.variables[] | {global_id, name, canonical_mechanism, "reuse_count": .fitness.reuse_count}'` — 应见 reuse_count > 1 的 entry (跨 session 复用)
- `cat ~/.explain/projects/<proj>/logs/lexicon_merge_*.jsonl | jq` 看 merge 记录

## Step 4: /compress UI dedup

任意 session 跑 `/compress` 后, 验输出末尾包含:

```
compress dedup: X candidates → Y reused / Z new (embedding pre-check)
```

数字含义:
- `X candidates`: LLM 提的 c_xxx 候选总数 (HITL 之前快照)
- `Y reused`: 与现有 lexicon 高余弦相似的候选 (display_threshold=0.75)
- `Z new`: 余弦无匹配的候选 (X = Y + Z)

## Step 5: Fallback / 兼容

`EXPLAIN_EMBEDDING_DISABLED=1` 环境变量:

```bash
EXPLAIN_EMBEDDING_DISABLED=1 .venv/bin/python -m explain_engine.cli
> /new
> /compress
> /quit
```

验证:
- 启动快 (跳过 BGE-M3 load)
- 不 crash
- /compress 输出末尾仍有 dedup line, 但 reused 永远为 0 (没 embedding, 所有 candidates 算 new fallback)
- lexicon 写入的 entry 含 `embedding: null` (新 entry; 旧 entry 不动)

## Pass 标准

- ✅ 912+ tests pass (Phase 12: 867 baseline → Phase 13: 912)
- ✅ @pytest.mark.embedding tests local 全 pass (`.venv/bin/python -m pytest -m embedding`)
- ✅ ruff 0
- ✅ Step 1-5 全 pass
- ✅ Audit log JSONL 写入正确

## 已知 fail-safe

- BGE-M3 model load 失败 (网络问题首次 download / cache 损坏) → fallback string-match
- /compress prompt 长度涨 ~2k token (Top-K=20) → deepseek-v4-pro 128k context 充裕
- 跨 session lexicon 累积过多 (>1000 entry) → embeddings_matrix 仍能 µs 级 cosine search, JSON 文件膨胀到 4 MB+ 仍可读
- Audit log 写失败 → warning 不 crash
- 同 session 重复 flush_to_lexicon → idempotent (source_sessions 不重复 append, reuse_count 不 ++)

## 注意

- BGE-M3 model load cold start ~30-75s (大模型 weight loading). 后续调用走 singleton instance, ~1-3s embed batch=32.
- proxy text `f"{name} - {description}"` 与 lexicon canonical_mechanism 格式异构, 导致同义候选 cosine 实测 ~0.76-0.85. UI 用 display_threshold=0.75 (低于 merge 时的 0.85) 给更诚实统计. 实际 flush_to_lexicon 时仍用 0.85 严格 merge.
- audit log 仅记录 embedding-based merge, 不记录 Phase 10 hash-based exact merge (保持 audit 信号清洁).
