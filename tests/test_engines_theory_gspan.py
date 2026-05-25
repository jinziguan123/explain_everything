"""Phase 16: 自实现 simplified gSpan (Yan & Han 2002), directed in-memory."""

import networkx as nx


class TestDFSEdge:
    def test_construct(self):
        from explain_engine.engines.theory.gspan import DFSEdge
        e = DFSEdge(from_idx=0, to_idx=1, from_label="A", edge_label="causes", to_label="B")
        assert e.from_idx == 0 and e.to_idx == 1


class TestFrequentSubgraph:
    def test_construct(self):
        from explain_engine.engines.theory.gspan import FrequentSubgraph
        fs = FrequentSubgraph(
            nodes=("n0", "n1"),
            edges=(("n0", "n1", "causes"),),
            support_count=3,
            embeddings_in_graphs=(("g0", {"n0": "x0", "n1": "x1"}),),
        )
        assert fs.support_count == 3


class TestCountFrequentEdges:
    def _make_graph(self, edges_with_labels):
        g = nx.DiGraph()
        for src, src_label, tgt, tgt_label, edge_label in edges_with_labels:
            g.add_node(src, label=src_label)
            g.add_node(tgt, label=tgt_label)
            g.add_edge(src, tgt, label=edge_label)
        return g

    def test_single_graph_returns_no_frequent(self):
        from explain_engine.engines.theory.gspan import _count_frequent_edges
        g = self._make_graph([("a", "A", "b", "B", "causes")])
        result = _count_frequent_edges([("g0", g)], min_support=2)
        assert result == []  # 单 graph 无 frequent

    def test_two_graphs_same_edge_template_is_frequent(self):
        from explain_engine.engines.theory.gspan import _count_frequent_edges
        g0 = self._make_graph([("a", "A", "b", "B", "causes")])
        g1 = self._make_graph([("x", "A", "y", "B", "causes")])
        result = _count_frequent_edges([("g0", g0), ("g1", g1)], min_support=2)
        assert len(result) == 1
        edge_template, count = result[0]
        assert edge_template == ("A", "causes", "B")
        assert count == 2

    def test_three_graphs_two_diff_labels(self):
        from explain_engine.engines.theory.gspan import _count_frequent_edges
        g0 = self._make_graph([("a", "A", "b", "B", "causes")])
        g1 = self._make_graph([("x", "A", "y", "B", "causes")])
        g2 = self._make_graph([("p", "C", "q", "D", "causes")])
        result = _count_frequent_edges([("g0", g0), ("g1", g1), ("g2", g2)], min_support=2)
        assert len(result) == 1  # 只 (A, causes, B) 满足 freq=2


class TestIsMinimumDFSCode:
    def test_single_edge_is_canonical(self):
        from explain_engine.engines.theory.gspan import DFSEdge, _is_minimum_dfs_code
        code = [DFSEdge(0, 1, "A", "causes", "B")]
        assert _is_minimum_dfs_code(code) is True

    def test_two_isomorphic_codes_only_min_passes(self):
        """同一 subgraph 可有多种 DFS 顺序, gSpan 取字典序最小的为 canonical.

        Graph: A → B, A → C
        Code 1: [(0,1,A,e,B), (0,2,A,e,C)]  ← min DFS code (B 先 visit, 字典序更小)
        Code 2: [(0,1,A,e,C), (0,2,A,e,B)]  ← 非 min
        """
        from explain_engine.engines.theory.gspan import DFSEdge, _is_minimum_dfs_code
        code_min = [DFSEdge(0, 1, "A", "e", "B"), DFSEdge(0, 2, "A", "e", "C")]
        code_non_min = [DFSEdge(0, 1, "A", "e", "C"), DFSEdge(0, 2, "A", "e", "B")]
        assert _is_minimum_dfs_code(code_min) is True
        assert _is_minimum_dfs_code(code_non_min) is False

    def test_chain_canonical(self):
        from explain_engine.engines.theory.gspan import DFSEdge, _is_minimum_dfs_code
        # A → B → C, DFS code [(0,1,A,e,B), (1,2,B,e,C)] 是 min
        code = [DFSEdge(0, 1, "A", "e", "B"), DFSEdge(1, 2, "B", "e", "C")]
        assert _is_minimum_dfs_code(code) is True


class TestRightmostExtensions:
    def test_single_node_seed_has_extensions(self):
        """seed code = [(0,1,A,e,B)], rightmost path = [0, 1].
        从 0 或 1 出去找新 edge → extension.
        """
        from explain_engine.engines.theory.gspan import (
            DFSEdge,
            _enumerate_rightmost_extensions,
        )
        # Graph 0: A → B → C
        g = nx.DiGraph()
        g.add_node("a", label="A")
        g.add_node("b", label="B")
        g.add_node("c", label="C")
        g.add_edge("a", "b", label="e")
        g.add_edge("b", "c", label="e")

        seed = [DFSEdge(0, 1, "A", "e", "B")]
        embedding = {0: "a", 1: "b"}  # motif_idx → graph_node
        extensions = _enumerate_rightmost_extensions(seed, [("g0", g)], [embedding])

        # 应找到 forward extension (1, 2, B, e, C)
        assert len(extensions) >= 1
        assert any(ext.from_idx == 1 and ext.to_label == "C" for ext in extensions)


