"""Phase 9 Wave D.2: post-turn hooks tests.

Three hooks fire after each TurnComplete (Claude Code PostSamplingHook pattern):
1. reflect_post_turn (sync, 0 LLM) — runs reflection.reflect, returns hint str
2. lifecycle_post_turn (sync, 0 LLM) — runs lifecycle.update_lifecycle
3. session_memory_writer (async fire-and-forget, 1 LLM call every 5 turns)
"""

from __future__ import annotations

import pytest

from explain_engine.chat.hooks import (
    SESSION_MEMORY_TRIGGER_INTERVAL,
    lifecycle_post_turn,
    reflect_post_turn,
    session_memory_writer,
)
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state_for_reflect() -> CognitiveState:
    """Build state with a weak L1 chain → reflect returns expand-downward."""
    g = ExplanationGraph(root_question="q")
    g.add_node(VariableNode(
        id="c_001", name="weak", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="p", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.1,  # low → weak chain
        mechanism_description="m",
    ))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="q",
        insight_candidates=["c_001"],
    )


# ──────────────────────────────────────────────────────────────────────
# 1. reflect_post_turn
# ──────────────────────────────────────────────────────────────────────


class TestReflectPostTurn:
    def test_weak_chain_returns_expand_downward_hint(self) -> None:
        """Weak L1 → reflect 返 expand-downward → hint 含 'expand'."""
        state = _make_state_for_reflect()
        hint = reflect_post_turn(state)
        assert hint is not None
        assert "expand" in hint

    def test_strong_chain_returns_none_or_stop(self) -> None:
        """All strong → reflect 返 continue (hint=None) 或 stop (graph 收敛)."""
        g = ExplanationGraph(root_question="q")
        g.add_node(VariableNode(
            id="c_001", name="strong", description="d",
            abstraction_level=1, confidence=0.9, epistemic="insight",
        ))
        g.add_node(VariableNode(
            id="p_001", name="p", description="d",
            abstraction_level=0, confidence=0.9, epistemic="observation",
        ))
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.9,
            mechanism_description="m",
        ))
        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
        hint = reflect_post_turn(state)
        # Acceptable: None for continue, or "stop" for stale (CONSISTENCY_STALE_TICKS=3
        # 但 state.tick=0, last_reflection_change_tick=0 → 0-0 < 3 → continue → None.
        assert hint is None or "stop" in hint

    def test_empty_graph_returns_none(self) -> None:
        """空 graph → reflect 直接 continue → hint=None."""
        g = ExplanationGraph(root_question="q")
        state = CognitiveState(graph=g, budget_remaining=10, root_question="q")
        hint = reflect_post_turn(state)
        assert hint is None


# ──────────────────────────────────────────────────────────────────────
# 2. lifecycle_post_turn
# ──────────────────────────────────────────────────────────────────────


class TestLifecyclePostTurn:
    def test_returns_changes_dict(self) -> None:
        """lifecycle_post_turn 返 dict (变更日志)."""
        state = _make_state_for_reflect()
        changes = lifecycle_post_turn(state, current_tick=1)
        assert isinstance(changes, dict)

    def test_low_fitness_node_marks_stale(self) -> None:
        """fitness < STALE_THRESHOLD → active → stale."""
        state = _make_state_for_reflect()
        # Force low fitness so c_001 → stale.
        # 加一个 high-degree sibling L1 让 c_001 centrality 占比降低 (从 1.0 → 0.5),
        # 否则单一 L1 拿满 centrality 加 0.3 分, 正好等于 STALE_THRESHOLD=0.3
        # (< 不触发 → 不进 stale).
        from explain_engine.schema.edges import RelationEdge
        from explain_engine.schema.nodes import VariableNode
        state.graph.add_node(VariableNode(
            id="c_002", name="other", description="d",
            abstraction_level=1, confidence=0.9, epistemic="insight",
        ))
        state.graph.add_node(VariableNode(
            id="p_002", name="p2", description="d",
            abstraction_level=0, confidence=0.9, epistemic="observation",
        ))
        state.graph.add_node(VariableNode(
            id="p_003", name="p3", description="d",
            abstraction_level=0, confidence=0.9, epistemic="observation",
        ))
        state.graph.add_edge(RelationEdge(
            id="e_002", source_node="c_002", target_node="p_002",
            relation_type="manifests_as", confidence=0.9, mechanism_description="m",
        ))
        state.graph.add_edge(RelationEdge(
            id="e_003", source_node="c_002", target_node="p_003",
            relation_type="manifests_as", confidence=0.9, mechanism_description="m",
        ))
        state.graph.nodes["c_001"].activation = 0.0
        state.graph.nodes["c_001"].stability = 0.0
        # 注入低 per_l1 explanatory power
        from explain_engine.engines.simulation import AcceptanceReport
        state.last_acceptance_report = AcceptanceReport(
            avg_consistency=0.0, avg_essentialness=0.0,
            per_l1={"c_001": 0.0, "c_002": 0.9},
        )
        changes = lifecycle_post_turn(state, current_tick=1)
        assert state.graph.nodes["c_001"].lifecycle_state == "stale"
        assert changes.get("c_001") == "stale"


