"""Phase G 接地引擎测试 (docs/设计预期-修正版.md §五.3 / §六)。

FakeLLM + 注入 search_fn, 不触网 / 不触 PG。
"""

import asyncio

import pytest

from explain_engine.chat.web_search import SearchResult
from explain_engine.engines.grounding import (
    TIER_BASE,
    _verdict_from_evidence,
    apply_grounded_confidence,
    compute_tiers,
    ground_state,
)
from explain_engine.llm.client import Response
from explain_engine.schema.edges import RelationEdge
from explain_engine.schema.evidence import Evidence
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


def _node(nid, name, level, **kw):
    return VariableNode(
        id=nid, name=name, description=f"{name}的描述",
        abstraction_level=level, confidence=0.8, epistemic="observation", **kw,
    )


def _edge(eid, src, tgt, rtype, conf=0.8, **kw):
    return RelationEdge(
        id=eid, source_node=src, target_node=tgt,
        relation_type=rtype, confidence=conf,
        mechanism_description="机制", **kw,
    )


def _state() -> CognitiveState:
    """d_001 →causes→ c_001 →manifests_as→ {p_001, p_002}"""
    state = CognitiveState.bootstrap("为什么 X", budget=5)
    g = state.graph
    g.add_node(_node("p_001", "现象一", 0))
    g.add_node(_node("p_002", "现象二", 0))
    g.add_node(_node("c_001", "机制一", 1))
    g.add_node(_node("d_001", "驱动一", 2))
    g.add_edge(_edge("e_001", "c_001", "p_001", "manifests_as", 0.9))
    g.add_edge(_edge("e_002", "c_001", "p_002", "manifests_as", 0.8))
    g.add_edge(_edge("e_003", "d_001", "c_001", "causes", 0.85))
    return state


def _ev(eid, url, stance):
    return Evidence(
        id=eid, claim="c", url=url, title="t", snippet="s",
        stance=stance, retrieved_at="2026-06-11T00:00:00+00:00",
    )


# ─── compute_tiers ─────────────────────────────────────────


def test_tiers_ungrounded_all_hypothesis():
    tiers = compute_tiers(_state())
    assert set(tiers.values()) == {"hypothesis"}


def test_tiers_verified_propagates_inference_downstream():
    state = _state()
    g = state.graph
    g.replace_node("d_001", g.nodes["d_001"].model_copy(
        update={"evidence_state": "verified"}))
    tiers = compute_tiers(state)
    assert tiers["d_001"] == "fact"
    # c_001 唯一前驱 d_001 是 fact → 推断; 其出边/子节点继续传递
    assert tiers["c_001"] == "inference"
    assert tiers["p_001"] == "inference"
    assert tiers["e_003"] == "inference"  # 边: source 是 fact
    assert tiers["e_001"] == "inference"  # 边: source 是 inference


def test_tiers_contested_does_not_propagate():
    state = _state()
    g = state.graph
    g.replace_node("d_001", g.nodes["d_001"].model_copy(
        update={"evidence_state": "contested"}))
    tiers = compute_tiers(state)
    assert tiers["d_001"] == "contested"
    assert tiers["c_001"] == "hypothesis"  # 争议不算可靠前驱
    assert tiers["e_003"] == "hypothesis"


def test_tiers_verified_edge_is_fact():
    state = _state()
    g = state.graph
    g.replace_edge("e_003", g.edges["e_003"].model_copy(
        update={"evidence_state": "verified"}))
    assert compute_tiers(state)["e_003"] == "fact"


# ─── 置信度公式 (§五.3) ────────────────────────────────────


def test_apply_grounded_confidence_caps_hypothesis():
    state = _state()
    apply_grounded_confidence(state)
    e = state.graph.edges["e_003"]
    # hypothesis 天花板 0.4 × 原始 0.85 = 0.34; 原始值保留
    assert e.llm_confidence == 0.85
    assert e.confidence == pytest.approx(0.34)


def test_apply_grounded_confidence_verified_keeps_full():
    state = _state()
    g = state.graph
    g.replace_edge("e_003", g.edges["e_003"].model_copy(
        update={"evidence_state": "verified"}))
    apply_grounded_confidence(state)
    assert state.graph.edges["e_003"].confidence == pytest.approx(
        TIER_BASE["fact"] * 0.85
    )


def test_apply_grounded_confidence_idempotent():
    state = _state()
    apply_grounded_confidence(state)
    once = {eid: e.confidence for eid, e in state.graph.edges.items()}
    apply_grounded_confidence(state)
    twice = {eid: e.confidence for eid, e in state.graph.edges.items()}
    assert once == twice  # 不复利衰减


