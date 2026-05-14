"""Phase 5 reasoning loop 主循环。

参考 docs/plans/2026-05-13-cognitive-engine-phase-5-design.md §5。

输入: state, llm, budget, on_tick (optional)
输出: stop_reason (str)
副作用:
  - state.tick / budget_remaining / last_gain_tick / reasoning_trace 更新
  - state.graph 通过 expand_one_frontier 长出新 d_NNN + causes edges
  - on_tick(state) 每 tick 调用 (可用作 SessionStore.save callback,
    Ctrl-C 也不丢落盘)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from explain_engine.engines import expansion
from explain_engine.llm.client import LLMClient
from explain_engine.runtime import stop as stop_mod
from explain_engine.runtime.scheduler import PhaseScheduler
from explain_engine.schema.state import CognitiveState, TraceEntry


async def run(
    state: CognitiveState,
    llm: LLMClient,
    budget: int,
    on_tick: Callable[[CognitiveState], None] | None = None,
    scheduler: PhaseScheduler | None = None,
) -> str:
    """主循环。返 stop_reason。

    Runtime 不写 session.meta.stage —— CLI 层在 run 完后改 stage 为 "converged"。
    """
    state.budget_remaining = budget
    state.tick = 0
    state.last_gain_tick = 0
    sched = scheduler or PhaseScheduler(K=4)

    while True:
        stop, reason = stop_mod.should_stop(state)
        if stop:
            assert reason is not None
            return reason

        action = sched.pick(state)
        target_id: str | None = None
        gain_delta = 0.0
        llm_calls = 0

        if action == "expand":
            frontier = state.graph.frontier_nodes()
            if frontier:
                target_id = frontier[0]
                _new_ids, gain_delta = await expansion.expand_one_frontier(
                    state, target_id, llm
                )
                llm_calls = 1
            else:
                # 不该到这: should_stop 会先触发 no_frontier。defensive：
                action = "evaluate"
        # action == "evaluate": no-op, snapshot only

        state.reasoning_trace.append(TraceEntry(
            tick=state.tick,
            action=action,
            target_node_id=target_id,
            gain_delta=gain_delta,
            llm_calls=llm_calls,
            timestamp=datetime.now(UTC).isoformat(),
        ))

        if gain_delta >= stop_mod.GAIN_THRESHOLD:
            state.last_gain_tick = state.tick

        state.tick += 1
        state.budget_remaining -= 1

        if on_tick is not None:
            on_tick(state)
