"""FastAPI 服务层 — 薄包现有引擎, 给 Web 前端用. 本地单用户."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI


def _pg_reachable(timeout: float = 1.5) -> bool:
    """快速 TCP 探测 EXPLAIN_DB_URL 的 host:port 是否可连.

    端口拒绝/无路由是瞬时或 timeout 内返回, 避免直接开连接池触发后台 worker
    无限重连刷屏 + verify_connection 的 ~10s PoolTimeout 卡顿 (web 启动体验)。
    解析失败/无法判断 → 返 True (交给 init_lexicon_backend 正常路径处理)。
    """
    import os
    import socket
    from urllib.parse import urlparse

    dsn = os.environ.get("EXPLAIN_DB_URL", "postgresql://explain:changeme@127.0.0.1:5432/explain")
    try:
        parsed = urlparse(dsn)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 5432
    except Exception:
        return True
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # best-effort: 初始化 lexicon backend (PG 可达则反映 PG, 否则 JSON fallback)。
    # 先做快速 TCP 探测: PG 端口不可达就直接走 JSON, 不开连接池 —— 避免 psycopg
    # 池后台 worker 刷屏 + 10s PoolTimeout 卡顿。PG 不可达不致命 (dispatcher 默认
    # JSON, variables.json)。探测通过才走完整 init; init 仍失败则关池兜底。
    import logging

    try:
        if _pg_reachable():
            from explain_engine.engines.lexicon import init_lexicon_backend
            active = await init_lexicon_backend()
            if not active:
                from explain_engine.persistence.lexicon_pg import close_async_pool
                await close_async_pool()
        else:
            logging.getLogger("explain_engine.web").info(
                "lexicon backend: PG 端口不可达 (TCP 探测), 走本机 JSON。"
                "如需全局知识数据, 先起 PG 再重启 serve。"
            )
    except Exception:
        pass
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Explain Engine Web", version="0.1.0", lifespan=_lifespan)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    from explain_engine.web.routes_sessions import router as sessions_router
    app.include_router(sessions_router)

    from explain_engine.web.routes_chat import router as chat_router
    app.include_router(chat_router)

    from explain_engine.web.routes_knowledge import router as knowledge_router
    app.include_router(knowledge_router)

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
