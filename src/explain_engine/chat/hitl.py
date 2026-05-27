"""Phase 9 Wave D.1: Smart HITL gate.

For tools with requires_hitl=True (currently only add_observation):
- If input.source == "user_explicit" → auto-approve (user already said add)
- If input.source == "llm_inferred" → prompt user via injected prompt_fn

For tools with requires_hitl=False → auto-approve (no gate).

prompt_fn 注入说明:
- caller (query_loop) 显式传 chat.input_provider; cli 模式是 prompt_toolkit-friendly
  async fn (read_input(pt_session, prompt_text=prompt)), textual 模式是 None.
- prompt_fn=None 时需 prompt → log warning + safe deny (return False). 防 textual
  hold stdin 时 builtin input 永远 block 导致 app 整体死锁 (P-1 hotfix, 同
  9838507 commit /resume 死锁机理).
- test 注入 AsyncMock 仍走老路径; 不 verify 死锁的测试 (大多数) 显式传 prompt_fn.

设计动机:
- HITL ("Human In The Loop") gate 是 Phase 9 安全网: LLM 可以提议"加 X 观察",
  但具体 mutate graph 前必须用户确认; 防 LLM 想错 / 编 / 误导致 graph 失真.
- "Smart" gate 不是无脑都问: user 自己说 "加这个观察" 已是确认, 重复问反而烦.
  规则用 input.source 区分: user_explicit 跳, llm_inferred 必问.

参考:
- docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave D.1.
- commit 9838507 message "其他受影响 slash handler 审计" 段 (P-1 follow-up).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

    from explain_engine.chat.tools import Tool, ToolContext


logger = logging.getLogger(__name__)


# Prompt function signature: (prompt_text) -> "y" / "n" / something else
PromptFn = Callable[[str], Awaitable[str]]


async def _default_prompt(prompt_text: str) -> str:
    """Builtin input() wrapped in asyncio.to_thread.

    P-1 注意: 此函数不再是 hitl_gate 的默认 prompt_fn (会在 textual 模式
    hold stdin 死锁). 仅保留给显式调用方 (纯 builtin REPL / 测试). 生产
    路径都应该用 chat.input_provider (cli: prompt_toolkit; textual: None →
    safe deny).
    """
    import asyncio
    return (await asyncio.to_thread(input, prompt_text)).strip().lower()


async def hitl_gate(
    tool: Tool,
    parsed_input: BaseModel,
    ctx: ToolContext,
    prompt_fn: PromptFn | None = None,
) -> bool:
    """Decide whether to approve the tool call.

    Args:
        tool: 要调的 Tool (读 requires_hitl + name)
        parsed_input: Pydantic 校验后的 input instance (读 source 字段)
        ctx: ToolContext (Wave D.1 暂未用; 给未来 per-state 策略保留)
        prompt_fn: async (prompt_text) -> "y"/"n"/... — caller (query_loop)
            传 chat.input_provider. None 表示 "无 interactive prompt 可用"
            (textual 模式), 触发 safe deny.

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
        if prompt_fn is None:
            # P-1: textual 模式无 interactive prompt → safe deny. 不可调
            # _default_prompt (会 hold stdin 死锁). LLM 收 "user denied via
            # HITL gate" 反馈自决下步.
            logger.warning(
                "HITL gate: LLM 推测 add_observation %r 但无 input_provider "
                "(textual 模式或 caller 未注入), 自动拒. 如需 approve, 用户"
                "可显式 /add_observation 或在 REPL 模式 (cli) 走 prompt 确认.",
                name,
            )
            return False
        prompt_text = (
            f"\nLLM wants to add observation: {name!r}. Approve? (y/n): "
        )
        try:
            answer = await prompt_fn(prompt_text)
        except (EOFError, KeyboardInterrupt):
            # 用户 abort (Ctrl-D / Ctrl-C) — 当 deny 处理, 不让 exception bubble
            # 到 query_loop 报 "tool failed" (那是 tool crash 语义, 不对).
            return False
        return answer.strip().lower() in {"y", "yes"}

    # Future hitl-required tools: default to auto-approve until per-tool logic added.
    # 留口子, 别 raise — 未加新 hitl 工具时不该阻断 loop.
    return True
