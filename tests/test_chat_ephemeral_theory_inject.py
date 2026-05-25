"""Phase 16: EphemeralChatSession.promote_to_persistent 注入 stable theories."""

import pytest


def _mk_theory(id):
    """构造 minimal stable Theory."""
    from explain_engine.engines.theory.theory import Theory
    return Theory(
        id=id, motif_type="chain",
        theme_ids=("th_001",), node_ids=("v_a",),
        edges=(("v_a", "v_b", "causes"),),
        supporting_sessions=("s_1", "s_2"),
        natural_language_summary=f"theory {id}",
        structure_complexity=2,
        first_seen_session="s_1", last_seen_session="s_2",
        predictive_power=0.7, stability_status="stable",
    )


class FakeLLM:
    """Mock LLM, 永返 minimal valid bootstrap output."""
    async def chat(self, messages, schema=None):
        class R:
            parsed: dict = {"phenomena": []}  # noqa: RUF012
        return R()


async def _stub_review(phenomena, input_provider, console=None):
    """Pass-through review stub (避真 HITL)."""
    return phenomena


class TestPromoteWithTheoryInject:
    @pytest.mark.asyncio
    async def test_stable_theories_passed_to_propose(self, tmp_path, monkeypatch):
        """mock get_active_theories 返 2 stable theory + 0 rejected,
        assert bootstrap_phenomena 被调时 theories=... 含 2 个."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.engines.theory.cache import TheoriesCache
        from explain_engine.persistence.storage_v2 import StorageV2
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))

        mocked_cache = TheoriesCache(
            stable_theories=[_mk_theory("t_1"), _mk_theory("t_2")],
            tentative_theories=[],
            rejected_theory_ids=set(),
            session_ids_snapshot=["s_1", "s_2", "s_3"],
            cold_start_threshold=3,
        )

        captured_theories: list = []

        async def mock_bootstrap(question, llm, **kwargs):
            captured_theories.append(kwargs.get("theories"))
            from explain_engine.schema.nodes import VariableNode
            return [VariableNode(
                id="p_001", name="fake", description="d",
                abstraction_level=0, confidence=0.7, epistemic="observation",
            )]

        # ephemeral.py 顶层 from-import, patch 须打 ephemeral 命名空间
        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.bootstrap_phenomena", mock_bootstrap,
        )
        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.review_phenomena_async", _stub_review,
        )

        # get_active_theories 在 ephemeral.py 内 局部 import — patch source 即可
        def mock_get_active(storage, embedder=None, force_recompute=False):
            return mocked_cache

        monkeypatch.setattr(
            "explain_engine.engines.theory.cache.get_active_theories",
            mock_get_active,
        )

        eph = EphemeralChatSession(storage=StorageV2())
        try:
            await eph.promote_to_persistent("test question", FakeLLM())
        except Exception:
            pass  # promote_to_persistent 内 review 等可能爆, 我们只验 bootstrap 被调 + theories 传

        # bootstrap_phenomena 被调
        assert len(captured_theories) == 1
        theories_arg = captured_theories[0]
        assert theories_arg is not None
        assert len(theories_arg) == 2
        assert {t.id for t in theories_arg} == {"t_1", "t_2"}

    @pytest.mark.asyncio
    async def test_cache_fail_fallback_empty(self, tmp_path, monkeypatch):
        """mock get_active_theories 抛 → promote 仍正常, theories=None or [] 传."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.persistence.storage_v2 import StorageV2
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))

        captured = []

        async def mock_bootstrap(question, llm, **kwargs):
            captured.append(kwargs.get("theories"))
            from explain_engine.schema.nodes import VariableNode
            return [VariableNode(
                id="p_001", name="fake", description="d",
                abstraction_level=0, confidence=0.7, epistemic="observation",
            )]

        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.bootstrap_phenomena", mock_bootstrap,
        )
        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.review_phenomena_async", _stub_review,
        )

        def mock_get_active_fail(storage, embedder=None, force_recompute=False):
            raise RuntimeError("simulated cache fail")

        monkeypatch.setattr(
            "explain_engine.engines.theory.cache.get_active_theories",
            mock_get_active_fail,
        )

        eph = EphemeralChatSession(storage=StorageV2())
        try:
            await eph.promote_to_persistent("test", FakeLLM())
        except Exception:
            pass

        # bootstrap 仍被调, theories 是空 list 或 None (fallback)
        assert len(captured) == 1
        assert not captured[0]  # None or empty

    @pytest.mark.asyncio
    async def test_rejected_theories_filtered(self, tmp_path, monkeypatch):
        """rejected_theory_ids 内的 theory 不传给 bootstrap."""
        from explain_engine.chat.ephemeral import EphemeralChatSession
        from explain_engine.engines.theory.cache import TheoriesCache
        from explain_engine.engines.theory.theory import Theory
        from explain_engine.persistence.storage_v2 import StorageV2
        monkeypatch.setenv("EXPLAIN_HOME", str(tmp_path))

        def _mk(id):
            return Theory(
                id=id, motif_type="chain",
                theme_ids=("th_001",), node_ids=("v_a",),
                edges=(("v_a", "v_b", "causes"),),
                supporting_sessions=("s_1",),
                natural_language_summary=f"theory {id}",
                structure_complexity=2,
                first_seen_session="s_1", last_seen_session="s_1",
                predictive_power=0.5, stability_status="stable",
            )

        mocked = TheoriesCache(
            stable_theories=[_mk("t_keep"), _mk("t_reject")],
            tentative_theories=[],
            rejected_theory_ids={"t_reject"},
            session_ids_snapshot=["s_1", "s_2", "s_3"],
            cold_start_threshold=3,
        )

        captured = []

        async def mock_bootstrap(question, llm, **kwargs):
            captured.append(kwargs.get("theories"))
            from explain_engine.schema.nodes import VariableNode
            return [VariableNode(
                id="p_001", name="fake", description="d",
                abstraction_level=0, confidence=0.7, epistemic="observation",
            )]

        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.bootstrap_phenomena", mock_bootstrap,
        )
        monkeypatch.setattr(
            "explain_engine.chat.ephemeral.review_phenomena_async", _stub_review,
        )
        monkeypatch.setattr(
            "explain_engine.engines.theory.cache.get_active_theories",
            lambda storage, embedder=None, force_recompute=False: mocked,
        )

        eph = EphemeralChatSession(storage=StorageV2())
        try:
            await eph.promote_to_persistent("test", FakeLLM())
        except Exception:
            pass

        assert len(captured) == 1
        assert captured[0] is not None
        ids = {t.id for t in captured[0]}
        assert "t_keep" in ids
        assert "t_reject" not in ids
