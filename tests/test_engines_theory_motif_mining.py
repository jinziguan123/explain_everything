"""Phase 16: motif_mining — per-theme subgraph 抽取 + 调 gspan_mine."""

from explain_engine.engines.theory.theory import Theme


def _fake_graph(nodes, edges):
    """Helper: build ExplanationGraph-like, 节点用 lexicon global_id 当 id."""
    class FakeNode:
        def __init__(self, nid, name, abstraction_level=1):
            self.id = nid
            self.name = name
            self.abstraction_level = abstraction_level
            self.confidence = 0.7
            self.epistemic = "insight"
            self.lifecycle_state = "active"
            self.description = name

    class FakeEdge:
        def __init__(self, eid, src, tgt, rel):
            self.id = eid
            self.source_node = src
            self.target_node = tgt
            self.relation_type = rel
            self.confidence = 0.8
            self.mechanism_description = ""

    class FakeGraph:
        def __init__(self):
            self.nodes: dict = {}
            self.edges: dict = {}

        def add_node(self, n):
            self.nodes[n.id] = n

        def add_edge(self, e):
            self.edges[e.id] = e

    g = FakeGraph()
    for nid, name in nodes:
        g.add_node(FakeNode(nid, name))
    for i, (src, tgt, rel) in enumerate(edges):
        g.add_edge(FakeEdge(f"e_{i}", src, tgt, rel))
    return g


class TestFindMotifsPerTheme:
    def test_empty_sessions_returns_empty(self):
        from explain_engine.engines.theory.motif_mining import find_motifs_per_theme
        theme = Theme(id="th_001", name="X",
                      member_global_ids=("v_a",), centroid_summary="")
        result = find_motifs_per_theme({}, theme, min_freq=3)
        assert result == []

    def test_3_sessions_with_same_chain_returns_motif(self):
        from explain_engine.engines.theory.motif_mining import find_motifs_per_theme
        sessions = {
            "s_1": _fake_graph([("v_a", "A"), ("v_b", "B")], [("v_a", "v_b", "causes")]),
            "s_2": _fake_graph([("v_a", "A"), ("v_b", "B")], [("v_a", "v_b", "causes")]),
            "s_3": _fake_graph([("v_a", "A"), ("v_b", "B")], [("v_a", "v_b", "causes")]),
        }
        theme = Theme(id="th_001", name="A-B",
                      member_global_ids=("v_a", "v_b"), centroid_summary="")
        result = find_motifs_per_theme(sessions, theme, min_freq=3)
        assert len(result) >= 1
        assert all(len(m.supporting_sessions) == 3 for m in result)

    def test_min_freq_gate(self):
        from explain_engine.engines.theory.motif_mining import find_motifs_per_theme
        sessions = {
            "s_1": _fake_graph([("v_a", "A"), ("v_b", "B")], [("v_a", "v_b", "causes")]),
            "s_2": _fake_graph([("v_a", "A"), ("v_b", "B")], [("v_a", "v_b", "causes")]),
        }
        theme = Theme(id="th_001", name="A-B",
                      member_global_ids=("v_a", "v_b"), centroid_summary="")
        # freq=2 但 min=3 → empty
        result = find_motifs_per_theme(sessions, theme, min_freq=3)
        assert result == []
