"""Wave C.1: ChatSession outer loop tests."""

import pytest

from explain_engine.chat.chat_copy import STATUS_THINKING
from explain_engine.chat.session import ChatEvent, ChatSession, ChatStateDict
from explain_engine.persistence.session import Session, SessionMeta, SessionStore
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_done_session(sid: str = "s_001abcde", stage: str = "done") -> Session:
    """Create a session via SessionStore.save().

    Phase 14 (2026-05-21): 加 stage 参数. Default "done" 向后兼容.
    """
    g = ExplanationGraph(root_question="why?")
    g.add_node(VariableNode(
        id="c_001", name="abs", description="d",
        abstraction_level=1, confidence=0.7, epistemic="insight",
    ))
    g.add_node(VariableNode(
        id="p_001", name="obs", description="d",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="c_001", target_node="p_001",
        relation_type="manifests_as", confidence=0.7, mechanism_description="m",
    ))
    state = CognitiveState(graph=g, budget_remaining=10, root_question="why?")
    state.insight_candidates = ["c_001"]
    meta = SessionMeta.new(question="why?")
    meta.session_id = sid  # override generated id
    meta.stage = stage
    sess = Session(meta=meta, state=state)
    store = SessionStore()
    store.save(sess)
    return sess


class TestChatSessionLoad:
    def test_load_existing_session_reads_metadata_graph(self) -> None:
        _make_done_session("s_001abcde")
        chat = ChatSession("s_001abcde")
        assert chat.state.graph.nodes["c_001"].name == "abs"
        assert chat.sid == "s_001abcde"
        # No transcript / memory yet for fresh chat
        assert chat.transcript == []
        assert chat.memory_md == ""
        # chat_state defaults (2026-05-20 hotfix: budget 默 0 = unlimited)
        assert chat.chat_state.turn_count == 0
        assert chat.chat_state.budget_per_session_remaining == 0
        assert chat.chat_state.budget_per_session_limit == 0  # unlimited

    def test_load_creates_empty_sidecar_state(self) -> None:
        """If no chat_state.json exists, default values used.

        2026-05-20 hotfix: 默 budget 改 0 (unlimited sentinel).
        """
        _make_done_session("s_002abcde")
        chat = ChatSession("s_002abcde")
        assert isinstance(chat.chat_state, ChatStateDict)
        assert chat.chat_state.budget_per_turn_remaining == 0
        assert chat.chat_state.budget_per_turn_limit == 0  # unlimited

    def test_load_missing_session_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            ChatSession("s_missing0")


class TestChatSessionPersist:
    def test_persist_writes_chat_state(self) -> None:
        _make_done_session("s_003abcde")
        chat = ChatSession("s_003abcde")
        chat.chat_state.turn_count = 5
        chat.persist()
        # Re-load fresh and verify persisted
        chat2 = ChatSession("s_003abcde")
        assert chat2.chat_state.turn_count == 5

    def test_persist_preserves_concurrent_autotitle(self) -> None:
        """回归: persist 不得用 turn 开始时的旧标题覆盖 autotitle 并发写入的真标题。

        复现 bug: 新建会话标题"新会话", 首条消息时 chat 流与 autotitle 并行,
        chat 流 turn 结束 persist 用内存旧值"新会话"覆盖了 autotitle 写的真标题,
        刷新后标题丢失。
        """
        _make_done_session("s_ab12ab12")
        SessionStore().update_question("s_ab12ab12", "新会话")
        chat = ChatSession("s_ab12ab12")  # 内存 meta.question = "新会话"
        # autotitle 在 turn 进行中并发把磁盘标题改成真标题
        SessionStore().update_question("s_ab12ab12", "傅里叶分析主题")
        # 对话流 turn 结束 persist (内存仍是旧"新会话")
        chat.persist()
        # 重新读盘: 真标题应被保留, 而非回退"新会话"
        assert SessionStore().load("s_ab12ab12").meta.question == "傅里叶分析主题"

    def test_close_flushes(self) -> None:
        _make_done_session("s_004abcde")
        chat = ChatSession("s_004abcde")
        chat.chat_state.budget_per_session_remaining = 42
        chat.close()
        chat2 = ChatSession("s_004abcde")
        assert chat2.chat_state.budget_per_session_remaining == 42