# ─── 聚合判定 (§六.1) ──────────────────────────────────────


def test_verdict_two_domains_support():
    evs = [_ev("ev_001", "https://a.com/1", "support"),
           _ev("ev_002", "https://b.org/2", "support")]
    assert _verdict_from_evidence(evs) == "verified"


def test_verdict_same_domain_not_independent():
    evs = [_ev("ev_001", "https://a.com/1", "support"),
           _ev("ev_002", "https://www.a.com/2", "support")]
    assert _verdict_from_evidence(evs) == "unverified"


def test_verdict_conflict_is_contested():
    evs = [_ev("ev_001", "https://a.com/1", "support"),
           _ev("ev_002", "https://b.org/2", "contradict")]
    assert _verdict_from_evidence(evs) == "contested"


def test_verdict_empty_unverified():
    assert _verdict_from_evidence([]) == "unverified"


# ─── ground_state 端到端 (FakeLLM + 注入 search) ───────────


class FakeGroundingLLM:
    """第 1 次调用返 claims, 之后返 stances (全 support)。"""

    def __init__(self, claim_targets: list[str], stances=None) -> None:
        self._claim_targets = claim_targets
        self._stances = stances or [
            {"index": 0, "stance": "support"},
            {"index": 1, "stance": "support"},
        ]
        self.calls = 0

    async def chat(self, messages, schema=None, model=None, on_delta=None):
        self.calls += 1
        if self.calls == 1:
            parsed = {"claims": [
                {"target_id": t, "claim": f"{t} 的可检索断言"}
                for t in self._claim_targets
            ]}
        else:
            parsed = {"verdicts": self._stances}
        return Response(text="", parsed=parsed, model="fake",
                        usage={"input_tokens": 0, "output_tokens": 0})


async def _two_domain_search(_query: str):
    return [
        SearchResult(title="来源A", url="https://a.com/x", snippet="支持"),
        SearchResult(title="来源B", url="https://b.org/y", snippet="支持"),
    ]


def test_ground_state_verifies_and_reweights():
    state = _state()
    llm = FakeGroundingLLM(["p_001", "p_002", "e_001", "e_002", "e_003"])
    summary = asyncio.run(ground_state(state, llm, search_fn=_two_domain_search))

    # 范围 (§六.1): 全部 L0 (p_001/p_002) + 核心变量入边 (e_003 是 c_001 的入边);
    # e_001/e_002 是核心变量出边, 不接地 (claims 即使给了也被过滤)
    assert summary.targets_total == 3
    assert summary.verified == 3
    assert summary.evidence_added == 6
    # 节点/边状态更新 + 证据落盘
    assert state.graph.nodes["p_001"].evidence_state == "verified"
    assert state.graph.nodes["p_001"].epistemic == "fact"  # L0 verified 升级
    assert state.graph.edges["e_003"].evidence_state == "verified"
    assert len(state.evidence) == 6
    # 置信度公式已应用: verified 边走 fact 天花板 1.0 × 原始
    e3 = state.graph.edges["e_003"]
    assert e3.llm_confidence == 0.85
    assert e3.confidence == pytest.approx(0.85)
    # 未接地的边维持假设级天花板 0.4 × 原始
    e1 = state.graph.edges["e_001"]
    assert e1.llm_confidence == 0.9
    assert e1.confidence == pytest.approx(0.36)


def test_ground_state_concurrency_limited_and_deterministic():
    """并发限流 (Semaphore) 生效, 且 evidence id 按 target 顺序确定分配。"""
    import asyncio as aio

    state = _state()
    # 多加几个 L0 撑大 target 数
    for i in range(3, 9):
        state.graph.add_node(_node(f"p_{i:03d}", f"现象{i}", 0))
    l0_count = sum(
        1 for n in state.graph.nodes.values() if n.abstraction_level == 0
    )
    targets = [f"p_{i:03d}" for i in range(1, l0_count + 1)] + ["e_003"]
    llm = FakeGroundingLLM(targets)

    current = 0
    peak = 0

    async def tracking_search(_q: str):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await aio.sleep(0.02)
        current -= 1
        return [
            SearchResult(title="来源A", url="https://a.com/x", snippet="s"),
            SearchResult(title="来源B", url="https://b.org/y", snippet="s"),
        ]

    summary = asyncio.run(ground_state(
        state, llm, search_fn=tracking_search, concurrency=3,
    ))
    assert summary.verified == summary.targets_total
    assert peak <= 3   # Semaphore 限流
    assert peak >= 2   # 确实并发了
    # evidence id 确定性: 串行落盘按 target 顺序, 首个 target 拿 ev_001/ev_002
    assert state.graph.nodes["p_001"].evidence_ids[0] == "ev_001"


