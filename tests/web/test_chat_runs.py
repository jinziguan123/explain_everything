"""ChatRun 后台运行机制单元测试 (asyncio 单 loop, 不经 HTTP)。

覆盖: 回放已有帧、订阅者续传新帧、done 后 drain 返回、并发多订阅者、
start_run 重复启动 RunInProgress、stop_run 取消。
"""
import asyncio

import pytest

from explain_engine.web import chat_runs
from explain_engine.web.chat_runs import (
    ChatRun,
    RunInProgress,
    get_run,
    start_run,
    stop_run,
)


@pytest.fixture(autouse=True)
def _clear_runs():
    chat_runs._runs.clear()
    yield
    chat_runs._runs.clear()


@pytest.mark.asyncio
async def test_subscribe_replays_then_returns_on_done():
    run = ChatRun("s_1")
    run.append("a")
    run.append("b")
    run.finish()
    got = [f async for f in run.subscribe()]
    assert got == ["a", "b"]


@pytest.mark.asyncio
async def test_subscribe_streams_live_frames():
    run = ChatRun("s_2")
    got = []

    async def consume():
        async for f in run.subscribe():
            got.append(f)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)  # 让订阅者进入等待
    run.append("x")
    await asyncio.sleep(0)
    run.append("y")
    run.finish()
    await asyncio.wait_for(task, timeout=1)
    assert got == ["x", "y"]


@pytest.mark.asyncio
async def test_multiple_subscribers_all_receive():
    """两个订阅者 (含"重连"晚到的那个) 都应拿到全部帧。"""
    run = ChatRun("s_3")
    a, b = [], []

    async def consume(sink):
        async for f in run.subscribe():
            sink.append(f)

    t1 = asyncio.create_task(consume(a))
    await asyncio.sleep(0)
    run.append("1")
    await asyncio.sleep(0)
    # 第二个订阅者晚加入 (模拟刷新重连) — 应回放已有帧
    t2 = asyncio.create_task(consume(b))
    await asyncio.sleep(0)
    run.append("2")
    run.finish()
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=1)
    assert a == ["1", "2"]
    assert b == ["1", "2"]


@pytest.mark.asyncio
async def test_start_run_conflict_raises():
    async def never_ending(self, text, llm=None):
        await asyncio.Event().wait()
        yield  # pragma: no cover

    class _Sess:
        handle_user_input = never_ending

    sess = _Sess()
    start_run("s_4", "hi", sess, object())
    with pytest.raises(RunInProgress):
        start_run("s_4", "hi again", sess, object())
    # 清理后台任务
    stop_run("s_4")


@pytest.mark.asyncio
async def test_drive_runs_to_completion_and_persists():
    """后台任务跑完 handle_user_input (不依赖订阅者), 并标记 done。"""
    events_seen = {"persisted": False}

    async def fake_handle(self, text, llm=None):
        from explain_engine.chat.session import ChatEvent
        yield ChatEvent(type="assistant_text_delta", content="hi")
        yield ChatEvent(type="turn_complete", content=None)
        events_seen["persisted"] = True  # 模拟收尾 (persist 位)

    class _Sess:
        handle_user_input = fake_handle

    run = start_run("s_5", "q", _Sess(), object())
    await asyncio.wait_for(run.task, timeout=1)
    assert run.done is True
    assert events_seen["persisted"] is True
    # 缓冲含 run_start + 两个事件
    body = "".join(run.frames)
    assert "run_start" in body and "assistant_text_delta" in body
    assert "turn_complete" in body


@pytest.mark.asyncio
async def test_stop_run_cancels_and_marks_done():
    started = asyncio.Event()

    async def never_ending(self, text, llm=None):
        started.set()
        await asyncio.Event().wait()
        yield  # pragma: no cover

    class _Sess:
        handle_user_input = never_ending

    run = start_run("s_6", "q", _Sess(), object())
    await asyncio.wait_for(started.wait(), timeout=1)
    assert stop_run("s_6") is True
    # 任务被取消, finally 标记 done
    import contextlib
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(run.task, timeout=1)
    assert run.done is True
    assert get_run("s_6") is run