# ──────────────────────────────────────────────────────────────────────
# 3. session_memory_writer (async)
# ──────────────────────────────────────────────────────────────────────


class TestSessionMemoryWriter:
    @pytest.mark.asyncio
    async def test_does_not_fire_on_non_trigger_turn(self, mocker) -> None:
        """turn_count % 5 != 0 → 不调 LLM, 不写 memory.md."""
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        _make_done_session("s_aaa00001")
        chat = ChatSession("s_aaa00001")
        chat.chat_state.turn_count = 3  # 3 % 5 != 0
        llm = mocker.AsyncMock()
        llm.chat = mocker.AsyncMock()
        await session_memory_writer(chat, llm)
        # llm.chat should NOT be called
        llm.chat.assert_not_called()
        # memory.md unchanged (still empty)
        assert chat.memory_md == ""

    @pytest.mark.asyncio
    async def test_fires_on_trigger_turn_with_transcript(self, mocker) -> None:
        """turn_count == 5 且有 transcript → 调 LLM 总结, 写 memory.md."""
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        _make_done_session("s_aaa00002")
        chat = ChatSession("s_aaa00002")
        chat.chat_state.turn_count = 5
        # Mock transcript with prefix (要够多 message 才有 prefix 可 summarize)
        chat.transcript = [
            {"role": "user", "content": "hi", "turn": 0},
            {"role": "assistant", "content": "ok", "turn": 0},
            {"role": "user", "content": "more", "turn": 1},
            {"role": "assistant", "content": "still", "turn": 1},
            {"role": "user", "content": "now", "turn": 4},
        ]

        # Mock LLM .chat to return summary
        class _FakeResp:
            text = "# Summary\nblah"
            parsed = None

        llm = mocker.AsyncMock()
        llm.chat = mocker.AsyncMock(return_value=_FakeResp())
        await session_memory_writer(chat, llm)
        # memory.md should be written
        assert chat.memory_md == "# Summary\nblah"
        llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_llm_error_gracefully(self, mocker) -> None:
        """LLM raises → log warning, 不传播."""
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        _make_done_session("s_aaa00003")
        chat = ChatSession("s_aaa00003")
        chat.chat_state.turn_count = 5
        chat.transcript = [
            {"role": "user", "content": "x", "turn": 0},
            {"role": "assistant", "content": "y", "turn": 0},
            {"role": "user", "content": "z", "turn": 1},
            {"role": "assistant", "content": "w", "turn": 1},
            {"role": "user", "content": "now", "turn": 4},
        ]
        llm = mocker.AsyncMock()
        llm.chat = mocker.AsyncMock(side_effect=RuntimeError("LLM down"))
        # Should NOT raise
        await session_memory_writer(chat, llm)
        # memory.md unchanged (LLM 失败 → 不写)
        assert chat.memory_md == ""

    @pytest.mark.asyncio
    async def test_turn_zero_skips(self, mocker) -> None:
        """turn_count == 0 → 不调 LLM (没东西可 summarize)."""
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        _make_done_session("s_aaa00004")
        chat = ChatSession("s_aaa00004")
        chat.chat_state.turn_count = 0
        llm = mocker.AsyncMock()
        llm.chat = mocker.AsyncMock()
        await session_memory_writer(chat, llm)
        llm.chat.assert_not_called()


