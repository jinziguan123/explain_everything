"""Wave C.1 + Phase 8 Wave 1: ReflectionEngine.reflect 测试.

design §6.2 / Phase 8 design §4. 0 LLM call, 用 Phase 6 simulation.check_consistency_batch.
决策优先级 (Wave 1 改): expand-downward > prune > stop > continue.

Wave 1 改了 reflect() 在 weak L1 时返 ('expand-downward', target) 而非 ('re-expand', target),
但 anti-thrash 内部老 trace 仍用 "re-expand" 字串测 backward compat (新+老 trace 都数).
"""

from explain_engine.engines.reflection import (
    CONSISTENCY_STALE_TICKS,
    LOW_CONSISTENCY_THRESHOLD,
    LOW_ESSENTIALNESS_THRESHOLD,
    reflect,
)
from explain_engine.engines.simulation import ConsistencyReport
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _node(nid: str, level: int) -> VariableNode:
    return VariableNode(
        id=nid, name=nid, description="d",
        abstraction_level=level, confidence=0.7,
        epistemic="insight" if level >= 1 else "observation",
    )


def _make_state(nodes: list[tuple[str, int]], tick: int = 0,
                last_refl: int = 0) -> CognitiveState:
    g = ExplanationGraph(root_question="q")
    for nid, lvl in nodes:
        g.add_node(_node(nid, lvl))
    return CognitiveState(
        graph=g, budget_remaining=10, root_question="q",
        tick=tick, last_reflection_change_tick=last_refl,
    )


def _mock_reports(mocker, reports: list[tuple[str, float, float]]):
    """Mock check_consistency_batch 返指定 reports.

    Each tuple: (target_id, consistency, essentialness).
    """
    fake = [
        ConsistencyReport(
            target_id=tid, consistency_score=c, essentialness_score=e,
            reachable_L0=[], weak_chains=[],
            contribution_breakdown={}, decay_trace=[],
        )
        for tid, c, e in reports
    ]
    mocker.patch(
        "explain_engine.engines.reflection.check_consistency_batch",
        return_value=fake,
    )


class TestReflectEdgeCases:
    def test_empty_graph_returns_continue(self) -> None:
        state = _make_state([])
        action, target = reflect(state)
        assert action == "continue"
        assert target is None

    def test_no_L1_L2_returns_continue(self) -> None:
        state = _make_state([("p_001", 0)])
        action, target = reflect(state)
        assert action == "continue"
        assert target is None


class TestExpandDownward:
    def test_single_low_consistency_L1_triggers_re_expand(self, mocker) -> None:
        state = _make_state([("c_001", 1), ("p_001", 0)])
        _mock_reports(mocker, [("c_001", 0.3, 0.5)])
        action, target = reflect(state)
        assert action == "expand-downward"
        assert target == "c_001"

    def test_multi_low_consistency_returns_lowest(self, mocker) -> None:
        state = _make_state([("c_001", 1), ("c_002", 1), ("p_001", 0)])
        _mock_reports(mocker, [
            ("c_001", 0.4, 0.5),
            ("c_002", 0.2, 0.5),
        ])
        action, target = reflect(state)
        assert action == "expand-downward"
        assert target == "c_002"   # 0.2 < 0.4

    def test_threshold_exclusive(self, mocker) -> None:
        """consistency = 0.5 exactly 不触发 (严格 <)."""
        state = _make_state([("c_001", 1), ("p_001", 0)])
        _mock_reports(mocker, [("c_001", LOW_CONSISTENCY_THRESHOLD, 0.5)])
        action, _ = reflect(state)
        assert action != "expand-downward"


