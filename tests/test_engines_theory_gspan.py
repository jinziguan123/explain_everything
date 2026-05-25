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
