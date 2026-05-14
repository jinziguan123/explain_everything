"""HITL 2 review_insights 渲染 gain 列时读 state.last_gains（不重算）。

stage=insight_pending 重入时复用持久化的 gain；drop candidate 时同步从 last_gains 移除。
"""

from io import StringIO

from rich.console import Console

from explain_engine.hitl.cli_interactive import review_insights
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _state_with_two_candidates() -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    for pid in ("p_001", "p_002"):
        g.add_node(VariableNode(id=pid, name=pid, description="d", abstraction_level=0,
                                confidence=0.8, epistemic="observation"))
    for cid in ("c_001", "c_002"):
        g.add_node(VariableNode(id=cid, name=cid, description="d", abstraction_level=1,
                                confidence=0.7, epistemic="insight"))
        g.add_edge(RelationEdge(id=f"e_{cid}", source_node=cid, target_node="p_001",
                                relation_type="manifests_as", confidence=0.7,
                                mechanism_description="m"))
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    state.insight_candidates = ["c_001", "c_002"]
    state.last_gains = {"c_001": 0.65, "c_002": 0.42}
    return state


def test_review_insights_table_uses_persisted_last_gains(monkeypatch) -> None:
    """HITL 2 渲染 gain 列读 state.last_gains，不是临时计算。"""
    state = _state_with_two_candidates()
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)

    import explain_engine.hitl.cli_interactive as hitl_mod

    answers = iter(["k", "k"])
    monkeypatch.setattr(hitl_mod.Prompt, "ask", lambda *a, **kw: next(answers))

    review_insights(state, console)

    out = buf.getvalue()
    assert "0.65" in out
    assert "0.42" in out


def test_review_insights_drop_removes_from_last_gains(monkeypatch) -> None:
    state = _state_with_two_candidates()
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)

    import explain_engine.hitl.cli_interactive as hitl_mod
    answers = iter(["k", "d"])   # keep c_001, drop c_002
    monkeypatch.setattr(hitl_mod.Prompt, "ask", lambda *a, **kw: next(answers))

    review_insights(state, console)

    assert "c_002" not in state.last_gains
    assert "c_001" in state.last_gains
