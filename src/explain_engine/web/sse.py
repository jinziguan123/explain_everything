"""ChatEvent → SSE 映射 + per-sid 串行锁."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from explain_engine.chat.session import ChatEvent

_locks: dict[str, asyncio.Lock] = {}
"""进程级 per-sid 锁表.

假设单事件循环 (uvicorn 生产即如此): asyncio.Lock 绑定到首次 await 时的 loop,
跨 loop 复用会抛 'Future attached to a different loop'. 测试里每个 TestClient
请求可能起新 loop, 故 tests/web/conftest.py 用 autouse fixture 在每个 test 前
清空本表, 避免跨 loop 复用导致的 flaky。本地单用户单 loop 场景无此问题。
"""


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
    data: dict[str, Any] = {"content": ev.content, "metadata": ev.metadata}
    # ToolUse/ToolResult 事件带基类没有的额外字段 (tool_name / tool_input / result);
    # 显式转发给前端, 否则前端拿不到真实工具名与详情 (卡片只能显示兜底"工具")。
    for attr in ("tool_name", "tool_input", "result"):
        if hasattr(ev, attr):
            data[attr] = getattr(ev, attr)
    return sse_pack(ev.type, data)


async def stream_chat_events(events: AsyncIterator[ChatEvent]) -> AsyncIterator[str]:
    """逐个 ChatEvent → SSE 帧; 引擎异常 → error 事件, 不崩流."""
    try:
        async for ev in events:
            yield chat_event_to_sse(ev)
    except Exception as exc:
        yield sse_pack("error", {"content": f"{type(exc).__name__}: {exc}"})
