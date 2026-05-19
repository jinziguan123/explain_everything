"""Phase 12: /graph helper tests (tmpdir / digraph builder / renderer detect)."""

import os
import shutil


class TestGetSessionTmpdir:
    def setup_method(self):
        """Reset module-global tmpdir before each test (isolation)."""
        import explain_engine.chat.slash_commands as sc
        if sc._SESSION_TMPDIR is not None:
            shutil.rmtree(sc._SESSION_TMPDIR, ignore_errors=True)
        sc._SESSION_TMPDIR = None

    def test_lazy_init_first_call_creates(self):
        from explain_engine.chat.slash_commands import _get_session_tmpdir
        tmpdir = _get_session_tmpdir()
        assert os.path.isdir(tmpdir)
        assert "explain_graph_" in tmpdir

    def test_second_call_reuses(self):
        from explain_engine.chat.slash_commands import _get_session_tmpdir
        tmpdir1 = _get_session_tmpdir()
        tmpdir2 = _get_session_tmpdir()
        assert tmpdir1 == tmpdir2

    def test_atexit_registered(self, monkeypatch):
        """First call 应 atexit.register(shutil.rmtree, tmpdir, ignore_errors=True)."""
        import explain_engine.chat.slash_commands as sc

        captured: list[tuple] = []

        def fake_register(func, *args, **kwargs):
            captured.append((func, args, kwargs))

        # patch atexit.register on the slash_commands aliased import (_atexit.register)
        monkeypatch.setattr(sc._atexit, "register", fake_register)
        sc._SESSION_TMPDIR = None  # force re-init

        from explain_engine.chat.slash_commands import _get_session_tmpdir
        tmpdir = _get_session_tmpdir()

        assert len(captured) == 1
        func, args, kwargs = captured[0]
        assert func is shutil.rmtree
        assert args == (tmpdir,)
        assert kwargs == {"ignore_errors": True}