class TestCountSupport:
    def test_support_count_across_graphs(self):
        """同 motif 出现在 2 个 graph → support = 2."""
        from explain_engine.engines.theory.gspan import DFSEdge, _count_support
        # 2 graph, 都含 A → B
        g0 = nx.DiGraph()
        g0.add_node("x", label="A")
        g0.add_node("y", label="B")
        g0.add_edge("x", "y", label="e")
        g1 = nx.DiGraph()
        g1.add_node("p", label="A")
        g1.add_node("q", label="B")
        g1.add_edge("p", "q", label="e")

        motif_code = [DFSEdge(0, 1, "A", "e", "B")]
        graphs = [("g0", g0), ("g1", g1)]
        support, embeddings = _count_support(motif_code, graphs)
        assert support == 2
        assert len(embeddings) == 2


class TestGspanMineIntegration:
    def _make_graph(self, edges):
        g = nx.DiGraph()
        seen = set()
        for src, src_lbl, tgt, tgt_lbl, edge_lbl in edges:
            if src not in seen:
                g.add_node(src, label=src_lbl)
                seen.add(src)
            if tgt not in seen:
                g.add_node(tgt, label=tgt_lbl)
                seen.add(tgt)
            g.add_edge(src, tgt, label=edge_lbl)
        return g

    def test_finds_frequent_chain_size_3(self):
        from explain_engine.engines.theory.gspan import gspan_mine
        # 3 graph, 都含 A → B → C chain
        graphs = [
            ("g0", self._make_graph([("a", "A", "b", "B", "e"), ("b", "B", "c", "C", "e")])),
            ("g1", self._make_graph([("x", "A", "y", "B", "e"), ("y", "B", "z", "C", "e")])),
            ("g2", self._make_graph([("p", "A", "q", "B", "e"), ("q", "B", "r", "C", "e")])),
        ]
        result = gspan_mine(graphs, min_support=3, min_size=3, max_size=5)
        # 应找出 chain motif (3 nodes A→B→C)
        assert any(len(fs.nodes) == 3 and fs.support_count == 3 for fs in result)

    def test_noise_graph_not_in_support(self):
        from explain_engine.engines.theory.gspan import gspan_mine
        graphs = [
            ("g0", self._make_graph([("a", "A", "b", "B", "e")])),
            ("g1", self._make_graph([("x", "A", "y", "B", "e")])),
            ("g2", self._make_graph([("p", "X", "q", "Y", "e")])),  # noise
        ]
        result = gspan_mine(graphs, min_support=2)
        # (A,e,B) freq=2 应在; noise (X,e,Y) freq=1 不应在.
        # FrequentSubgraph.edges 只含 motif-local id + edge_label, 不含 node label,
        # 所以校验改为: 至少 1 motif 在 g0/g1 出现, 且没有任何 motif 在 g2 出现.
        assert len(result) >= 1
        for fs in result:
            gids_in_embeddings = {gid for gid, _ in fs.embeddings_in_graphs}
            assert "g2" not in gids_in_embeddings
            # 至少有 1 个 freq>=2 的 motif (来自 g0+g1)
        assert any(fs.support_count >= 2 for fs in result)

    def test_min_support_3_with_only_2_graphs_returns_empty(self):
        from explain_engine.engines.theory.gspan import gspan_mine
        graphs = [
            ("g0", self._make_graph([("a", "A", "b", "B", "e")])),
            ("g1", self._make_graph([("x", "A", "y", "B", "e")])),
        ]
        result = gspan_mine(graphs, min_support=3)
        assert result == []

    def test_max_size_caps_motif(self):
        from explain_engine.engines.theory.gspan import gspan_mine
        # 都含 5-node chain, 但 max_size=3
        graphs = [
            ("g0", self._make_graph([
                ("a", "A", "b", "B", "e"), ("b", "B", "c", "C", "e"),
                ("c", "C", "d", "D", "e"), ("d", "D", "e_node", "E", "e"),
            ])),
            ("g1", self._make_graph([
                ("a", "A", "b", "B", "e"), ("b", "B", "c", "C", "e"),
                ("c", "C", "d", "D", "e"), ("d", "D", "e_node", "E", "e"),
            ])),
        ]
        result = gspan_mine(graphs, min_support=2, max_size=3)
        assert all(len(fs.nodes) <= 3 for fs in result)
