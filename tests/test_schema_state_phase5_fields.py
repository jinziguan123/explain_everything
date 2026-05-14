"""CognitiveState Phase 5 新字段: last_gains + reasoning_trace。"""

from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState, TraceEntry


class TestCognitiveStatePhase5Fields:
    def test_last_gains_defaults_empty(self) -> None:
        state = CognitiveState.bootstrap("why", budget=10)
        assert state.last_gains == {}

    def test_reasoning_trace_defaults_empty(self) -> None:
        state = CognitiveState.bootstrap("why", budget=10)
        assert state.reasoning_trace == []

    def test_last_gains_assignable(self) -> None:
        state = CognitiveState.bootstrap("why", budget=10)
        state.last_gains = {"c_001": 0.42, "c_002": 0.55}
        assert state.last_gains["c_001"] == 0.42

    def test_reasoning_trace_appendable(self) -> None:
        state = CognitiveState.bootstrap("why", budget=10)
        entry = TraceEntry(
            tick=0, action="expand", target_node_id="c_001",
            gain_delta=0.5, llm_calls=1, timestamp="2026-05-13T10:00:00",
        )
        state.reasoning_trace.append(entry)
        assert len(state.reasoning_trace) == 1

    def test_to_dict_includes_new_fields(self) -> None:
        state = CognitiveState.bootstrap("why", budget=10)
        state.last_gains = {"c_001": 0.42}
        state.reasoning_trace.append(
            TraceEntry(
                tick=0, action="expand", target_node_id="c_001",
                gain_delta=0.5, llm_calls=1, timestamp="2026-05-13T10:00:00",
            )
        )
        d = state.to_dict()
        assert d["last_gains"] == {"c_001": 0.42}
        assert len(d["reasoning_trace"]) == 1
        assert d["reasoning_trace"][0]["tick"] == 0
        assert d["reasoning_trace"][0]["action"] == "expand"

    def test_from_dict_recovers_new_fields(self) -> None:
        d = {
            "graph": ExplanationGraph(root_question="why").to_dict(),
            "budget_remaining": 10,
            "root_question": "why",
            "active_frontier": [],
            "insight_candidates": [],
            "tick": 0,
            "last_gain_tick": 0,
            "last_gains": {"c_001": 0.42},
            "reasoning_trace": [
                {
                    "tick": 0,
                    "action": "expand",
                    "target_node_id": "c_001",
                    "gain_delta": 0.5,
                    "llm_calls": 1,
                    "timestamp": "2026-05-13T10:00:00",
                }
            ],
        }
        state = CognitiveState.from_dict(d)
        assert state.last_gains == {"c_001": 0.42}
        assert len(state.reasoning_trace) == 1
        assert state.reasoning_trace[0].action == "expand"

    def test_from_dict_phase4_compat(self) -> None:
        """旧 Phase 4 session 无新字段，默认空。"""
        d = {
            "graph": ExplanationGraph(root_question="why").to_dict(),
            "budget_remaining": 10,
            "root_question": "why",
        }
        state = CognitiveState.from_dict(d)
        assert state.last_gains == {}
        assert state.reasoning_trace == []
