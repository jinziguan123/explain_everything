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
async def test_strip_unverified_numbers_keeps_verified():
    """claim 中的数字在证据中能找到 → 保留。"""
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({
        "claims": [
            {"text": "板块涨 5% 受政策推动", "evidence_ids": ["e1"]},
        ],
    })
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", "今日板块涨幅 5%")],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert "板块涨 5%" in out["narrative"]
    assert out["unverified_drops"] == []


@pytest.mark.asyncio
async def test_strip_unverified_numbers_drops_hallucinated():
    """claim 中的数字在证据中找不到 → 整句删除并记 unverified_drops。"""
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({
        "claims": [
            {"text": "政策利好推动情绪修复", "evidence_ids": ["e1"]},
            {"text": "成交额放大至 200 亿", "evidence_ids": ["e1"]},
        ],
    })
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", "证监会发文支持半导体产业")],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert "政策利好推动情绪修复" in out["narrative"]
    assert "200 亿" not in out["narrative"]
    assert len(out["unverified_drops"]) == 1
    assert "200 亿" in out["unverified_drops"][0]


@pytest.mark.asyncio
async def test_strip_keeps_claims_without_numbers():
    """无数字的 claim 不受校验影响。"""
    fake_llm = MagicMock()
    fake_llm.chat.return_value = json.dumps({
        "claims": [{"text": "市场情绪偏暖", "evidence_ids": ["e1"]}],
    })
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", "无具体数字的证据")],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert "市场情绪偏暖" in out["narrative"]
    assert out["unverified_drops"] == []


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
