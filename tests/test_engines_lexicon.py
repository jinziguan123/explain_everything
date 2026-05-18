"""Phase 10 Wave 1: Variable Lexicon CRUD tests."""

import json

import pytest

from explain_engine.engines.lexicon import (
    _compute_global_id,
    _load_lexicon,
    _now_iso,
    _save_lexicon,
    _should_promote,
    _upsert_var,
)
from explain_engine.schema.nodes import VariableNode


def _make_node(
    nid: str = "c_001",
    name: str = "长期不确定性",
    abstraction_level: int = 2,
    activation: float = 0.8,
    lifecycle_state: str = "active",
    epistemic: str = "insight",
) -> VariableNode:
    """Helper: 建 VariableNode 用于 test."""
    return VariableNode(
        id=nid,
        name=name,
        description=f"{name} 的描述",
        abstraction_level=abstraction_level,
        confidence=0.7,
        epistemic=epistemic,
        activation=activation,
        lifecycle_state=lifecycle_state,
    )


class TestComputeGlobalId:
    def test_same_inputs_yield_same_id(self):
        a = _compute_global_id("长期不确定性", "通常 cause 风险规避")
        b = _compute_global_id("长期不确定性", "通常 cause 风险规避")
        assert a == b

    def test_different_name_yields_different_id(self):
        a = _compute_global_id("长期不确定性", "通常 cause 风险规避")
        b = _compute_global_id("身份焦虑", "通常 cause 风险规避")
        assert a != b

    def test_different_mech_yields_different_id(self):
        a = _compute_global_id("长期不确定性", "通常 cause 风险规避")
        b = _compute_global_id("长期不确定性", "通常 cause 储蓄上升")
        assert a != b

    def test_id_format_is_v_plus_8_hex(self):
        gid = _compute_global_id("x", "y")
        assert gid.startswith("v_")
        assert len(gid) == 10  # "v_" + 8 hex char
        hex_part = gid[2:]
        assert all(c in "0123456789abcdef" for c in hex_part)


class TestLoadLexicon:
    def test_load_missing_file_returns_empty_schema(self, tmp_path):
        path = tmp_path / "knowledge" / "variables.json"
        # path 不存在, 父目录也不存在
        lexicon = _load_lexicon(path)
        assert lexicon["version"] == 1
        assert lexicon["variables"] == []
        assert "updated_at" in lexicon

    def test_load_valid_file_returns_parsed(self, tmp_path):
        path = tmp_path / "variables.json"
        path.write_text(json.dumps({
            "version": 1,
            "updated_at": "2026-05-18T00:00:00Z",
            "variables": [{"global_id": "v_abc12345", "name": "x"}],
        }))
        lexicon = _load_lexicon(path)
        assert lexicon["version"] == 1
        assert len(lexicon["variables"]) == 1
        assert lexicon["variables"][0]["name"] == "x"

    def test_load_corrupt_json_raises_with_path(self, tmp_path):
        path = tmp_path / "variables.json"
        path.write_text("{ not valid json")
        with pytest.raises(json.JSONDecodeError):
            _load_lexicon(path)

    def test_load_partial_json_setdefaults_missing_keys(self, tmp_path):
        """Wave 2 M1 fix: 用户手编 {} 时 _load_lexicon 不该后续 KeyError."""
        path = tmp_path / "variables.json"
        path.write_text("{}")
        lexicon = _load_lexicon(path)
        assert lexicon["version"] == 1
        assert lexicon["variables"] == []
        assert "updated_at" in lexicon


class TestSaveLexicon:
    def test_save_creates_parent_dir(self, tmp_path):
        path = tmp_path / "knowledge" / "variables.json"
        _save_lexicon(path, {"version": 1, "variables": []})
        assert path.exists()
        assert path.parent.exists()

    def test_save_writes_valid_json(self, tmp_path):
        path = tmp_path / "variables.json"
        lexicon = {
            "version": 1,
            "updated_at": "2026-05-18T00:00:00Z",
            "variables": [{"global_id": "v_x", "name": "测试"}],
        }
        _save_lexicon(path, lexicon)
        loaded = json.loads(path.read_text())
        assert loaded == lexicon

    def test_save_atomic_no_tmp_left(self, tmp_path):
        path = tmp_path / "variables.json"
        _save_lexicon(path, {"version": 1, "variables": []})
        # .tmp 应已 rename 走
        assert not (tmp_path / "variables.json.tmp").exists()


