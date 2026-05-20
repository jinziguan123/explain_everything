# Variable Embedding (Candidate E, Medium scope) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** lexicon `canonical_mech` 用 BGE-M3 embedding 解 LLM 措辞漂移导致的同概念 split, 并在 /compress 加 pre+post filter 防 LLM 生重复概念.

**Architecture:** 4 component — (1) `BGE_M3_Embedder` singleton (lazy load, MPS fp16); (2) Lexicon schema 加 `embedding` field + lazy migration; (3) `lexicon_merge.py` cosine threshold-only 0.85 + audit log; (4) compression prompt 改 (Top-K pre-filter) + post-filter silent reuse.

**Tech Stack:** Python 3.11+, pytest, numpy (existing), FlagEmbedding>=1.3 (new), torch>=2.1 (new, MPS backend), BGE-M3 model (cached at `~/.cache/huggingface/hub/models--BAAI--bge-m3`, 4.3 GB).

**Related Design:** [docs/plans/2026-05-20-variable-embedding-design.md](2026-05-20-variable-embedding-design.md) (commit `ab7f430`)

---

## Project Conventions (MUST 遵守)

- 测试: `.venv/bin/python -m pytest` (NOT bare `python`)
- Lint: `.venv/bin/ruff check src/ tests/`
- Commit: 中文 HEREDOC + `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer (`(1M context)` 后缀必须)
- 分支 dev. NEVER push, NEVER --no-verify, NEVER amend.
- No emoji (除非 user 显式要)
- Pydantic v2 (`Field(default=...)`, `model_validator(mode="after")`)
- TDD: failing test → minimal impl → verify pass → commit
- `CognitiveState` 类在 `src/explain_engine/schema/state.py` (NOT `ChatState`)
- SessionMeta SID 必须 `^s_[0-9a-f]{8}$`

## Pre-flight Check (执行前必跑)

**!! CRITICAL !!**: SessionStart hook 可能让 Bash/Read 对 `~/Desktop/explain_everything` 报 `EPERM: Operation not permitted`. 第一个 task 前先 verify:

```bash
.venv/bin/python -c "print('ok')"
git -C /Users/jinziguan/Desktop/explain_everything status
```

如果报 EPERM, 让 user 去 System Settings → Privacy → Full Disk Access 给 Claude Code/Terminal 加权限, 然后重启 shell. 否则下面所有 task 都会卡.

Write 工具 + `dangerouslyDisableSandbox=true` Bash 命令仍 work (验证过), 但 Read + 普通 Bash 会受限. 实现时如撞此问题, 用 `Read 替代不了 Bash 时改用 dangerouslyDisableSandbox 短期 workaround`.

## File Path Conventions

所有 task 用绝对路径, 避免 cwd 切换问题:
- Project root: `/Users/jinziguan/Desktop/explain_everything`
- src 根: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/`
- tests 根: `/Users/jinziguan/Desktop/explain_everything/tests/`

---

## Wave 1: Embedder + Lexicon Storage 基础设施 (3-5 天)

### Task W1.1: 加 FlagEmbedding + torch deps

**Files:**
- Modify: `/Users/jinziguan/Desktop/explain_everything/pyproject.toml` `[project] dependencies`
- Modify (auto): `/Users/jinziguan/Desktop/explain_everything/uv.lock`

**Step 1: Read current pyproject deps**

```bash
grep -A 20 "^dependencies = " /Users/jinziguan/Desktop/explain_everything/pyproject.toml
```

Confirm current 13 deps incl. `graphviz>=0.20` (added in Phase 12). 期望 alphabetical order maintained.

**Step 2: Edit `pyproject.toml`** — 加 2 行 deps (alphabetical):

OLD line (before `graphviz`):
```toml
    "anthropic>=0.100.0",
    "graphviz>=0.20",
```

NEW:
```toml
    "anthropic>=0.100.0",
    "flagembedding>=1.3",   # Phase 13: BGE-M3 wrapper (Candidate E)
    "graphviz>=0.20",
```

OLD line (before `typer`):
```toml
    "tenacity>=9.1.4",
    "typer>=0.25.1",
```

NEW:
```toml
    "tenacity>=9.1.4",
    "torch>=2.1",           # Phase 13: MPS backend for BGE-M3
    "typer>=0.25.1",
]
```

**Step 3: Sync uv deps**

```bash
cd /Users/jinziguan/Desktop/explain_everything
uv sync
```

Expected: torch + FlagEmbedding installed; venv 涨 ~2-3 GB. 若 timeout (默认 2 min), 加 `--timeout 600` 或拆 sync 分两次.

**Step 4: Smoke import + GPU check**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -c "
import torch
from FlagEmbedding import BGEM3FlagModel

print(f'torch {torch.__version__}')
print(f'mps available: {torch.backends.mps.is_available()}')
print(f'mps built: {torch.backends.mps.is_built()}')

# Test model load (uses cached 4.3GB, no download)
model = BGEM3FlagModel('BAAI/bge-m3', use_fp16=True, device='mps')
out = model.encode(['测试句子'], return_dense=True)
print(f'embedding shape: {out[\"dense_vecs\"].shape}')
print(f'dtype: {out[\"dense_vecs\"].dtype}')
"
```

Expected:
```
torch 2.x.x
mps available: True
mps built: True
embedding shape: (1, 1024)
dtype: float32  # 即使 fp16 计算, 输出是 fp32 dense vec
```

如 `mps available: False`, 报给 user — 可能 PyTorch 装错 build.
如 model load >30s, 那是 cold cache, 之后会快.

**Step 5: Verify existing 867 test 仍 pass (no regression)**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest --tb=line 2>&1 | tail -3
```

Expected: 867 passed.

**Step 6: Commit**

```bash
git -C /Users/jinziguan/Desktop/explain_everything add pyproject.toml uv.lock
git -C /Users/jinziguan/Desktop/explain_everything commit -m "$(cat <<'EOF'
deps · 加 FlagEmbedding + torch (Candidate E Variable Embedding 前置)

Wave 1 Task 1: 装 FlagEmbedding>=1.3 (BGE-M3 wrapper) + torch>=2.1
(MPS backend). 系统 BGE-M3 model 已 cache 4.3GB 不重 download. smoke
import + mps detect + 编码 1 句 (1024-dim) 验证通过.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task W1.2: 新模块 BGE_M3_Embedder singleton

**Files:**
- Create: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/embedding/__init__.py`
- Create: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/embedding/bge_m3.py`
- Create: `/Users/jinziguan/Desktop/explain_everything/tests/embedding/__init__.py`
- Create: `/Users/jinziguan/Desktop/explain_everything/tests/embedding/test_bge_m3.py`

**Step 1: Create test directory skeleton**

```bash
mkdir -p /Users/jinziguan/Desktop/explain_everything/src/explain_engine/embedding
mkdir -p /Users/jinziguan/Desktop/explain_everything/tests/embedding
touch /Users/jinziguan/Desktop/explain_everything/src/explain_engine/embedding/__init__.py
touch /Users/jinziguan/Desktop/explain_everything/tests/embedding/__init__.py
```

**Step 2: Write failing tests** — Create `tests/embedding/test_bge_m3.py`:

```python
"""Phase 13 (2026-05-20): BGE_M3_Embedder singleton test.

Tests marked @pytest.mark.embedding (heavy, slow): default skip in CI
via pyproject.toml `addopts = "-m 'not integration and not embedding'"`.
Local dev runs explicitly: `pytest -m embedding`.
"""

import numpy as np
import pytest


pytestmark = pytest.mark.embedding


class TestBGEM3Embedder:
    def setup_method(self):
        """Reset singleton between tests."""
        from explain_engine.embedding import bge_m3
        bge_m3.BGE_M3_Embedder._instance = None

    def test_get_embedder_returns_singleton(self):
        from explain_engine.embedding.bge_m3 import get_embedder
        e1 = get_embedder()
        e2 = get_embedder()
        assert e1 is e2

    def test_embed_returns_shape(self):
        from explain_engine.embedding.bge_m3 import get_embedder
        e = get_embedder()
        vecs = e.embed(["测试句子 A", "测试句子 B"])
        assert vecs.shape == (2, 1024)
        assert vecs.dtype == np.float32

    def test_embed_single_text(self):
        from explain_engine.embedding.bge_m3 import get_embedder
        e = get_embedder()
        vecs = e.embed(["孤独的一句话"])
        assert vecs.shape == (1, 1024)

    def test_embed_empty_list_returns_empty(self):
        from explain_engine.embedding.bge_m3 import get_embedder
        e = get_embedder()
        vecs = e.embed([])
        assert vecs.shape == (0, 1024)

    def test_device_detection_uses_mps_on_apple(self):
        from explain_engine.embedding.bge_m3 import get_embedder
        import torch
        e = get_embedder()
        expected = "mps" if torch.backends.mps.is_available() else "cpu"
        assert e.device == expected

    def test_disabled_env_short_circuits(self, monkeypatch):
        """EXPLAIN_EMBEDDING_DISABLED=1 → get_embedder raises RuntimeError, callers fall back to string match."""
        monkeypatch.setenv("EXPLAIN_EMBEDDING_DISABLED", "1")
        from explain_engine.embedding import bge_m3
        bge_m3.BGE_M3_Embedder._instance = None
        from explain_engine.embedding.bge_m3 import get_embedder
        with pytest.raises(RuntimeError, match="EXPLAIN_EMBEDDING_DISABLED"):
            get_embedder()
