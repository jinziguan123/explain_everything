"""P-1 hotfix regression: chat/hitl.py textual 死锁修.

老 bug (复现 path):
- chat/hitl.py:_default_prompt 用 asyncio.to_thread(input, prompt_text) 收 y/n
- chat/loop.py:239 调 hitl_gate(tool, parsed_input, ctx) 没传 prompt_fn → 走默
  _default_prompt
- LLM 调 add_observation tool with source=llm_inferred → query_loop → hitl_gate
  → _default_prompt → builtin input
- textual ExplainChatApp hold stdin raw mode → builtin input 永远 block
- app 整体死锁, Ctrl+C 也无法退出 (同 9838507 commit 修的 /resume 死锁机理)

修法 (本 hotfix):
- hitl_gate signature: prompt_fn: PromptFn | None = None (老默 _default_prompt)
- prompt_fn=None 且需弹 prompt → log warning + return False (safe deny)
- chat/loop.py:239 显式传 prompt_fn=chat.input_provider:
  - cli 模式: input_provider = lambda prompt: read_input(pt_session, prompt) →
    prompt_toolkit safe path → 正常 y/n
  - textual 模式: input_provider = None → safe deny + log → 永不调 builtin input

参考: 9838507 commit message 段 "其他受影响 slash handler 审计" 标记的 follow-up.
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
    """P-1 fix: prompt_fn=None 是 textual-mode 显式 safe fallback.

    ExplainChatApp 不能弹 builtin input (会 hold stdin 死锁), 所以 textual
    模式下 chat.input_provider 留 None. query_loop 把它传给 hitl_gate → safe
    deny.
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
    async def test_none_prompt_llm_inferred_safe_denies(self) -> None:
        """关键: llm_inferred + prompt_fn=None → safe deny (False), 不 raise."""
        ctx = _make_ctx()
        parsed = _AddObservationInput(
            name="x", description="d", source="llm_inferred",
        )
        approved = await hitl_gate(
            add_observation_tool, parsed, ctx, prompt_fn=None,
        )
        assert approved is False

    @pytest.mark.asyncio
    async def test_none_prompt_llm_inferred_logs_warning(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """safe deny 时 log WARNING, 含 obs name — 用户能从 log 看到发生了啥."""
        ctx = _make_ctx()
        parsed = _AddObservationInput(
            name="my_obs", description="d", source="llm_inferred",
        )
        with caplog.at_level(logging.WARNING, logger="explain_engine.chat.hitl"):
            await hitl_gate(
                add_observation_tool, parsed, ctx, prompt_fn=None,
            )
        # warning 应提到 obs name (用户能看到 LLM 想加什么)
        assert any("my_obs" in r.message for r in caplog.records), (
            f"未找到 obs name 'my_obs' 的 warning, records="
            f"{[r.message for r in caplog.records]}"
        )


# ─── 2. unit: 默 prompt_fn 不再是 _default_prompt ───


class TestHitlGateDefaultIsNone:
    """P-1 fix: hitl_gate 默 prompt_fn 老是 _default_prompt (→ asyncio.to_thread
    (input) → textual hang). 修后默 None → safe deny.
    """

    @pytest.mark.asyncio
    async def test_default_prompt_fn_no_arg_safe_denies(self) -> None:
        """不传 prompt_fn (走默) + llm_inferred → safe deny.

        老 bug: 默 _default_prompt 调 input() → 阻塞.
        修后: 默 None → safe deny 立即返 False.
        """
        ctx = _make_ctx()
        parsed = _AddObservationInput(
            name="x", description="d", source="llm_inferred",
        )
        # 关键: 不显式传 prompt_fn — 走默. 修前: 走 _default_prompt 调 input.
        approved = await hitl_gate(add_observation_tool, parsed, ctx)
        assert approved is False


# ─── 3. regression: 模拟 textual hold stdin (老 bug 复现) ───


class TestHitlGateNoHangWithBlockingInput:
    """P-1 regression: 模拟 textual hold stdin (builtin input 永远 block) — 5s
    wait_for. 修前: 默 _default_prompt 走 asyncio.to_thread(input) → block →
    test 超时 fail. 修后: 默 None → safe deny 立即返 → test 通过.
    """

    @pytest.mark.asyncio
    async def test_no_hang_when_input_would_block(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """input() 阻塞 60s 模拟 textual 死锁, hitl_gate 5s 内必须返."""
        import builtins
        import time

        def _blocking_input(prompt: str = "") -> str:
            # 模拟 textual hold stdin: builtin input 永远 block
            time.sleep(60)
            return "n"

        monkeypatch.setattr(builtins, "input", _blocking_input)

        ctx = _make_ctx()
        parsed = _AddObservationInput(
            name="x", description="d", source="llm_inferred",
        )
        # 修前: 默 _default_prompt → asyncio.to_thread(_blocking_input) →
        # 60s sleep → 5s wait_for 超时 → fail.
        # 修后: 默 None → safe deny → 立即返 False.
        approved = await asyncio.wait_for(
            hitl_gate(add_observation_tool, parsed, ctx),
            timeout=5.0,
        )
        assert approved is False


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
    async def test_query_loop_none_input_provider_safe_denies_no_hang(
        self, tmp_path, monkeypatch,
    ) -> None:
        """textual 模式 simulation: chat.input_provider=None + LLM 推 add_observation
        llm_inferred → 5s wait_for. 修前: 走 _default_prompt → asyncio.to_thread
        (input) → textual hold stdin → block. 修后: safe deny 立即返.
        """
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))
        monkeypatch.setenv("EXPLAIN_PROJECT_ID", "test_hitl_p1_no_provider")

        # 模拟 textual hold stdin: monkey patch input 60s block
        import builtins
        import time

        def _blocking_input(prompt: str = "") -> str:
            time.sleep(60)
            return "n"

        monkeypatch.setattr(builtins, "input", _blocking_input)

        from explain_engine.chat.loop import ToolResultEvent, query_loop

        chat = _make_chat_session("s_b1b00001")
        # textual 模式: 不设 input_provider (默 None)
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

        # 5s wait_for — 修前会 timeout, 修后立即返
        events = await asyncio.wait_for(_run_loop(), timeout=5.0)

        tool_results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
        assert len(tool_results) == 1
        assert tool_results[0].tool_name == "add_observation"
        # safe deny 写入 result 走老 "user denied via HITL gate"
        assert "denied" in tool_results[0].result.lower(), (
            f"prompt_fn=None 应 safe deny, 实际 result={tool_results[0].result!r}"
        )
