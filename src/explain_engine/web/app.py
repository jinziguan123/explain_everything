"""FastAPI 服务层 — 薄包现有引擎, 给 Web 前端用. 本地单用户."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Explain Engine Web", version="0.1.0")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    from explain_engine.web.routes_sessions import router as sessions_router
    app.include_router(sessions_router)

    from explain_engine.web.routes_chat import router as chat_router
    app.include_router(chat_router)

    # 托管前端构建产物 (frontend/dist). 必须在所有 /api router 之后挂在 "/",
    # 否则 catch-all 会吞掉 API 路由。dist 不存在 (未构建) → 仅 API, serve 命令提示。
    _mount_frontend(app)

    return app


def _frontend_dist() -> Path:
    # app.py → web → explain_engine → src → <repo root>/frontend/dist
    return Path(__file__).resolve().parents[3] / "frontend" / "dist"


def _mount_frontend(app: FastAPI) -> None:
    from starlette.staticfiles import StaticFiles

    dist = _frontend_dist()
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="static")
