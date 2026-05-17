# Phase 9 — Conversational Cognitive Engine Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把 cognitive engine 从"CLI 命令式"升级为"Claude Code 风格的对话式 agent". 用户 `explain chat <sid>` 进入持续对话 loop, LLM 自主调度 7 tool, 配套 3-tier compaction + dual budget + smart HITL + project-based 持久化 (~/.explain/projects/<proj>/sessions/<sid>/).

**Architecture:** 7-layer 架构 (Entry → ChatSession → query_loop → 7 Tools → Post-turn Hooks → 3-tier Compaction → storage_v2 persistence). 100% 复用 Phase 0-8 existing engines (作为 tool 调用), 不动 runtime/scheduler/stop. Wave A 一次性 migrate sessions/ → ~/.explain/projects/<proj>/sessions/<sid>/, 老 SessionStore 接口兼容 delegation 到 storage_v2. Wave B-F 新建 chat/ 目录 9 文件. Wave G acceptance + 文档.

**Tech Stack:** Python 3.11+ / Pydantic / Typer / Rich (Live render) / asyncio (background hooks) / pytest / pytest-mock / pytest-asyncio. Phase 0-8 完全复用 (engines/_propagation, expansion, compression, simulation, reflection, lifecycle, prediction, counterfactual, input_validation, intervention_parser, _llm_retry, evaluation, bootstrap, rescore), 无新增 runtime dependency.

**Branch:** `dev` (latest: `913538c` Phase 9 design)

**Design Doc:** [2026-05-17-conversational-cognitive-engine-design.md](2026-05-17-conversational-cognitive-engine-design.md)

**Phase 0-8 现状:** 482 PASS, ruff 0. Phase 7-8 acceptance baseline 3 sessions (s_f3beb777 / s_705f0435 / s_7d491774) 在 `sessions/` 平铺存储, 需 Wave A 自动迁移.

---

## 与 Design Doc 的偏差说明

实施前调研无 reconcile gap. 明确的实现约定:

1. **测试用 `.venv/bin/python -m pytest`** (项目用 uv-managed venv, NOT bare `python`)
2. **commit message 中文 + `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer with `(1M context)` suffix**
3. **每 Wave 完成后 stop checkpoint**, 等用户审通过再进下一 Wave
4. **测试 fixture 用 `EXPLAIN_HOME=tmp_path/.explain` env var + monkeypatch** (替代 Phase 0-8 的 `SESSIONS_DIR=tmp_path`)
5. **LLM mock 用 `pytest-mock`** (跟 Phase 0-8 一致), 不用 `unittest.mock`
6. **新 dataclass 用 `@dataclass(frozen=True)`** 当 immutable 语义合适; otherwise plain dataclass
7. **新 Pydantic model 用 v2 风格** (`Field(default=..., ge=..., le=...)`)
8. **ruff check 全程 0 errors**
9. **Chat 内 file write 用 atomic rename** (写 `path.tmp` 然后 `os.replace(path.tmp, path)`) 防 race
10. **Anthropic SDK 用 tool_use API** (structured function-calling), 不是 raw JSON parsing
11. **transcript.jsonl 用 JSONL 格式** (一行一 JSON message), append-only
12. **背景 hook 用 `asyncio.create_task` fire-and-forget**, errors 不阻塞主流程 (log + 静默)

---

## 任务索引

**Wave A — Persistence Migration (1 task, +15 tests)**
- Task A.1: `storage_v2` + `migration` + 老 482 测试 fixture 迁 (~10 step)

**Wave B — Tool Layer (2 tasks, +20 tests)**
- Task B.1: `chat/tools.py` Tool dataclass + 5 engine tool wrappers (~8 step, +12 tests)
- Task B.2: `add_observation` + `read_node` tools (~6 step, +8 tests)

**Wave C — Chat Loop (2 tasks, +15 tests)**
- Task C.1: `chat/session.py` ChatSession outer + persistence integration (~7 step, +8 tests)
- Task C.2: `chat/loop.py` query_loop async generator + `system_prompt.py` 动态拼装 (~8 step, +7 tests)

**Wave D — Hooks + Budget + HITL (2 tasks, +13 tests)**
- Task D.1: `chat/budget.py` BudgetCounter + `chat/hitl.py` smart gate (~6 step, +7 tests)
- Task D.2: `chat/hooks.py` reflect/lifecycle/session_memory_writer (~7 step, +6 tests)

**Wave E — Compaction + A+B fix (2 tasks, +12 tests)**
- Task E.1: `chat/compaction.py` 3-tier (microCompact + sessionMemory + emergency) (~7 step, +9 tests)
- Task E.2: A+B 折叠 — rollout_from_roots edge case fix + alignment 持久化通过 chat_state (~5 step, +3 tests)

**Wave F — Slash + CLI (2 tasks, +10 tests)**
- Task F.1: `chat/slash_commands.py` 6 个默认 slash (~5 step, +6 tests)
- Task F.2: CLI `explain chat` + `explain migrate` command + Rich Live streaming (~6 step, +4 tests)

**Wave G — Acceptance + 文档 (2 tasks, +5 tests)**
- Task G.1: Real chat run on fresh session + 老 482 测试全 PASS verify (~5 step, +5 tests)
- Task G.2: acceptance doc + README + Phase 10 motivations (~3 step, 0 tests)

**总: 13 task / ~85 step / +90 tests (482 → 572 final).**

---

# Wave A — Persistence Migration

## Task A.1: `storage_v2` + `migration` + 老 482 测试 fixture 迁

**目的**: Phase 0-8 `SessionStore` 用 `sessions/s_xxx.json` 平铺存储. Phase 9 改为 project-based 目录结构 `~/.explain/projects/<project_id>/sessions/<sid>/{metadata.json, graph.json}`. 老 SessionStore 接口完全保留 (delegation), 482 老 test 通过 `EXPLAIN_HOME` env var 自动 isolate 到 tmp_path. 新增 `explain migrate` 一次性命令.

**Files:**
- Create: `src/explain_engine/persistence/storage_v2.py`
- Create: `src/explain_engine/persistence/migration.py`
- Modify: `src/explain_engine/persistence/session.py` (delegate to storage_v2)
- Create: `tests/test_persistence_storage_v2.py`
- Create: `tests/test_persistence_migration.py`
- Modify: `tests/conftest.py` (add EXPLAIN_HOME fixture)

---

### Step 1: 写 storage_v2 失败测试

Create `tests/test_persistence_storage_v2.py`:

```python
"""Wave A.1: storage_v2 project-based persistence tests."""

import os
import json
from pathlib import Path

import pytest

from explain_engine.persistence.storage_v2 import (
    StorageV2,
    compute_project_id,
)


