# Web 前端 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为认知引擎加一个本地 Web 前端，实现舒适的流式聊天 + 每个 session 的知识图谱可视化 + 全局知识总览，与现有 TUI 共用引擎和存储。

**Architecture:** FastAPI 服务层（`src/explain_engine/web/`）薄包现有 `ChatSession`/`query_loop`/engine 函数/lexicon/theories，通过 REST + SSE 暴露；React+Vite+TS SPA（`frontend/`）消费，图谱用 Cytoscape.js+dagre。本地单用户，绑 127.0.0.1，无鉴权。聊天/动作走 SSE 单向流，HITL 自动策略。

**Tech Stack:** 后端 FastAPI + uvicorn + sse-starlette；前端 React + Vite + TypeScript + Cytoscape.js + cytoscape-dagre + @tanstack/react-query + react-router-dom + react-markdown。

**设计依据:** [docs/plans/2026-06-04-web-frontend-design.md](2026-06-04-web-frontend-design.md)

**通用约定（每个后端测试都适用）:**
- 运行测试加环境隔离 + 避开真实 PG：
  `EXPLAIN_EMBEDDING_DISABLED=1 EXPLAIN_DB_URL="postgresql://x:x@127.0.0.1:1/x" EXPLAIN_DB_CONNECT_TIMEOUT_S=1 .venv/bin/python -m pytest <path> -q`
- conftest 已自动隔离 `EXPLAIN_HOME` 到 tmp（`isolated_explain_home`）。
- LLM 一律 mock（`unittest.mock.AsyncMock`），不打真实 API。
- 质量门禁：后端 `ruff check` + `mypy`；前端 `npm run lint` + `tsc --noEmit`。
- 频繁提交：每个 Task 末尾 commit。提交信息中文，结尾带 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

---

# Phase A — 核心闭环（聊天 + session 图谱）

## Task A1：后端骨架 + `explain serve` + 健康检查

**Files:**
- Modify: `pyproject.toml`（加依赖）
- Create: `src/explain_engine/web/__init__.py`
- Create: `src/explain_engine/web/app.py`
- Modify: `src/explain_engine/cli.py`（加 `serve` 命令）
- Create: `tests/web/__init__.py`
- Test: `tests/web/test_app_health.py`

**Step 1: 加依赖**
在 `pyproject.toml` 的 `dependencies` 加：
```toml
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sse-starlette>=2.1",
```
运行 `uv sync` 安装。

**Step 2: 写失败测试** `tests/web/test_app_health.py`
```python
from fastapi.testclient import TestClient

from explain_engine.web.app import create_app


def test_health_ok():
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

**Step 3: 运行确认失败**
`pytest tests/web/test_app_health.py -q` → FAIL（模块不存在）。

**Step 4: 最小实现** `src/explain_engine/web/app.py`
```python
"""FastAPI 服务层 — 薄包现有引擎, 给 Web 前端用. 本地单用户."""
from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Explain Engine Web", version="0.1.0")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
```
`src/explain_engine/web/__init__.py` 留空；`tests/web/__init__.py` 留空。

**Step 5: 运行确认通过** `pytest tests/web/test_app_health.py -q` → PASS。

**Step 6: 加 `serve` CLI 命令** 在 `cli.py` 末尾（参照其它 `@app.command()`）：
```python
@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8800,
) -> None:
    """启动本地 Web 服务 (浏览器访问 http://127.0.0.1:8800)."""
    import uvicorn

    from explain_engine.web.app import create_app

    uvicorn.run(create_app(), host=host, port=port)
```

**Step 7: ruff + mypy + commit**
```bash
.venv/bin/ruff check src/explain_engine/web src/explain_engine/cli.py
.venv/bin/mypy src/explain_engine/web/app.py
git add pyproject.toml uv.lock src/explain_engine/web tests/web src/explain_engine/cli.py
git commit -m "web · Task A1: FastAPI 骨架 + explain serve + 健康检查"
```

---

## Task A2：图谱序列化器（ExplanationGraph → Cytoscape elements）

**Files:**
- Create: `src/explain_engine/web/serializers.py`
- Test: `tests/web/test_serializers.py`

**Step 1: 写失败测试** `tests/web/test_serializers.py`
```python
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.web.serializers import graph_to_cytoscape


