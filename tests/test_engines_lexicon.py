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

    @pytest.mark.asyncio
    async def test_top_k_llm_canonical_caps_llm_calls(self, monkeypatch):
        """Mitigation #2 (2026-05-19): flush_to_lexicon 按 activation desc
        sort promoted vars, top-K 真 LLM 生 canonical, 其余 fallback (llm=None).

        减少 long compress 时长 — typical 5+ promoted var 时省 (N-K) LLM call.
        """
        from explain_engine.engines import lexicon as lex_module

        # 建 graph 含 5 个 L1 不同 activation (高到低)
        g = ExplanationGraph(root_question="why?")
        for i, act in enumerate([0.95, 0.85, 0.75, 0.65, 0.55]):
            g.add_node(VariableNode(
                id=f"c_00{i}", name=f"v{i}", description=f"d{i}",
                abstraction_level=1, confidence=0.7, epistemic="insight",
                activation=act, lifecycle_state="active",
            ))
        state = CognitiveState(
            graph=g, budget_remaining=10, root_question="why?",
        )
        meta = SessionMeta.new(question="why?")
        meta.session_id = "s_topk001"
        session = Session(meta=meta, state=state)

        # Capture _build_canonical_mechanism's llm arg per call
        # (不 forward real, 因 real 需真 LLM; spy 仅捕 + 返 stub string)
        captured_llms = []

        async def spy_build(node, sess, llm):
            captured_llms.append((node.id, llm))
            return f"stub mech for {node.id}"

        monkeypatch.setattr(lex_module, "_build_canonical_mechanism", spy_build)

        sentinel_llm = object()  # type: ignore[arg-type]
        promoted = await flush_to_lexicon(
            session, StorageV2(),
            llm=sentinel_llm,  # type: ignore[arg-type]
            llm_canonical_top_k=3,
        )

        assert promoted == 5
        # 验前 3 (highest activation) 真 LLM (llm=sentinel), 后 2 fallback (None)
        # captured 按 dict iter order — Python 3.7+ insertion order, 应等同 add 顺序
        assert len(captured_llms) == 5
        # 排 by activation desc 后 top 3 (c_000/c_001/c_002, activation 0.95/0.85/0.75)
        top_3_ids = {nid for nid, _ in captured_llms[:3]}
        bottom_2_ids = {nid for nid, _ in captured_llms[3:]}
        assert top_3_ids == {"c_000", "c_001", "c_002"}
        assert bottom_2_ids == {"c_003", "c_004"}
        # Top-3 收 sentinel_llm, bottom-2 收 None
        for _nid, llm_arg in captured_llms[:3]:
            assert llm_arg is sentinel_llm
        for _nid, llm_arg in captured_llms[3:]:
            assert llm_arg is None

    @pytest.mark.asyncio
    async def test_default_top_k_3(self, monkeypatch):
        """Mitigation #2: default top_k=3 (不显式传时)."""
        from explain_engine.engines import lexicon as lex_module

        g = ExplanationGraph(root_question="why?")
        for i, act in enumerate([0.95, 0.85, 0.75, 0.65, 0.55]):
            g.add_node(VariableNode(
                id=f"c_01{i}", name=f"v{i}", description=f"d{i}",
                abstraction_level=1, confidence=0.7, epistemic="insight",
                activation=act, lifecycle_state="active",
            ))
        state = CognitiveState(
            graph=g, budget_remaining=10, root_question="why?",
        )
        meta = SessionMeta.new(question="why?")
        meta.session_id = "s_topk002"
        session = Session(meta=meta, state=state)

        captured = []

        async def spy(node, sess, llm):
            captured.append((node.id, llm))
            return f"stub for {node.id}"

        monkeypatch.setattr(lex_module, "_build_canonical_mechanism", spy)

        sentinel = object()
        await flush_to_lexicon(
            session, StorageV2(), llm=sentinel,  # type: ignore[arg-type]
        )  # 不传 llm_canonical_top_k, 默认 3

        # 5 captured, 前 3 (sentinel), 后 2 (None)
        assert len(captured) == 5
        sentinel_count = sum(1 for _, llm in captured if llm is sentinel)
        none_count = sum(1 for _, llm in captured if llm is None)
        assert sentinel_count == 3, f"default top_k=3, 但 sentinel count={sentinel_count}"
        assert none_count == 2


# ── Wave 3: _select_top_k_vars + _render_lexicon_for_prompt ──────────────────

from explain_engine.engines.lexicon import (  # noqa: E402
    _render_lexicon_for_prompt,
    _select_top_k_vars,
)


