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
    # 最近一次"用户提问"的时间戳 (handle_user_input 追加 user 消息时记下,
    # turn 收尾 persist 落盘)。会话列表按它降序排 → 最近用过的会话置顶。
    # 旧 metadata.json 无此字段 → 默认 0.0, 排序时回落到 created_at。
    last_user_message_at: float = 0.0

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

    def load_question(self, session_id: str) -> str | None:
        """只读磁盘上 metadata.question (会话标题)。缺失/损坏 → None。

        给对话流 persist 前回读最新标题用 (标题由 create/autotitle 拥有,
        对话流不应用 turn 开始时的旧值覆盖它)。
        """
        try:
            q = self._storage.load_metadata(session_id).get("question")
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            return None
        return q if isinstance(q, str) and q else None

    def update_question(self, session_id: str, question: str) -> None:
        """只更新 metadata.question (+ updated_at), 不动 graph.json。

        用于 autotitle 与正在进行的 chat 流并发的场景: chat 流会全量
        save() (metadata + graph), 若 autotitle 也走 save() 会用它加载时的
        旧图覆盖 chat 刚写入的新图 → 丢节点。这里只重写 metadata sidecar,
        与 graph 写入互不干扰 (race-safe)。

        session 不存在 → FileNotFoundError, 由上层转 404。
        """
        meta_dict = self._storage.load_metadata(session_id)
        meta_dict["question"] = question
        meta_dict["updated_at"] = time.time()
        self._storage.save_metadata(session_id, meta_dict)

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
        # 按"最近用户提问时间"降序 (最近用过的会话置顶); 没提问过的会话
        # (last_user_message_at=0) 回落到 created_at, 避免沉底。
        metas.sort(
            key=lambda m: m.last_user_message_at or m.created_at, reverse=True
        )
        return metas

    def delete(self, session_id: str) -> None:
        """Phase 17.2 Feature C: 删整个 session dir.

        Thin wrapper → StorageV2.delete_session. 不存在 FileNotFoundError 上层抓.
        权限 / IO 失败 OSError 传播 (不静默吞).
        """
        self._storage.delete_session(session_id)
