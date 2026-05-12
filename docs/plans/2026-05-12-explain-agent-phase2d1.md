# Explain Agent · Phase 2.D-1 实施计划（connection_explorer + Tavily + 永久快照）

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把 Phase 2.A-2.C 形成的 6 维归因架构扩展为"6 维核心 + 跨议题延伸 + 永久可追溯证据"——独立 connection_explorer 节点提出 ≤3 延伸议题，Tavily web search 接入三个时效敏感维度 + connection_explorer，本地磁盘 SnapshotStore 让所有新爬新闻和 web 结果都有不会失效的快照。

**Architecture:** main_graph 拓扑加一个节点 `dynamic_sub → connection_explorer → report`。新增 `SnapshotStore`（磁盘读写 + MySQL 指针表）+ `WebSearchAdapter`（Tavily 封装）两个组件。framework YAML 在 policy/sentiment_event/international 三个维度的 data_sources 末尾加 `web_search`。所有数据流仍走 `Evidence` 单一抽象，新增 `ConnectionThread` state 字段记录延伸思考。

**Tech Stack:** Python 3.11+、`tavily-python`（新增）、`trafilatura`（新增）、Phase 2.A-2.C 已有的 langgraph / pydantic / sqlalchemy / qdrant / rich / pytest-asyncio。

**Phase 2.D-1 不做的：**
- fan_out 性能优化（推到 Phase 2.D-2）
- Lazy News Ingest（推到 Phase 2.D-2）
- 历史 178 条 news_corpus 回填 snapshot
- web 抓取 HTML（仅存 Tavily 返回的清洗后正文）
- citation.url 失效时的 UI 兜底逻辑（snapshot 数据先备好，未来用）

**前置依赖：**
- Phase 2.C 完工（commit `b8b41c9`）
- Phase 2.D-1 设计文档已就绪（commit `858e6b0`，`docs/plans/2026-05-12-explain-agent-phase2d1-design.md`）
- `.env` 凭证齐全，全部 93 个单测通过
- 用户拿到 Tavily API key（注册 https://tavily.com 拿免费 key，写入 `.env` 的 `TAVILY_API_KEY`）

---

## 任务总览

| # | 任务 | 预计时长 |
|---|---|---|
| 1 | 添加 tavily-python + trafilatura 依赖 + Settings 字段 | 25 min |
| 2 | SnapshotStore 实现 + 单测 | 50 min |
| 3 | State schema · ConnectionThread + connection_threads | 20 min |
| 4 | WebSearchAdapter（Tavily 封装）+ 单测 | 60 min |
| 5 | ingest pipeline 集成 SnapshotStore | 50 min |
| 6 | framework YAML 三个维度加 web_search | 15 min |
| 7 | connection_explorer 节点实现 + 单测 | 90 min |
| 8 | main_graph 接入 connection_explorer | 30 min |
| 9 | report_builder 追加延伸思考段落 | 30 min |
| 10 | persist 节点 tree_json 补 connection_threads | 20 min |
| 11 | main_graph 集成测试更新 + REPL 适配 + smoke 验收 | 60 min |

**合计：约 8 小时纯开发，预计 1-2 个工作日完成。**

---

## Task 1: 添加 tavily-python + trafilatura 依赖 + Settings 字段

**目标：** 装两个新依赖，在 `Settings` 中加 `tavily_api_key` 字段（可选，None 时禁用 web_search）。

**Files:**
- Modify: `pyproject.toml`（uv 改）
- Modify: `src/explain_agent/config.py`
- Modify: `.env`（用户自己加 `TAVILY_API_KEY=tvly-xxx`，本任务不动）

**Step 1: 添加依赖**

Run:
```bash
uv add tavily-python trafilatura
```
Expected: 装上两个包，pyproject.toml 和 uv.lock 都被改。

**Step 2: 验证 import**

Run:
```bash
uv run python -c "from tavily import TavilyClient; import trafilatura; print('OK')"
```
Expected: 打印 `OK`。

**Step 3: 在 Settings 中加 tavily_api_key 字段**

打开 `src/explain_agent/config.py`，在 `qdrant_api_key` 旁边新增：

```python
class Settings(BaseSettings):
    # ...
    qdrant_api_key: str | None = None
    tavily_api_key: str | None = None  # 新增, Phase 2.D-1 web search 用
    # ...
```

**Step 4: 跑全量测试确认无回归**

Run: `uv run pytest`
Expected: 93 passed（同 Phase 2.C 终态）。

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/explain_agent/config.py
git commit -m "$(cat <<'EOF'
新增 tavily-python + trafilatura 依赖与 tavily_api_key Settings 字段

为 Phase 2.D-1 web search 与永久快照做准备。tavily_api_key 默认 None
（禁用 web_search），用户在 .env 设置即可启用。
EOF
)"
```

---

## Task 2: SnapshotStore 实现

**目标：** `SnapshotStore.save(content, content_type) -> snapshot_id` 把正文写入 `{SNAPSHOT_DIR}/{yyyy/mm/dd}/{snapshot_id}.txt` 并 INSERT `explain_snapshot_blob`；`load(snapshot_id) -> str | None` 读回。

**Files:**
- Create: `src/explain_agent/storage/__init__.py`（空）
- Create: `src/explain_agent/storage/snapshot.py`
- Create: `tests/test_snapshot_store.py`

**Step 1: 创建目录骨架**

Run:
```bash
mkdir -p src/explain_agent/storage
touch src/explain_agent/storage/__init__.py
```

**Step 2: 写单测**

`tests/test_snapshot_store.py`：

```python
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from explain_agent.storage.snapshot import SnapshotStore


def test_save_writes_file_and_returns_id(tmp_path):
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    store = SnapshotStore(base_dir=tmp_path, engine=mock_engine)
    snap_id = store.save("正文内容测试", content_type="news")

    # 返回 id 形如 "snap_xxxxx"
    assert snap_id.startswith("snap_")

    # 文件落到 yyyy/mm/dd 子目录
    today = datetime.now()
    expected = tmp_path / f"{today.year:04d}" / f"{today.month:02d}" / f"{today.day:02d}" / f"{snap_id}.txt"
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == "正文内容测试"

    # 写了 explain_snapshot_blob
    mock_conn.exec_driver_sql.assert_called_once()
    call = mock_conn.exec_driver_sql.call_args
    assert "explain_snapshot_blob" in call.args[0]
    params = call.args[1]
    assert params[0] == snap_id
    assert params[1] == "news"
    assert str(expected) in params[2]


def test_load_reads_existing_snapshot(tmp_path):
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    store = SnapshotStore(base_dir=tmp_path, engine=mock_engine)
    snap_id = store.save("hello", content_type="news")

    out = store.load(snap_id)
    assert out == "hello"


def test_load_returns_none_when_missing(tmp_path):
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    store = SnapshotStore(base_dir=tmp_path, engine=mock_engine)
    # 直接构造一个 fake id
    out = store.load("snap_nonexistent")
    assert out is None


