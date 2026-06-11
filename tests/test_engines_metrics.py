"""压缩值 CV 与收敛判据测试 (docs/设计预期-修正版.md §五.1 / §五.4)。"""

import pytest

from explain_engine.engines.metrics import (
    CV_EPSILON,
    compression_value,
    cv_converged,
)
from explain_engine.runtime.runtime import run
from explain_engine.runtime.scheduler import PhaseScheduler
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _node(nid, name, level, **kw):
    return VariableNode(
        id=nid, name=name, description="d", abstraction_level=level,
        confidence=0.8, epistemic="observation", **kw,
    )


def _edge(eid, src, tgt, rtype, conf):
    return RelationEdge(
        id=eid, source_node=src, target_node=tgt, relation_type=rtype,
        confidence=conf, mechanism_description="m",
    )


def _state() -> CognitiveState:
    """d_001 →causes(0.85)→ c_001 →manifests_as→ p_001(0.9), p_002(0.8); p_003 孤儿."""
    state = CognitiveState.bootstrap("q", budget=5)
    g = state.graph
    g.add_node(_node("p_001", "p1", 0))
    g.add_node(_node("p_002", "p2", 0))
    g.add_node(_node("p_003", "p3", 0))
    g.add_node(_node("c_001", "c1", 1))
    g.add_node(_node("d_001", "d1", 2))
    g.add_edge(_edge("e_001", "c_001", "p_001", "manifests_as", 0.9))
    g.add_edge(_edge("e_002", "c_001", "p_002", "manifests_as", 0.8))
    g.add_edge(_edge("e_003", "d_001", "c_001", "causes", 0.85))
    return state


# ─── compression_value (§五.1) ─────────────────────────────


def test_cv_known_graph():
    r = compression_value(_state())
    # 从 d_001 传播: c_001=0.85; p_001=0.765; p_002=0.68; p_003=0 (孤儿)
    assert r.coverage == pytest.approx(0.765 + 0.68)
    # L = |L1|+|L2| + 0.5·|E| = 2 + 1.5
    assert r.length == pytest.approx(3.5)
    assert r.cv == pytest.approx(r.coverage / 3.5)


def test_cv_empty_structure_is_zero():
    state = CognitiveState.bootstrap("q", budget=5)
    state.graph.add_node(_node("p_001", "p1", 0))
    r = compression_value(state)
    assert r.cv == 0.0
    assert r.length == 0.0


def test_cv_excludes_decayed():
    state = _state()
    g = state.graph
    g.replace_node("d_001", g.nodes["d_001"].model_copy(
        update={"lifecycle_state": "decayed"}))
    r = compression_value(state)
    # roots 退化为 L1 (c_001); d_001 与 e_003 不计入 length
    assert r.length == pytest.approx(1 + 0.5 * 2)
    assert r.coverage == pytest.approx(0.9 + 0.8)


def test_cv_reflects_grounded_confidence():
    """接地后 confidence 被天花板压低 → CV 下降 (证据质量进指标)。"""
    from explain_engine.engines.grounding import apply_grounded_confidence

    state = _state()
    before = compression_value(state).cv
    apply_grounded_confidence(state)  # 全 hypothesis → ×0.4
    after = compression_value(state).cv
    assert after < before


# ─── cv_converged (§五.4) ──────────────────────────────────


def test_cv_converged_needs_window_plus_one():
    assert not cv_converged([0.5, 0.5, 0.5])          # 只有 3 个观测
    assert cv_converged([0.5, 0.5, 0.5, 0.5])         # 3 个零增量


def test_cv_converged_rejects_active_growth():
    assert not cv_converged([0.1, 0.2, 0.3, 0.4])


def test_cv_converged_ignores_old_history():
    # 早期大增量不影响, 只看最近 W 个
    assert cv_converged([0.0, 0.4, 0.41, 0.41, 0.41])
    eps = CV_EPSILON
    assert not cv_converged([0.4, 0.41, 0.41, 0.41 + 2 * eps])


# ─── runtime 集成: cv_converged 停止 ───────────────────────


@pytest.mark.asyncio
async def test_runtime_stops_with_cv_converged(mocker) -> None:
    """图不再变化 (reflect 全 continue) → 3 tick 后 cv_converged 停止。"""
    state = _state()
    mocker.patch(
        "explain_engine.engines.reflection.reflect",
        return_value=("continue", None),
    )
    mocker.patch(
        "explain_engine.runtime.runtime.aggregate_acceptance",
        return_value=mocker.MagicMock(),
    )
    # lifecycle 会读 MagicMock acceptance 的字段算 fitness → 直接 no-op
    mocker.patch(
        "explain_engine.runtime.runtime.lifecycle_mod.update_lifecycle",
    )
    # c_001 已被 causes 覆盖 → frontier 空, 所有 tick 落入 reflect/continue
    reason = await run(
        state, mocker.AsyncMock(), budget=10, scheduler=PhaseScheduler(K=2),
    )
    assert reason == "cv_converged"
    assert state.tick == 3  # W=3 个零增量后立即停, 不烧满 budget