def _graph() -> ExplanationGraph:
    g = ExplanationGraph(root_question="为什么")
    g.add_node(VariableNode(id="p_001", name="房价", description="d",
        abstraction_level=0, confidence=0.9, epistemic="observation"))
    g.add_node(VariableNode(id="c_001", name="不确定性", description="d",
        abstraction_level=1, confidence=0.8, epistemic="insight"))
    g.add_edge(RelationEdge(id="e1", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7, mechanism_description="m"))
    return g


def test_graph_to_cytoscape_shape():
    out = graph_to_cytoscape(_graph())
    assert out["root_question"] == "为什么"
    ids = {n["data"]["id"] for n in out["elements"]["nodes"]}
    assert ids == {"p_001", "c_001"}
    edge = out["elements"]["edges"][0]["data"]
    assert edge["source"] == "c_001" and edge["target"] == "p_001"
    assert edge["relation"] == "manifests_as"
    node = next(n["data"] for n in out["elements"]["nodes"] if n["data"]["id"] == "c_001")
    assert node["level"] == 1 and node["epistemic"] == "insight"
```

**Step 2: 运行确认失败** `pytest tests/web/test_serializers.py -q` → FAIL。

**Step 3: 实现** `src/explain_engine/web/serializers.py`
```python
"""把引擎数据结构转成前端 (Cytoscape) 友好的 JSON."""
from __future__ import annotations

from typing import Any

from explain_engine.schema.graph import ExplanationGraph


def graph_to_cytoscape(graph: ExplanationGraph) -> dict[str, Any]:
    """ExplanationGraph → Cytoscape elements (nodes/edges)."""
    nodes = []
    for n in graph.nodes.values():
        nodes.append({"data": {
            "id": n.id,
            "label": n.name,
            "level": n.abstraction_level,
            "epistemic": n.epistemic,
            "confidence": n.confidence,
            "description": n.description,
            "lifecycle": n.lifecycle_state,
            "activation": n.activation,
            "stability": n.stability,
        }})
    edges = []
    for e in graph.edges.values():
        edges.append({"data": {
            "id": e.id,
            "source": e.source_node,
            "target": e.target_node,
            "relation": e.relation_type,
            "confidence": e.confidence,
            "mechanism": e.mechanism_description,
        }})
    return {
        "root_question": graph.root_question,
        "elements": {"nodes": nodes, "edges": edges},
    }
```
> 注：`graph.nodes` / `graph.edges` 是只读 view（见 `schema/graph.py`）。

**Step 4: 运行确认通过** → PASS。

**Step 5: ruff + mypy + commit**
```bash
git add src/explain_engine/web/serializers.py tests/web/test_serializers.py
git commit -m "web · Task A2: 图谱 → Cytoscape 序列化器"
```

---

## Task A3：Session 列表 / 详情 / 图谱 / transcript 端点（只读）

**Files:**
- Create: `src/explain_engine/web/routes_sessions.py`
- Modify: `src/explain_engine/web/app.py`（include_router）
- Test: `tests/web/test_routes_sessions.py`

**测试夹具:** 复用 `tests/test_chat_session.py::_make_done_session(sid)` 造 session（sid 须匹配 `s_[0-9a-f]{8}`）。

**Step 1: 写失败测试** `tests/web/test_routes_sessions.py`
```python
from fastapi.testclient import TestClient

from explain_engine.web.app import create_app
from tests.test_chat_session import _make_done_session


def _client():
    return TestClient(create_app())


def test_list_sessions_empty():
    resp = _client().get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_and_get_session():
    _make_done_session("s_a1b2c3d4")
    c = _client()
    lst = c.get("/api/sessions").json()
    assert any(s["sid"] == "s_a1b2c3d4" for s in lst)
    detail = c.get("/api/sessions/s_a1b2c3d4")
    assert detail.status_code == 200
    assert detail.json()["sid"] == "s_a1b2c3d4"


