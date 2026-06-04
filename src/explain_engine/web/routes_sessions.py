"""Session 只读端点 (list / get / graph / transcript)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from explain_engine.chat.session import ChatSession
from explain_engine.persistence.session import SessionStore
from explain_engine.web.serializers import graph_to_cytoscape

router = APIRouter(prefix="/api/sessions")


def _load_chat(sid: str) -> ChatSession:
    try:
        return ChatSession(sid)
    except Exception as exc:  # FileNotFoundError / ChatSessionLoadError 等
        raise HTTPException(status_code=404, detail=f"session {sid} 不可用: {exc}") from exc


@router.get("")
async def list_sessions() -> list[dict[str, Any]]:
    store = SessionStore()
    return [
        {
            "sid": meta.session_id,
            "question": meta.question,
            "stage": meta.stage,
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
        }
        for meta in store.list()
    ]


@router.get("/{sid}")
async def get_session(sid: str) -> dict[str, Any]:
    chat = _load_chat(sid)
    meta = chat._session.meta
    graph = chat.state.graph
    return {
        "sid": sid,
        "question": meta.question,
        "stage": meta.stage,
        "tick": chat.state.tick,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
    }


@router.get("/{sid}/graph")
async def get_graph(sid: str) -> dict[str, Any]:
    chat = _load_chat(sid)
    return graph_to_cytoscape(chat.state.graph)


@router.get("/{sid}/transcript")
async def get_transcript(sid: str) -> list[dict[str, Any]]:
    chat = _load_chat(sid)
    return chat.transcript
