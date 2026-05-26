"""Phase 17.1 Wave 4: lexicon_pg.py 公共 API tests (替老 lexicon.py JSON 行为).

每 task 一个 TestXxx class, fixture `reset_pg` (TRUNCATE per test) + EXPLAIN_DB_URL
透明指向 explain_test 库. 没设 EXPLAIN_TEST_DB_URL 时自动 skip.
"""
from __future__ import annotations

import os

import pytest

from explain_engine.schema.nodes import VariableNode

# 没设 EXPLAIN_TEST_DB_URL 时 skip (同 test_lexicon_pg_pool.py)
_skip_no_test_db = pytest.mark.skipif(
    os.environ.get("EXPLAIN_TEST_DB_URL") is None,
    reason="EXPLAIN_TEST_DB_URL not set (见 deploy/postgres/README.md '建 test db' 一节)",
)


def _make_node(
    nid: str = "c_001",
    name: str = "长期不确定性",
    abstraction_level: int = 1,
    activation: float = 0.8,
    lifecycle_state: str = "active",
    epistemic: str = "insight",
    stability: float = 0.0,
) -> VariableNode:
    """Helper: 建 VariableNode (复用 tests/test_engines_lexicon.py _make_node 风格)."""
    return VariableNode(
        id=nid,
        name=name,
        description=f"{name} 的描述",
        abstraction_level=abstraction_level,  # type: ignore[arg-type]
        confidence=0.7,
        epistemic=epistemic,  # type: ignore[arg-type]
        activation=activation,
        stability=stability,
        lifecycle_state=lifecycle_state,  # type: ignore[arg-type]
    )


# ── Task 4.1: _should_promote ───────────────────────────────────────────


class TestShouldPromote:
    """Phase 17.1 Task 4.1: _should_promote (跟老 lexicon._should_promote 同语义).

    Promote 条件:
      - abstraction_level >= 1 (L0 observation 不进 lexicon)
      - lifecycle_state == 'active' (stale/decayed 不 promote)
      - activation >= 0.5 (conservative threshold)
    """

    def test_should_promote_l1_high_activation_active(self):
        from explain_engine.persistence.lexicon_pg import _should_promote

        node = _make_node(abstraction_level=1, activation=0.8, lifecycle_state="active")
        assert _should_promote(node) is True

    def test_should_promote_l0_returns_false(self):
        from explain_engine.persistence.lexicon_pg import _should_promote

        node = _make_node(
            abstraction_level=0, activation=0.9, lifecycle_state="active",
            epistemic="observation",
        )
        assert _should_promote(node) is False

    def test_should_promote_stale_returns_false(self):
        from explain_engine.persistence.lexicon_pg import _should_promote

        node = _make_node(
            abstraction_level=2, activation=0.9, lifecycle_state="stale",
        )
        assert _should_promote(node) is False

    def test_should_promote_low_activation_returns_false(self):
        from explain_engine.persistence.lexicon_pg import _should_promote

        node = _make_node(
            abstraction_level=1, activation=0.3, lifecycle_state="active",
        )
        assert _should_promote(node) is False


# ── Task 4.2: _build_canonical_mechanism ────────────────────────────────


class _FakeEdge:
    def __init__(self, source_node: str, target_node: str):
        self.source_node = source_node
        self.target_node = target_node


class _FakeGraph:
    def __init__(self, nodes: dict, edges: dict):
        self.nodes = nodes
        self.edges = edges


class _FakeState:
    def __init__(self, graph: _FakeGraph):
        self.graph = graph


class _FakeMeta:
    def __init__(self, session_id: str):
        self.session_id = session_id


class _FakeSession:
    def __init__(self, graph: _FakeGraph, session_id: str = "s_fake0001"):
        self.state = _FakeState(graph)
        self.meta = _FakeMeta(session_id)


def _build_simple_session(node_name: str = "n_a") -> _FakeSession:
    """造一个 session: node_name 有 1 outgoing (n_b) + 1 incoming (n_c)."""
    n_a = _make_node(nid=node_name, name=node_name)
    n_b = _make_node(nid="n_b", name="n_b 后继")
    n_c = _make_node(nid="n_c", name="n_c 前驱")
    edges = {
        "e1": _FakeEdge(source_node=node_name, target_node="n_b"),
        "e2": _FakeEdge(source_node="n_c", target_node=node_name),
    }
    nodes = {node_name: n_a, "n_b": n_b, "n_c": n_c}
    return _FakeSession(_FakeGraph(nodes, edges))


