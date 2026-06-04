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