def _make_var_entry(
    global_id: str = "v_abc12345",
    name: str = "长期不确定性",
    description: str = "宏观层面持续低预期",
    abstraction_level: int = 2,
    reuse_count: int = 3,
    avg_essentialness: float = 0.8,
    canonical_mechanism: str = "通常 cause 风险规避; 由社会结构性压力 cause",
) -> dict:
    """Helper: 建 lexicon var entry (dict form, 同 knowledge/variables.json 行存)."""
    return {
        "global_id": global_id,
        "name": name,
        "description": description,
        "abstraction_level": abstraction_level,
        "epistemic": "insight",
        "fitness": {
            "reuse_count": reuse_count,
            "avg_essentialness": avg_essentialness,
            "avg_consistency": 0.7,
            "first_seen_at": "2026-05-01T00:00:00Z",
            "last_seen_at": "2026-05-18T00:00:00Z",
        },
        "canonical_mechanism": canonical_mechanism,
        "source_sessions": ["s_001", "s_002", "s_003"][:reuse_count],
    }


class TestSelectTopK:
    def test_empty_lexicon_returns_empty(self):
        assert _select_top_k_vars({"variables": []}, k=20) == []

    def test_k_zero_returns_empty(self):
        lex = {"variables": [_make_var_entry()]}
        assert _select_top_k_vars(lex, k=0) == []

    def test_k_larger_than_total_returns_all(self):
        lex = {"variables": [
            _make_var_entry(global_id="v_a", name="A"),
            _make_var_entry(global_id="v_b", name="B"),
        ]}
        result = _select_top_k_vars(lex, k=20)
        assert len(result) == 2

    def test_composite_score_descending(self):
        """composite = reuse × (essentialness + 0.1), sort desc."""
        lex = {"variables": [
            # score = 1 * (0.5 + 0.1) = 0.6
            _make_var_entry(
                global_id="v_low", name="Low",
                reuse_count=1, avg_essentialness=0.5,
            ),
            # score = 5 * (0.9 + 0.1) = 5.0
            _make_var_entry(
                global_id="v_high", name="High",
                reuse_count=5, avg_essentialness=0.9,
            ),
            # score = 3 * (0.6 + 0.1) = 2.1
            _make_var_entry(
                global_id="v_mid", name="Mid",
                reuse_count=3, avg_essentialness=0.6,
            ),
        ]}
        result = _select_top_k_vars(lex, k=20)
        names = [v["name"] for v in result]
        assert names == ["High", "Mid", "Low"]

    def test_zero_essentialness_not_completely_zeroed(self):
        """essentialness=0 + reuse=5 仍应排在 essentialness=0.1 + reuse=1 前 (+0.1 防完全 0)."""
        lex = {"variables": [
            # score = 5 * (0 + 0.1) = 0.5
            _make_var_entry(
                global_id="v_zero", name="Zero",
                reuse_count=5, avg_essentialness=0.0,
            ),
            # score = 1 * (0.1 + 0.1) = 0.2
            _make_var_entry(
                global_id="v_low", name="Low",
                reuse_count=1, avg_essentialness=0.1,
            ),
        ]}
        result = _select_top_k_vars(lex, k=20)
        assert result[0]["name"] == "Zero"


class TestRenderLexicon:
    def test_render_empty_returns_empty_string(self):
        assert _render_lexicon_for_prompt([]) == ""

    def test_single_var_contains_essentials(self):
        var = _make_var_entry(
            global_id="v_abc12345",
            name="长期不确定性",
            abstraction_level=2,
            reuse_count=3,
        )
        out = _render_lexicon_for_prompt([var])
        assert "v_abc12345" in out
        assert "长期不确定性" in out
        assert "L2" in out
        assert "3" in out  # reuse count

    def test_long_desc_capped(self):
        long_desc = "啊" * 200
        var = _make_var_entry(description=long_desc)
        out = _render_lexicon_for_prompt([var])
        # 80 char cap → 单行 "啊" 数 <= 80
        # 整个 prompt 不该 contain 200 个连续 "啊"
        assert "啊" * 200 not in out
        # 确认有 cap, e.g. 含 81 个连续 "啊" 也不行
        assert "啊" * 81 not in out

    def test_long_mech_capped(self):
        long_mech = "通常 cause " + "x" * 200
        var = _make_var_entry(canonical_mechanism=long_mech)
        out = _render_lexicon_for_prompt([var])
        # mech cap 60 char
        assert "x" * 200 not in out
        assert "x" * 61 not in out

    def test_chinese_no_garbled(self):
        var = _make_var_entry(
            name="社会结构性压力",
            description="宏观维度的持续高位风险",
            canonical_mechanism="通常 cause 长期不确定性; 由历史路径 cause",
        )
        out = _render_lexicon_for_prompt([var])
        assert "社会结构性压力" in out
        assert "宏观" in out
        assert "通常 cause" in out

    def test_includes_disclaimer(self):
        var = _make_var_entry()
        out = _render_lexicon_for_prompt([var])
        # disclaimer 含 "不强制" 或 "仅供参考" — let LLM 知道 lexicon 是 hint 不是 rule
        assert "不强制" in out or "仅供参考" in out