class TestBuildCanonicalMechanism:
    """Phase 17.1 Task 4.2: _build_canonical_mechanism edge fallback + LLM 路径."""

    @pytest.mark.asyncio
    async def test_build_canonical_mechanism_no_llm(self):
        """llm=None 走 edge fallback, 返非空 str (含 outgoing / incoming neighbor name)."""
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism,
        )

        session = _build_simple_session("n_a")
        node = session.state.graph.nodes["n_a"]
        mech = await _build_canonical_mechanism(node, session, llm=None)
        assert isinstance(mech, str)
        assert len(mech) > 0
        assert "n_b 后继" in mech
        assert "n_c 前驱" in mech

    @pytest.mark.asyncio
    async def test_build_canonical_mechanism_no_neighbors_fallback(self):
        """孤立 node (无 edge) 时返默认 '<name> (无 edge 上下文)'."""
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism,
        )

        n_iso = _make_node(nid="n_iso", name="孤岛")
        session = _FakeSession(_FakeGraph({"n_iso": n_iso}, {}))
        mech = await _build_canonical_mechanism(n_iso, session, llm=None)
        assert "孤岛" in mech

    @pytest.mark.asyncio
    async def test_build_canonical_mechanism_with_llm_mock(self, mock_llm_response):
        """llm 非 None 时调 LLM, 返 response.text (cap 100 chars / 第 1 行)."""
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism,
        )

        # mock LLM: chat() 返一个 Response, text = '通常 cause X; 由 Y cause'
        class _MockLLM:
            async def chat(self, messages, schema=None, model=None):
                return mock_llm_response({}, raw_text="通常 cause X; 由 Y cause")

        session = _build_simple_session("n_a")
        node = session.state.graph.nodes["n_a"]
        mech = await _build_canonical_mechanism(node, session, llm=_MockLLM())
        assert mech == "通常 cause X; 由 Y cause"

    @pytest.mark.asyncio
    async def test_build_canonical_mechanism_llm_error_fallback(self):
        """LLM 调 raise LLMError → fall back to edge-based summary."""
        from explain_engine.llm.errors import LLMError
        from explain_engine.persistence.lexicon_pg import (
            _build_canonical_mechanism,
        )

        class _BadLLM:
            async def chat(self, messages, schema=None, model=None):
                raise LLMError("boom")

        session = _build_simple_session("n_a")
        node = session.state.graph.nodes["n_a"]
        mech = await _build_canonical_mechanism(node, session, llm=_BadLLM())
        # fallback 应含 neighbor name
        assert "n_b 后继" in mech


# ── Task 4.3: _batch_embed ──────────────────────────────────────────────


class TestBatchEmbed:
    """Phase 17.1 Task 4.3: _batch_embed (BGE-M3 + EXPLAIN_EMBEDDING_DISABLED fallback)."""

    @pytest.mark.asyncio
    async def test_batch_embed_disabled_returns_none_list(self, monkeypatch):
        """EXPLAIN_EMBEDDING_DISABLED=1 → 返 [None] * len(canonicals), 不 load BGE-M3."""
        from explain_engine.persistence.lexicon_pg import _batch_embed

        # conftest autouse 已 set 这个 env (默认 disabled), 显式确认
        monkeypatch.setenv("EXPLAIN_EMBEDDING_DISABLED", "1")
        out = await _batch_embed(["a", "b", "c"])
        assert out == [None, None, None]

    @pytest.mark.asyncio
    async def test_batch_embed_empty_returns_empty(self):
        """空 input 返 [] (省 model load)."""
        from explain_engine.persistence.lexicon_pg import _batch_embed

        out = await _batch_embed([])
        assert out == []


# ── Task 4.4: flush_to_lexicon 主流程 ───────────────────────────────────


