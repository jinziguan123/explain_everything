"""Web 层共享依赖 helper."""
from __future__ import annotations

from fastapi import HTTPException

from explain_engine.chat.session import ChatSession


def load_chat_or_404(sid: str) -> ChatSession:
    """按 sid 加载 ChatSession; 缺失/损坏 → HTTP 404.

    统一 session 加载入口 (routes_sessions / routes_chat / routes_actions 共用),
    保证 404 行为一致。
    """
    try:
        return ChatSession(sid)
    except Exception as exc:  # FileNotFoundError / ChatSessionLoadError 等
        raise HTTPException(status_code=404, detail=f"session {sid} 不可用: {exc}") from exc
