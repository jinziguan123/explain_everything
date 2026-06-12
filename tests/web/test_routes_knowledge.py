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


# ── Phase X1: 预测台账端点 ──────────────────────────────────


def _write_theory_cache() -> str:
    """往隔离 home 写一个含单理论的 theories.json, 返回理论 id。"""
    from explain_engine.engines.theory.cache import (
        TheoriesCache,
        _atomic_write_cache,
    )
    from explain_engine.engines.theory.theory import Theory
    from explain_engine.persistence.storage_v2 import StorageV2

    theory = Theory(
        id="t_aaaaaaaaaa", motif_type="chain", theme_ids=(),
        node_ids=("v_a", "v_b"), edges=(("v_a", "v_b", "causes"),),
        supporting_sessions=("s_1",), natural_language_summary="x",
        structure_complexity=2, first_seen_session="s_1",
        last_seen_session="s_1",
    )
    storage = StorageV2()
    _atomic_write_cache(
        TheoriesCache(tentative_theories=[theory]),
        storage.knowledge_dir() / "theories.json",
    )
    return theory.id


def test_predictions_empty() -> None:
    resp = _client().get("/api/predictions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_prediction_add_unknown_theory_404() -> None:
    resp = _client().post("/api/predictions", json={
        "theory_id": "t_nonexist00", "assertion": "某断言内容足够长",
    })
    assert resp.status_code == 404


def test_prediction_add_list_resolve_roundtrip() -> None:
    tid = _write_theory_cache()
    client = _client()

    added = client.post("/api/predictions", json={
        "theory_id": tid, "assertion": "未来半年某指标保持正增长",
        "method": "time_window", "deadline": "2026-12-01",
    })
    assert added.status_code == 200
    pid = added.json()["id"]
    assert added.json()["origin"] == "user"

    listed = client.get("/api/predictions").json()
    assert [p["id"] for p in listed] == [pid]
    assert listed[0]["due"] is False

    resolved = client.post(f"/api/predictions/{pid}/resolve", json={"hit": True})
    assert resolved.status_code == 200
    assert resolved.json()["prediction"]["status"] == "hit"
    assert resolved.json()["theory_stats"]["predictive_power"] == 1.0

    # 台账不可篡改: 重复结算 409
    again = client.post(f"/api/predictions/{pid}/resolve", json={"hit": False})
    assert again.status_code == 409


def test_prediction_resolve_unknown_404() -> None:
    resp = _client().post("/api/predictions/p_999/resolve", json={"hit": True})
    assert resp.status_code == 404


def test_prediction_add_time_window_requires_deadline() -> None:
    tid = _write_theory_cache()
    resp = _client().post("/api/predictions", json={
        "theory_id": tid, "assertion": "缺期限的时间窗断言示例",
        "method": "time_window",
    })
    assert resp.status_code == 422
