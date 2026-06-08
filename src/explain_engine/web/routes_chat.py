"""聊天端点: 生成与 HTTP 连接解耦 (后台任务 + 可重连 SSE)。

- POST /{sid}/chat        启动一轮后台生成 (返回 JSON, 不是流)。
- GET  /{sid}/chat/stream 订阅当前轮事件流 (从缓冲头回放 + 续传); 刷新后重连即此。
- POST /{sid}/chat/stop   取消进行中的生成。

刷新页面只断开 GET 订阅, 后台生成不受影响、照常 persist; 重连后 GET 从头回放。
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from explain_engine.config import make_llm_client
from explain_engine.web.chat_runs import RunInProgress, get_run, start_run, stop_run
from explain_engine.web.deps import load_chat_or_404
from explain_engine.web.sse import sse_pack

router = APIRouter(prefix="/api/sessions")


class ChatBody(BaseModel):
    message: str


@router.post("/{sid}/chat")
async def chat(sid: str, body: ChatBody) -> dict[str, bool]:
    """启动一轮后台生成。缺失 session → 404; 已有进行中的生成 → 409。"""
    llm = make_llm_client()
    chat_session = load_chat_or_404(sid)  # 缺失/损坏 → 404 (在 200 前)
    try:
        start_run(sid, body.message, chat_session, llm)
    except RunInProgress as exc:
        raise HTTPException(
            status_code=409, detail="本会话已有进行中的生成, 请等待完成或先停止"
        ) from exc
    return {"started": True}


@router.get("/{sid}/chat/stream")
async def chat_stream(sid: str) -> StreamingResponse:
    """订阅当前轮的事件流。

    仅订阅"进行中"(未 done) 的 run; 无 run 或已完成 → 立即发 no_active_run。
    已完成的轮次以 transcript 为准 (生成结束时已 persist), 不再回放, 避免刷新后
    重放盖掉历史。
    """
    run = get_run(sid)

    async def gen() -> AsyncIterator[str]:
        if run is None or run.done:
            yield sse_pack("no_active_run", {"content": None})
            return
        async for frame in run.subscribe():
            yield frame

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/{sid}/chat/stop")
async def chat_stop(sid: str) -> dict[str, bool]:
    """取消进行中的后台生成 (无则 no-op)。"""
    stopped = stop_run(sid)
    return {"stopped": stopped}
