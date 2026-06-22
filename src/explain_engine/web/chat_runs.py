"""会话生成的后台运行管理: 把一轮 AI 生成从 HTTP 连接上解耦。

为什么:
- 原来生成是惰性 async generator, 只在 SSE 客户端读流时才前进, 且任务挂在
  StreamingResponse 上 → 浏览器刷新/断线 → 服务器取消响应任务 → 生成中断,
  连收尾 persist 都来不及跑 (图谱/标题丢失)。
- 现在: 一轮生成放进独立的后台 asyncio.Task, 事件写入按会话的帧缓冲;
  HTTP 不再驱动生成, 只是"订阅"缓冲。刷新 → 仅断开订阅, 后台照跑、照 persist;
  前端重连后从缓冲头回放 + 续传。

并发设计 (单事件循环, 即 uvicorn 生产场景):
- ChatRun.frames 是已生成 SSE 帧的有序列表; append/finish 同步写 + set 一个
  asyncio.Event 唤醒订阅者。
- subscribe() 是无锁消费: 先冲掉所有已有帧, 再 clear+复检+await, 避免丢唤醒;
  支持任意多个/反复的订阅者 (刷新重连即新订阅)。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from explain_engine.web.sse import chat_event_to_sse, sid_lock, sse_pack

if TYPE_CHECKING:
    from explain_engine.chat.session import ChatEvent, ChatSession
    from explain_engine.llm.client import LLMClient

logger = logging.getLogger(__name__)


class RunInProgress(Exception):
    """该会话已有进行中的生成 (尚未结束), 拒绝重复启动。"""


class ChatRun:
    """一轮生成的服务端状态: 帧缓冲 + 完成标志 + 后台任务句柄。"""

    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.frames: list[str] = []  # 已生成的 SSE 帧 (含 event+data)
        self.done = False
        self.task: asyncio.Task | None = None
        self._updated = asyncio.Event()

    def append(self, frame: str) -> None:
        """追加一帧并唤醒所有订阅者 (同步, 可在取消处理里安全调用)。"""
        self.frames.append(frame)
        self._updated.set()

    def finish(self) -> None:
        """标记本轮结束并唤醒订阅者 (drain 完即返回)。"""
        self.done = True
        self._updated.set()

    async def subscribe(self) -> AsyncIterator[str]:
        """从缓冲头回放已有帧, 再续传新帧, 直到 done。可被多次/并发调用。

        clear+复检+await 顺序保证不丢唤醒: 即便另一订阅者 clear 了 Event,
        本订阅者 await 前会先复检 frames 长度, 有新帧就不进入等待。
        """
        idx = 0
        while True:
            while idx < len(self.frames):
                frame = self.frames[idx]
                idx += 1
                yield frame
            if self.done:
                return
            self._updated.clear()
            if idx < len(self.frames) or self.done:
                continue
            await self._updated.wait()


# 进程级注册表: 每个 sid 至多保留最近一轮 ChatRun (下轮启动时替换)。
_runs: dict[str, ChatRun] = {}


def get_run(sid: str) -> ChatRun | None:
    return _runs.get(sid)


def start_run(
    sid: str,
    message: str,
    chat_session: ChatSession,
    llm: LLMClient,
) -> ChatRun:
    """启动一轮后台生成。已有未结束的 run → RunInProgress。

    立即返回 (不等生成); 后台 Task 驱动 handle_user_input, 把事件写进 run.frames。
    """
    existing = _runs.get(sid)
    if existing is not None and not existing.done:
        raise RunInProgress(sid)
    run = ChatRun(sid)
    # 首帧 run_start: 让重连/订阅端据此重建"当前轮"的 assistant 气泡 (去重)。
    run.append(sse_pack("run_start", {"content": None}))
    _runs[sid] = run
    run.task = asyncio.create_task(_drive(run, chat_session, message, llm))
    return run


def stop_run(sid: str) -> bool:
    """取消进行中的后台生成。返回是否确有可取消的 run。"""
    run = _runs.get(sid)
    if run is not None and not run.done and run.task is not None:
        run.task.cancel()
        return True
    return False


_SLASH_ERROR_TYPES = frozenset({"slash_error", "slash_unknown"})
_WEB_PASSTHROUGH = frozenset({
    "status_start", "status_end", "turn_complete", "run_start",
    "assistant_text_delta", "thinking_delta", "tool_use", "tool_result",
    "budget_exhausted", "error",
})


def _to_web_sse(ev: "ChatEvent") -> str:
    """将 ChatEvent 转为 web 前端能识别的 SSE 帧。

    Web 前端 handleEvent 只处理标准事件类型; slash_* 自定义类型会被
    default:break 静默忽略. 此函数将 slash 事件转译为前端能渲染的类型.
    """
    if ev.type in _WEB_PASSTHROUGH:
        return chat_event_to_sse(ev)
    if ev.type in _SLASH_ERROR_TYPES:
        return sse_pack("error", {"content": ev.content, "metadata": ev.metadata})
    content = ev.content
    if isinstance(content, str) and content:
        return sse_pack("assistant_text_delta", {
            "content": content, "metadata": ev.metadata,
        })
    return ""


async def _drive(
    run: ChatRun,
    chat_session: ChatSession,
    message: str,
    llm: LLMClient,
) -> None:
    """后台驱动一轮 handle_user_input, 事件转 SSE 帧写入 run。

    不挂在任何请求上 → 客户端断开不影响; 异常/取消都补一帧并 finish。
    """
    try:
        async with sid_lock(run.sid):
            logger.info("[_drive] %s: start handle_user_input(%r)", run.sid, message)
            async for ev in chat_session.handle_user_input(message, llm):
                logger.info("[_drive] %s: event type=%s content=%r", run.sid, ev.type, str(ev.content)[:80] if ev.content else None)
                frame = _to_web_sse(ev)
                if frame:
                    run.append(frame)
    except asyncio.CancelledError:
        run.append(sse_pack("error", {"content": "生成已停止"}))
        raise
    except Exception as exc:
        logger.warning(
            "chat run %s failed: %s: %s", run.sid, type(exc).__name__, exc
        )
        run.append(sse_pack("error", {"content": f"{type(exc).__name__}: {exc}"}))
    finally:
        run.finish()


async def shutdown_runs() -> None:
    """取消所有未结束的后台 run (供应用关闭时调用)。"""
    for run in list(_runs.values()):
        if run.task is not None and not run.done:
            run.task.cancel()
            with contextlib.suppress(Exception):
                await run.task
    _runs.clear()
