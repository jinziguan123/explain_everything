"""Phase 9 Wave D.2: Post-turn hooks (Claude Code PostSamplingHook pattern).

每个 TurnComplete 后, ChatSession.handle_user_input 依次跑 3 个 hook:

1. reflect_post_turn (sync, 0 LLM)
   - 刷新 acceptance cache → 调 reflection.reflect
   - 返"人话"hint str (e.g. "reflect 建议: expand-downward (target=c_002)")
   - hint 写到 chat.next_turn_hint, 下一 turn assemble_system_prompt 读
   - LLM 看到反思建议自己决定 (不强制执行 — 哲学锚点 §10.1 meta-cognition)

2. lifecycle_post_turn (sync, 0 LLM)
   - 调 lifecycle.update_lifecycle 推进 active → stale → decayed 状态机
   - 返 changes dict (调试/log 用)
   - 哲学锚点 §11.3 entropy: 自动 decay 控 graph 体积

3. session_memory_writer (async fire-and-forget, 1 LLM call 每 5 turn)
   - turn_count % 5 == 0 才跑; turn_count == 0 直接 skip
   - snapshot transcript prefix (去掉最近 2 turn 的 message) → LLM 总结 → 写 memory.md
   - 错误 log warning, 不传播 (background task, 不能让 user 看到)
   - 类似 Claude Code 的 PostSamplingHook background summarize 模式

参考:
- docs/plans/2026-05-17-conversational-cognitive-engine-design.md
- docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave D.2
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from explain_engine.engines import lifecycle as lifecycle_mod
from explain_engine.engines import reflection
from explain_engine.engines.simulation import aggregate_acceptance

if TYPE_CHECKING:
    from explain_engine.chat.session import ChatSession
    from explain_engine.llm.client import LLMClient
    from explain_engine.schema.state import CognitiveState

logger = logging.getLogger(__name__)


SESSION_MEMORY_TRIGGER_INTERVAL: int = 5
"""每 N turn 跑一次 session_memory_writer. 5 是 Wave D.2 经验值, Wave E 可调."""

_TURN_TO_MSG_RATIO: int = 2
"""粗略 heuristic: 每 turn 产 ~2-3 message (user + assistant). 用来算
'保留最近 N turn 的 message 不压, 之前的全 summarize'."""


def reflect_post_turn(state: CognitiveState) -> str | None:
    """Run reflection.reflect → 返人话 hint str (or None if continue).

    内部流程:
        1. 刷新 acceptance cache (state.last_acceptance_report = aggregate_acceptance(state))
           — reflect 优先用 cached report, 不刷新会拿陈旧分数.
        2. 调 reflection.reflect(state) → (action, target_id).
        3. 把 (action, target_id) 渲染成中文 hint str, 给 next turn assemble_system_prompt.

    Args:
        state: 当前 CognitiveState (mutated: last_acceptance_report 被刷新).

    Returns:
        hint str (e.g. "reflect 建议: expand-downward (target=c_002)") or None
        if reflect 返 "continue".

    Note:
        - hint 不强制执行 — 仅作 LLM next turn 看到的提示.
        - lifecycle_post_turn 也会推进 lifecycle; 此 hook 不主动推进 (testability:
          单独 reflect hook 时 graph lifecycle 状态可控).
    """
    # 1. 刷新 acceptance cache (reflect 用 cached report)
    state.last_acceptance_report = aggregate_acceptance(state)

    # 2. 调 reflect
    action, target = reflection.reflect(state)

    # 3. 渲染 hint
    if action == "continue":
        return None
    if action == "stop":
        return "reflect 建议: stop (graph 已收敛)"
    if target:
        return f"reflect 建议: {action} (target={target})"
    return f"reflect 建议: {action}"


def lifecycle_post_turn(
    state: CognitiveState,
    current_tick: int,
) -> dict[str, str]:
    """Advance lifecycle state machine (active → stale → decayed).

    Thin wrapper over lifecycle.update_lifecycle — 抽 hook 是为了 (a) 在 session.py
    里统一 post-turn hooks 调用点, (b) testability (可单独 mock).

    Args:
        state: 当前 CognitiveState (mutated: nodes' lifecycle_state / age_ticks /
               stale_since_tick).
        current_tick: 当前 tick (Wave D.2 用 chat_state.turn_count 作 tick).

    Returns:
        {node_id: new_state} 变更日志 (只含状态变更的节点; 用 debug log / CLI 反馈).
    """
    return lifecycle_mod.update_lifecycle(state, current_tick=current_tick)


async def session_memory_writer(
    chat: ChatSession,
    llm: LLMClient,
) -> None:
    """Background hook: summarize transcript prefix → write memory.md.

    触发条件:
        - chat.chat_state.turn_count > 0
        - chat.chat_state.turn_count % SESSION_MEMORY_TRIGGER_INTERVAL == 0
        - 否则直接 return (no-op).

    流程:
        1. Snapshot transcript prefix (去掉最近 _TURN_TO_MSG_RATIO * 2 个 message).
        2. 把 prefix 喂给 LLM (system="压缩成 markdown session memory") 拿 summary text.
        3. 写到 memory.md + 更新 chat.memory_md (内存 cache).
        4. 任何异常 → log warning, 不传播 (background task 不能让 user 看到 stack).

    Fire-and-forget 语义: caller (session.py) 用 asyncio.create_task 起此 coro,
    不 await 完成. 故此函数 must not raise to event loop.

    Args:
        chat: ChatSession (读 transcript / storage / sid, mutate chat.memory_md).
        llm: LLMClient (调 chat 方法; 没 chat 方法 → log warning + return).
    """
    # ── 触发条件 ──
    if chat.chat_state.turn_count == 0:
        return   # 什么都没 summarize
    if chat.chat_state.turn_count % SESSION_MEMORY_TRIGGER_INTERVAL != 0:
        return   # 不到触发点

    try:
        # ── 1. Snapshot prefix (保留最近 2 turn 的 message 不压) ──
        snapshot_idx = max(0, len(chat.transcript) - _TURN_TO_MSG_RATIO * 2)
        prefix_msgs = chat.transcript[:snapshot_idx]
        if not prefix_msgs:
            return   # 没 prefix 可 summarize

        # ── 2. 调 LLM ──
        # NOTE: LLMClient.chat(messages, schema, model) 签名 — 把 system prompt 当
        # 第一条 Message(role="system", ...). content 字段必须是 str (Message schema).
        if not hasattr(llm, "chat"):
            logger.warning(
                "session_memory_writer: LLMClient lacks .chat method; skipping"
            )
            return

        sys_prompt = (
            "把以下 chat 对话压缩成 markdown session memory, "
            "给未来 LLM context 用. 包含: question / "
            "graph 演化关键时刻 / 已 explored 假设 / 未决 thread."
        )
        # 把 prefix_msgs (chat transcript dict 格式) 转 Message-compatible dict.
        # content 可能是 list (assistant 含 tool_use) — 拍扁成 str 再喂 LLM.
        from explain_engine.llm.client import Message
        messages = [Message(role="system", content=sys_prompt)]
        for m in prefix_msgs:
            content = m.get("content", "")
            if not isinstance(content, str):
                # tool_use / tool_result 等 list[dict] → repr 一下
                content = str(content)
            role = m.get("role", "user")
            # transcript 里只可能是 user / assistant; system 已加. 防御性 fallback user.
            if role not in ("user", "assistant"):
                role = "user"
            messages.append(Message(role=role, content=content))

        response = await llm.chat(messages, schema=None)
        # Response shape: .text str (per llm/client.py)
        summary = getattr(response, "text", None) or str(response)

        if not summary:
            logger.warning(
                "session_memory_writer: LLM returned empty summary for %s; skipping",
                chat.sid,
            )
            return

        # ── 3. 写 memory.md ──
        chat.storage.save_memory(chat.sid, summary)
        chat.memory_md = summary
        logger.info(
            "session_memory_writer wrote %d chars to memory.md for %s at turn %d",
            len(summary), chat.sid, chat.chat_state.turn_count,
        )
    except Exception as exc:
        # Background task: 不传播, log warning 即可.
        logger.warning(
            "session_memory_writer failed for %s: %s: %s",
            chat.sid, type(exc).__name__, exc,
        )