def test_save_handles_empty_content(tmp_path):
    """空内容不写文件，返回 None。"""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    store = SnapshotStore(base_dir=tmp_path, engine=mock_engine)
    snap_id = store.save("", content_type="news")
    assert snap_id is None
    mock_conn.exec_driver_sql.assert_not_called()
```

**Step 3: 跑测试确认失败**

Run: `uv run pytest tests/test_snapshot_store.py -v`
Expected: ImportError（snapshot.py 不存在）。

**Step 4: 实现 SnapshotStore**

`src/explain_agent/storage/snapshot.py`：

```python
from datetime import datetime
from pathlib import Path
from uuid import uuid4


class SnapshotStore:
    """把网页正文落到本地磁盘 + MySQL 指针，让 citation 链接失效后仍能追溯。"""

    def __init__(self, base_dir, engine):
        self.base_dir = Path(base_dir)
        self.engine = engine

    def save(self, content: str, content_type: str = "news") -> str | None:
        """落正文到 {base_dir}/{yyyy/mm/dd}/{snap_id}.txt + INSERT blob 表。
        返回 snapshot_id；content 为空时返回 None 不落盘。
        """
        if not content:
            return None

        snap_id = f"snap_{uuid4().hex[:16]}"
        now = datetime.now()
        sub_dir = self.base_dir / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"
        sub_dir.mkdir(parents=True, exist_ok=True)
        path = sub_dir / f"{snap_id}.txt"
        path.write_text(content, encoding="utf-8")
        size = path.stat().st_size

        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                """
                INSERT INTO explain_agent.explain_snapshot_blob
                  (snapshot_id, content_type, storage_path, size_bytes, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (snap_id, content_type, str(path), size, now),
            )
        return snap_id

    def load(self, snapshot_id: str) -> str | None:
        """根据 snapshot_id 读回正文，不存在返回 None。"""
        if not snapshot_id:
            return None
        # 遍历 base_dir 查找（按日期分目录，直接搜文件名）
        for path in self.base_dir.rglob(f"{snapshot_id}.txt"):
            return path.read_text(encoding="utf-8")
        return None
```

**Step 5: 跑测试**

Run: `uv run pytest tests/test_snapshot_store.py -v`
Expected: 4 passed。

**Step 6: Commit**

```bash
git add src/explain_agent/storage/ tests/test_snapshot_store.py
git commit -m "$(cat <<'EOF'
新增 SnapshotStore（本地磁盘 + MySQL 指针）

save(content, content_type) → snap_id：写 {base_dir}/{yyyy/mm/dd}/
{snap_id}.txt + INSERT explain_snapshot_blob。空内容返回 None 不落盘。
load(snap_id) → str | None：按日期分目录 rglob 查找。
EOF
)"
```

---

## Task 3: State schema · ConnectionThread

**目标：** 加 `ConnectionThread` TypedDict + `connection_threads` 字段；`new_attribution_state` 默认值含 `connection_threads: []`。

**Files:**
- Modify: `src/explain_agent/graph/state.py`
- Modify: `tests/test_graph_state.py`

**Step 1: 写测试**

在 `tests/test_graph_state.py` 末尾追加：

```python
def test_connection_thread_typeddict():
    from explain_agent.graph.state import ConnectionThread
    t: ConnectionThread = {
        "title": "美国 BIS 制裁 HBM 进展",
        "hypothesis": "证据反复出现 HBM 但未被 6 维覆盖",
        "content": "本周 BIS 发布...",
        "evidence_ids": ["e1"],
        "source": "web",
        "confidence": 4,
    }
    assert t["source"] == "web"
    assert t["confidence"] == 4


def test_new_state_has_connection_threads():
    s = new_attribution_state(raw_question="test")
    assert s["connection_threads"] == []
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_graph_state.py -v`
Expected: FAIL（ConnectionThread 不存在 + new_state 没该字段）。

**Step 3: 改 state.py**

打开 `src/explain_agent/graph/state.py`，在 `NarrativeClaim` 之后新增：

```python
class ConnectionThread(TypedDict):
    title: str
    hypothesis: str
    content: str
    evidence_ids: list[str]
    source: Literal["local", "web", "mixed"]
    confidence: int  # 1-5
```

在 `AttributionState` 中追加字段（`unverified_drops` 旁边）：

```python
class AttributionState(TypedDict, total=False):
    # ...已有
    unverified_drops: list[str]
    connection_threads: list[ConnectionThread]  # 新增
    # ...
```

`new_attribution_state` 默认值字典加：

```python
"unverified_drops": [],
"connection_threads": [],  # 新增
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_graph_state.py -v`
Expected: 8 passed（6 老的 + 2 新的）。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/state.py tests/test_graph_state.py
git commit -m "$(cat <<'EOF'
State schema · 加 ConnectionThread + connection_threads

为 connection_explorer 节点做准备。ConnectionThread 含 title /
hypothesis / content / evidence_ids / source（local/web/mixed）/
confidence（1-5）。default 值在 new_attribution_state 中初始化为 []。
EOF
)"
```

---

## Task 4: WebSearchAdapter（Tavily 封装）

**目标：** 实现 `WebSearchAdapter.query(q)` 返回 `list[Evidence]`，每条调 `snapshot_store.save` 落盘并填 `snapshot_id`。

**Files:**
- Create: `src/explain_agent/adapters/web_search.py`
- Create: `tests/test_web_search_adapter.py`

**Step 1: 写测试**

`tests/test_web_search_adapter.py`：

```python
from datetime import date
from unittest.mock import MagicMock

import pytest

from explain_agent.adapters.web_search import WebSearchAdapter
from explain_agent.core.types import AdapterQuery


@pytest.mark.asyncio
async def test_query_calls_tavily_and_returns_evidence():
    fake_tavily = MagicMock()
    fake_tavily.search.return_value = {
        "results": [
            {
                "url": "https://a.com/news/1",
                "title": "标题 1",
                "content": "正文内容 1",
                "score": 0.9,
            },
            {
                "url": "https://b.com/news/2",
                "title": "标题 2",
                "content": "正文内容 2",
                "score": 0.85,
            },
        ],
    }
    fake_store = MagicMock()
    fake_store.save.side_effect = ["snap_001", "snap_002"]

    adapter = WebSearchAdapter(tavily_client=fake_tavily, snapshot_store=fake_store)
    q = AdapterQuery(
        keywords=["半导体", "制裁"],
        time_window=(date(2026, 5, 5), date(2026, 5, 12)),
        target="半导体",
        limit=5,
    )
    out = await adapter.query(q)

    assert len(out) == 2
    assert out[0].source == "web_search"
    assert out[0].source_type == "news"
    assert out[0].url == "https://a.com/news/1"
    assert out[0].snippet == "正文内容 1"
    assert out[0].snapshot_id == "snap_001"
    fake_tavily.search.assert_called_once()
    assert fake_store.save.call_count == 2


@pytest.mark.asyncio
async def test_query_handles_tavily_failure():
    fake_tavily = MagicMock()
    fake_tavily.search.side_effect = RuntimeError("api error")
    fake_store = MagicMock()

    adapter = WebSearchAdapter(tavily_client=fake_tavily, snapshot_store=fake_store)
    q = AdapterQuery(
        keywords=["x"],
        time_window=(date(2026, 5, 5), date(2026, 5, 12)),
        target="x",
    )
    out = await adapter.query(q)
    assert out == []


@pytest.mark.asyncio
async def test_query_returns_empty_when_tavily_empty():
    fake_tavily = MagicMock()
    fake_tavily.search.return_value = {"results": []}
    fake_store = MagicMock()

    adapter = WebSearchAdapter(tavily_client=fake_tavily, snapshot_store=fake_store)
    q = AdapterQuery(
        keywords=["x"],
        time_window=(date(2026, 5, 5), date(2026, 5, 12)),
        target="x",
    )
    out = await adapter.query(q)
    assert out == []
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_web_search_adapter.py -v`
Expected: ImportError。

**Step 3: 实现 WebSearchAdapter**

`src/explain_agent/adapters/web_search.py`：

```python
from datetime import datetime
from uuid import uuid4

from explain_agent.core.types import AdapterQuery, Evidence


class WebSearchAdapter:
    name = "web_search"

    def __init__(self, tavily_client, snapshot_store, max_results: int = 5):
        self.tavily = tavily_client
        self.snapshot_store = snapshot_store
        self.max_results = max_results

    async def query(self, q: AdapterQuery) -> list[Evidence]:
        query_text = " ".join([q.target] + q.keywords)
        max_n = min(q.limit, self.max_results) if q.limit else self.max_results
        try:
            resp = self.tavily.search(
                query=query_text,
                max_results=max_n,
                search_depth="basic",
            )
        except Exception:
            return []
        results = (resp or {}).get("results") or []
        out: list[Evidence] = []
        now = datetime.now()
        for item in results:
            content = item.get("content") or ""
            url = item.get("url")
            title = item.get("title")
            snap_id = None
            try:
                snap_id = self.snapshot_store.save(content, content_type="web_search")
            except Exception:
                snap_id = None
            out.append(
                Evidence(
                    id=str(uuid4()),
                    source=self.name,
                    source_type="news",
                    url=url,
                    title=title,
                    snippet=content,
                    snapshot_id=snap_id,
                    timestamp=now,
                    metadata={
                        "target": q.target,
                        "score": item.get("score"),
                        "query": query_text,
                    },
                )
            )
        return out
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_web_search_adapter.py -v`
Expected: 3 passed。

**Step 5: Commit**

```bash
git add src/explain_agent/adapters/web_search.py tests/test_web_search_adapter.py
git commit -m "$(cat <<'EOF'
新增 WebSearchAdapter（Tavily 封装 + 自动 snapshot）

query(q) → 调 tavily.search, 每条结果立刻过 SnapshotStore.save 拿
snapshot_id, 然后包装成 Evidence(source="web_search",
source_type="news")。Tavily API 失败或返回空都返回 []，不阻塞 worker。
EOF
)"
```

---

## Task 5: ingest pipeline 集成 SnapshotStore

**目标：** `NewsIndexer.index()` 在 `INSERT explain_news_corpus` 之前先 `trafilatura` 提取正文 + SnapshotStore 落盘，把 `snapshot_id` 写进 corpus 表。

**Files:**
- Modify: `src/explain_agent/ingest/news_indexer.py`
- Modify: `tests/test_news_indexer.py`

**Step 1: 写测试**

读 `tests/test_news_indexer.py` 看现有结构。在末尾追加：

```python
def test_index_writes_snapshot_id(monkeypatch):
    """index() 调用时为每条 NewsItem 生成 snapshot_id 并填入 explain_news_corpus。"""
    from datetime import datetime
    from unittest.mock import MagicMock
    from explain_agent.ingest.news_indexer import NewsIndexer
    from explain_agent.ingest.news_crawler import NewsItem

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn
    mock_conn.exec_driver_sql.return_value.fetchall.return_value = []  # _filter_existing

    mock_tagger = MagicMock()
    mock_tagger.tag.return_value = {"industries": ["半导体"], "concepts": []}
    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [[0.1] * 1024]
    mock_qdrant = MagicMock()

    mock_store = MagicMock()
    mock_store.save.return_value = "snap_abc"

    indexer = NewsIndexer(
        engine=mock_engine, tagger=mock_tagger,
        embedder=mock_embedder, qdrant=mock_qdrant,
        snapshot_store=mock_store,
    )
    item = NewsItem(
        news_id="n1", url_hash="h1", source="test",
        url="http://a.com", title="测试标题",
        content="<html><body><p>测试正文</p></body></html>",
        published_at=datetime(2026, 5, 10, 9, 0),
    )
    n = indexer.index([item])
    assert n == 1

    # save 被调
    mock_store.save.assert_called_once()
    saved_content = mock_store.save.call_args.args[0]
    assert "测试正文" in saved_content  # trafilatura 应抽到正文

    # 查 INSERT 调用是否含 snap_abc
    insert_calls = [c for c in mock_conn.exec_driver_sql.call_args_list
                    if "INSERT INTO explain_agent.explain_news_corpus" in c.args[0]]
    assert insert_calls, "应该 INSERT explain_news_corpus"
    insert_params = insert_calls[0].args[1]
    assert "snap_abc" in insert_params
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_news_indexer.py::test_index_writes_snapshot_id -v`
Expected: FAIL（NewsIndexer 没 `snapshot_store` 参数 + INSERT 没 `snapshot_id` 列）。

**Step 3: 改 NewsIndexer**

修改 `src/explain_agent/ingest/news_indexer.py`：

```python
import json
import trafilatura
from qdrant_client.http.models import PointStruct
from sqlalchemy.engine import Engine
from explain_agent.ingest.news_crawler import NewsItem
from explain_agent.ingest.tagger import NewsTagger
from explain_agent.embedding.bge_m3 import BGEM3Embedder


class NewsIndexer:
    def __init__(
        self,
        engine: Engine,
        tagger: NewsTagger,
        embedder: BGEM3Embedder,
        qdrant,
        snapshot_store=None,
        collection: str = "news_v1",
    ):
        self.engine = engine
        self.tagger = tagger
        self.embedder = embedder
        self.qdrant = qdrant
        self.snapshot_store = snapshot_store
        self.collection = collection

    def _filter_existing(self, items: list[NewsItem]) -> list[NewsItem]:
        # 保持不动
        if not items:
            return []
        hashes = [i.url_hash for i in items]
        placeholders = ",".join(["%s"] * len(hashes))
        with self.engine.begin() as conn:
            rows = conn.exec_driver_sql(
                f"SELECT url_hash FROM explain_agent.explain_news_corpus WHERE url_hash IN ({placeholders})",
                tuple(hashes),
            ).fetchall()
        existing = {r[0] for r in rows}
        return [i for i in items if i.url_hash not in existing]

    def _make_snapshot(self, content: str) -> str | None:
        """trafilatura 提取正文 + SnapshotStore 落盘，返回 snapshot_id。"""
        if self.snapshot_store is None or not content:
            return None
        try:
            extracted = trafilatura.extract(content) or content
        except Exception:
            extracted = content
        try:
            return self.snapshot_store.save(extracted, content_type="news")
        except Exception:
            return None

    def index(self, items: list[NewsItem]) -> int:
        items = self._filter_existing(items)
        if not items:
            return 0

        tags = [self.tagger.tag(i.title, i.content) for i in items]
        texts = [f"{i.title}。{i.content[:1500]}" for i in items]
        vectors = self.embedder.embed(texts)
        snap_ids = [self._make_snapshot(i.content) for i in items]

        with self.engine.begin() as conn:
            for item, tag, snap_id in zip(items, tags, snap_ids):
                conn.exec_driver_sql(
                    """
                    INSERT INTO explain_agent.explain_news_corpus
                      (news_id, url_hash, source, url, title, content, published_at, tags, snapshot_id, is_indexed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        item.news_id,
                        item.url_hash,
                        item.source,
                        item.url,
                        item.title,
                        item.content,
                        item.published_at,
                        json.dumps(tag, ensure_ascii=False),
                        snap_id,
                    ),
                )

        points = [
            PointStruct(
                id=item.news_id,
                vector=vec,
                payload={
                    "news_id": item.news_id,
                    "source": item.source,
                    "title": item.title,
                    "published_at": item.published_at.isoformat(),
                    "tags": tag.get("industries", []) + tag.get("concepts", []),
                    "url": item.url,
                    "snapshot_id": snap_id,
                },
            )
            for item, vec, tag, snap_id in zip(items, vectors, tags, snap_ids)
        ]
        self.qdrant.upsert(collection_name=self.collection, points=points)
        return len(items)
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_news_indexer.py -v`
Expected: 全部通过（既有 + 新的）。

**Step 5: Commit**

```bash
git add src/explain_agent/ingest/news_indexer.py tests/test_news_indexer.py
git commit -m "$(cat <<'EOF'
ingest pipeline · 新闻采集时生成 snapshot 落盘

