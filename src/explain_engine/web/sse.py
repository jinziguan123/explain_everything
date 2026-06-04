"""ChatEvent → SSE 映射 + per-sid 串行锁."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from explain_engine.chat.session import ChatEvent

_locks: dict[str, asyncio.Lock] = {}


def sid_lock(sid: str) -> asyncio.Lock:
    """每 sid 一把锁, 串行化同 session 的改图操作 (防 graph.json 损坏)."""
    if sid not in _locks:
        _locks[sid] = asyncio.Lock()
    return _locks[sid]


def sse_pack(event: str, data: Any) -> str:
    """组 SSE 帧."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def chat_event_to_sse(ev: ChatEvent) -> str:
    return sse_pack(ev.type, {"content": ev.content, "metadata": ev.metadata})


async def stream_chat_events(events: AsyncIterator[ChatEvent]) -> AsyncIterator[str]:
    """逐个 ChatEvent → SSE 帧; 引擎异常 → error 事件, 不崩流."""
    try:
        async for ev in events:
            yield chat_event_to_sse(ev)
    except Exception as exc:
        yield sse_pack("error", {"content": f"{type(exc).__name__}: {exc}"})
