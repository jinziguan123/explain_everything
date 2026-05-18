"""Phase 9 Wave C.2: inner query_loop (LLM ↔ tools while-loop).

Pattern 来自 Claude Code 的 queryLoop:
1. 每 iter assemble system prompt (动态拼装 graph hints)
2. 调 LLM with messages + tools schema (Anthropic native tool_use)
3. yield AssistantTextEvent / ToolUseEvent (streaming UX)
4. 若 response.stop_reason == "end_turn" 且无 tool_uses → TurnCompleteEvent, return
5. 否则 dispatch 每个 tool_use, yield ToolResultEvent, consume budget, append transcript,
   下一轮

终止条件:
- LLM stop_reason="end_turn" 且没 tool_uses (正常结束 turn)
- per_turn 或 per_session budget 用尽 (BudgetExhaustedEvent)
- LLMClient 没 chat_with_tools API (fallback, F.2 才真接 Anthropic native tool_use)

设计参考 docs/plans/2026-05-17-conversational-cognitive-engine-design.md.
设计参考 docs/plans/2026-05-17-conversational-cognitive-engine-plan.md Wave C.2.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from explain_engine.chat.budget import BudgetCounter
from explain_engine.chat.compaction import prepare_messages
from explain_engine.chat.hitl import hitl_gate
from explain_engine.chat.session import ChatEvent
from explain_engine.chat.system_prompt import assemble_system_prompt
from explain_engine.chat.tools import ALL_TOOLS, Tool, ToolContext

if TYPE_CHECKING:
    from explain_engine.chat.session import ChatSession
    from explain_engine.llm.client import LLMClient


@dataclass
class AssistantTextEvent(ChatEvent):
    """Streamed text chunk from LLM (Wave C.2 是 non-streaming, 整段返回)."""

    type: str = "assistant_text"


@dataclass
class ToolUseEvent(ChatEvent):
    """LLM called a tool (yielded 在 dispatch 前, 给 REPL 实时反馈)."""

    type: str = "tool_use"
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)


@dataclass
class ToolResultEvent(ChatEvent):
    """Tool returned a result (yielded 在 dispatch 后, content=纯 str 返给 LLM)."""

    type: str = "tool_result"
    tool_name: str = ""
    result: str = ""


@dataclass
class TurnCompleteEvent(ChatEvent):
    """LLM signaled end_turn — 无 tool_use, loop 正常结束."""

    type: str = "turn_complete"


@dataclass
class BudgetExhaustedEvent(ChatEvent):
    """Per-turn or per-session budget hit; loop 中断 return."""

    type: str = "budget_exhausted"
    scope: str = "per_turn"  # or "per_session"


def _tool_by_name(name: str) -> Tool | None:
    """Lookup tool in ALL_TOOLS registry."""
    for t in ALL_TOOLS:
        if t.name == name:
            return t
    return None


def _transcript_to_messages(transcript: list[dict]) -> list[dict]:
    """Convert chat transcript format to Anthropic API messages format.

    Transcript schema (Wave C.1): {role, content, turn}.
    Anthropic API: {role, content}. 抹掉 'turn' meta key.

    Wave E.1 note: 过滤 role=system msg — Anthropic API messages 数组只接受
    user / assistant; system prompt 走 chat_with_tools 的独立 system 参数.

    Wave E.1 fix: compaction 函数现已不产 system role msg —
    session_memory_splice 只 drop prefix (memory_md 由 assemble_system_prompt
    内联进 sys_prompt), emergency_compact 返 user role + [EMERGENCY COMPACTION]
    marker. 此过滤器保留作 defense-in-depth, 防未来 caller 误加 system msg.
    """
    return [
        {"role": m["role"], "content": m["content"]}
        for m in transcript
        if m.get("role") in ("user", "assistant")
    ]


async def query_loop(
    chat: ChatSession,
    llm: LLMClient,
) -> AsyncIterator[ChatEvent]:
    """Inner LLM ↔ tools while-loop.

    Args:
        chat: ChatSession (读 transcript / state / chat_state, mutate them)
        llm: LLMClient (must have chat_with_tools method; fallback to AttributeError path
             for clients without native tool_use API — Wave F.2 will add it).

    Yields:
        AssistantTextEvent / ToolUseEvent / ToolResultEvent / TurnCompleteEvent
        / BudgetExhaustedEvent

    Note: chat.transcript 在每次 tool 调用后 mutate (append assistant + user[tool_result]),
          chat_state.budget_per_*_remaining 在每个 tool dispatch 后 -1.
          不主动 persist — caller (ChatSession.handle_user_input) 在最后 persist().
    """
    ctx = ToolContext(state=chat.state, llm=llm)
    budget = BudgetCounter(chat.chat_state)  # Wave D.1: thin wrapper
    # Anthropic tools schema: 每个 tool 转 {name, description, input_schema (pydantic JSON schema)}
    tools_schema = [
        {
            "name": t.name,
            "description": t.description(ctx),
            "input_schema": t.input_schema.model_json_schema(),
        }
        for t in ALL_TOOLS
    ]

    # Wave D.2: one-shot consumption of next_turn_hint. 第一 iter 注入 system prompt
    # 后立刻清 None — 防 hint 在 multi-iter loop 里 stale repeated (e.g. user 已 follow
    # 建议 fix 了, 第 2 iter 不该还看到老 hint).
    pending_hint = chat.next_turn_hint
    chat.next_turn_hint = None

    while True:
        # ── Budget check (per-turn first, fall through to per-session) ──
        if budget.turn_exhausted():
            yield BudgetExhaustedEvent(scope="per_turn")
            return
        if budget.session_exhausted():
            yield BudgetExhaustedEvent(scope="per_session")
            return

        # ── Assemble system prompt (dynamic each iter) ──
        sys_prompt = assemble_system_prompt(
            state=chat.state,
            question=chat._session.meta.question,
            memory_md=chat.memory_md,
            budget=budget.as_dict(),
            hint=pending_hint,   # Wave D.2: 上 turn reflect 留的 hint, 第一 iter 后清
        )
        # 第一 iter 用完后清, 后续 iter 不再渲染 hint section
        pending_hint = None

        # ── Convert transcript → Anthropic messages format ──
        # Wave E.1: 3-tier compaction (microCompact + sessionMemory splice +
        # emergency). 跑 prepare_messages 在每 iter 顶, 自动 GC stale tool_result
        # / 拼 session_memory / 紧急 LLM summarize, 控 context 不爆.
        prepared = await prepare_messages(
            transcript=chat.transcript,
            memory_md=chat.memory_md,
            last_memory_turn=chat.chat_state.last_compact_at_turn,
            current_turn=chat.chat_state.turn_count,
            llm=llm,
        )
        messages = _transcript_to_messages(prepared)

        # ── Call LLM with tools ──
        # NOTE: LLMClient Protocol (llm/client.py) 目前只有 `chat`; chat_with_tools
        # 是 Wave F.2 Anthropic native tool_use 集成时才加. C.2 这里 fallback:
        # 没 chat_with_tools 属性 → AttributeError, yield TurnComplete, return.
        #
        # I2 / Task F.2 contract: llm.chat_with_tools(system, messages, tools) 必须返
        # 一个 object 满足以下 attributes (flattened facade over Anthropic SDK Message):
        #   - .text: str  (concat 所有 TextBlock.text in Message.content)
        #   - .tool_uses: list[dict]  (每个 dict: {id: str, name: str, input: dict};
        #                              对应 Message.content 里的每个 ToolUseBlock)
        #   - .stop_reason: str  (forward Anthropic Message.stop_reason,
        #                         如 "end_turn", "tool_use", "max_tokens")
        #
        # F.2 adapter (in llm/client.py 或新 chat_llm.py) 应:
        #   1. Call anthropic SDK messages.create(system=..., messages=..., tools=...)
        #   2. Concat 所有 TextBlock.text → .text
        #   3. Filter ContentBlock for type=="tool_use" → .tool_uses (dict 化)
        #   4. Forward .stop_reason 原值
        # 注意: 不要把 raw SDK Message 对象直接当 response 传, query_loop 只认
        # 上面的 facade shape (decouple SDK 升级风险).
        try:
            response = await llm.chat_with_tools(
                system=sys_prompt,
                messages=messages,
                tools=tools_schema,
            )
        except AttributeError:
            yield TurnCompleteEvent(content="llm_client_lacks_tools_api")
            return

        # ── Stream assistant text if any ──
        if response.text:
            yield AssistantTextEvent(content=response.text)

        # ── No tool calls → end turn ──
        if not response.tool_uses:
            yield TurnCompleteEvent()
            return

        # ── Dispatch each tool call ──
        # I1: 单次 LLM response 可含多个 tool_use; 仅外层 while 顶部检查 budget
        # 会让 budget 被 overshoot (e.g. budget=1, response 含 5 tool_use → -4).
        # 故 dispatch 每个 tool 前再次检查 budget; 0 即 break + 记 scope, 同时
        # 已 dispatch 的 tool_result 仍 append (部分结果持久, 不丢历史), 然后
        # yield BudgetExhaustedEvent + return.
        tool_result_messages: list[dict[str, Any]] = []
        dispatched_tool_uses: list[dict[str, Any]] = []
        budget_exhausted_scope: str | None = None
        for tool_use in response.tool_uses:
            # mid-loop budget check (防 multi-tool response overshoot)
            if budget.turn_exhausted():
                budget_exhausted_scope = "per_turn"
                break
            if budget.session_exhausted():
                budget_exhausted_scope = "per_session"
                break

            tool = _tool_by_name(tool_use["name"])
            if tool is None:
                result = f"unknown tool: {tool_use['name']!r}"
                yield ToolResultEvent(tool_name=tool_use["name"], result=result)
            else:
                yield ToolUseEvent(
                    tool_name=tool.name,
                    tool_input=tool_use.get("input", {}),
                )
                try:
                    parsed_input = tool.input_schema.model_validate(
                        tool_use.get("input", {})
                    )
                    # Wave D.1: HITL gate. 非 requires_hitl 直接 True;
                    # add_observation + source=user_explicit 直接 True;
                    # add_observation + source=llm_inferred 弹 prompt 等 user.
                    # parse 失败走下方 except, gate 不调.
                    approved = await hitl_gate(tool, parsed_input, ctx)
                    if not approved:
                        result = "user denied via HITL gate"
                    else:
                        result = await tool.call(parsed_input, ctx)
                except Exception as exc:
                    # tool call 异常 catch 成 str 给 LLM 看, 让它 retry / 换策略
                    # 比让 query_loop 整个 crash 友好得多.
                    result = (
                        f"tool {tool.name} failed: {type(exc).__name__}: {exc}"
                    )
                yield ToolResultEvent(tool_name=tool.name, result=result)

            tool_result_messages.append({
                "type": "tool_result",
                "tool_use_id": tool_use.get("id", "unknown"),
                "content": result,
            })
            dispatched_tool_uses.append(tool_use)
            # 每个 tool call 消耗 1 budget (per-turn + per-session 同步扣)
            # Wave D.1: 走 BudgetCounter.consume (替 direct chat_state 访问)
            budget.consume()

        # ── Append assistant message (text + tool_uses) + tool_result message ──
        # 注: 仅 append 实际 dispatch 过的 tool_use, 否则 LLM 端 tool_use_id
        # 与 tool_result 对不上 (Anthropic API 会 reject 不匹配的 pair).
        #
        # F.4: 若 response 带 raw_content_blocks (Anthropic 协议) 用它 ——
        # 保留 text + thinking + dispatched tool_uses, drop 因 budget 跳过的
        # tool_use. thinking block 必须 echo (deepseek-reasoner / Claude
        # extended thinking 要求). 无 raw_content_blocks (OpenAI 或老 mock)
        # 走 fallback 重建.
        assistant_content: list[dict[str, Any]]
        raw_blocks = getattr(response, "raw_content_blocks", None) or []
        if raw_blocks:
            dispatched_ids = {tu.get("id", "unknown") for tu in dispatched_tool_uses}
            assistant_content = [
                block for block in raw_blocks
                if block.get("type") != "tool_use" or block.get("id") in dispatched_ids
            ]
        else:
            assistant_content = []
            if response.text:
                assistant_content.append({"type": "text", "text": response.text})
            for tu in dispatched_tool_uses:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tu.get("id", "unknown"),
                    "name": tu["name"],
                    "input": tu.get("input", {}),
                })
        # 只有当 assistant 有内容 (text 或 dispatched tool_uses) 时才 append,
        # 防空 assistant 消息 (e.g. text='' + 全部 tool_use 被 budget skip).
        if assistant_content:
            assistant_msg = {
                "role": "assistant",
                "content": assistant_content,
                "turn": chat.chat_state.turn_count,
            }
            chat.transcript.append(assistant_msg)
            chat.storage.append_transcript(chat.sid, assistant_msg)

        if tool_result_messages:
            user_msg = {
                "role": "user",
                "content": tool_result_messages,
                "turn": chat.chat_state.turn_count,
            }
            chat.transcript.append(user_msg)
            chat.storage.append_transcript(chat.sid, user_msg)

        # I1: mid-loop budget break → yield BudgetExhaustedEvent + return
        # (transcript 已 append 部分 result, 不丢历史)
        if budget_exhausted_scope is not None:
            yield BudgetExhaustedEvent(scope=budget_exhausted_scope)
            return
