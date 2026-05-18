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
