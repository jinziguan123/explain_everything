"""证据审计 + H3 埋点测试 (Phase X3)。不触网 / 不触 PG。"""

import json

from explain_engine.engines.evidence_audit import (
    AuditResult,
    collect_evidence_pool,
    record_audit,
    sample_evidence,
)
from explain_engine.engines.lexicon import read_h3_log, record_h3
from explain_engine.schema.evidence import Evidence
from explain_engine.schema.nodes import VariableNode
from explain_engine.schema.state import CognitiveState


class StubStorage:
    def __init__(self, p):
        self._p = p

    def knowledge_dir(self):
        return self._p


class StubMeta:
    def __init__(self, sid):
        self.session_id = sid


class StubSession:
    def __init__(self, state):
        self.state = state


class StubStore:
    """SessionStore 替身: list()/load() 喂内存 session。"""

    def __init__(self, sessions: dict):
        self._sessions = sessions

    def list(self):
        return [StubMeta(sid) for sid in self._sessions]

    def load(self, sid):
        return StubSession(self._sessions[sid])


def _state_with_evidence(support=2, contradict=1) -> CognitiveState:
    state = CognitiveState.bootstrap("q", budget=5)
    node = VariableNode(
        id="p_001", name="现象", description="d", abstraction_level=0,
        confidence=0.8, epistemic="observation",
        evidence_ids=[f"ev_{i:03d}" for i in range(1, support + contradict + 1)],
    )
    state.graph.add_node(node)
    i = 0
    for stance, count in (("support", support), ("contradict", contradict)):
        for _ in range(count):
            i += 1
            ev = Evidence(
                id=f"ev_{i:03d}", claim="断言", url=f"https://a{i}.com/x",
                title=f"来源{i}", snippet="s", stance=stance,
                retrieved_at="2026-06-12T00:00:00+00:00",
            )
            state.evidence[ev.id] = ev
    return state


# ─── 证据池与抽样 ──────────────────────────────────────────


def test_collect_pool_only_support_with_target():
    store = StubStore({"s_aaaa0001": _state_with_evidence(support=2, contradict=1)})
    pool = collect_evidence_pool(store)
    assert len(pool) == 2  # contradict 不进审计池
    assert all(it.stance == "support" for it in pool)
    assert all(it.target_id == "p_001" for it in pool)


def test_sample_deterministic():
    store = StubStore({
        "s_aaaa0001": _state_with_evidence(support=5, contradict=0),
        "s_aaaa0002": _state_with_evidence(support=5, contradict=0),
    })
    pool = collect_evidence_pool(store)
    s1 = sample_evidence(pool, 4, seed=7)
    s2 = sample_evidence(pool, 4, seed=7)
    assert [(i.sid, i.evidence_id) for i in s1] == [(i.sid, i.evidence_id) for i in s2]
    s3 = sample_evidence(pool, 4, seed=8)
    assert [(i.sid, i.evidence_id) for i in s1] != [(i.sid, i.evidence_id) for i in s3]


# ─── 审计结果与落盘 ────────────────────────────────────────


def test_audit_result_verdict():
    r = AuditResult(sampled=10, checked=10, genuine=9, fake=1)
    assert r.fake_rate == 0.1
    assert r.passed is True
    r2 = AuditResult(sampled=10, checked=10, genuine=8, fake=2)
    assert r2.passed is False
    assert AuditResult(sampled=5).passed is None  # 没核对无结论


def test_record_audit_appends(tmp_path):
    storage = StubStorage(tmp_path)
    record_audit(storage, AuditResult(sampled=3, checked=3, genuine=3), seed=1)
    record_audit(storage, AuditResult(sampled=3, checked=2, genuine=1, fake=1), seed=2)
    data = json.loads((tmp_path / "evidence_audits.json").read_text(encoding="utf-8"))
    assert len(data["audits"]) == 2
    assert data["audits"][0]["passed"] is True
    assert data["audits"][1]["fake_rate"] == 0.5


# ─── H3 per-session 埋点 ───────────────────────────────────


def test_record_h3_and_read(tmp_path):
    storage = StubStorage(tmp_path)
    record_h3(storage, "s_aaaa0001", ["created", "created", "merged"])
    record_h3(storage, "s_aaaa0002", ["merged", "merged", "created"])
    log = read_h3_log(storage)
    assert [r["sid"] for r in log] == ["s_aaaa0001", "s_aaaa0002"]
    assert log[0]["reuse_rate"] == round(1 / 3, 3)
    assert log[1]["reused"] == 2


def test_record_h3_skips_pure_refresh(tmp_path):
    storage = StubStorage(tmp_path)
    record_h3(storage, "s_aaaa0001", ["refreshed", "refreshed"])
    assert read_h3_log(storage) == []


def test_read_h3_dedupes_by_sid_keeping_last(tmp_path):
    storage = StubStorage(tmp_path)
    record_h3(storage, "s_aaaa0001", ["created"])
    record_h3(storage, "s_aaaa0001", ["merged", "merged"])  # 修正后重 flush
    log = read_h3_log(storage)
    assert len(log) == 1
    assert log[0]["reused"] == 2