"""Phase 17.1 Wave 5: canonical mechanism cache (Track B) tests.

Wave 5 加 `compute_canonical_signature` + `_build_canonical_mechanism_cached`,
让重复 var (同 name + desc + level + epi + edge topology) 跳 LLM 直返已存 canonical.
省 50-80% LLM cost (重复 abstraction 不重算 canonical sentence).

每 task 一 TestXxx class, DB 涉及的标 `@_skip_no_test_db` + `@reset_pg`.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 没设 EXPLAIN_TEST_DB_URL 时 skip (跟 test_lexicon_pg_pool.py 同)
_skip_no_test_db = pytest.mark.skipif(
    os.environ.get("EXPLAIN_TEST_DB_URL") is None,
    reason="EXPLAIN_TEST_DB_URL not set (见 deploy/postgres/README.md '建 test db' 一节)",
)


# ── Fake helpers (模拟 VariableNode / RelationEdge / Session) ──────────────


class _FakeNode:
    """模拟 VariableNode 仅供 cache test 用."""

    def __init__(
        self,
        id: str = "n1",
        name: str = "默认名",
        description: str = "默认描述",
        abstraction_level: int = 1,
        epistemic: str = "insight",
        activation: float = 0.7,
        stability: float = 0.5,
    ):
        self.id = id
        self.name = name
        self.description = description
        self.abstraction_level = abstraction_level
        self.epistemic = epistemic
        self.activation = activation
        self.stability = stability


class _FakeEdge:
    def __init__(
        self,
        source_node: str,
        target_node: str,
        relation_type: str = "manifests_as",
    ):
        self.source_node = source_node
        self.target_node = target_node
        self.relation_type = relation_type


class _FakeGraph:
    def __init__(self, edges: list[_FakeEdge] | None = None, nodes: dict | None = None):
        self.edges = {f"e{i}": e for i, e in enumerate(edges or [])}
        self.nodes = nodes or {}


class _FakeState:
    def __init__(self, edges: list[_FakeEdge] | None = None, nodes: dict | None = None):
        self.graph = _FakeGraph(edges, nodes)


class _FakeMeta:
    def __init__(self, session_id: str = "s_test"):
        self.session_id = session_id


class _FakeSession:
    def __init__(
        self,
        edges: list[_FakeEdge] | None = None,
        nodes: dict | None = None,
        sid: str = "s_test",
    ):
        self.state = _FakeState(edges, nodes)
        self.meta = _FakeMeta(sid)


# ── Task 5.1: compute_canonical_signature ──────────────────────────────────


class TestComputeCanonicalSignature:
    """Phase 17.1 Task 5.1: compute_canonical_signature sha256[:16].

    Hash 输入 = name + desc + abstraction_level + epistemic + sorted(edge_keys).
    edge 只算 source==node.id 或 target==node.id 的 (排除无关 edge), 排序保
    deterministic.

    改 LLM prompt 时手 bump CANONICAL_MODEL_VERSION → 旧 cache 全 miss → 重 build.
    """

    def test_signature_stable_same_input(self):
        """同 node + edges 调 2 次返同 hash."""
        from explain_engine.persistence.lexicon_pg import (
            compute_canonical_signature,
        )

        node = _FakeNode(id="n1", name="X")
        edges = [_FakeEdge("n1", "n2"), _FakeEdge("n3", "n1")]
        sig1 = compute_canonical_signature(node, edges)
        sig2 = compute_canonical_signature(node, edges)
        assert sig1 == sig2
        assert isinstance(sig1, str)
        assert len(sig1) == 16  # sha256 hex truncated to 16

    def test_signature_changes_on_edge_topology(self):
        """加 1 个相关 edge 后 hash 变."""
        from explain_engine.persistence.lexicon_pg import (
            compute_canonical_signature,
        )

        node = _FakeNode(id="n1")
        sig_before = compute_canonical_signature(node, [_FakeEdge("n1", "n2")])
        sig_after = compute_canonical_signature(
            node, [_FakeEdge("n1", "n2"), _FakeEdge("n1", "n3")],
        )
        assert sig_before != sig_after

    def test_signature_changes_on_name(self):
        """node.name 改 hash 变."""
        from explain_engine.persistence.lexicon_pg import (
            compute_canonical_signature,
        )

        edges = [_FakeEdge("n1", "n2")]
        sig_a = compute_canonical_signature(_FakeNode(id="n1", name="A"), edges)
        sig_b = compute_canonical_signature(_FakeNode(id="n1", name="B"), edges)
        assert sig_a != sig_b

    def test_signature_irrelevant_edges_excluded(self):
        """edges 含 source≠node.id 且 target≠node.id 的, hash 不受影响."""
        from explain_engine.persistence.lexicon_pg import (
            compute_canonical_signature,
        )

        node = _FakeNode(id="n1")
        # 一组只含 node 自己的 edges
        only_relevant = [_FakeEdge("n1", "n2")]
        # 另一组含 1 个无关 edge (source/target 都不是 n1)
        with_irrelevant = [
            _FakeEdge("n1", "n2"),
            _FakeEdge("n7", "n8"),  # 完全无关
        ]
        sig1 = compute_canonical_signature(node, only_relevant)
        sig2 = compute_canonical_signature(node, with_irrelevant)
        assert sig1 == sig2, "无关 edge 不应影响 signature"


# ── Task 5.2: _get_node_edges helper ───────────────────────────────────────


class TestGetNodeEdges:
    """Phase 17.1 Task 5.2: _get_node_edges 取 session.state.graph.edges 中
    source/target == node.id 的 edge list. 给 compute_canonical_signature 用,
    单独函数让 test 容易 mock.
    """

    def test_get_node_edges_filters_relevant_edges(self):
        """mock session 含 graph.edges dict (5 edge, 2 个跟 node 关), 调返 2."""
        from explain_engine.persistence.lexicon_pg import _get_node_edges

        node = _FakeNode(id="n1")
        # 5 个 edge — 只 2 个跟 n1 有关 (e1 outgoing, e3 incoming)
        edges = [
            _FakeEdge("n1", "n2"),           # e1: n1 outgoing  ✓
            _FakeEdge("n7", "n8"),           # e2: 无关
            _FakeEdge("n3", "n1"),           # e3: n1 incoming  ✓
            _FakeEdge("n4", "n5"),           # e4: 无关
            _FakeEdge("n6", "n9"),           # e5: 无关
        ]
        session = _FakeSession(edges=edges)
        relevant = _get_node_edges(node, session)
        assert len(relevant) == 2
        # 全是 source/target == n1
        for e in relevant:
            assert e.source_node == "n1" or e.target_node == "n1"

    def test_get_node_edges_empty_when_no_relevant(self):
        """全无关 edge 返 []."""
        from explain_engine.persistence.lexicon_pg import _get_node_edges

        node = _FakeNode(id="n1")
        session = _FakeSession(edges=[
            _FakeEdge("n7", "n8"),
            _FakeEdge("n3", "n9"),
        ])
        assert _get_node_edges(node, session) == []

    def test_get_node_edges_empty_when_no_edges(self):
        """session 完全无 edge 时返 []."""
        from explain_engine.persistence.lexicon_pg import _get_node_edges

        node = _FakeNode(id="n1")
        session = _FakeSession(edges=[])
        assert _get_node_edges(node, session) == []


# ── DB helper for Task 5.3+ (insert pre-existing var into PG) ──────────────


def _insert_var_with_signature(
    global_id: str,
    name: str,
    description: str,
    abstraction_level: int,
    epistemic: str,
    canonical_mechanism: str,
    canonical_signature: str,
    canonical_model_ver: str = "v1",
):
    """sync helper: 把 1 个 var (含 canonical_signature) insert 进 DB.

    模拟 flush_to_lexicon 写 var 之后的 state — cache test 前置条件.
    """
    import psycopg

    from explain_engine.persistence.lexicon_pg import _get_dsn

    now = datetime.now(UTC)
    with psycopg.connect(_get_dsn()) as conn:
        conn.execute(
            """INSERT INTO variables (
                global_id, name, description, abstraction_level, epistemic,
                canonical_mechanism, canonical_signature, canonical_model_ver,
                reuse_count, avg_essentialness, avg_consistency,
                first_seen_at, last_seen_at, source_sessions
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s
            )""",
            (
                global_id, name, description, abstraction_level, epistemic,
                canonical_mechanism, canonical_signature, canonical_model_ver,
                1, 0.5, 0.5,
                now, now, ["s_pre"],
            ),
        )
        conn.commit()


# ── Task 5.3: _build_canonical_mechanism_cached (cache-lookup-first) ──────


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestBuildCanonicalMechanismCachedLookup:
    """Phase 17.1 Task 5.3: _build_canonical_mechanism_cached cache-lookup-first.

    - Cache miss (DB 无匹 signature 行) → 调 Wave 4 _build_canonical_mechanism (真 LLM).
    - Cache hit (signature + model_ver 匹) → 直返已存 canonical_mechanism, 跳 LLM.
    """

    @pytest.mark.asyncio
    async def test_cache_miss_calls_llm(self):
        """空 DB, _build_canonical_mechanism 被 mock 返 'X canon', _cached 应返同值
        + mock 被调 1 次."""
        from explain_engine.persistence import lexicon_pg
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism_cached,
            get_async_pool,
        )

        node = _FakeNode(id="n1", name="X")
        session = _FakeSession(edges=[_FakeEdge("n1", "n2")])
        pool = await get_async_pool()

        with patch.object(
            lexicon_pg,
            "_build_canonical_mechanism",
            new_callable=AsyncMock,
            return_value="X canon",
        ) as mock_build:
            result = await _build_canonical_mechanism_cached(
                node, session, llm=None, pool=pool,
            )
        assert result == "X canon"
        mock_build.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_hit_returns_stored_canonical(self):
        """先 insert var with signature='sig_x' + canonical='cached canon'.
        mock compute_canonical_signature 返 'sig_x'. _build_canonical_mechanism
        被 mock 抛 RuntimeError (若被调即 fail). 调 _cached 返 'cached canon'."""
        from explain_engine.persistence import lexicon_pg
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism_cached,
            get_async_pool,
        )

        # 先 insert 已存 var (cache state)
        _insert_var_with_signature(
            global_id="v_cached01",
            name="缓存 var",
            description="d",
            abstraction_level=1,
            epistemic="insight",
            canonical_mechanism="cached canon",
            canonical_signature="sig_x_aaa1bbcc",  # 16 char-like
            canonical_model_ver="v1",
        )

        node = _FakeNode(id="n1", name="any")  # name irrelevant — signature mock
        session = _FakeSession(edges=[])
        pool = await get_async_pool()

        # mock signature 算法返已存的 sig_x_aaa1bbcc + LLM 路径若被调即 boom
        async def _boom(*args, **kwargs):
            raise RuntimeError("LLM should NOT be called on cache hit")

        with patch.object(
            lexicon_pg, "compute_canonical_signature",
            return_value="sig_x_aaa1bbcc",
        ), patch.object(
            lexicon_pg, "_build_canonical_mechanism",
            new=_boom,
        ):
            result = await _build_canonical_mechanism_cached(
                node, session, llm=None, pool=pool,
            )
        assert result == "cached canon"


# ── Task 5.4: cache hit 跳 LLM 验证 (强 assert_not_called + 用 MagicMock llm) ──


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestCacheHitSkipsLlm:
    """Phase 17.1 Task 5.4: cache hit 时 llm 对象 + _build_canonical_mechanism
    全无调用 (strict assert_not_called, 比 5.3 的 raise 更直接).

    场景: llm 是 MagicMock — 若代码路径错误进 _build_canonical_mechanism,
    llm.chat 会被调, 测试自然 fail.
    """

    @pytest.mark.asyncio
    async def test_cache_hit_does_not_invoke_llm_mock(self):
        """signature 命中后, 既不调 _build_canonical_mechanism 也不 touch llm."""
        from explain_engine.persistence import lexicon_pg
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism_cached,
            get_async_pool,
        )

        # 1. 预存 cache: signature='sig_hit_v1_1234', canonical='hit canon'
        _insert_var_with_signature(
            global_id="v_hit00001",
            name="hit var",
            description="d",
            abstraction_level=1,
            epistemic="insight",
            canonical_mechanism="hit canon",
            canonical_signature="sig_hit_v1_1234",
            canonical_model_ver="v1",
        )

        # 2. llm 是 strict MagicMock — 任何 attr access (.chat etc) 都会记录
        llm_mock = MagicMock(name="llm")
        # AsyncMock 包 _build_canonical_mechanism — 严格 not_called
        build_mock = AsyncMock(return_value="should not happen")

        node = _FakeNode(id="n_hit", name="hit any")
        session = _FakeSession(edges=[_FakeEdge("n_hit", "n_other")])
        pool = await get_async_pool()

        with patch.object(
            lexicon_pg, "compute_canonical_signature",
            return_value="sig_hit_v1_1234",
        ), patch.object(
            lexicon_pg, "_build_canonical_mechanism", new=build_mock,
        ):
            result = await _build_canonical_mechanism_cached(
                node, session, llm=llm_mock, pool=pool,
            )

        # 3. 验证: 返已存 canonical
        assert result == "hit canon"
        # 4. 验证: LLM 完全没被 touch (没访问任何 attr, 没调任何 method)
        llm_mock.assert_not_called()
        assert llm_mock.method_calls == []
        # 5. 验证: _build_canonical_mechanism 也没被调
        build_mock.assert_not_called()


# ── Task 5.5: cache miss → LLM → 后续 hit (mimics flush sequence) ──────────


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestCacheMissThenHit:
    """Phase 17.1 Task 5.5: 完整 lifecycle —
      1. 首次调 _cached → cache miss → LLM mock 调 1 次 → 返 'first canon'
      2. 模拟 flush_to_lexicon 写 var (含 signature + canonical) 进 DB
      3. 第 2 次调 _cached → cache hit → LLM mock 不再被调

    Wave 5 暂不改 flush_to_lexicon 调 cached, 所以第 2 步手动 insert var 来
    模拟 flush 之后的状态.
    """

    @pytest.mark.asyncio
    async def test_miss_then_hit_skips_second_llm_call(self):
        from explain_engine.persistence import lexicon_pg
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism_cached,
            compute_canonical_signature,
            get_async_pool,
        )

        node = _FakeNode(
            id="n_miss",
            name="miss_var",
            description="miss desc",
            abstraction_level=1,
            epistemic="insight",
        )
        session = _FakeSession(edges=[_FakeEdge("n_miss", "n_x")])
        pool = await get_async_pool()

        # 1. 算出本 node 的实际 signature (后续 insert 用同一个)
        signature = compute_canonical_signature(
            node, [e for e in session.state.graph.edges.values()],
        )

        # 2. 第 1 次: cache miss → LLM mock 被调 1 次
        build_mock = AsyncMock(return_value="first canon")
        with patch.object(
            lexicon_pg, "_build_canonical_mechanism", new=build_mock,
        ):
            result_1 = await _build_canonical_mechanism_cached(
                node, session, llm=None, pool=pool,
            )
        assert result_1 == "first canon"
        assert build_mock.call_count == 1

        # 3. 模拟 flush_to_lexicon 写 var (实际是 flush 写, 这里手动 insert):
        #    signature 跟 node 实际算出的一致, canonical 跟 LLM 返的一致.
        _insert_var_with_signature(
            global_id="v_miss0001",
            name=node.name,
            description=node.description,
            abstraction_level=node.abstraction_level,
            epistemic=node.epistemic,
            canonical_mechanism="first canon",
            canonical_signature=signature,
            canonical_model_ver="v1",
        )

        # 4. 第 2 次: cache hit → LLM mock 不再被调
        build_mock_2 = AsyncMock(return_value="should not be called")
        with patch.object(
            lexicon_pg, "_build_canonical_mechanism", new=build_mock_2,
        ):
            result_2 = await _build_canonical_mechanism_cached(
                node, session, llm=None, pool=pool,
            )
        assert result_2 == "first canon"
        build_mock_2.assert_not_called()
