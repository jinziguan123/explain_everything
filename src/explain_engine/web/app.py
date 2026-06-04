"""FastAPI 服务层 — 薄包现有引擎, 给 Web 前端用. 本地单用户."""
from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Explain Engine Web", version="0.1.0")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    from explain_engine.web.routes_sessions import router as sessions_router
    app.include_router(sessions_router)

    return app