class TestHandleUserInput:
    @pytest.mark.asyncio
    async def test_slash_command_routes_to_dispatcher(self) -> None:
        """Wave F.1: slash 现走 dispatch_slash, /show 返 slash_show event."""
        _make_done_session("s_005abcde")
        chat = ChatSession("s_005abcde")
        events: list[ChatEvent] = []
        async for ev in chat.handle_user_input("/show"):
            events.append(ev)
        assert any(ev.type == "slash_show" for ev in events)
        # Slash 不 bump turn_count / 不 append transcript
        assert chat.chat_state.turn_count == 0
        assert chat.transcript == []

    @pytest.mark.asyncio
    async def test_non_slash_appends_to_transcript(self) -> None:
        _make_done_session("s_006abcde")
        chat = ChatSession("s_006abcde")
        events: list[ChatEvent] = []
        async for ev in chat.handle_user_input("hello"):
            events.append(ev)
        assert len(chat.transcript) == 1
        assert chat.transcript[0]["role"] == "user"
        assert chat.transcript[0]["content"] == "hello"
        # Turn count incremented
        assert chat.chat_state.turn_count == 1

    @pytest.mark.asyncio
    async def test_non_slash_resets_per_turn_budget(self) -> None:
        """Turn start 时 reset_turn() 把 per-turn remaining 拉回 limit.

        2026-05-20 hotfix: 默 unlimited (limit=0) → reset 把 remaining 归 0
        (tracking 起点). 老 case 默 10/50 已废.
        """
        _make_done_session("s_007abcde")
        chat = ChatSession("s_007abcde")
        # 设 finite limit + 残余 remaining 模拟前 turn 用过
        chat.chat_state.budget_per_turn_limit = 10
        chat.chat_state.budget_per_turn_remaining = 3
        events: list[ChatEvent] = []
        async for ev in chat.handle_user_input("test"):
            events.append(ev)
        # Reset to limit
        assert chat.chat_state.budget_per_turn_remaining == 10


class TestIsSlashCommand:
    def test_slash_prefix_detected(self) -> None:
        _make_done_session("s_008abcde")
        chat = ChatSession("s_008abcde")
        assert chat.is_slash_command("/help") is True
        assert chat.is_slash_command("  /show") is True  # strip
        assert chat.is_slash_command("hello") is False
        assert chat.is_slash_command("") is False


class TestChatSessionLoadError:
    """I2 regression: 损坏的 sidecar 抛出 ChatSessionLoadError (而非裸 JSONDecodeError)."""

    def test_corrupt_chat_state_raises_named_error(self) -> None:
        from explain_engine.chat.session import ChatSession, ChatSessionLoadError
        from explain_engine.persistence.storage_v2 import StorageV2
        _make_done_session("s_009abcde")
        # Write corrupt chat_state.json directly
        storage = StorageV2()
        chat_state_path = storage.session_dir("s_009abcde") / "chat_state.json"
        chat_state_path.parent.mkdir(parents=True, exist_ok=True)
        chat_state_path.write_text("{ this is not valid json")

        with pytest.raises(ChatSessionLoadError) as exc_info:
            ChatSession("s_009abcde")
        assert exc_info.value.sid == "s_009abcde"
        assert "chat_state.json" in exc_info.value.file
        assert "s_009abcde" in str(exc_info.value)

    def test_corrupt_transcript_raises_named_error(self) -> None:
        from explain_engine.chat.session import ChatSession, ChatSessionLoadError
        from explain_engine.persistence.storage_v2 import StorageV2
        _make_done_session("s_00aabcde")
        storage = StorageV2()
        transcript_path = storage.session_dir("s_00aabcde") / "transcript.jsonl"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text("{valid: false}\n")  # not valid json

        with pytest.raises(ChatSessionLoadError) as exc_info:
            ChatSession("s_00aabcde")
        assert "transcript.jsonl" in exc_info.value.file


