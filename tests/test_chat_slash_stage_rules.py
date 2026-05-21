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