class TestComputeProjectId:
    def test_cwd_hash_8_hex(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        pid = compute_project_id()
        assert len(pid) == 8
        assert all(c in "0123456789abcdef" for c in pid)

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "myproj")
        assert compute_project_id() == "myproj"

    def test_stable_for_same_cwd(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert compute_project_id() == compute_project_id()


class TestStorageV2:
    def test_session_dir_path(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        path = s.session_dir("s_001abc")
        assert path.parent.parent.parent == tmp_path / "projects"
        assert path.name == "s_001abc"

    def test_save_metadata_creates_dir(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_metadata("s_001", {"question": "why X", "stage": "done"})
        path = s.session_dir("s_001") / "metadata.json"
        assert path.exists()
        assert json.loads(path.read_text())["question"] == "why X"

    def test_load_metadata_round_trip(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_metadata("s_001", {"k": "v"})
        assert s.load_metadata("s_001") == {"k": "v"}

    def test_load_missing_raises(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        with pytest.raises(FileNotFoundError):
            s.load_metadata("s_999")

    def test_save_graph_round_trip(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_graph("s_001", {"nodes": [], "edges": []})
        assert s.load_graph("s_001") == {"nodes": [], "edges": []}

    def test_list_sessions(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_metadata("s_001", {})
        s.save_metadata("s_002", {})
        assert sorted(s.list_sessions()) == ["s_001", "s_002"]

    def test_atomic_write(self, tmp_path, monkeypatch) -> None:
        """write_atomic uses tmp + rename, no partial file on crash."""
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_metadata("s_001", {"key": "value"})
        path = s.session_dir("s_001") / "metadata.json"
        # No .tmp file lingering
        tmp_files = list(path.parent.glob("*.tmp"))
        assert tmp_files == []

    def test_append_transcript_jsonl(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.append_transcript("s_001", {"role": "user", "content": "hi"})
        s.append_transcript("s_001", {"role": "assistant", "content": "hello"})
        lines = s.load_transcript("s_001")
        assert len(lines) == 2
        assert lines[0]["role"] == "user"
        assert lines[1]["content"] == "hello"

    def test_save_load_chat_state(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_chat_state("s_001", {"budget_remaining": 42})
        assert s.load_chat_state("s_001") == {"budget_remaining": 42}

    def test_save_load_memory_md(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        s.save_memory("s_001", "# Session memory\n\nsummary here")
        assert s.load_memory("s_001") == "# Session memory\n\nsummary here"

    def test_load_chat_state_missing_returns_none(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        s = StorageV2()
        assert s.load_chat_state("s_001") is None
```

### Step 2: 写 migration 失败测试

Create `tests/test_persistence_migration.py`:

```python
"""Wave A.1: migration tests — sessions/*.json → ~/.explain/projects/<proj>/sessions/<sid>/"""

import json
from pathlib import Path

from explain_engine.persistence.migration import (
    detect_legacy_sessions,
    migrate_session,
    migrate_all,
)


class TestDetectLegacy:
    def test_finds_legacy_files(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "sessions").mkdir()
        (tmp_path / "sessions" / "s_001.json").write_text('{"meta":{"session_id":"s_001"}}')
        (tmp_path / "sessions" / "s_002.json").write_text('{"meta":{"session_id":"s_002"}}')
        # backup files should NOT be picked up
        (tmp_path / "sessions" / "s_001.before-rescore.json").write_text("{}")
        found = detect_legacy_sessions()
        assert sorted(found) == ["s_001", "s_002"]

    def test_no_sessions_dir_returns_empty(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert detect_legacy_sessions() == []


class TestMigrateSession:
    def test_migrates_single(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
        # Setup legacy file
        (tmp_path / "sessions").mkdir()
        legacy = {
            "meta": {
                "session_id": "s_001",
                "question": "why?",
                "stage": "done",
                "budget": 10,
                "created_at": "2026-05-15T10:00:00+00:00",
                "updated_at": "2026-05-15T10:00:00+00:00",
            },
            "state": {
                "graph": {"nodes": [], "edges": [], "root_question": "why?"},
                "tick": 0,
                "budget_remaining": 10,
                "root_question": "why?",
                "insight_candidates": [],
                "reasoning_trace": [],
                "last_gains": {},
                "last_gain_tick": 0,
                "last_reflection_change_tick": 0,
            },
        }
        (tmp_path / "sessions" / "s_001.json").write_text(json.dumps(legacy))

        migrate_session("s_001")

        # New layout exists
        from explain_engine.persistence.storage_v2 import StorageV2
        s = StorageV2()
        meta = s.load_metadata("s_001")
        assert meta["question"] == "why?"
        graph_data = s.load_graph("s_001")
        assert graph_data["root_question"] == "why?"
        # Legacy file moved to .legacy/
        legacy_archive = tmp_path / "sessions" / ".legacy" / "s_001.json"
        assert legacy_archive.exists()
        # Original removed
        assert not (tmp_path / "sessions" / "s_001.json").exists()

    def test_dry_run_no_change(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
        (tmp_path / "sessions").mkdir()
        (tmp_path / "sessions" / "s_001.json").write_text(
            '{"meta":{"session_id":"s_001","question":"q","stage":"done","budget":1,'
            '"created_at":"2026-01-01T00:00:00+00:00","updated_at":"2026-01-01T00:00:00+00:00"},'
            '"state":{"graph":{"nodes":[],"edges":[],"root_question":"q"},'
            '"tick":0,"budget_remaining":1,"root_question":"q","insight_candidates":[],'
            '"reasoning_trace":[],"last_gains":{},"last_gain_tick":0,"last_reflection_change_tick":0}}'
        )
        migrate_session("s_001", dry_run=True)
        # No new directory created
        assert not (tmp_path / ".explain").exists()
        # Legacy untouched
        assert (tmp_path / "sessions" / "s_001.json").exists()


class TestMigrateAll:
    def test_migrates_multiple(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
        (tmp_path / "sessions").mkdir()
        for sid in ["s_001", "s_002", "s_003"]:
            data = {
                "meta": {
                    "session_id": sid, "question": "q", "stage": "done", "budget": 1,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                },
                "state": {
                    "graph": {"nodes": [], "edges": [], "root_question": "q"},
                    "tick": 0, "budget_remaining": 1, "root_question": "q",
                    "insight_candidates": [], "reasoning_trace": [],
                    "last_gains": {}, "last_gain_tick": 0,
                    "last_reflection_change_tick": 0,
                },
            }
            (tmp_path / "sessions" / f"{sid}.json").write_text(json.dumps(data))

        results = migrate_all()
        assert len(results) == 3
        from explain_engine.persistence.storage_v2 import StorageV2
        s = StorageV2()
        assert sorted(s.list_sessions()) == ["s_001", "s_002", "s_003"]
```

### Step 3: 跑测试 — 全 fail

Run: `.venv/bin/python -m pytest tests/test_persistence_storage_v2.py tests/test_persistence_migration.py -v`

Expected: ~15 FAIL (ImportError: storage_v2 / migration).

### Step 4: 实现 storage_v2.py

Create `src/explain_engine/persistence/storage_v2.py`:

```python
"""Phase 9 Wave A: Project-based filesystem storage.

Replaces flat sessions/*.json with:
  ~/.explain/projects/<project_id>/sessions/<sid>/{metadata.json, graph.json,
                                                    transcript.jsonl, memory.md,
                                                    chat_state.json}
  ~/.explain/projects/<project_id>/knowledge/ (Phase 10+ 占位)

project_id = sha256(cwd absolute path)[:8] OR EXPLAIN_PROJECT_ID env var.
EXPLAIN_HOME env var (default ~/.explain) for test isolation.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def compute_project_id() -> str:
    """Project ID = EXPLAIN_PROJECT_ID env var OR sha256(cwd)[:8]."""
    override = os.environ.get("EXPLAIN_PROJECT_ID")
    if override:
        return override
    cwd = os.path.abspath(os.getcwd())
    return hashlib.sha256(cwd.encode("utf-8")).hexdigest()[:8]


def explain_home() -> Path:
    """Base dir: EXPLAIN_HOME env var OR ~/.explain."""
    home = os.environ.get("EXPLAIN_HOME")
    if home:
        return Path(home)
    return Path.home() / ".explain"


def _write_atomic(path: Path, content: str) -> None:
    """Write to .tmp then rename for atomicity."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


class StorageV2:
    """Project-based session storage."""

    def __init__(self, project_id: str | None = None):
        self.project_id = project_id or compute_project_id()

    def project_dir(self) -> Path:
        return explain_home() / "projects" / self.project_id

    def session_dir(self, sid: str) -> Path:
        return self.project_dir() / "sessions" / sid

    def knowledge_dir(self) -> Path:
        return self.project_dir() / "knowledge"

    # ── metadata.json ──
    def save_metadata(self, sid: str, meta: dict[str, Any]) -> None:
        _write_atomic(self.session_dir(sid) / "metadata.json", json.dumps(meta, ensure_ascii=False, indent=2))

    def load_metadata(self, sid: str) -> dict[str, Any]:
        path = self.session_dir(sid) / "metadata.json"
        if not path.exists():
            raise FileNotFoundError(f"metadata not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    # ── graph.json ──
    def save_graph(self, sid: str, graph_data: dict[str, Any]) -> None:
        _write_atomic(self.session_dir(sid) / "graph.json", json.dumps(graph_data, ensure_ascii=False, indent=2))

    def load_graph(self, sid: str) -> dict[str, Any]:
        path = self.session_dir(sid) / "graph.json"
        if not path.exists():
            raise FileNotFoundError(f"graph not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    # ── transcript.jsonl (append-only) ──
    def append_transcript(self, sid: str, message: dict[str, Any]) -> None:
        path = self.session_dir(sid) / "transcript.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    def load_transcript(self, sid: str) -> list[dict[str, Any]]:
        path = self.session_dir(sid) / "transcript.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # ── memory.md ──
    def save_memory(self, sid: str, content: str) -> None:
        _write_atomic(self.session_dir(sid) / "memory.md", content)

    def load_memory(self, sid: str) -> str:
        path = self.session_dir(sid) / "memory.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    # ── chat_state.json ──
    def save_chat_state(self, sid: str, state: dict[str, Any]) -> None:
        _write_atomic(self.session_dir(sid) / "chat_state.json", json.dumps(state, ensure_ascii=False, indent=2))

    def load_chat_state(self, sid: str) -> dict[str, Any] | None:
        path = self.session_dir(sid) / "chat_state.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # ── list sessions ──
    def list_sessions(self) -> list[str]:
        sessions_root = self.project_dir() / "sessions"
        if not sessions_root.exists():
            return []
        return [d.name for d in sessions_root.iterdir() if d.is_dir() and not d.name.startswith(".")]
```

### Step 5: 实现 migration.py

Create `src/explain_engine/persistence/migration.py`:

```python
"""Phase 9 Wave A: One-time migration sessions/*.json → ~/.explain/projects/<proj>/sessions/<sid>/

Idempotent: detect existing migrated sessions and skip.
Legacy files moved to sessions/.legacy/ (not deleted, for safety).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from explain_engine.persistence.storage_v2 import StorageV2

_SESSION_RE = re.compile(r"^s_[0-9a-f]{8}\.json$")  # 防 backup .before-rescore.json 等


def detect_legacy_sessions() -> list[str]:
    """Find sessions/s_*.json (skip backup snapshots)."""
    legacy_dir = Path("sessions")
    if not legacy_dir.exists():
        return []
    sids: list[str] = []
    for f in legacy_dir.iterdir():
        if f.is_file() and _SESSION_RE.match(f.name):
            sids.append(f.stem)  # 去 .json
    return sorted(sids)


def migrate_session(sid: str, dry_run: bool = False) -> dict:
    """Migrate one legacy session/s_<sid>.json to storage_v2 layout.

    Returns: {sid, migrated: bool, reason?: str}
    """
    legacy_path = Path("sessions") / f"{sid}.json"
    if not legacy_path.exists():
        return {"sid": sid, "migrated": False, "reason": "legacy file not found"}

    storage = StorageV2()
    if storage.session_dir(sid).exists():
        return {"sid": sid, "migrated": False, "reason": "already migrated"}

    if dry_run:
        return {"sid": sid, "migrated": False, "reason": "dry-run"}

    # Load legacy {meta: {...}, state: {graph: {...}, ...}}
    legacy_data = json.loads(legacy_path.read_text(encoding="utf-8"))
    meta = legacy_data["meta"]
    state = legacy_data["state"]

    # Split into 2 new files
    storage.save_metadata(sid, meta)
    # graph.json contains full state (graph + tick + reasoning_trace + ...)
    storage.save_graph(sid, state)

    # Move legacy to .legacy/ (safety: never delete user data)
    legacy_archive_dir = Path("sessions") / ".legacy"
    legacy_archive_dir.mkdir(exist_ok=True)
    shutil.move(str(legacy_path), str(legacy_archive_dir / f"{sid}.json"))

    return {"sid": sid, "migrated": True}


def migrate_all(dry_run: bool = False) -> list[dict]:
    """Migrate all legacy sessions found. Returns per-session result."""
    sids = detect_legacy_sessions()
    return [migrate_session(sid, dry_run=dry_run) for sid in sids]
```

### Step 6: 改 session.py 兼容 (delegate to storage_v2)

Modify `src/explain_engine/persistence/session.py`. Keep `SessionStore` interface; internally delegate to storage_v2.

(Detailed Edit instructions: read current session.py, find `SessionStore` class methods (`load`, `save`, `list`), replace internal implementation to call `StorageV2`. Backward-compat: `SessionStore(directory)` ignores directory param (just delegate to StorageV2 which respects EXPLAIN_HOME).)

Key behavior:
- `SessionStore.load(sid)` → loads metadata + graph from storage_v2, reconstructs `Session` object
- `SessionStore.save(session)` → splits Session → metadata + graph, calls storage_v2.save_metadata + save_graph
- `SessionStore.list()` → storage_v2.list_sessions()

### Step 7: 改 conftest.py 加 EXPLAIN_HOME fixture

Modify `tests/conftest.py`:

```python
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_explain_home(tmp_path, monkeypatch):
    """Phase 9: All tests auto-isolate EXPLAIN_HOME to tmp_path/.explain.
    
    Replaces Phase 0-8 SESSIONS_DIR fixture (now obsolete).
    """
    monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path / ".explain"))
    monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_proj")
    yield
```

Verify: search `tests/` for `SESSIONS_DIR` fixture usage and confirm all tests now work via EXPLAIN_HOME.

### Step 8: 跑测试 — 验证 storage_v2 + migration tests PASS

Run: `.venv/bin/python -m pytest tests/test_persistence_storage_v2.py tests/test_persistence_migration.py -v`

Expected: ~15 PASS.

### Step 9: 跑全测 + ruff

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/ tests/
```

Expected: 497 PASS (482 + 15), ruff 0. ⚠️ 若老 test fail (因 SESSIONS_DIR 迁移), 同步更新.

### Step 10: Commit

```bash
git add src/explain_engine/persistence/storage_v2.py \
        src/explain_engine/persistence/migration.py \
        src/explain_engine/persistence/session.py \
        tests/test_persistence_storage_v2.py \
        tests/test_persistence_migration.py \
        tests/conftest.py
# Plus any old test fixture migrations
git commit -m "$(cat <<'EOF'
Wave A.1 · storage_v2 + migration + 482 test fixture 迁

新模块 persistence/storage_v2.py:
- StorageV2 类 + compute_project_id() + explain_home()
- session_dir = ~/.explain/projects/<proj>/sessions/<sid>/
- 5 文件 API: metadata.json / graph.json / transcript.jsonl (append-only) /
  memory.md / chat_state.json
- atomic write (tmp + rename) 防 race

新模块 persistence/migration.py:
- detect_legacy_sessions: 找 sessions/s_*.json (regex 防 backup)
- migrate_session(sid, dry_run): 拆 legacy → metadata + graph; move to .legacy/
- migrate_all: idempotent, 已迁的 skip

老 SessionStore 接口兼容: delegate to storage_v2 内部.

tests/conftest.py 加 isolated_explain_home autouse fixture (替老 SESSIONS_DIR
fixture pattern). 所有 482 老 test 透明 isolation 到 tmp_path/.explain.

哲学锚点 §5.3 Persistent World Model: knowledge/ 目录留位 Phase 10+.

+15 tests (storage_v2 12 + migration 3). 497 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave A Checkpoint

**完成度**: 1/1 task. +15 tests (482 → 497). Project-based persistence + 老测试 transparently migrated.

**验证**:
- storage_v2 12 tests PASS
- migration 3 tests PASS
- 482 老测试经 EXPLAIN_HOME fixture 全 PASS

**Stop**. 等用户审过, 进 Wave B.

---

# Wave B — Tool Layer

## Task B.1: `chat/tools.py` Tool dataclass + 5 engine tool wrappers

**目的**: 定义 Tool dataclass (per Claude Code Tool_architecture.md pattern), 5 engine tool 包 (`expand` / `compress` / `check` / `predict` / `counterfactual`). 每 tool 有 `name` / `input_schema` (Pydantic) / `description()` (dynamic, 含 graph hint) / `call()` (async wrapper 老 engine). 暂不集成 chat loop, 单纯 tool 定义 + 单元测试.

**Files:**
- Create: `src/explain_engine/chat/__init__.py`
- Create: `src/explain_engine/chat/tools.py`
- Create: `tests/test_chat_tools.py`

---

### Step 1: 写失败测试

Create `tests/test_chat_tools.py`:

```python
"""Wave B.1: chat/tools.py — Tool dataclass + 5 engine tool wrappers."""

import pytest

from explain_engine.chat.tools import (
    Tool,
    ToolContext,
    expand_tool,
    compress_tool,
    check_tool,
    predict_tool,
    counterfactual_tool,
    ALL_TOOLS,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state_with_l1():
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(
        id="c_001", name="abs", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="obs", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7, mechanism_description="m",
    ))
    return CognitiveState(graph=g, budget_remaining=10, root_question="why",
                          insight_candidates=["c_001"])


class TestToolDataclass:
    def test_tool_has_required_fields(self) -> None:
        from pydantic import BaseModel

        class _Input(BaseModel):
            x: int

        async def _fake_call(input, ctx):
            return f"got {input.x}"

        t = Tool(
            name="test",
            input_schema=_Input,
            description=lambda ctx: "test tool",
            call=_fake_call,
        )
        assert t.name == "test"
        assert t.is_readonly is False
        assert t.is_destructive is False
        assert t.requires_hitl is False


class TestALL_TOOLS:
    def test_contains_5_engine_tools(self) -> None:
        # B.1 only registers 5 engine tools; B.2 adds add_observation + read_node
        names = {t.name for t in ALL_TOOLS}
        assert names == {"expand", "compress", "check", "predict", "counterfactual"}


class TestExpandTool:
    def test_description_includes_graph_hint(self) -> None:
        state = _make_state_with_l1()
        ctx = ToolContext(state=state)
        desc = expand_tool.description(ctx)
        assert "expand" in desc.lower() or "扩" in desc
        # Should mention graph size
        assert "1" in desc or "L1" in desc

    def test_input_schema_validates_direction(self) -> None:
        # downward / upward / auto are valid
        valid_input = expand_tool.input_schema(l1_id="c_001", direction="downward")
        assert valid_input.direction == "downward"

    @pytest.mark.asyncio
    async def test_call_dispatches_to_expand_downward(self, mocker) -> None:
        state = _make_state_with_l1()
        ctx = ToolContext(state=state, llm=mocker.MagicMock())

        mock_expand = mocker.patch(
            "explain_engine.chat.tools.expansion.expand_downward",
            return_value=["p_002", "p_003"],
        )
        # AsyncMock
        from unittest.mock import AsyncMock
        mock_expand.side_effect = None
        mocker.patch(
            "explain_engine.chat.tools.expansion.expand_downward",
            new_callable=AsyncMock,
            return_value=["p_002", "p_003"],
        )
        input = expand_tool.input_schema(l1_id="c_001", direction="downward")
        result = await expand_tool.call(input, ctx)
        assert "p_002" in result or "2" in result


class TestCheckTool:
    @pytest.mark.asyncio
    async def test_call_returns_acceptance_summary(self, mocker) -> None:
        state = _make_state_with_l1()
        ctx = ToolContext(state=state, llm=mocker.MagicMock())
        from explain_engine.engines.simulation import AcceptanceReport
        mocker.patch(
            "explain_engine.chat.tools.aggregate_acceptance",
            return_value=AcceptanceReport(
                avg_consistency=0.8, avg_essentialness=0.5,
                weak_chain_l1s=["c_002"],
                rollout_coverage=0.9,
            ),
        )
        input = check_tool.input_schema()
        result = await check_tool.call(input, ctx)
        assert "0.8" in result or "consistency" in result.lower()
        assert "c_002" in result  # weak_chain_l1s


class TestPredictAndCounterfactualTools:
    def test_predict_has_intervention_text_param(self) -> None:
        input = predict_tool.input_schema(intervention_text="如果加入 X")
        assert input.intervention_text == "如果加入 X"

    def test_counterfactual_has_intervention_text_param(self) -> None:
        input = counterfactual_tool.input_schema(intervention_text="如果移除 Y")
        assert input.intervention_text == "如果移除 Y"


class TestCompressTool:
    @pytest.mark.asyncio
    async def test_call_dispatches_to_compression(self, mocker) -> None:
        state = _make_state_with_l1()
        ctx = ToolContext(state=state, llm=mocker.MagicMock())
        from unittest.mock import AsyncMock
        mocker.patch(
            "explain_engine.chat.tools.compression.compress",
            new_callable=AsyncMock,
            return_value=["c_002", "c_003"],
        )
        input = compress_tool.input_schema()
        result = await compress_tool.call(input, ctx)
        assert "c_002" in result or "compressed" in result.lower()
```

### Step 2: 跑测试 — 全 fail

Run: `.venv/bin/python -m pytest tests/test_chat_tools.py -v`

Expected: ~12 FAIL.

### Step 3: 实现 chat/__init__.py + tools.py

Create `src/explain_engine/chat/__init__.py`:

```python
"""Phase 9: Conversational Cognitive Engine.

Modules:
  session.py — ChatSession outer loop (Task C.1)
  loop.py    — query_loop inner async generator (Task C.2)
  tools.py   — Tool dataclass + 7 tool definitions (Task B.1/B.2)
  hooks.py   — reflect/lifecycle/sessionMemory writer (Task D.2)
  compaction.py — 3-tier compaction (Task E.1)
  slash_commands.py — local intercepts (Task F.1)
  system_prompt.py — dynamic prompt assembly (Task C.2)
  budget.py   — per-turn + per-session counter (Task D.1)
  hitl.py     — mid-turn confirmation prompt (Task D.1)
"""
```

Create `src/explain_engine/chat/tools.py`:

```python
"""Phase 9 Wave B: Tool dataclass + 5 engine tool wrappers + (B.2) 2 new tools.

Each Tool wraps an existing engine (or simple graph mutation/read) so LLM can
invoke via Anthropic native tool_use API in chat loop (Task C.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from explain_engine.engines import (
    compression,
    counterfactual,
    expansion,
    prediction,
)
from explain_engine.engines.simulation import aggregate_acceptance, check_consistency
from explain_engine.llm.client import LLMClient
from explain_engine.schema.state import CognitiveState


@dataclass
class ToolContext:
    """Per-call ctx passed to every Tool.call()."""
    state: CognitiveState
    llm: LLMClient | None = None


@dataclass
class Tool:
    """Phase 9 Wave B: capability wrap for LLM tool_use API."""
    name: str
    input_schema: type[BaseModel]
    description: Callable[[ToolContext], str]
    call: Callable[[BaseModel, ToolContext], Awaitable[str]]
    is_readonly: bool = False
    is_destructive: bool = False
    requires_hitl: bool = False  # True → 走 hitl gate (Task D.1)


# ─── 5 engine tool wrappers ───

class _ExpandInput(BaseModel):
    l1_id: str | None = Field(default=None, description="L1 node id; None=auto-pick frontier")
    direction: Literal["downward", "upward", "auto"] = Field(default="auto")


def _expand_description(ctx: ToolContext) -> str:
    g = ctx.state.graph
    n_l0 = sum(1 for n in g.nodes.values() if n.abstraction_level == 0)
    n_l1 = sum(1 for n in g.nodes.values() if n.abstraction_level == 1)
    return (
        f"扩展 L1 节点. direction=downward 加 manifests_as L0 子节点; "
        f"upward 加 incoming causes driver; auto 由 LLM 决定. "
        f"l1_id=None → auto pick frontier. "
        f"Current: {n_l0} L0, {n_l1} L1."
    )


async def _expand_call(input: _ExpandInput, ctx: ToolContext) -> str:
    if input.direction in ("downward", "auto") and input.l1_id:
        ids = await expansion.expand_downward(ctx.state, input.l1_id, ctx.llm)
        return f"expanded {input.l1_id} downward, added L0: {ids}"
    elif input.direction == "upward" or input.l1_id is None:
        # frontier expand (upward by default)
        ids, gain = await expansion.expand_one_frontier(ctx.state, ctx.llm, target=input.l1_id)
        return f"expanded frontier, added drivers: {ids} (gain={gain:.2f})"
    return "no expansion performed"


expand_tool = Tool(
    name="expand",
    input_schema=_ExpandInput,
    description=_expand_description,
    call=_expand_call,
)


class _CompressInput(BaseModel):
    pass


async def _compress_call(input: _CompressInput, ctx: ToolContext) -> str:
    candidates = await compression.compress(ctx.state, ctx.llm)
    return f"compressed, {len(candidates)} L1 candidates: {candidates}"


compress_tool = Tool(
    name="compress",
    input_schema=_CompressInput,
    description=lambda ctx: "把 L0 observations 抽象成 L1 candidates (Phase 4 compression).",
    call=_compress_call,
)


class _CheckInput(BaseModel):
    target_id: str | None = Field(default=None, description="若 None, 全 graph multi-signal; 若指定, 单 target consistency")


async def _check_call(input: _CheckInput, ctx: ToolContext) -> str:
    if input.target_id is None:
        report = aggregate_acceptance(ctx.state)
        return (
            f"acceptance: avg_consistency={report.avg_consistency:.3f}, "
            f"avg_essentialness={report.avg_essentialness:.3f}, "
            f"weak_chain_l1s={report.weak_chain_l1s}, "
            f"rollout_coverage={report.rollout_coverage:.3f}, "
            f"missing_l0={report.missing_l0}"
        )
    else:
        r = check_consistency(ctx.state, input.target_id)
        return (
            f"target {input.target_id}: consistency={r.consistency_score:.3f}, "
            f"essentialness={r.essentialness_score:.3f}, "
            f"weak_chains_in_target={r.weak_chains}"
        )


check_tool = Tool(
    name="check",
    input_schema=_CheckInput,
    description=lambda ctx: "跑 multi-signal acceptance (target_id=None) 或单 target consistency check.",
    call=_check_call,
    is_readonly=True,
)


class _PredictInput(BaseModel):
    intervention_text: str = Field(min_length=1, description="自然语言, e.g. '如果加入 X 因素'")


async def _predict_call(input: _PredictInput, ctx: ToolContext) -> str:
    report = await prediction.predict(ctx.state, input.intervention_text, ctx.llm)
    return (
        f"prediction added: new_nodes={report.new_node_ids}, "
        f"predicted_L0={report.predicted_L0_ids}, "
        f"propagated_activations={dict(list(report.propagation_acts.items())[:5])}..."
    )


predict_tool = Tool(
    name="predict",
    input_schema=_PredictInput,
    description=lambda ctx: "Forward prediction: 给定自然语言 intervention, 预测会 manifest 什么新现象.",
    call=_predict_call,
)


class _CounterfactualInput(BaseModel):
    intervention_text: str = Field(min_length=1, description="自然语言, e.g. '如果移除 Y' 或 '用 Z 替代 Y'")


async def _counterfactual_call(input: _CounterfactualInput, ctx: ToolContext) -> str:
    report = await counterfactual.substitute(ctx.state, input.intervention_text, ctx.llm)
    return (
        f"counterfactual: baseline_avg_consistency={report.baseline_avg_consistency:.3f}, "
        f"cf_avg_consistency={report.cf_avg_consistency:.3f}, "
        f"diff={report.diff_summary}"
    )


counterfactual_tool = Tool(
    name="counterfactual",
    input_schema=_CounterfactualInput,
    description=lambda ctx: "Counterfactual: '如果移除/替换 Y' 副作用 = 0 (deepcopy graph 模拟).",
    call=_counterfactual_call,
)


# Master registry
ALL_TOOLS: list[Tool] = [
    expand_tool,
    compress_tool,
    check_tool,
    predict_tool,
    counterfactual_tool,
]
```

### Step 4: 跑测试 — verify PASS

Run: `.venv/bin/python -m pytest tests/test_chat_tools.py -v`

Expected: 12 PASS (or close, fix any minor signature mismatch).

### Step 5: 跑全测 + ruff

Expected: 509 PASS (497 + 12), ruff 0.

### Step 6: Commit

```bash
git add src/explain_engine/chat/__init__.py \
        src/explain_engine/chat/tools.py \
        tests/test_chat_tools.py
git commit -m "$(cat <<'EOF'
Wave B.1 · Tool dataclass + 5 engine tool wrappers

新模块 chat/__init__.py (Phase 9 主目录) + chat/tools.py.

Tool dataclass: name / input_schema (Pydantic) / description (dynamic, 含 graph
hint) / call (async wrapper) / is_readonly / is_destructive / requires_hitl.

5 engine tool wrappers (复用 Phase 0-8 全部 engines):
- expand     → expansion.expand_downward / expand_one_frontier (auto pick)
- compress   → compression.compress
- check      → simulation.aggregate_acceptance / check_consistency
- predict    → prediction.predict
- counterfactual → counterfactual.substitute

Tool description 是 dynamic Callable[ctx -> str], 含 graph 当前状态 hint
(e.g. expand 描述含 'Current: 5 L0, 3 L1'), LLM 看到现状决定怎么调用.

ALL_TOOLS 注册表; Task B.2 加 add_observation + read_node = 7 总.

+12 tests. 509 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task B.2: `add_observation` + `read_node` tools

**目的**: 加 2 个 chat-specific tool — `add_observation` (mutation, 含 HITL hint) + `read_node` (read-only, lazy load 用). Tool 数从 5 → 7.

**Files:**
- Modify: `src/explain_engine/chat/tools.py` (append 2 tools + ALL_TOOLS)
- Modify: `tests/test_chat_tools.py` (add tests)

---

### Step 1: 写失败测试

Append to `tests/test_chat_tools.py`:

```python
class TestAddObservationTool:
    def test_input_schema_source_literal(self) -> None:
        from explain_engine.chat.tools import add_observation_tool
        # source must be "user_explicit" or "llm_inferred"
        input = add_observation_tool.input_schema(
            name="new obs", description="d", source="user_explicit",
        )
        assert input.source == "user_explicit"

    def test_input_schema_rejects_invalid_source(self) -> None:
        from explain_engine.chat.tools import add_observation_tool
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            add_observation_tool.input_schema(
                name="x", description="d", source="other",
            )

    @pytest.mark.asyncio
    async def test_call_adds_l0_node(self) -> None:
        from explain_engine.chat.tools import add_observation_tool
        state = _make_state_with_l1()
        ctx = ToolContext(state=state)
        input = add_observation_tool.input_schema(
            name="新观察", description="desc", source="user_explicit",
        )
        result = await add_observation_tool.call(input, ctx)
        # New L0 node added
        new_nodes = [n for n in state.graph.nodes.values() 
                     if n.name == "新观察" and n.abstraction_level == 0]
        assert len(new_nodes) == 1
        assert "added" in result.lower()

    def test_requires_hitl_flag(self) -> None:
        from explain_engine.chat.tools import add_observation_tool
        # HITL is conditional on source; the flag here says "may need hitl"
        assert add_observation_tool.requires_hitl is True


class TestReadNodeTool:
    @pytest.mark.asyncio
    async def test_call_returns_full_description(self) -> None:
        from explain_engine.chat.tools import read_node_tool
        state = _make_state_with_l1()
        ctx = ToolContext(state=state)
        input = read_node_tool.input_schema(node_id="c_001")
        result = await read_node_tool.call(input, ctx)
        assert "c_001" in result
        assert "abs" in result or "d" in result  # name or desc

    @pytest.mark.asyncio
    async def test_call_missing_node_returns_error_message(self) -> None:
        from explain_engine.chat.tools import read_node_tool
        state = _make_state_with_l1()
        ctx = ToolContext(state=state)
        input = read_node_tool.input_schema(node_id="c_999")
        result = await read_node_tool.call(input, ctx)
        assert "not found" in result.lower() or "未找到" in result

    def test_is_readonly(self) -> None:
        from explain_engine.chat.tools import read_node_tool
        assert read_node_tool.is_readonly is True


class TestALL_TOOLS_v2:
    def test_contains_7_tools_after_b2(self) -> None:
        from explain_engine.chat.tools import ALL_TOOLS
        names = {t.name for t in ALL_TOOLS}
        assert names == {
            "expand", "compress", "check", "predict", "counterfactual",
            "add_observation", "read_node",
        }
```

### Step 2: 跑测试 — fail

Run: `.venv/bin/python -m pytest tests/test_chat_tools.py -v -k "AddObservation or ReadNode or _v2"`

Expected: ~8 FAIL.

### Step 3: 实现 2 tools

Append to `src/explain_engine/chat/tools.py`:

```python
from explain_engine.schema.nodes import VariableNode


# ─── add_observation tool (Phase 9 Wave B.2 mutation) ───

class _AddObservationInput(BaseModel):
    name: str = Field(min_length=1, description="新观察简短名字")
    description: str = Field(min_length=1, description="详细描述")
    source: Literal["user_explicit", "llm_inferred"] = Field(
        description="user_explicit = user 明确说加; llm_inferred = LLM 自己推断 (走 HITL gate)"
    )


def _next_p_id(state: CognitiveState) -> str:
    existing = [
        int(nid.split("_")[1]) for nid in state.graph.nodes
        if nid.startswith("p_") and nid[2:].isdigit()
    ]
    return f"p_{(max(existing) + 1) if existing else 1:03d}"


async def _add_observation_call(input: _AddObservationInput, ctx: ToolContext) -> str:
    # HITL gate handled at dispatch level (chat/hitl.py Task D.1); engine just adds
    nid = _next_p_id(ctx.state)
    ctx.state.graph.add_node(VariableNode(
        id=nid, name=input.name, description=input.description,
        abstraction_level=0, confidence=0.7, epistemic="observation",
        source="user",
    ))
    return f"added L0 observation {nid}: {input.name} (source={input.source})"


add_observation_tool = Tool(
    name="add_observation",
    input_schema=_AddObservationInput,
    description=lambda ctx: (
        "添加新 L0 observation 到 graph. "
        "若 user 明确说'加这个观察'→ source='user_explicit' (直接执行). "
        "若 LLM 自己推断该加 → source='llm_inferred' (触发 user confirm)."
    ),
    call=_add_observation_call,
    requires_hitl=True,  # Conditional gate; impl checks source
)


# ─── read_node tool (Phase 9 Wave B.2 lazy load) ───

class _ReadNodeInput(BaseModel):
    node_id: str = Field(min_length=1)


async def _read_node_call(input: _ReadNodeInput, ctx: ToolContext) -> str:
    node = ctx.state.graph.nodes.get(input.node_id)
    if node is None:
        return f"node {input.node_id!r} not found in graph"
    return (
        f"{node.id} | {node.name} | level={node.abstraction_level} | "
        f"confidence={node.confidence:.2f} | epistemic={node.epistemic} | "
        f"lifecycle_state={node.lifecycle_state}\n"
        f"description: {node.description}"
    )


read_node_tool = Tool(
    name="read_node",
    input_schema=_ReadNodeInput,
    description=lambda ctx: "读节点完整 description (lean context lazy load 用).",
    call=_read_node_call,
    is_readonly=True,
)


# Update master registry
ALL_TOOLS = [
    expand_tool, compress_tool, check_tool, predict_tool, counterfactual_tool,
    add_observation_tool, read_node_tool,
]
```

### Step 4: 跑测试 + 全测 + ruff

Expected: 517 PASS (509 + 8), ruff 0.

### Step 5: Commit

```bash
git add src/explain_engine/chat/tools.py tests/test_chat_tools.py
git commit -m "$(cat <<'EOF'
Wave B.2 · add_observation + read_node tools (总 7 tool)

add_observation (mutation, requires_hitl=True):
- input: name / description / source ∈ {user_explicit, llm_inferred}
- user_explicit → 直接加 L0 (走 user 主动添加路径)
- llm_inferred → Task D.1 hitl gate 触发 mid-turn confirm
- 加 L0 节点用 next_p_id(state), epistemic="observation", source="user"

read_node (readonly, lazy load):
- input: node_id
- output: id|name|level|confidence|epistemic|lifecycle_state + 完整 description
- 给 lean context 模式 (Q6 选 γ 后, system prompt 只含 graph summary, LLM 想看
  细节调此 tool)

ALL_TOOLS 现 7 tools (5 engine + 2 chat-specific).

+8 tests. 517 PASS, ruff 0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Wave B Checkpoint

**完成度**: 2/2 task. +20 tests (497 → 517). 7 tools 全定义.

**验证**:
- Tool dataclass + 5 engine tool wrappers (12 tests)
- add_observation + read_node 2 chat tools (8 tests)
- ALL_TOOLS 注册 7 tools

**Stop**. 等用户审过, 进 Wave C.

---

# Wave C — Chat Loop

> **Note**: Task C.1 + C.2 是 Phase 9 核心. ChatSession (outer) + query_loop (inner) + system_prompt 拼装. 引入 Anthropic native tool_use API. 测试用 mock LLM 模拟 tool_use response.

## Task C.1: ChatSession outer loop + persistence integration

**目的**: ChatSession 是 Phase 9 顶层 orchestrator — 加载 sidecar / 解析 user input (slash command vs 自然语言) / 持久化 / 退出处理. 暂不接 query_loop (Task C.2), 但骨架完整.

**Files:**
- Create: `src/explain_engine/chat/session.py`
- Create: `tests/test_chat_session.py`

---

### Step 1-7: TDD 流程 (省略 detail, 按 Wave A/B 模板做)

关键测试 outline:
- `test_load_existing_session_reads_metadata_graph`
- `test_load_new_session_creates_empty_sidecar`
- `test_persist_appends_transcript`
- `test_persist_writes_chat_state`
- `test_slash_command_intercept_does_not_call_llm`
- `test_exit_flushes_sidecar`
- `test_resume_loads_existing_transcript`
- `test_chat_state_includes_budget_alignment_field`

实现要点:
- `ChatSession` class 持有: `sid`, `storage: StorageV2`, `state: CognitiveState`, `transcript: list`, `memory_md: str`, `chat_state: ChatStateDict`
- `__init__(sid)` load from storage_v2
- `await handle_user_input(text) -> AsyncIterator[Event]` — slash dispatch OR query_loop yield
- `persist_async()` debounced (e.g. every 5s flush, or on TurnComplete)
- `close()` final flush

### Step 8: Commit

(详细 commit message 同 Wave B 模板; ~8 tests; 525 PASS)

---

## Task C.2: query_loop async generator + system_prompt 动态拼装

**目的**: query_loop 是 inner LLM ↔ tools while-loop (Claude Code query.ts: queryLoop). system_prompt 动态拼装 (per Claude Code fetchSystemPromptParts).

**Files:**
- Create: `src/explain_engine/chat/loop.py`
- Create: `src/explain_engine/chat/system_prompt.py`
- Create: `tests/test_chat_loop.py`
- Create: `tests/test_chat_system_prompt.py`

关键测试:
- `test_query_loop_terminates_when_no_tool_use`
- `test_query_loop_dispatches_tool_call`
- `test_query_loop_appends_tool_result_to_messages`
- `test_query_loop_streams_assistant_text_event`
- `test_query_loop_streams_tool_use_event`
- `test_system_prompt_includes_graph_summary`
- `test_system_prompt_includes_all_tool_descriptions`
- `test_system_prompt_includes_budget_status`

(详细 code 同 Wave A/B 模板; ~7 tests; 532 PASS)

---

## Wave C Checkpoint

**完成度**: 2/2 task. +15 tests (517 → 532). Chat loop 骨架 + system prompt 拼装.

**验证**:
- ChatSession 持久化集成 (8 tests)
- query_loop + system_prompt (7 tests)

**Stop**. 等用户审过, 进 Wave D.

---

# Wave D — Hooks + Budget + HITL

## Task D.1: Budget counter + Smart HITL gate

(详细同上模板; ~7 tests; 539 PASS)

## Task D.2: reflect / lifecycle / sessionMemory writer hooks

(详细同上模板; ~6 tests; 545 PASS)

---

## Wave D Checkpoint

+13 tests (532 → 545). Budget + HITL + 3 hooks 完整.

**Stop**. 等用户审过.

---

# Wave E — Compaction + A+B fix

## Task E.1: 3-tier compaction

关键测试:
- microCompact: stale tool result 替换为 stub
- sessionMemory splice: memory.md 新于 transcript prefix 时 splice in
- emergency: token limit 触发 sync LLM summarize
- pipeline: 3 tier 顺序应用

(详细同上模板; ~9 tests; 554 PASS)

## Task E.2: A+B 折叠 fix

**A**: `_propagation.rollout_from_roots` 当所有 L2 decayed → fallback to active L1 roots.

Modify `src/explain_engine/engines/_propagation.py`:
- 改 `roots = {...}` 后, 立即 filter `if not _is_decayed(r)`, 然后 if not active_roots: fallback to L1

**B**: `last_input_alignment_report` 持久化通过 chat_state.json.

Modify `ChatSession` (Task C.1) 的 chat_state 序列化, 自动包含 alignment.

(~3 tests; 557 PASS)

---

## Wave E Checkpoint

+12 tests (545 → 557). 3-tier compaction + A+B fix.

**Stop**. 等用户审过.

---

# Wave F — Slash + CLI

## Task F.1: 6 个默认 slash commands

`/quit`, `/help`, `/show`, `/budget`, `/compact`, `/save`.

(~6 tests; 563 PASS)

## Task F.2: CLI explain chat + explain migrate + Rich Live streaming

(~4 tests; 567 PASS)

---

## Wave F Checkpoint

+10 tests (557 → 567). Slash + CLI 集成.

**Stop**. 等用户审过, 进 Wave G.

---

# Wave G — Acceptance + 文档

## Task G.1: Real chat run + 老 482 测试全 PASS verify

```bash
.venv/bin/explain migrate            # 一次性
.venv/bin/explain chat <fresh_sid>   # 真实 chat
# 验证: 7 tool 都被调到, 3-tier compaction 真触发, HITL 真 prompt
```

写 5 个 acceptance test 锁定 end-to-end 流程.

(572 PASS)

## Task G.2: acceptance doc + README + Phase 10 motivations

Create `docs/plans/2026-05-17-conversational-cognitive-engine-acceptance.md`.

Update `README.md` Status to Phase 9 milestone.

(0 tests)

---

## Wave G Checkpoint (Phase 9 完结)

**总成绩**:
- 13 task, ~85 step
- +90 tests (482 → 572)
- ruff 0 全程
- 13 commit per Wave (3+2+2+2+2+2 = 13) + 7 Wave fix commits ≈ 20 commits
- Acceptance verdict: TBD

**Stop**. Phase 9 完结. 等用户决定:
- Phase 10 (cross-session knowledge pool 实际填充)
- Housekeeping (extract _thresholds.py 等)

---

# 附录 A: Wave 间依赖图

```
A.1 → B.1 → B.2 ──┐
     ↓     ↓      ├─→ C.1 → C.2 ──→ D.1, D.2 ─→ E.1, E.2 ─→ F.1, F.2 ─→ G.1 → G.2
     A.1 → C.1 ──┘                  (B.2 hitl)   (C.2 ctx)   (C.1 cli)
```

可并行 batch:
- Batch 1: A.1 (foundation)
- Batch 2: B.1 (依赖 A.1)
- Batch 3: B.2 + C.1 (并行, 依赖 B.1 / A.1)
- Batch 4: C.2 (依赖 B.2 + C.1)
- Batch 5: D.1 + D.2 + F.1 + F.2 (并行, 依赖 C.2)
- Batch 6: E.1 + E.2 (依赖 D.2)
- Batch 7: G.1 → G.2 (顺序)

线性执行更稳, Wave-by-Wave stop checkpoint.

---

# 附录 B: 测试增量明细

| Task | 新 tests | 累积 |
|------|---------|------|
| A.1 | +15 | 497 |
| B.1 | +12 | 509 |
| B.2 | +8 | 517 |
| C.1 | +8 | 525 |
| C.2 | +7 | 532 |
| D.1 | +7 | 539 |
| D.2 | +6 | 545 |
| E.1 | +9 | 554 |
| E.2 | +3 | 557 |
| F.1 | +6 | 563 |
| F.2 | +4 | 567 |
| G.1 | +5 | 572 |
| G.2 | 0 | 572 |

**目标 final: 572 PASS (Phase 8 baseline 482 → Phase 9 final 572, +90).**

---

# 附录 C: Commit 风格 reminder

每个 Task commit 用 HEREDOC + 中文 + Co-Authored-By with `(1M context)` trailer:

```bash
git commit -m "$(cat <<'EOF'
Wave X.Y · 简短主题

详细描述 (中文):
- 改动 1
- 改动 2

哲学锚点 / Claude Code 参考 (if applicable).
测试增量 + ruff 状态.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

**Plan 完结**. 共 13 task / 7 Wave / +90 tests. 等待用户决定执行方式.