class TestShouldPromote:
    def test_l0_rejected(self):
        node = _make_node(abstraction_level=0)
        assert not _should_promote(node)

    def test_decayed_rejected(self):
        node = _make_node(lifecycle_state="decayed")
        assert not _should_promote(node)

    def test_stale_rejected(self):
        node = _make_node(lifecycle_state="stale")
        assert not _should_promote(node)

    def test_low_activation_rejected(self):
        node = _make_node(activation=0.3)
        assert not _should_promote(node)

    def test_l1_active_high_activation_accepted(self):
        node = _make_node(abstraction_level=1, activation=0.7, lifecycle_state="active")
        assert _should_promote(node)

    def test_l2_active_high_activation_accepted(self):
        node = _make_node(abstraction_level=2, activation=0.9, lifecycle_state="active")
        assert _should_promote(node)


class TestUpsertVar:
    def _empty_lexicon(self):
        return {"version": 1, "updated_at": _now_iso(), "variables": []}

    def test_new_var_added_with_reuse_count_1(self):
        lex = self._empty_lexicon()
        node = _make_node()
        _upsert_var(lex, node, "通常 cause 风险规避", "s_001")
        assert len(lex["variables"]) == 1
        v = lex["variables"][0]
        assert v["name"] == "长期不确定性"
        assert v["fitness"]["reuse_count"] == 1
        assert v["source_sessions"] == ["s_001"]

    def test_existing_var_new_sid_increments_count(self):
        lex = self._empty_lexicon()
        node = _make_node()
        _upsert_var(lex, node, "mech", "s_001")
        _upsert_var(lex, node, "mech", "s_002")
        assert len(lex["variables"]) == 1
        v = lex["variables"][0]
        assert v["fitness"]["reuse_count"] == 2
        assert set(v["source_sessions"]) == {"s_001", "s_002"}

    def test_existing_var_same_sid_idempotent(self):
        """同 session 多次 flush 不 ++ count."""
        lex = self._empty_lexicon()
        node = _make_node()
        _upsert_var(lex, node, "mech", "s_001")
        _upsert_var(lex, node, "mech", "s_001")
        _upsert_var(lex, node, "mech", "s_001")
        assert len(lex["variables"]) == 1
        v = lex["variables"][0]
        assert v["fitness"]["reuse_count"] == 1
        assert v["source_sessions"] == ["s_001"]

    def test_different_name_creates_separate_entries(self):
        lex = self._empty_lexicon()
        _upsert_var(lex, _make_node(name="A"), "mech", "s_001")
        _upsert_var(lex, _make_node(name="B"), "mech", "s_001")
        assert len(lex["variables"]) == 2


class TestNowIso:
    def test_format_iso8601(self):
        ts = _now_iso()
        # 形如 "2026-05-18T15:30:00Z" 或 "2026-05-18T15:30:00.123456Z"
        assert "T" in ts
        assert ts.endswith("Z") or ts[-6] in "+-"


# ── Wave 2: build_canonical_mechanism + flush_to_lexicon ──────────────────────

from unittest.mock import AsyncMock  # noqa: E402

from explain_engine.engines.lexicon import (  # noqa: E402
    _build_canonical_mechanism,
    flush_to_lexicon,
)
from explain_engine.persistence.session import Session, SessionMeta  # noqa: E402
from explain_engine.persistence.storage_v2 import StorageV2  # noqa: E402
from explain_engine.schema.edges import RelationEdge  # noqa: E402
from explain_engine.schema.graph import ExplanationGraph  # noqa: E402
from explain_engine.schema.state import CognitiveState  # noqa: E402


def _make_state_with_l2(
    name: str = "长期不确定性",
    add_edges: bool = True,
) -> CognitiveState:
    """单 L2 (active high activation) + 1 L1 incoming + 1 L0 outgoing manifest."""
    g = ExplanationGraph(root_question="why?")
    g.add_node(VariableNode(
        id="d_001", name=name, description="root driver",
        abstraction_level=2, confidence=0.8, epistemic="insight",
        activation=0.9, lifecycle_state="active",
    ))
    g.add_node(VariableNode(
        id="c_001", name="风险规避", description="mid",
        abstraction_level=1, confidence=0.7, epistemic="inference",
        activation=0.7, lifecycle_state="active",
    ))
    g.add_node(VariableNode(
        id="p_001", name="储蓄率上升", description="L0 obs",
        abstraction_level=0, confidence=0.7, epistemic="observation",
    ))
    if add_edges:
        g.add_edge(RelationEdge(
            id="e_001", source_node="d_001", target_node="c_001",
            relation_type="causes", confidence=0.7,
            mechanism_description="不确定性 → 风险规避",
        ))
        g.add_edge(RelationEdge(
            id="e_002", source_node="c_001", target_node="p_001",
            relation_type="manifests_as", confidence=0.7,
            mechanism_description="...",
        ))
    return CognitiveState(graph=g, budget_remaining=10, root_question="why?")