NewsIndexer 新增可选 snapshot_store 参数；每条 NewsItem 入库前先用
trafilatura 抽取正文 → SnapshotStore.save → snapshot_id 写入
explain_news_corpus.snapshot_id 字段 + Qdrant payload。store 缺失
或抽取失败时 snapshot_id 仍为 None，不阻塞 ingest。
EOF
)"
```

---

## Task 6: framework YAML 三个维度加 web_search

**目标：** `frameworks/cn_equity_sector_attribution.yaml` 的 policy / sentiment_event / international 三个维度的 `data_sources` 末尾追加 `web_search`。

**Files:**
- Modify: `frameworks/cn_equity_sector_attribution.yaml`
- Create: `tests/test_framework_web_search.py`

**Step 1: 写测试**

`tests/test_framework_web_search.py`：

```python
from explain_agent.graph.framework_loader import load_framework


def test_policy_dim_includes_web_search():
    fw = load_framework("cn_equity_sector_attribution")
    policy = next(d for d in fw["dimensions"] if d["id"] == "policy")
    assert "web_search" in policy["data_sources"]


def test_sentiment_event_dim_includes_web_search():
    fw = load_framework("cn_equity_sector_attribution")
    se = next(d for d in fw["dimensions"] if d["id"] == "sentiment_event")
    assert "web_search" in se["data_sources"]


