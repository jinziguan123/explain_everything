"""Web 层共享依赖 helper."""
from __future__ import annotations

import re

from fastapi import HTTPException

from explain_engine.chat.session import ChatSession

# sid 形如 s_<8 hex> (见 SessionMeta.new / StorageV2 校验). 防 sid 拼路径时的
# path-traversal (defense-in-depth: 本地单用户, 路由层也会拦, 但读路径不校验 sid).
_SID_RE = re.compile(r"^s_[0-9a-f]{8}$")


def load_chat_or_404(sid: str) -> ChatSession:
    """按 sid 加载 ChatSession; 非法 sid / 缺失 / 损坏 → HTTP 404.

    统一 session 加载入口 (routes_sessions / routes_chat / routes_actions 共用),
    保证 404 行为一致。
    """
    if not _SID_RE.match(sid):
        raise HTTPException(status_code=404, detail=f"非法 session id: {sid}")
    try:
        return ChatSession(sid)
    except Exception as exc:  # FileNotFoundError / ChatSessionLoadError 等
        raise HTTPException(status_code=404, detail=f"session {sid} 不可用: {exc}") from exc
