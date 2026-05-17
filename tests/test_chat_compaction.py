"""Phase 9 Wave E.1: 3-tier compaction tests.

3 tiers (cheap → expensive):
1. micro_compact — tool_result > STALE_TURN_AGE 替 stub
2. session_memory_splice — memory.md 存在 → 删 prefix (memory_md 本身由
   assemble_system_prompt 内联进 sys_prompt, 不在 messages 数组里 splice)
3. emergency_compact — token > EMERGENCY_TOKEN_LIMIT → LLM summarize all,
   返回 user role msg + [EMERGENCY COMPACTION] marker (system role 会被
   loop._transcript_to_messages 过滤)

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
        """Tier 2 仅 drop 已被 memory 覆盖的 prefix; memory_md 本身由
        assemble_system_prompt 内联渲染 (Anthropic API messages 数组只接受
        user/assistant; prepend system msg 会被 loop._transcript_to_messages
        过滤掉 → memory 实际从未到 LLM, 故改由 sys_prompt 走 native system 参数).
        """
        transcript = [
            {"role": "user", "turn": 0, "content": "old"},
            {"role": "assistant", "turn": 0, "content": "old reply"},
            {"role": "user", "turn": 5, "content": "recent"},
        ]
        result = session_memory_splice(
            transcript, "# Summary\nblah", last_memory_turn=5,
        )
        # 不再 prepend system msg; 只 drop prefix
        assert all(m.get("role") != "system" for m in result)
        # Tail 只保留 turn >= 5 的 msg
        assert len(result) == 1
        assert result[0]["turn"] == 5
        assert result[0]["content"] == "recent"

    def test_zero_last_memory_turn_no_op(self):
        transcript = [{"role": "user", "turn": 0, "content": "x"}]
        assert session_memory_splice(transcript, "summary", 0) == transcript


# ─── Tier 3: emergency compact ───


class TestEmergencyCompact:
    @pytest.mark.asyncio
    async def test_summarizes_via_llm(self, mocker):
        """LLM 总结后必须返 user role (system role 会被
        loop._transcript_to_messages 过滤). marker [EMERGENCY COMPACTION]
        让 LLM 知道这是上下文压缩总结而非真实 user input.
        """
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
        # 必须 user role 才能过 _transcript_to_messages 过滤到达 LLM
        assert result[0]["role"] == "user"
        assert "[EMERGENCY COMPACTION" in result[0]["content"]
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
        """Pipeline: tier 2 splice 后只 drop prefix, 无 system role msg.
        memory_md 渲染走 assemble_system_prompt 而非 messages 数组.
        """
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
        # Prefix dropped; 无 system msg 残留
        assert all(m.get("role") != "system" for m in result)
        # 只保留 turn >= 5
        assert len(result) == 1
        assert result[0]["turn"] == 5

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


# ─── I3 regression: combined tier 1 + tier 2 ───


class TestCombinedTiersIntegration:
    """I3 regression: tier 1 (microCompact) + tier 2 (splice) compose correctly."""

    @pytest.mark.asyncio
    async def test_micro_compact_and_splice_compose(self):
        """Pipeline: stale tool_result inside post-splice tail → still gets stubbed."""
        from explain_engine.chat.compaction import prepare_messages

        # transcript:
        # - turn 0: user msg, assistant msg (will be dropped by splice last_memory_turn=5)
        # - turn 6: stale tool_result (within tail, but old enough for tier 1 stub at current_turn=12)
        # - turn 11: recent tool_result (within tail + not stale → keep content)
        transcript = [
            {"role": "user", "turn": 0, "content": "very old"},
            {"role": "assistant", "turn": 0, "content": "old reply"},
            {"role": "user", "turn": 6, "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "stale data"},
            ]},
            {"role": "user", "turn": 11, "content": [
                {"type": "tool_result", "tool_use_id": "t2", "content": "fresh data"},
            ]},
        ]
        result = await prepare_messages(
            transcript=transcript,
            memory_md="summary covers turns 0-4",
            last_memory_turn=5,
            current_turn=12,
        )
        # Tier 2 dropped turn<5: first 2 messages gone
        # Tier 1 stubbed stale tool_result (12-6=6 > STALE_TURN_AGE=5): t1 content → stub
        # Recent tool_result (12-11=1): preserved
        # Should have 2 messages (no system msg since splice was refactored to just drop)
        assert len(result) == 2
        # First: stale stub
        first_content = result[0]["content"][0]
        assert "stale" in first_content["content"].lower()
        assert first_content["tool_use_id"] == "t1"  # preserved
        # Second: fresh, untouched
        second_content = result[1]["content"][0]
        assert second_content["content"] == "fresh data"
        assert second_content["tool_use_id"] == "t2"


# ─── I2 regression: emergency_compact empty summary guard ───


class TestEmergencyCompactEmptyGuard:
    """I2 regression: empty LLM summary keeps original."""

    @pytest.mark.asyncio
    async def test_empty_summary_keeps_original(self, mocker):
        from unittest.mock import AsyncMock

        from explain_engine.chat.compaction import emergency_compact

        class _R:
            text = ""  # empty
        llm = mocker.MagicMock()
        llm.chat = AsyncMock(return_value=_R())
        messages = [{"role": "user", "content": "x"}]
        result = await emergency_compact(messages, llm)
        assert result == messages  # unchanged

    @pytest.mark.asyncio
    async def test_whitespace_summary_keeps_original(self, mocker):
        from unittest.mock import AsyncMock

        from explain_engine.chat.compaction import emergency_compact

        class _R:
            text = "   \n  "  # whitespace only
        llm = mocker.MagicMock()
        llm.chat = AsyncMock(return_value=_R())
        messages = [{"role": "user", "content": "x"}]
        result = await emergency_compact(messages, llm)
        assert result == messages