def test_international_dim_includes_web_search():
    fw = load_framework("cn_equity_sector_attribution")
    intl = next(d for d in fw["dimensions"] if d["id"] == "international")
    assert "web_search" in intl["data_sources"]


def test_industry_chain_dim_NOT_include_web_search():
    fw = load_framework("cn_equity_sector_attribution")
    ic = next(d for d in fw["dimensions"] if d["id"] == "industry_chain")
    assert "web_search" not in ic["data_sources"]
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_framework_web_search.py -v`
Expected: FAIL（3 个白名单测试），1 通过（industry_chain 不含 web_search）。

**Step 3: 改 YAML**

打开 `frameworks/cn_equity_sector_attribution.yaml`，找到 policy 维度，把 `data_sources` 改成：

```yaml
  - id: policy
    name: 政策/宏观
    priority: 1
    data_sources:
      - news_corpus
      - web_search
    query_template: "最近 {time_window} 内与 {target} 相关的政策、监管表态、高层讲话"
    # ...其余不动
```

同样改 sentiment_event 和 international：

```yaml
  - id: sentiment_event
    data_sources:
      - news_corpus
      - web_search
    # ...

  - id: international
    data_sources:
      - news_corpus
      - web_search
    # ...
```

industry_chain / capital_flow / technical 不动。

**Step 4: 跑测试**

Run: `uv run pytest tests/test_framework_web_search.py -v`
Expected: 4 passed。

**Step 5: Commit**

```bash
git add frameworks/cn_equity_sector_attribution.yaml tests/test_framework_web_search.py
git commit -m "$(cat <<'EOF'
framework YAML · policy / sentiment_event / international 加 web_search

三个时效敏感维度的 data_sources 末尾追加 web_search，使 DimensionWorker
在本地 Qdrant 检索后可以补充 Tavily web search。industry_chain /
capital_flow / technical 保持本地纯净（这三个用结构化数据更可靠）。
EOF
)"
```

---

## Task 7: connection_explorer 节点实现

**目标：** 实现 `connection_explorer_node`：强模型提议 ≤3 个延伸议题 → 收敛门控（overlap_with_main_dims / confidence<3 砍） → 对每个保留议题决定走 web_search 或 news_corpus 检索 → 强模型基于结果写 100-200 字 thread.content。

**Files:**
- Create: `src/explain_agent/graph/nodes/connection_explorer.py`
- Create: `tests/test_node_connection_explorer.py`

**Step 1: 写单测**

`tests/test_node_connection_explorer.py`：

```python
import json
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
import pytest

from explain_agent.core.types import Evidence
from explain_agent.graph.nodes.connection_explorer import connection_explorer_node
from explain_agent.graph.state import new_attribution_state, DimensionResult


def make_ev(id: str, snippet: str = "snip") -> Evidence:
    return Evidence(
        id=id, source="x", source_type="news",
        snippet=snippet, timestamp=datetime.now(),
    )