class TestChatSessionDIRemoval:
    """I1 regression: storage 参数已移除 (会撞内部 env-driven SessionStore).

    2026-05-18 放宽: 允许其他 optional kwarg (如本日加的 llm), 但 storage 永远不能复活.
    """

    def test_init_signature_does_not_take_storage(self) -> None:
        """I1 regression: storage 参数已移除 (会撞内部 env-driven SessionStore).

        放宽: 允许其他 optional kwarg (如 2026-05-18 加的 llm), 但 storage 永远不能复活.
        """
        import inspect

        from explain_engine.chat.session import ChatSession
        sig = inspect.signature(ChatSession.__init__)
        params = list(sig.parameters.keys())
        assert "storage" not in params, f"storage param resurrected: {params}"
        assert params[:2] == ["self", "sid"], (
            f"sid must remain positional 2nd param: {params[:2]}"
        )


class TestAlignmentPersistenceE2B:
    """E.2 B regression: state.last_input_alignment_report persists via chat_state."""

    def test_alignment_set_then_persist_then_reload(self):
        from explain_engine.chat.session import ChatSession
        from explain_engine.engines.input_validation import InputAlignmentReport
        # Helper from existing tests
        _make_done_session("s_a11ec001")

        chat = ChatSession("s_a11ec001")
        # Simulate alignment validation result
        report = InputAlignmentReport(
            question_subject="Q主体",
            observation_subjects=["O1", "O2"],
            overlap_score=4,
            falsifiable_reason="aligned well",
        )
        chat.state.last_input_alignment_report = report
        chat.close()

        # Reload — new ChatSession instance, fresh memory
        chat2 = ChatSession("s_a11ec001")
        assert chat2.state.last_input_alignment_report is not None
        assert chat2.state.last_input_alignment_report.overlap_score == 4
        assert chat2.state.last_input_alignment_report.question_subject == "Q主体"


class TestChatSessionLLM:
    def test_default_llm_is_none(self) -> None:
        """Backward compat: 不传 llm 时 chat.llm is None."""
        _make_done_session("s_11111111")
        chat = ChatSession("s_11111111")
        assert chat.llm is None

    def test_accepts_llm_kwarg(self) -> None:
        """ChatSession(sid, llm=client) — slash /new 路径需要."""
        _make_done_session("s_22222222")
        sentinel = object()  # 任意 stub, 此 test 不调它
        chat = ChatSession("s_22222222", llm=sentinel)  # type: ignore[arg-type]
        assert chat.llm is sentinel


class TestMakeDoneSessionFixture:
    """Phase 14: Helper fixture 支持自定义 stage (stage gate test 用)."""

    def test_default_stage_is_done(self):
        _make_done_session("s_fff00001")
        assert SessionStore().load("s_fff00001").meta.stage == "done"

    def test_stage_param_creates_bootstrap_pending(self):
        _make_done_session("s_fff00002", stage="bootstrap_pending")
        assert SessionStore().load("s_fff00002").meta.stage == "bootstrap_pending"

    def test_stage_param_creates_insight_pending(self):
        _make_done_session("s_fff00003", stage="insight_pending")
        assert SessionStore().load("s_fff00003").meta.stage == "insight_pending"

    def test_stage_param_creates_converged(self):
        _make_done_session("s_fff00004", stage="converged")
        assert SessionStore().load("s_fff00004").meta.stage == "converged"


class TestChatEventMetadata:
    """Phase 16.2 Wave 4: ChatEvent.metadata optional 字段 — wrapper 反解 intervention 用."""

    def test_chat_event_metadata_optional_default_none(self):
        e = ChatEvent(type="slash_show", content="hello")
        assert e.metadata is None


