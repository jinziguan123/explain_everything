"""聊天端点测试 (解耦后: POST 启动 + GET 订阅 + POST 停止)。

跨请求的后台任务在 TestClient 下不易驱动 (每请求新 loop), 故:
- 路由层测 POST 启动/404/409、GET no_active_run、GET 回放 (预置 done run)、stop。
- 后台 ChatRun 的回放/续传/并发由 test_chat_runs.py 用 asyncio 单 loop 覆盖。
"""
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from explain_engine.web import chat_runs
from explain_engine.web.app import create_app
from explain_engine.web.sse import sse_pack


def _patch_session(monkeypatch):
    """stub ChatSession 加载 + handle_user_input + llm, 让 POST 不真跑引擎。"""
    from explain_engine.chat.session import ChatEvent, ChatSession

    async def fake_handle(self, text, llm=None):
        yield ChatEvent(type="assistant_text_delta", content="你好")
        yield ChatEvent(type="turn_complete", content=None)

    monkeypatch.setattr(ChatSession, "handle_user_input", fake_handle)
    monkeypatch.setattr(ChatSession, "__init__", lambda self, sid: None)
    monkeypatch.setattr(
        "explain_engine.web.routes_chat.make_llm_client", lambda: AsyncMock()
    )


def test_post_chat_starts_run(monkeypatch):
    _patch_session(monkeypatch)
    client = TestClient(create_app())
    r = client.post("/api/sessions/s_a1b2c3d6/chat", json={"message": "在吗"})
    assert r.status_code == 200
    assert r.json() == {"started": True}
    # 后台 run 已登记
    assert chat_runs.get_run("s_a1b2c3d6") is not None


def test_post_chat_conflict_when_run_active(monkeypatch):
    _patch_session(monkeypatch)
    # 预置一个"进行中" (未 finish) 的 run
    run = chat_runs.ChatRun("s_a1b2c3d7")
    chat_runs._runs["s_a1b2c3d7"] = run  # done=False
    client = TestClient(create_app())
    r = client.post("/api/sessions/s_a1b2c3d7/chat", json={"message": "x"})
    assert r.status_code == 409


def test_post_chat_missing_session_404():
    """缺失 session → 404 (load_chat_or_404)。"""
    client = TestClient(create_app())
    r = client.post("/api/sessions/s_00000000/chat", json={"message": "hi"})
    assert r.status_code == 404


def test_get_stream_no_active_run():
    client = TestClient(create_app())
    with client.stream("GET", "/api/sessions/s_a1b2c3d8/chat/stream") as resp:
        body = "".join(resp.iter_text())
    assert resp.status_code == 200
    assert "no_active_run" in body


def test_get_stream_done_run_is_no_active_run():
    """已完成的 run 不再回放 (以 transcript 为准), GET → no_active_run。

    防回归: 刷新后若重放已完成轮次, 会盖掉历史聊天内容。
    """
    run = chat_runs.ChatRun("s_a1b2c3d9")
    run.append(sse_pack("run_start", {"content": None}))
    run.append(sse_pack("turn_complete", {"content": None}))
    run.finish()
    chat_runs._runs["s_a1b2c3d9"] = run

    client = TestClient(create_app())
    with client.stream("GET", "/api/sessions/s_a1b2c3d9/chat/stream") as resp:
        body = "".join(resp.iter_text())
    assert "no_active_run" in body
    assert "turn_complete" not in body  # 不回放已完成轮


def test_stop_no_active_run_is_noop():
    client = TestClient(create_app())
    r = client.post("/api/sessions/s_a1b2c3da/chat/stop")
    assert r.status_code == 200
    assert r.json() == {"stopped": False}