def test_get_graph():
    _make_done_session("s_a1b2c3d5")
    resp = _client().get("/api/sessions/s_a1b2c3d5/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert "elements" in body and "nodes" in body["elements"]


def test_get_missing_session_404():
    resp = _client().get("/api/sessions/s_00000000")
    assert resp.status_code == 404
```

**Step 2: 运行确认失败** → FAIL。

**Step 3: 实现** `src/explain_engine/web/routes_sessions.py`
```python
"""Session 只读端点."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from explain_engine.chat.session import ChatSession
from explain_engine.persistence.session import SessionStore
from explain_engine.web.serializers import graph_to_cytoscape

router = APIRouter(prefix="/api/sessions")


def _load_chat(sid: str) -> ChatSession:
    try:
        return ChatSession(sid)
    except Exception as exc:  # 不存在 / 损坏
        raise HTTPException(status_code=404, detail=f"session {sid} 不可用: {exc}") from exc


@router.get("")
async def list_sessions() -> list[dict[str, Any]]:
    store = SessionStore()
    out = []
    for meta in store.list():  # 按现有 SessionStore API 调整
        out.append({
            "sid": meta.session_id,
            "question": meta.question,
            "stage": meta.stage,
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
        })
    return out


@router.get("/{sid}")
async def get_session(sid: str) -> dict[str, Any]:
    chat = _load_chat(sid)
    g = chat.state.graph
    return {
        "sid": sid,
        "question": chat._session.meta.question,
        "stage": chat._session.meta.stage,
        "tick": getattr(chat.state, "tick", 0),
        "node_count": len(g.nodes),
        "edge_count": len(g.edges),
    }


@router.get("/{sid}/graph")
async def get_graph(sid: str) -> dict[str, Any]:
    chat = _load_chat(sid)
    return graph_to_cytoscape(chat.state.graph)


@router.get("/{sid}/transcript")
async def get_transcript(sid: str) -> list[dict[str, Any]]:
    chat = _load_chat(sid)
    return chat.transcript
```
> 实现期校准：`SessionStore().list()` 的真实返回类型 / 字段名（见 `persistence/session.py`），`SessionMeta` 字段名（question/stage/created_at/updated_at）。若 list 返回的是 meta 列表则直接用；若是 sid 列表则逐个 load meta。

在 `app.py` 的 `create_app` 内：
```python
    from explain_engine.web.routes_sessions import router as sessions_router
    app.include_router(sessions_router)
```

**Step 4: 运行确认通过** → PASS（4 个测试）。

**Step 5: ruff + mypy + commit**
```bash
git commit -am "web · Task A3: session 列表/详情/图谱/transcript 只读端点"
```

---

## Task A4：新建 session 端点（POST /api/sessions）

**Files:**
- Modify: `src/explain_engine/web/routes_sessions.py`
- Test: `tests/web/test_routes_sessions.py`（追加）

**Step 1: 写失败测试**（追加）
```python
def test_create_session():
    resp = _client().post("/api/sessions", json={"question": "为什么年轻人不消费"})
    assert resp.status_code == 201
    sid = resp.json()["sid"]
    assert sid.startswith("s_")
    # 新建后可在列表看到
    assert any(s["sid"] == sid for s in _client().get("/api/sessions").json())
```

**Step 2: 运行确认失败** → FAIL。

**Step 3: 实现** —— 复用现有创建逻辑（参照 `cli.py::_run_new` / `SessionStore.create`）。在 router 加：
```python
from pydantic import BaseModel

class CreateSessionBody(BaseModel):
    question: str

@router.post("", status_code=201)
async def create_session(body: CreateSessionBody) -> dict[str, str]:
    store = SessionStore()
    meta = store.create(body.question)  # 按现有 API 调整: 返回 meta 或 sid
    return {"sid": meta.session_id}
```
> 实现期校准：`SessionStore.create` 的真实签名与是否需要初始化空 graph / bootstrap。MVP 仅建空 session（聊天再 deepen/bootstrap），与 TUI `/deepen` 流程保持一致；若需要 bootstrap 现象，留作 Phase B 的可选项，避免在 POST 里打真实 LLM。

**Step 4-5: 通过 + commit**
```bash
git commit -am "web · Task A4: 新建 session 端点"
```

---

## Task A5：聊天 SSE 端点 + 事件映射 + per-sid 锁

**Files:**
- Create: `src/explain_engine/web/sse.py`（ChatEvent → SSE 映射 + 锁）
- Create: `src/explain_engine/web/routes_chat.py`
- Modify: `src/explain_engine/web/app.py`（include_router）
- Test: `tests/web/test_routes_chat.py`

**Step 1: 写失败测试** `tests/web/test_routes_chat.py`
```python
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from explain_engine.web.app import create_app


def test_chat_sse_streams_events(monkeypatch):
    # mock ChatSession.handle_user_input → 产出两个 ChatEvent
    from explain_engine.chat.session import ChatEvent, ChatSession

    async def fake_handle(self, text, llm=None):
        yield ChatEvent(type="assistant_text_delta", content="你好")
        yield ChatEvent(type="turn_complete", content=None)

    monkeypatch.setattr(ChatSession, "handle_user_input", fake_handle)
    monkeypatch.setattr(ChatSession, "__init__", lambda self, sid: None)
    monkeypatch.setattr("explain_engine.web.routes_chat.make_llm_client", lambda: AsyncMock())

    client = TestClient(create_app())
    with client.stream("POST", "/api/sessions/s_a1b2c3d6/chat",
                       json={"message": "在吗"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "assistant_text_delta" in body
    assert "你好" in body
    assert "turn_complete" in body
```

**Step 2: 运行确认失败** → FAIL。

**Step 3: 实现** `src/explain_engine/web/sse.py`
```python
"""ChatEvent → SSE 映射 + per-sid 串行锁."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from explain_engine.chat.session import ChatEvent

_locks: dict[str, asyncio.Lock] = {}


def sid_lock(sid: str) -> asyncio.Lock:
    """每 sid 一把锁, 串行化同 session 的改图操作 (防 graph.json 损坏)."""
    if sid not in _locks:
        _locks[sid] = asyncio.Lock()
    return _locks[sid]


def sse_pack(event: str, data: Any) -> str:
    """组 SSE 帧."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def chat_event_to_sse(ev: ChatEvent) -> str:
    return sse_pack(ev.type, {"content": ev.content, "metadata": ev.metadata})


async def stream_chat_events(
    events: AsyncIterator[ChatEvent],
) -> AsyncIterator[str]:
    try:
        async for ev in events:
            yield chat_event_to_sse(ev)
    except Exception as exc:  # 引擎异常 → error 事件, 不崩流
        yield sse_pack("error", {"content": f"{type(exc).__name__}: {exc}"})
```

`src/explain_engine/web/routes_chat.py`
```python
"""聊天 SSE 端点."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from explain_engine.chat.session import ChatSession
from explain_engine.config import make_llm_client
from explain_engine.web.sse import sid_lock, stream_chat_events

router = APIRouter(prefix="/api/sessions")


class ChatBody(BaseModel):
    message: str


@router.post("/{sid}/chat")
async def chat(sid: str, body: ChatBody) -> EventSourceResponse:
    llm = make_llm_client()

    async def gen():
        async with sid_lock(sid):
            chat_session = ChatSession(sid)
            events = chat_session.handle_user_input(body.message, llm)
            async for frame in stream_chat_events(events):
                # sse_starlette 期望 dict 或 str; 我们已组好整帧 → 用 raw
                yield frame

    return EventSourceResponse(gen(), media_type="text/event-stream")
```
> 注：若用 `sse_starlette` 的 `EventSourceResponse`，它会自己加 `data:` 前缀。两种做法二选一——要么用 `sse_starlette` 并 yield `{"event":..., "data":...}` dict（推荐，省手写组帧），要么用 starlette 原生 `StreamingResponse` + 自己 `sse_pack`。实现期统一一种；测试断言关键字段在响应体里即可。**推荐用 `StreamingResponse` + 手写 `sse_pack`**（与上面 sse.py 一致，最可控）：
```python
from starlette.responses import StreamingResponse
return StreamingResponse(gen(), media_type="text/event-stream")
```

在 `app.py` include `routes_chat.router`。

**Step 4: 运行确认通过** → PASS。

**Step 5: ruff + mypy + commit**
```bash
git commit -am "web · Task A5: 聊天 SSE 端点 + ChatEvent 映射 + per-sid 锁"
```

---

## Task A6：静态文件托管 + serve 接前端

**Files:**
- Modify: `src/explain_engine/web/app.py`
- Test: `tests/web/test_static_mount.py`

**Step 1: 写失败测试**
```python
def test_api_still_works_without_dist(tmp_path, monkeypatch):
    # dist 不存在时 API 仍可用, 不报错
    from explain_engine.web.app import create_app
    from fastapi.testclient import TestClient
    client = TestClient(create_app())
    assert client.get("/api/health").status_code == 200
```

**Step 2-4: 实现** 在 `create_app` 末尾（include router 之后）：
```python
    from pathlib import Path

    from starlette.staticfiles import StaticFiles

    dist = Path(__file__).parent.parent.parent.parent / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="static")
    # dist 不存在 → 仅 API; serve 命令里打印提示 (见下)