def test_ground_state_search_failure_is_not_fatal():
    state = _state()
    llm = FakeGroundingLLM(["p_001", "p_002", "e_001", "e_002", "e_003"])

    async def broken_search(_q: str):
        raise RuntimeError("rate limited")

    summary = asyncio.run(ground_state(state, llm, search_fn=broken_search))
    assert summary.verified == 0
    assert summary.unverified == summary.targets_total
    assert summary.errors
    # 置信公式仍应用 (全 hypothesis 天花板)
    assert state.graph.edges["e_003"].confidence == pytest.approx(0.34)


# ─── 增量接地 (Phase A 机器提案) ───────────────────────────


def test_ground_state_incremental_skips_already_grounded():
    """第二次增量接地: 已接地对象不再检索, 但置信公式仍应用。"""
    state = _state()
    targets = ["p_001", "p_002", "e_001", "e_002", "e_003"]
    search_calls = []

    async def counting_search(q: str):
        search_calls.append(q)
        return await _two_domain_search(q)

    s1 = asyncio.run(ground_state(
        state, FakeGroundingLLM(targets), search_fn=counting_search,
    ))
    assert s1.targets_total == 3
    assert state.graph.nodes["p_001"].grounded_at is not None
    n_first = len(search_calls)

    s2 = asyncio.run(ground_state(
        state, FakeGroundingLLM(targets), search_fn=counting_search,
    ))
    assert s2.targets_total == 0          # 无新对象
    assert len(search_calls) == n_first   # 零检索
    assert s2.edges_reweighted >= 0       # 公式仍跑 (图结构可能变了)


def test_ground_state_incremental_picks_up_new_objects():
    """图演化出新 L0 后, 增量接地只处理新对象。"""
    state = _state()
    targets = ["p_001", "p_002", "e_001", "e_002", "e_003", "p_003"]
    asyncio.run(ground_state(
        state, FakeGroundingLLM(targets), search_fn=_two_domain_search,
    ))
    # 模拟推理循环长出新现象
    state.graph.add_node(_node("p_003", "新现象", 0))
    s2 = asyncio.run(ground_state(
        state, FakeGroundingLLM(targets), search_fn=_two_domain_search,
    ))
    assert s2.targets_total == 1
    assert state.graph.nodes["p_003"].evidence_state == "verified"


def test_ground_state_failed_search_retried_next_round():
    """检索失败的对象 grounded_at 保持 None, 下轮增量重试。"""
    state = _state()
    targets = ["p_001", "p_002", "e_001", "e_002", "e_003"]

    async def broken_search(_q: str):
        raise RuntimeError("rate limited")

    asyncio.run(ground_state(
        state, FakeGroundingLLM(targets), search_fn=broken_search,
    ))
    assert state.graph.nodes["p_001"].grounded_at is None

    s2 = asyncio.run(ground_state(
        state, FakeGroundingLLM(targets), search_fn=_two_domain_search,
    ))
    assert s2.targets_total == 3  # 全部重试
    assert s2.verified == 3


def test_ground_state_all_mode_regrounds():
    """incremental=False 全量重接地 (CLI --all)。"""
    state = _state()
    targets = ["p_001", "p_002", "e_001", "e_002", "e_003"]
    asyncio.run(ground_state(
        state, FakeGroundingLLM(targets), search_fn=_two_domain_search,
    ))
    s2 = asyncio.run(ground_state(
        state, FakeGroundingLLM(targets), search_fn=_two_domain_search,
        incremental=False,
    ))
    assert s2.targets_total == 3


# ─── 持久化 roundtrip ──────────────────────────────────────


def test_evidence_roundtrip_via_state_dict():
    state = _state()
    llm = FakeGroundingLLM(["p_001", "p_002", "e_001", "e_002", "e_003"])
    asyncio.run(ground_state(state, llm, search_fn=_two_domain_search))

    restored = CognitiveState.from_dict(state.to_dict())
    assert restored.evidence.keys() == state.evidence.keys()
    assert restored.graph.nodes["p_001"].evidence_state == "verified"
    assert restored.graph.edges["e_001"].llm_confidence == 0.9
    # 旧格式 (无 evidence 键) 兼容
    legacy = state.to_dict()
    legacy.pop("evidence")
    assert CognitiveState.from_dict(legacy).evidence == {}
