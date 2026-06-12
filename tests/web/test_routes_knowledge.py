"""Task C1: 全局知识端点测试 (overview / theories list / reject).

空 home + embedding disabled + 坏 DB → lexicon JSON fallback (空 variables.json),
theory cache 冷启动 (空)。全部确定性。
"""
from fastapi.testclient import TestClient

from explain_engine.web.app import create_app
from tests.test_chat_session import _make_done_session


def _client() -> TestClient:
    return TestClient(create_app())


def test_overview_empty() -> None:
    resp = _client().get("/api/knowledge/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["session_count"], int)
    assert body["session_count"] >= 0
    assert body["variable_count"] == 0
    # Phase T: theory_count 加 weakened; overview 加 H3 复用率
    assert body["theory_count"] == {"stable": 0, "tentative": 0, "weakened": 0}
    assert body["h3_reuse"] == {"vars_reused": 0, "reuse_rate": 0.0}
    assert body["top_variables"] == []
    assert body["theories"] == []


def test_overview_counts_sessions() -> None:
    _make_done_session("s_a1b2c301")
    _make_done_session("s_a1b2c302")
    body = _client().get("/api/knowledge/overview").json()
    assert body["session_count"] >= 2


def test_theories_list_empty() -> None:
    resp = _client().get("/api/theories")
    assert resp.status_code == 200
    assert resp.json() == []


def test_reject_unknown_theory() -> None:
    resp = _client().post("/api/theories/nonexistent/reject")
    assert resp.status_code == 200
    assert resp.json() == {"rejected": False}


def test_knowledge_graph_empty() -> None:
    resp = _client().get("/api/knowledge/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["elements"]["nodes"] == []
    assert body["elements"]["edges"] == []
