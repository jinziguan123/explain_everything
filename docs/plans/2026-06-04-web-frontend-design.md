# Web 前端设计 — 舒适聊天 + 知识图谱可视化

> 日期：2026-06-04 · 状态：已批准，待出实现计划
> 关联：[业务架构报告](../业务架构设计报告.md) · [技术架构报告](../技术架构设计报告.md)

## 1. 目标与动机

为现有「认知引擎」（Python 后端 + Textual TUI）增加一个 **Web 前端**，让用户：

1. **更舒适地与 AI 聊天**（流式、Markdown、thinking 折叠、工具调用可见、停止按钮等）；
2. **可视化看到每个 session 的知识图谱**（L0/L1/L2 因果 DAG）；
3. **可视化看到全局知识情况**（跨 session 的 lexicon 变量 + theories）。

定位：**互补**，不替代 TUI。Web 与 TUI **共用同一后端引擎与 `~/.explain` 存储**，TUI 作为 power-user 入口保留。

## 2. 已确认决策

| 维度 | 决策 |
|------|------|
| 定位 | 互补（Web + TUI 共存，共用引擎与存储） |
| 部署 | 本地单用户，绑 `127.0.0.1`，无鉴权 |
| 前端栈 | React + Vite + TypeScript |
| 后端栈 | FastAPI（uvicorn），新增 `explain serve` 命令 |
| 图谱库 | Cytoscape.js + dagre 分层布局 |
| 交互深度 | 聊天驱动 + 显式引擎控制按钮；图谱**只读**（不编辑） |
| 全局知识 | 仪表盘（指标+列表）+ 跨 session 知识图谱 |
| 流式/HITL | SSE 单向流 + HITL 自动策略（只读工具 & user_explicit 自动通过，不阻塞） |

## 3. 整体架构

```
现有: Python 引擎 ── Textual TUI (保留)
                  └── ~/.explain/projects/<id>/  (sessions + knowledge)
新增:             └── FastAPI 服务层 (src/explain_engine/web/) ──SSE/REST──> React SPA (frontend/)
```

- 新增 `src/explain_engine/web/`（FastAPI 后端）+ 仓库根 `frontend/`（React+Vite+TS）。
- 新 CLI：`explain serve --host 127.0.0.1 --port 8800`。
- **完全复用现有引擎**，不重写：
  - `StorageV2` / `SessionStore`：列 / 读 / 删 session
  - `ChatSession` + `chat/loop.py::query_loop`：聊天主循环（产出 ChatEvent）
  - `engines/*` + `chat/slash_commands.py` 动作逻辑：compress/run/expand/predict/counterfactual/rescore
  - `engines/lexicon.py`（PG/JSON dispatcher）+ `engines/theory/cache.py`：全局知识
  - `schema/graph.py::ExplanationGraph.to_dict()`：图谱序列化
  - `config.make_llm_client` / `make_light_llm_client`：LLM
- **进程/状态模型**：服务常驻；LLM client、BGE-M3 embedder 进程单例；每个聊天/动作请求按 sid 从存储加载 `ChatSession` → 执行一轮 → 引擎自动持久化。
- **并发安全**：按 sid 维护 `asyncio.Lock`，串行化同一 session 的改图操作，防 `graph.json` 损坏。单用户场景锁竞争极少。
- **前端托管**：生产环境 FastAPI 用 `StaticFiles` 托管 Vite 构建产物 `frontend/dist/`；开发用 Vite dev server 代理 `/api` → FastAPI。`dist/` 不存在时 API-only 并打印提示。

## 4. 后端 API

### 4.1 Session（只读为主）
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sessions` | 列表：sid / 问题 / stage / created / updated / 节点数 |
| POST | `/api/sessions` | 新建持久化 session（body: `{question}`） |
| GET | `/api/sessions/{sid}` | 元信息 + stage + budget |
| GET | `/api/sessions/{sid}/transcript` | 聊天历史（前端重水合） |
| GET | `/api/sessions/{sid}/graph` | Cytoscape elements：节点(L0/L1/L2、confidence、epistemic、mechanism、fitness、lifecycle、weak 标记) + 边(类型 causes/manifests_as、confidence、机制) |
| DELETE | `/api/sessions/{sid}` | 删除 |

### 4.2 聊天（SSE）
- `POST /api/sessions/{sid}/chat`，body `{message}` → `text/event-stream`
- SSE 事件类型（直接映射现有 `ChatEvent`）：
  `assistant_text_delta` / `thinking_delta` / `tool_use` / `tool_result` / `status_start` / `status_end` / `turn_complete` / `budget_exhausted` / `error`
- 取消：客户端断开 SSE → 后端 cancel 当前 turn task（复用 escape-cancel 语义）。

### 4.3 动作（SSE 进度 + 最终结果）
- `POST /api/sessions/{sid}/actions/{action}`，`action ∈ {compress, run, expand, predict, counterfactual, rescore}`
- body 视动作而定（如 run 的 budget、predict/counterfactual 的 intervention 文本）。
- 复用 slash handler / engine 函数，通过 SSE 推 `status_*` 进度事件，结束推 `done` + 摘要。

### 4.4 全局知识
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/knowledge/overview` | 指标（session 数 / 变量数 / stable+tentative 理论数）+ 列表（top 变量按 reuse、theory 摘要） |
| GET | `/api/knowledge/graph` | 跨 session 知识图：节点=lexicon 变量（size=reuse_count，color=theme 聚类），theory motif 高亮叠加 |
| GET | `/api/theories` | 理论列表（类型/模式/覆盖/准确度/状态） |
| POST | `/api/theories/{id}/reject` | 拒绝理论（后续不 bootstrap inject） |

