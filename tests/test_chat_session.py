"""Wave C.1: ChatSession outer loop tests."""

import pytest

from explain_engine.chat.session import ChatEvent, ChatSession, ChatStateDict
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_done_session(sid: str = "s_001abcde") -> Session:
    """Create a done-stage session via SessionStore.save()."""
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
    meta.session_id = sid  # override generated id
    meta.stage = "done"
    sess = Session(meta=meta, state=state)
    store = SessionStore()
    store.save(sess)
    return sess


class TestChatSessionLoad:
    def test_load_existing_session_reads_metadata_graph(self) -> None:
        _make_done_session("s_001abcde")
        chat = ChatSession("s_001abcde")
        assert chat.state.graph.nodes["c_001"].name == "abs"
        assert chat.sid == "s_001abcde"
        # No transcript / memory yet for fresh chat
        assert chat.transcript == []
        assert chat.memory_md == ""
        # chat_state defaults
        assert chat.chat_state.turn_count == 0
        assert chat.chat_state.budget_per_session_remaining == 50

    def test_load_creates_empty_sidecar_state(self) -> None:
        """If no chat_state.json exists, default values used."""
        _make_done_session("s_002abcde")
        chat = ChatSession("s_002abcde")
        assert isinstance(chat.chat_state, ChatStateDict)
        assert chat.chat_state.budget_per_turn_remaining == 10

    def test_load_missing_session_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            ChatSession("s_missing0")


class TestChatSessionPersist:
    def test_persist_writes_chat_state(self) -> None:
        _make_done_session("s_003abcde")
        chat = ChatSession("s_003abcde")
        chat.chat_state.turn_count = 5
        chat.persist()
        # Re-load fresh and verify persisted
        chat2 = ChatSession("s_003abcde")
        assert chat2.chat_state.turn_count == 5

    def test_close_flushes(self) -> None:
        _make_done_session("s_004abcde")
        chat = ChatSession("s_004abcde")
        chat.chat_state.budget_per_session_remaining = 42
        chat.close()
        chat2 = ChatSession("s_004abcde")
        assert chat2.chat_state.budget_per_session_remaining == 42


class TestHandleUserInput:
    @pytest.mark.asyncio
    async def test_slash_command_routes_to_stub(self) -> None:
        _make_done_session("s_005abcde")
        chat = ChatSession("s_005abcde")
        events: list[ChatEvent] = []
        async for ev in chat.handle_user_input("/show"):
            events.append(ev)
        assert any(ev.type == "slash_unimplemented" for ev in events)

    @pytest.mark.asyncio
    async def test_non_slash_appends_to_transcript(self) -> None:
        _make_done_session("s_006abcde")
        chat = ChatSession("s_006abcde")
        events: list[ChatEvent] = []
        async for ev in chat.handle_user_input("hello"):
            events.append(ev)
        assert len(chat.transcript) == 1
        assert chat.transcript[0]["role"] == "user"
        assert chat.transcript[0]["content"] == "hello"
        # Turn count incremented
        assert chat.chat_state.turn_count == 1

    @pytest.mark.asyncio
    async def test_non_slash_resets_per_turn_budget(self) -> None:
        _make_done_session("s_007abcde")
        chat = ChatSession("s_007abcde")
        # Simulate prior turn ate budget
        chat.chat_state.budget_per_turn_remaining = 3
        events: list[ChatEvent] = []
        async for ev in chat.handle_user_input("test"):
            events.append(ev)
        # Reset to limit
        assert chat.chat_state.budget_per_turn_remaining == 10


class TestIsSlashCommand:
    def test_slash_prefix_detected(self) -> None:
        _make_done_session("s_008abcde")
        chat = ChatSession("s_008abcde")
        assert chat.is_slash_command("/help") is True
        assert chat.is_slash_command("  /show") is True  # strip
        assert chat.is_slash_command("hello") is False
        assert chat.is_slash_command("") is False
