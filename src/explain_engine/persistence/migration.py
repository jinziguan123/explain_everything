"""Phase 9 Wave A: One-time migration sessions/*.json → ~/.explain/projects/<proj>/sessions/<sid>/

Idempotent: detect existing migrated sessions and skip.
Legacy files moved to sessions/.legacy/ (not deleted, for safety).
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from explain_engine.persistence.storage_v2 import StorageV2

# 严格匹配主 session 文件名, 排除 backup snapshots (e.g. s_xxx.before-rescore.json)
_SESSION_RE = re.compile(r"^s_[0-9a-f]{8}\.json$")


def detect_legacy_sessions() -> list[str]:
    """Find sessions/s_*.json (skip backup snapshots). Returns sorted sids."""
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

    Behavior:
    - 若 legacy file 不存在: return {"sid": sid, "migrated": False, "reason": "legacy file not found"}
    - 若已迁 (session_dir 已存在): return {"sid": sid, "migrated": False, "reason": "already migrated"}
    - 若 dry_run=True: return {"sid": sid, "migrated": False, "reason": "dry-run"}
    - 否则: 拆 legacy {meta, state} → metadata.json + graph.json, move legacy
      file to sessions/.legacy/<sid>.json (safety: never delete user data),
      return {"sid": sid, "migrated": True}
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
    archive = legacy_archive_dir / f"{sid}.json"
    if archive.exists():
        # Bump suffix to preserve prior backup (defensive: never silently overwrite)
        archive = legacy_archive_dir / f"{sid}.{int(time.time())}.json"
    shutil.move(str(legacy_path), str(archive))

    return {"sid": sid, "migrated": True}


def migrate_all(dry_run: bool = False) -> list[dict]:
    """Migrate all legacy sessions found. Idempotent. Returns per-session result."""
    sids = detect_legacy_sessions()
    return [migrate_session(sid, dry_run=dry_run) for sid in sids]
