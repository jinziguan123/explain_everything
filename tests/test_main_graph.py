from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
import json
import pytest
from explain_agent.core.types import Evidence
from explain_agent.graph.state import new_attribution_state, DimensionResult


@pytest.mark.asyncio
async def test_main_graph_compiles_and_runs_with_mocks():
    """端到端 mock 全部节点，确认 graph 拓扑能跑通。"""
    from explain_agent.graph.main_graph import build_main_graph

    fake_market_adapter = MagicMock()
    fake_market_adapter.query = AsyncMock(return_value=[
        Evidence(id="m1", source="clickhouse_market", source_type="market_data",
                 snippet="半导体涨 5%", raw_payload={}, timestamp=datetime.now())
    ])

    fake_worker = MagicMock()
    fake_worker.run = AsyncMock(return_value=DimensionResult(
        evidence=[Evidence(id="e1", source="x", source_type="news",
                           snippet="snip", timestamp=datetime.now())],
        mini_summary="某维 mini", retry_count=1, no_data=False, confidence="medium",
    ))
    fake_worker_factory = MagicMock(return_value=fake_worker)

    fake_weak_llm = MagicMock()
    fake_weak_llm.chat.side_effect = lambda **kwargs: json.dumps({
        "target": "半导体",
        "time_window_start": "2026-05-05",
        "time_window_end": "2026-05-12",
        "intent": "up",
    })

    fake_strong_llm = MagicMock()
    fake_strong_llm.chat.side_effect = [
        json.dumps({"needs_subbranch": False, "subbranches": []}),
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

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.begin.return_value.__enter__.return_value = mock_conn

    graph = build_main_graph(
        market_adapter=fake_market_adapter,
        worker_factory=fake_worker_factory,
        weak_llm=fake_weak_llm,
        strong_llm=fake_strong_llm,
        engine=mock_engine,
    )
    state = new_attribution_state("为什么半导体板块今天涨停")

    result = await graph.ainvoke(state)
    assert result["target"] == "半导体"
    assert result["domain_id"] == "cn_equity_sector_attribution"
    assert "market_facts" in result
    assert len(result["dimension_results"]) == 6
    assert "narrative" in result
    assert result["confidence"] in ("high", "medium", "low")
    assert len(result["narrative_claims"]) >= 1
    assert all(c["evidence_ids"] for c in result["narrative_claims"])
    assert all("维度" in v and "重写" in v for v in result["dimension_reports"].values())
    assert result.get("connection_threads") == []
