import json
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from explain_agent.core.types import Evidence
from explain_agent.graph.dimension_worker import DimensionWorker


def make_evidence(id: str, score: float = 0.8) -> Evidence:
    return Evidence(
        id=id, source="news_corpus", source_type="news",
        snippet=f"证据 {id}", timestamp=datetime.now(),
        metadata={"score": score},
    )


@pytest.mark.asyncio
async def test_worker_terminates_when_evidence_sufficient():
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(side_effect=[
        json.dumps({"keywords": ["半导体", "涨停"]}),
        json.dumps({"sufficient": True, "relevant_ids": ["e1", "e2"]}),
        "本维度共找到 2 条政策类证据,主因是 ...",
    ])
    mock_adapter = MagicMock()
    mock_adapter.query = AsyncMock(return_value=[make_evidence("e1"), make_evidence("e2")])
    registry = {"news_corpus": mock_adapter}

    worker = DimensionWorker(
        dimension_config={
            "id": "policy", "name": "政策/宏观", "data_sources": ["news_corpus"],
            "query_template": "{target} 相关政策",
        },
        worker_config={"max_rounds": 10, "soft_terminate_no_gain_rounds": 2},
        llm=fake_llm, adapter_registry=registry,
    )
    result = await worker.run(
        target="半导体",
        time_window=(date(2026, 5, 5), date(2026, 5, 12)),
        market_facts={"snippet": "板块涨 5%"},
    )
    assert len(result["evidence"]) == 2
    assert result["no_data"] is False
    assert result["retry_count"] == 1
    assert result["confidence"] in ("high", "medium", "low")


@pytest.mark.asyncio
async def test_worker_marks_no_data_when_adapter_always_empty():
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value=json.dumps({"keywords": ["x"]}))
    mock_adapter = MagicMock()
    mock_adapter.query = AsyncMock(return_value=[])

    worker = DimensionWorker(
        dimension_config={"id": "policy", "name": "政策", "data_sources": ["news_corpus"],
                          "query_template": "test"},
        worker_config={"max_rounds": 3, "soft_terminate_no_gain_rounds": 2},
        llm=fake_llm, adapter_registry={"news_corpus": mock_adapter},
    )
    result = await worker.run(
        target="X", time_window=(date(2026, 5, 5), date(2026, 5, 12)), market_facts={},
    )
    assert result["no_data"] is True
    assert result["evidence"] == []
    assert result["mini_summary"] == "本维度未检索到相关证据"


@pytest.mark.asyncio
async def test_worker_auto_expands_window_on_empty():
    """单日窗口查空时自动扩到 7 天再试。"""
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(side_effect=[
        json.dumps({"keywords": ["k1"]}),
        json.dumps({"sufficient": True, "relevant_ids": ["e1"]}),
        "summary",
    ])

    call_windows = []

    async def adapter_query(q):
        call_windows.append((q.time_window[0], q.time_window[1]))
        if (q.time_window[1] - q.time_window[0]).days <= 3:
            return []
        return [make_evidence("e1")]

    mock_adapter = MagicMock()
    mock_adapter.query = adapter_query

    worker = DimensionWorker(
        dimension_config={"id": "x", "name": "X", "data_sources": ["news_corpus"],
                          "query_template": "t"},
        worker_config={"max_rounds": 1, "soft_terminate_no_gain_rounds": 99},
        llm=fake_llm, adapter_registry={"news_corpus": mock_adapter},
    )
    result = await worker.run(
        target="X",
        time_window=(date(2026, 5, 11), date(2026, 5, 11)),
        market_facts={},
    )
    assert len(call_windows) >= 2
    expanded = call_windows[-1]
    assert (expanded[1] - expanded[0]).days >= 6
    assert result["no_data"] is False


@pytest.mark.asyncio
async def test_worker_respects_max_rounds():
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(side_effect=[
        json.dumps({"keywords": [f"k{i}"]}) if i % 2 == 0 else json.dumps({"sufficient": False})
        for i in range(20)
    ])
    mock_adapter = MagicMock()
    mock_adapter.query = AsyncMock(return_value=[make_evidence("e_x")])

    worker = DimensionWorker(
        dimension_config={"id": "policy", "name": "政策", "data_sources": ["news_corpus"],
                          "query_template": "test"},
        worker_config={"max_rounds": 3, "soft_terminate_no_gain_rounds": 99},
        llm=fake_llm, adapter_registry={"news_corpus": mock_adapter},
    )
    result = await worker.run(
        target="X", time_window=(date(2026, 5, 5), date(2026, 5, 12)), market_facts={},
    )
    assert result["retry_count"] == 3
