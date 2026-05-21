"""Phase 12 (2026-05-19): /show 4 section layout test."""

import pytest

from explain_engine.chat.slash_commands import dispatch_slash


def _make_session_with_graph(sid: str, *, l0: int = 3, l1: int = 2, l2: int = 0):
    """Helper: 建 ChatSession + 含 L0/L1/L2 node 的 graph + 1 manifests_as edge.

    用 _make_done_session pattern (复用 tests/test_chat_session.py).
    清空 fixture 默认 2-node graph 后按 l0/l1/l2 重建.
    """
    from explain_engine.chat.session import ChatSession
    from explain_engine.schema.edges import RelationEdge
    from explain_engine.schema.nodes import VariableNode
    from tests.test_chat_session import _make_done_session

    _make_done_session(sid)
    chat = ChatSession(sid)
    g = chat.state.graph

    # Clear fixture's default graph 重建
    for nid in list(g.nodes):
        g.remove_node(nid)

    for i in range(l0):
        g.add_node(VariableNode(
            id=f"p_{i+1:03d}", name=f"observation_{i+1}",
            description=f"obs desc {i+1}",
            abstraction_level=0, confidence=0.8, epistemic="observation",
        ))
    for i in range(l1):
        g.add_node(VariableNode(
            id=f"c_{i+1:03d}", name=f"concept_{i+1}",
            description=f"concept desc {i+1}",
            abstraction_level=1, confidence=0.7, epistemic="insight",
        ))
    for i in range(l2):
        g.add_node(VariableNode(
            id=f"d_{i+1:03d}", name=f"driver_{i+1}",
            description=f"driver desc {i+1}",
            abstraction_level=2, confidence=0.6, epistemic="inference",
        ))
    if l1 > 0 and l0 > 0:
        g.add_edge(RelationEdge(
            id="e_001", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.85,
            mechanism_description="c_001 manifests as p_001",
        ))
    return chat


class TestShowLayout:
    """Phase 15: section header + L0/L1/L2 名称 全中文化."""

    @pytest.mark.asyncio
    async def test_four_section_headers(self):
        chat = _make_session_with_graph("s_5409a001")
        events = await dispatch_slash(chat, "/show")
        content = events[0].content
        assert "=== 当前 session ===" in content
        assert "=== 因果图" in content
        assert "=== 因果关系" in content
        assert "=== 接受度评估 ===" in content

    @pytest.mark.asyncio
    async def test_node_tree_grouped_by_level(self):
        chat = _make_session_with_graph("s_5409a002", l0=2, l1=1, l2=1)
        events = await dispatch_slash(chat, "/show")
        content = events[0].content
        assert "[现象] (2)" in content
        assert "[归纳出的模式] (1)" in content
        assert "[深层原因] (1)" in content

    @pytest.mark.asyncio
    async def test_node_lines_show_epi_and_conf(self):
        chat = _make_session_with_graph("s_5409a003")
        events = await dispatch_slash(chat, "/show")
        content = events[0].content
        assert "[obs 0.80]" in content
        assert "[ins 0.70]" in content

    @pytest.mark.asyncio
    async def test_edge_section_grouped_by_type(self):
        chat = _make_session_with_graph("s_5409a004")
        events = await dispatch_slash(chat, "/show")
        content = events[0].content
        # raw key 保留 + 中文注释附加
        assert "manifests_as" in content
        assert "体现为" in content

    @pytest.mark.asyncio
    async def test_empty_graph(self):
        """0 nodes → Graph section '(空)', Edges section '(无因果关系)'."""
        from explain_engine.chat.session import ChatSession
        from tests.test_chat_session import _make_done_session
        _make_done_session("s_5409a005")
        chat = ChatSession("s_5409a005")
        for nid in list(chat.state.graph.nodes):
            chat.state.graph.remove_node(nid)
        events = await dispatch_slash(chat, "/show")
        content = events[0].content
        assert "(空)" in content or "(无因果关系)" in content

    @pytest.mark.asyncio
    async def test_weak_marker_priority_lifecycle_over_weak(self):
        """同节点 lifecycle=stale 且在 weak_chain_l1s → 显 [stale] 不显 (weak)."""
        chat = _make_session_with_graph("s_5409a006", l1=1)
        chat.state.graph.nodes["c_001"].lifecycle_state = "stale"

        from explain_engine.engines.simulation import AcceptanceReport
        def fake_aggregate(state):
            return AcceptanceReport(
                avg_consistency=0.5, avg_essentialness=0.0,
                weak_chain_l1s=["c_001"], rollout_coverage=1.0,
            )

        import explain_engine.engines.simulation as sim
        original = sim.aggregate_acceptance
        sim.aggregate_acceptance = fake_aggregate
        try:
            events = await dispatch_slash(chat, "/show")
            content = events[0].content
            l1_lines = [ln for ln in content.split("\n") if "c_001" in ln and "「" in ln]
            assert l1_lines
            assert "[stale]" in l1_lines[0]
            assert "(weak)" not in l1_lines[0]
        finally:
            sim.aggregate_acceptance = original

    @pytest.mark.asyncio
    async def test_multisignal_at_bottom(self):
        chat = _make_session_with_graph("s_5409a007")
        events = await dispatch_slash(chat, "/show")
        content = events[0].content
        ms_idx = content.find("=== 接受度评估 ===")
        eg_idx = content.find("=== 因果关系")
        assert ms_idx > eg_idx > 0

    @pytest.mark.asyncio
    async def test_aggregate_failure_does_not_crash(self):
        """aggregate_acceptance raise → 接受度评估 section fallback, /show 不 crash."""
        chat = _make_session_with_graph("s_5409a008")
        import explain_engine.engines.simulation as sim
        original = sim.aggregate_acceptance

        def boom(state):
            raise RuntimeError("boom")

        sim.aggregate_acceptance = boom
        try:
            events = await dispatch_slash(chat, "/show")
            content = events[0].content
            assert "接受度评估失败" in content
            assert "=== 当前 session ===" in content
        finally:
            sim.aggregate_acceptance = original