```
在 `cli.py::serve` 内、`uvicorn.run` 前加：
```python
    from pathlib import Path
    dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
    if not dist.is_dir():
        print("提示: 前端未构建; 开发模式请另开终端跑 `cd frontend && npm run dev`")
```
> 注意 `StaticFiles` 挂在 `/` 必须在所有 `/api` router 之后 include，避免吞掉 API 路由。

**Step 5: 通过 + commit**
```bash
git commit -am "web · Task A6: 静态托管 frontend/dist (缺省 API-only)"
```

---

## Task A7：前端脚手架（Vite + React + TS）

**Files:**
- Create: `frontend/`（`npm create vite@latest frontend -- --template react-ts`）
- Modify: `frontend/vite.config.ts`（/api 代理）
- Modify: `.gitignore`
- Create: `frontend/src/api/client.ts`

**Step 1: 脚手架**
```bash
cd /Users/jinziguan/Desktop/explain_everything
npm create vite@latest frontend -- --template react-ts
cd frontend && npm install
npm install cytoscape cytoscape-dagre @tanstack/react-query react-router-dom react-markdown
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom @types/cytoscape
```

**Step 2: Vite 代理** `frontend/vite.config.ts` 的 `defineConfig` 加：
```ts
  server: {
    proxy: { "/api": "http://127.0.0.1:8800" },
  },
  test: { environment: "jsdom", globals: true, setupFiles: "./src/test-setup.ts" },