```

**Step 3: Run failing tests**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/embedding/test_bge_m3.py -v -m embedding
```

Expected: 6 ERROR `ImportError: cannot import name 'BGE_M3_Embedder'`.

**Step 4: Write impl** — Create `src/explain_engine/embedding/bge_m3.py`:

```python
"""Phase 13 (2026-05-20): BGE-M3 dense embedding via FlagEmbedding + MPS.

Singleton (process-level) lazy-loaded model. Apple Silicon MPS backend
with fp16 (sweet spot: ~3-5x CPU, cosine precision loss < 0.001).

Disable via env var `EXPLAIN_EMBEDDING_DISABLED=1` for CI / no-GPU env;
callers should catch RuntimeError and fall back to string match.
"""

from __future__ import annotations

import os

import numpy as np


class BGE_M3_Embedder:
    """BGE-M3 dense embedding singleton."""

    _instance: BGE_M3_Embedder | None = None

    def __init__(self) -> None:
        if os.environ.get("EXPLAIN_EMBEDDING_DISABLED") == "1":
            raise RuntimeError(
                "EXPLAIN_EMBEDDING_DISABLED=1 — embedding disabled, "
                "caller should fall back to string match path."
            )

        import torch
        from FlagEmbedding import BGEM3FlagModel

        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model = BGEM3FlagModel(
            "BAAI/bge-m3",
            use_fp16=True,
            device=self.device,
        )

    def embed(self, texts: list[str]) -> np.ndarray:
        """Batch encode → (N, 1024) float32 dense embeddings.

        Empty list → (0, 1024) empty array (avoid downstream NaN).
        """
        if not texts:
            return np.zeros((0, 1024), dtype=np.float32)
        out = self.model.encode(texts, return_dense=True)
        vecs = out["dense_vecs"]
        return vecs.astype(np.float32, copy=False)


def get_embedder() -> BGE_M3_Embedder:
    """Lazy singleton accessor. First call loads BGE-M3 ~3-5s (cached).

    Raises RuntimeError if EXPLAIN_EMBEDDING_DISABLED=1 — caller falls back.
    """
    if BGE_M3_Embedder._instance is None:
        BGE_M3_Embedder._instance = BGE_M3_Embedder()
    return BGE_M3_Embedder._instance
```

**Step 5: Add `embedding` marker to `pyproject.toml`**

Edit `[tool.pytest.ini_options]` section. Current:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "integration: 需要真实 LLM API 的集成测试，CI 默认跳过",
]
addopts = "-m 'not integration'"
```

Update to:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "integration: 需要真实 LLM API 的集成测试，CI 默认跳过",
    "embedding: 需要 BGE-M3 model (4.3GB local) + MPS GPU, CI 默认跳过",
]
addopts = "-m 'not integration and not embedding'"
```

**Step 6: Run tests with explicit -m embedding**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/embedding/test_bge_m3.py -v -m embedding
```

Expected: 6 PASS (first run will cold-load model ~5-30s; subsequent ~1s).

**Step 7: Run full suite without embedding tests (verify no regression)**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest --tb=line 2>&1 | tail -3
```

Expected: 867 passed (embedding tests skipped by default).

**Step 8: Lint**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/ruff check src/ tests/
```

Expected: 0.

**Step 9: Commit**

```bash
git -C /Users/jinziguan/Desktop/explain_everything add \
  pyproject.toml \
  src/explain_engine/embedding/ \
  tests/embedding/
git -C /Users/jinziguan/Desktop/explain_everything commit -m "$(cat <<'EOF'
embedding · 加 BGE_M3_Embedder singleton (lazy load + MPS fp16)

Wave 1 Task 2: 新模块 src/explain_engine/embedding/bge_m3.py. 进程
级 singleton, lazy 首调 load BGE-M3 (~3-5s cached), Apple Silicon
MPS backend + fp16. EXPLAIN_EMBEDDING_DISABLED=1 env 短路给 CI /
no-GPU 用. 加 pytest marker 'embedding' 默认 skip. 6 unit test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task W1.3: Lexicon schema 加 embedding 字段

**Files:**
- Modify: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/lexicon.py` (pydantic schema)
- Modify: `/Users/jinziguan/Desktop/explain_everything/tests/test_lexicon.py` (existing test file)

**Step 1: Read current lexicon schema**

```bash
grep -n "class.*Entry\|embedding\|canonical_mech" /Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/lexicon.py | head -10
```

Likely `LexiconEntry` Pydantic model with fields like `entry_id`, `canonical_mech`, `evidence_ids`, `reuse_count`, etc. 找到 schema 行号.

**Step 2: Write failing test** — Append to `tests/test_lexicon.py` (or 新 `tests/test_lexicon_embedding.py`):

```python
class TestLexiconEntryEmbedding:
    """Phase 13 Wave 1 Task 3: LexiconEntry 加 embedding 字段."""

    def test_entry_default_embedding_none(self):
        """Old entries lacking embedding still validate."""
        from explain_engine.engines.lexicon import LexiconEntry
        e = LexiconEntry(
            entry_id="lex_001",
            canonical_mech="测试机制",
            evidence_ids=["e_001"],
            reuse_count=1,
        )
        assert e.embedding is None

    def test_entry_with_embedding_validates(self):
        from explain_engine.engines.lexicon import LexiconEntry
        e = LexiconEntry(
            entry_id="lex_002",
            canonical_mech="测试机制",
            evidence_ids=["e_001"],
            reuse_count=1,
            embedding=[0.1] * 1024,
        )
        assert e.embedding is not None
        assert len(e.embedding) == 1024

    def test_entry_embedding_wrong_dim_rejected(self):
        """Pydantic 应 reject 维度 ≠ 1024 的 embedding (defensive)."""
        from pydantic import ValidationError

        from explain_engine.engines.lexicon import LexiconEntry
        with pytest.raises(ValidationError, match="embedding"):
            LexiconEntry(
                entry_id="lex_003",
                canonical_mech="测试",
                evidence_ids=[],
                reuse_count=1,
                embedding=[0.1] * 512,  # wrong dim
            )

    def test_entry_round_trip_json_preserves_embedding(self):
        """model_dump → model_validate 保 embedding 精度."""
        from explain_engine.engines.lexicon import LexiconEntry
        emb = [0.1, 0.2, 0.3] + [0.0] * 1021
        e = LexiconEntry(
            entry_id="lex_004",
            canonical_mech="测试",
            evidence_ids=[],
            reuse_count=1,
            embedding=emb,
        )
        dumped = e.model_dump()
        restored = LexiconEntry.model_validate(dumped)
        assert restored.embedding == emb
```

**Step 3: Run failing test**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_lexicon.py::TestLexiconEntryEmbedding -v
```

Expected: 4 FAIL (Pydantic validation error: unknown field `embedding`).

**Step 4: Modify `LexiconEntry` Pydantic model**

Find `class LexiconEntry(BaseModel):` in `src/explain_engine/engines/lexicon.py`. Add `embedding` field with validator:

```python
from pydantic import BaseModel, Field, field_validator


class LexiconEntry(BaseModel):
    # ... existing fields ...

    embedding: list[float] | None = Field(
        default=None,
        description="BGE-M3 dense vector (1024-dim float32). None for legacy entries pre-Phase 13.",
    )

    @field_validator("embedding")
    @classmethod
    def _validate_embedding_dim(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and len(v) != 1024:
            raise ValueError(
                f"embedding must be 1024-dim (BGE-M3), got {len(v)}"
            )
        return v
```

**Step 5: Run tests → PASS**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_lexicon.py::TestLexiconEntryEmbedding -v
```

Expected: 4 PASS.

**Step 6: Verify no regression**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest --tb=line 2>&1 | tail -3
```

Expected: 867 + 4 = 871 PASS.

**Step 7: Lint**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/ruff check src/ tests/
```

Expected: 0.

**Step 8: Commit**

```bash
git -C /Users/jinziguan/Desktop/explain_everything add \
  src/explain_engine/engines/lexicon.py \
  tests/test_lexicon.py
git -C /Users/jinziguan/Desktop/explain_everything commit -m "$(cat <<'EOF'
engines/lexicon · LexiconEntry 加 embedding 字段 (Phase 13 Wave 1)

Wave 1 Task 3: Pydantic schema 加 'embedding: list[float] | None'
(默认 None 兼容 Phase 10/11 老 entry), field_validator 强制 1024
维 (BGE-M3). model_dump round-trip 保精度. 4 unit test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task W1.4: Lexicon 启动加载 stack embeddings 到 ndarray

