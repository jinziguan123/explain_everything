"""Phase 14: chat slash 命令的 stage gate + transition + hint 装饰器.

Phase 15 起 HINTS_BY_KEY 改 import from chat_copy (single source).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TYPE_CHECKING

from explain_engine.chat.chat_copy import HINTS_BY_KEY, err_stage_not_allowed

if TYPE_CHECKING:
    from explain_engine.chat.session import ChatEvent, ChatSession

Handler = Callable[["ChatSession", list[str]], Awaitable[list["ChatEvent"]]]


__all__ = ["HINTS_BY_KEY", "with_stage_gate"]


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

            # Ephemeral: 装饰器短路, 把控让给 handler 内部 _ephemeral_reject.
            # EphemeralChatSession 无 _session 属性 + stage 概念不适用.
            if getattr(chat, "is_ephemeral", False):
                return await fn(chat, args)

            stage = chat._session.meta.stage

            # ① gate check
            if allowed is not None and stage not in allowed:
                events = [ChatEvent(
                    type="slash_error",
                    content=err_stage_not_allowed(_cmd_name(fn), stage, allowed),
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
            #
            # Phase 17.1 Wave 8 修正: 老代码用 entry stage (`stage`) 比, 假设
            # handler 不改 stage 内部. _handle_compress 因 mid-stage save 改成
            # 'insight_pending', entry='done' == success_stage='done' 时不刷回 →
            # 错误 final stage='insight_pending'.
            # 改用 **current stage** (handler 跑后的 chat._session.meta.stage) 比.
            # entry=done + handler 改 insight_pending → current insight_pending !=
            # done → 刷回 + persist (Wave 8 用例). entry=done + handler 不改 →
            # current=done == success_stage → 不动 (idempotent, 保留老行为).
            if (
                success_stage is not None
                and chat._session.meta.stage != success_stage
            ):
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