```
`frontend/src/test-setup.ts`:
```ts
import "@testing-library/jest-dom";
```

**Step 3: .gitignore** 追加：
```
frontend/node_modules/
frontend/dist/
```

**Step 4: API client** `frontend/src/api/client.ts`
```ts
export interface SessionSummary {
  sid: string; question: string; stage: string;
  created_at: string; updated_at: string;
}
export async function listSessions(): Promise<SessionSummary[]> {
  const r = await fetch("/api/sessions");
  if (!r.ok) throw new Error(`listSessions ${r.status}`);
  return r.json();
}
export async function getGraph(sid: string) {
  const r = await fetch(`/api/sessions/${sid}/graph`);
  if (!r.ok) throw new Error(`getGraph ${r.status}`);
  return r.json();
}
export async function createSession(question: string): Promise<{ sid: string }> {
  const r = await fetch("/api/sessions", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!r.ok) throw new Error(`createSession ${r.status}`);
  return r.json();
}
```

**Step 5: 冒烟构建 + commit**
```bash
cd frontend && npm run build   # 确认能构建出 dist/
cd .. && git add frontend .gitignore && git commit -m "web · Task A7: 前端脚手架 (Vite+React+TS) + API client"
```
> `frontend/dist/` 已 gitignore，只提交源码与配置。

---

## Task A8：SSE 流式聊天 hook

**Files:**
- Create: `frontend/src/api/chatStream.ts`
- Test: `frontend/src/api/chatStream.test.ts`

**Step 1: 写失败测试**（mock fetch 返回一段 event-stream，断言回调收到解析后的事件）
```ts
import { describe, it, expect, vi } from "vitest";
import { streamChat } from "./chatStream";

