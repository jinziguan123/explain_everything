"""Wave C.2: runtime.run 加 reflect 分支测试."""

import pytest

from explain_engine.engines.expansion import ExpansionOutput, _DriverCandidate
from explain_engine.engines.simulation import ConsistencyReport
from explain_engine.runtime.runtime import run
from explain_engine.runtime.scheduler import PhaseScheduler
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_state_with_L1() -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    g.add_node(VariableNode(
        id="c_001", name="c", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="d_001", name="d", description="d",
        abstraction_level=2, confidence=0.6, epistemic="inference",
    ))
    g.add_node(VariableNode(
        id="p_001", name="p", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7, mechanism_description="m",
    ))
    g.add_edge(RelationEdge(
        id="e_002", source_node="d_001", target_node="c_001",
        relation_type="causes", confidence=0.6, mechanism_description="m",
    ))
    return CognitiveState(graph=g, budget_remaining=10, root_question="q")


@pytest.mark.asyncio
async def test_run_with_K2_emits_reflect_action(mocker) -> None:
    state = _make_state_with_L1()
    mocker.patch(
        "explain_engine.engines.expansion._call_with_retry",
        return_value=ExpansionOutput(drivers=[]),
    )
    mocker.patch(
        "explain_engine.engines.reflection.check_consistency_batch",
        return_value=[
            ConsistencyReport(
                target_id="c_001", consistency_score=0.8, essentialness_score=0.5,
                reachable_L0=["p_001"], weak_chains=[],
                contribution_breakdown={}, decay_trace=[],
            ),
        ],
    )
    await run(state, mocker.AsyncMock(), budget=6,
              scheduler=PhaseScheduler(K=2))
    actions = [e.action for e in state.reasoning_trace]
    assert "reflect" in actions


@pytest.mark.asyncio
async def test_reflect_re_expand_mutates_graph(mocker) -> None:
    state = _make_state_with_L1()
    mocker.patch(
        "explain_engine.engines.reflection.check_consistency_batch",
        return_value=[
            ConsistencyReport(
                target_id="c_001", consistency_score=0.3, essentialness_score=0.5,
                reachable_L0=["p_001"], weak_chains=[],
                contribution_breakdown={}, decay_trace=[],
            ),
        ],
    )
    mocker.patch(
        "explain_engine.engines.expansion._call_with_retry",
        return_value=ExpansionOutput(drivers=[
            _DriverCandidate(name="new_d", description="d", mechanism="m", plausibility=4),
        ]),
    )
    initial_drivers = sum(
        1 for n in state.graph.nodes.values() if n.abstraction_level == 2
    )
    await run(state, mocker.AsyncMock(), budget=3,
              scheduler=PhaseScheduler(K=2))
    final_drivers = sum(
        1 for n in state.graph.nodes.values() if n.abstraction_level == 2
    )
    assert final_drivers > initial_drivers


@pytest.mark.asyncio
async def test_reflect_prune_removes_node(mocker) -> None:
    state = _make_state_with_L1()
    # 加 d_002 with low essentialness
    state.graph.add_node(VariableNode(
        id="d_002", name="d2", description="d",
        abstraction_level=2, confidence=0.6, epistemic="inference",
    ))
    state.graph.add_edge(RelationEdge(
        id="e_003", source_node="d_002", target_node="c_001",
        relation_type="causes", confidence=0.6, mechanism_description="m",
    ))
    mocker.patch(
        "explain_engine.engines.reflection.check_consistency_batch",
        return_value=[
            ConsistencyReport(
                target_id="c_001", consistency_score=0.8, essentialness_score=0.5,
                reachable_L0=["p_001"], weak_chains=[],
                contribution_breakdown={}, decay_trace=[],
            ),
            ConsistencyReport(
                target_id="d_001", consistency_score=0.7, essentialness_score=0.5,
                reachable_L0=[], weak_chains=[],
                contribution_breakdown={}, decay_trace=[],
            ),
            ConsistencyReport(
                target_id="d_002", consistency_score=0.7, essentialness_score=0.02,
                reachable_L0=[], weak_chains=[],
                contribution_breakdown={}, decay_trace=[],
            ),
        ],
    )
    mocker.patch(
        "explain_engine.engines.expansion._call_with_retry",
        return_value=ExpansionOutput(drivers=[]),
    )
    await run(state, mocker.AsyncMock(), budget=3,
              scheduler=PhaseScheduler(K=2))
    # d_002 应被 prune
    assert "d_002" not in state.graph.nodes


@pytest.mark.asyncio
async def test_on_tick_callback_invoked_each_tick(mocker) -> None:
    state = _make_state_with_L1()
    mocker.patch(
        "explain_engine.engines.expansion._call_with_retry",
        return_value=ExpansionOutput(drivers=[]),
    )
    mocker.patch(
        "explain_engine.engines.reflection.check_consistency_batch",
        return_value=[],
    )
    calls = []
    await run(
        state, mocker.AsyncMock(), budget=5,
        scheduler=PhaseScheduler(K=2),
        on_tick=lambda s: calls.append(s.tick),
    )
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_reflection_trace_entry_has_action(mocker) -> None:
    state = _make_state_with_L1()
    mocker.patch(
        "explain_engine.engines.reflection.check_consistency_batch",
        return_value=[
            ConsistencyReport(
                target_id="c_001", consistency_score=0.3, essentialness_score=0.5,
                reachable_L0=["p_001"], weak_chains=[],
                contribution_breakdown={}, decay_trace=[],
            ),
        ],
    )
    mocker.patch(
        "explain_engine.engines.expansion._call_with_retry",
        return_value=ExpansionOutput(drivers=[
            _DriverCandidate(name="x", description="d", mechanism="m", plausibility=4),
        ]),
    )
    await run(state, mocker.AsyncMock(), budget=3,
              scheduler=PhaseScheduler(K=2))
    reflect_entries = [e for e in state.reasoning_trace if e.action == "reflect"]
    assert len(reflect_entries) >= 1
    assert reflect_entries[0].reflection_action in ("re-expand", "prune", "stop", "continue")
