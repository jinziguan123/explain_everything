"""Phase 9 Wave C.2: query_loop async generator tests.

测试覆盖:
- 终止: stop_reason='end_turn' 且无 tool_uses → TurnCompleteEvent
- 调度: 有 tool_uses → 派发 tool, yield ToolUseEvent + ToolResultEvent
- 预算: per_turn / per_session 耗尽 → BudgetExhaustedEvent + return
- 持久化: tool_result 写 transcript (assistant + tool_result 两条)

参考 docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave C.2.
"""

from __future__ import annotations

import pytest

from explain_engine.chat.session import ChatSession
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_done_session(sid: str = "s_100b0001") -> Session:
    g = ExplanationGraph(root_question="why?")
    g.add_node(VariableNode(
        id="c_001", name="abs", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="obs", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7, mechanism_description="m",
    ))
    state = CognitiveState(graph=g, budget_remaining=10, root_question="why?")
    state.insight_candidates = ["c_001"]
    meta = SessionMeta.new(question="why?")
    meta.session_id = sid
    meta.stage = "done"
    sess = Session(meta=meta, state=state)
    SessionStore().save(sess)
    return sess


class _FakeLLMResponse:
    """Mimics Anthropic chat_with_tools response: .text / .tool_uses / .stop_reason."""

    def __init__(self, text="", tool_uses=None, stop_reason="end_turn"):
        self.text = text
        self.tool_uses = tool_uses or []
        self.stop_reason = stop_reason