function mockStreamResponse(text: string): Response {
  const body = new ReadableStream({
    start(c) { c.enqueue(new TextEncoder().encode(text)); c.close(); },
  });
  return new Response(body, { status: 200 });
}

describe("streamChat", () => {
  it("parses SSE frames into events", async () => {
    const sse =
      'event: assistant_text_delta\ndata: {"content":"你好"}\n\n' +
      'event: turn_complete\ndata: {"content":null}\n\n';
    vi.spyOn(global, "fetch").mockResolvedValue(mockStreamResponse(sse));
    const got: string[] = [];
    await streamChat("s_x", "hi", (ev) => got.push(ev.event));
    expect(got).toEqual(["assistant_text_delta", "turn_complete"]);
  });
});
```

**Step 2: 运行确认失败** `cd frontend && npx vitest run src/api/chatStream.test.ts` → FAIL。

**Step 3: 实现** `frontend/src/api/chatStream.ts`
```ts
export interface SSEEvent { event: string; data: { content: unknown; metadata?: unknown } }

export async function streamChat(
  sid: string, message: string,
  onEvent: (ev: SSEEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`/api/sessions/${sid}/chat`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }), signal,
  });
  if (!resp.body) throw new Error("no stream body");
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
      const ev: Partial<SSEEvent> = {};
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) ev.event = line.slice(7);
        else if (line.startsWith("data: ")) ev.data = JSON.parse(line.slice(6));
      }
      if (ev.event) onEvent(ev as SSEEvent);
    }
  }
}
```

**Step 4: 通过 + commit**
```bash
cd frontend && npx vitest run src/api/chatStream.test.ts
cd .. && git commit -am "web · Task A8: SSE 流式聊天 hook + 单测"
```

---

## Task A9：聊天面板组件

**Files:**
- Create: `frontend/src/components/ChatPanel.tsx`
- Test: `frontend/src/components/ChatPanel.test.tsx`

**Step 1: 写失败测试**（render，输入并提交，mock streamChat 推一段 delta，断言渲染出文本 + 停止按钮）—— 用 `vi.mock("../api/chatStream")`。

**Step 3: 实现要点**（`ChatPanel.tsx`）
- 状态：`messages`（user/assistant 列表）、`streaming`（bool）、`AbortController` ref。
- 提交：append user 消息 → 调 `streamChat(sid, text, onEvent, ctrl.signal)`；
  - `assistant_text_delta`：累加到当前 assistant 气泡（react-markdown 渲染）。
  - `thinking_delta`：累加到可折叠 thinking 区。
  - `tool_use`：渲染工具调用 chip + spinner；`tool_result`：撤 spinner。
  - `turn_complete`：结束本轮；触发父组件回调 `onTurnComplete()`（用于刷新图）。
  - `error`：渲染红色错误条。
- 停止按钮：`ctrl.abort()`，关流。

**Step 4-5: 通过 + commit**
```bash
git commit -am "web · Task A9: 聊天面板 (流式/markdown/thinking/工具chip/停止)"
```

---

## Task A10：知识图谱面板组件（Cytoscape + dagre）

**Files:**
- Create: `frontend/src/components/GraphPanel.tsx`
- Create: `frontend/src/components/NodeDrawer.tsx`
- Test: `frontend/src/components/GraphPanel.test.tsx`（mock getGraph，断言 cytoscape 收到 elements；可 mock cytoscape）

**Step 3: 实现要点**
- `useQuery(["graph", sid], () => getGraph(sid))` 拉数据；父组件在 turn_complete 后 `queryClient.invalidateQueries(["graph", sid])` 刷新。
- 初始化 cytoscape，注册 `cytoscape-dagre`，layout `{ name: "dagre", rankDir: "BT" }`（L0 在下、L2 在上）。
- 样式：节点按 `data(level)` 着色（0/1/2 三色），`data(lifecycle)=="decayed"` 降透明度；边按 `data(relation)` 区分线型，宽度 ∝ `data(confidence)`。
- 点击节点 → 打开 `NodeDrawer` 显示 label/level/epistemic/confidence/description/activation/stability。

**Step 4-5: 通过 + commit**
```bash
git commit -am "web · Task A10: 知识图谱面板 (Cytoscape+dagre) + 节点详情抽屉"
```

---

## Task A11：三栏布局 + 路由 + 串起 Phase A

**Files:**
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/components/SessionSidebar.tsx`
- Create: `frontend/src/pages/Workspace.tsx`

