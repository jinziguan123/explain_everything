"""Session 端点 (list / get / graph / transcript / create / delete)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from explain_engine.config import Settings
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.state import CognitiveState
from explain_engine.web.deps import load_chat_or_404
from explain_engine.web.serializers import graph_to_cytoscape

router = APIRouter(prefix="/api/sessions")


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
    chat = load_chat_or_404(sid)
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
    chat = load_chat_or_404(sid)
    return graph_to_cytoscape(chat.state.graph)


@router.get("/{sid}/transcript")
async def get_transcript(sid: str) -> list[dict[str, Any]]:
    chat = load_chat_or_404(sid)
    return chat.transcript


@router.post("/{sid}/autotitle")
async def autotitle(sid: str) -> dict[str, str]:
    """用 light LLM 据首条用户消息为会话生成简短标题, 写回 meta.question。

    无首条用户消息 / LLM 不可用 / 生成失败 → 原样返回当前标题 (不改)。
    """
    chat = load_chat_or_404(sid)

    first_user = ""
    for e in chat.transcript:
        if e.get("role") == "user" and isinstance(e.get("content"), str):
            first_user = e["content"].strip()
            break
    if not first_user:
        return {"title": chat._session.meta.question}

    from explain_engine.config import make_light_llm_client
    from explain_engine.llm.client import Message

    try:
        llm = make_light_llm_client()
        prompt = (
            "用不超过 12 个汉字概括下面这段提问的主题, 作为会话标题。"
            "只输出标题本身, 不要引号、标点或任何解释。\n\n" + first_user[:500]
        )
        resp = await llm.chat([Message(role="user", content=prompt)])
        title = (resp.text or "").strip().splitlines()[0].strip("「」\"' 　")[:30]
    except Exception:
        title = ""

    if not title:
        return {"title": chat._session.meta.question}

    chat._session.meta.question = title
    try:
        chat.state.graph.root_question = title  # 与 meta 保持一致 (best-effort)
    except Exception:
        pass
    chat.persist()
    return {"title": title}


@router.delete("/{sid}", status_code=204)
async def delete_session(sid: str) -> Response:
    """删除整个 session (graph / transcript / chat_state 等)。缺失/非法 sid → 404。"""
    try:
        SessionStore().delete(sid)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"session {sid} 不可删: {exc}") from exc
    return Response(status_code=204)
