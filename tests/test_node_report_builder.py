import json
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock
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
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "claims": [
            {"text": "半导体板块上涨主因是政策支持。", "evidence_ids": ["e1"]},
            {"text": "存储芯片涨价拉动设备需求。", "evidence_ids": ["e2", "e3"]},
        ],
    }))
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
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "claims": [
            {"text": "板块涨 5% 受政策推动", "evidence_ids": ["e1"]},
        ],
    }))
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
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "claims": [
            {"text": "政策利好推动情绪修复", "evidence_ids": ["e1"]},
            {"text": "成交额放大至 200 亿", "evidence_ids": ["e1"]},
        ],
    }))
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
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "claims": [{"text": "市场情绪偏暖", "evidence_ids": ["e1"]}],
    }))
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
async def test_dim_reports_rewritten_by_strong_llm():
    """有数据的维度由强模型重写，no_data 维度保持原文。"""
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(side_effect=[
        json.dumps({"claims": [{"text": "test claim", "evidence_ids": ["e1"]}]}),
        "[政策维度重写] 见 [e1] 政策利好。",
    ])
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", "政策证据")],
            mini_summary="弱模型版", retry_count=1, no_data=False, confidence="high",
        ),
        "technical": DimensionResult(
            evidence=[], mini_summary="本维度未检索到相关证据",
            retry_count=10, no_data=True, confidence="low",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert "[政策维度重写]" in out["dimension_reports"]["policy"]
    assert "[e1]" in out["dimension_reports"]["policy"]
    assert out["dimension_reports"]["technical"] == "本维度未检索到相关证据"


@pytest.mark.asyncio
async def test_dim_reports_skip_llm_when_no_data():
    """no_data 维度不应触发任何 strong LLM 调用 (除 narrative)。"""
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(side_effect=[
        json.dumps({"claims": []}),
    ])
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "d1": DimensionResult(evidence=[], mini_summary="无数据", retry_count=1, no_data=True, confidence="low"),
        "d2": DimensionResult(evidence=[], mini_summary="无数据", retry_count=1, no_data=True, confidence="low"),
    }
    await report_builder_node(state, llm=fake_llm)
    assert fake_llm.achat.call_count == 1


@pytest.mark.asyncio
async def test_confidence_high_with_diverse_sources():
    """≥8 个被引用 evidence 且 source_type 多样性 ≥3 → high。"""
    fake_llm = MagicMock()
    eids = [f"e{i}" for i in range(10)]
    chinese_words = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛"]
    fake_llm.achat = AsyncMock(side_effect=[
        json.dumps({"claims": [{"text": chinese_words[i], "evidence_ids": eids[i:i+2]} for i in range(8)]}),
        "policy dim report",
        "industry_chain dim report",
        "capital_flow dim report",
    ])
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev(eids[i], source_type="news") for i in range(4)],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
        "industry_chain": DimensionResult(
            evidence=[make_ev(eids[i], source_type="market_data") for i in range(4, 7)],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
        "capital_flow": DimensionResult(
            evidence=[make_ev(eids[i], source_type="capital_flow") for i in range(7, 10)],
            mini_summary="", retry_count=1, no_data=False, confidence="medium",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert out["confidence"] == "high"


@pytest.mark.asyncio
async def test_confidence_low_with_few_citations():
    """被引用 evidence 数 < 4 → low。"""
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(side_effect=[
        json.dumps({"claims": [{"text": "单一声明", "evidence_ids": ["e1"]}]}),
        "policy dim report",
    ])
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e1", source_type="news")],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert out["confidence"] == "low"


@pytest.mark.asyncio
async def test_confidence_medium_with_4_citations_2_sources():
    """4 个 evidence，2 种 source_type → medium。"""
    fake_llm = MagicMock()
    eids = [f"e{i}" for i in range(4)]
    fake_llm.achat = AsyncMock(side_effect=[
        json.dumps({"claims": [{"text": "整体叙事", "evidence_ids": eids}]}),
        "policy dim report",
        "industry_chain dim report",
    ])
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {
        "policy": DimensionResult(
            evidence=[make_ev("e0", source_type="news"), make_ev("e1", source_type="news")],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
        "industry_chain": DimensionResult(
            evidence=[make_ev("e2", source_type="market_data"), make_ev("e3", source_type="market_data")],
            mini_summary="", retry_count=1, no_data=False, confidence="high",
        ),
    }
    out = await report_builder_node(state, llm=fake_llm)
    assert out["confidence"] == "medium"


def test_narrative_system_prompt_encourages_multi_source():
    """NARRATIVE_SYSTEM 必须显式提醒 LLM 鼓励多 source_type 引用。"""
    from explain_agent.graph.nodes.report_builder import NARRATIVE_SYSTEM
    assert "source_type" in NARRATIVE_SYSTEM
    for stype in ("news", "market_data", "capital_flow"):
        assert stype in NARRATIVE_SYSTEM


@pytest.mark.asyncio
async def test_narrative_falls_back_when_json_invalid():
    """JSON 解析失败时回退到纯文本 narrative，claims 为空。"""
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value="no json here, just text")
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {}

    out = await report_builder_node(state, llm=fake_llm)
    assert out["narrative"] == "no json here, just text"
    assert out["narrative_claims"] == []


@pytest.mark.asyncio
async def test_report_includes_connection_section_when_threads():
    """connection_threads 非空时 connection_section 含 title 与 content。"""
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(side_effect=[
        json.dumps({"claims": [{"text": "claim", "evidence_ids": ["e1"]}]}),
        "policy report",
    ])
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
    fake_llm.achat = AsyncMock(return_value=json.dumps({"claims": []}))
    state = new_attribution_state("test")
    state["target"] = "X"
    state["time_window"] = (date(2026, 5, 5), date(2026, 5, 12))
    state["market_facts"] = {"snippet": ""}
    state["dimension_results"] = {}
    state["connection_threads"] = []

    out = await report_builder_node(state, llm=fake_llm)
    assert out["connection_section"] == ""