**Step 3: 实现要点**
- `App.tsx`：QueryClientProvider + BrowserRouter，路由 `/` → `Workspace`，`/knowledge` → 占位（Phase C）。
- `Workspace`：三栏 = `SessionSidebar`（列表/新建/切换/删除）+ `ChatPanel`（中）+ `GraphPanel`（右）。选中 sid 提升到 Workspace state；`ChatPanel.onTurnComplete` → invalidate graph query。
- `SessionSidebar`：`useQuery(["sessions"], listSessions)`；新建调 `createSession` 后 invalidate + 选中。

**Step 4: 端到端手测**
```bash
# 终端1: EXPLAIN_* 环境就绪后
.venv/bin/python -m explain serve
# 终端2:
cd frontend && npm run dev   # 打开 http://localhost:5173
```
确认：建 session → 聊天流式 → 右侧图谱刷新 → 点节点看详情。

**Step 5: commit**
```bash
git commit -am "web · Task A11: 三栏工作台布局 + 路由 + Phase A 闭环"
```

---

# Phase B — 引擎控制按钮

## Task B1：动作 SSE 端点（compress/run/expand/predict/counterfactual/rescore）

**Files:**
- Create: `src/explain_engine/web/routes_actions.py`
- Modify: `src/explain_engine/web/app.py`
- Test: `tests/web/test_routes_actions.py`

**设计:** 不整体复用 slash handler（其 `_spinner` 依赖 `chat.tui_app`）。改为：对每个动作，包一层 async 生成器，发 `status_start` → 调底层 engine 函数（或 slash handler 并捕获结果）→ 发 `done` + 摘要。所有同步重活（如 embedding、`runtime.run` 内的同步段）按需 `asyncio.to_thread`（参照 Phase 20.5 修法，防阻塞 event loop）。

**Step 1: 写失败测试**（mock 对应 engine 函数，断言 SSE 体含 `status_start` 与 `done`，且持久化被调用）。例：
```python
def test_compress_action_streams(monkeypatch):
    from explain_engine.chat.session import ChatSession
    monkeypatch.setattr(ChatSession, "__init__", lambda self, sid: None)
    # mock 内部 compress 逻辑...
    client = TestClient(create_app())
    with client.stream("POST", "/api/sessions/s_a1b2c3d7/actions/compress", json={}) as r:
        body = "".join(r.iter_text())
    assert "status_start" in body and "done" in body
```

**Step 3: 实现要点** `routes_actions.py`
```python
ACTIONS = {"compress", "run", "expand", "predict", "counterfactual", "rescore"}

@router.post("/{sid}/actions/{action}")
async def run_action(sid: str, action: str, body: dict = Body(default={})):
    if action not in ACTIONS:
        raise HTTPException(404, f"未知动作 {action}")
    llm = make_llm_client()
    async def gen():
        async with sid_lock(sid):
            chat = ChatSession(sid)
            yield sse_pack("status_start", {"content": f"执行 {action}..."})
            try:
                summary = await _dispatch_action(chat, action, body, llm)  # 调底层逻辑
                chat.persist()
                yield sse_pack("done", {"content": summary})
            except Exception as exc:
                yield sse_pack("error", {"content": f"{type(exc).__name__}: {exc}"})
    return StreamingResponse(gen(), media_type="text/event-stream")
```
`_dispatch_action` 内按 action 调对应引擎入口（compress→propose_candidates+score_all+flush；run→runtime.run；expand/predict/counterfactual/rescore 同理），同步重活 `await asyncio.to_thread(...)`。

**Step 4-5: 通过 + commit**

---

## Task B2：前端控制按钮 + 动作 SSE + 刷新图

**Files:**
- Modify: `frontend/src/components/GraphPanel.tsx`（加按钮组）
- Create: `frontend/src/api/actionStream.ts`（复用 chatStream 逻辑或抽公共解析）
- Test: 组件测试 mock actionStream

