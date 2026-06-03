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
    """Mimics Anthropic chat_with_tools response: .text / .tool_uses / .stop_reason.

    F.4: raw_content_blocks 默认空 — query_loop fallback 走老重建 (text + tool_uses)
    路径, 兼容现有测试.
    """

    def __init__(
        self,
        text="",
        tool_uses=None,
        stop_reason="end_turn",
        raw_content_blocks=None,
    ):
        self.text = text
        self.tool_uses = tool_uses or []
        self.stop_reason = stop_reason
        self.raw_content_blocks = raw_content_blocks or []


class _FakeLLMClient:
    """Scripted LLM: pops responses in order on each chat_with_tools call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0

    async def chat_with_tools(self, system, messages, tools, on_delta=None):
        # Phase 20.3: 接受 on_delta 但不 emit — 模拟"非流式 provider", query_loop
        # 走 fallback 发整段 AssistantTextEvent (现有断言据此). 流式逐 delta 路径
        # 由 _StreamingFakeLLMClient 专门覆盖.
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
    """2026-05-20 hotfix: query_loop 删了 mid-loop budget enforce. 即便
    finite limit 已用尽, query_loop 仍正常跑当前 turn (LLM 自然 stop 即结束).
    turn-start enforce 由 ChatSession.handle_user_input 负责, 见
    TestHandleUserInputBudgetEnforcement."""

    @pytest.mark.asyncio
    async def test_finite_budget_exhausted_does_not_break_mid_turn(self) -> None:
        """per_session_remaining=0 + finite limit → query_loop 不打断, 跑完 turn."""
        from explain_engine.chat.loop import (
            BudgetExhaustedEvent,
            TurnCompleteEvent,
            query_loop,
        )

        _make_done_session("s_aaaa0005")
        chat = ChatSession("s_aaaa0005")
        # finite limit + 已用尽
        chat.chat_state.budget_per_turn_limit = 5
        chat.chat_state.budget_per_turn_remaining = 0
        chat.chat_state.budget_per_session_limit = 10
        chat.chat_state.budget_per_session_remaining = 0

        llm = _FakeLLMClient([
            _FakeLLMResponse(text="hi", tool_uses=[], stop_reason="end_turn"),
        ])
        events = []
        async for ev in query_loop(chat, llm):
            events.append(ev)
        # LLM 仍被调 (不像老逻辑直接 return)
        assert llm.call_count == 1
        # 没 BudgetExhaustedEvent
        assert not any(isinstance(e, BudgetExhaustedEvent) for e in events)
        # 正常 TurnComplete
        assert any(isinstance(e, TurnCompleteEvent) for e in events)


class TestHandleUserInputBudgetEnforcement:
    """2026-05-20 hotfix: turn-start enforce 从 query_loop 搬到
    ChatSession.handle_user_input. session_exhausted → 拒受 user 输入 (yield
    BudgetExhaustedEvent + return), 不调 query_loop. 默 unlimited 不 trigger."""

    @pytest.mark.asyncio
    async def test_session_exhausted_refuses_new_turn(self) -> None:
        from explain_engine.chat.loop import BudgetExhaustedEvent

        _make_done_session("s_aaaa0006")
        chat = ChatSession("s_aaaa0006")
        chat.chat_state.budget_per_session_limit = 10
        chat.chat_state.budget_per_session_remaining = 0  # 已用尽

        llm = _FakeLLMClient([_FakeLLMResponse(text="x")])
        events = []
        async for ev in chat.handle_user_input("hello", llm=llm):
            events.append(ev)
        # LLM 没被调
        assert llm.call_count == 0
        # turn_count 没 bump (turn 没真启动)
        assert chat.chat_state.turn_count == 0
        # yield BudgetExhaustedEvent(per_session)
        budget_evs = [e for e in events if isinstance(e, BudgetExhaustedEvent)]
        assert len(budget_evs) == 1
        assert budget_evs[0].scope == "per_session"

    @pytest.mark.asyncio
    async def test_default_unlimited_never_refuses(self) -> None:
        """新 default (limit=0) → 任意 turn 都被接受, 即便 remaining<<0."""
        _make_done_session("s_aaaa0007")
        chat = ChatSession("s_aaaa0007")
        # default unlimited; 模拟 historical heavy use → remaining 很负
        chat.chat_state.budget_per_session_remaining = -10000
        chat.chat_state.budget_per_session_limit = 0  # unlimited

        llm = _FakeLLMClient([
            _FakeLLMResponse(text="ok", tool_uses=[], stop_reason="end_turn"),
        ])
        events = []
        async for ev in chat.handle_user_input("hello", llm=llm):
            events.append(ev)
        assert llm.call_count == 1
        assert chat.chat_state.turn_count == 1


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


class TestMultiToolNoMidLoopBudgetCap:
    """2026-05-20 hotfix: 老 TestBudgetMidLoopExhaustion 测的 mid-loop cap
    行为已删. 新行为: LLM 一次 response 含 N tool_uses → 全部 dispatch,
    即便 budget overshoot 也不打断. budget.consume() 仍 track (走负值)."""

    @pytest.mark.asyncio
    async def test_all_tools_dispatched_even_when_budget_overshoots(
        self, mocker,
    ) -> None:
        """LLM 返 3 tool_uses, budget=1 finite → 全 3 dispatched, remaining 走负."""
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
            _FakeLLMResponse(text="done", tool_uses=[], stop_reason="end_turn"),
        ])

        events = []
        async for ev in query_loop(chat, llm):
            events.append(ev)

        tool_use_events = [e for e in events if isinstance(e, ToolUseEvent)]
        budget_events = [e for e in events if isinstance(e, BudgetExhaustedEvent)]
        # 全 3 tool dispatched (mid-loop cap 已删)
        assert len(tool_use_events) == 3
        # 没 BudgetExhaustedEvent (mid-loop break 也删了)
        assert budget_events == []
        # consume() 把 remaining 扣到 -2 (initial=1, dispatched 3 → 1-3=-2)
        assert chat.chat_state.budget_per_turn_remaining == -2