@pytest.mark.asyncio
async def test_proposes_threads_and_runs_local_search():
    """强模型提议 1 个本地议题，触发本地检索。"""
    fake_llm = MagicMock()
    # 第 1 次: 提议 JSON
    # 第 2 次: 给本地议题写 content
    fake_llm.chat.side_effect = [
        json.dumps({
            "threads": [
                {
                    "title": "存储芯片产能扩张",
                    "hypothesis": "证据反复提到存储但未被 6 维深入分析",
                    "need_web_search": False,
                    "confidence": 4,
                    "overlap_with_main_dims": False,
                    "query_keywords": ["存储芯片", "产能"],
                },
            ],
        }),
        "存储芯片产能扩张主要表现在 [e2] ...",
    ]
    fake_news_adapter = MagicMock()
    fake_news_adapter.query = AsyncMock(return_value=[make_ev("e2", "存储扩产新闻")])
    fake_web_adapter = MagicMock()
    fake_web_adapter.query = AsyncMock(return_value=[])

    state = new_attribution_state("test")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", "政策证据")], mini_summary="",
            retry_count=1, no_data=False, confidence="high",
        ),
    }
    state["dimension_reports"] = {"policy": "政策报告"}

    out = await connection_explorer_node(
        state, llm=fake_llm,
        adapter_registry={"news_corpus": fake_news_adapter, "web_search": fake_web_adapter},
    )
    assert len(out["connection_threads"]) == 1
    t = out["connection_threads"][0]
    assert t["title"] == "存储芯片产能扩张"
    assert t["source"] == "local"
    assert "e2" in t["evidence_ids"]
    fake_news_adapter.query.assert_called_once()
    fake_web_adapter.query.assert_not_called()


@pytest.mark.asyncio
async def test_filters_low_confidence_and_overlap():
    """confidence<3 与 overlap_with_main_dims=True 的议题被砍。"""
    fake_llm = MagicMock()
    fake_llm.chat.side_effect = [
        json.dumps({
            "threads": [
                {"title": "A", "hypothesis": "", "need_web_search": False,
                 "confidence": 2, "overlap_with_main_dims": False, "query_keywords": []},
                {"title": "B", "hypothesis": "", "need_web_search": False,
                 "confidence": 5, "overlap_with_main_dims": True, "query_keywords": []},
                {"title": "C", "hypothesis": "", "need_web_search": False,
                 "confidence": 4, "overlap_with_main_dims": False, "query_keywords": ["c"]},
            ],
        }),
        "C 议题回答",
    ]
    fake_news_adapter = MagicMock()
    fake_news_adapter.query = AsyncMock(return_value=[make_ev("ec")])
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["dimension_results"] = {}

    out = await connection_explorer_node(
        state, llm=fake_llm,
        adapter_registry={"news_corpus": fake_news_adapter, "web_search": MagicMock()},
    )
    titles = [t["title"] for t in out["connection_threads"]]
    assert titles == ["C"]


@pytest.mark.asyncio
async def test_triggers_web_search_when_flagged():
    fake_llm = MagicMock()
    fake_llm.chat.side_effect = [
        json.dumps({
            "threads": [
                {"title": "BIS 制裁", "hypothesis": "需要最新外部新闻",
                 "need_web_search": True, "confidence": 5,
                 "overlap_with_main_dims": False, "query_keywords": ["BIS", "HBM"]},
            ],
        }),
        "BIS 制裁最新进展 [ew1] ...",
    ]
    fake_news = MagicMock()
    fake_news.query = AsyncMock(return_value=[])
    fake_web = MagicMock()
    fake_web.query = AsyncMock(return_value=[make_ev("ew1", "tavily 返回")])

    state = new_attribution_state("test")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["dimension_results"] = {}

    out = await connection_explorer_node(
        state, llm=fake_llm,
        adapter_registry={"news_corpus": fake_news, "web_search": fake_web},
    )
    assert len(out["connection_threads"]) == 1
    t = out["connection_threads"][0]
    assert t["source"] == "web"
    assert "ew1" in t["evidence_ids"]
    fake_web.query.assert_called_once()


@pytest.mark.asyncio
async def test_caps_at_three_threads():
    fake_llm = MagicMock()
    # 模型给 5 个，应该只保留前 3 个高 confidence 的
    threads_proposal = {
        "threads": [
            {"title": f"T{i}", "hypothesis": "", "need_web_search": False,
             "confidence": 5, "overlap_with_main_dims": False, "query_keywords": []}
            for i in range(5)
        ]
    }
    fake_llm.chat.side_effect = [json.dumps(threads_proposal)] + ["回答"] * 3
    fake_news = MagicMock()
    fake_news.query = AsyncMock(return_value=[make_ev("e")])

    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["dimension_results"] = {}

    out = await connection_explorer_node(
        state, llm=fake_llm,
        adapter_registry={"news_corpus": fake_news, "web_search": MagicMock()},
    )
    assert len(out["connection_threads"]) == 3


@pytest.mark.asyncio
async def test_returns_empty_when_json_invalid():
    fake_llm = MagicMock()
    fake_llm.chat.return_value = "not json"
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["dimension_results"] = {}

    out = await connection_explorer_node(
        state, llm=fake_llm,
        adapter_registry={"news_corpus": MagicMock(), "web_search": MagicMock()},
    )
    assert out["connection_threads"] == []
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_node_connection_explorer.py -v`
Expected: ImportError。

**Step 3: 实现节点**

`src/explain_agent/graph/nodes/connection_explorer.py`：

```python
import json
import re
from typing import Any

from explain_agent.core.types import AdapterQuery
from explain_agent.graph.state import AttributionState, ConnectionThread
from explain_agent.llm import LLMClient


PROPOSE_SYSTEM = """你是金融归因 agent 的延伸思考器。基于 6 维核心归因和现有证据池，
提议 ≤3 个值得探索的"延伸议题"——指反复出现但未被 6 维覆盖的实体/事件/趋势，
或与本议题强相关的跨学科 / 跨地域 / 跨时间的对照案例。

输出 JSON:
{
  "threads": [
    {
      "title": "议题标题(15-30 字)",
      "hypothesis": "为什么这个议题值得延伸",
      "need_web_search": true|false,
      "confidence": 1-5,
      "overlap_with_main_dims": true|false,
      "query_keywords": ["关键词1", "关键词2"]
    }
  ]
}

规则:
- ≤3 个，宁可少不可多
- need_web_search=true 适用于"最新政策/制裁/事件/海外信号"等需要 5 天内时效信息的议题
- need_web_search=false 适用于"产业链结构/历史类比/概念延伸"等本地新闻语料能覆盖的议题
- overlap_with_main_dims=true 表示与 6 维核心报告中已论述的内容重复，自己标 True 让上层砍掉
- confidence 是对"该议题真的能延伸出有价值信息"的自评

只输出 JSON。
"""


