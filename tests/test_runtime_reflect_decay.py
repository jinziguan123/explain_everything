"""Wave 4 Task 4.2: runtime end-to-end decay dispatch test.

Closes I3 review gap: unit tests cover update_lifecycle / pick_decay_target /
reflect() / soft_decay separately; this verifies the runtime.run path actually
chains them correctly (aggregate → update_lifecycle → reflect → soft_decay
→ TraceEntry).
"""

from unittest.mock import MagicMock

import pytest

from explain_engine.engines.simulation import AcceptanceReport
from explain_engine.runtime import runtime
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state_low_fitness_l2() -> CognitiveState:
    """无 weak L1 + 1 个 low-fitness L2 → reflect 应返 ('decay', 'd_999')."""
    g = ExplanationGraph(root_question="q")
    g.add_node(VariableNode(
        id="c_001", name="strong", description="d",
        abstraction_level=1, confidence=0.9, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="p", description="d",
        abstraction_level=0, confidence=0.9, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.9,
        mechanism_description="m",
    ))
    # Low fitness L2
    g.add_node(VariableNode(
        id="d_999", name="useless", description="d",
        abstraction_level=2, confidence=0.7, epistemic="inference",
        activation=0.0, stability=0.0,
    ))
    state = CognitiveState(
        graph=g, budget_remaining=10, root_question="q",
        insight_candidates=["c_001"],
    )
    return state


@pytest.mark.asyncio
async def test_runtime_dispatches_decay_to_soft_decay(mocker) -> None:
    """End-to-end: scheduler picks reflect → reflect returns ('decay', 'd_999')
    → runtime calls soft_decay → node.lifecycle_state == 'decayed'.
    """
    state = _make_state_low_fitness_l2()

    # Mock aggregate_acceptance to inject low-fitness signal for d_999
    mocker.patch(
        "explain_engine.runtime.runtime.aggregate_acceptance",
        return_value=AcceptanceReport(
            avg_consistency=0.9, avg_essentialness=0.0,
            per_l1={"c_001": 0.9},
            per_l2={"d_999": 0.0},
            weak_chain_l1s=[],
        ),
    )

    sched = MagicMock()
    sched.pick.side_effect = ["reflect"]

    fake_llm = MagicMock()
    await runtime.run(state, llm=fake_llm, budget=1, scheduler=sched)

    # Verify decay actually happened
    assert state.graph.nodes["d_999"].lifecycle_state == "decayed"

    # Verify trace entry recorded
    decay_entries = [
        e for e in state.reasoning_trace
        if e.action == "reflect" and e.reflection_action == "decay"
    ]
    assert len(decay_entries) == 1
    assert decay_entries[0].target_node_id == "d_999"


@pytest.mark.asyncio
async def test_runtime_calls_update_lifecycle_before_reflect(mocker) -> None:
    """Verifies ordering: aggregate → update_lifecycle → reflect.

    If update_lifecycle were called AFTER reflect, the decay decision would
    be based on stale state and decayed nodes wouldn't be excluded mid-tick.
    """
    state = _make_state_low_fitness_l2()
    call_order = []

    def _track_aggregate(*args, **kwargs):
        call_order.append("aggregate")
        return AcceptanceReport(avg_consistency=1.0, avg_essentialness=1.0)

    def _track_update(*args, **kwargs):
        call_order.append("update_lifecycle")
        return {}

    mocker.patch(
        "explain_engine.runtime.runtime.aggregate_acceptance",
        side_effect=_track_aggregate,
    )
    mocker.patch(
        "explain_engine.engines.lifecycle.update_lifecycle",
        side_effect=_track_update,
    )

    sched = MagicMock()
    sched.pick.side_effect = ["reflect"]
    fake_llm = MagicMock()
    await runtime.run(state, llm=fake_llm, budget=1, scheduler=sched)

    # Order: aggregate first, then update_lifecycle (both before reflect)
    assert call_order == ["aggregate", "update_lifecycle"]
