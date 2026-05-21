"""Phase 14: chat slash 命令的 stage gate + transition + hint 装饰器."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from explain_engine.chat.session import ChatEvent, ChatSession

Handler = Callable[["ChatSession", list[str]], Awaitable[list["ChatEvent"]]]


HINTS_BY_KEY: dict[str, str] = {
    "need_promote_first": (
        "session 还没启动 — 自然语言输入一个 question 先建 session, "
        "然后再 /compress."
    ),
    "need_compress_first": (
        "需要先 /compress 压缩 graph 抽出 abstraction 层. "
        "当前 stage 不允许这个命令."
    ),
    "after_compress": (
        "▸ 下一步可选:\n"
        "  /run — 自动跑 reasoning loop 推 drivers (推荐)\n"
        "  /predict <现象> — 预测某干预的下游效果\n"
        "  /counterfactual <现象> — 反事实分析"
    ),
    "after_run": (
        "▸ session 已收敛. 可选:\n"
        "  /predict <现象> — 干预预测\n"
        "  /counterfactual <现象> — 反事实\n"
        "  /show — 看完整 graph"
    ),
    "after_inference": (
        "▸ 可继续 /predict 或 /counterfactual 探索, /show 看 graph 更新."
    ),
    "after_rescore": (
        "▸ edge confidence 已重评. /show 看变化, /run 重跑 reasoning loop."
    ),
}


def with_stage_gate(
    *,
    allowed: list[str] | None = None,
    success_stage: str | None = None,
    fail_hint_key: str | None = None,
    success_hint_key: str | None = None,
) -> Callable[[Handler], Handler]:
    """装饰 slash handler 加 stage gate + transition + hint.

    allowed=None → 任意 stage 都可调.
    success_stage=None → 不动 stage.
    fail_hint_key=None → gate 拒时不附 hint.
    success_hint_key=None → 成功后不附 hint.
    """
    def deco(fn: Handler) -> Handler:
        @wraps(fn)
        async def wrapped(chat, args):
            from explain_engine.chat.session import ChatEvent
            stage = chat._session.meta.stage

            # ① gate check
            if allowed is not None and stage not in allowed:
                events = [ChatEvent(
                    type="slash_error",
                    content=(
                        f"/{_cmd_name(fn)} 在当前 stage={stage!r} 不允许 "
                        f"(需 stage ∈ {allowed})."
                    ),
                )]
                if fail_hint_key and fail_hint_key in HINTS_BY_KEY:
                    events.append(ChatEvent(
                        type="slash_next_step_hint",
                        content=HINTS_BY_KEY[fail_hint_key],
                    ))
                return events

            # ② 调 handler
            events = await fn(chat, args)

            # ③ handler 自己 yield error → 不 transition, 不 hint
            has_error = any(e.type == "slash_error" for e in events)
            if has_error:
                return events

            # ④ stage transition + persist
            if success_stage is not None and stage != success_stage:
                chat._session.meta.stage = success_stage
                if (
                    hasattr(chat, "persist")
                    and not getattr(chat, "is_ephemeral", False)
                ):
                    try:
                        chat.persist()
                    except Exception:
                        pass  # persist 失败不阻断 hint 显示

            # ⑤ success hint
            if success_hint_key and success_hint_key in HINTS_BY_KEY:
                events.append(ChatEvent(
                    type="slash_next_step_hint",
                    content=HINTS_BY_KEY[success_hint_key],
                ))

            return events
        return wrapped
    return deco


def _cmd_name(fn) -> str:
    """`_handle_run` → `run`. 用于 error 文案."""
    name = fn.__name__
    return name.removeprefix("_handle_") if name.startswith("_handle_") else name
