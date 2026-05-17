"""Phase 9 Wave E.1: 3-tier compaction tests.

3 tiers (cheap → expensive):
1. micro_compact — tool_result > STALE_TURN_AGE 替 stub
2. session_memory_splice — memory.md 存在 → prepend system msg + 删 prefix
3. emergency_compact — token > EMERGENCY_TOKEN_LIMIT → LLM summarize all

参考 docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave E.1.
"""

from __future__ import annotations

import pytest

from explain_engine.chat.compaction import (
    EMERGENCY_TOKEN_LIMIT,
    STALE_TURN_AGE,
    emergency_compact,
    estimate_token_count,
    micro_compact,
    prepare_messages,
    session_memory_splice,
)

# ─── Tier 1: microCompact ───


class TestMicroCompact:
    def test_keeps_recent_tool_result(self):
        transcript = [{
            "role": "user", "turn": 5,
            "content": [{
                "type": "tool_result", "tool_use_id": "t1", "content": "data",
            }],
        }]
        result = micro_compact(transcript, current_turn=6)
        assert result[0]["content"][0]["content"] == "data"

    def test_replaces_stale_tool_result(self):
        transcript = [{
            "role": "user", "turn": 0,
            "content": [{
                "type": "tool_result", "tool_use_id": "t1", "content": "old data",
            }],
        }]
        result = micro_compact(transcript, current_turn=10)
        assert "stale" in result[0]["content"][0]["content"].lower()
        assert result[0]["content"][0]["tool_use_id"] == "t1"  # preserved

    def test_preserves_user_text(self):
        transcript = [{
            "role": "user", "turn": 0,
            "content": "regular user message",
        }]
        result = micro_compact(transcript, current_turn=10)
        assert result[0]["content"] == "regular user message"

    def test_preserves_assistant(self):
        transcript = [{
            "role": "assistant", "turn": 0,
            "content": [{"type": "text", "text": "old assistant reply"}],
        }]
        result = micro_compact(transcript, current_turn=10)
        assert result[0]["content"][0]["text"] == "old assistant reply"


# ─── Tier 2: sessionMemory splice ───


class TestSessionMemorySplice:
    def test_no_memory_no_change(self):
        transcript = [{"role": "user", "turn": 0, "content": "x"}]
        assert session_memory_splice(transcript, "", 0) == transcript

    def test_splices_memory_drops_prefix(self):
        transcript = [
            {"role": "user", "turn": 0, "content": "old"},
            {"role": "assistant", "turn": 0, "content": "old reply"},
            {"role": "user", "turn": 5, "content": "recent"},
        ]
        result = session_memory_splice(
            transcript, "# Summary\nblah", last_memory_turn=5,
        )
        # First msg should be the memory
        assert result[0]["role"] == "system"
        assert "blah" in result[0]["content"]
        # Tail starts from turn=5
        assert len(result) == 2
        assert result[1]["content"] == "recent"

    def test_zero_last_memory_turn_no_op(self):
        transcript = [{"role": "user", "turn": 0, "content": "x"}]
        assert session_memory_splice(transcript, "summary", 0) == transcript


# ─── Tier 3: emergency compact ───


class TestEmergencyCompact:
    @pytest.mark.asyncio
    async def test_summarizes_via_llm(self, mocker):
        from unittest.mock import AsyncMock

        class _R:
            text = "summary"

        llm = mocker.MagicMock()
        llm.chat = AsyncMock(return_value=_R())
        messages = [
            {"role": "user", "content": "very long content " * 1000},
        ]
        result = await emergency_compact(messages, llm)
        assert len(result) == 1
        assert "summary" in result[0]["content"]

    @pytest.mark.asyncio
    async def test_llm_error_returns_original(self, mocker):
        from unittest.mock import AsyncMock

        llm = mocker.MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("down"))
        messages = [{"role": "user", "content": "x"}]
        result = await emergency_compact(messages, llm)
        assert result == messages


# ─── Token estimate ───


class TestEstimateTokens:
    def test_simple_string(self):
        msgs = [{"role": "user", "content": "abc" * 100}]  # 300 chars
        est = estimate_token_count(msgs)
        assert est > 0
        assert est < 300

    def test_nested_content(self):
        msgs = [{
            "role": "assistant",
            "content": [
                {"type": "text", "text": "hello world"},
                {"type": "tool_use", "input": {"x": "value"}},
            ],
        }]
        est = estimate_token_count(msgs)
        assert est > 0


# ─── Pipeline ───


class TestPrepareMessages:
    @pytest.mark.asyncio
    async def test_full_pipeline_no_emergency(self):
        transcript = [
            {"role": "user", "turn": 0, "content": "old"},
            {"role": "user", "turn": 5, "content": "recent"},
        ]
        result = await prepare_messages(
            transcript=transcript,
            memory_md="# summary",
            last_memory_turn=5,
            current_turn=6,
        )
        # Memory spliced, prefix dropped
        assert any(m.get("role") == "system" for m in result)

    @pytest.mark.asyncio
    async def test_emergency_skipped_when_small(self):
        transcript = [{"role": "user", "turn": 0, "content": "hi"}]
        result = await prepare_messages(
            transcript=transcript,
            memory_md="",
            last_memory_turn=0,
            current_turn=1,
        )
        # Small input, no emergency
        assert result == transcript  # unchanged


# ─── Constants ───


class TestConstants:
    def test_stale_turn_age(self):
        assert STALE_TURN_AGE == 5

    def test_emergency_token_limit(self):
        assert EMERGENCY_TOKEN_LIMIT == 100_000