> 全部沿用 lexicon dispatcher：PG 可达走 PG，否则 JSON fallback；embedding 不可用时知识图优雅降级为空态/仅列表。

## 5. 前端结构（React + Vite + TS）

### 路由 / 页面
- **`/`（聊天工作台）**：三栏
  - 左：session 侧栏（列表、新建、切换、删除、stage/budget 标记）
  - 中：聊天区（流式 token、Markdown/代码块、thinking 折叠、工具调用 chip + spinner、停止按钮、budget 显示、输入框）
  - 右：该 session 知识图谱（Cytoscape，dagre 分层 L2→L1→L0）+ 控制按钮组（Compress/Run/Expand/Predict/Counterfactual/Rescore）；每轮/动作完成后刷新
- **`/knowledge`（全局知识）**：顶部仪表盘（指标卡 + 变量表 + theory 卡片）+ 下方跨 session 知识图谱（Cytoscape）
- **节点详情抽屉**：点击任一图谱节点 → 名称 / 层级 / 置信度 / epistemic / 机制 / fitness（及变量的 reuse、source sessions）

### 选型
- 服务态：React Query（缓存 + 失效刷新）
- 图谱：Cytoscape.js + cytoscape-dagre
- Markdown：react-markdown（+ 代码高亮）
- SSE：fetch + ReadableStream（手动解析 event-stream，便于带 POST body 与取消）
- 样式：轻量（Tailwind 或 CSS Modules，实现期定）

### 颜色/样式约定
- 节点按 abstraction_level 着色（L0 现象 / L1 模式 / L2 深层原因），epistemic 用边框/图标区分；decayed 节点淡化。
- 边按类型区分（causes 实线、manifests_as 虚线），粗细=confidence。
- 知识图：节点 size=reuse_count，color=theme 聚类；stable theory 的 motif 节点/边高亮。

## 6. 数据流

**一轮聊天**
1. 用户发消息 → `POST /chat`（SSE）。
2. 后端：按 sid 取锁 → 加载 ChatSession → `query_loop(message, llm)` async-for ChatEvent → 翻译为 SSE → flush。
3. `turn_complete` 后引擎持久化，关闭流。
4. 前端实时追加 delta；`turn_complete` 后重拉 `/graph` 更新右侧面板。

**一个动作（如 compress）**
1. 点按钮 → `POST /actions/compress`（SSE 进度）。
2. 后端跑 handler 逻辑 → 推 `status_*` → 持久化 → 推 `done` + 摘要。
3. 前端显示进度，完成后刷新 `/graph`。

**图谱加载**：`GET /graph` 返回 Cytoscape elements（含全部字段，节点详情抽屉复用同一 payload）。

## 7. 错误处理

- per-sid `asyncio.Lock` 防并发改图。
- 引擎异常 → SSE `error` 事件（友好中文文案，复用 chat_copy 风格），不致命崩流。
- PG / embedding 不可用 → 知识端点降级（JSON fallback / 空态），与现有 lexicon dispatcher 一致。
- 前端：错误 toast；空态（无 session、空图、无知识沉淀）；停止按钮取消进行中的 turn。

## 8. 测试策略

- **后端**：pytest + FastAPI `TestClient`。mock LLM（沿用现有 `AsyncMock` 模式），conftest 的 `EXPLAIN_HOME` tmp 隔离 + **DB 覆盖 env 避开真实 PG**（见 memory：`EXPLAIN_DB_URL=postgresql://x:x@127.0.0.1:1/x`）。覆盖：SSE 事件映射、图序列化、各动作端点、知识端点、并发锁。
- **前端**：Vitest + React Testing Library 测组件（聊天流式渲染、图面板 mock 数据渲染、节点抽屉）。E2E（Playwright）后置，不入 MVP。
- **质量门禁**：后端 ruff + mypy；前端 eslint + tsc。

## 9. 打包 / 开发流

- 运行：`explain serve`（uvicorn 起 FastAPI，托管 `frontend/dist/`）。
- 前端开发：`cd frontend && npm run dev`（Vite 代理 `/api` → `:8800`）。
- 新增依赖：后端 `fastapi`、`uvicorn[standard]`、`sse-starlette`（或手写 `StreamingResponse`）；前端 `package.json`（react、vite、typescript、cytoscape、cytoscape-dagre、@tanstack/react-query、react-router-dom、react-markdown 等）。
- `.gitignore` 增 `frontend/node_modules/`、`frontend/dist/`。

## 10. 范围边界（YAGNI — MVP 砍掉）

- 无鉴权、无多用户。
- 图谱**只读**（不做节点/理论的图上编辑；理论 reject 走按钮端点）。
- HITL **自动策略**，不做确认弹窗（后置）。
- 图谱不做流式中**实时**更新（按轮/动作后刷新）。
- LLM provider 管理 UI 后置（暂用 TUI `/llm` 或 config）。
- Web 聚焦**持久化 session**；ephemeral 轻聊后置。

## 11. 分期落地

- **Phase A（核心闭环）**：后端 sessions / graph / chat-SSE + React 聊天工作台 + session 图谱面板。
- **Phase B（控制）**：动作控制按钮 + SSE 进度。
- **Phase C（全局知识）**：知识仪表盘 + 跨 session 知识图谱 + theory 列表/reject。

## 12. 风险与备注

- **TUI 真终端测试本地不可跑**（.env 真实 PG 致 `run_test` 挂死）；Web 后端测试用 TestClient + DB 覆盖，规避此坑。
- 大图性能：Cytoscape + dagre 对数百节点可接受；跨 session 知识图若过大，加 top-N / 按 theme 折叠（实现期按需）。
- chat 引擎的 HITL/取消在 Web 下走自动策略 + SSE 断开取消，需在实现期确认 `query_loop` 的 cancel 行为与 textual 一致。