ANSWER_SYSTEM = """你是延伸议题的回答器。基于给定证据写一段 100-200 字的回答。

要求:
- 用 [e_xxx] 格式标注引用的 evidence_id
- 不出现未在证据中出现的数据
- 不预测/不推荐操作
- 如果证据不足以回答，直接说"现有证据不足以展开此延伸"

直接输出文本。
"""


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def connection_explorer_node(
    state: AttributionState,
    llm: LLMClient,
    adapter_registry: dict[str, Any],
) -> dict:
    # 1. 强模型提议延伸议题
    dim_reports = state.get("dimension_reports", {})
    dim_results = state.get("dimension_results", {})
    evidence_dump = []
    for dim_id, r in dim_results.items():
        for e in r["evidence"][:5]:
            evidence_dump.append({"dim": dim_id, "id": e.id, "snippet": e.snippet[:200]})

    user = (
        f"target: {state.get('target')}\n"
        f"time_window: {state.get('time_window')}\n"
        f"6 维报告: {json.dumps(dim_reports, ensure_ascii=False)}\n"
        f"证据池(节选): {json.dumps(evidence_dump, ensure_ascii=False)}"
    )
    try:
        raw = llm.chat(system=PROPOSE_SYSTEM, user=user, max_tokens=2000)
    except Exception:
        return {"connection_threads": []}
    data = _extract_json(raw)
    if not data or "threads" not in data:
        return {"connection_threads": []}

    # 2. 过滤 + 截断
    proposals = [
        p for p in data["threads"]
        if isinstance(p, dict)
        and not p.get("overlap_with_main_dims", False)
        and int(p.get("confidence", 0)) >= 3
    ]
    proposals.sort(key=lambda p: int(p.get("confidence", 0)), reverse=True)
    proposals = proposals[:3]

    # 3. 每个 proposal 走检索 + 回答
    threads: list[ConnectionThread] = []
    for p in proposals:
        keywords = list(p.get("query_keywords") or [])
        need_web = bool(p.get("need_web_search", False))
        source: str = "local"
        evidences = []
        if need_web and "web_search" in adapter_registry:
            try:
                evidences = await adapter_registry["web_search"].query(
                    AdapterQuery(
                        keywords=keywords,
                        time_window=state["time_window"],
                        target=state["target"],
                        limit=5,
                    )
                )
                source = "web"
            except Exception:
                evidences = []
        if not evidences and "news_corpus" in adapter_registry:
            try:
                evidences = await adapter_registry["news_corpus"].query(
                    AdapterQuery(
                        keywords=keywords,
                        time_window=state["time_window"],
                        target=state["target"],
                        limit=5,
                    )
                )
                if source == "web":
                    source = "mixed"
                else:
                    source = "local"
            except Exception:
                evidences = []
        if not evidences:
            continue

        ev_dump = [
            {"id": e.id, "source_type": e.source_type, "snippet": (e.snippet or "")[:300]}
            for e in evidences
        ]
        ans_user = (
            f"议题: {p.get('title')}\n"
            f"提议理由: {p.get('hypothesis')}\n"
            f"证据池: {json.dumps(ev_dump, ensure_ascii=False)}"
        )
        try:
            content = llm.chat(system=ANSWER_SYSTEM, user=ans_user, max_tokens=2000)
        except Exception:
            continue

        threads.append(
            ConnectionThread(
                title=p.get("title", ""),
                hypothesis=p.get("hypothesis", ""),
                content=content,
                evidence_ids=[e.id for e in evidences],
                source=source,
                confidence=int(p.get("confidence", 0)),
            )
        )

    return {"connection_threads": threads}
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_node_connection_explorer.py -v`
Expected: 5 passed。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/nodes/connection_explorer.py tests/test_node_connection_explorer.py
git commit -m "$(cat <<'EOF'
新增 connection_explorer 节点（≤3 延伸议题 + 强模型自决 web）

强模型读 6 维 evidence 与 reports，提议 ≤3 个延伸议题（含
need_web_search / confidence / overlap_with_main_dims 自评字段），
过滤 confidence<3 与 overlap=True，对剩余议题决定走 web_search 或
news_corpus 检索，再用强模型写 100-200 字回答。任何步骤异常都
fallback 到 connection_threads=[] 不阻塞 main graph。
EOF
)"
```

---

## Task 8: main_graph 接入 connection_explorer

**目标：** `build_main_graph` 在 `dynamic_sub` 后插入新节点；新增 `adapter_registry` 参数（让节点能调 news_corpus / web_search）。

**Files:**
- Modify: `src/explain_agent/graph/main_graph.py`
- Modify: `tests/test_main_graph.py`

**Step 1: 更新 main_graph**

打开 `src/explain_agent/graph/main_graph.py`，让 `build_main_graph` 接受新参数：

```python
from explain_agent.graph.nodes.connection_explorer import connection_explorer_node


def build_main_graph(
    market_adapter,
    worker_factory,
    weak_llm,
    strong_llm,
    engine,
    adapter_registry: dict | None = None,  # 新增, 用于 connection_explorer
    on_node_event=None,
):
    # ...原 _trace, _parse, _router, _load_fw, _facts, _fan_out, _synth, _sub 保留
    
    async def _connection(state):
        if adapter_registry is None:
            return {"connection_threads": []}
        return await connection_explorer_node(
            state, llm=strong_llm, adapter_registry=adapter_registry,
        )
    
    async def _report(state):
        return await report_builder_node(state, llm=strong_llm)

    # ...
    g.add_node("parse", _trace("parse", _parse))
    g.add_node("router", _trace("router", _router))
    g.add_node("load_framework", _trace("load_framework", _load_fw))
    g.add_node("market_facts", _trace("market_facts", _facts))
    g.add_node("fan_out", _trace("fan_out", _fan_out))
    g.add_node("synth", _trace("synth", _synth))
    g.add_node("dynamic_sub", _trace("dynamic_sub", _sub))
    g.add_node("connection_explorer", _trace("connection_explorer", _connection))  # 新增
    g.add_node("report", _trace("report", _report))
    g.add_node("persist", _trace("persist", _persist))

    g.set_entry_point("parse")
    g.add_edge("parse", "router")
    g.add_edge("router", "load_framework")
    g.add_edge("load_framework", "market_facts")
    g.add_edge("market_facts", "fan_out")
    g.add_edge("fan_out", "synth")
    g.add_edge("synth", "dynamic_sub")
    g.add_edge("dynamic_sub", "connection_explorer")  # 新边
    g.add_edge("connection_explorer", "report")  # 新边
    g.add_edge("report", "persist")
    g.add_edge("persist", END)
```

**Step 2: 改 test_main_graph.py**

打开 `tests/test_main_graph.py`，修改 `fake_strong_llm.chat.side_effect` 增加 connection_explorer 的两次调用：

```python
fake_strong_llm.chat.side_effect = [
    json.dumps({"needs_subbranch": False, "subbranches": []}),  # synth
    # connection_explorer 提议
    json.dumps({"threads": []}),
    # narrative
    json.dumps({
        "claims": [
            {"text": "测试叙事甲", "evidence_ids": ["e1"]},
            {"text": "测试叙事乙", "evidence_ids": ["e1"]},
        ],
    }),
    "维度甲重写报告 [e1]",
    "维度乙重写报告 [e1]",
    "维度丙重写报告 [e1]",
    "维度丁重写报告 [e1]",
    "维度戊重写报告 [e1]",
    "维度己重写报告 [e1]",
]
```

并在调用 `build_main_graph` 时传入 `adapter_registry=None`（保留默认行为）：

