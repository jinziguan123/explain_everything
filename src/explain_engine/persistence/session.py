"""Session 落地：每个 session 一个 JSON 文件。

文件名: {session_id}.json
session_id 格式: s_{8 hex}
"""

import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from explain_engine.schema.state import CognitiveState

Stage = Literal[
    "bootstrap_pending",   # 等 HITL 1
    "running",             # runtime loop 中
    "finalize_pending",    # 等 HITL 2
    "done",                # render 完成
]


def _new_session_id() -> str:
    return "s_" + secrets.token_hex(4)


@dataclass
class SessionMeta:
    session_id: str
    question: str
    stage: Stage
    created_at: float
    updated_at: float

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
        return cls(
            meta=SessionMeta(**d["meta"]),
            state=CognitiveState.from_dict(d["state"]),
        )


class SessionStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.directory / f"{session_id}.json"

    def save(self, session: Session) -> None:
        session.meta.updated_at = time.time()
        p = self._path(session.meta.session_id)
        p.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2))

    def load(self, session_id: str) -> Session:
        p = self._path(session_id)
        if not p.exists():
            raise FileNotFoundError(f"session {session_id} not found at {p}")
        return Session.from_dict(json.loads(p.read_text()))

    def list(self) -> list[SessionMeta]:
        metas: list[SessionMeta] = []
        for p in self.directory.glob("s_*.json"):
            d = json.loads(p.read_text())
            metas.append(SessionMeta(**d["meta"]))
        metas.sort(key=lambda m: m.created_at, reverse=True)
        return metas
