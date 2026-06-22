"""P-1 hotfix regression + Phase X2 全采纳: chat/hitl.py textual 行为.

P-1 原 bug: textual hold stdin → builtin input 死锁.
P-1 修法: prompt_fn=None → safe deny (return False).
Phase X2 改进: prompt_fn=None → 自动采纳 (return True), 与 bootstrap/compress
全采纳策略一致, 用户通过 /review 事后修订. 仍不调 _default_prompt, 避免死锁.

参考: 9838507 commit + Phase X2 commit 7580209.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from explain_engine.chat.hitl import hitl_gate
from explain_engine.chat.tools import (
    ToolContext,
    _AddObservationInput,
    _CheckInput,
    add_observation_tool,
    check_tool,
)
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.state import CognitiveState


def _make_ctx() -> ToolContext:
    g = ExplanationGraph(root_question="why?")
    state = CognitiveState(graph=g, budget_remaining=10, root_question="why?")
    return ToolContext(state=state, llm=None)


# ─── 1. unit: prompt_fn=None 行为矩阵 ───


class TestHitlGatePromptFnNone:
    """Phase X2: prompt_fn=None (textual 模式) → 自动采纳 llm_inferred.

    ExplainChatApp 不能弹 builtin input (会 hold stdin 死锁), 所以 textual
    模式下 chat.input_provider 留 None. Phase X2 改为自动采纳 + /review 修订.
    """

    @pytest.mark.asyncio
    async def test_none_prompt_user_explicit_still_approved(self) -> None:
        """user_explicit 不需 prompt → 不论 prompt_fn 是 None 还是别的都 True."""
        ctx = _make_ctx()
        parsed = _AddObservationInput(
            name="x", description="d", source="user_explicit",
        )
        approved = await hitl_gate(
            add_observation_tool, parsed, ctx, prompt_fn=None,
        )
        assert approved is True

    @pytest.mark.asyncio
    async def test_none_prompt_non_hitl_tool_auto_approved(self) -> None:
        """non-HITL tool (check 等) → 永远 True, prompt_fn 怎样都不调."""
        ctx = _make_ctx()
        parsed = _CheckInput(target_id=None)
        approved = await hitl_gate(check_tool, parsed, ctx, prompt_fn=None)
        assert approved is True

    @pytest.mark.asyncio
    async def test_none_prompt_llm_inferred_auto_approved(self) -> None:
        """Phase X2: llm_inferred + prompt_fn=None → 自动采纳 (True)."""
        ctx = _make_ctx()
        parsed = _AddObservationInput(
            name="x", description="d", source="llm_inferred",
        )
        approved = await hitl_gate(
            add_observation_tool, parsed, ctx, prompt_fn=None,
        )
        assert approved is True

    @pytest.mark.asyncio
    async def test_none_prompt_llm_inferred_logs_info(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """自动采纳时 log INFO, 含 obs name — 用户能从 log 看到发生了啥."""
        ctx = _make_ctx()
        parsed = _AddObservationInput(
            name="my_obs", description="d", source="llm_inferred",
        )
        with caplog.at_level(logging.INFO, logger="explain_engine.chat.hitl"):
            await hitl_gate(
                add_observation_tool, parsed, ctx, prompt_fn=None,
            )
        assert any("my_obs" in r.message for r in caplog.records), (
            f"未找到 obs name 'my_obs' 的 info log, records="
            f"{[r.message for r in caplog.records]}"
        )


# ─── 2. unit: 默 prompt_fn 不再是 _default_prompt ───


class TestHitlGateDefaultIsNone:
    """P-1 fix + X2: hitl_gate 默 prompt_fn=None (不走 _default_prompt).
    X2 改为自动采纳.
    """

    @pytest.mark.asyncio
    async def test_default_prompt_fn_no_arg_auto_approves(self) -> None:
        """不传 prompt_fn (走默) + llm_inferred → 自动采纳 (X2).

        P-1: 默 None 不走 _default_prompt (避免死锁).
        X2: None → 自动采纳 (True).
        """
        ctx = _make_ctx()
        parsed = _AddObservationInput(
            name="x", description="d", source="llm_inferred",
        )
        approved = await hitl_gate(add_observation_tool, parsed, ctx)
        assert approved is True


# ─── 3. regression: 模拟 textual hold stdin (老 bug 复现) ───


class TestHitlGateNoHangWithBlockingInput:
    """P-1 regression: 模拟 textual hold stdin (builtin input 永远 block) — 5s
    wait_for. 默 prompt_fn=None 不走 _default_prompt, 立即返 (X2: 返 True).
    """

    @pytest.mark.asyncio
    async def test_no_hang_when_input_would_block(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """input() 阻塞 60s 模拟 textual 死锁, hitl_gate 5s 内必须返."""
        import builtins
        import time

        def _blocking_input(prompt: str = "") -> str:
            time.sleep(60)
            return "n"

        monkeypatch.setattr(builtins, "input", _blocking_input)

        ctx = _make_ctx()
        parsed = _AddObservationInput(
            name="x", description="d", source="llm_inferred",
        )
        # P-1: 不走 _default_prompt (不调 input), 立即返.
        # X2: 返 True (自动采纳).
        approved = await asyncio.wait_for(
            hitl_gate(add_observation_tool, parsed, ctx),
            timeout=5.0,
        )
        assert approved is True


# ─── 4. integration: query_loop 把 chat.input_provider 传给 hitl_gate ───


class _FakeLLMResponse:
    def __init__(
        self,
        text: str = "",
        tool_uses: list | None = None,
        stop_reason: str = "end_turn",
        raw_content_blocks: list | None = None,
    ):
        self.text = text
        self.tool_uses = tool_uses or []
        self.stop_reason = stop_reason
        self.raw_content_blocks = raw_content_blocks or []


class _FakeLLMClient:
    def __init__(self, responses: list[_FakeLLMResponse]) -> None:
        self.responses = list(responses)
        self.call_count = 0

    async def chat_with_tools(self, system, messages, tools, on_delta=None):
        # Phase 20.3: 接受 on_delta (query_loop 现以 stream_llm 桥式调用); 不 emit,
        # query_loop 走整段 AssistantTextEvent fallback.
        del system, messages, tools, on_delta
        self.call_count += 1
        if not self.responses:
            return _FakeLLMResponse()
        return self.responses.pop(0)


def _make_chat_session(sid: str = "s_b1c00001"):
    """构造 ChatSession 用 SessionStore (跟 test_chat_loop.py 对齐)."""
    from explain_engine.chat.session import ChatSession
    from explain_engine.persistence.session import (
        Session,
        SessionMeta,
        SessionStore,
    )
    from explain_engine.schema.graph import ExplanationGraph
    from explain_engine.schema.state import CognitiveState

    g = ExplanationGraph(root_question="why?")
    state = CognitiveState(graph=g, budget_remaining=10, root_question="why?")
    meta = SessionMeta.new(question="why?")
    meta.session_id = sid
    meta.stage = "done"
    sess = Session(meta=meta, state=state)
    SessionStore().save(sess)
    return ChatSession(sid)


class TestQueryLoopPassesInputProvider:
    """P-1 fix: query_loop 调 hitl_gate 时显式传 chat.input_provider.

    - chat.input_provider != None (cli 模式) → hitl_gate 走它 (prompt_toolkit safe)
    - chat.input_provider = None (textual 模式) → hitl_gate safe deny
    """

    @pytest.mark.asyncio
    async def test_query_loop_uses_chat_input_provider_when_set(
        self, tmp_path, monkeypatch,
    ) -> None:
        """chat.input_provider 设为 AsyncMock('y') → hitl_gate 调它 → tool 被
        approve → ToolResultEvent.result 不含 'denied'.
        """
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_hitl_p1_with_provider")

        from explain_engine.chat.loop import ToolResultEvent, query_loop

        chat = _make_chat_session("s_b1a00001")
        # cli 模式 simulation — provider 必被调一次, 返 "y" 同意
        chat.input_provider = AsyncMock(return_value="y")

        llm = _FakeLLMClient([
            _FakeLLMResponse(
                text="propose add",
                tool_uses=[{
                    "id": "tu_1",
                    "name": "add_observation",
                    "input": {
                        "name": "auto_obs",
                        "description": "llm inferred",
                        "source": "llm_inferred",
                    },
                }],
                stop_reason="tool_use",
            ),
            _FakeLLMResponse(text="done", tool_uses=[], stop_reason="end_turn"),
        ])

        events = []
        async for ev in query_loop(chat, llm):
            events.append(ev)

        # input_provider 必被 await 一次
        chat.input_provider.assert_awaited_once()
        # tool 应 approve, result 不含 "denied"
        tool_results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
        assert len(tool_results) == 1
        assert tool_results[0].tool_name == "add_observation"
        assert "denied" not in tool_results[0].result.lower(), (
            f"input_provider 返 'y' 但 tool 被 deny: {tool_results[0].result!r}"
        )

    @pytest.mark.asyncio
    async def test_query_loop_none_input_provider_auto_approves_no_hang(
        self, tmp_path, monkeypatch,
    ) -> None:
        """Phase X2: textual 模式 chat.input_provider=None + LLM 推 add_observation
        llm_inferred → 自动采纳 (不阻塞, 不死锁).
        """
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_hitl_p1_no_provider")

        import builtins
        import time

        def _blocking_input(prompt: str = "") -> str:
            time.sleep(60)
            return "n"

        monkeypatch.setattr(builtins, "input", _blocking_input)

        from explain_engine.chat.loop import ToolResultEvent, query_loop

        chat = _make_chat_session("s_b1b00001")
        assert chat.input_provider is None, "default 应为 None"

        llm = _FakeLLMClient([
            _FakeLLMResponse(
                text="propose add",
                tool_uses=[{
                    "id": "tu_1",
                    "name": "add_observation",
                    "input": {
                        "name": "auto_obs_textual",
                        "description": "llm inferred",
                        "source": "llm_inferred",
                    },
                }],
                stop_reason="tool_use",
            ),
            _FakeLLMResponse(text="done", tool_uses=[], stop_reason="end_turn"),
        ])

        async def _run_loop() -> list:
            events = []
            async for ev in query_loop(chat, llm):
                events.append(ev)
            return events

        events = await asyncio.wait_for(_run_loop(), timeout=5.0)

        tool_results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
        assert len(tool_results) == 1
        assert tool_results[0].tool_name == "add_observation"
        # X2: 自动采纳, result 不含 "denied"
        assert "denied" not in tool_results[0].result.lower(), (
            f"X2 应自动采纳, 实际 result={tool_results[0].result!r}"
        )
