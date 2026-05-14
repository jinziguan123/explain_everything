"""Phase 5/7 reasoning loop 主循环。

参考 docs/plans/2026-05-13-cognitive-engine-phase-5-design.md §5
+ Phase 7 design §6.3-6.5 (Wave C.2 加 reflect 分支)。

输入: state, llm, budget, on_tick (optional)
输出: stop_reason (str)
副作用:
  - state.tick / budget_remaining / last_gain_tick /
    last_reflection_change_tick / reasoning_trace 更新
  - state.graph 通过 expand_one_frontier / re_expand / prune 演化
  - on_tick(state) 每 tick 调用 (可用作 SessionStore.save callback,
    Ctrl-C 也不丢落盘)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from explain_engine.engines import expansion, reflection
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
    state.last_reflection_change_tick = 0
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
        reflection_action = None

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
                action = "reflect"

        if action == "reflect":
            refl_action, refl_target = reflection.reflect(state)
            reflection_action = refl_action

            if refl_action == "re-expand" and refl_target is not None:
                _new_ids, gain_delta = await expansion.re_expand(
                    state, refl_target, llm
                )
                llm_calls = 1
                target_id = refl_target
                state.last_reflection_change_tick = state.tick
            elif refl_action == "prune" and refl_target is not None:
                state.graph.remove_node(refl_target)
                target_id = refl_target
                state.last_reflection_change_tick = state.tick
            elif refl_action == "stop":
                # 强制触发 reflection_signaled_stop:
                # 把 last_reflection_change_tick 推到 stale 阈值之外, C.3
                # should_stop 会据此停止。
                state.last_reflection_change_tick = max(
                    0, state.tick - reflection.CONSISTENCY_STALE_TICKS - 1
                )
            # refl_action == "continue": no-op

            # Wave C.2: reflect tick 本身算"活动",不算 no_gain idle —
            # 否则 reflect-only 循环 3 tick 内就被 no_gain_for_3_ticks 误停。
            state.last_gain_tick = state.tick

        state.reasoning_trace.append(TraceEntry(
            tick=state.tick,
            action=action,
            target_node_id=target_id,
            gain_delta=gain_delta,
            llm_calls=llm_calls,
            timestamp=datetime.now(UTC).isoformat(),
            reflection_action=reflection_action,
        ))

        if gain_delta >= stop_mod.GAIN_THRESHOLD:
            state.last_gain_tick = state.tick

        state.tick += 1
        state.budget_remaining -= 1

        if on_tick is not None:
            on_tick(state)
