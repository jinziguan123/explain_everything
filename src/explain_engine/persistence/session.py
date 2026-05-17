"""Session 落地：Phase 9 起 delegate to storage_v2 (project-based dir layout)。

公开接口 (SessionMeta / Session / SessionStore) 完全向后兼容 Phase 0-8 测试,
但内部不再用 flat sessions/*.json — 改 storage_v2.StorageV2 写到
~/.explain/projects/<proj>/sessions/<sid>/{metadata.json, graph.json}.

session_id 格式: s_{8 hex}
"""

import logging
import re
import secrets
import time
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from explain_engine.persistence.storage_v2 import StorageV2
from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)

Stage = Literal[
    "bootstrap_pending",   # 等 HITL 1
    "insight_pending",     # Compression + Evaluation 完成，等 HITL 2
    "done",                # HITL 2 完成 (Phase 4 终态 → Phase 5 入口)
    "converged",           # Phase 5 reasoning loop 完成 (终态)
]

_SESSION_ID_RE = re.compile(r"^s_[0-9a-f]{8}$")
_VALID_STAGES = frozenset({"bootstrap_pending", "insight_pending", "done", "converged"})


def _new_session_id() -> str:
    return "s_" + secrets.token_hex(4)


@dataclass
class SessionMeta:
    session_id: str
    question: str
    stage: Stage
    created_at: float
    updated_at: float

    def __post_init__(self) -> None:
        if not _SESSION_ID_RE.match(self.session_id):
            raise ValueError(f"invalid session_id format: {self.session_id!r}")
        if self.stage not in _VALID_STAGES:
            raise ValueError(
                f"invalid stage: {self.stage!r}, must be one of {sorted(_VALID_STAGES)}"
            )

    @classmethod
    def new(cls, question: str) -> "SessionMeta":
        now = time.time()
        return cls(
            session_id=_new_session_id(),
            question=question,
            stage="bootstrap_pending",
            created_at=now,
            updated_at=now,
        )


@dataclass
class Session:
    meta: SessionMeta
    state: CognitiveState

    def to_dict(self) -> dict:
        return {
            "meta": asdict(self.meta),
            "state": self.state.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        try:
            return cls(
                meta=SessionMeta(**d["meta"]),
                state=CognitiveState.from_dict(d["state"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"invalid session dict: {exc}") from exc


class SessionStore:
    """Phase 0-8 向后兼容接口, 内部 delegate 到 StorageV2.

    `directory` 参数仍接受但被忽略 — Phase 9 起所有 session 走
    storage_v2 layout (~/.explain/projects/<proj>/sessions/<sid>/).
    EXPLAIN_HOME / EXPLAIN_PROJECT_ID env vars 控制实际落盘位置.

    Phase 9 Wave A.1 fix · I2: passing `directory=` emits DeprecationWarning
    so users who set SESSIONS_DIR env (assuming it routes data) notice the
    silent no-op and switch to EXPLAIN_HOME / EXPLAIN_PROJECT_ID.
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        if directory is not None:
            warnings.warn(
                "SessionStore(directory=...) parameter is ignored since Phase 9. "
                "Storage is now project-based at ~/.explain/projects/<proj>/sessions/. "
                "Set EXPLAIN_HOME env var to override, or EXPLAIN_PROJECT_ID for "
                "project isolation.",
                DeprecationWarning,
                stacklevel=2,
            )
        # directory 不再存; storage_v2 每次读 EXPLAIN_HOME / EXPLAIN_PROJECT_ID.
        self.directory = Path(directory) if directory is not None else None
        self._storage = StorageV2()

    def save(self, session: Session) -> None:
        session.meta.updated_at = time.time()
        sid = session.meta.session_id
        meta_dict = asdict(session.meta)
        state_dict = session.state.to_dict()
        self._storage.save_metadata(sid, meta_dict)
        self._storage.save_graph(sid, state_dict)

    def load(self, session_id: str) -> Session:
        try:
            meta_dict = self._storage.load_metadata(session_id)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"session {session_id} not found at {self._storage.session_dir(session_id)}"
            ) from exc
        try:
            state_dict = self._storage.load_graph(session_id)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"session {session_id} graph not found at {self._storage.session_dir(session_id)}"
            ) from exc
        try:
            return Session(
                meta=SessionMeta(**meta_dict),
                state=CognitiveState.from_dict(state_dict),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"invalid session dict: {exc}") from exc

    def list(self) -> list[SessionMeta]:
        """List all session metas. 跳过 corrupted / 不合规的 session_dir."""
        metas: list[SessionMeta] = []
        for sid in self._storage.list_sessions():
            try:
                meta_dict = self._storage.load_metadata(sid)
                metas.append(SessionMeta(**meta_dict))
            except (FileNotFoundError, ValueError, TypeError, KeyError) as exc:
                logger.warning("skipping unreadable session %s: %s", sid, exc)
                continue
        metas.sort(key=lambda m: m.created_at, reverse=True)
        return metas
