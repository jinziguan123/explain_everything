"""Phase 14: slash_stage_rules decorator tests (装饰器 4 步: gate / 业务 / transition / hint)."""

import pytest

from explain_engine.chat.session import ChatEvent
from explain_engine.chat.slash_stage_rules import with_stage_gate


class TestPassThrough:
    @pytest.mark.asyncio
    async def test_no_gate_no_transition_no_hint_passes_through(self):
        """allowed=None + success_stage=None + 无 hint → handler events 原样返."""

        @with_stage_gate(
            allowed=None,
            success_stage=None,
            fail_hint_key=None,
            success_hint_key=None,
        )
        async def handler(chat, args):
            return [ChatEvent(type="slash_ok", content="done")]

        events = await handler(_FakeChat(stage="bootstrap_pending"), [])
        assert len(events) == 1
        assert events[0].type == "slash_ok"
        assert events[0].content == "done"


class TestGateCheck:
    @pytest.mark.asyncio
    async def test_blocks_disallowed_stage(self):
        """stage=bp + allowed=[done] → slash_error, handler 不被调."""
        called = {"n": 0}

        @with_stage_gate(allowed=["done"])
        async def handler(chat, args):
            called["n"] += 1
            return [ChatEvent(type="slash_ok", content="should not run")]

        events = await handler(_FakeChat(stage="bootstrap_pending"), [])
        assert called["n"] == 0
        assert len(events) == 1
        assert events[0].type == "slash_error"
        assert "bootstrap_pending" in events[0].content
        assert "['done']" in events[0].content or "'done'" in events[0].content

    @pytest.mark.asyncio
    async def test_allows_listed_stage(self):
        """stage=done + allowed=[done] → handler 被调."""

        @with_stage_gate(allowed=["done"])
        async def handler(chat, args):
            return [ChatEvent(type="slash_ok", content="ran")]

        events = await handler(_FakeChat(stage="done"), [])
        assert events[0].type == "slash_ok"

    @pytest.mark.asyncio
    async def test_allowed_none_means_any_stage(self):
        """allowed=None → 任意 stage 都允许."""

        @with_stage_gate(allowed=None)
        async def handler(chat, args):
            return [ChatEvent(type="slash_ok", content="ran")]

        for stage in ("bootstrap_pending", "insight_pending", "done", "converged"):
            events = await handler(_FakeChat(stage=stage), [])
            assert events[0].type == "slash_ok"


class TestFailHint:
    @pytest.mark.asyncio
    async def test_fail_hint_appended_when_key_in_table(self):
        """gate 失败 + fail_hint_key 在 HINTS_BY_KEY → 加 hint event."""
        from explain_engine.chat.slash_stage_rules import HINTS_BY_KEY

        HINTS_BY_KEY["__test_fail__"] = "test fail message"
        try:
            @with_stage_gate(allowed=["done"], fail_hint_key="__test_fail__")
            async def handler(chat, args):
                return [ChatEvent(type="slash_ok", content="x")]

            events = await handler(_FakeChat(stage="bootstrap_pending"), [])
            types = [e.type for e in events]
            assert "slash_error" in types
            assert "slash_next_step_hint" in types
            hint = next(e for e in events if e.type == "slash_next_step_hint")
            assert hint.content == "test fail message"
        finally:
            HINTS_BY_KEY.pop("__test_fail__", None)

    @pytest.mark.asyncio
    async def test_no_fail_hint_when_key_none(self):
        @with_stage_gate(allowed=["done"], fail_hint_key=None)
        async def handler(chat, args):
            return [ChatEvent(type="slash_ok", content="x")]

        events = await handler(_FakeChat(stage="bootstrap_pending"), [])
        assert all(e.type != "slash_next_step_hint" for e in events)

    @pytest.mark.asyncio
    async def test_no_fail_hint_when_key_missing_from_table(self):
        """fail_hint_key 指向不存在的 key → 静默跳 (不抛)."""

        @with_stage_gate(allowed=["done"], fail_hint_key="nonexistent_key")
        async def handler(chat, args):
            return [ChatEvent(type="slash_ok", content="x")]

        events = await handler(_FakeChat(stage="bootstrap_pending"), [])
        assert all(e.type != "slash_next_step_hint" for e in events)