class TestPrune:
    def test_low_essentialness_L2_triggers_prune(self, mocker) -> None:
        state = _make_state([("c_001", 1), ("d_001", 2), ("p_001", 0)])
        _mock_reports(mocker, [
            ("c_001", 0.8, 0.5),
            ("d_001", 0.7, 0.02),
        ])
        action, target = reflect(state)
        assert action == "prune"
        assert target == "d_001"

    def test_re_expand_priority_over_prune(self, mocker) -> None:
        """Same time: low consistency L1 + low essentialness L2 → expand-downward 优先."""
        state = _make_state([("c_001", 1), ("d_001", 2), ("p_001", 0)])
        _mock_reports(mocker, [
            ("c_001", 0.3, 0.5),
            ("d_001", 0.7, 0.02),
        ])
        action, target = reflect(state)
        assert action == "expand-downward"
        assert target == "c_001"

    def test_multi_low_essentialness_returns_lowest(self, mocker) -> None:
        """对称 multi-L1 lowest-consistency: 多 L2 选 lowest essentialness."""
        state = _make_state([("c_001", 1), ("d_001", 2), ("d_002", 2), ("p_001", 0)])
        _mock_reports(mocker, [
            ("c_001", 0.8, 0.5),
            ("d_001", 0.7, 0.04),
            ("d_002", 0.7, 0.01),
        ])
        action, target = reflect(state)
        assert action == "prune"
        assert target == "d_002"   # 0.01 < 0.04

    def test_essentialness_threshold_exclusive(self, mocker) -> None:
        """对称 consistency=0.5 严格 <: essentialness=0.05 恰好不触发 prune."""
        state = _make_state([("d_001", 2), ("p_001", 0)])
        _mock_reports(mocker, [("d_001", 0.7, LOW_ESSENTIALNESS_THRESHOLD)])
        action, _ = reflect(state)
        assert action != "prune"


class TestStop:
    def test_stale_change_tick_triggers_stop(self, mocker) -> None:
        state = _make_state(
            [("c_001", 1), ("p_001", 0)],
            tick=CONSISTENCY_STALE_TICKS,
            last_refl=0,
        )
        _mock_reports(mocker, [("c_001", 0.8, 0.5)])
        action, target = reflect(state)
        assert action == "stop"
        assert target is None

    def test_fresh_change_tick_returns_continue(self, mocker) -> None:
        state = _make_state(
            [("c_001", 1), ("p_001", 0)],
            tick=1, last_refl=0,
        )
        _mock_reports(mocker, [("c_001", 0.8, 0.5)])
        action, _ = reflect(state)
        assert action == "continue"


class TestNoLLMCalls:
    def test_reflect_uses_no_llm(self, mocker) -> None:
        """Reflect 0 LLM call — 仅用 Phase 6 simulation."""
        state = _make_state([("c_001", 1), ("p_001", 0)])
        _mock_reports(mocker, [("c_001", 0.8, 0.5)])
        action, _ = reflect(state)
        assert action in ("continue", "expand-downward", "re-expand", "prune", "stop")


