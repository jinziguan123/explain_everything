"""Session 只读端点 (list / get / graph / transcript)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from explain_engine.chat.session import ChatSession
from explain_engine.config import Settings
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.state import CognitiveState
from explain_engine.web.serializers import graph_to_cytoscape

router = APIRouter(prefix="/api/sessions")


def _load_chat(sid: str) -> ChatSession:
    try:
        return ChatSession(sid)
    except Exception as exc:  # FileNotFoundError / ChatSessionLoadError 等
        raise HTTPException(status_code=404, detail=f"session {sid} 不可用: {exc}") from exc


class CreateSessionBody(BaseModel):
    question: str


@router.post("", status_code=201)
async def create_session(body: CreateSessionBody) -> dict[str, str]:
    """新建空 session (仅 question + 新 sid + 初始 stage); 不调 LLM、不 bootstrap 现象。

    用户后续在 session 内对话以逐步构建图谱 (见 chat 流程)。
    """
    settings = Settings()
    state = CognitiveState.bootstrap(body.question, budget=settings.default_budget)
    meta = SessionMeta.new(question=body.question)
    store = SessionStore()
    try:
        store.save(Session(meta=meta, state=state))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"session 保存失败: {exc}") from exc
    return {"sid": meta.session_id}


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