def _build_multi_node_session(
    session_id: str = "s_flush001",
    n: int = 2,
    abstraction_level: int = 1,
    activation: float = 0.7,
    lifecycle_state: str = "active",
) -> _FakeSession:
    """造 session: n 个 promote-qualified node (L1+, active, activation>=0.5).

    每 node 互不连接 (无 edge), 走 edge fallback canonical = '<name> (无 edge 上下文)'.
    """
    nodes = {}
    for i in range(n):
        nid = f"n_flush_{i:03d}"
        nodes[nid] = _make_node(
            nid=nid,
            name=f"概念 {i}",
            abstraction_level=abstraction_level,
            activation=activation,
            lifecycle_state=lifecycle_state,
        )
    return _FakeSession(_FakeGraph(nodes, {}), session_id)


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestFlushToLexicon:
    """Phase 17.1 Task 4.4: flush_to_lexicon PG impl (替老 JSON).

    Pipeline:
      1. session.state.graph.nodes 过 _should_promote → candidates
      2. sort by activation desc
      3. _build_canonical_mechanism (top-K LLM, rest edge fallback)
      4. _batch_embed
      5. async with transaction: per candidate find_duplicate → merge OR insert
      6. return promoted count (insert-new 数, merge 算 0)
    """

    @pytest.mark.asyncio
    async def test_flush_to_lexicon_new_var_inserts(self):
        """2 个 promote 节点全新 insert (库空, 无 dup), promoted=2, DB count=2."""
        import psycopg

        from explain_engine.persistence.lexicon_pg import (
            _get_dsn,
            flush_to_lexicon,
        )

        session = _build_multi_node_session(n=2)
        promoted = await flush_to_lexicon(session, storage=None, llm=None)
        assert promoted == 2

        # 真 DB count 校验
        with psycopg.connect(_get_dsn()) as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM variables").fetchone()[0]
        assert cnt == 2

    @pytest.mark.asyncio
    async def test_flush_to_lexicon_skips_non_qualified(self):
        """L0 / stale / low activation 全不 promote, promoted=0."""
        import psycopg

        from explain_engine.persistence.lexicon_pg import (
            _get_dsn,
            flush_to_lexicon,
        )

        nodes = {
            "l0": _make_node(nid="l0", name="l0", abstraction_level=0,
                             activation=0.9, lifecycle_state="active",
                             epistemic="observation"),
            "stale": _make_node(nid="stale", name="stale",
                                abstraction_level=2, activation=0.9,
                                lifecycle_state="stale"),
            "low": _make_node(nid="low", name="low", abstraction_level=1,
                              activation=0.3, lifecycle_state="active"),
        }
        session = _FakeSession(_FakeGraph(nodes, {}), "s_skip0001")
        promoted = await flush_to_lexicon(session, storage=None, llm=None)
        assert promoted == 0

        with psycopg.connect(_get_dsn()) as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM variables").fetchone()[0]
        assert cnt == 0

    @pytest.mark.asyncio
    async def test_flush_to_lexicon_dup_merges(self):
        """先 insert 1 var 有 embedding, 然后 flush 含 1 个 sim>0.85 node → merge (promoted=0, DB count=1)."""
        import numpy as np
        import psycopg

        from explain_engine.persistence.lexicon_pg import (
            _get_dsn,
            _insert_var,
            flush_to_lexicon,
            get_async_pool,
        )

        rng = np.random.default_rng(seed=7)
        base_emb = rng.random(1024).astype(np.float32).tolist()
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)

        pool = await get_async_pool()
        async with pool.connection() as conn:
            await _insert_var(conn, {
                "global_id": "v_winner99",
                "name": "已有 var",
                "description": "winner",
                "abstraction_level": 1,
                "epistemic": "insight",
                "canonical_mechanism": "已有 canonical",
                "canonical_signature": "sig123",
                "canonical_model_ver": "v1",
                "reuse_count": 2,
                "avg_essentialness": 0.6,
                "avg_consistency": 0.8,
                "first_seen_at": now,
                "last_seen_at": now,
                "source_sessions": ["s_orig0001"],
                "embedding": base_emb,
            })

        # mock _batch_embed 让候选 node 的 embedding = base_emb (= 100% sim, 必 merge)
        from explain_engine.persistence import lexicon_pg as lp

        async def _mock_batch_embed(canonicals):
            return [base_emb] * len(canonicals)

        # 用 monkeypatch 直接 setattr (无 fixture, 手动 try/finally)
        orig = lp._batch_embed
        lp._batch_embed = _mock_batch_embed
        try:
            session = _build_multi_node_session(session_id="s_dup00001", n=1)
            promoted = await flush_to_lexicon(session, storage=None, llm=None)
        finally:
            lp._batch_embed = orig

        # merge → promoted 计数为 0 (insert 新数), DB 仍 1 行
        assert promoted == 0
        with psycopg.connect(_get_dsn()) as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM variables").fetchone()[0]
            row = conn.execute(
                "SELECT reuse_count, source_sessions FROM variables WHERE global_id = %s",
                ("v_winner99",),
            ).fetchone()
        assert cnt == 1
        assert row[0] == 3  # 2 + 1
        # source_sessions union 含新 sid
        assert "s_dup00001" in row[1]
        assert "s_orig0001" in row[1]


# ── Task 4.5: get_lexicon_top_k_for_compress (sync) ─────────────────────


