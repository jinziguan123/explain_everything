"""Phase T 预测台账测试 (docs/设计预期-修正版.md §七)。

storage 用 stub (knowledge_dir → tmp_path), 不触 PG / 不触网 / 不加载 BGE。
"""

import typing

import numpy as np
import pytest

from explain_engine.engines.theory.ledger import (
    Prediction,
    add_prediction,
    apply_ledger_overlay,
    decide_stability,
    due_predictions,
    load_ledger,
    resolve_prediction,
    settle_retrodictions,
    stats_for,
)
from explain_engine.engines.theory.ranking import competition_rank, overlap_ratio
from explain_engine.engines.theory.theory import Theory


class StubStorage:
    def __init__(self, p):
        self._p = p

    def knowledge_dir(self):
        return self._p


def _theory(tid="t_aaaaaaaaaa", nodes=("v_a", "v_b"), pp=0.5, sessions=("s_1", "s_2")):
    return Theory(
        id=tid, motif_type="chain", theme_ids=(), node_ids=tuple(nodes),
        edges=((nodes[0], nodes[1], "causes"),), supporting_sessions=tuple(sessions),
        natural_language_summary="x", structure_complexity=len(nodes),
        first_seen_session=sessions[0], last_seen_session=sessions[-1],
        predictive_power=pp,
    )


# ─── 登记 / 结算 / 持久化 ─────────────────────────────────


def test_add_and_load_roundtrip(tmp_path):
    storage = StubStorage(tmp_path)
    p1 = add_prediction(storage, "t_x", "断言一", method="search")
    p2 = add_prediction(storage, "t_x", "断言二", method="time_window",
                        deadline="2026-07-01")
    assert (p1.id, p2.id) == ("p_001", "p_002")
    loaded = load_ledger(storage)
    assert [p.assertion for p in loaded] == ["断言一", "断言二"]
    assert loaded[1].deadline == "2026-07-01"


def test_time_window_requires_deadline(tmp_path):
    with pytest.raises(ValueError, match="deadline"):
        add_prediction(StubStorage(tmp_path), "t_x", "a", method="time_window")


def test_bad_deadline_format_rejected(tmp_path):
    with pytest.raises(ValueError):
        add_prediction(StubStorage(tmp_path), "t_x", "a",
                       method="search", deadline="07/01/2026")


def test_resolve_hit_and_immutability(tmp_path):
    storage = StubStorage(tmp_path)
    p = add_prediction(storage, "t_x", "a")
    resolved = resolve_prediction(storage, p.id, hit=True, note="来源链接")
    assert resolved.status == "hit"
    assert resolved.resolved_at is not None
    # 台账不可篡改: 重复结算拒绝
    with pytest.raises(ValueError, match="不可篡改"):
        resolve_prediction(storage, p.id, hit=False)


def test_resolve_missing_id(tmp_path):
    with pytest.raises(KeyError):
        resolve_prediction(StubStorage(tmp_path), "p_999", hit=True)


# ─── 统计 / 连败 / 削弱 ────────────────────────────────────


def _resolve_seq(storage, tid, outcomes):
    """登记 + 按序结算一串预测, 返回 stats。"""
    for i, hit in enumerate(outcomes):
        p = add_prediction(storage, tid, f"断言{i}")
        resolve_prediction(storage, p.id, hit=hit)
    return stats_for(load_ledger(storage), tid)


def test_stats_predictive_power(tmp_path):
    st = _resolve_seq(StubStorage(tmp_path), "t_x", [True, True, False])
    assert st.hits == 2 and st.misses == 1
    assert st.predictive_power == pytest.approx(2 / 3)
    assert st.consecutive_misses == 1
    assert not st.weakened


def test_stats_weakened_after_two_consecutive_misses(tmp_path):
    st = _resolve_seq(StubStorage(tmp_path), "t_x", [True, False, False])
    assert st.consecutive_misses == 2
    assert st.weakened


def test_stats_hit_resets_streak(tmp_path):
    st = _resolve_seq(StubStorage(tmp_path), "t_x", [False, False, True])
    assert st.consecutive_misses == 0
    assert not st.weakened


def test_stats_no_resolved_pp_none(tmp_path):
    storage = StubStorage(tmp_path)
    add_prediction(storage, "t_x", "a")
    st = stats_for(load_ledger(storage), "t_x")
    assert st.predictive_power is None
    assert st.pending == 1


def test_due_predictions(tmp_path):
    storage = StubStorage(tmp_path)
    add_prediction(storage, "t_x", "过期", method="time_window", deadline="2026-01-01")
    add_prediction(storage, "t_x", "未到", method="time_window", deadline="2099-01-01")
    add_prediction(storage, "t_x", "无期限")
    due = due_predictions(load_ledger(storage), today="2026-06-12")
    assert [p.assertion for p in due] == ["过期"]


# ─── overlay / 状态决策 (§七.1/.2) ─────────────────────────


def test_overlay_ledger_overrides_retrodiction(tmp_path):
    storage = StubStorage(tmp_path)
    st = _resolve_seq(storage, "t_aaaaaaaaaa", [True, False])
    out = apply_ledger_overlay([_theory(pp=0.9)], {"t_aaaaaaaaaa": st})
    assert out[0].predictive_power == pytest.approx(0.5)
    assert out[0].predictive_power_source == "ledger"


def test_overlay_keeps_retro_when_no_resolved(tmp_path):
    storage = StubStorage(tmp_path)
    add_prediction(storage, "t_aaaaaaaaaa", "a")  # pending only
    st = stats_for(load_ledger(storage), "t_aaaaaaaaaa")
    out = apply_ledger_overlay([_theory(pp=0.9)], {"t_aaaaaaaaaa": st})
    assert out[0].predictive_power == pytest.approx(0.9)
    assert out[0].predictive_power_source == "retrodiction"


