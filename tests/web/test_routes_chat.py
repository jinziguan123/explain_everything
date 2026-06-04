from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from explain_engine.web.app import create_app


def test_chat_sse_streams_events(monkeypatch):
    from explain_engine.chat.session import ChatEvent, ChatSession

    async def fake_handle(self, text, llm=None):
        yield ChatEvent(type="assistant_text_delta", content="你好")
        yield ChatEvent(type="turn_complete", content=None)

    monkeypatch.setattr(ChatSession, "handle_user_input", fake_handle)
    monkeypatch.setattr(ChatSession, "__init__", lambda self, sid: None)
    monkeypatch.setattr(
        "explain_engine.web.routes_chat.make_llm_client", lambda: AsyncMock()
    )

    client = TestClient(create_app())
    with client.stream(
        "POST", "/api/sessions/s_a1b2c3d6/chat", json={"message": "在吗"}
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "assistant_text_delta" in body
    assert "你好" in body
    assert "turn_complete" in body


def test_chat_sse_error_event(monkeypatch):
    """引擎抛异常 → SSE error 事件, 不 500 崩流."""
    from explain_engine.chat.session import ChatSession

    async def boom(self, text, llm=None):
        raise RuntimeError("引擎炸了")
        yield  # make it an async generator

    monkeypatch.setattr(ChatSession, "handle_user_input", boom)
    monkeypatch.setattr(ChatSession, "__init__", lambda self, sid: None)
    monkeypatch.setattr(
        "explain_engine.web.routes_chat.make_llm_client", lambda: AsyncMock()
    )
    client = TestClient(create_app())
    with client.stream(
        "POST", "/api/sessions/s_a1b2c3d6/chat", json={"message": "hi"}
    ) as resp:
        body = "".join(resp.iter_text())
    assert "error" in body


def test_chat_missing_session_404():
    """缺失 session → 出 200 流之前转 404 (对齐 A3/A4 的 _load_chat 404 模式)."""
    client = TestClient(create_app())
    resp = client.post("/api/sessions/s_00000000/chat", json={"message": "hi"})
    assert resp.status_code == 404
