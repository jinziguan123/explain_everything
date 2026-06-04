"""聊天 SSE 端点."""
from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from explain_engine.chat.session import ChatSession
from explain_engine.config import make_llm_client
from explain_engine.web.sse import sid_lock, stream_chat_events

router = APIRouter(prefix="/api/sessions")


class ChatBody(BaseModel):
    message: str


@router.post("/{sid}/chat")
async def chat(sid: str, body: ChatBody) -> StreamingResponse:
    llm = make_llm_client()

    async def gen() -> AsyncIterator[str]:
        async with sid_lock(sid):
            chat_session = ChatSession(sid)
            events = chat_session.handle_user_input(body.message, llm)
            async for frame in stream_chat_events(events):
                yield frame

    return StreamingResponse(gen(), media_type="text/event-stream")
