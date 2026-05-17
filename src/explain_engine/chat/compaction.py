"""Phase 9 Wave E.1: 3-tier context compaction (Claude Code style).

Layers (cheap → expensive):
1. microCompact — drop stale tool results > STALE_TURN_AGE old, replace with stub
2. sessionMemory splice — if memory.md fresher than transcript prefix, splice in
3. emergency — token limit hit → sync LLM summarize all, replace messages

prepare_messages(transcript, memory_md, current_turn, llm=None) is the pipeline entry.

设计参考:
- docs/plans/2026-05-17-conversational-cognitive-engine-design.md
- docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave E.1
- 哲学锚点: Claude Code microCompact + session memory + emergency tier
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from explain_engine.llm.client import LLMClient

logger = logging.getLogger(__name__)

# Tunable constants (Wave G acceptance may adjust)
STALE_TURN_AGE: int = 5
"""Tool results older than N turns (in transcript turn field) get stub-replaced
by microCompact."""

EMERGENCY_TOKEN_LIMIT: int = 100_000
"""Above this estimated token count, emergency compact triggers."""

_TOKENS_PER_CHAR_ESTIMATE: float = 0.3
"""rough heuristic: ~1 token per ~3.3 chars (English-leaning).
Cheap estimate for "are we close to limit?" gating, not byte-perfect."""


def estimate_token_count(messages: list[dict]) -> int:
    """Heuristic token estimate (cheap; for emergency trigger gate).

    Not byte-perfect but good enough for "are we approaching limit" decisions.
    Walks str / list-of-block content (text / tool_use / tool_result shapes).
    """
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if "text" in block:
                        total_chars += len(block["text"])
                    if "content" in block:  # tool_result
                        total_chars += len(str(block["content"]))
                    if "input" in block:  # tool_use
                        total_chars += len(str(block["input"]))
    return int(total_chars * _TOKENS_PER_CHAR_ESTIMATE)


def micro_compact(transcript: list[dict], current_turn: int) -> list[dict]:
    """Tier 1: replace stale tool_result content with stub.

    Tool results from > STALE_TURN_AGE turns ago are replaced with
    [stale tool result GC'd by microCompact] to free context.
    Assistant/user text 不动.

    Args:
        transcript: chat history (list of message dicts with 'turn' meta).
        current_turn: 现在的 turn 号 (用来算 age = current - msg.turn).

    Returns:
        新 list (不 mutate 入参). 过期 tool_result 的 content 替换成 stub,
        tool_use_id 保留 (Anthropic API tool_use ↔ tool_result 配对依赖此 id).
    """
    STALE_STUB = "[stale tool result GC'd by microCompact]"
    result: list[dict] = []
    for msg in transcript:
        turn = msg.get("turn", current_turn)
        age = current_turn - turn
        # Only compact tool_result messages (user role with tool_result content list)
        if (
            age > STALE_TURN_AGE
            and msg.get("role") == "user"
            and isinstance(msg.get("content"), list)
        ):
            # Replace each tool_result content block
            new_content = []
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    new_content.append({
                        "type": "tool_result",
                        "tool_use_id": block.get("tool_use_id"),
                        "content": STALE_STUB,
                    })
                else:
                    new_content.append(block)
            new_msg = dict(msg)
            new_msg["content"] = new_content
            result.append(new_msg)
        else:
            result.append(msg)
    return result


def session_memory_splice(
    transcript: list[dict],
    memory_md: str,
    last_memory_turn: int,
) -> list[dict]:
    """Tier 2: prepend memory.md as system msg + drop transcript prefix.

    memory.md is written by session_memory_writer hook (D.2) every 5 turns.
    The summary covers turns [0, last_memory_turn). If memory_md is empty
    or last_memory_turn=0, no-op (return input unchanged).

    Args:
        transcript: chat history (list of message dicts with 'turn' meta).
        memory_md: 最新 session memory 文本 (D.2 hook 写, ChatSession.memory_md cache).
        last_memory_turn: memory.md 覆盖到的 turn 号 (chat_state.last_compact_at_turn).

    Returns:
        新 list: [memory_system_msg, *tail_with_turn>=last_memory_turn].
        若 memory_md 空 或 last_memory_turn=0 → 返原 transcript.
    """
    if not memory_md or last_memory_turn <= 0:
        return transcript
    memory_msg = {
        "role": "system",
        "content": (
            f"[Session memory through turn {last_memory_turn}]\n\n{memory_md}"
        ),
    }
    # Keep messages with turn >= last_memory_turn (the "recent" tail)
    tail = [m for m in transcript if m.get("turn", 0) >= last_memory_turn]
    return [memory_msg, *tail]


async def emergency_compact(
    messages: list[dict],
    llm: LLMClient,
) -> list[dict]:
    """Tier 3: synchronous LLM summarize all messages, return single system msg.

    Last resort when token estimate > EMERGENCY_TOKEN_LIMIT. LLM failure → 返
    原 messages (fail-safe; 总比 crash 强).

    Args:
        messages: 完整 messages list (tier 1+2 跑完后还 over-limit 时调).
        llm: LLMClient (调 .chat 总结).

    Returns:
        单 system msg list (含 LLM summary), 或 LLM 失败 → 原 messages.
    """
    try:
        from explain_engine.llm.client import Message

        sys_prompt = (
            "紧急 context 压缩. 总结这段长对话, 给下一轮 LLM 看. "
            "保留: 用户的核心问题, graph 演化里程碑, 已尝试的策略, 未决线程."
        )
        # Build messages with system as messages[0] (LLMClient.chat takes
        # messages list; provider adapter handles role conversion).
        llm_messages = [Message(role="system", content=sys_prompt)]
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Flatten content list to text for summarization
                content = "\n".join(
                    block.get("text", str(block)) if isinstance(block, dict)
                    else str(block)
                    for block in content
                )
            llm_messages.append(Message(
                role=role if role in ("user", "assistant") else "user",
                content=content,
            ))

        if not hasattr(llm, "chat"):
            logger.warning("emergency_compact: LLM lacks .chat method")
            return messages  # bail out, keep original

        response = await llm.chat(messages=llm_messages, schema=None)
        summary = response.text if hasattr(response, "text") else str(response)
        return [{
            "role": "system",
            "content": f"[Emergency compaction — prior context]\n\n{summary}",
        }]
    except Exception as exc:
        logger.warning(
            f"emergency_compact failed: {type(exc).__name__}: {exc}"
        )
        return messages  # bail; keep original on failure


async def prepare_messages(
    transcript: list[dict],
    memory_md: str,
    last_memory_turn: int,
    current_turn: int,
    llm: LLMClient | None = None,
) -> list[dict]:
    """3-tier pipeline: micro → session_memory → emergency.

    Args:
        transcript: chat history (list of message dicts).
        memory_md: current session_memory content.
        last_memory_turn: last turn covered by memory_md.
        current_turn: now.
        llm: optional, only needed for tier 3 emergency.

    Returns:
        Compacted messages ready for LLM.
    """
    # Tier 1: cheap
    msgs = micro_compact(transcript, current_turn)
    # Tier 2: cheap (just splice if memory available)
    msgs = session_memory_splice(msgs, memory_md, last_memory_turn)
    # Tier 3: expensive (only if still over limit AND llm available)
    if estimate_token_count(msgs) > EMERGENCY_TOKEN_LIMIT and llm is not None:
        msgs = await emergency_compact(msgs, llm)
    return msgs