**Files:**
- Modify: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/lexicon.py` (load 函数)
- Modify: `/Users/jinziguan/Desktop/explain_everything/tests/test_lexicon.py`

**Step 1: Identify lexicon load function**

```bash
grep -n "def load_lexicon\|def _load\|json.load" /Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/lexicon.py | head -5
```

定位 lexicon load 入口 — 可能是 `load_lexicon()` 函数 或 `Lexicon` 类的 `__init__` / `.load()` 方法.

**Step 2: Write failing test**

```python
class TestLexiconEmbeddingMatrix:
    """Phase 13 Wave 1 Task 4: load 时 stack embeddings → ndarray."""

    def test_empty_lexicon_matrix_shape(self, tmp_path):
        """0 entries → ndarray shape (0, 1024)."""
        # build empty lexicon JSON
        from explain_engine.engines.lexicon import Lexicon
        lex_path = tmp_path / "lexicon.json"
        lex_path.write_text('{"entries": [], "version": 2}')
        lex = Lexicon.load(lex_path)
        assert lex.embeddings_matrix.shape == (0, 1024)

    def test_two_entries_matrix_shape(self, tmp_path):
        import json
        lex_path = tmp_path / "lexicon.json"
        data = {
            "version": 2,
            "entries": [
                {
                    "entry_id": "lex_001",
                    "canonical_mech": "m1",
                    "evidence_ids": [],
                    "reuse_count": 1,
                    "embedding": [0.1] * 1024,
                },
                {
                    "entry_id": "lex_002",
                    "canonical_mech": "m2",
                    "evidence_ids": [],
                    "reuse_count": 1,
                    "embedding": [0.2] * 1024,
                },
            ],
        }
        lex_path.write_text(json.dumps(data))
        from explain_engine.engines.lexicon import Lexicon
        lex = Lexicon.load(lex_path)
        assert lex.embeddings_matrix.shape == (2, 1024)
        assert lex.embeddings_matrix[0, 0] == pytest.approx(0.1)
        assert lex.embeddings_matrix[1, 0] == pytest.approx(0.2)

    def test_entry_with_none_embedding_excluded_from_matrix(self, tmp_path):
        """Entries with embedding=None 不入 matrix; .entry_id_to_matrix_idx 跳过."""
        import json
        lex_path = tmp_path / "lexicon.json"
        data = {
            "version": 2,
            "entries": [
                {
                    "entry_id": "lex_001",
                    "canonical_mech": "m1",
                    "evidence_ids": [],
                    "reuse_count": 1,
                    "embedding": None,
                },
                {
                    "entry_id": "lex_002",
                    "canonical_mech": "m2",
                    "evidence_ids": [],
                    "reuse_count": 1,
                    "embedding": [0.1] * 1024,
                },
            ],
        }
        lex_path.write_text(json.dumps(data))
        from explain_engine.engines.lexicon import Lexicon
        lex = Lexicon.load(lex_path)
        assert lex.embeddings_matrix.shape == (1, 1024)  # only lex_002
        assert "lex_002" in lex.entry_id_to_matrix_idx
        assert "lex_001" not in lex.entry_id_to_matrix_idx
```

**Step 3: Run failing test → FAIL**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_lexicon.py::TestLexiconEmbeddingMatrix -v
```

Expected: 3 ERROR `AttributeError: 'Lexicon' object has no attribute 'embeddings_matrix'`.

**Step 4: Modify `Lexicon` class** — Add `embeddings_matrix` + `entry_id_to_matrix_idx`:

In `Lexicon` class `load` method (or `__init__`):

```python
import numpy as np


class Lexicon:
    # ... existing ...

    embeddings_matrix: np.ndarray         # shape (M, 1024); M ≤ N (excludes None-embedding entries)
    entry_id_to_matrix_idx: dict[str, int]  # entry_id → row index in embeddings_matrix

    @classmethod
    def load(cls, path: Path) -> Lexicon:
        # ... existing JSON load + Pydantic validate ...
        # entries: list[LexiconEntry] populated

        # Build embeddings matrix (skip entries with embedding=None)
        embeddings_list = []
        id_to_idx = {}
        for entry in entries:
            if entry.embedding is not None:
                id_to_idx[entry.entry_id] = len(embeddings_list)
                embeddings_list.append(entry.embedding)

        embeddings_matrix = (
            np.array(embeddings_list, dtype=np.float32)
            if embeddings_list
            else np.zeros((0, 1024), dtype=np.float32)
        )

        lex = cls(entries=entries, ...)  # 现有构造
        lex.embeddings_matrix = embeddings_matrix
        lex.entry_id_to_matrix_idx = id_to_idx
        return lex
```

**Step 5: Run tests → PASS**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_lexicon.py::TestLexiconEmbeddingMatrix -v
```

Expected: 3 PASS.

**Step 6: Verify no regression**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest --tb=line 2>&1 | tail -3
```

Expected: 871 + 3 = 874 PASS.

**Step 7: Lint**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/ruff check src/ tests/
```

Expected: 0.

**Step 8: Commit**

```bash
git -C /Users/jinziguan/Desktop/explain_everything add \
  src/explain_engine/engines/lexicon.py \
  tests/test_lexicon.py
git -C /Users/jinziguan/Desktop/explain_everything commit -m "$(cat <<'EOF'
engines/lexicon · load 时 stack embeddings → ndarray (Phase 13 Wave 1)

Wave 1 Task 4: Lexicon.load() 把所有有 embedding 的 entry vec stack
成 self.embeddings_matrix: ndarray (M, 1024) + entry_id_to_matrix_idx
映射 (跳过 embedding=None 的 entry). 后续 cosine batch op 用此 matrix.
3 unit test (empty / 2 entries / None excluded).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task W1.5: Lazy migration (启动 batch embed 缺字段 entry)

**Files:**
- Modify: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/lexicon.py` (migration hook)
- Modify: `/Users/jinziguan/Desktop/explain_everything/tests/test_lexicon.py`

**Step 1: Write failing test**

```python
class TestLexiconLazyMigration:
    """Phase 13 Wave 1 Task 5: load 时 lazy batch embed 缺 embedding 的 entry."""

    @pytest.mark.embedding
    def test_missing_embedding_batch_embeds_on_load(self, tmp_path, monkeypatch):
        """老 entry 缺 embedding → load 自动 batch embed + 回写 JSON."""
        import json
        lex_path = tmp_path / "lexicon.json"
        data = {
            "version": 2,
            "entries": [
                {
                    "entry_id": "lex_001",
                    "canonical_mech": "经济压力上升",
                    "evidence_ids": [],
                    "reuse_count": 1,
                    "embedding": None,
                },
                {
                    "entry_id": "lex_002",
                    "canonical_mech": "保守消费倾向",
                    "evidence_ids": [],
                    "reuse_count": 1,
                    "embedding": None,
                },
            ],
        }
        lex_path.write_text(json.dumps(data))

        from explain_engine.engines.lexicon import Lexicon
        lex = Lexicon.load(lex_path)

        # 所有 entry 应有 embedding
        for e in lex.entries:
            assert e.embedding is not None
            assert len(e.embedding) == 1024

        # JSON 文件应回写
        reloaded = json.loads(lex_path.read_text())
        assert reloaded["entries"][0]["embedding"] is not None
        assert reloaded["entries"][1]["embedding"] is not None

    def test_disabled_env_skips_migration(self, tmp_path, monkeypatch):
        """EXPLAIN_EMBEDDING_DISABLED=1 时跳过 migration, entry 保 None."""
        monkeypatch.setenv("EXPLAIN_EMBEDDING_DISABLED", "1")
        import json
        lex_path = tmp_path / "lexicon.json"
        data = {
            "version": 2,
            "entries": [{
                "entry_id": "lex_001",
                "canonical_mech": "经济压力上升",
                "evidence_ids": [],
                "reuse_count": 1,
                "embedding": None,
            }],
        }
        lex_path.write_text(json.dumps(data))

        from explain_engine.engines.lexicon import Lexicon
        lex = Lexicon.load(lex_path)
        assert lex.entries[0].embedding is None  # not migrated
        # JSON 也不应改写
        reloaded = json.loads(lex_path.read_text())
        assert reloaded["entries"][0]["embedding"] is None

    @pytest.mark.embedding
    def test_partial_migration(self, tmp_path):
        """已有部分 embedding → 只 migrate 缺的."""
        import json
        lex_path = tmp_path / "lexicon.json"
        data = {
            "version": 2,
            "entries": [
                {
                    "entry_id": "lex_001",
                    "canonical_mech": "已有 embedding",
                    "evidence_ids": [],
                    "reuse_count": 1,
                    "embedding": [0.5] * 1024,  # 占位 (非真 BGE-M3 输出)
                },
                {
                    "entry_id": "lex_002",
                    "canonical_mech": "缺 embedding 需 migrate",
                    "evidence_ids": [],
                    "reuse_count": 1,
                    "embedding": None,
                },
            ],
        }
        lex_path.write_text(json.dumps(data))

        from explain_engine.engines.lexicon import Lexicon
        lex = Lexicon.load(lex_path)

        # lex_001 保持原 embedding (不重 embed)
        assert lex.entries[0].embedding == [0.5] * 1024
        # lex_002 已 migrate
        assert lex.entries[1].embedding is not None
        assert lex.entries[1].embedding != [0.5] * 1024  # 真 BGE-M3 输出 ≠ 占位
```

**Step 2: Run failing tests → FAIL**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_lexicon.py::TestLexiconLazyMigration -v
```

Expected: 3 FAIL — entry.embedding 仍 None.

**Step 3: Implement migration in `Lexicon.load`**

In `Lexicon.load(path)`, AFTER pydantic validate but BEFORE building matrix:

