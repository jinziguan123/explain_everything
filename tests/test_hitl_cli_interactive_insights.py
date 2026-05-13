"""HITL 2 review_insights 测试。"""

from unittest.mock import patch

from rich.console import Console

from explain_engine.hitl.cli_interactive import review_insights
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _build_state_with_candidates() -> tuple[CognitiveState, dict[str, float]]:
    state = CognitiveState.bootstrap("q", budget=20)
    # 2 concrete
    for i in (1, 2):
        state.graph.add_node(
            VariableNode(
                id=f"p_{i:03d}", name=f"p{i}", description="",
                abstraction_level=0, confidence=0.7, epistemic="observation",
            )
        )
    # 3 candidates
    for i, name in enumerate(["c_001", "c_002", "c_003"], start=1):
        state.graph.add_node(
            VariableNode(
                id=name, name=f"abs_{i}", description=f"def_{i}",
                abstraction_level=1, confidence=0.7, epistemic="insight",
                source="llm",
            )
        )
        state.graph.add_edge(
            RelationEdge(
                id=f"e_{i:03d}", source_node=name, target_node="p_001",
                relation_type="manifests_as", confidence=0.7,
                mechanism_description=f"mech_{i}",
            )
        )
    state.insight_candidates = ["c_001", "c_002", "c_003"]
    gains = {"c_001": 0.6, "c_002": 0.5, "c_003": 0.3}
    return state, gains


class TestReviewInsights:
    def test_keep_all(self) -> None:
        state, gains = _build_state_with_candidates()
        with patch("rich.prompt.Prompt.ask", side_effect=["k", "k", "k"]):
            review_insights(state, gains, console=Console(file=None))
        # 3 abstract 全留
        assert sum(1 for n in state.graph.nodes.values() if n.abstraction_level == 1) == 3
        # insight_candidates 已清空
        assert state.insight_candidates == []

    def test_drop_one(self) -> None:
        state, gains = _build_state_with_candidates()
        with patch("rich.prompt.Prompt.ask", side_effect=["k", "d", "k"]):
            review_insights(state, gains, console=Console(file=None))
        assert "c_002" not in state.graph.nodes
        assert "e_002" not in state.graph.edges  # 级联
        assert "c_001" in state.graph.nodes
        assert "c_003" in state.graph.nodes

    def test_drop_all(self) -> None:
        state, gains = _build_state_with_candidates()
        with patch("rich.prompt.Prompt.ask", side_effect=["d", "d", "d"]):
            review_insights(state, gains, console=Console(file=None))
        assert sum(1 for n in state.graph.nodes.values() if n.abstraction_level == 1) == 0

    def test_edit_updates_source(self) -> None:
        state, gains = _build_state_with_candidates()
        # edit: choice="e", new name, new description
        with patch(
            "rich.prompt.Prompt.ask",
            side_effect=["e", "新名称", "新描述", "k", "k"],
        ):
            review_insights(state, gains, console=Console(file=None))
        assert state.graph.nodes["c_001"].name == "新名称"
        assert state.graph.nodes["c_001"].description == "新描述"
        assert state.graph.nodes["c_001"].source == "user"

    def test_view_full_then_keep(self) -> None:
        state, gains = _build_state_with_candidates()
        # v 后回到 prompt，再 k
        with patch(
            "rich.prompt.Prompt.ask",
            side_effect=["v", "k", "k", "k"],
        ):
            review_insights(state, gains, console=Console(file=None))
        assert "c_001" in state.graph.nodes  # 被 keep
