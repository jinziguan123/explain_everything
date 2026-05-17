"""Phase 9 Wave D.1: smart HITL gate tests.

hitl_gate(tool, parsed_input, ctx, prompt_fn) 行为矩阵:
- non-HITL tool (check / read_node / expand / ...) → True (auto-approve)
- add_observation + source="user_explicit" → True (auto-approve, user 已主动说加)
- add_observation + source="llm_inferred" → 调 prompt_fn, "y" → True, else False
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from explain_engine.chat.hitl import _default_prompt, hitl_gate
from explain_engine.chat.tools import (
    _AddObservationInput,
    _CheckInput,
    add_observation_tool,
    check_tool,
)
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState


def _make_ctx():
    from explain_engine.chat.tools import ToolContext
    g = ExplanationGraph(root_question="why?")
    state = CognitiveState(graph=g, budget_remaining=10, root_question="why?")
    return ToolContext(state=state, llm=None)


class TestHITLNonHitlTool:
    @pytest.mark.asyncio
    async def test_non_hitl_tool_auto_approved(self) -> None:
        """tool.requires_hitl=False → 直接 True, 不调 prompt_fn."""
        ctx = _make_ctx()
        parsed = _CheckInput(target_id=None)
        prompt = AsyncMock(return_value="n")  # 就算 prompt 返 n 也不该调
        approved = await hitl_gate(check_tool, parsed, ctx, prompt_fn=prompt)
        assert approved is True
        prompt.assert_not_called()


class TestHITLAddObservation:
    @pytest.mark.asyncio
    async def test_add_observation_user_explicit_auto_approved(self) -> None:
        """source='user_explicit' → 直接 True, 不调 prompt."""
        ctx = _make_ctx()
        parsed = _AddObservationInput(
            name="x", description="d", source="user_explicit",
        )
        prompt = AsyncMock(return_value="n")
        approved = await hitl_gate(add_observation_tool, parsed, ctx, prompt_fn=prompt)
        assert approved is True
        prompt.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_observation_llm_inferred_prompts_user_yes_approved(self) -> None:
        """source='llm_inferred' + user 答 y → True."""
        ctx = _make_ctx()
        parsed = _AddObservationInput(
            name="x", description="d", source="llm_inferred",
        )
        prompt = AsyncMock(return_value="y")
        approved = await hitl_gate(add_observation_tool, parsed, ctx, prompt_fn=prompt)
        assert approved is True
        prompt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_observation_llm_inferred_prompts_user_no_denied(self) -> None:
        """source='llm_inferred' + user 答 n → False."""
        ctx = _make_ctx()
        parsed = _AddObservationInput(
            name="x", description="d", source="llm_inferred",
        )
        prompt = AsyncMock(return_value="n")
        approved = await hitl_gate(add_observation_tool, parsed, ctx, prompt_fn=prompt)
        assert approved is False

    @pytest.mark.asyncio
    async def test_add_observation_llm_inferred_prompts_user_garbage_denied(self) -> None:
        """任何 != 'y' 的输入 → False (safe default)."""
        ctx = _make_ctx()
        parsed = _AddObservationInput(
            name="x", description="d", source="llm_inferred",
        )
        prompt = AsyncMock(return_value="maybe")
        approved = await hitl_gate(add_observation_tool, parsed, ctx, prompt_fn=prompt)
        assert approved is False


class TestHitlGateApprovalWords:
    """I1 regression: accept both 'y' and 'yes' (and case-insensitive)."""

    @pytest.mark.asyncio
    async def test_yes_word_approved(self) -> None:
        from unittest.mock import AsyncMock

        from explain_engine.chat.hitl import hitl_gate
        from explain_engine.chat.tools import ToolContext, add_observation_tool

        prompt = AsyncMock(return_value="yes")
        parsed = add_observation_tool.input_schema(
            name="x", description="d", source="llm_inferred",
        )
        ctx = ToolContext(state=None)  # ctx unused by hitl_gate
        assert await hitl_gate(add_observation_tool, parsed, ctx, prompt) is True

    @pytest.mark.asyncio
    async def test_y_letter_approved(self) -> None:
        from unittest.mock import AsyncMock

        from explain_engine.chat.hitl import hitl_gate
        from explain_engine.chat.tools import ToolContext, add_observation_tool

        prompt = AsyncMock(return_value="y")
        parsed = add_observation_tool.input_schema(
            name="x", description="d", source="llm_inferred",
        )
        ctx = ToolContext(state=None)
        assert await hitl_gate(add_observation_tool, parsed, ctx, prompt) is True

    @pytest.mark.asyncio
    async def test_maybe_denied(self) -> None:
        from unittest.mock import AsyncMock

        from explain_engine.chat.hitl import hitl_gate
        from explain_engine.chat.tools import ToolContext, add_observation_tool

        prompt = AsyncMock(return_value="maybe")
        parsed = add_observation_tool.input_schema(
            name="x", description="d", source="llm_inferred",
        )
        ctx = ToolContext(state=None)
        assert await hitl_gate(add_observation_tool, parsed, ctx, prompt) is False


class TestHitlGateInterruption:
    """I2 regression: Ctrl-D (EOFError) / Ctrl-C → deny, not tool failure."""

    @pytest.mark.asyncio
    async def test_eof_denies(self) -> None:
        from unittest.mock import AsyncMock

        from explain_engine.chat.hitl import hitl_gate
        from explain_engine.chat.tools import ToolContext, add_observation_tool

        prompt = AsyncMock(side_effect=EOFError())
        parsed = add_observation_tool.input_schema(
            name="x", description="d", source="llm_inferred",
        )
        ctx = ToolContext(state=None)
        assert await hitl_gate(add_observation_tool, parsed, ctx, prompt) is False

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_denies(self) -> None:
        from unittest.mock import AsyncMock

        from explain_engine.chat.hitl import hitl_gate
        from explain_engine.chat.tools import ToolContext, add_observation_tool

        prompt = AsyncMock(side_effect=KeyboardInterrupt())
        parsed = add_observation_tool.input_schema(
            name="x", description="d", source="llm_inferred",
        )
        ctx = ToolContext(state=None)
        assert await hitl_gate(add_observation_tool, parsed, ctx, prompt) is False


class TestDefaultPrompt:
    @pytest.mark.asyncio
    async def test_default_prompt_uses_asyncio_to_thread(self) -> None:
        """_default_prompt 走 asyncio.to_thread(input, ...) 不实际阻塞."""
        with patch(
            "asyncio.to_thread",
            new=AsyncMock(return_value="  Y  "),
        ) as mock_to_thread:
            answer = await _default_prompt("prompt? ")
            assert answer == "y"  # strip + lower
            mock_to_thread.assert_awaited_once()
            # asyncio.to_thread(input, "prompt? ")
            args, _ = mock_to_thread.call_args
            assert args[0] is input
            assert args[1] == "prompt? "