```python
@classmethod
def load(cls, path: Path) -> Lexicon:
    # ... 现有 JSON load + LexiconEntry validate ...

    # ─── Phase 13 lazy migration: batch embed 缺 embedding 的 entry ───
    needs_migration = [e for e in entries if e.embedding is None]
    if needs_migration and os.environ.get("EXPLAIN_EMBEDDING_DISABLED") != "1":
        try:
            from explain_engine.embedding.bge_m3 import get_embedder
            from rich.console import Console
            console = Console()
            with console.status(
                f"首次升级 lexicon embedding: {len(needs_migration)} entries...",
                spinner="dots",
            ):
                embedder = get_embedder()
                texts = [e.canonical_mech for e in needs_migration]
                vecs = embedder.embed(texts)
                for entry, vec in zip(needs_migration, vecs):
                    entry.embedding = vec.tolist()
            # 回写 JSON
            cls._write_back(path, entries)
        except Exception as exc:
            # migration failure → 不阻塞, entry 保 None 走 fallback
            import logging
            logging.warning(
                f"Lexicon migration failed: {type(exc).__name__}: {exc}. "
                "Falling back to string-match path for entries lacking embedding."
            )

    # ─── 现有 matrix build (上一 task) ───
    # ...
```

加 helper:

```python
@classmethod
def _write_back(cls, path: Path, entries: list[LexiconEntry]) -> None:
    """Atomic write: tmp file → rename. 避免 partial write."""
    import json
    tmp = path.with_suffix(".tmp")
    data = {
        "version": 2,
        "entries": [e.model_dump() for e in entries],
    }
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)
```

**Step 4: Run tests → PASS**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_lexicon.py::TestLexiconLazyMigration -v -m "embedding or not embedding"
```

Expected: 3 PASS (2 embedding marker tests + 1 disabled-env without marker).

**Step 5: Verify no regression**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest --tb=line 2>&1 | tail -3
```

Expected: 874 + 1 = 875 PASS (only `test_disabled_env_skips_migration` runs by default; embedding marker tests skipped).

**Step 6: Lint**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/ruff check src/ tests/
```

Expected: 0.

**Step 7: Commit**

```bash
git -C /Users/jinziguan/Desktop/explain_everything add \
  src/explain_engine/engines/lexicon.py \
  tests/test_lexicon.py
git -C /Users/jinziguan/Desktop/explain_everything commit -m "$(cat <<'EOF'
engines/lexicon · 加 lazy migration (Phase 13 Wave 1)

Wave 1 Task 5: Lexicon.load() 检 entry 缺 embedding, 触发 lazy
batch embed (Rich status spinner 进度) 并 atomic write-back JSON.
EXPLAIN_EMBEDDING_DISABLED=1 跳过. Migration 失败不阻塞 (warning
+ fallback string-match path). 3 unit test (real embed / disabled
env / partial migration).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task W1.6: Wave 1 verify (full suite + lint)

**Step 1: Full test suite**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest --tb=short 2>&1 | tail -5
```

Expected: 875 PASS, 0 FAIL.

**Step 2: Lint**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/ruff check src/ tests/
```

Expected: 0.

**Step 3: Embedding tests explicit**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest -m embedding -v 2>&1 | tail -15
```

Expected: ~11-12 embedding tests PASS (6 BGE_M3 + 2 migration real-embed + ...) .

**Step 4: 若全 pass, Wave 1 完成. 不另开 commit.**

---

## Wave 2: Merge Logic + Lexicon Dedup Integration (3-4 天)

### Task W2.1: lexicon_merge.find_duplicate cosine

**Files:**
- Create: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/lexicon_merge.py`
- Create: `/Users/jinziguan/Desktop/explain_everything/tests/test_lexicon_merge.py`

**Step 1: Write failing tests** — Create `tests/test_lexicon_merge.py`:

```python
"""Phase 13 Wave 2 Task 1: lexicon_merge.find_duplicate cosine logic."""

import numpy as np
import pytest


class TestFindDuplicate:
    def test_empty_matrix_returns_none(self):
        from explain_engine.engines.lexicon_merge import find_duplicate
        empty = np.zeros((0, 1024), dtype=np.float32)
        new = np.random.randn(1024).astype(np.float32)
        assert find_duplicate(new, empty) is None

    def test_identical_vector_returns_index(self):
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.random.randn(3, 1024).astype(np.float32)
        # New = exact copy of row 1
        idx = find_duplicate(existing[1], existing)
        assert idx == 1

    def test_orthogonal_vector_returns_none(self):
        """Orthogonal (cos=0) → below 0.85 threshold → None."""
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.array([[1.0, 0.0] + [0.0] * 1022], dtype=np.float32)
        new = np.array([0.0, 1.0] + [0.0] * 1022, dtype=np.float32)
        assert find_duplicate(new, existing) is None

    def test_high_similarity_returns_index(self):
        """cos sim 0.99 (well above 0.85) → return index."""
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.array([[1.0, 0.0] + [0.0] * 1022], dtype=np.float32)
        # New = small perturbation of existing
        new = np.array([0.999, 0.045] + [0.0] * 1022, dtype=np.float32)
        new = new / np.linalg.norm(new)
        assert find_duplicate(new, existing) == 0

    def test_threshold_boundary_below(self):
        """cos sim slightly below 0.85 → None."""
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.array([[1.0, 0.0] + [0.0] * 1022], dtype=np.float32)
        # sim = cos(0.85_below) ≈ 0.84
        # construct vec at angle ~32° (cos 32° ≈ 0.848)
        theta = np.arccos(0.84)
        new = np.array(
            [np.cos(theta), np.sin(theta)] + [0.0] * 1022,
            dtype=np.float32,
        )
        assert find_duplicate(new, existing) is None

    def test_threshold_boundary_above(self):
        """cos sim slightly above 0.85 → return index."""
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.array([[1.0, 0.0] + [0.0] * 1022], dtype=np.float32)
        theta = np.arccos(0.86)
        new = np.array(
            [np.cos(theta), np.sin(theta)] + [0.0] * 1022,
            dtype=np.float32,
        )
        assert find_duplicate(new, existing) == 0

    def test_multiple_above_picks_max(self):
        """Multiple entries above threshold → return argmax index."""
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.array([
            [1.0, 0.0] + [0.0] * 1022,
            [0.9, 0.1] + [0.0] * 1022,
        ], dtype=np.float32)
        existing = existing / np.linalg.norm(existing, axis=1, keepdims=True)
        new = existing[0].copy()  # closest to row 0
        assert find_duplicate(new, existing) == 0

    def test_custom_threshold(self):
        """Pass custom threshold (e.g., 0.95) for stricter merge."""
        from explain_engine.engines.lexicon_merge import find_duplicate
        existing = np.array([[1.0, 0.0] + [0.0] * 1022], dtype=np.float32)
        theta = np.arccos(0.86)
        new = np.array(
            [np.cos(theta), np.sin(theta)] + [0.0] * 1022,
            dtype=np.float32,
        )
        # Default 0.85: returns 0; custom 0.95: returns None
        assert find_duplicate(new, existing, threshold=0.95) is None
```

**Step 2: Run failing tests → FAIL**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_lexicon_merge.py -v
```

Expected: 8 ERROR `ImportError: cannot import name 'find_duplicate'`.

**Step 3: Write impl** — Create `src/explain_engine/engines/lexicon_merge.py`:

```python
"""Phase 13 Wave 2: cosine similarity merge logic for lexicon dedup."""

from __future__ import annotations

import numpy as np


LEXICON_MERGE_THRESHOLD = 0.85
"""Cosine similarity threshold for merging lexicon entries.

Hard-coded MVP. BGE-M3 中文同义句典型 cosine 0.85+. False merge 风险
低于 0.05 在 manual smoke; 边界 case 由 audit log 事后修正.
"""


def find_duplicate(
    new_emb: np.ndarray,
    existing_matrix: np.ndarray,
    threshold: float = LEXICON_MERGE_THRESHOLD,
) -> int | None:
    """Return existing_matrix row index of max cosine sim if > threshold, else None.

    Args:
        new_emb: shape (1024,), candidate vector
        existing_matrix: shape (N, 1024), N = lexicon entries with embedding
        threshold: cosine cutoff (default 0.85)

    Returns:
        int idx if max(cos) > threshold, else None
    """
    if existing_matrix.shape[0] == 0:
        return None

    # Normalize for cosine
    new_norm = np.linalg.norm(new_emb)
    existing_norms = np.linalg.norm(existing_matrix, axis=1)
    denoms = existing_norms * new_norm
    denoms = np.maximum(denoms, 1e-9)  # avoid div0

    sims = (existing_matrix @ new_emb) / denoms
    max_idx = int(np.argmax(sims))
    return max_idx if sims[max_idx] > threshold else None
```

**Step 4: Run tests → PASS**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_lexicon_merge.py -v
```

Expected: 8 PASS.

**Step 5: Verify no regression**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest --tb=line 2>&1 | tail -3
```

Expected: 875 + 8 = 883 PASS.

**Step 6: Lint**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/ruff check src/ tests/
```

Expected: 0.

**Step 7: Commit**

