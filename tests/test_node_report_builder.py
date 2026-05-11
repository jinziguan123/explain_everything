import json
from datetime import date, datetime
from unittest.mock import MagicMock
import pytest
from explain_agent.core.types import Evidence
from explain_agent.graph.state import new_attribution_state, DimensionResult
from explain_agent.graph.nodes.report_builder import report_builder_node


def make_ev(id: str, snippet: str = "snip", source_type: str = "news", url: str | None = "http://a.com") -> Evidence:
    return Evidence(
        id=id, source="x", source_type=source_type,
        url=url, snippet=snippet, timestamp=datetime.now(),
    )


@pytest.mark.asyncio
async def test_narrative_returns_structured_claims():
    """强模型返回 JSON，每个 claim 都有 evidence_ids。"""
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({
        "claims": [
            {"text": "半导体板块上涨主因是政策支持。", "evidence_ids": ["e1"]},
            {"text": "存储芯片涨价拉动设备需求。", "evidence_ids": ["e2", "e3"]},
        ],
    })
    state = new_attribution_state("test")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": "板块涨 5%"}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", "政策支持")], mini_summary="政策维",
            retry_count=1, no_data=False, confidence="high",
        ),
        "industry_chain": DimensionResult(
            evidence=[make_ev("e2", "存储涨价"), make_ev("e3", "设备需求")],
            mini_summary="产业链维", retry_count=1, no_data=False, confidence="high",
        ),
    }

    out = await report_builder_node(state, llm=fake_llm)
    assert len(out["narrative_claims"]) == 2
    assert all(c["evidence_ids"] for c in out["narrative_claims"])
    assert "半导体板块上涨主因是政策支持" in out["narrative"]
    assert "存储芯片涨价" in out["narrative"]


@pytest.mark.asyncio
async def test_narrative_falls_back_when_json_invalid():
    """JSON 解析失败时回退到纯文本 narrative，claims 为空。"""
    fake_llm = MagicMock()
    fake_llm.chat.return_value = "no json here, just text"
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {}

    out = await report_builder_node(state, llm=fake_llm)
    assert out["narrative"] == "no json here, just text"
    assert out["narrative_claims"] == []
