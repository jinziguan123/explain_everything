"""证据审计 — H2 协议的"来源伪造率检查"运营化 (Phase X3, §四 H2)。

接地管线的证据 (URL + snippet) 来自 LLM 判定的检索结果, 存在两类风险:
检索结果与断言其实无关 (立场误判)、来源本身不可靠。审计流程:

  跨 session 收集证据池 → 确定性抽样 N 条 → 人逐条核对
  → 伪造率 = 不合格数 / 已核对数 → 记录落盘 (可追溯)

判定线 (docs/H1-盲评协议.md / §四 H2): 伪造率 > 10% → 接地管线不合格。
纯数据层 (0 LLM call); 交互在 CLI 层。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

FAKE_RATE_THRESHOLD: float = 0.10
"""H2 协议: 伪造率超过此值, 接地管线判不合格。"""


@dataclass(frozen=True)
class AuditItem:
    """待审计的一条证据 (含溯源上下文)。"""

    sid: str
    evidence_id: str
    claim: str
    url: str
    title: str
    snippet: str
    stance: str
    target_id: str
    """该证据支撑的节点/边 id。"""


@dataclass
class AuditResult:
    sampled: int = 0
    checked: int = 0
    genuine: int = 0
    fake: int = 0
    skipped: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)
    """[{evidence_id, sid, url, verdict}] 逐条判定记录。"""

    @property
    def fake_rate(self) -> float | None:
        return self.fake / self.checked if self.checked else None

    @property
    def passed(self) -> bool | None:
        """None = 没核对任何条目, 无结论。"""
        if self.checked == 0:
            return None
        return (self.fake_rate or 0.0) <= FAKE_RATE_THRESHOLD


def collect_evidence_pool(store) -> list[AuditItem]:
    """跨全部 session 收集证据池 (只收 support 立场 — 实证判定所依据的)。

    Args:
        store: SessionStore (有 .list() / .load())。
    """
    pool: list[AuditItem] = []
    for meta in store.list():
        try:
            session = store.load(meta.session_id)
        except Exception:
            continue
        state = session.state
        if not state.evidence:
            continue
        # evidence_id → 它支撑的 target (节点/边)
        target_of: dict[str, str] = {}
        for obj_id, obj in [*state.graph.nodes.items(), *state.graph.edges.items()]:
            for ev_id in obj.evidence_ids:
                target_of[ev_id] = obj_id
        for ev in state.evidence.values():
            if ev.stance != "support":
                continue
            pool.append(AuditItem(
                sid=meta.session_id, evidence_id=ev.id, claim=ev.claim,
                url=ev.url, title=ev.title, snippet=ev.snippet,
                stance=ev.stance, target_id=target_of.get(ev.id, "?"),
            ))
    return pool


def sample_evidence(
    pool: list[AuditItem], n: int, seed: int = 42,
) -> list[AuditItem]:
    """确定性抽样: 按 sha256(seed:sid:evidence_id) 排序取前 n。

    不用 random — 同 seed 同池必得同样本 (审计可复现)。
    """
    def _key(item: AuditItem) -> str:
        return hashlib.sha256(
            f"{seed}:{item.sid}:{item.evidence_id}".encode()
        ).hexdigest()

    return sorted(pool, key=_key)[:n]


def record_audit(storage, result: AuditResult, seed: int) -> None:
    """审计记录追加到 knowledge/evidence_audits.json (诚实条款: 无论结果如何都存)。"""
    path = storage.knowledge_dir() / "evidence_audits.json"
    records: list[dict[str, Any]] = []
    if path.exists():
        try:
            records = json.loads(path.read_text(encoding="utf-8")).get("audits", [])
        except (OSError, json.JSONDecodeError):
            records = []
    records.append({
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "seed": seed,
        "sampled": result.sampled,
        "checked": result.checked,
        "genuine": result.genuine,
        "fake": result.fake,
        "skipped": result.skipped,
        "fake_rate": result.fake_rate,
        "passed": result.passed,
        "items": result.items,
    })
    tmp = path.with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(
        json.dumps({"version": "1.0", "audits": records},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)