```bash
git -C /Users/jinziguan/Desktop/explain_everything add \
  src/explain_engine/engines/lexicon_merge.py \
  tests/test_lexicon_merge.py
git -C /Users/jinziguan/Desktop/explain_everything commit -m "$(cat <<'EOF'
engines/lexicon_merge · find_duplicate cosine threshold-only (Phase 13 Wave 2)

Wave 2 Task 1: 新模块 lexicon_merge.py. find_duplicate(new_emb,
existing_matrix, threshold=0.85) batch cosine 找 max sim > threshold
的 entry index, 无则 None. 空 matrix 短路, 边界 case test (0.84/
0.86) 全覆盖. 8 unit test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task W2.2: Audit log writer

**Files:**
- Modify: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/lexicon_merge.py`
- Modify: `/Users/jinziguan/Desktop/explain_everything/tests/test_lexicon_merge.py`

**Step 1: Write failing test**

```python
class TestMergeAuditLog:
    """Phase 13 Wave 2 Task 2: audit log on merge events."""

    def test_write_audit_record(self, tmp_path):
        from explain_engine.engines.lexicon_merge import write_merge_audit
        log_dir = tmp_path / "logs"
        write_merge_audit(
            log_dir=log_dir,
            merged_into="lex_001",
            merged_from="lex_pending_xxx",
            sim=0.91,
            evidence_ids=["e_001", "e_002"],
        )
        # Find log file (date-stamped)
        log_files = list(log_dir.glob("lexicon_merge_*.jsonl"))
        assert len(log_files) == 1
        content = log_files[0].read_text()
        import json
        rec = json.loads(content.strip())
        assert rec["merged_into"] == "lex_001"
        assert rec["merged_from"] == "lex_pending_xxx"
        assert rec["sim"] == pytest.approx(0.91)
        assert rec["evidence_ids"] == ["e_001", "e_002"]
        assert "timestamp" in rec

    def test_append_multiple_same_day(self, tmp_path):
        from explain_engine.engines.lexicon_merge import write_merge_audit
        log_dir = tmp_path / "logs"
        for i in range(3):
            write_merge_audit(
                log_dir=log_dir,
                merged_into=f"lex_{i:03d}",
                merged_from="lex_pending",
                sim=0.9,
                evidence_ids=[],
            )
        log_files = list(log_dir.glob("lexicon_merge_*.jsonl"))
        assert len(log_files) == 1
        lines = log_files[0].read_text().strip().split("\n")
        assert len(lines) == 3

    def test_log_dir_creates_if_missing(self, tmp_path):
        from explain_engine.engines.lexicon_merge import write_merge_audit
        log_dir = tmp_path / "nonexistent" / "logs"
        write_merge_audit(
            log_dir=log_dir,
            merged_into="x",
            merged_from="y",
            sim=0.9,
            evidence_ids=[],
        )
        assert log_dir.is_dir()
```

**Step 2: Run failing → FAIL**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_lexicon_merge.py::TestMergeAuditLog -v
```

Expected: 3 ERROR `ImportError: cannot import name 'write_merge_audit'`.

**Step 3: Write impl** — Append to `src/explain_engine/engines/lexicon_merge.py`:

```python
import json
from datetime import datetime
from pathlib import Path


