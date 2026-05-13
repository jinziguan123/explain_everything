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


@pytest.mark.asyncio
async def test_followup_attempts_reasoning_for_cross_event_question():
    """跨事件追问（如特朗普访华）应基于现有 evidence 推测, 不直接拒绝。"""
    fake_llm = MagicMock()
    # 模拟强模型按新 prompt 行为：先推测, 末尾建议 /new
    fake_llm.achat = AsyncMock(return_value=(
        "基于现有 international 维度的 [e_xxx] 证据推测, 该事件可能加剧中美科技战, "
        "对半导体板块情绪有阶段性扰动。若需该事件的实时数据/详细分析, "
        "建议用 /new 开新会话。"
    ))

    session = {
        "session_id": "s_test",
        "target": "半导体",
        "narrative": "...",
        "dimension_reports": {"international": "中美科技战 ..."},
        "citations": [{"evidence_id": "e1", "source_type": "news", "snippet": "..."}],
        "market_facts": {"snippet": ""},
        "time_window": "2026-05-08 to 2026-05-13",
    }
    out = await run_followup(
        session=session, history=[], question="特朗普今天访华影响",
        llm=fake_llm, engine=MagicMock(),
    )
    assert "推测" in out["answer"] or "/new" in out["answer"]
    # 严格断言: 不应包含旧 prompt 的"超出当前会话的 X 主题"措辞
    assert "超出当前会话" not in out["answer"]


def test_followup_system_prompt_encourages_evidence_reasoning():
    """FOLLOWUP_SYSTEM prompt 应明确包含"先尝试基于现有 evidence 推测"。"""
    from explain_agent.graph.followup import FOLLOWUP_SYSTEM
    assert "推测" in FOLLOWUP_SYSTEM
    assert "/new" in FOLLOWUP_SYSTEM
    # 不应再有旧的"超出 target 范围就拒"措辞
    assert "完全跳出当前 target 的范围" not in FOLLOWUP_SYSTEM
