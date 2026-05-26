"""lexicon PG backend light_llm 注入 — Phase 17.2 Task 8.

验 _build_canonical_mechanism_cached 接受 optional light_llm 参数 + 通过
with_light_fallback 走 cheap model. light=None 时跟现行为 100% 一致
(零回归).

跟 Task 7 (JSON backend) 对称, PG impl 仅在 cache miss 时调 LLM,
所以本测试 mock 掉 cache 层, 验 light_llm 传到 _build_canonical_mechanism.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

# 没设 EXPLAIN_TEST_DB_URL 时 skip (跟 test_lexicon_pg_cache.py 同)
_skip_no_test_db = pytest.mark.skipif(
    os.environ.get("EXPLAIN_TEST_DB_URL") is None,
    reason="EXPLAIN_TEST_DB_URL not set (见 deploy/postgres/README.md '建 test db' 一节)",
)


class _FakeNode:
    def __init__(self, id: str = "n1", name: str = "X"):
        self.id = id
        self.name = name
        self.description = "d"
        self.abstraction_level = 1
        self.epistemic = "insight"
        self.activation = 0.7
        self.stability = 0.5


class _FakeEdge:
    def __init__(self, source_node: str, target_node: str):
        self.source_node = source_node
        self.target_node = target_node
        self.relation_type = "manifests_as"


class _FakeGraph:
    def __init__(self, edges: list[_FakeEdge] | None = None):
        self.edges = {f"e{i}": e for i, e in enumerate(edges or [])}
        self.nodes = {}


class _FakeState:
    def __init__(self, edges: list[_FakeEdge] | None = None):
        self.graph = _FakeGraph(edges)


class _FakeMeta:
    def __init__(self):
        self.session_id = "s_test"


class _FakeSession:
    def __init__(self, edges: list[_FakeEdge] | None = None):
        self.state = _FakeState(edges)
        self.meta = _FakeMeta()


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestPgCanonicalLightLlm:
    """Phase 17.2 Task 8: PG _build_canonical_mechanism_cached light_llm 注入."""

    @pytest.mark.asyncio
    async def test_light_llm_passed_through_to_build(self):
        """cache miss 时, light_llm 透传到 _build_canonical_mechanism."""
        from explain_engine.persistence import lexicon_pg
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism_cached,
            get_async_pool,
        )

        node = _FakeNode()
        session = _FakeSession(edges=[_FakeEdge("n1", "n2")])
        pool = await get_async_pool()

        light = AsyncMock(name="light_llm")
        main = AsyncMock(name="main_llm")

        called_with_light = []

        async def fake_build(node, session, llm, light_llm=None):
            called_with_light.append((id(llm), id(light_llm)))
            return "X canon"

        with patch.object(
            lexicon_pg, "_build_canonical_mechanism", new=fake_build,
        ):
            result = await _build_canonical_mechanism_cached(
                node, session, llm=main, pool=pool, light_llm=light,
            )

        assert result == "X canon"
        assert len(called_with_light) == 1
        passed_llm_id, passed_light_id = called_with_light[0]
        assert passed_llm_id == id(main)
        assert passed_light_id == id(light)

    @pytest.mark.asyncio
    async def test_light_llm_none_zero_regression(self):
        """light_llm=None (默) → 跟现行为一致, _build_canonical_mechanism 收 None."""
        from explain_engine.persistence import lexicon_pg
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism_cached,
            get_async_pool,
        )

        node = _FakeNode()
        session = _FakeSession(edges=[_FakeEdge("n1", "n2")])
        pool = await get_async_pool()
        main = AsyncMock(name="main_llm")

        called = []

        async def fake_build(node, session, llm, light_llm=None):
            called.append(light_llm)
            return "Y canon"

        with patch.object(
            lexicon_pg, "_build_canonical_mechanism", new=fake_build,
        ):
            result = await _build_canonical_mechanism_cached(
                node, session, llm=main, pool=pool,
            )

        assert result == "Y canon"
        assert called == [None]
