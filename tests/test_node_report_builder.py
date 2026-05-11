from datetime import date, datetime
from unittest.mock import MagicMock
import pytest
from explain_agent.core.types import Evidence
from explain_agent.graph.state import new_attribution_state, DimensionResult
from explain_agent.graph.nodes.report_builder import report_builder_node


def make_ev(id: str, source_type: str = "news", url: str | None = "http://a.com") -> Evidence:
    return Evidence(
        id=id, source="x", source_type=source_type,
        url=url, snippet=f"snip {id}", timestamp=datetime.now(),
    )


@pytest.mark.asyncio
async def test_report_assembles_narrative_and_dim_reports():
    fake_llm = MagicMock()
    fake_llm.chat.return_value = "半导体板块今日上涨主因是 ... (强模型生成的简短叙事)"

    state = new_attribution_state("为什么半导体涨")
    state["target"] = "半导体"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": "板块涨 5%"}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1")], mini_summary="政策维 mini",
            retry_count=1, no_data=False, confidence="high",
        ),
        "technical": DimensionResult(
            evidence=[], mini_summary="本维度未检索到相关证据",
            retry_count=10, no_data=True, confidence="low",
        ),
    }

    out = await report_builder_node(state, llm=fake_llm)
    assert "半导体板块今日上涨主因" in out["narrative"]
    assert "政策维 mini" in out["dimension_reports"]["policy"]
    assert "未检索到" in out["dimension_reports"]["technical"]
    assert any(c["evidence_id"] == "e1" for c in out["citations"])
    assert out["confidence"] in ("high", "medium", "low")
