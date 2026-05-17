"""Phase 9 Wave D.1: Smart HITL gate.

For tools with requires_hitl=True (currently only add_observation):
- If input.source == "user_explicit" → auto-approve (user already said add)
- If input.source == "llm_inferred" → prompt user via Rich console.input

For tools with requires_hitl=False → auto-approve (no gate).

Pluggable prompt function for testability (default uses asyncio.to_thread(input);
tests inject AsyncMock to avoid blocking the event loop).

设计动机:
- HITL ("Human In The Loop") gate 是 Phase 9 安全网: LLM 可以提议"加 X 观察",
  但具体 mutate graph 前必须用户确认; 防 LLM 想错 / 编 / 误导致 graph 失真.
- "Smart" gate 不是无脑都问: user 自己说 "加这个观察" 已是确认, 重复问反而烦.
  规则用 input.source 区分: user_explicit 跳, llm_inferred 必问.
- prompt_fn 注入是为 test (AsyncMock) + 未来 GUI/Web mode 换 prompt 实现.

参考 docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave D.1.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

    from explain_engine.chat.tools import Tool, ToolContext


# Prompt function signature: (prompt_text) -> "y" / "n" / something else
PromptFn = Callable[[str], Awaitable[str]]


async def _default_prompt(prompt_text: str) -> str:
    """Default prompt using builtin input() wrapped in asyncio.to_thread.

    builtin input() 是 blocking syscall, 直接在 async loop 里调会冻 event loop;
    asyncio.to_thread 把它扔到 worker thread 跑, 当前 coroutine 让出 control.
    返值 strip + lower 后返, 让 caller 比较 'y' 即可.
    """
    import asyncio
    return (await asyncio.to_thread(input, prompt_text)).strip().lower()


async def hitl_gate(
    tool: Tool,
    parsed_input: BaseModel,
    ctx: ToolContext,
    prompt_fn: PromptFn = _default_prompt,
) -> bool:
    """Decide whether to approve the tool call.

    Args:
        tool: 要调的 Tool (读 requires_hitl + name)
        parsed_input: Pydantic 校验后的 input instance (读 source 字段)
        ctx: ToolContext (Wave D.1 暂未用; 给未来 per-state 策略保留)
        prompt_fn: async (prompt_text) -> "y"/"n"/... (test 用 AsyncMock 替)

    Returns:
        True if approved (proceed with tool.call), False if denied (skip tool.call,
        caller writes "user denied via HITL gate" as tool result).
    """
    # ctx 参数当前未用, 但作为 hook 保留 (D.2+ 可能根据 graph state 调整 gate 策略)
    del ctx
    if not tool.requires_hitl:
        return True

    # add_observation smart gate
    if tool.name == "add_observation":
        source = getattr(parsed_input, "source", None)
        if source == "user_explicit":
            return True
        # llm_inferred → ask user
        name = getattr(parsed_input, "name", "<unknown>")
        prompt_text = (
            f"\nLLM wants to add observation: {name!r}. Approve? (y/n): "
        )
        answer = await prompt_fn(prompt_text)
        return answer == "y"

    # Future hitl-required tools: default to auto-approve until per-tool logic added.
    # 留口子, 别 raise — 未加新 hitl 工具时不该阻断 loop.
    return True
