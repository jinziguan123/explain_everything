"""Phase 20.3: 把 callback-based 流式 LLM 调用桥接成 async generator.

LLM client (anthropic_protocol) 的流式接口是 callback 式 —
`chat(messages, on_delta=...)` 在 chunk 到达时调 `await on_delta(kind, text)`,
方法本身仍返回完整 Response/ToolsResponse. 但 chat 层 (ephemeral / query_loop)
是 async generator (yield ChatEvent), 无法在 callback 里 yield.

`stream_llm` 用一个 asyncio.Queue 桥接二者: 把 LLM 调用跑成 task, callback
往 queue 塞 (kind, text) delta, 本 generator 从 queue 取并 yield, 末尾 yield
("done", result) 带上完整返回值. LLM 抛异常时 re-raise 到 generator caller
(保持原错误传播契约).

用法:
    async for kind, payload in stream_llm(
        lambda emit: llm.chat(messages, on_delta=emit)
    ):
        if kind == "text":      # payload: str delta (正文)
            ...
        elif kind == "thinking":  # payload: str delta (推理段)
            ...
        elif kind == "done":      # payload: Response/ToolsResponse (完整结果)
            final = payload
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

# (kind, text) async callback — 传给 LLM client 的 on_delta.
Emit = Callable[[str, str], Awaitable[None]]
# run(emit) -> awaitable[result]: 给定 emit callback, 发起 LLM 调用并返完整结果.
RunWithEmit = Callable[[Emit], Awaitable[Any]]


async def stream_llm(run: RunWithEmit) -> AsyncIterator[tuple[str, Any]]:
    """桥接 callback 式流式 LLM 调用为 (kind, payload) 事件流.

    yield 序列 (按时间):
        ("text"|"thinking", str)  各 delta, 到达顺序
        ("done", result)          最后一项, payload 是 LLM 完整返回值

    LLM 调用抛异常 → 在本 generator 内 re-raise (caller 用 try/except 兜).
    caller 提前 break / GeneratorExit → finally 取消底层 task 并 await 清理.
    """
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def emit(kind: str, text: str) -> None:
        await queue.put((kind, text))

    async def runner() -> None:
        try:
            result = await run(emit)
            await queue.put(("done", result))
        except BaseException as exc:
            # 转发任何异常 (含 LLMError / CancelledError) 到 generator 处重抛
            await queue.put(("error", exc))

    task = asyncio.create_task(runner())
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "error":
                raise payload
            yield kind, payload
            if kind == "done":
                return
    finally:
        # caller break / 异常 / 正常结束都到这: 确保 task 不悬挂.
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
