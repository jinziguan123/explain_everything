"""Phase 11 Wave 1: EphemeralChatSession lifecycle + promote tests."""

from unittest.mock import AsyncMock

import pytest


class TestEphemeralChatSession:
    def test_init_no_sid(self):
        """Ephemeral 永无 sid."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.persistence.storage_v2 import StorageV2

        eph = EphemeralChatSession(storage=StorageV2())
        assert eph.sid is None
        assert eph.is_ephemeral is True

    def test_empty_state(self):
        """初始 graph 空, transcript 空."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.persistence.storage_v2 import StorageV2

        eph = EphemeralChatSession(storage=StorageV2())
        assert len(eph.state.graph.nodes) == 0
        assert eph.transcript == []
        assert eph.memory_md == ""

    def test_default_chat_state(self):
        """默认 budget 10/50 (Phase 9 ChatStateDict)."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.persistence.storage_v2 import StorageV2

        eph = EphemeralChatSession(storage=StorageV2())
        assert eph.chat_state.budget_per_turn_limit == 10
        assert eph.chat_state.budget_per_session_limit == 50

    def test_input_provider_default_none(self):
        """input_provider 默认 None, REPL 启动时 set."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.persistence.storage_v2 import StorageV2

        eph = EphemeralChatSession(storage=StorageV2())
        assert eph.input_provider is None


class TestPromoteToPersistent:
    @pytest.mark.asyncio
    async def test_promote_creates_session(self, monkeypatch):
        """promote_to_persistent 跑 bootstrap + 建 real Session + save."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.persistence.session import SessionStore
        from explain_engine.persistence.storage_v2 import StorageV2
        from explain_engine.schema.nodes import VariableNode

        async def fake_bootstrap(question, llm, lexicon=None, lexicon_top_k=20):
            return [
                VariableNode(
                    id="p_001",
                    name="test_phenomenon",
                    description="test desc",
                    abstraction_level=0,
                    confidence=0.7,
                    epistemic="observation",
                )
            ]

        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.bootstrap_phenomena", fake_bootstrap
        )

        # Wave 1 stub 已 accept all, 不必 monkeypatch review_phenomena_async,
        # 但保留显式 monkeypatch 防 Wave 2 实装后 test 误调真 HITL.
        async def fake_review(phenomena, input_provider, console=None):
            return phenomena

        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.review_phenomena_async", fake_review
        )

        eph = EphemeralChatSession(storage=StorageV2())
        eph.input_provider = AsyncMock()

        mock_llm = AsyncMock()
        real_chat = await eph.promote_to_persistent("why?", mock_llm)

        assert real_chat.sid is not None
        assert real_chat.sid.startswith("s_")
        # real ChatSession 不应 is_ephemeral
        assert getattr(real_chat, "is_ephemeral", False) is False
        # session 已 save (新 sid 在 SessionStore 列表)
        sids = [m.session_id for m in SessionStore().list()]
        assert real_chat.sid in sids
        # graph 含 promote 时 add 的 phenomenon
        assert "p_001" in real_chat.state.graph.nodes

    @pytest.mark.asyncio
    async def test_promote_propagates_chat_state(self, monkeypatch):
        """promote 后 ephemeral.chat_state (含 /budget 改的) 拷到 real ChatSession."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.persistence.storage_v2 import StorageV2
        from explain_engine.schema.nodes import VariableNode

        async def fake_bootstrap(question, llm, lexicon=None, lexicon_top_k=20):
            return [
                VariableNode(
                    id="p_001",
                    name="x",
                    description="d",
                    abstraction_level=0,
                    confidence=0.7,
                    epistemic="observation",
                )
            ]

        async def fake_review(phenomena, input_provider, console=None):
            return phenomena

        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.bootstrap_phenomena", fake_bootstrap
        )
        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.review_phenomena_async", fake_review
        )

        eph = EphemeralChatSession(storage=StorageV2())
        # 模拟用户在 ephemeral 阶段用 /budget 改了 limit
        eph.chat_state.budget_per_turn_limit = 99
        eph.chat_state.budget_per_session_limit = 199

        mock_llm = AsyncMock()
        real_chat = await eph.promote_to_persistent("why?", mock_llm)

        assert real_chat.chat_state.budget_per_turn_limit == 99
        assert real_chat.chat_state.budget_per_session_limit == 199

    @pytest.mark.asyncio
    async def test_promote_bootstrap_error_keeps_ephemeral(self, monkeypatch):
        """bootstrap LLM error → raise, ephemeral 不消失 (caller 留 ephemeral)."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.llm.errors import LLMError
        from explain_engine.persistence.session import SessionStore
        from explain_engine.persistence.storage_v2 import StorageV2

        async def fake_bootstrap(*args, **kwargs):
            raise LLMError("api down")

        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.bootstrap_phenomena", fake_bootstrap
        )

        eph = EphemeralChatSession(storage=StorageV2())
        # baseline: 之前 session 数 (其他 test 可能 create)
        before = len(SessionStore().list())

        mock_llm = AsyncMock()
        with pytest.raises(LLMError):
            await eph.promote_to_persistent("why?", mock_llm)

        # ephemeral 仍是 ephemeral, 无 sid; SessionStore 没新建
        assert eph.sid is None
        assert eph.is_ephemeral is True
        after = len(SessionStore().list())
        assert after == before
