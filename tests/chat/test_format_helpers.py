"""Phase 12 (2026-05-19): /show + /graph detail helpers test."""


class TestFormatEpiShort:
    def test_fact(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("fact") == "fact"

    def test_observation(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("observation") == "obs"

    def test_inference(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("inference") == "inf"

    def test_insight(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("insight") == "ins"

    def test_speculation(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        assert _format_epi_short("speculation") == "spec"

    def test_unknown_returns_input(self):
        from explain_engine.chat.slash_commands import _format_epi_short
        # 防御: 未知 epi 返原值 (新加 Epistemic literal 时不 crash)
        assert _format_epi_short("emerging") == "emerging"


class TestFormatNodeBrief:
    """新行格式: `{id} [{epi_short} {conf:.2f}] {marker?}「{name}」: {desc[:60]}...?`"""

    def _make_state_with_node(self, **node_kwargs):
        """Helper: build minimal CognitiveState with 1 VariableNode.

        `_format_node_brief` 仅读 `state.graph.nodes.get(nid)`, 用 chat.state
        (= CognitiveState, 见 ChatSession.__init__) 即可.
        """
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.nodes import VariableNode
        from explain_engine.schema.state import CognitiveState
        g = ExplanationGraph(root_question="Q")
        node = VariableNode(**node_kwargs)
        g.add_node(node)
        state = CognitiveState(graph=g, budget_remaining=10, root_question="Q")
        return state

    def test_basic_format_includes_epi_conf_name(self):
        from explain_engine.chat.slash_commands import _format_node_brief
        state = self._make_state_with_node(
            id="p_001", name="房价上涨", description="一线城市房价持续上涨",
            abstraction_level=0, confidence=0.85, epistemic="observation",
        )
        out = _format_node_brief(state, "p_001")
        assert "p_001" in out
        assert "[obs 0.85]" in out
        assert "「房价上涨」" in out
        assert "一线城市房价持续上涨" in out

    def test_desc_truncation_at_60(self):
        from explain_engine.chat.slash_commands import _format_node_brief
        long_desc = "x" * 100
        state = self._make_state_with_node(
            id="p_002", name="n", description=long_desc,
            abstraction_level=0, confidence=0.5, epistemic="fact",
        )
        out = _format_node_brief(state, "p_002")
        assert "..." in out
        # desc 部分恰好 60 char + "..."
        assert out.endswith("x" * 60 + "...")

    def test_marker_weak(self):
        from explain_engine.chat.slash_commands import _format_node_brief
        state = self._make_state_with_node(
            id="c_001", name="n", description="d",
            abstraction_level=1, confidence=0.5, epistemic="insight",
        )
        out = _format_node_brief(state, "c_001", weak=True)
        assert "(weak)" in out

    def test_marker_stale(self):
        from explain_engine.chat.slash_commands import _format_node_brief
        state = self._make_state_with_node(
            id="c_002", name="n", description="d",
            abstraction_level=1, confidence=0.5, epistemic="insight",
            lifecycle_state="stale",
        )
        out = _format_node_brief(state, "c_002")
        assert "[stale]" in out

    def test_marker_decayed(self):
        from explain_engine.chat.slash_commands import _format_node_brief
        state = self._make_state_with_node(
            id="c_003", name="n", description="d",
            abstraction_level=1, confidence=0.5, epistemic="insight",
            lifecycle_state="decayed",
        )
        out = _format_node_brief(state, "c_003")
        assert "[decayed]" in out

    def test_marker_priority_decayed_over_weak(self):
        """lifecycle > weak — decayed + weak 同时只显 [decayed]."""
        from explain_engine.chat.slash_commands import _format_node_brief
        state = self._make_state_with_node(
            id="c_004", name="n", description="d",
            abstraction_level=1, confidence=0.5, epistemic="insight",
            lifecycle_state="decayed",
        )
        out = _format_node_brief(state, "c_004", weak=True)
        assert "[decayed]" in out
        assert "(weak)" not in out

    def test_marker_priority_stale_over_weak(self):
        from explain_engine.chat.slash_commands import _format_node_brief
        state = self._make_state_with_node(
            id="c_005", name="n", description="d",
            abstraction_level=1, confidence=0.5, epistemic="insight",
            lifecycle_state="stale",
        )
        out = _format_node_brief(state, "c_005", weak=True)
        assert "[stale]" in out
        assert "(weak)" not in out

    def test_missing_node_fallback(self):
        """Fix 3 兼容: nid 不在 graph 返原 '(节点不在 graph)' fallback."""
        from explain_engine.chat.slash_commands import _format_node_brief
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.state import CognitiveState
        state = CognitiveState(
            graph=ExplanationGraph(root_question="Q"),
            budget_remaining=10,
            root_question="Q",
        )
        out = _format_node_brief(state, "p_999")
        assert "p_999" in out
        assert "节点不在 graph" in out


class TestFormatEdgeBrief:
    """Edge 行格式: `{source} → {target} [{conf:.2f}] {mechanism[:max_mech]}...?`"""

    def _make_edge(self, **kwargs):
        from explain_engine.schema.edges import RelationEdge
        return RelationEdge(**kwargs)

    def test_basic_format(self):
        from explain_engine.chat.slash_commands import _format_edge_brief
        edge = self._make_edge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.85,
            mechanism_description="经济不安全感在房价感受层表现为购房意愿降",
        )
        out = _format_edge_brief(edge)
        assert "c_001" in out
        assert "→" in out
        assert "p_001" in out
        assert "[0.85]" in out
        assert "经济不安全感在房价感受层表现为购房意愿降" in out

    def test_mechanism_truncation(self):
        from explain_engine.chat.slash_commands import _format_edge_brief
        long_mech = "y" * 100
        edge = self._make_edge(
            id="e_002", source_node="c_001", target_node="c_002",
            relation_type="causes", confidence=0.5,
            mechanism_description=long_mech,
        )
        out = _format_edge_brief(edge, max_mech=60)
        assert "..." in out
        assert out.endswith("y" * 60 + "...")

    def test_relation_type_not_in_line(self):
        """type 已在 section header 分组, 行内不重复显."""
        from explain_engine.chat.slash_commands import _format_edge_brief
        edge = self._make_edge(
            id="e_003", source_node="a", target_node="b",
            relation_type="amplifies", confidence=0.7,
            mechanism_description="m",
        )
        out = _format_edge_brief(edge)
        assert "amplifies" not in out