def test_decide_stability_rules(tmp_path):
    storage = StubStorage(tmp_path)
    # 1. 无预测 → 永远 tentative (叙事级), 窗口满足也不晋升
    assert decide_stability(None, window_promoted=True) == "tentative"
    # 2. 有预测 + 窗口满足 → stable
    st_ok = _resolve_seq(storage, "t_a", [True])
    assert decide_stability(st_ok, window_promoted=True) == "stable"
    assert decide_stability(st_ok, window_promoted=False) == "tentative"
    # 3. 连败 ≥2 → weakened, 即使窗口满足
    st_weak = _resolve_seq(storage, "t_b", [False, False])
    assert decide_stability(st_weak, window_promoted=True) == "weakened"


# ─── 竞争排序 (§七.3) ──────────────────────────────────────


def test_overlap_ratio():
    t1 = _theory(nodes=("v_a", "v_b"))
    t2 = _theory(tid="t_bbbbbbbbbb", nodes=("v_a", "v_b", "v_c"))
    assert overlap_ratio(t1, t2) == pytest.approx(2 / 3)


def test_competition_rank_lexicographic():
    # pp 高者居前; pp 相同比综合分/复现数
    weak = _theory(tid="t_1111111111", pp=0.2, sessions=("s_1", "s_2", "s_3"))
    strong = _theory(tid="t_2222222222", pp=0.8, sessions=("s_1",))
    ranked = competition_rank([weak, strong], n_sessions_total=3)
    assert [t.id for t in ranked] == ["t_2222222222", "t_1111111111"]


# ─── retrodiction 自动结算 ─────────────────────────────────


class _StubEmbedder:
    """断言"年轻人储蓄上升"与同名 L0 同向量; 其余文本互相正交。"""

    _vocab: typing.ClassVar[dict[str, list[float]]] = {
        "年轻人储蓄上升": [1.0, 0.0, 0.0],
        "完全无关现象": [0.0, 1.0, 0.0],
    }

    def encode(self, texts):
        # 未知文本落到第三个正交维度 — 与语料任何向量余弦为 0
        return np.array([self._vocab.get(t, [0.0, 0.0, 1.0]) for t in texts])


class _StubNode:
    def __init__(self, name):
        self.name = name
        self.abstraction_level = 0


class _StubGraph:
    def __init__(self, names):
        self.nodes = {f"p_{i}": _StubNode(n) for i, n in enumerate(names)}


def test_settle_retrodictions(tmp_path, mocker):
    storage = StubStorage(tmp_path)
    theory = _theory(tid="t_aaaaaaaaaa", sessions=("s_1",))
    add_prediction(storage, theory.id, "年轻人储蓄上升", method="retrodiction")
    add_prediction(storage, theory.id, "完全无关断言", method="retrodiction")

    cache = mocker.MagicMock(
        stable_theories=[theory], tentative_theories=[],
    )
    mocker.patch(
        "explain_engine.engines.theory.cache.get_active_theories",
        return_value=cache,
    )
    mocker.patch(
        "explain_engine.engines.theory.loader.load_all_session_graphs",
        return_value={"s_2": _StubGraph(["年轻人储蓄上升", "完全无关现象"])},
    )
    store = mocker.MagicMock()
    store.list.return_value = [
        mocker.MagicMock(session_id="s_1"), mocker.MagicMock(session_id="s_2"),
    ]
    mocker.patch(
        "explain_engine.persistence.session.SessionStore", return_value=store,
    )

    settled = settle_retrodictions(storage, _StubEmbedder())
    assert len(settled) == 2
    outcomes = {p.id: hit for p, hit in settled}
    assert outcomes == {"p_001": True, "p_002": False}
    # 台账已落盘
    statuses = {p.id: p.status for p in load_ledger(storage)}
    assert statuses == {"p_001": "hit", "p_002": "miss"}


def test_settle_retrodictions_skips_without_heldout(tmp_path, mocker):
    storage = StubStorage(tmp_path)
    theory = _theory(tid="t_aaaaaaaaaa", sessions=("s_1",))
    add_prediction(storage, theory.id, "断言", method="retrodiction")

    cache = mocker.MagicMock(stable_theories=[theory], tentative_theories=[])
    mocker.patch(
        "explain_engine.engines.theory.cache.get_active_theories",
        return_value=cache,
    )
    store = mocker.MagicMock()
    store.list.return_value = [mocker.MagicMock(session_id="s_1")]  # 无 held-out
    mocker.patch(
        "explain_engine.persistence.session.SessionStore", return_value=store,
    )

    assert settle_retrodictions(storage, _StubEmbedder()) == []
    assert load_ledger(storage)[0].status == "pending"  # 维持待结算


# ─── 序列化兼容 ────────────────────────────────────────────


def test_theory_cache_serialization_with_new_fields():
    from explain_engine.engines.theory.cache import _theory_from_dict, _theory_to_dict

    t = _theory()
    t2 = _theory_from_dict(_theory_to_dict(t))
    assert t2 == t
    # 旧格式 (无新字段) 兼容默认值
    legacy = _theory_to_dict(t)
    legacy.pop("predictive_power_source")
    t3 = _theory_from_dict(legacy)
    assert t3.predictive_power_source == "retrodiction"


def test_prediction_model_roundtrip():
    p = Prediction(id="p_001", theory_id="t_x", assertion="a",
                   created_at="2026-06-12T00:00:00+00:00")
    assert Prediction.model_validate(p.model_dump()) == p