class TestLLMTurnHistory:
    """Phase 16.2 Wave 6: handle_user_input 完整结束后 append llm_turn entry."""

    @pytest.mark.asyncio
    async def test_process_user_turn_appends_llm_turn_history(self, monkeypatch):
        from explain_engine.chat.loop import AssistantTextEvent
        from explain_engine.persistence.storage_v2 import StorageV2

        _make_done_session("s_aaa10061")
        chat = ChatSession("s_aaa10061")

        async def fake_query_loop(chat_arg, llm_arg):
            yield AssistantTextEvent(content="回答 X")

        monkeypatch.setattr("explain_engine.chat.loop.query_loop", fake_query_loop)

        events: list[ChatEvent] = []
        async for ev in chat.handle_user_input("问题 Y", llm=object()):  # type: ignore[arg-type]
            events.append(ev)

        # repl_history 应有 1 个 llm_turn entry
        storage = StorageV2(project_id="test_proj")
        history = storage.load_repl_history("s_aaa10061")
        llm_turn_entries = [e for e in history if e.get("type") == "llm_turn"]
        assert len(llm_turn_entries) == 1
        assert llm_turn_entries[0]["user_input"] == "问题 Y"
        assert llm_turn_entries[0]["assistant_text"] == "回答 X"

    @pytest.mark.asyncio
    async def test_process_user_turn_llm_failure_no_append(self, monkeypatch):
        """LLM 异常: query_loop 中途 raise → async generator propagate, append 点不到, jsonl 空."""
        from explain_engine.chat.loop import AssistantTextEvent
        from explain_engine.persistence.storage_v2 import StorageV2

        _make_done_session("s_aaa10062")
        chat = ChatSession("s_aaa10062")

        async def fake_query_loop(chat_arg, llm_arg):
            yield AssistantTextEvent(content="部分回答")
            raise RuntimeError("LLM 挂了")

        monkeypatch.setattr("explain_engine.chat.loop.query_loop", fake_query_loop)

        with pytest.raises(RuntimeError, match="LLM 挂了"):
            async for _ev in chat.handle_user_input("问题", llm=object()):  # type: ignore[arg-type]
                pass

        storage = StorageV2(project_id="test_proj")
        history = storage.load_repl_history("s_aaa10062")
        llm_turn = [e for e in history if e.get("type") == "llm_turn"]
        assert llm_turn == [], "LLM 异常分支不应写 llm_turn entry"

    @pytest.mark.asyncio
    async def test_process_user_turn_keyboard_interrupt_no_append(self, monkeypatch):
        """SIGINT 中断: query_loop yield 后 raise KeyboardInterrupt, 同理不到 append 点."""
        from explain_engine.chat.loop import AssistantTextEvent
        from explain_engine.persistence.storage_v2 import StorageV2

        _make_done_session("s_aaa10063")
        chat = ChatSession("s_aaa10063")

        async def fake_query_loop(chat_arg, llm_arg):
            yield AssistantTextEvent(content="开始回答")
            raise KeyboardInterrupt()

        monkeypatch.setattr("explain_engine.chat.loop.query_loop", fake_query_loop)

        with pytest.raises(KeyboardInterrupt):
            async for _ev in chat.handle_user_input("问题", llm=object()):  # type: ignore[arg-type]
                pass

        storage = StorageV2(project_id="test_proj")
        history = storage.load_repl_history("s_aaa10063")
        llm_turn = [e for e in history if e.get("type") == "llm_turn"]
        assert llm_turn == [], "SIGINT 中断不应写 llm_turn entry"

    @pytest.mark.asyncio
    async def test_process_user_turn_tool_use_intermediate_not_appended(self, monkeypatch):
        """多轮 LLM ↔ tool: query_loop yield N 个 assistant_text + tool_use events,
        最终 jsonl 仅 1 个 llm_turn entry, assistant_text 是 N 个 text chunks join,
        tool_use events 不入 history."""
        from explain_engine.chat.loop import (
            AssistantTextEvent,
            ToolResultEvent,
            ToolUseEvent,
        )
        from explain_engine.persistence.storage_v2 import StorageV2

        _make_done_session("s_aaa10064")
        chat = ChatSession("s_aaa10064")

        async def fake_query_loop(chat_arg, llm_arg):
            yield AssistantTextEvent(content="先看看 ")
            yield ToolUseEvent(content={"tool": "search", "args": {}})
            yield ToolResultEvent(content={"result": "..."})
            yield AssistantTextEvent(content="然后分析 ")
            yield ToolUseEvent(content={"tool": "calc", "args": {}})
            yield ToolResultEvent(content={"result": "42"})
            yield AssistantTextEvent(content="最终答案是 42.")

        monkeypatch.setattr("explain_engine.chat.loop.query_loop", fake_query_loop)

        async for _ev in chat.handle_user_input("用工具算", llm=object()):  # type: ignore[arg-type]
            pass

        storage = StorageV2(project_id="test_proj")
        history = storage.load_repl_history("s_aaa10064")
        llm_turn = [e for e in history if e.get("type") == "llm_turn"]
        # 仅 1 entry (不是 7 个 events 各 1 个)
        assert len(llm_turn) == 1
        # assistant_text 是 3 chunks 的 join (tool_use 不入)
        assert llm_turn[0]["assistant_text"] == "先看看 然后分析 最终答案是 42."
        # tool_use 没污染
        assert "tool" not in llm_turn[0]["assistant_text"]

    def test_chat_event_metadata_explicit_dict(self):
        e = ChatEvent(type="slash_predict", content="x", metadata={"intervention": "假设 X"})
        assert e.metadata == {"intervention": "假设 X"}
        # 多 key 也行
        e2 = ChatEvent(type="x", content="y", metadata={"a": 1, "b": 2})
        assert e2.metadata == {"a": 1, "b": 2}