# ──────────────────────────────────────────────────────────────────────
# 4. constants
# ──────────────────────────────────────────────────────────────────────


class TestTriggerInterval:
    def test_default_is_5(self) -> None:
        assert SESSION_MEMORY_TRIGGER_INTERVAL == 5


# ──────────────────────────────────────────────────────────────────────
# 5. Wave D.2 fix · regression tests (C1 + I1 + I2)
# ──────────────────────────────────────────────────────────────────────


class TestHookOrderingC1:
    """C1 regression: lifecycle MUST fire before reflect (matches runtime.py)."""

    @pytest.mark.asyncio
    async def test_lifecycle_called_before_reflect_in_handle_user_input(self, mocker):
        from unittest.mock import AsyncMock

        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session

        _make_done_session("s_0fde0001")
        chat = ChatSession("s_0fde0001")
        call_order = []

        def _track_lifecycle(state, current_tick):
            call_order.append("lifecycle")
            return {}

        def _track_reflect(state):
            call_order.append("reflect")
            return None

        mocker.patch(
            "explain_engine.chat.session.lifecycle_post_turn",
            side_effect=_track_lifecycle,
        )
        mocker.patch(
            "explain_engine.chat.session.reflect_post_turn",
            side_effect=_track_reflect,
        )

        # Fake LLM that returns end_turn immediately (no tools)
        class _R:
            text = "ok"
            tool_uses: list = []  # noqa: RUF012 (test fixture, single-class instance)
            stop_reason = "end_turn"
        fake_llm = AsyncMock()
        fake_llm.chat_with_tools = AsyncMock(return_value=_R())

        events = []
        async for ev in chat.handle_user_input("hi", llm=fake_llm):
            events.append(ev)

        # C1: lifecycle MUST be called before reflect
        assert call_order == ["lifecycle", "reflect"]


class TestHookExceptionAtomicity:
    """I1 regression: hook failure doesn't abort persist()."""

    @pytest.mark.asyncio
    async def test_hook_exception_still_persists(self, mocker, caplog):
        import logging
        from unittest.mock import AsyncMock

        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session

        _make_done_session("s_a10c0001")
        chat = ChatSession("s_a10c0001")

        # Mock reflect_post_turn to raise
        mocker.patch(
            "explain_engine.chat.session.reflect_post_turn",
            side_effect=RuntimeError("synthetic"),
        )

        class _R:
            text = "ok"
            tool_uses: list = []  # noqa: RUF012 (test fixture, single-class instance)
            stop_reason = "end_turn"
        fake_llm = AsyncMock()
        fake_llm.chat_with_tools = AsyncMock(return_value=_R())

        with caplog.at_level(logging.WARNING):
            events = []
            async for ev in chat.handle_user_input("hi", llm=fake_llm):
                events.append(ev)

        # turn_count was bumped + persisted despite hook crash
        chat2 = ChatSession("s_a10c0001")
        assert chat2.chat_state.turn_count >= 1
        # Warning logged
        assert any("synthetic" in r.message for r in caplog.records)


class TestACloseAwaitsBackgroundTasks:
    """I2 regression: aclose() waits for in-flight background tasks."""

    @pytest.mark.asyncio
    async def test_aclose_awaits_background_tasks(self):
        import asyncio

        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session

        _make_done_session("s_ac10ce01")
        chat = ChatSession("s_ac10ce01")

        # Inject a slow background task
        complete = asyncio.Event()

        async def slow_task():
            await asyncio.sleep(0.05)
            complete.set()

        task = asyncio.create_task(slow_task())
        chat._background_tasks.add(task)
        task.add_done_callback(chat._background_tasks.discard)

        await chat.aclose()
        assert complete.is_set()
