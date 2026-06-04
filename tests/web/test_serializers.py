from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.web.serializers import graph_to_cytoscape


def _graph() -> ExplanationGraph:
    g = ExplanationGraph(root_question="为什么")
    g.add_node(VariableNode(id="p_001", name="房价", description="d",
        abstraction_level=0, confidence=0.9, epistemic="observation"))
    g.add_node(VariableNode(id="c_001", name="不确定性", description="d",
        abstraction_level=1, confidence=0.8, epistemic="insight"))
    g.add_edge(RelationEdge(id="e1", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7, mechanism_description="m"))
    return g


def test_graph_to_cytoscape_shape():
    out = graph_to_cytoscape(_graph())
    assert out["root_question"] == "为什么"
    ids = {n["data"]["id"] for n in out["elements"]["nodes"]}
    assert ids == {"p_001", "c_001"}
    edge = out["elements"]["edges"][0]["data"]
    assert edge["source"] == "c_001" and edge["target"] == "p_001"
    assert edge["relation"] == "manifests_as"
    node = next(n["data"] for n in out["elements"]["nodes"] if n["data"]["id"] == "c_001")
    assert node["level"] == 1 and node["epistemic"] == "insight"


def test_empty_graph():
    g = ExplanationGraph(root_question="q")
    out = graph_to_cytoscape(g)
    assert out["elements"]["nodes"] == []
    assert out["elements"]["edges"] == []


def test_lexicon_to_cytoscape_nodes_and_theme():
    from explain_engine.engines.theory.cache import TheoriesCache
    from explain_engine.engines.theory.theory import Theme, Theory
    from explain_engine.web.serializers import lexicon_to_cytoscape
    vars_ = [
        {"global_id": "v_a", "name": "不确定性", "reuse_count": 5, "abstraction_level": 1},
        {"global_id": "v_b", "name": "房价", "reuse_count": 2, "abstraction_level": 0},
    ]
    theme = Theme(id="t1", name="经济", member_global_ids=("v_a",), centroid_summary="s")
    theory = Theory(
        id="th1", motif_type="chain", theme_ids=("t1",), node_ids=("v_a", "v_b"),
        edges=(("v_a", "v_b", "manifests_as"),), supporting_sessions=("s_1",),
        natural_language_summary="x", structure_complexity=1,
        first_seen_session="s_1", last_seen_session="s_1",
    )
    cache = TheoriesCache(themes=[theme], stable_theories=[theory])
    out = lexicon_to_cytoscape(vars_, cache)
    nodes = {n["data"]["id"]: n["data"] for n in out["elements"]["nodes"]}
    assert nodes["v_a"]["theme"] == "t1" and nodes["v_a"]["in_theory"] is True
    assert nodes["v_b"]["theme"] == ""  # 不在任何 theme
    edges = out["elements"]["edges"]
    assert len(edges) == 1 and edges[0]["data"]["relation"] == "manifests_as"


def test_lexicon_to_cytoscape_skips_dangling_edge():
    from explain_engine.engines.theory.cache import TheoriesCache
    from explain_engine.engines.theory.theory import Theory
    from explain_engine.web.serializers import lexicon_to_cytoscape
    vars_ = [{"global_id": "v_a", "name": "a", "reuse_count": 1, "abstraction_level": 0}]
    theory = Theory(
        id="th1", motif_type="chain", theme_ids=(), node_ids=("v_a", "v_missing"),
        edges=(("v_a", "v_missing", "causes"),), supporting_sessions=("s_1",),
        natural_language_summary="x", structure_complexity=1,
        first_seen_session="s_1", last_seen_session="s_1",
    )
    cache = TheoriesCache(stable_theories=[theory])
    out = lexicon_to_cytoscape(vars_, cache)
    assert out["elements"]["edges"] == []  # v_missing 不在节点集 → 跳过