class TestExpansionAntiThrash:
    """Wave C 补丁2: re-expand anti-thrash 防死循环."""

    def test_no_trace_not_exhausted(self, mocker) -> None:
        """空 trace → 没人 exhausted, expand-downward 正常工作."""
        state = _make_state([("c_001", 1), ("p_001", 0)])
        _mock_reports(mocker, [("c_001", 0.3, 0.5)])
        action, target = reflect(state)
        assert action == "expand-downward"
        assert target == "c_001"

    def test_single_re_expand_in_trace_not_exhausted(self, mocker) -> None:
        """单次 expansion 不算 exhausted (LIMIT=2, 1 < 2). 用老 re-expand 字串测 backward compat."""
        from explain_engine.schema.state import TraceEntry
        state = _make_state([("c_001", 1), ("p_001", 0)])
        state.reasoning_trace.append(TraceEntry(
            tick=0, action="reflect", target_node_id="c_001",
            gain_delta=0.5, llm_calls=1, timestamp="t",
            reflection_action="re-expand",
        ))
        _mock_reports(mocker, [("c_001", 0.3, 0.5)])
        action, target = reflect(state)
        assert action == "expand-downward"   # 仍 pick c_001
        assert target == "c_001"

    def test_two_consecutive_re_expand_exhausts_target(self, mocker) -> None:
        """连续 2 次 expansion 同 target → exhausted, 第 3 次不 pick (老 re-expand 字串测 backward compat)."""
        from explain_engine.schema.state import TraceEntry
        state = _make_state([("c_001", 1), ("p_001", 0)])
        for tick in range(2):
            state.reasoning_trace.append(TraceEntry(
                tick=tick, action="reflect", target_node_id="c_001",
                gain_delta=0.5, llm_calls=1, timestamp="t",
                reflection_action="re-expand",
            ))
        _mock_reports(mocker, [("c_001", 0.3, 0.5)])
        action, _ = reflect(state)
        # c_001 exhausted, 但没其他 candidate → fall through to continue
        assert action != "expand-downward"
        assert action != "re-expand"

    def test_expand_entries_do_not_break_streak(self, mocker) -> None:
        """expand entries 跳过, 不破 streak."""
        from explain_engine.schema.state import TraceEntry
        state = _make_state([("c_001", 1), ("p_001", 0)])
        # re-expand c_001, expand c_999 (interleaved), re-expand c_001 → c_001 应 exhausted
        state.reasoning_trace.append(TraceEntry(
            tick=0, action="reflect", target_node_id="c_001",
            gain_delta=0.5, llm_calls=1, timestamp="t",
            reflection_action="re-expand",
        ))
        state.reasoning_trace.append(TraceEntry(
            tick=1, action="expand", target_node_id="c_999",
            gain_delta=0.5, llm_calls=1, timestamp="t",
        ))
        state.reasoning_trace.append(TraceEntry(
            tick=2, action="reflect", target_node_id="c_001",
            gain_delta=0.5, llm_calls=1, timestamp="t",
            reflection_action="re-expand",
        ))
        _mock_reports(mocker, [("c_001", 0.3, 0.5)])
        action, _ = reflect(state)
        assert action != "expand-downward"   # c_001 exhausted
        assert action != "re-expand"

    def test_different_target_breaks_streak(self, mocker) -> None:
        """同 target 1 次 + 不同 target 1 次 + 同 target 1 次 → 不 exhausted (streak 断)."""
        from explain_engine.schema.state import TraceEntry
        state = _make_state([("c_001", 1), ("c_002", 1), ("p_001", 0)])
        state.reasoning_trace.append(TraceEntry(
            tick=0, action="reflect", target_node_id="c_001",
            gain_delta=0.5, llm_calls=1, timestamp="t",
            reflection_action="re-expand",
        ))
        state.reasoning_trace.append(TraceEntry(
            tick=1, action="reflect", target_node_id="c_002",
            gain_delta=0.5, llm_calls=1, timestamp="t",
            reflection_action="re-expand",
        ))
        # 倒序扫: 最后是 c_002 (count=1), c_001 不同 target → break. c_002 count=1 < 2 不 exhausted.
        _mock_reports(mocker, [("c_001", 0.3, 0.5), ("c_002", 0.4, 0.5)])
        action, _ = reflect(state)
        # c_002 不 exhausted, c_001 也不 exhausted (因为 c_002 在 c_001 后面打断了 c_001 streak)
        assert action == "expand-downward"

    def test_continue_action_does_not_break_count_within_window(self, mocker) -> None:
        """v2: continue 是 reflect entry, 算 window 大小但不加 c_001 count.

        re-expand c_001 → continue → re-expand c_001 (3 reflect entries, 都在 window=5 内).
        c_001 出现 2 次 ≥ LIMIT → exhausted (v1 语义会因 continue 打断 streak 不 exhausted).
        """
        from explain_engine.schema.state import TraceEntry
        state = _make_state([("c_001", 1), ("p_001", 0)])
        state.reasoning_trace.append(TraceEntry(
            tick=0, action="reflect", target_node_id="c_001",
            gain_delta=0.5, llm_calls=1, timestamp="t",
            reflection_action="re-expand",
        ))
        state.reasoning_trace.append(TraceEntry(
            tick=1, action="reflect", target_node_id=None,
            gain_delta=0.0, llm_calls=0, timestamp="t",
            reflection_action="continue",
        ))
        state.reasoning_trace.append(TraceEntry(
            tick=2, action="reflect", target_node_id="c_001",
            gain_delta=0.5, llm_calls=1, timestamp="t",
            reflection_action="re-expand",
        ))
        _mock_reports(mocker, [("c_001", 0.3, 0.5)])
        action, _ = reflect(state)
        assert action != "expand-downward"   # c_001 occurrence count=2 在 window=5 内, exhausted
        assert action != "re-expand"

    def test_prune_between_re_expands_does_not_break_count(self, mocker) -> None:
        """v2 修 v1 leak: prune 不该 reset 同 target re-expand 计数.

        re-expand c_001 → re-expand c_001 → prune d_999 → re-expand c_001 (4 reflects).
        v1: 倒序见 prune 即 break, c_001 count=1 → 不 exhausted (LEAK).
        v2: 4 entries 都在 window=5, c_001 count=3 → exhausted ✓
        """
        from explain_engine.schema.state import TraceEntry
        state = _make_state([("c_001", 1), ("p_001", 0)])
        for tick, action_target in [
            (0, ("re-expand", "c_001")),
            (1, ("re-expand", "c_001")),
            (2, ("prune", "d_999")),
            (3, ("re-expand", "c_001")),
        ]:
            state.reasoning_trace.append(TraceEntry(
                tick=tick, action="reflect", target_node_id=action_target[1],
                gain_delta=0.5 if action_target[0] == "re-expand" else 0.0,
                llm_calls=1 if action_target[0] == "re-expand" else 0,
                timestamp="t",
                reflection_action=action_target[0],
            ))
        _mock_reports(mocker, [("c_001", 0.3, 0.5)])
        action, _ = reflect(state)
        assert action != "expand-downward"   # 修 v1 leak
        assert action != "re-expand"

    def test_old_re_expand_outside_window_forgotten(self, mocker) -> None:
        """超出 LOOKBACK_WINDOW 的 re-expand entries 不算入 count.

        老 2 次 re-expand c_001 + 6 个 continue 把它们推出 window=5, c_001 重新可选.
        """
        from explain_engine.schema.state import TraceEntry
        state = _make_state([("c_001", 1), ("p_001", 0)])
        # 老 re-expand
        state.reasoning_trace.append(TraceEntry(
            tick=0, action="reflect", target_node_id="c_001",
            gain_delta=0.5, llm_calls=1, timestamp="t",
            reflection_action="re-expand",
        ))
        state.reasoning_trace.append(TraceEntry(
            tick=1, action="reflect", target_node_id="c_001",
            gain_delta=0.5, llm_calls=1, timestamp="t",
            reflection_action="re-expand",
        ))
        # 6 个 continue 把老 re-expand 推出 window=5
        for tick in range(2, 8):
            state.reasoning_trace.append(TraceEntry(
                tick=tick, action="reflect", target_node_id=None,
                gain_delta=0.0, llm_calls=0, timestamp="t",
                reflection_action="continue",
            ))
        # window=5, 倒序前 5 个都是 continue, 看不到老 re-expand
        _mock_reports(mocker, [("c_001", 0.3, 0.5)])
        action, target = reflect(state)
        assert action == "expand-downward"   # c_001 不再 exhausted (Wave 1 改用 expand-downward)
        assert target == "c_001"

    def test_exhausted_falls_through_to_prune(self, mocker) -> None:
        """c_001 exhausted + 有 low essentialness L2 → fall to prune."""
        from explain_engine.schema.state import TraceEntry
        state = _make_state([("c_001", 1), ("d_001", 2), ("p_001", 0)])
        for tick in range(2):
            state.reasoning_trace.append(TraceEntry(
                tick=tick, action="reflect", target_node_id="c_001",
                gain_delta=0.5, llm_calls=1, timestamp="t",
                reflection_action="re-expand",
            ))
        _mock_reports(mocker, [
            ("c_001", 0.3, 0.5),    # 仍 low consistency 但 exhausted
            ("d_001", 0.7, 0.02),   # low essentialness → prune candidate
        ])
        action, target = reflect(state)
        assert action == "prune"
        assert target == "d_001"
