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
    """Project-based session storage.

    Layout:
      <EXPLAIN_HOME>/projects/<project_id>/
        sessions/<sid>/
          metadata.json   — SessionMeta dict (question/stage/created_at/updated_at)
          graph.json      — CognitiveState dict (graph + tick + reasoning_trace + ...)
          transcript.jsonl — chat REPL append-only (Phase 9 Wave B+)
          memory.md       — long-term session memory (Phase 9 Wave E+)
          chat_state.json — chat REPL volatile state (Phase 9 Wave B+)
        knowledge/        — cross-session pool (Phase 10+ 占位)
    """

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
        _write_atomic(
            self.session_dir(sid) / "metadata.json",
            json.dumps(meta, ensure_ascii=False, indent=2),
        )

    def load_metadata(self, sid: str) -> dict[str, Any]:
        path = self.session_dir(sid) / "metadata.json"
        if not path.exists():
            raise FileNotFoundError(f"metadata not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    # ── graph.json ──
    def save_graph(self, sid: str, graph_data: dict[str, Any]) -> None:
        _write_atomic(
            self.session_dir(sid) / "graph.json",
            json.dumps(graph_data, ensure_ascii=False, indent=2),
        )

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
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

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
        _write_atomic(
            self.session_dir(sid) / "chat_state.json",
            json.dumps(state, ensure_ascii=False, indent=2),
        )

    def load_chat_state(self, sid: str) -> dict[str, Any] | None:
        path = self.session_dir(sid) / "chat_state.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # ── repl_history.jsonl (Phase 16.2: append-only, display-only, 不进 LLM context) ──
    def append_repl_history(self, sid: str, entry: dict[str, Any]) -> None:
        path = self.session_dir(sid) / "repl_history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── list sessions ──
    def list_sessions(self) -> list[str]:
        sessions_root = self.project_dir() / "sessions"
        if not sessions_root.exists():
            return []
        return [
            d.name
            for d in sessions_root.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
