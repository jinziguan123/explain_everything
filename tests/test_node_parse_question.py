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


@pytest.mark.asyncio
async def test_parse_today_morning_qualifier():
    """用户问"今天上午"应解析出 intent_qualifier=上午。"""
    from datetime import date
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "target": "上午行情",
        "time_window_start": "2026-05-13",
        "time_window_end": "2026-05-13",
        "intent": "general",
        "intent_qualifier": "上午",
    }))
    state = new_attribution_state("总结一下今天上午的行情")
    state["asked_at"] = datetime(2026, 5, 13, 10, 0, 0)
    out = await parse_question_node(state, llm=fake_llm)
    assert out["intent_qualifier"] == "上午"
    assert out["time_window"] == (date(2026, 5, 13), date(2026, 5, 13))


@pytest.mark.asyncio
async def test_parse_recent_qualifier_default():
    """用户没说时间, intent_qualifier 默认 '近期'。"""
    from datetime import date
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "target": "半导体",
        "time_window_start": "2026-05-06",
        "time_window_end": "2026-05-13",
        "intent": "up",
        "intent_qualifier": "近期",
    }))
    state = new_attribution_state("半导体板块为什么涨")
    state["asked_at"] = datetime(2026, 5, 13, 10, 0, 0)
    out = await parse_question_node(state, llm=fake_llm)
    assert out["intent_qualifier"] == "近期"


@pytest.mark.asyncio
async def test_parse_qualifier_falls_back_to_recent_when_missing():
    """LLM 没输出 intent_qualifier 字段时, fallback 到 '近期'。"""
    fake_llm = MagicMock()
    fake_llm.achat = AsyncMock(return_value=json.dumps({
        "target": "半导体",
        "time_window_start": "2026-05-06",
        "time_window_end": "2026-05-13",
        "intent": "up",
    }))
    state = new_attribution_state("半导体涨")
    state["asked_at"] = datetime(2026, 5, 13, 10, 0, 0)
    out = await parse_question_node(state, llm=fake_llm)
    assert out["intent_qualifier"] == "近期"
