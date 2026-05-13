"""CognitiveState test."""

import pytest

from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState


class TestCognitiveState:
    def test_create_from_question(self):
        s = CognitiveState.bootstrap(question="why?", budget=15)
        assert s.root_question == "why?"
        assert s.budget_remaining == 15
        assert s.tick == 0
        assert s.last_gain_tick == 0
        assert s.insight_candidates == []
        assert isinstance(s.graph, ExplanationGraph)
        assert s.graph.root_question == "why?"

    def test_tick_advances(self):
        s = CognitiveState.bootstrap(question="why?", budget=3)
        s.advance_tick()
        assert s.tick == 1
        assert s.budget_remaining == 2

    def test_advance_below_zero_rejected(self):
        s = CognitiveState.bootstrap(question="why?", budget=1)
        s.advance_tick()
        with pytest.raises(ValueError, match="budget exhausted"):
            s.advance_tick()

    def test_record_gain(self):
        s = CognitiveState.bootstrap(question="why?", budget=10)
        s.advance_tick()
        s.advance_tick()
        s.record_gain()
        assert s.last_gain_tick == s.tick

    def test_round_trip_json(self):
        s = CognitiveState.bootstrap(question="why?", budget=20)
        s.advance_tick()
        s.insight_candidates.append("n_abs_001")
        d = s.to_dict()
        restored = CognitiveState.from_dict(d)
        assert restored.tick == s.tick
        assert restored.budget_remaining == s.budget_remaining
        assert restored.insight_candidates == s.insight_candidates

    def test_from_dict_invalid_raises(self):
        with pytest.raises(ValueError, match="invalid state dict"):
            CognitiveState.from_dict({"graph": {}})  # 缺多个必需 key

    def test_negative_budget_rejected(self):
        with pytest.raises(ValueError, match="budget_remaining"):
            CognitiveState(
                graph=ExplanationGraph(root_question="why?"),
                budget_remaining=-1,
                root_question="why?",
            )

    def test_negative_tick_rejected(self):
        with pytest.raises(ValueError, match="tick"):
            CognitiveState(
                graph=ExplanationGraph(root_question="why?"),
                budget_remaining=5,
                root_question="why?",
                tick=-1,
            )