class TestBuildDigraph:
    def _make_state(self, *, l0=1, l1=1, l2=0):
        """Build minimal CognitiveState with l0/l1/l2 nodes, no edges."""
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.nodes import VariableNode
        from explain_engine.schema.state import CognitiveState
        g = ExplanationGraph(root_question="Q")
        for i in range(l0):
            g.add_node(VariableNode(
                id=f"p_{i+1:03d}", name=f"obs{i+1}", description="d",
                abstraction_level=0, confidence=0.85, epistemic="observation",
            ))
        for i in range(l1):
            g.add_node(VariableNode(
                id=f"c_{i+1:03d}", name=f"concept{i+1}", description="d",
                abstraction_level=1, confidence=0.78, epistemic="insight",
            ))
        for i in range(l2):
            g.add_node(VariableNode(
                id=f"d_{i+1:03d}", name=f"driver{i+1}", description="d",
                abstraction_level=2, confidence=0.60, epistemic="inference",
            ))
        return CognitiveState(graph=g, budget_remaining=10, root_question="Q")

    def test_empty_returns_digraph_with_zero_nodes(self):
        from explain_engine.chat.slash_commands import _build_digraph
        from explain_engine.schema.graph import ExplanationGraph
        from explain_engine.schema.state import CognitiveState
        state = CognitiveState(
            graph=ExplanationGraph(root_question="Q"),
            budget_remaining=10,
            root_question="Q",
        )
        dg = _build_digraph(state, weak_l1_ids=set())
        assert "digraph" in dg.source.lower()

    def test_l0_node_box_lightblue(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=1, l1=0)
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "p_001" in src
        assert "box" in src
        assert "lightblue" in src

    def test_l1_node_ellipse_lightyellow(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=0, l1=1)
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "c_001" in src
        assert "ellipse" in src
        assert "lightyellow" in src

    def test_l2_node_doubleoctagon_lightcoral(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=0, l1=0, l2=1)
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "d_001" in src
        assert "doubleoctagon" in src
        assert "lightcoral" in src

    def test_node_label_contains_id_name_conf(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=1, l1=0)
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "obs1" in src
        assert "0.85" in src

    def test_weak_l1_red_border(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=0, l1=1)
        dg = _build_digraph(state, weak_l1_ids={"c_001"})
        src = dg.source
        assert "red" in src
        assert "penwidth=2" in src or "penwidth=\"2\"" in src

    def test_decayed_node_dashed_gray(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=0, l1=1)
        state.graph.nodes["c_001"].lifecycle_state = "decayed"
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "dashed" in src
        assert "gray80" in src

    def test_stale_node_dotted(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state(l0=0, l1=1)
        state.graph.nodes["c_001"].lifecycle_state = "stale"
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "dotted" in src

    def test_edge_manifests_as_dashed(self):
        from explain_engine.chat.slash_commands import _build_digraph
        from explain_engine.schema.edges import RelationEdge
        state = self._make_state(l0=1, l1=1)
        state.graph.add_edge(RelationEdge(
            id="e1", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.85,
            mechanism_description="m",
        ))
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "dashed" in src

    def test_edge_amplifies_thick(self):
        from explain_engine.chat.slash_commands import _build_digraph
        from explain_engine.schema.edges import RelationEdge
        state = self._make_state(l0=0, l1=2)
        state.graph.add_edge(RelationEdge(
            id="e1", source_node="c_001", target_node="c_002",
            relation_type="amplifies", confidence=0.7,
            mechanism_description="m",
        ))
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "penwidth=2.5" in src or "penwidth=\"2.5\"" in src

    def test_edge_suppresses_red(self):
        from explain_engine.chat.slash_commands import _build_digraph
        from explain_engine.schema.edges import RelationEdge
        state = self._make_state(l0=0, l1=2)
        state.graph.add_edge(RelationEdge(
            id="e1", source_node="c_001", target_node="c_002",
            relation_type="suppresses", confidence=0.7,
            mechanism_description="m",
        ))
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "red" in src

    def test_edge_label_format(self):
        from explain_engine.chat.slash_commands import _build_digraph
        from explain_engine.schema.edges import RelationEdge
        state = self._make_state(l0=1, l1=1)
        state.graph.add_edge(RelationEdge(
            id="e1", source_node="c_001", target_node="p_001",
            relation_type="causes", confidence=0.80,
            mechanism_description="m",
        ))
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "cau" in src
        assert "0.80" in src

    def test_rankdir_tb(self):
        from explain_engine.chat.slash_commands import _build_digraph
        state = self._make_state()
        dg = _build_digraph(state, weak_l1_ids=set())
        src = dg.source
        assert "rankdir=TB" in src or "rankdir=\"TB\"" in src


class TestDetectInlineRenderer:
    """检测顺序: iTerm2 → Kitty/Ghostty → chafa → None."""

    def test_iterm_detected(self, monkeypatch):
        from explain_engine.chat.slash_commands import _detect_inline_renderer
        monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.setattr(
            "shutil.which",
            lambda x: "/usr/local/bin/imgcat" if x == "imgcat" else None,
        )
        cmd, name = _detect_inline_renderer("/tmp/foo.png")
        assert name == "iterm"
        assert cmd[0] == "imgcat"
        assert "/tmp/foo.png" in cmd

    def test_kitty_window_id_detected(self, monkeypatch):
        from explain_engine.chat.slash_commands import _detect_inline_renderer
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        monkeypatch.setenv("KITTY_WINDOW_ID", "1")
        monkeypatch.setattr(
            "shutil.which",
            lambda x: "/usr/local/bin/kitty" if x == "kitty" else None,
        )
        cmd, name = _detect_inline_renderer("/tmp/foo.png")
        assert name == "kitty"
        assert cmd[:3] == ["kitty", "+kitten", "icat"]

    def test_ghostty_detected(self, monkeypatch):
        from explain_engine.chat.slash_commands import _detect_inline_renderer
        monkeypatch.setenv("TERM_PROGRAM", "ghostty")
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.setattr(
            "shutil.which",
            lambda x: "/usr/local/bin/kitty" if x == "kitty" else None,
        )
        _cmd, name = _detect_inline_renderer("/tmp/foo.png")
        assert name == "kitty"

    def test_chafa_fallback(self, monkeypatch):
        from explain_engine.chat.slash_commands import _detect_inline_renderer
        monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.setattr(
            "shutil.which",
            lambda x: "/usr/local/bin/chafa" if x == "chafa" else None,
        )
        cmd, name = _detect_inline_renderer("/tmp/foo.png")
        assert name == "chafa"
        assert cmd[0] == "chafa"
        assert "--size" in cmd

    def test_none_when_all_unavailable(self, monkeypatch):
        from explain_engine.chat.slash_commands import _detect_inline_renderer
        monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.setattr("shutil.which", lambda x: None)
        cmd, name = _detect_inline_renderer("/tmp/foo.png")
        assert cmd is None
        assert name == "none"

    def test_iterm_missing_imgcat_falls_to_chafa(self, monkeypatch):
        """iTerm 但 imgcat 不在 PATH → 试下一档 chafa."""
        from explain_engine.chat.slash_commands import _detect_inline_renderer
        monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
        monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
        monkeypatch.setattr(
            "shutil.which",
            lambda x: "/usr/local/bin/chafa" if x == "chafa" else None,
        )
        _cmd, name = _detect_inline_renderer("/tmp/foo.png")
        assert name == "chafa"