def write_merge_audit(
    log_dir: Path,
    merged_into: str,
    merged_from: str,
    sim: float,
    evidence_ids: list[str],
) -> None:
    """Append JSONL record to logs/lexicon_merge_<YYYY-MM-DD>.jsonl.

    Each merge writes 1 line for post-hoc audit. Failures swallowed
    (warning) — audit log shouldn't block lexicon writes.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_path = log_dir / f"lexicon_merge_{date_str}.jsonl"

    record = {
        "timestamp": datetime.now().isoformat(),
        "merged_into": merged_into,
        "merged_from": merged_from,
        "sim": float(sim),
        "evidence_ids": evidence_ids,
    }
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        import logging
        logging.warning(f"audit log write failed: {exc}")
```

**Step 4: Run tests → PASS**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_lexicon_merge.py::TestMergeAuditLog -v
```

Expected: 3 PASS.

**Step 5: Verify no regression + lint + commit**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest --tb=line 2>&1 | tail -3
/Users/jinziguan/Desktop/explain_everything/.venv/bin/ruff check src/ tests/

git -C /Users/jinziguan/Desktop/explain_everything add \
  src/explain_engine/engines/lexicon_merge.py \
  tests/test_lexicon_merge.py
git -C /Users/jinziguan/Desktop/explain_everything commit -m "$(cat <<'EOF'
engines/lexicon_merge · 加 write_merge_audit JSONL (Phase 13 Wave 2)

Wave 2 Task 2: write_merge_audit(log_dir, merged_into, merged_from,
sim, evidence_ids) 追加 JSONL 进 logs/lexicon_merge_<YYYY-MM-DD>.
jsonl. log_dir 不存在自动 mkdir, 写失败 warn 不 raise. 3 unit test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: 886 PASS, ruff 0.

---

### Task W2.3: 集成 merge 进 flush_to_lexicon

**Files:**
- Modify: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/lexicon.py` (`flush_to_lexicon` 函数)
- Modify: `/Users/jinziguan/Desktop/explain_everything/tests/test_lexicon.py`

**Step 1: Read current flush_to_lexicon**

```bash
grep -n "def flush_to_lexicon\|llm_canonical_top_k" /Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/lexicon.py | head -5
```

定位写入函数. 应该在 lexicon module 顶层或 Lexicon 类方法.

**Step 2: Write failing test**

```python
class TestFlushToLexiconDedup:
    """Phase 13 Wave 2 Task 3: flush_to_lexicon 用 embedding merge."""

    @pytest.mark.embedding
    def test_duplicate_canonical_merges(self, tmp_path):
        """同义 canonical_mech 应 merge 不新建."""
        from explain_engine.engines.lexicon import Lexicon, flush_to_lexicon

        lex_path = tmp_path / "lexicon.json"
        # 初始 1 entry
        lex_path.write_text(
            '{"version": 2, "entries": [{'
            '"entry_id": "lex_001",'
            '"canonical_mech": "经济不安全感导致防御性储蓄上升",'
            '"evidence_ids": ["e_001"],'
            '"reuse_count": 1,'
            '"embedding": null'
            '}]}'
        )
        # First load triggers migration → lex_001 gets embedding
        lex = Lexicon.load(lex_path)
        assert lex.entries[0].embedding is not None

        # 同义新 candidate flush
        flush_to_lexicon(
            lexicon=lex,
            canonical_mech="对未来收入预期下降引发储蓄倾向",  # 同义
            evidence_ids=["e_002"],
            log_dir=tmp_path / "logs",
        )

        # 仍 1 entry, evidence_ids 合并
        assert len(lex.entries) == 1
        assert set(lex.entries[0].evidence_ids) == {"e_001", "e_002"}
        assert lex.entries[0].reuse_count == 2

        # Audit log 写了
        log_files = list((tmp_path / "logs").glob("lexicon_merge_*.jsonl"))
        assert len(log_files) == 1

    @pytest.mark.embedding
    def test_distinct_canonical_creates_new(self, tmp_path):
        """语义无关 → 新建 entry."""
        from explain_engine.engines.lexicon import Lexicon, flush_to_lexicon

        lex_path = tmp_path / "lexicon.json"
        lex_path.write_text(
            '{"version": 2, "entries": [{'
            '"entry_id": "lex_001",'
            '"canonical_mech": "房价上涨",'
            '"evidence_ids": ["e_001"],'
            '"reuse_count": 1,'
            '"embedding": null'
            '}]}'
        )
        lex = Lexicon.load(lex_path)

        flush_to_lexicon(
            lexicon=lex,
            canonical_mech="老龄化人口结构",  # 完全不同主题
            evidence_ids=["e_002"],
            log_dir=tmp_path / "logs",
        )

        assert len(lex.entries) == 2
        # 新 entry 有 embedding
        assert lex.entries[1].embedding is not None
        # 老 entry evidence 不动
        assert lex.entries[0].evidence_ids == ["e_001"]

    def test_disabled_env_string_match_fallback(self, tmp_path, monkeypatch):
        """EXPLAIN_EMBEDDING_DISABLED=1 → 走 string-match 旧路径."""
        monkeypatch.setenv("EXPLAIN_EMBEDDING_DISABLED", "1")
        from explain_engine.engines.lexicon import Lexicon, flush_to_lexicon

        lex_path = tmp_path / "lexicon.json"
        lex_path.write_text(
            '{"version": 2, "entries": [{'
            '"entry_id": "lex_001",'
            '"canonical_mech": "exact match string",'
            '"evidence_ids": ["e_001"],'
            '"reuse_count": 1,'
            '"embedding": null'
            '}]}'
        )
        lex = Lexicon.load(lex_path)

        # Exact string → merge
        flush_to_lexicon(
            lexicon=lex,
            canonical_mech="exact match string",
            evidence_ids=["e_002"],
            log_dir=tmp_path / "logs",
        )
        assert len(lex.entries) == 1
        assert set(lex.entries[0].evidence_ids) == {"e_001", "e_002"}

        # Different string → new entry
        flush_to_lexicon(
            lexicon=lex,
            canonical_mech="completely different",
            evidence_ids=["e_003"],
            log_dir=tmp_path / "logs",
        )
        assert len(lex.entries) == 2
```

**Step 3: Run failing tests → FAIL**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_lexicon.py::TestFlushToLexiconDedup -v
```

Expected: 3 FAIL (现有 `flush_to_lexicon` 还是 string-match, 同义 case 创新 entry 而非 merge).

**Step 4: Modify `flush_to_lexicon`**

Find current function (likely top-level in `lexicon.py` 或 `Lexicon` 类方法). Update with embedding path + string-match fallback:

```python
def flush_to_lexicon(
    lexicon: Lexicon,
    canonical_mech: str,
    evidence_ids: list[str],
    log_dir: Path | None = None,
) -> None:
    """Append candidate to lexicon, dedup via embedding (default) or string match.

    If EXPLAIN_EMBEDDING_DISABLED=1 or embedder fails: fall back to exact
    string match on canonical_mech.

    Side effects:
        - merge hit: lexicon.entries[idx].evidence_ids extended,
          reuse_count++, audit log written
        - no hit: new LexiconEntry appended; embedding included if embedder OK
    """
    import os
    use_embedding = os.environ.get("EXPLAIN_EMBEDDING_DISABLED") != "1"

    hit_idx: int | None = None
    sim_value: float | None = None  # for audit log

    if use_embedding:
        try:
            from explain_engine.embedding.bge_m3 import get_embedder
            from explain_engine.engines.lexicon_merge import (
                LEXICON_MERGE_THRESHOLD,
                find_duplicate,
            )
            embedder = get_embedder()
            new_emb = embedder.embed([canonical_mech])[0]
            hit_idx = find_duplicate(
                new_emb,
                lexicon.embeddings_matrix,
                threshold=LEXICON_MERGE_THRESHOLD,
            )
            if hit_idx is not None:
                # convert matrix idx → entry idx (matrix may skip None-embedding entries)
                # rebuild mapping inverse
                idx_to_entry_id = {
                    v: k for k, v in lexicon.entry_id_to_matrix_idx.items()
                }
                hit_entry_id = idx_to_entry_id[hit_idx]
                hit_entry_idx = next(
                    i for i, e in enumerate(lexicon.entries)
                    if e.entry_id == hit_entry_id
                )
                # compute sim for audit
                row = lexicon.embeddings_matrix[hit_idx]
                sim_value = float(
                    np.dot(row, new_emb) /
                    (np.linalg.norm(row) * np.linalg.norm(new_emb))
                )
                hit_idx = hit_entry_idx
        except Exception as exc:
            import logging
            logging.warning(
                f"embedding lookup failed ({type(exc).__name__}: {exc}), "
                "falling back to string match"
            )
            use_embedding = False
            hit_idx = None

    if not use_embedding or hit_idx is None:
        # String match fallback (preserves Phase 10 behavior)
        for i, e in enumerate(lexicon.entries):
            if e.canonical_mech == canonical_mech:
                hit_idx = i
                break

    if hit_idx is not None:
        entry = lexicon.entries[hit_idx]
        new_evidence = [eid for eid in evidence_ids if eid not in entry.evidence_ids]
        entry.evidence_ids.extend(new_evidence)
        entry.reuse_count += 1
        # Audit log (only when embedding-based merge)
        if sim_value is not None and log_dir is not None:
            from explain_engine.engines.lexicon_merge import write_merge_audit
            write_merge_audit(
                log_dir=log_dir,
                merged_into=entry.entry_id,
                merged_from=canonical_mech[:80],  # truncate
                sim=sim_value,
                evidence_ids=new_evidence,
            )
    else:
        # New entry
        new_entry = LexiconEntry(
            entry_id=f"lex_{len(lexicon.entries) + 1:03d}",
            canonical_mech=canonical_mech,
            evidence_ids=evidence_ids,
            reuse_count=1,
            embedding=new_emb.tolist() if use_embedding else None,
        )
        lexicon.entries.append(new_entry)
        # Update matrix + idx mapping
        if use_embedding:
            lexicon.embeddings_matrix = (
                np.vstack([lexicon.embeddings_matrix, new_emb[None, :]])
                if lexicon.embeddings_matrix.shape[0] > 0
                else new_emb[None, :].astype(np.float32)
            )
            lexicon.entry_id_to_matrix_idx[new_entry.entry_id] = (
                lexicon.embeddings_matrix.shape[0] - 1
            )
```

**Step 5: Run tests → PASS**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_lexicon.py::TestFlushToLexiconDedup -v -m "embedding or not embedding"
```

Expected: 3 PASS.

**Step 6: Verify no regression + lint + commit**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest --tb=line 2>&1 | tail -3
/Users/jinziguan/Desktop/explain_everything/.venv/bin/ruff check src/ tests/

git -C /Users/jinziguan/Desktop/explain_everything add \
  src/explain_engine/engines/lexicon.py \
  tests/test_lexicon.py
git -C /Users/jinziguan/Desktop/explain_everything commit -m "$(cat <<'EOF'
engines/lexicon · flush_to_lexicon 集成 embedding merge (Phase 13 Wave 2)

Wave 2 Task 3: flush_to_lexicon 用 BGE-M3 cosine 找现有 entry,
> 0.85 命中 → append evidence_ids + reuse_count++ + audit log.
EXPLAIN_EMBEDDING_DISABLED=1 / embedder 失败 → fallback string-match.
新 entry 含 embedding + 同步 embeddings_matrix + idx mapping. 3 test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: 887 PASS (+1 disabled-env not-marker test, 2 embedding-marker tests skip by default), ruff 0.

---

### Task W2.4: Wave 2 verify

**Step 1: Full test + lint**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest --tb=short 2>&1 | tail -5
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest -m embedding -v 2>&1 | tail -15
/Users/jinziguan/Desktop/explain_everything/.venv/bin/ruff check src/ tests/
```

Expected: full 887 PASS (default), embedding marker ~15-20 PASS, ruff 0.

**Step 2: Manual smoke (optional)**

```bash
cd /Users/jinziguan/Desktop/explain_everything
.venv/bin/python -m explain_engine.cli
> /new        # 跑 1 个 session, flush 出几个 lex entry
> /lexicon    # 验 entry 都有 embedding 字段 (查 ~/.explain/.../knowledge/variables.json)
> /quit
```

第 2 次跑同主题:
```bash
.venv/bin/python -m explain_engine.cli
> /new        # 同主题 question, 期待 lexicon merge 而非新建 entry
> /lexicon    # 验 reuse_count 累加 (而非新建)
> /quit
```

Optional smoke, 跳过亦可.

---

## Wave 3: /compress Pre + Post Filter (3-4 天)

### Task W3.1: Compression prompt 加 Top-K pre-filter section

**Files:**
- Modify: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/llm/prompts/compression.yaml` (确认路径; 可能是 `propose_candidates.yaml` 或类似)
- Modify: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/compression.py` (prompt fill 时传 Top-K)

**Step 1: Identify prompt + compression entry points**

```bash
find /Users/jinziguan/Desktop/explain_everything/src/explain_engine/llm/prompts -name "*.yaml" | head -10
grep -n "propose_candidates\|compression\|render_prompt" /Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/compression.py | head -10
```

找到当前 propose_candidates 的 prompt yaml 文件 + Python fill 代码.

**Step 2: Write failing test** — 新 `tests/test_compress_dedup.py`:

```python
"""Phase 13 Wave 3: /compress pre+post filter with embedding."""

import pytest


class TestCompressPreFilter:
    """Pre-filter: prompt 含 Top-K lexicon canonical_mech."""

    def test_prompt_includes_top_k_canonical(self, tmp_path):
        """propose_candidates_prompt 应含 'Top-K' section + canonical_mech 文本."""
        from explain_engine.engines.compression import build_propose_prompt

        existing_lexicon_canonicals = [
            ("lex_001", "经济不安全感"),
            ("lex_002", "保守消费倾向"),
            ("lex_003", "代际财富分化"),
        ]
        prompt = build_propose_prompt(
            phenomena=["p_001: 房价上涨"],
            existing_lexicon=existing_lexicon_canonicals,
        )
        assert "Top-K" in prompt or "已有概念" in prompt
        for lex_id, canonical in existing_lexicon_canonicals:
            assert canonical in prompt

    def test_empty_lexicon_omits_section(self):
        """Top-K 空 → prompt 不出现 '已有概念' section."""
        from explain_engine.engines.compression import build_propose_prompt
        prompt = build_propose_prompt(
            phenomena=["p_001: 房价上涨"],
            existing_lexicon=[],
        )
        assert "已有概念" not in prompt
        assert "Top-K" not in prompt
```

**Step 3: Run failing → FAIL**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_compress_dedup.py::TestCompressPreFilter -v
```

Expected: 2 ERROR `cannot import build_propose_prompt` 或 signature mismatch.

**Step 4: Modify compression.py**

加 `build_propose_prompt` (或 modify 现有 prompt render) 收 `existing_lexicon: list[tuple[str, str]]` param. Edit yaml template 加 conditional section:

```yaml
# In compression.yaml propose_candidates template
{% if existing_lexicon %}
## 已有概念 (Top-K, 按 fitness 排序)
若新候选语义重复其中之一, 复用 ID 而非新生:

{% for lex_id, canonical in existing_lexicon %}
- {{ lex_id }}: {{ canonical }}
{% endfor %}

{% endif %}
```

Python side:

```python
def build_propose_prompt(
    phenomena: list[str],
    existing_lexicon: list[tuple[str, str]] | None = None,
) -> str:
    """Render propose_candidates prompt with optional Top-K pre-filter.

    Phase 13 Wave 3: existing_lexicon Top-K (e.g. K=20) shown to LLM
    so it can avoid re-generating concepts that are semantically dup of
    existing lexicon entries. K=0 / empty list → section omitted.
    """
    from jinja2 import Template
    # ... existing template render
    rendered = template.render(
        phenomena=phenomena,
        existing_lexicon=existing_lexicon or [],
    )
    return rendered
```

**Step 5: Run tests PASS + verify + commit**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_compress_dedup.py::TestCompressPreFilter -v
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest --tb=line 2>&1 | tail -3
/Users/jinziguan/Desktop/explain_everything/.venv/bin/ruff check src/ tests/

git -C /Users/jinziguan/Desktop/explain_everything add \
  src/explain_engine/engines/compression.py \
  src/explain_engine/llm/prompts/compression.yaml \
  tests/test_compress_dedup.py
git -C /Users/jinziguan/Desktop/explain_everything commit -m "$(cat <<'EOF'
engines/compression · propose_candidates prompt 加 Top-K pre-filter (Phase 13 Wave 3)

Wave 3 Task 1: build_propose_prompt 收 existing_lexicon param 渲
'已有概念 (Top-K)' section, 让 LLM 看现有 lexicon canonical 避免
重生重复. 空 Top-K → section omitted. 2 unit test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: 889 PASS, ruff 0.

---

### Task W3.2: 在 compress engine 入口 wire Top-K lexicon

**Files:**
- Modify: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/compression.py` (propose_candidates 入口)
- Modify: `/Users/jinziguan/Desktop/explain_everything/tests/test_compress_dedup.py`

**Step 1: Locate propose_candidates entry**

```bash
grep -n "def propose_candidates\|def compress\|lexicon.entries" /Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/compression.py | head -10
```

**Step 2: Write failing test**

```python
class TestCompressTopKWiring:
    """propose_candidates 应自动从 lexicon 拿 Top-K 传 prompt."""

    @pytest.mark.embedding
    def test_top_k_passed_to_prompt(self, tmp_path, monkeypatch):
        """Verify lexicon Top-K (K=20 default) is wired into prompt."""
        # Build fake lexicon with 30 entries
        from explain_engine.engines.lexicon import Lexicon, LexiconEntry
        entries = [
            LexiconEntry(
                entry_id=f"lex_{i:03d}",
                canonical_mech=f"机制{i}",
                evidence_ids=[],
                reuse_count=10 - (i % 10),  # vary fitness
                embedding=None,  # skip embedding for this test
            )
            for i in range(30)
        ]
        lex = Lexicon(entries=entries)

        # Mock LLM client to capture prompt
        captured_prompt = []

        class FakeLLM:
            async def complete(self, prompt: str, **kw):
                captured_prompt.append(prompt)
                return '{"candidates": []}'  # empty

        # Call propose_candidates
        from explain_engine.engines.compression import propose_candidates_async
        await propose_candidates_async(
            llm=FakeLLM(),
            phenomena=["p_001: test"],
            lexicon=lex,
            top_k=20,
        )

        assert len(captured_prompt) == 1
        # Top-K=20: prompt should mention lex_000..lex_019 (by fitness order)
        assert "机制0" in captured_prompt[0] or "lex_00" in captured_prompt[0]
        # Lower fitness entries (lex_020..lex_029) should NOT appear
        assert "机制29" not in captured_prompt[0]
```

**Step 3: Run failing → FAIL**

**Step 4: Modify `propose_candidates_async`** — fetch Top-K from lexicon:

```python
async def propose_candidates_async(
    llm,
    phenomena: list[str],
    lexicon: Lexicon | None = None,
    top_k: int = 20,
) -> list[CandidateConcept]:
    # Build existing_lexicon Top-K by fitness
    existing_lexicon: list[tuple[str, str]] = []
    if lexicon is not None and lexicon.entries:
        sorted_entries = sorted(
            lexicon.entries,
            key=lambda e: e.reuse_count,
            reverse=True,
        )[:top_k]
        existing_lexicon = [
            (e.entry_id, e.canonical_mech) for e in sorted_entries
        ]

    prompt = build_propose_prompt(
        phenomena=phenomena,
        existing_lexicon=existing_lexicon,
    )
    response = await llm.complete(prompt)
    # ... parse JSON + return candidates ...
```

**Step 5: Run tests + verify + commit**

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest tests/test_compress_dedup.py -v
/Users/jinziguan/Desktop/explain_everything/.venv/bin/ruff check src/ tests/

git -C /Users/jinziguan/Desktop/explain_everything add \
  src/explain_engine/engines/compression.py \
  tests/test_compress_dedup.py
git -C /Users/jinziguan/Desktop/explain_everything commit -m "$(cat <<'EOF'
engines/compression · propose_candidates wire Top-K from lexicon (Phase 13 Wave 3)

Wave 3 Task 2: propose_candidates_async 收 lexicon param, 按
reuse_count 排序取 Top-K=20 (default) 传给 build_propose_prompt
做 pre-filter. K=0 / None lexicon → 不塞. 1 test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task W3.3: Post-filter — LLM 候选 → embed → cosine dedup

**Files:**
- Modify: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/engines/compression.py`
- Modify: `/Users/jinziguan/Desktop/explain_everything/tests/test_compress_dedup.py`

**Step 1: Write failing test**

```python
class TestCompressPostFilter:
    """LLM 生 candidate → embed → cosine vs lexicon → 命中改 reuse."""

    @pytest.mark.embedding
    def test_dup_candidate_silently_reused(self, tmp_path):
        from explain_engine.engines.lexicon import Lexicon, LexiconEntry, flush_to_lexicon
        from explain_engine.engines.compression import postfilter_candidates

        # Lexicon 含 1 entry "经济不安全感导致防御性储蓄"
        entry = LexiconEntry(
            entry_id="lex_001",
            canonical_mech="经济不安全感导致防御性储蓄",
            evidence_ids=["e_001"],
            reuse_count=1,
            embedding=None,
        )
        lex = Lexicon(entries=[entry])
        # Trigger migration so lex_001 gets embedding
        # ... (use load(path) instead of direct construction in real test)

        # Fake LLM candidates (2): 1 dup, 1 new
        candidates = [
            {"canonical_mech": "对未来收入预期下降引发储蓄倾向", "evidence_ids": ["e_002"]},  # dup
            {"canonical_mech": "老龄化人口结构", "evidence_ids": ["e_003"]},                # new
        ]

        result = postfilter_candidates(candidates, lex)
        assert result["reused"] == 1
        assert result["new"] == 1
        # Dup 的 evidence_ids 进 lex_001
        assert "e_002" in lex.entries[0].evidence_ids
        # New 的 evidence_ids 进 new entry
        assert any(e.canonical_mech == "老龄化人口结构" for e in lex.entries)

    def test_no_lexicon_all_new(self):
        """Lexicon 空 → 所有 candidate 当 new."""
        from explain_engine.engines.lexicon import Lexicon
        from explain_engine.engines.compression import postfilter_candidates

        lex = Lexicon(entries=[])
        candidates = [
            {"canonical_mech": "x", "evidence_ids": []},
            {"canonical_mech": "y", "evidence_ids": []},
        ]
        result = postfilter_candidates(candidates, lex)
        assert result["reused"] == 0
        assert result["new"] == 2

    def test_disabled_env_string_match_fallback(self, tmp_path, monkeypatch):
        """EXPLAIN_EMBEDDING_DISABLED=1 → string match path."""
        monkeypatch.setenv("EXPLAIN_EMBEDDING_DISABLED", "1")
        # ... similar but exact string match
```

**Step 2: Run failing + impl + verify + commit**

Impl outline (in `compression.py`):

```python
def postfilter_candidates(
    candidates: list[dict],
    lexicon: Lexicon,
) -> dict[str, int]:
    """For each LLM candidate, dedup via embedding cosine vs lexicon.

    Returns:
        {"reused": int, "new": int} count summary.

    Side effects:
        - dup: flush_to_lexicon merges evidence_ids
        - new: flush_to_lexicon creates new entry
    """
    reused = 0
    new_count = 0
    for cand in candidates:
        len_before = len(lexicon.entries)
        flush_to_lexicon(
            lexicon=lexicon,
            canonical_mech=cand["canonical_mech"],
            evidence_ids=cand["evidence_ids"],
            log_dir=...,
        )
        if len(lexicon.entries) == len_before:
            reused += 1
        else:
            new_count += 1
    return {"reused": reused, "new": new_count}
```

Commit message:
```
engines/compression · post-filter LLM candidates via embedding cosine (Phase 13 Wave 3)

Wave 3 Task 3: postfilter_candidates 逐 candidate flush_to_lexicon
(已含 embedding merge), 数 reuse / new 比例返 dict 给 UI 报告.
Lexicon 空 / disabled env 走 fallback. 3 test (mixed reuse / empty
lex / disabled env).
```

---

### Task W3.4: UI 显式 "X candidates → Y reused / Z new"

**Files:**
- Modify: `/Users/jinziguan/Desktop/explain_everything/src/explain_engine/chat/slash_commands.py` (`_handle_compress` 输出末尾加 stat 行)
- Modify: `/Users/jinziguan/Desktop/explain_everything/tests/test_chat_slash_commands.py` (`TestSlashCompress`)

**Step 1: Write failing test**

```python
@pytest.mark.embedding
async def test_compress_output_shows_reuse_stats(self):
    """slash_compress event content 含 'reused' / 'new' 统计."""
    # ... build session with non-empty lexicon ...
    events = await dispatch_slash(chat, "/compress")
    content = events[0].content
    assert "reused" in content.lower() or "复用" in content
    assert "new" in content.lower() or "新建" in content
```

**Step 2: Modify `_handle_compress`**

In `slash_commands.py::_handle_compress`, after `propose_candidates_async` + `postfilter_candidates`, append a line to the ChatEvent content:

```python
stats = postfilter_candidates(candidates, lexicon)
output_lines.append(
    f"\nCompress: {len(candidates)} candidates → "
    f"{stats['reused']} reused (embedding) / {stats['new']} new"
)
```

**Step 3: Run tests + verify + commit**

```
chat/slash · /compress UI 显 reuse/new 统计 (Phase 13 Wave 3)

Wave 3 Task 4: _handle_compress 输出末尾加 'Compress: X candidates
→ Y reused (embedding) / Z new' 让用户看 post-filter dedup 效果. 1
e2e test.
```

---

### Task W3.5: Wave 3 verify

```bash
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest --tb=short 2>&1 | tail -5
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest -m embedding -v 2>&1 | tail -20
/Users/jinziguan/Desktop/explain_everything/.venv/bin/ruff check src/ tests/
```

Expected: ~893+ PASS (default), ~25-30 embedding PASS, ruff 0.

---

## Wave 4: Docs + Acceptance (1 天)

### Task W4.1: Update README + acceptance

**Files:**
- Modify: `/Users/jinziguan/Desktop/explain_everything/README.md` (Status block + new Phase 13 section)
- Create: `/Users/jinziguan/Desktop/explain_everything/docs/plans/2026-05-20-variable-embedding-acceptance.md`

**Step 1: README Status block 升级**

加 `**Phase 13 milestone (2026-05-XX)**` 在 Phase 12 milestone 上方 (类似 Phase 12 加上次).

**Step 2: 新 Phase 13 section** 加在 `## Phase 12` 前:

```markdown
## Phase 13 (2026-05-XX) — Variable Embedding (Candidate E)

lexicon `canonical_mechanism` 从 string match 升级为 **BGE-M3 dense embedding** + cosine threshold 0.85 merge, 解 LLM 措辞漂移导致的同概念 split. `/compress` 加 pre-filter (prompt 塞 Top-K=20 lexicon) + post-filter (LLM 候选 → embed → silent reuse).

**核心 (brainstorming Q&A 锁)**:
- Scope: Medium — lexicon dedup + /compress pre+post filter
- Model: BGE-M3 via FlagEmbedding, fp16 + Apple Silicon MPS backend
- Storage: 持久到 lexicon JSON (embedding field per entry, 4 KB/entry)
- Threshold: 0.85 hard-coded MVP + audit log
- Migration: lazy on startup, batch embed 缺字段 entry

**新依赖**: FlagEmbedding>=1.3 + torch>=2.1 + BGE-M3 (4.3 GB local cache)

**新机制**:
- Audit log: `logs/lexicon_merge_<date>.jsonl` 每次 merge 1 行 (entry_id, sim, evidence_ids)
- Fallback: `EXPLAIN_EMBEDDING_DISABLED=1` → string-match path (CI / no-GPU)

**文档**:
- design: [docs/plans/2026-05-20-variable-embedding-design.md](docs/plans/2026-05-20-variable-embedding-design.md)
- plan: [docs/plans/2026-05-20-variable-embedding-plan.md](docs/plans/2026-05-20-variable-embedding-plan.md)
- acceptance: [docs/plans/2026-05-20-variable-embedding-acceptance.md](docs/plans/2026-05-20-variable-embedding-acceptance.md)
```

**Step 3: 新 acceptance doc** — Create `2026-05-20-variable-embedding-acceptance.md`:

```markdown
# Candidate E Variable Embedding Acceptance Smoke

**Date**: 2026-05-XX (Phase 13 完成日)
**HEAD**: <commit_sha_after_W4.1>

## 前置

- macOS Apple Silicon + Full Disk Access permission
- BGE-M3 model cache present (`~/.cache/huggingface/hub/models--BAAI--bge-m3/`)
- `which dot` 仍正常 (Phase 12 /graph 依赖)

## Step 1: 装新 deps

- `cd /Users/jinziguan/Desktop/explain_everything && uv sync`
- 验 `.venv/bin/python -c "from FlagEmbedding import BGEM3FlagModel; print('OK')"`

## Step 2: Migration

- 第一次跑 lexicon-touching 命令 (e.g. `explain` REPL → `/lexicon`)
- 验 Rich console 显 "首次升级 lexicon embedding: N entries"
- 跑完 `cat ~/.explain/projects/<proj>/knowledge/variables.json | jq '.entries[0].embedding | length'` 应 1024

## Step 3: 同概念 merge 验证

- `/new` 起 session 1 主题 "为什么年轻人不消费"
- 跑 reasoning → flush 出几个 lex entry (含 "经济不安全感" 类概念)
- `/quit`
- `/new` 起 session 2 类似主题 "Gen Z 消费保守的原因"
- 跑同步 → flush 时验:
  - `/lexicon` 列表中, 同义概念 reuse_count 累加而非新建
  - `cat logs/lexicon_merge_*.jsonl | jq` 验 merge 记录

## Step 4: /compress dedup 验

- 跑 `/compress` 在含 lexicon prior 的 session
- 输出末尾应有 `Compress: X candidates → Y reused (embedding) / Z new`
- 验 Y > 0 (LLM 真生重复, embedding 兜底)

## Step 5: Fallback

- `EXPLAIN_EMBEDDING_DISABLED=1 explain` → 不 load model, 走 string-match
- 验启动快 + lexicon flush 不 crash

## Pass 标准

- ✅ 870+ tests pass (Phase 12: 867 + 25-30 new)
- ✅ embedding marker tests local 全 pass (`-m embedding`)
- ✅ Acceptance step 1-5 全过
- ✅ ruff 0

## 已知 fail-safe

- BGE-M3 load 失败 → fallback string-match, warning
- /compress prompt token 涨 ~2k (Top-K=20) → deepseek-v4-pro 128k 还远
```

**Step 4: Commit**

```bash
git -C /Users/jinziguan/Desktop/explain_everything add \
  README.md \
  docs/plans/2026-05-20-variable-embedding-acceptance.md
git -C /Users/jinziguan/Desktop/explain_everything commit -m "$(cat <<'EOF'
docs · README + acceptance 加 Phase 13 Variable Embedding (Wave 4)

Wave 4 Task 1: README Status block 升级到 Phase 13 milestone +
新 Phase 13 section (motivation + 4 核心决策 + 新 dep + 新机制 +
文档 link). 新建 acceptance doc 含 5 step manual smoke (migration /
merge / compress dedup / fallback / pass criteria).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final Verification

```bash
# Full suite
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest --tb=short 2>&1 | tail -5

# Embedding tests explicit
/Users/jinziguan/Desktop/explain_everything/.venv/bin/python -m pytest -m embedding --tb=short 2>&1 | tail -10

# Lint
/Users/jinziguan/Desktop/explain_everything/.venv/bin/ruff check src/ tests/

# Git log review
git -C /Users/jinziguan/Desktop/explain_everything log --oneline ab7f430..HEAD

# Status clean
git -C /Users/jinziguan/Desktop/explain_everything status
```

Expected:
- 893+ tests pass (default, embedding skipped)
- ~25-30 embedding tests pass explicit
- Ruff 0
- ~12-14 commits Wave 1-4 + 1 docs

---

## Risk + Rollback

**Risk 1: torch + FlagEmbedding install timeout / lockfile conflict**
- Mitigation: `uv sync --timeout 600` 或分 step (装 torch 单独, 再装 FlagEmbedding)
- Rollback: `git checkout pyproject.toml uv.lock`

**Risk 2: BGE-M3 model cache 损坏**
- Mitigation: `rm -rf ~/.cache/huggingface/hub/models--BAAI--bge-m3` + re-download
- Affected: 一次性 ~4.3 GB download

**Risk 3: threshold 0.85 false merge / split 在真 session**
- Mitigation: audit log 记录每次 merge → 事后审计
- Rollback: 单 entry split 暂手动改 `~/.explain/projects/<proj>/knowledge/variables.json` 拆分 evidence_ids
- Future: Phase 14 加 `/lexicon revert <merge_id>` 自动 undo

**Risk 4: /compress prompt 过长 LLM 拒绝**
- Mitigation: Top-K default 20, deepseek 128k context 远够; 监控 token cost
- Rollback: Top-K → 10 或 0 (关 pre-filter)

**Risk 5: macOS sandbox permission denied (SessionStart 后)**
- Mitigation: System Settings → Privacy → Full Disk Access 加 Claude Code / Terminal
- 不影响 design, 但实现卡死

**Rollback**: 每 task 独立 commit. 完全回滚: `git reset --hard ab7f430` (Phase 12 末尾设计 commit 前一位)
