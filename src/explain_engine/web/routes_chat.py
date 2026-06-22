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
    import logging as _logging
    _log = _logging.getLogger(__name__)
    _log.info("[chat POST] sid=%s message=%r", sid, body.message[:60])
    llm = make_llm_client()
    chat_session = load_chat_or_404(sid)  # 缺失/损坏 → 404 (在 200 前)
    try:
        start_run(sid, body.message, chat_session, llm)
    except RunInProgress as exc:
        _log.warning("[chat POST] sid=%s RunInProgress!", sid)
        raise HTTPException(
            status_code=409, detail="本会话已有进行中的生成, 请等待完成或先停止"
        ) from exc
    _log.info("[chat POST] sid=%s started OK", sid)
    return {"started": True}


@router.get("/{sid}/chat/stream")
async def chat_stream(sid: str) -> StreamingResponse:
    """订阅当前轮的事件流。

    进行中的 run → subscribe() 实时续传;
    已完成但有缓冲帧 → 回放缓冲帧 (修复 slash 命令等快速完成场景的竞态:
    _drive 在前端 GET 到达前已 finish, 旧逻辑返 no_active_run 导致前端
    永远卡在 streaming 状态);
    无 run → no_active_run (刷新后无活动轮)。
    """
    run = get_run(sid)

    async def gen() -> AsyncIterator[str]:
        if run is None:
            yield sse_pack("no_active_run", {"content": None})
            return
        if run.done:
            for frame in run.frames:
                yield frame
            return
        async for frame in run.subscribe():
            yield frame

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/{sid}/chat/stop")
async def chat_stop(sid: str) -> dict[str, bool]:
    """取消进行中的后台生成 (无则 no-op)。"""
    stopped = stop_run(sid)
    return {"stopped": stopped}