```python
graph = build_main_graph(
    market_adapter=fake_market_adapter,
    worker_factory=fake_worker_factory,
    weak_llm=fake_weak_llm,
    strong_llm=fake_strong_llm,
    engine=mock_engine,
)
```

新增断言：

```python
result = await graph.ainvoke(state)
# ...原有断言
assert result.get("connection_threads") == []  # adapter_registry 为 None 时空
```

**Step 3: 跑测试**

Run: `uv run pytest tests/test_main_graph.py -v`
Expected: 1 passed。

**Step 4: 跑全量回归**

Run: `uv run pytest`
Expected: 全部通过（含 Phase 2.A/2.B/2.C 回归）。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/main_graph.py tests/test_main_graph.py
git commit -m "$(cat <<'EOF'
main_graph 接入 connection_explorer 节点

拓扑变更：dynamic_sub → connection_explorer → report。新增可选
adapter_registry 参数；未传时 connection_explorer 跳过返回空。
集成测试 fake_strong_llm 增加 1 次 propose 调用。
EOF
)"
```

---

## Task 9: report_builder 追加 connection_section

**目标：** report_builder 输出新增 `connection_section: str`，用于 REPL/UI 渲染"延伸思考"段落。

**Files:**
- Modify: `src/explain_agent/graph/nodes/report_builder.py`
- Modify: `tests/test_node_report_builder.py`

**Step 1: 写测试**

在 `tests/test_node_report_builder.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_report_includes_connection_section_when_threads():
    """connection_threads 非空时 connection_section 含 title 与 content。"""
    fake_llm = MagicMock()
    fake_llm.chat.side_effect = [
        json.dumps({"claims": [{"text": "claim", "evidence_ids": ["e1"]}]}),
        "policy report",
    ]
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1")], mini_summary="",
            retry_count=1, no_data=False, confidence="high",
        ),
    }
    state["connection_threads"] = [
        {
            "title": "存储芯片产能",
            "hypothesis": "未被覆盖",
            "content": "存储扩产 [e_x]",
            "evidence_ids": ["e_x"],
            "source": "local",
            "confidence": 4,
        }
    ]
    out = await report_builder_node(state, llm=fake_llm)
    assert "connection_section" in out
    assert "存储芯片产能" in out["connection_section"]
    assert "存储扩产" in out["connection_section"]


@pytest.mark.asyncio
async def test_report_connection_section_empty_when_no_threads():
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({"claims": []})
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {}
    state["connection_threads"] = []

    out = await report_builder_node(state, llm=fake_llm)
    assert out["connection_section"] == ""
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: 2 个新测试 FAIL（report_builder 还没返回 connection_section）。

**Step 3: 改 report_builder.py**

打开 `src/explain_agent/graph/nodes/report_builder.py`，在文件靠后位置（紧挨 return 之前）新增渲染函数：

```python
def _render_connection_section(threads: list) -> str:
    if not threads:
        return ""
    parts: list[str] = ["## 延伸思考"]
    for t in threads:
        title = t.get("title", "")
        content = t.get("content", "")
        source = t.get("source", "local")
        conf = t.get("confidence", 0)
        parts.append(f"\n▎ {title}  [source={source}, confidence={conf}]")
        parts.append(content)
    return "\n".join(parts)
```

修改 `report_builder_node` 的 return：

```python
async def report_builder_node(state: AttributionState, llm: LLMClient | None = None) -> dict:
    # ...原有逻辑全部保留

    threads = state.get("connection_threads") or []
    connection_section = _render_connection_section(threads)

    return {
        "narrative": narrative,
        "narrative_claims": narrative_claims,
        "unverified_drops": unverified_drops,
        "dimension_reports": dim_reports,
        "citations": citations,
        "confidence": confidence,
        "connection_section": connection_section,  # 新增
    }
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_node_report_builder.py -v`
Expected: 全部通过（原 11 + 新 2 = 13）。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/nodes/report_builder.py tests/test_node_report_builder.py
git commit -m "$(cat <<'EOF'
report_builder · 输出 connection_section 渲染延伸思考段落

新增 _render_connection_section(threads) 把 connection_threads 渲染
为可打印文本，含 title / source / confidence 元信息和 content。
threads 为空时返回 ""。原有字段 narrative / dimension_reports /
citations / confidence 全部保留不变。
EOF
)"
```

---

## Task 10: persist 节点 tree_json 补 connection_threads

**目标：** persist 时把 `connection_threads` 和 `connection_section` 写到 `tree_json`，便于事后追溯。

**Files:**
- Modify: `src/explain_agent/graph/nodes/persist.py`
- Modify: `tests/test_node_persist.py`

**Step 1: 写测试**

在 `tests/test_node_persist.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_persist_writes_connection_threads():
    """tree_json 必须包含 connection_threads 与 connection_section。"""
    import json as _json
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    state = new_attribution_state("test", session_id="s_2d1")
    state["domain_id"] = "cn_equity_sector_attribution"
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["narrative"] = "..."
    state["narrative_claims"] = []
    state["unverified_drops"] = []
    state["dimension_reports"] = {}
    state["citations"] = []
    state["confidence"] = "medium"
    state["total_cost"] = 0.0
    state["connection_threads"] = [
        {"title": "T1", "hypothesis": "h", "content": "c",
         "evidence_ids": ["e1"], "source": "local", "confidence": 4},
    ]

    await persist_node(state, engine=mock_engine)

    tree_json_arg = None
    for call in mock_conn.exec_driver_sql.call_args_list:
        sql = call.args[0]
        if "explain_evidence_tree" in sql:
            tree_json_arg = call.args[1][1]
            break
    assert tree_json_arg is not None
    tree = _json.loads(tree_json_arg)
    assert "connection_threads" in tree
    assert tree["connection_threads"][0]["title"] == "T1"
```

**Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_node_persist.py -v`
Expected: 新测试 FAIL（persist 没写新字段）。

**Step 3: 改 persist.py**

打开 `src/explain_agent/graph/nodes/persist.py`，在 `tree` 字典中追加：

```python
tree = {
    # ...已有字段保留
    "narrative_claims": state.get("narrative_claims", []),
    "unverified_drops": state.get("unverified_drops", []),
    "connection_threads": state.get("connection_threads", []),  # 新增
}
```

**Step 4: 跑测试**

Run: `uv run pytest tests/test_node_persist.py -v`
Expected: 3 passed。

**Step 5: Commit**

```bash
git add src/explain_agent/graph/nodes/persist.py tests/test_node_persist.py
git commit -m "$(cat <<'EOF'
persist · tree_json 补写 connection_threads

让延伸思考的全量结构落 MySQL，便于后续做漂移率标注 / Phase 3 评估。
EOF
)"
```

---

## Task 11: REPL 适配 + 端到端 smoke 验收

**目标：** REPL 主循环在 main report 后打印 `connection_section`；smoke 脚本验证完整链路。

