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
