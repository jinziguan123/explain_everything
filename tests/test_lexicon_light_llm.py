"""lexicon JSON backend light_llm 注入 — Phase 17.2 Task 7.

验 _build_canonical_mechanism 接受 optional light_llm 参数 + 通过
with_light_fallback 走 cheap model. light=None 时跟现行为 100% 一致
(零回归).

Phase 17.2 final review fix: 加 e2e test 验 flush_to_lexicon →
_build_canonical_mechanism 端到端透传 light_llm (避免之前 dead code 风险).
"""

from unittest.mock import AsyncMock

import pytest

from explain_engine.engines.lexicon import _build_canonical_mechanism
from explain_engine.llm.client import Response
from explain_engine.llm.errors import LLMError
from explain_engine.persistence.session import Session, SessionMeta
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.graph import ExplanationGraph
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _make_session() -> Session:
    g = ExplanationGraph(root_question="why?")
    g.add_node(VariableNode(
        id="d_001", name="长期不确定性", description="root driver",
        abstraction_level=2, confidence=0.8, epistemic="insight",
        activation=0.9, lifecycle_state="active",
    ))
    g.add_node(VariableNode(
        id="c_001", name="风险规避", description="mid",
        abstraction_level=1, confidence=0.7, epistemic="inference",
        activation=0.7, lifecycle_state="active",
    ))
    g.add_edge(RelationEdge(
        id="e_001", source_node="d_001", target_node="c_001",
        relation_type="causes", confidence=0.7,
        mechanism_description="不确定性 → 风险规避",
    ))
    state = CognitiveState(graph=g, budget_remaining=10, root_question="why?")
    return Session(meta=SessionMeta.new(question="why?"), state=state)


def _make_response(text: str) -> Response:
    return Response(
        text=text,
        parsed=None,
        model="mock",
        usage={"input_tokens": 0, "output_tokens": 0},
    )


@pytest.mark.asyncio
async def test_build_canonical_uses_light_llm_when_provided():
    """light_llm 提供 → light.chat 被调, main 不被调."""
    session = _make_session()
    node = session.state.graph.nodes["d_001"]

    light = AsyncMock()
    light.chat = AsyncMock(return_value=_make_response("LIGHT-CANONICAL"))
    main = AsyncMock()
    main.chat = AsyncMock(return_value=_make_response("MAIN-CANONICAL"))

    mech = await _build_canonical_mechanism(node, session, main, light_llm=light)

    light.chat.assert_called_once()
    main.chat.assert_not_called()
    assert "LIGHT-CANONICAL" in mech


@pytest.mark.asyncio
async def test_build_canonical_light_fallback_to_main_on_error():
    """light raise LLMError → main 接管."""
    session = _make_session()
    node = session.state.graph.nodes["d_001"]

    light = AsyncMock()
    light.chat = AsyncMock(side_effect=LLMError("net down"))
    main = AsyncMock()
    main.chat = AsyncMock(return_value=_make_response("MAIN-CANONICAL"))

    mech = await _build_canonical_mechanism(node, session, main, light_llm=light)

    light.chat.assert_called_once()
    main.chat.assert_called_once()
    assert "MAIN-CANONICAL" in mech


@pytest.mark.asyncio
async def test_build_canonical_light_none_uses_main():
    """light_llm=None (默) → 跟现行为一致, main.chat 被调."""
    session = _make_session()
    node = session.state.graph.nodes["d_001"]

    main = AsyncMock()
    main.chat = AsyncMock(return_value=_make_response("MAIN-CANONICAL"))

    mech = await _build_canonical_mechanism(node, session, main)

    main.chat.assert_called_once()
    assert "MAIN-CANONICAL" in mech


@pytest.mark.asyncio
async def test_build_canonical_light_none_zero_regression_fallback():
    """light_llm=None 且 main raise LLMError → edge fallback (零回归原行为)."""
    session = _make_session()
    node = session.state.graph.nodes["d_001"]

    main = AsyncMock()
    main.chat = AsyncMock(side_effect=LLMError("api down"))

    mech = await _build_canonical_mechanism(node, session, main)

    # edge fallback: "通常 cause 风险规避"
    assert "风险规避" in mech


