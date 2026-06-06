"""A6: 静态托管. dist 不存在时 API 仍可用; 存在时托管 index.html."""
from fastapi.testclient import TestClient

from explain_engine.web.app import create_app


def test_api_works_without_dist():
    # frontend/dist 通常未构建 (gitignore) → 仅 API, 不报错
    client = TestClient(create_app())
    assert client.get("/api/health").status_code == 200


def test_serves_index_when_dist_exists(tmp_path, monkeypatch):
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    monkeypatch.setattr("explain_engine.web.app._frontend_dist", lambda: dist)

    client = TestClient(create_app())
    # 根路径返回 SPA index.html
    resp = client.get("/")
    assert resp.status_code == 200
    assert "hi" in resp.text
    # API 仍优先 (挂载在 router 之后)
    assert client.get("/api/health").json() == {"status": "ok"}


def test_index_html_no_cache(tmp_path, monkeypatch):
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    monkeypatch.setattr("explain_engine.web.app._frontend_dist", lambda: dist)

    resp = TestClient(create_app()).get("/")
    assert resp.status_code == 200
    assert "no-cache" in resp.headers.get("cache-control", "")
