import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest

from explain_agent.graph.followup import run_followup, _build_followup_prompt


@pytest.mark.asyncio
async def test_run_followup_returns_answer_and_triggers_persist(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value="政策面主要是 ...")

    persisted = []

    async def fake_persist(engine, session_id, q, a):
        persisted.append((session_id, q, a))

    monkeypatch.setattr(
        "explain_agent.graph.followup._persist_followup", fake_persist
    )

    session = {
        "session_id": "s_abc",
        "target": "半导体",
        "time_window": ["2026-05-06", "2026-05-11"],
        "narrative": "半导体上涨主因 ...",
        "narrative_claims": [{"text": "claim1", "evidence_ids": ["e1"]}],
        "dimension_reports": {"policy": "政策维度详细", "industry_chain": "..."},
        "citations": [
            {"evidence_id": "e1", "url": "http://a", "source_type": "news"},
        ],
        "market_facts": {"snippet": "板块涨 5%"},
    }
    out = await run_followup(
        session=session,
        history=[],
        question="政策面具体是什么？",
        llm=fake_llm,
        engine=MagicMock(),
    )
    assert out["answer"] == "政策面主要是 ..."
    assert out["session_id"] == "s_abc"
    await asyncio.sleep(0.05)
    assert len(persisted) == 1
    assert persisted[0][1] == "政策面具体是什么？"


@pytest.mark.asyncio
async def test_followup_falls_back_when_llm_raises(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(side_effect=RuntimeError("network down"))

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(
        "explain_agent.graph.followup._persist_followup", noop
    )

    session = {
        "session_id": "s_abc", "target": "X",
        "time_window": ["2026-05-06", "2026-05-11"],
        "narrative": "", "narrative_claims": [],
        "dimension_reports": {}, "citations": [], "market_facts": {},
    }
    out = await run_followup(
        session=session, history=[], question="?",
        llm=fake_llm, engine=MagicMock(),
    )
    assert out["answer"].startswith("（追问失败")


def test_build_followup_prompt_truncates_citations():
    session = {
        "session_id": "s_a", "target": "半导体",
        "time_window": ["2026-05-06", "2026-05-11"],
        "narrative": "n", "narrative_claims": [],
        "dimension_reports": {f"d{i}": "report" for i in range(6)},
        "citations": [
            {"evidence_id": f"e{i}", "url": f"u{i}",
             "source_type": "news", "snippet": "x" * 500}
            for i in range(40)
        ],
        "market_facts": {"snippet": "锚点"},
    }
    user = _build_followup_prompt(session, history=[], question="why?")
    assert user.count("evidence_id") <= 20
    for i in range(6):
        assert f"d{i}" in user
