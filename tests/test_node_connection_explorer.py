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
    fake_llm.achat = AsyncMock(side_effect=[
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
    ])
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
    fake_llm.achat = AsyncMock(side_effect=[
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
    ])
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
    fake_llm.achat = AsyncMock(side_effect=[
        json.dumps({
            "threads": [
                {"title": "BIS 制裁", "hypothesis": "需要最新外部新闻",
                 "need_web_search": True, "confidence": 5,
                 "overlap_with_main_dims": False, "query_keywords": ["BIS", "HBM"]},
            ],
        }),
        "BIS 制裁最新进展 [ew1] ...",
    ])
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
    threads_proposal = {
        "threads": [
            {"title": f"T{i}", "hypothesis": "", "need_web_search": False,
             "confidence": 5, "overlap_with_main_dims": False, "query_keywords": []}
            for i in range(5)
        ]
    }
    fake_llm.achat = AsyncMock(side_effect=[json.dumps(threads_proposal)] + ["回答"] * 3)
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
    fake_llm.achat = AsyncMock(return_value="not json")
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["dimension_results"] = {}

    out = await connection_explorer_node(
        state, llm=fake_llm,
        adapter_registry={"news_corpus": MagicMock(), "web_search": MagicMock()},
    )
    assert out["connection_threads"] == []


@pytest.mark.asyncio
async def test_connection_explorer_processes_threads_concurrently():
    """多个 thread 的 retrieve+answer 必须并发。"""
    import asyncio
    import time

    sleep_per_answer = 0.3

    async def slow_answer(**kw):
        await asyncio.sleep(sleep_per_answer)
        system = kw.get("system", "")
        # PROPOSE_SYSTEM 含 "threads" JSON 输出格式
        if "threads" in system and "query_keywords" in system:
            return json.dumps({
                "threads": [
                    {"title": f"T{i}", "hypothesis": "h", "need_web_search": False,
                     "confidence": 5, "overlap_with_main_dims": False, "query_keywords": []}
                    for i in range(3)
                ]
            })
        return "answer"

    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(side_effect=slow_answer)
    fake_news = MagicMock()
    fake_news.query = AsyncMock(return_value=[make_ev("e1")])

    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["dimension_results"] = {}

    t0 = time.perf_counter()
    out = await connection_explorer_node(
        state, llm=fake_llm,
        adapter_registry={"news_corpus": fake_news, "web_search": MagicMock()},
    )
    elapsed = time.perf_counter() - t0

    # propose (0.3) + 3 × answer 并发 (0.3) = 0.6s
    # 顺序: 0.3 + 3*0.3 = 1.2s
    # 给 0.9s 宽容上限
    assert len(out["connection_threads"]) == 3
    assert elapsed < 0.9, (
        f"connection_explorer 看起来仍在顺序处理 thread: {elapsed:.2f}s, "
        f"预期 < 0.9s。"
    )