class TestBuildCanonicalMechanism:
    @pytest.mark.asyncio
    async def test_with_llm_returns_llm_output(self):
        state = _make_state_with_l2()
        node = state.graph.nodes["d_001"]
        session = Session(meta=SessionMeta.new(question="why?"), state=state)

        mock_llm = AsyncMock()
        from explain_engine.llm.client import Response
        mock_llm.chat = AsyncMock(return_value=Response(
            text="通常 cause 风险规避; 由社会结构性压力 cause",
            parsed=None,
            model="mock",
            usage={"input_tokens": 0, "output_tokens": 0},
        ))

        mech = await _build_canonical_mechanism(node, session, mock_llm)
        # Wave 2 review I-2: 严格断言 LLM 真被调 + LLM-only string 在返
        # 值里, 区分 "LLM 被调用" vs "LLM 失败掉 fallback".
        mock_llm.chat.assert_called_once()
        assert "社会结构性压力" in mech  # 只有 LLM 输出含此 string, fallback 不会有

    @pytest.mark.asyncio
    async def test_no_llm_uses_edge_fallback(self):
        state = _make_state_with_l2()
        node = state.graph.nodes["d_001"]
        session = Session(meta=SessionMeta.new(question="why?"), state=state)

        mech = await _build_canonical_mechanism(node, session, None)
        # Fallback 应含 incoming/outgoing 边的目标 name
        # d_001 outgoing causes 到 c_001 (风险规避), 无 incoming
        assert "风险规避" in mech

    @pytest.mark.asyncio
    async def test_llm_error_falls_back(self):
        from explain_engine.llm.errors import LLMError
        state = _make_state_with_l2()
        node = state.graph.nodes["d_001"]
        session = Session(meta=SessionMeta.new(question="why?"), state=state)

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(side_effect=LLMError("api down"))

        mech = await _build_canonical_mechanism(node, session, mock_llm)
        # Fallback 路径
        assert "风险规避" in mech


class TestFlushToLexicon:
    @pytest.mark.asyncio
    async def test_promotes_l1_l2_only(self):
        state = _make_state_with_l2()
        meta = SessionMeta.new(question="why?")
        meta.session_id = "s_flush001"
        session = Session(meta=meta, state=state)

        promoted = await flush_to_lexicon(
            session, StorageV2(), llm=None,
        )
        # d_001 (L2 active 0.9) + c_001 (L1 active 0.7) → 2
        assert promoted == 2

        # 验 lexicon 内 L0 没进
        path = StorageV2().knowledge_dir() / "variables.json"
        lex = _load_lexicon(path)
        names = {v["name"] for v in lex["variables"]}
        assert names == {"长期不确定性", "风险规避"}

    @pytest.mark.asyncio
    async def test_skips_decayed(self):
        state = _make_state_with_l2()
        state.graph.nodes["d_001"].lifecycle_state = "decayed"
        meta = SessionMeta.new(question="why?")
        meta.session_id = "s_flush002"
        session = Session(meta=meta, state=state)

        promoted = await flush_to_lexicon(session, StorageV2(), llm=None)
        assert promoted == 1  # 仅 c_001

    @pytest.mark.asyncio
    async def test_idempotent_same_sid(self):
        state = _make_state_with_l2()
        meta = SessionMeta.new(question="why?")
        meta.session_id = "s_flush003"
        session = Session(meta=meta, state=state)

        await flush_to_lexicon(session, StorageV2(), llm=None)
        await flush_to_lexicon(session, StorageV2(), llm=None)
        await flush_to_lexicon(session, StorageV2(), llm=None)

        path = StorageV2().knowledge_dir() / "variables.json"
        lex = _load_lexicon(path)
        # 同 sid 3 次 flush, reuse_count 仍 1
        for v in lex["variables"]:
            assert v["fitness"]["reuse_count"] == 1
            assert v["source_sessions"] == ["s_flush003"]