class _FakeLLMClient:
    """Scripted LLM: pops responses in order on each chat_with_tools call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0

    async def chat_with_tools(self, system, messages, tools):
        self.call_count += 1
        if not self.responses:
            return _FakeLLMResponse()
        return self.responses.pop(0)


class TestQueryLoopTermination:
    @pytest.mark.asyncio
    async def test_terminates_when_no_tool_use(self) -> None:
        """LLM 不调 tool → 1 次 LLM call, yield TurnCompleteEvent, return."""
        from explain_engine.chat.loop import TurnCompleteEvent, query_loop

        _make_done_session("s_aaaa0001")
        chat = ChatSession("s_aaaa0001")
        llm = _FakeLLMClient([
            _FakeLLMResponse(text="hello user", tool_uses=[], stop_reason="end_turn"),
        ])
        events = []
        async for ev in query_loop(chat, llm):
            events.append(ev)
        assert llm.call_count == 1
        assert any(isinstance(ev, TurnCompleteEvent) for ev in events)

    @pytest.mark.asyncio
    async def test_yields_assistant_text_when_provided(self) -> None:
        from explain_engine.chat.loop import AssistantTextEvent, query_loop

        _make_done_session("s_aaaa0002")
        chat = ChatSession("s_aaaa0002")
        llm = _FakeLLMClient([
            _FakeLLMResponse(text="hi from llm", tool_uses=[]),
        ])
        events = []
        async for ev in query_loop(chat, llm):
            events.append(ev)
        text_evs = [ev for ev in events if isinstance(ev, AssistantTextEvent)]
        assert len(text_evs) >= 1
        assert text_evs[0].content == "hi from llm"


class TestQueryLoopToolDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_tool_call(self, mocker) -> None:
        """LLM 调 check (readonly), tool 派发 + yield ToolUseEvent + ToolResultEvent."""
        from explain_engine.chat.loop import (
            ToolResultEvent,
            ToolUseEvent,
            query_loop,
        )
        from explain_engine.engines.simulation import AcceptanceReport

        _make_done_session("s_aaaa0003")
        chat = ChatSession("s_aaaa0003")

        # check_tool 调 aggregate_acceptance — mock 它
        fake_report = AcceptanceReport(
            avg_consistency=0.9,
            avg_essentialness=0.5,
            per_l1={"c_001": 0.9},
            per_l2={},
            weak_chain_l1s=[],
            lowest_l1=("c_001", 0.9),
            consistency_spread=0.0,
            essentialness_spread=0.0,
            rollout_coverage=1.0,
            missing_l0=[],
        )
        mocker.patch(
            "explain_engine.chat.tools.aggregate_acceptance",
            return_value=fake_report,
        )

        llm = _FakeLLMClient([
            _FakeLLMResponse(
                text="checking",
                tool_uses=[{"id": "tu_1", "name": "check", "input": {"target_id": None}}],
                stop_reason="tool_use",
            ),
            _FakeLLMResponse(text="done", tool_uses=[], stop_reason="end_turn"),
        ])
        events = []
        async for ev in query_loop(chat, llm):
            events.append(ev)
        # 2 LLM calls (first → tool, second → end_turn)
        assert llm.call_count == 2
        tool_use_evs = [ev for ev in events if isinstance(ev, ToolUseEvent)]
        tool_result_evs = [ev for ev in events if isinstance(ev, ToolResultEvent)]
        assert len(tool_use_evs) == 1
        assert tool_use_evs[0].tool_name == "check"
        assert len(tool_result_evs) == 1
        assert tool_result_evs[0].tool_name == "check"
        # result string contains avg_consistency=0.9
        assert "0.9" in tool_result_evs[0].result or "consistency" in tool_result_evs[0].result.lower()

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error_string(self) -> None:
        """LLM 调不存在的 tool → ToolResultEvent.result 含 'unknown tool'."""
        from explain_engine.chat.loop import ToolResultEvent, query_loop

        _make_done_session("s_aaaa0004")
        chat = ChatSession("s_aaaa0004")
        llm = _FakeLLMClient([
            _FakeLLMResponse(
                tool_uses=[{"id": "tu_1", "name": "nonexistent", "input": {}}],
                stop_reason="tool_use",
            ),
            _FakeLLMResponse(text="oops", tool_uses=[], stop_reason="end_turn"),
        ])
        events = []
        async for ev in query_loop(chat, llm):
            events.append(ev)
        result_evs = [ev for ev in events if isinstance(ev, ToolResultEvent)]
        assert len(result_evs) == 1
        assert "unknown" in result_evs[0].result.lower()


class TestQueryLoopBudget:
    @pytest.mark.asyncio
    async def test_per_turn_budget_exhaustion(self) -> None:
        """per_turn_remaining=0 → BudgetExhaustedEvent(scope='per_turn'), return."""
        from explain_engine.chat.loop import BudgetExhaustedEvent, query_loop

        _make_done_session("s_aaaa0005")
        chat = ChatSession("s_aaaa0005")
        chat.chat_state.budget_per_turn_remaining = 0
        llm = _FakeLLMClient([_FakeLLMResponse(text="x")])
        events = []
        async for ev in query_loop(chat, llm):
            events.append(ev)
        assert llm.call_count == 0  # never called
        budget_evs = [ev for ev in events if isinstance(ev, BudgetExhaustedEvent)]
        assert len(budget_evs) == 1
        assert budget_evs[0].scope == "per_turn"

    @pytest.mark.asyncio
    async def test_per_session_budget_exhaustion(self) -> None:
        """per_session_remaining=0 (per_turn 还有) → BudgetExhaustedEvent(scope='per_session')."""
        from explain_engine.chat.loop import BudgetExhaustedEvent, query_loop

        _make_done_session("s_aaaa0006")
        chat = ChatSession("s_aaaa0006")
        chat.chat_state.budget_per_turn_remaining = 5
        chat.chat_state.budget_per_session_remaining = 0
        llm = _FakeLLMClient([_FakeLLMResponse(text="x")])
        events = []
        async for ev in query_loop(chat, llm):
            events.append(ev)
        budget_evs = [ev for ev in events if isinstance(ev, BudgetExhaustedEvent)]
        assert len(budget_evs) == 1
        assert budget_evs[0].scope == "per_session"


class TestQueryLoopTranscriptPersist:
    @pytest.mark.asyncio
    async def test_appends_tool_result_to_transcript(self, mocker) -> None:
        """Tool 调用后, transcript 多 2 条 (assistant + user 的 tool_result)."""
        from explain_engine.chat.loop import query_loop
        from explain_engine.engines.simulation import AcceptanceReport

        _make_done_session("s_aaaa0007")
        chat = ChatSession("s_aaaa0007")
        before_count = len(chat.transcript)

        fake_report = AcceptanceReport(
            avg_consistency=0.9, avg_essentialness=0.5,
            per_l1={}, per_l2={}, weak_chain_l1s=[],
            lowest_l1=None, consistency_spread=0.0,
            essentialness_spread=0.0, rollout_coverage=1.0, missing_l0=[],
        )
        mocker.patch(
            "explain_engine.chat.tools.aggregate_acceptance",
            return_value=fake_report,
        )

        llm = _FakeLLMClient([
            _FakeLLMResponse(
                text="checking",
                tool_uses=[{"id": "tu_1", "name": "check", "input": {"target_id": None}}],
                stop_reason="tool_use",
            ),
            _FakeLLMResponse(text="done", tool_uses=[], stop_reason="end_turn"),
        ])
        async for _ev in query_loop(chat, llm):
            pass
        # 2 messages appended: assistant turn (text + tool_use) + user (tool_result)
        assert len(chat.transcript) - before_count == 2
        assert chat.transcript[-2]["role"] == "assistant"
        assert chat.transcript[-1]["role"] == "user"
        # tool_result content shape
        last = chat.transcript[-1]["content"]
        assert isinstance(last, list)
        assert last[0]["type"] == "tool_result"
        assert last[0]["tool_use_id"] == "tu_1"

    @pytest.mark.asyncio
    async def test_budget_consumed_per_tool_call(self, mocker) -> None:
        """每次 tool 调用 → per_turn / per_session remaining -1."""
        from explain_engine.chat.loop import query_loop
        from explain_engine.engines.simulation import AcceptanceReport

        _make_done_session("s_aaaa0008")
        chat = ChatSession("s_aaaa0008")
        per_turn_before = chat.chat_state.budget_per_turn_remaining
        per_session_before = chat.chat_state.budget_per_session_remaining

        fake_report = AcceptanceReport(
            avg_consistency=0.9, avg_essentialness=0.5,
            per_l1={}, per_l2={}, weak_chain_l1s=[],
            lowest_l1=None, consistency_spread=0.0,
            essentialness_spread=0.0, rollout_coverage=1.0, missing_l0=[],
        )
        mocker.patch(
            "explain_engine.chat.tools.aggregate_acceptance",
            return_value=fake_report,
        )

        llm = _FakeLLMClient([
            _FakeLLMResponse(
                tool_uses=[{"id": "tu_1", "name": "check", "input": {"target_id": None}}],
                stop_reason="tool_use",
            ),
            _FakeLLMResponse(text="done", tool_uses=[], stop_reason="end_turn"),
        ])
        async for _ev in query_loop(chat, llm):
            pass
        # 1 tool call → -1 budget per scope
        assert chat.chat_state.budget_per_turn_remaining == per_turn_before - 1
        assert chat.chat_state.budget_per_session_remaining == per_session_before - 1


class TestChatSessionIntegration:
    @pytest.mark.asyncio
    async def test_handle_user_input_with_llm_runs_query_loop(self) -> None:
        """ChatSession.handle_user_input(text, llm=fake) → query_loop yields events."""
        from explain_engine.chat.loop import TurnCompleteEvent

        _make_done_session("s_aaaa0009")
        chat = ChatSession("s_aaaa0009")
        llm = _FakeLLMClient([
            _FakeLLMResponse(text="hi back", tool_uses=[], stop_reason="end_turn"),
        ])
        events = []
        async for ev in chat.handle_user_input("hello", llm=llm):
            events.append(ev)
        # User input appended (turn_count incremented), then query_loop ran
        assert chat.chat_state.turn_count == 1
        assert llm.call_count == 1
        assert any(isinstance(ev, TurnCompleteEvent) for ev in events)

    @pytest.mark.asyncio
    async def test_handle_user_input_without_llm_yields_placeholder(self) -> None:
        """llm=None → C.1 backward compat placeholder event."""
        _make_done_session("s_aaaa000a")
        chat = ChatSession("s_aaaa000a")
        events = []
        async for ev in chat.handle_user_input("hello"):
            events.append(ev)
        assert any(ev.type == "placeholder" for ev in events)


class TestBudgetMidLoopExhaustion:
    """I1 regression: budget check inside tool dispatch loop prevents overshoot.

    背景: LLM 一次 response 可含多个 tool_use; 仅外层 while 顶部检查 budget 会
    在单 response 内 dispatch 全部 tool, 导致 remaining 变负 (overshoot).
    修复: dispatch 每个 tool 前再次检查 budget, 0 时 break + scope 记录 +
    transcript 仍 append (部分 result 持久) + yield BudgetExhaustedEvent.
    """

    @pytest.mark.asyncio
    async def test_per_turn_budget_caps_multi_tool_response(self, mocker) -> None:
        """LLM 返 3 tool_uses 但 budget=1 → 只 dispatch 1 + BudgetExhaustedEvent."""
        from explain_engine.chat.loop import (
            BudgetExhaustedEvent,
            ToolUseEvent,
            query_loop,
        )
        from explain_engine.engines.simulation import AcceptanceReport

        _make_done_session("s_bbb00001")
        chat = ChatSession("s_bbb00001")
        chat.chat_state.budget_per_turn_remaining = 1
        chat.chat_state.budget_per_turn_limit = 1
        chat.chat_state.budget_per_session_remaining = 10
        chat.chat_state.budget_per_session_limit = 10

        fake_report = AcceptanceReport(
            avg_consistency=0.9, avg_essentialness=0.5,
            per_l1={}, per_l2={}, weak_chain_l1s=[],
            lowest_l1=None, consistency_spread=0.0,
            essentialness_spread=0.0, rollout_coverage=1.0, missing_l0=[],
        )
        mocker.patch(
            "explain_engine.chat.tools.aggregate_acceptance",
            return_value=fake_report,
        )

        llm = _FakeLLMClient([
            _FakeLLMResponse(
                text="ok",
                tool_uses=[
                    {"id": "tu_1", "name": "check", "input": {"target_id": None}},
                    {"id": "tu_2", "name": "check", "input": {"target_id": None}},
                    {"id": "tu_3", "name": "check", "input": {"target_id": None}},
                ],
                stop_reason="tool_use",
            ),
        ])

        events = []
        async for ev in query_loop(chat, llm):
            events.append(ev)

        tool_use_events = [e for e in events if isinstance(e, ToolUseEvent)]
        budget_events = [e for e in events if isinstance(e, BudgetExhaustedEvent)]
        # 只 1 tool 被 dispatch (budget 初始=1)
        assert len(tool_use_events) == 1
        # BudgetExhaustedEvent 被 yield
        assert len(budget_events) == 1
        assert budget_events[0].scope == "per_turn"
        # budget 不为负
        assert chat.chat_state.budget_per_turn_remaining == 0

    @pytest.mark.asyncio
    async def test_per_session_budget_caps_multi_tool_response(self, mocker) -> None:
        """与上类似, 但 per-session budget 先到 0."""
        from explain_engine.chat.loop import (
            BudgetExhaustedEvent,
            ToolUseEvent,
            query_loop,
        )
        from explain_engine.engines.simulation import AcceptanceReport

        _make_done_session("s_bbb00002")
        chat = ChatSession("s_bbb00002")
        chat.chat_state.budget_per_turn_remaining = 10
        chat.chat_state.budget_per_turn_limit = 10
        chat.chat_state.budget_per_session_remaining = 2
        chat.chat_state.budget_per_session_limit = 2

        fake_report = AcceptanceReport(
            avg_consistency=0.9, avg_essentialness=0.5,
            per_l1={}, per_l2={}, weak_chain_l1s=[],
            lowest_l1=None, consistency_spread=0.0,
            essentialness_spread=0.0, rollout_coverage=1.0, missing_l0=[],
        )
        mocker.patch(
            "explain_engine.chat.tools.aggregate_acceptance",
            return_value=fake_report,
        )

        llm = _FakeLLMClient([
            _FakeLLMResponse(
                text="",
                tool_uses=[
                    {"id": "tu_1", "name": "check", "input": {"target_id": None}},
                    {"id": "tu_2", "name": "check", "input": {"target_id": None}},
                    {"id": "tu_3", "name": "check", "input": {"target_id": None}},
                    {"id": "tu_4", "name": "check", "input": {"target_id": None}},
                ],
                stop_reason="tool_use",
            ),
        ])

        events = []
        async for ev in query_loop(chat, llm):
            events.append(ev)

        tool_use_events = [e for e in events if isinstance(e, ToolUseEvent)]
        budget_events = [e for e in events if isinstance(e, BudgetExhaustedEvent)]
        # 只 2 dispatched (session budget 初=2)
        assert len(tool_use_events) == 2
        assert len(budget_events) == 1
        assert budget_events[0].scope == "per_session"
        assert chat.chat_state.budget_per_session_remaining == 0