class TestChatSessionStatusYield:
    """Phase 19 Task 9: ChatSession.handle_user_input yield status_start/end.

    保守路线: 只 yield status_start/end (spinner mount/unmount signal), 不实装
    thinking_text yield — 留 Phase 20 待 ToolsResponse 加 reasoning field 后跟进.

    status 包 query_loop 整段 try/finally — LLMError 仍冒到 REPL outer loop
    (跟现错误传播契约一致), finally 保 spinner 一定关.
    """

    @pytest.mark.asyncio
    async def test_handle_user_input_yields_status_start_at_head(self, monkeypatch):
        """非 slash + 有 llm 路径: 头部 yield status_start("思考中...")."""
        from explain_engine.chat.loop import AssistantTextEvent

        _make_done_session("s_aaa19001")
        chat = ChatSession("s_aaa19001")

        async def fake_query_loop(chat_arg, llm_arg):
            yield AssistantTextEvent(content="answer")

        monkeypatch.setattr("explain_engine.chat.loop.query_loop", fake_query_loop)

        events: list[ChatEvent] = []
        async for ev in chat.handle_user_input("问题", llm=object()):  # type: ignore[arg-type]
            events.append(ev)

        # status_start 在 query_loop event 之前
        assert events[0].type == "status_start"
        assert events[0].content == STATUS_THINKING

    @pytest.mark.asyncio
    async def test_handle_user_input_yields_status_end_after_query_loop(self, monkeypatch):
        """Phase 20.3: spinner 在首个内容到达即关 (early close), 之后才是正文.

        顺序: status_start → status_end(early) → assistant_text → status_end(finally).
        早关 status_end 让流式文字紧接 spinner 流出, 不再等整 turn 跑完; finally
        仍会再 yield 一次 status_end (TUI _unmount_status 幂等, 重复安全).
        """
        from explain_engine.chat.loop import AssistantTextEvent

        _make_done_session("s_19002001")
        chat = ChatSession("s_19002001")

        async def fake_query_loop(chat_arg, llm_arg):
            yield AssistantTextEvent(content="answer")

        monkeypatch.setattr("explain_engine.chat.loop.query_loop", fake_query_loop)

        events: list[ChatEvent] = []
        async for ev in chat.handle_user_input("问题", llm=object()):  # type: ignore[arg-type]
            events.append(ev)

        types = [e.type for e in events]
        assert types[0] == "status_start"
        # spinner 提前关: 首个 status_end 在 assistant_text 之前 (或同步出现)
        assert types.index("status_end") < types.index("assistant_text")
        # finally 仍补一个 status_end (幂等)
        assert types[-1] == "status_end"
        assert types.count("status_end") >= 1

    @pytest.mark.asyncio
    async def test_handle_user_input_yields_status_end_on_query_loop_error(self, monkeypatch):
        """query_loop raise 时, status_end 经 try/finally yield, 然后异常冒到 caller.

        这是跟 ephemeral try/except early return 行为不同 — ChatSession 现错误传播
        契约 LLMError 仍冒到 REPL outer loop, finally 仅保 spinner 一定关.
        """
        from explain_engine.chat.loop import AssistantTextEvent

        _make_done_session("s_19003001")
        chat = ChatSession("s_19003001")

        async def fake_query_loop(chat_arg, llm_arg):
            yield AssistantTextEvent(content="开始")
            raise RuntimeError("LLM 挂了")

        monkeypatch.setattr("explain_engine.chat.loop.query_loop", fake_query_loop)

        collected: list[ChatEvent] = []
        with pytest.raises(RuntimeError, match="LLM 挂了"):
            async for ev in chat.handle_user_input("问题", llm=object()):  # type: ignore[arg-type]
                collected.append(ev)

        # finally 块的 status_end 应已 yield 给 caller (Python async gen finally yield 行为)
        types = [e.type for e in collected]
        assert "status_start" in types
        assert "status_end" in types
        # 顺序: status_start → assistant_text → status_end (finally) → raise
        assert types.index("status_start") < types.index("status_end")

    @pytest.mark.asyncio
    async def test_handle_user_input_no_thinking_text_yield_phase19_conservative(self, monkeypatch):
        """Phase 19 ChatSession 保守路线: query_loop 不 yield thinking_text.

        ToolsResponse 还没 reasoning field (Phase 20 跟进), 现 ChatSession 不实装.
        若以后 query_loop 内自行 yield thinking_text event, ChatSession 透传 OK,
        但 Phase 19 Wave 2 不主动加.
        """
        from explain_engine.chat.loop import AssistantTextEvent

        _make_done_session("s_19004001")
        chat = ChatSession("s_19004001")

        async def fake_query_loop(chat_arg, llm_arg):
            yield AssistantTextEvent(content="answer")

        monkeypatch.setattr("explain_engine.chat.loop.query_loop", fake_query_loop)

        events: list[ChatEvent] = []
        async for ev in chat.handle_user_input("问题", llm=object()):  # type: ignore[arg-type]
            events.append(ev)

        types = [e.type for e in events]
        assert "thinking_text" not in types

    @pytest.mark.asyncio
    async def test_handle_user_input_no_status_when_llm_none(self, monkeypatch):
        """llm=None placeholder 分支不 yield status (没 LLM 调用 = 没必要 spinner)."""
        _make_done_session("s_19005001")
        chat = ChatSession("s_19005001")

        events: list[ChatEvent] = []
        async for ev in chat.handle_user_input("问题", llm=None):
            events.append(ev)

        types = [e.type for e in events]
        assert "status_start" not in types
        assert "status_end" not in types
        assert "placeholder" in types  # 仍走老 placeholder 路径

    @pytest.mark.asyncio
    async def test_handle_user_input_no_status_on_slash_path(self, monkeypatch):
        """slash command 走 dispatch_slash, 不调 LLM, 无 status yield.

        Phase 19 Task 9 status 仅包 query_loop, 不影响 slash 本地路径.
        """
        _make_done_session("s_19006001")
        chat = ChatSession("s_19006001")

        events: list[ChatEvent] = []
        async for ev in chat.handle_user_input("/help"):
            events.append(ev)

        types = [e.type for e in events]
        assert "status_start" not in types
        assert "status_end" not in types
