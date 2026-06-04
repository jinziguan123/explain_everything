"""Task A3: session 只读端点测试 (list / get / graph / transcript / 404)."""
from fastapi.testclient import TestClient

from explain_engine.web.app import create_app
from tests.test_chat_session import _make_done_session


def _client() -> TestClient:
    return TestClient(create_app())


def test_list_sessions_empty() -> None:
    resp = _client().get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_and_get_session() -> None:
    _make_done_session("s_a1b2c3d4")
    c = _client()
    lst = c.get("/api/sessions").json()
    assert any(s["sid"] == "s_a1b2c3d4" for s in lst)
    detail = c.get("/api/sessions/s_a1b2c3d4")
    assert detail.status_code == 200
    assert detail.json()["sid"] == "s_a1b2c3d4"


def test_get_graph() -> None:
    _make_done_session("s_a1b2c3d5")
    resp = _client().get("/api/sessions/s_a1b2c3d5/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert "elements" in body and "nodes" in body["elements"]


def test_get_transcript() -> None:
    _make_done_session("s_a1b2c3d8")
    resp = _client().get("/api/sessions/s_a1b2c3d8/transcript")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_missing_session_404() -> None:
    resp = _client().get("/api/sessions/s_00000000")
    assert resp.status_code == 404
