from datetime import datetime
from explain_agent.graph.state import (
    AttributionState, DimensionResult, SubBranchSpec, Citation,
    new_attribution_state,
)


def test_new_state_has_required_defaults():
    s = new_attribution_state(raw_question="为什么半导体涨停", session_id="s1")
    assert s["raw_question"] == "为什么半导体涨停"
    assert s["session_id"] == "s1"
    assert isinstance(s["asked_at"], datetime)
    assert s["dimension_results"] == {}
    assert s["subbranches"] == []
    assert s["needs_subbranch"] is False
    assert s["citations"] == []
    assert s["errors"] == []
    assert s["llm_calls"] == {}
    assert s["total_cost"] == 0.0


def test_dimension_result_typeddict():
    r: DimensionResult = {
        "evidence": [],
        "mini_summary": "本维度无相关证据",
        "retry_count": 3,
        "no_data": True,
        "confidence": "low",
    }
    assert r["no_data"] is True


def test_subbranch_spec():
    spec: SubBranchSpec = {
        "name": "美国 HBM 制裁",
        "query_hints": ["BIS", "HBM"],
    }
    assert spec["name"] == "美国 HBM 制裁"


def test_citation():
    c: Citation = {
        "evidence_id": "e_001",
        "url": "https://example.com",
        "snapshot_id": "s_001",
        "source_type": "news",
    }
    assert c["evidence_id"] == "e_001"