class TestSuccessTransition:
    @pytest.mark.asyncio
    async def test_stage_updated_on_success(self):
        """handler 返无 error + success_stage 设 → stage 更新."""
        chat = _FakeChat(stage="done")

        @with_stage_gate(allowed=["done"], success_stage="converged")
        async def handler(c, args):
            return [ChatEvent(type="slash_run", content="ok")]

        await handler(chat, [])
        assert chat._session.meta.stage == "converged"

    @pytest.mark.asyncio
    async def test_persist_called_on_success_transition(self):
        chat = _FakeChat(stage="done")

        @with_stage_gate(allowed=["done"], success_stage="converged")
        async def handler(c, args):
            return [ChatEvent(type="slash_run", content="ok")]

        await handler(chat, [])
        assert chat._persist_count == 1

    @pytest.mark.asyncio
    async def test_no_transition_when_handler_returns_error(self):
        """handler 自己 yield slash_error → 不 transition."""
        chat = _FakeChat(stage="done")

        @with_stage_gate(allowed=["done"], success_stage="converged")
        async def handler(c, args):
            return [ChatEvent(type="slash_error", content="business logic 出错")]

        await handler(chat, [])
        assert chat._session.meta.stage == "done"
        assert chat._persist_count == 0

    @pytest.mark.asyncio
    async def test_no_transition_when_success_stage_none(self):
        chat = _FakeChat(stage="done")

        @with_stage_gate(allowed=["done"], success_stage=None)
        async def handler(c, args):
            return [ChatEvent(type="slash_predict", content="ok")]

        await handler(chat, [])
        assert chat._session.meta.stage == "done"

    @pytest.mark.asyncio
    async def test_idempotent_transition_no_double_persist(self):
        """stage 已等于 success_stage → 不 persist (no-op)."""
        chat = _FakeChat(stage="converged")

        @with_stage_gate(allowed=["converged"], success_stage="converged")
        async def handler(c, args):
            return [ChatEvent(type="slash_x", content="ok")]

        await handler(chat, [])
        assert chat._persist_count == 0


class TestSuccessHint:
    @pytest.mark.asyncio
    async def test_success_hint_appended_when_key_in_table(self):
        from explain_engine.chat.slash_stage_rules import HINTS_BY_KEY
        HINTS_BY_KEY["__test_success__"] = "test success message"
        try:
            @with_stage_gate(allowed=["done"], success_hint_key="__test_success__")
            async def handler(c, args):
                return [ChatEvent(type="slash_run", content="ok")]

            events = await handler(_FakeChat(stage="done"), [])
            assert events[-1].type == "slash_next_step_hint"
            assert events[-1].content == "test success message"
        finally:
            HINTS_BY_KEY.pop("__test_success__", None)

    @pytest.mark.asyncio
    async def test_no_success_hint_when_handler_returns_error(self):
        @with_stage_gate(allowed=["done"], success_hint_key="after_run")
        async def handler(c, args):
            return [ChatEvent(type="slash_error", content="x")]

        events = await handler(_FakeChat(stage="done"), [])
        assert all(e.type != "slash_next_step_hint" for e in events)


class TestHintsByKey:
    def test_all_six_keys_present(self):
        from explain_engine.chat.slash_stage_rules import HINTS_BY_KEY
        expected = {
            "need_promote_first",
            "need_compress_first",
            "after_compress",
            "after_run",
            "after_inference",
            "after_rescore",
        }
        assert expected.issubset(HINTS_BY_KEY.keys())

    def test_hint_content_mentions_relevant_commands(self):
        from explain_engine.chat.slash_stage_rules import HINTS_BY_KEY
        c = HINTS_BY_KEY["after_compress"]
        assert "/run" in c
        assert "/predict" in c
        assert "/compress" in HINTS_BY_KEY["need_compress_first"]
        assert "/show" in HINTS_BY_KEY["after_rescore"]


class _FakeChat:
    """Minimal duck-typed chat for decorator unit tests."""

    def __init__(self, stage: str = "done", is_ephemeral: bool = False) -> None:
        self._session = _FakeSession(stage)
        self.is_ephemeral = is_ephemeral
        self._persist_count = 0

    def persist(self) -> None:
        self._persist_count += 1


class _FakeSession:
    def __init__(self, stage: str) -> None:
        self.meta = _FakeMeta(stage)


class _FakeMeta:
    def __init__(self, stage: str) -> None:
        self.stage = stage
