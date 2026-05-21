"""Phase 14: chat slash 命令的 stage gate + transition + hint 装饰器."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from explain_engine.chat.session import ChatEvent, ChatSession

Handler = Callable[["ChatSession", list[str]], Awaitable[list["ChatEvent"]]]


HINTS_BY_KEY: dict[str, str] = {}  # 后续 task 填


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
                return [ChatEvent(
                    type="slash_error",
                    content=(
                        f"/{_cmd_name(fn)} 在当前 stage={stage!r} 不允许 "
                        f"(需 stage ∈ {allowed})."
                    ),
                )]

            return await fn(chat, args)
        return wrapped
    return deco


def _cmd_name(fn) -> str:
    """`_handle_run` → `run`. 用于 error 文案."""
    name = fn.__name__
    return name.removeprefix("_handle_") if name.startswith("_handle_") else name
