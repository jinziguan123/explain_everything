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


def test_create_session() -> None:
    resp = _client().post("/api/sessions", json={"question": "为什么年轻人不消费"})
    assert resp.status_code == 201
    sid = resp.json()["sid"]
    assert sid.startswith("s_")
    # 新建后可在列表看到
    assert any(s["sid"] == sid for s in _client().get("/api/sessions").json())


def test_invalid_sid_404():
    """M-4: 非 s_<8hex> 形态的 sid → 404 (防 path-traversal, defense-in-depth)."""
    c = _client()
    assert c.get("/api/sessions/not-a-sid").status_code == 404
    assert c.get("/api/sessions/s_zzzz/graph").status_code == 404


def test_delete_session():
    _make_done_session("s_a1b2c3e0")
    c = _client()
    assert any(s["sid"] == "s_a1b2c3e0" for s in c.get("/api/sessions").json())
    assert c.delete("/api/sessions/s_a1b2c3e0").status_code == 204
    # 删后列表不再含它
    assert not any(s["sid"] == "s_a1b2c3e0" for s in c.get("/api/sessions").json())


def test_delete_missing_session_404():
    assert _client().delete("/api/sessions/s_00000000").status_code == 404


def test_delete_invalid_sid_404():
    assert _client().delete("/api/sessions/not-a-sid").status_code == 404