**实现要点:** 按钮组（Compress/Run/Expand/Predict/Counterfactual/Rescore）→ 调动作 SSE，显示进度，`done` 后 invalidate graph query 刷新。predict/counterfactual 需弹输入框收 intervention 文本。
commit。

---

# Phase C — 全局知识总览

## Task C1：knowledge/overview + theories 端点

**Files:**
- Create: `src/explain_engine/web/routes_knowledge.py`
- Modify: `app.py`
- Test: `tests/web/test_routes_knowledge.py`

**实现要点:**
- `GET /api/knowledge/overview`：session 数（SessionStore）、变量数（lexicon dispatcher: `get_top_n_vars` / 读 variables）、理论数（theory cache `get_active_theories`），+ top 变量列表 + theory 摘要列表。
- `GET /api/theories`：复用 `engines/theory/cache.get_active_theories`。
- `POST /api/theories/{id}/reject`：复用 cache `reject_theory`。
- 全部走 lexicon dispatcher（PG/JSON）；测试用 DB 覆盖 env + `EXPLAIN_EMBEDDING_DISABLED=1`，空知识返回空态。
TDD：先测空态（0 session → counts 0、列表空），再测有数据。commit。

## Task C2：knowledge/graph 端点（跨 session 知识图）

**Files:**
- Modify: `routes_knowledge.py`, `serializers.py`（加 `lexicon_to_cytoscape`）
- Test: 追加

**实现要点:** 节点=lexicon 变量（`data.reuse`=reuse_count 用于 size、`data.theme`=聚类用于颜色），边=同一 theory motif 内变量连接（或 theme 内共现）。theory 的 motif 节点/边加 `data.in_theory=true` 高亮。先 TDD 序列化器（纯函数，喂假 lexicon dict），再接端点。commit。

## Task C3：前端知识页（仪表盘 + 跨 session 图）

**Files:**
- Create: `frontend/src/pages/Knowledge.tsx`
- Create: `frontend/src/components/KnowledgeDashboard.tsx`（指标卡 + 变量表 + theory 卡片）
- 复用 `GraphPanel` 的 Cytoscape 渲染（抽成通用 `<CytoGraph elements layout />`）
- Test: 组件测试 mock 数据

**实现要点:** 路由 `/knowledge`；顶部 dashboard（调 overview）、下方 CytoGraph（调 knowledge/graph）；theory 卡片带 reject 按钮。导航在布局加 `/` ↔ `/knowledge` 切换。commit。

---

# 收尾

## Task D1：全量回归 + 文档

- 后端：`EXPLAIN_EMBEDDING_DISABLED=1 EXPLAIN_DB_URL=...:1/x ... pytest tests/web -q` 全绿；`ruff check src/explain_engine/web`；`mypy src/explain_engine/web`。
- 前端：`cd frontend && npx vitest run && npm run build && npx tsc --noEmit`。
- README 加「Web 前端」章节：`explain serve` + `cd frontend && npm run dev` 用法、`npm run build` 生产托管。
- commit。

---

## 实现注意事项汇总

1. **SSE 同步阻塞**：动作/聊天里任何同步 torch embedding 或重 CPU 段必须 `asyncio.to_thread`（参照已合入的 Phase 20.5 修法），否则单 event loop 被阻塞。
2. **per-sid 锁**：所有改图端点（chat + actions）走 `sid_lock(sid)`，防 `graph.json` 并发损坏。
3. **测试避坑**：后端测试必须带 DB 覆盖 env（`EXPLAIN_DB_URL=postgresql://x:x@127.0.0.1:1/x` + `EXPLAIN_DB_CONNECT_TIMEOUT_S=1`）+ `EXPLAIN_EMBEDDING_DISABLED=1`，否则连真实 PG 会挂（见 memory tui-runtest-env-pg-hang）。
4. **现有 API 校准**：`SessionStore.list/create`、`SessionMeta` 字段、`ChatSession` 构造与 `_session.meta`、lexicon dispatcher 的读取函数名，实现时以源码为准微调（计划已标注校准点）。
5. **YAGNI**：不做鉴权 / 图编辑 / HITL 弹窗 / 实时图更新 / provider 管理 UI / ephemeral（见设计 §10）。
