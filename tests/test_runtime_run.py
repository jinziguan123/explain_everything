"""Runtime.run — Phase 5 主循环 integration test (mock LLM)。"""

import pytest

from explain_engine.llm.client import Response
from explain_engine.runtime.runtime import run
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


class FakeLLM:
    """Mock LLM: 每次 chat 返一个 driver。"""

    def __init__(self, plausibility: int = 4) -> None:
        self.plausibility = plausibility
        self.call_count = 0

    async def chat(self, messages, schema=None, model=None):
        self.call_count += 1
        return Response(
            text="ignored",
            parsed={"drivers": [{
                "name": f"driver-{self.call_count}",
                "description": "d",
                "mechanism": "m",
                "plausibility": self.plausibility,
            }]},
            model="fake",
            usage={"input_tokens": 0, "output_tokens": 0},
        )


def _state_with_n_frontiers(n: int) -> CognitiveState:
    g = ExplanationGraph(root_question="why")
    g.add_node(VariableNode(id="p_001", name="p1", description="d", abstraction_level=0,
                            confidence=0.8, epistemic="observation"))
    for i in range(1, n + 1):
        cid = f"c_{i:03d}"
        g.add_node(VariableNode(id=cid, name=cid, description="d", abstraction_level=1,
                                confidence=0.7, epistemic="insight"))
        g.add_edge(RelationEdge(id=f"e_{cid}", source_node=cid, target_node="p_001",
                                relation_type="manifests_as", confidence=0.7,
                                mechanism_description="m"))
    return CognitiveState(graph=g, budget_remaining=0, root_question="why")


def _state_with_2_frontiers() -> CognitiveState:
    return _state_with_n_frontiers(2)


@pytest.mark.asyncio
async def test_run_budget_5_completes() -> None:
    """2 frontier + budget=5, plausibility=4 (gain=0.8 has gain): expand 2 次
    后 frontier 空 → no_frontier_remaining。budget + tick 守恒。"""
    state = _state_with_2_frontiers()
    llm = FakeLLM(plausibility=4)
    # cv_stop=False: 本测试验证 budget/tick 守恒语义; fixture 无 L0 → CV 恒 0,
    # Phase G 的 cv_converged 会在 3 tick 提前停 (那是另一个测试的事)。
    reason = await run(state, llm, budget=5, cv_stop=False)
    assert reason in {"budget_exhausted", "no_gain_for_3_ticks", "no_frontier_remaining"}
    # budget_remaining + tick 守恒
    assert state.budget_remaining + state.tick == 5
    assert state.tick >= 2
    assert len(state.reasoning_trace) == state.tick


@pytest.mark.asyncio
async def test_run_budget_exhausted() -> None:
    """足够多 frontier (10) + budget=5: 5 tick 把 budget 吃光。"""
    state = _state_with_n_frontiers(10)
    llm = FakeLLM(plausibility=4)
    # cv_stop=False: 同上, 验证 budget 吃光语义不被 cv_converged 截断。
    reason = await run(state, llm, budget=5, cv_stop=False)
    assert reason == "budget_exhausted"
    assert state.budget_remaining == 0
    assert state.tick == 5


@pytest.mark.asyncio
async def test_run_writes_reasoning_trace() -> None:
    state = _state_with_2_frontiers()
    llm = FakeLLM(plausibility=4)
    await run(state, llm, budget=5)
    # 每 tick 1 entry
    assert all(e.action in {"expand", "reflect"} for e in state.reasoning_trace)
    # 第一个 tick 是 expand (K=4 modulo)
    assert state.reasoning_trace[0].action == "expand"


@pytest.mark.asyncio
async def test_run_updates_last_gain_tick() -> None:
    state = _state_with_2_frontiers()
    llm = FakeLLM(plausibility=4)   # gain = 4/5 = 0.8 >= 0.1
    await run(state, llm, budget=5)
    # 至少 1 个 tick 触发 gain 更新
    assert state.last_gain_tick > 0


@pytest.mark.asyncio
async def test_run_stops_on_low_gain() -> None:
    """plausibility=1 → gain=0.2，patch GAIN_THRESHOLD 到 0.5 让 no_gain 触发。
    需要 ≥3 frontier (tick 0/1/2 都成功 expand 而非 no_frontier)。"""
    from explain_engine.runtime import stop as stop_mod

    state = _state_with_n_frontiers(5)
    llm = FakeLLM(plausibility=1)   # gain = 0.2 < 0.5 threshold
    original = stop_mod.GAIN_THRESHOLD
    stop_mod.GAIN_THRESHOLD = 0.5
    try:
        reason = await run(state, llm, budget=20)
        assert reason == "no_gain_for_3_ticks"
    finally:
        stop_mod.GAIN_THRESHOLD = original


@pytest.mark.asyncio
async def test_run_no_frontier_stops_immediately() -> None:
    g = ExplanationGraph(root_question="why")
    # 没有任何 level=1 节点
    state = CognitiveState(graph=g, budget_remaining=0, root_question="why")
    llm = FakeLLM()
    reason = await run(state, llm, budget=10)
    assert reason == "no_frontier_remaining"
    assert state.tick == 0


@pytest.mark.asyncio
async def test_run_calls_on_tick_callback() -> None:
    state = _state_with_2_frontiers()
    llm = FakeLLM(plausibility=4)
    seen: list[int] = []

    def on_tick(s: CognitiveState) -> None:
        seen.append(s.tick)

    await run(state, llm, budget=5, on_tick=on_tick)
    assert len(seen) == state.tick   # 每 tick 调一次


@pytest.mark.asyncio
async def test_run_does_not_set_converged() -> None:
    """Runtime.run 自身不改 stage (由 CLI 层负责)。
    state 没有 meta 字段, 这个 test 只是断言 run 返字符串 reason。"""
    state = _state_with_2_frontiers()
    llm = FakeLLM(plausibility=4)
    reason = await run(state, llm, budget=5)
    assert isinstance(reason, str)
    assert isinstance(state.reasoning_trace, list)