# ── e2e: flush_to_lexicon → _build_canonical_mechanism 透传 light_llm ──
# Phase 17.2 final review fix (I-2):
# 之前 _build_canonical_mechanism 已接 light_llm 参数, 但 flush_to_lexicon
# 不接 light_llm → caller 没法 wire → 整 lexicon light_llm 在 production 是 dead
# code. 本 e2e 验从 flush_to_lexicon(light_llm=X) 端到端透传到
# _build_canonical_mechanism(light_llm=X).


def _make_session_with_promotable_var() -> Session:
    """建带 1 个 L2 (active high activation) 的 session — flush_to_lexicon 会 promote."""
    g = ExplanationGraph(root_question="why?")
    g.add_node(VariableNode(
        id="d_001", name="长期不确定性 e2e", description="root driver",
        abstraction_level=2, confidence=0.8, epistemic="insight",
        activation=0.9, lifecycle_state="active",
    ))
    state = CognitiveState(graph=g, budget_remaining=10, root_question="why?")
    meta = SessionMeta.new(question="why?")
    meta.session_id = "s_e2e_light_001"
    return Session(meta=meta, state=state)


@pytest.mark.asyncio
async def test_flush_to_lexicon_passes_light_llm_to_build_canonical(
    monkeypatch, tmp_path,
):
    """e2e: flush_to_lexicon(light_llm=X) → _build_canonical_mechanism(light_llm=X)."""
    from explain_engine.engines import lexicon as lex_module
    from explain_engine.persistence.storage_v2 import StorageV2

    session = _make_session_with_promotable_var()

    light = AsyncMock(name="light_llm_sentinel")
    main = AsyncMock(name="main_llm_sentinel")

    captured = []

    async def spy_build(node, sess, llm, light_llm=None):
        captured.append({"node_id": node.id, "llm": llm, "light_llm": light_llm})
        return f"stub canonical {node.id}"

    monkeypatch.setattr(lex_module, "_build_canonical_mechanism", spy_build)
    # 隔离 lexicon 写入到 tmp dir
    monkeypatch.setenv("EXPLAIN_STORAGE_ROOT", str(tmp_path))
    # 跳 BGE-M3 batch embed (test mode)
    monkeypatch.setenv("EXPLAIN_EMBEDDING_DISABLED", "1")

    # 强制走 JSON impl (避免 PG 干扰)
    monkeypatch.setattr(lex_module, "_PG_BACKEND_ACTIVE", False)

    promoted = await lex_module.flush_to_lexicon(
        session, StorageV2(), llm=main, light_llm=light,
    )

    assert promoted == 1
    assert len(captured) == 1
    call = captured[0]
    assert call["node_id"] == "d_001"
    assert call["llm"] is main
    # 关键断言: light_llm 端到端透传
    assert call["light_llm"] is light


@pytest.mark.asyncio
async def test_flush_to_lexicon_light_llm_none_default(monkeypatch, tmp_path):
    """e2e: 不传 light_llm (默) → _build_canonical_mechanism 收 light_llm=None (零回归)."""
    from explain_engine.engines import lexicon as lex_module
    from explain_engine.persistence.storage_v2 import StorageV2

    session = _make_session_with_promotable_var()
    main = AsyncMock(name="main_llm")

    captured = []

    async def spy_build(node, sess, llm, light_llm=None):
        captured.append(light_llm)
        return "stub"

    monkeypatch.setattr(lex_module, "_build_canonical_mechanism", spy_build)
    monkeypatch.setenv("EXPLAIN_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("EXPLAIN_EMBEDDING_DISABLED", "1")
    monkeypatch.setattr(lex_module, "_PG_BACKEND_ACTIVE", False)

    await lex_module.flush_to_lexicon(session, StorageV2(), llm=main)

    assert captured == [None]