def _insert_sample_var_sync(global_id: str, **fitness_kw):
    """sync helper: psycopg.connect 直接 insert (用于 sync API test)."""
    import psycopg
    from psycopg.rows import dict_row  # noqa: F401

    from explain_engine.persistence.lexicon_pg import _get_dsn

    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    defaults = {
        "global_id": global_id,
        "name": f"name_{global_id}",
        "description": "desc",
        "abstraction_level": 1,
        "epistemic": "insight",
        "canonical_mechanism": "mech",
        "canonical_signature": "sig",
        "canonical_model_ver": "v1",
        "reuse_count": 1,
        "avg_essentialness": 0.5,
        "avg_consistency": 0.5,
        "first_seen_at": now,
        "last_seen_at": now,
        "source_sessions": ["s_x"],
    }
    defaults.update(fitness_kw)
    with psycopg.connect(_get_dsn()) as conn:
        conn.execute(
            """INSERT INTO variables (
                global_id, name, description, abstraction_level, epistemic,
                canonical_mechanism, canonical_signature, canonical_model_ver,
                reuse_count, avg_essentialness, avg_consistency,
                first_seen_at, last_seen_at, source_sessions
            ) VALUES (
                %(global_id)s, %(name)s, %(description)s, %(abstraction_level)s, %(epistemic)s,
                %(canonical_mechanism)s, %(canonical_signature)s, %(canonical_model_ver)s,
                %(reuse_count)s, %(avg_essentialness)s, %(avg_consistency)s,
                %(first_seen_at)s, %(last_seen_at)s, %(source_sessions)s
            )""",
            defaults,
        )
        conn.commit()


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestGetLexiconTopKForCompress:
    """Phase 17.1 Task 4.5: get_lexicon_top_k_for_compress (sync API)."""

    def test_get_lexicon_top_k_returns_dict_list_sorted_by_composite(self):
        from explain_engine.persistence.lexicon_pg import (
            get_lexicon_top_k_for_compress,
        )

        # 3 var: composite = ess * cons * reuse
        # v_low: 0.1 * 0.1 * 1 = 0.01
        # v_mid: 0.5 * 0.5 * 4 = 1.00
        # v_top: 0.9 * 0.9 * 10 = 8.10
        _insert_sample_var_sync(
            "v_low00001", avg_essentialness=0.1, avg_consistency=0.1, reuse_count=1,
        )
        _insert_sample_var_sync(
            "v_mid00001", avg_essentialness=0.5, avg_consistency=0.5, reuse_count=4,
        )
        _insert_sample_var_sync(
            "v_top00001", avg_essentialness=0.9, avg_consistency=0.9, reuse_count=10,
        )

        rows = get_lexicon_top_k_for_compress(storage=None, k=2)
        assert isinstance(rows, list)
        assert len(rows) == 2
        gids = [r["global_id"] for r in rows]
        assert gids == ["v_top00001", "v_mid00001"]
        # 是 dict (含其他字段)
        assert "name" in rows[0]
        assert "canonical_mechanism" in rows[0]

    def test_get_lexicon_top_k_empty_db_returns_empty(self):
        from explain_engine.persistence.lexicon_pg import (
            get_lexicon_top_k_for_compress,
        )

        rows = get_lexicon_top_k_for_compress(storage=None, k=20)
        assert rows == []


# ── Task 4.6: get_top_n_vars ────────────────────────────────────────────


@_skip_no_test_db
@pytest.mark.usefixtures("reset_pg")
class TestGetTopNVars:
    """Phase 17.1 Task 4.6: get_top_n_vars 返 list[VariableNode] (跟老 lexicon API 同)."""

    def test_get_top_n_vars_returns_variable_node_list(self):
        from explain_engine.persistence.lexicon_pg import get_top_n_vars

        _insert_sample_var_sync(
            "v_n_top001", name="顶级 abstraction", abstraction_level=2,
            avg_essentialness=0.9, avg_consistency=0.9, reuse_count=10,
            epistemic="insight",
        )
        _insert_sample_var_sync(
            "v_n_mid001", name="中级 abstraction",
            avg_essentialness=0.5, avg_consistency=0.5, reuse_count=4,
        )
        _insert_sample_var_sync(
            "v_n_low001", name="低级 abstraction",
            avg_essentialness=0.1, avg_consistency=0.1, reuse_count=1,
        )

        nodes = get_top_n_vars(storage=None, n=2)
        assert len(nodes) == 2
        # 全是 VariableNode 实例
        for node in nodes:
            assert isinstance(node, VariableNode)
        # 排序: top 优先
        assert nodes[0].name == "顶级 abstraction"
        assert nodes[0].id == "v_n_top001"
        assert nodes[0].abstraction_level == 2
        assert nodes[0].epistemic == "insight"
        assert nodes[1].name == "中级 abstraction"

    def test_get_top_n_vars_empty_returns_empty_list(self):
        from explain_engine.persistence.lexicon_pg import get_top_n_vars

        assert get_top_n_vars(storage=None, n=10) == []
