"""Wave C.2: runtime.run 加 reflect 分支测试."""

import pytest

from explain_engine.engines.expansion import (
    DownwardExpansionOutput,
    ExpansionOutput,
    _DriverCandidate,
    _PredictedL0,
)
from explain_engine.engines.simulation import ConsistencyReport
from explain_engine.runtime.runtime import run
from explain_engine.runtime.scheduler import PhaseScheduler
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _dual_call_side_effect(driver_name: str = "new_d", l0_name: str = "new_l0"):
    """side_effect for expansion._call_with_retry that returns the right output by model.

    Wave 1 Phase 8: reflect 改用 expand-downward → engines.expand_downward 用
    DownwardExpansionOutput; expand 仍用 ExpansionOutput. mock 需按 schema 分发.
    """
    async def _impl(_llm, _messages, output_model):
        if output_model is DownwardExpansionOutput:
            return DownwardExpansionOutput(predicted_L0=[
                _PredictedL0(name=l0_name, description="d", mechanism="m", plausibility=4),
            ])
        return ExpansionOutput(drivers=[
            _DriverCandidate(name=driver_name, description="d", mechanism="m", plausibility=4),
        ])
    return _impl


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
async def test_reflect_expand_downward_mutates_graph(mocker) -> None:
    """Wave 1 Phase 8: reflect 在 weak L1 时走 expand-downward → 给 c_001 加 L0 子节点.

    改前 (Phase 7): reflect 返 re-expand → re_expand 加 L2 driver, 验 L2 增长.
    改后 (Wave 1): reflect 返 expand-downward → expand_downward 加 L0 child, 验 L0 增长.
    """
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
        side_effect=_dual_call_side_effect(),
    )
    initial_l0 = sum(
        1 for n in state.graph.nodes.values() if n.abstraction_level == 0
    )
    await run(state, mocker.AsyncMock(), budget=3,
              scheduler=PhaseScheduler(K=2))
    final_l0 = sum(
        1 for n in state.graph.nodes.values() if n.abstraction_level == 0
    )
    assert final_l0 > initial_l0


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
    # Wave C.3 后: fixture 图无 frontier (c_001 已被 d_001 "causes" 覆盖),
    # 所有 expand tick fall through 到 reflect, reflect 返 "continue" 不 bump
    # last_reflection_change_tick → 在 tick=4 时 reflection_signaled_stop 触发。
    # budget=4 让 budget 先于 reflection_stop 终止, 验证 on_tick 每 tick 调一次。
    await run(
        state, mocker.AsyncMock(), budget=4,
        scheduler=PhaseScheduler(K=2),
        on_tick=lambda s: calls.append(s.tick),
    )
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_scheduler_driven_reflect_with_non_empty_frontier(mocker) -> None:
    """C.2 review M-1 follow-up: scheduler 触发的 reflect tick (而非 fallthrough).

    Fixture 留 frontier 给 expand tick 用; scheduler 在 tick K (=2) 时 yield "reflect".
    验证此条路径 (scheduler-driven) 与 expand 分支 fallthrough 路径互不干扰。
    """
    g = ExplanationGraph(root_question="q")
    # c_001 没 incoming causes — frontier (expand 可用)
    g.add_node(VariableNode(
        id="c_001", name="c", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="p", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7, mechanism_description="m",
    ))
    state = CognitiveState(graph=g, budget_remaining=10, root_question="q")

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
    await run(state, mocker.AsyncMock(), budget=3,
              scheduler=PhaseScheduler(K=2))
    # K=2 → tick 0,1 expand; tick 2 reflect
    actions = [e.action for e in state.reasoning_trace]
    # First 2 ticks should be expand (frontier non-empty), tick 2 reflect
    assert actions[0] == "expand"
    assert actions[1] == "expand"
    assert actions[2] == "reflect"


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
        side_effect=_dual_call_side_effect(driver_name="x", l0_name="x_l0"),
    )
    await run(state, mocker.AsyncMock(), budget=3,
              scheduler=PhaseScheduler(K=2))
    reflect_entries = [e for e in state.reasoning_trace if e.action == "reflect"]
    assert len(reflect_entries) >= 1
    # Wave 1 Phase 8: 加 "expand-downward" (re-expand 保留作 backward compat).
    assert reflect_entries[0].reflection_action in (
        "expand-downward", "re-expand", "prune", "stop", "continue",
    )
