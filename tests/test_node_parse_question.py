import json
from datetime import datetime, date
from unittest.mock import AsyncMock, MagicMock
import pytest
from explain_agent.graph.state import new_attribution_state
from explain_agent.graph.nodes.parse_question import parse_question_node


@pytest.mark.asyncio
async def test_parse_returns_target_time_intent():
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "target": "半导体",
        "time_window_start": "2026-05-05",
        "time_window_end": "2026-05-12",
        "intent": "up",
    }))
    state = new_attribution_state("为什么半导体最近一周涨停")
    state["asked_at"] = datetime(2026, 5, 12, 15, 0)

    out = await parse_question_node(state, llm=fake_llm)
    assert out["target"] == "半导体"
    assert out["time_window"] == (date(2026, 5, 5), date(2026, 5, 12))
    assert out["intent"] == "up"


@pytest.mark.asyncio
async def test_parse_today_expands_to_five_days():
    """'今天'语义应展开为最近 5 天窗口，避免单日窗口导致 CK 查空。"""
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "target": "半导体",
        "time_window_start": "2026-05-07",
        "time_window_end": "2026-05-12",
        "intent": "up",
    }))
    state = new_attribution_state("为什么半导体今天涨")
    state["asked_at"] = datetime(2026, 5, 12, 15, 0)

    out = await parse_question_node(state, llm=fake_llm)
    delta_days = (out["time_window"][1] - out["time_window"][0]).days
    assert delta_days >= 4


@pytest.mark.asyncio
async def test_parse_falls_back_to_today_when_llm_returns_bad_json():
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value="no json here")
    state = new_attribution_state("半导体")
    state["asked_at"] = datetime(2026, 5, 12, 15, 0)

    out = await parse_question_node(state, llm=fake_llm)
    assert out["target"] == "半导体"
    assert out["time_window"][1] == date(2026, 5, 12)
    assert out["intent"] == "general"