**Files:**
- Modify: `src/explain_agent/cli/repl/loop.py`
- Modify: `scripts/run_main_graph_smoke.py`
- Modify: `scripts/run_repl_smoke.py`（可选）
- Modify: `README.md`

**Step 1: REPL 主循环渲染 connection_section**

打开 `src/explain_agent/cli/repl/loop.py`，找到 `_run_main` 函数。当前在 main graph 跑完后只 print 是 main_graph 自身已经渲染过的（依靠 console.print 在 main_graph 节点里？）—— 实际上当前 main_graph 不主动 print 报告，是 smoke 脚本/REPL 调用方负责打印。

修改 `_run_main`：

```python
async def _run_main(state: ReplState, env: ReplEnvironment, question: str) -> None:
    result = await env.run_main_graph(question)
    if not result:
        env.console.print("[red]main graph 未返回有效结果[/red]")
        return
    state.current_session_id = result.get("session_id")
    state.current_session = load_session(env.engine, state.current_session_id)
    state.followup_history = []

    # 打印结果
    env.console.print(f"\n[bold yellow]Narrative:[/bold yellow]\n{result.get('narrative', '')}")
    env.console.print(f"\n[bold]Confidence:[/bold] {result.get('confidence')}")
    dim_reports = result.get("dimension_reports", {})
    if dim_reports:
        env.console.print("\n[bold yellow]Dimension Reports:[/bold yellow]")
        for dim_id, report in dim_reports.items():
            env.console.print(f"\n[bold]▎ {dim_id}[/bold]")
            env.console.print(report[:600])
    if result.get("connection_section"):
        env.console.print(f"\n[bold magenta]{result['connection_section']}[/bold magenta]")
```

**Step 2: smoke 脚本接入 adapter_registry**

打开 `scripts/run_main_graph_smoke.py`，修改 build_main_graph 调用：

```python
from explain_agent.adapters.web_search import WebSearchAdapter
from explain_agent.storage.snapshot import SnapshotStore
from explain_agent.config import get_settings

# 在 registry 构造后:
settings = get_settings()
snapshot_store = SnapshotStore(base_dir=settings.snapshot_dir, engine=explain_engine)

if settings.tavily_api_key:
    from tavily import TavilyClient
    tavily = TavilyClient(api_key=settings.tavily_api_key)
    registry["web_search"] = WebSearchAdapter(tavily, snapshot_store)

graph = build_main_graph(
    market_adapter=market,
    worker_factory=worker_factory,
    weak_llm=weak,
    strong_llm=strong,
    engine=explain_engine,
    adapter_registry=registry,  # 新增
    on_node_event=on_node_event,
)
```

打开 `src/explain_agent/cli/repl/loop.py` 的 `_build_environment()`，做同样的改造：

```python
def _build_environment() -> ReplEnvironment:
    # ...原有
    from explain_agent.adapters.web_search import WebSearchAdapter
    from explain_agent.storage.snapshot import SnapshotStore
    from explain_agent.config import get_settings

    settings = get_settings()
    snapshot_store = SnapshotStore(base_dir=settings.snapshot_dir, engine=explain_engine)
    # 给 NewsCorpusAdapter / NewsIndexer 等用同一个 store, 暂时不动 news_corpus（它已存于 Qdrant，不需要重新过 store）

    if settings.tavily_api_key:
        from tavily import TavilyClient
        tavily = TavilyClient(api_key=settings.tavily_api_key)
        registry["web_search"] = WebSearchAdapter(tavily, snapshot_store)

    # ...build_main_graph 调用加上 adapter_registry=registry
```

**Step 3: 跑全量回归**

Run: `uv run pytest`
Expected: 全部通过。

**Step 4: 端到端 smoke（手动验收）**

前提：用户在 `.env` 设置了 `TAVILY_API_KEY`。

```bash
PYTHONUNBUFFERED=1 uv run python -u scripts/run_main_graph_smoke.py "为什么半导体板块今天涨" 2>&1 | tee /tmp/smoke_2d1.log
```

**验收标准：**
- 跑通完整 main graph + connection_explorer，无异常
- 输出包含"## 延伸思考"段落（≥1 个 thread）
- MySQL `explain_evidence_tree.tree_json` 含 `connection_threads` 字段
- `explain_snapshot_blob` 表新增 ≥1 条 `content_type='web_search'` 记录（如果 Tavily 触发）
- `{SNAPSHOT_DIR}/{yyyy/mm/dd}/` 下有新 .txt 文件

如果 `.env` 没设 `TAVILY_API_KEY`，整个 web_search 链路被跳过，但 connection_explorer 仍能用 news_corpus 跑出本地议题。

**Step 5: 更新 README**

在 README 末尾追加：

```markdown

## Phase 2.D-1 验收状态

- [x] SnapshotStore（本地磁盘 + MySQL 指针）
- [x] WebSearchAdapter（Tavily 封装，可选启用）
- [x] ingest pipeline 集成 snapshot（新爬即存，不回填历史）
- [x] framework YAML 三个时效维度加 web_search
- [x] connection_explorer 节点（≤3 延伸议题 + 强模型自决 web）
- [x] State schema 扩展（ConnectionThread / connection_threads）
- [x] report_builder 渲染"延伸思考"段落
- [x] persist tree_json 补 connection_threads
- [x] 全部单测通过（含 Phase 2.A/2.B/2.C 回归）
- [ ] 端到端 smoke：手动用户验收（需要 TAVILY_API_KEY）
```

**Step 6: Commit**

```bash
git add src/explain_agent/cli/repl/loop.py scripts/run_main_graph_smoke.py README.md
git commit -m "$(cat <<'EOF'
REPL + smoke 集成 connection_explorer + WebSearchAdapter

_build_environment 按 TAVILY_API_KEY 是否存在决定注入 WebSearchAdapter；
build_main_graph 调用增加 adapter_registry。REPL 主循环在 main graph
完成后打印 narrative / dimension reports / connection_section。
README 加 Phase 2.D-1 验收清单。
EOF
)"
```

---

## 完成后

Phase 2.D-1 完成后的产出：

- ✅ `SnapshotStore` + `WebSearchAdapter` + `connection_explorer_node` 三个核心组件
- ✅ ingest pipeline 自动落 snapshot（新爬即存）
- ✅ framework YAML 三个时效维度可触发 web search
- ✅ main_graph 拓扑：parse → ... → dynamic_sub → connection_explorer → report → persist
- ✅ State schema：connection_threads 扩展
- ✅ tree_json 全量落库
- ✅ REPL 渲染"延伸思考"

**下一步：Phase 2.D-2 brainstorm**（fan_out 性能优化 + Lazy News Ingest），等 -1 跑通 + 用户日常用 explain 一段时间，积累 connection_threads 的漂移率数据后再做。

**Phase 3 路线图同步**：Phase 3-A 的 connection_explorer 节点骨架本期已完工，Phase 3-A 启动条件改为"-1 跑出 20-30 个真实案例的 connection_threads 漂移率标注（🟢/🟡/🔴）"。
