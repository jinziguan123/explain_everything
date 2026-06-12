"""Phase T 预测台账 — docs/设计预期-修正版.md §七.2。

理论必须可失败, 否则系统会神学化。台账把这条哲学变成机制:

  theory 登记预测 (断言 + 检验方式 + 期限)
      ↓
  台账结算 (手动 resolve / retrodiction 自动结算)
      ↓
  predictive_power = 命中 / 已结算   (覆盖 falsifiability.py 的回溯估计)
      ↓
  连续 WEAKEN_MISS_STREAK 次落空 → 理论降级 "weakened" (已削弱)

台账独立持久化在 knowledge/predictions.json — theory cache 整体重算时
台账不动 (theory id 是内容稳定 hash, 重算后仍能对上)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

WEAKEN_MISS_STREAK: int = 2
"""§七.2: 连续落空次数达到此值 → 理论降级"已削弱"。"""

RETRO_MATCH_THRESHOLD: float = 0.85
"""retrodiction 自动结算的 embedding 匹配阈值 (与 Phase 13 merge 一致)。"""

PredictionMethod = Literal["retrodiction", "search", "time_window"]
# retrodiction: 用理论预测一个它未参与构建的历史 session (可自动结算)
# search:       断言可被 web 检索证实/证伪 (手动 resolve)
# time_window:  等待现实时间窗内应验 (手动 resolve, 有 deadline)

PredictionStatus = Literal["pending", "hit", "miss"]


class Prediction(BaseModel):
    """一条已登记的可检验预测。"""

    id: str = Field(min_length=1)          # p_NNN
    theory_id: str = Field(min_length=1)
    assertion: str = Field(min_length=1)
    """具体、可观察的断言 (不是理论的复述)。"""
    method: PredictionMethod = "search"
    deadline: str | None = None
    """iso 日期 (YYYY-MM-DD)。time_window 必填; 其他方式可选。"""
    status: PredictionStatus = "pending"
    created_at: str = ""
    resolved_at: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class TheoryLedgerStats:
    """单个理论的台账聚合。"""

    total: int = 0
    pending: int = 0
    hits: int = 0
    misses: int = 0
    consecutive_misses: int = 0
    """按 resolved_at 时间序的尾部连败数。"""

    @property
    def resolved(self) -> int:
        return self.hits + self.misses

    @property
    def predictive_power(self) -> float | None:
        """台账命中率; 无已结算预测时为 None (回退回溯估计)。"""
        return self.hits / self.resolved if self.resolved else None

    @property
    def weakened(self) -> bool:
        return self.consecutive_misses >= WEAKEN_MISS_STREAK


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _ledger_path(storage) -> Path:
    return storage.knowledge_dir() / "predictions.json"


def load_ledger(storage) -> list[Prediction]:
    path = _ledger_path(storage)
    if not path.exists():
        return []
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [Prediction.model_validate(p) for p in d.get("predictions", [])]


def _save_ledger(storage, predictions: list[Prediction]) -> None:
    """临时文件 + rename, 与 theory cache 同款原子写。"""
    path = _ledger_path(storage)
    tmp = path.with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(
        {"version": "1.0", "predictions": [p.model_dump() for p in predictions]},
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")
    tmp.replace(path)


def _next_prediction_id(predictions: list[Prediction]) -> str:
    existing = [
        int(p.id.split("_")[1]) for p in predictions
        if p.id.startswith("p_") and p.id[2:].isdigit()
    ]
    return f"p_{(max(existing) + 1) if existing else 1:03d}"


def add_prediction(
    storage,
    theory_id: str,
    assertion: str,
    method: PredictionMethod = "search",
    deadline: str | None = None,
) -> Prediction:
    """登记一条预测。time_window 必须带 deadline (YYYY-MM-DD)。"""
    if method == "time_window" and not deadline:
        raise ValueError("time_window 预测必须指定 --deadline (YYYY-MM-DD)")
    if deadline is not None:
        datetime.strptime(deadline, "%Y-%m-%d")  # 格式校验, 不合法直接抛
    predictions = load_ledger(storage)
    pred = Prediction(
        id=_next_prediction_id(predictions),
        theory_id=theory_id,
        assertion=assertion.strip(),
        method=method,
        deadline=deadline,
        created_at=_now_iso(),
    )
    predictions.append(pred)
    _save_ledger(storage, predictions)
    return pred


def resolve_prediction(
    storage, prediction_id: str, hit: bool, note: str | None = None,
) -> Prediction:
    """结算一条 pending 预测。已结算的拒绝重复结算 (台账不可篡改)。"""
    predictions = load_ledger(storage)
    for i, p in enumerate(predictions):
        if p.id == prediction_id:
            if p.status != "pending":
                raise ValueError(
                    f"{prediction_id} 已结算为 {p.status}, 台账不可篡改"
                )
            updated = p.model_copy(update={
                "status": "hit" if hit else "miss",
                "resolved_at": _now_iso(),
                "note": note or p.note,
            })
            predictions[i] = updated
            _save_ledger(storage, predictions)
            return updated
    raise KeyError(f"预测 {prediction_id} 不存在")


def stats_for(predictions: list[Prediction], theory_id: str) -> TheoryLedgerStats:
    """单理论台账聚合。连败按 resolved_at 排序后从尾部数。"""
    mine = [p for p in predictions if p.theory_id == theory_id]
    resolved = sorted(
        (p for p in mine if p.status != "pending"),
        key=lambda p: p.resolved_at or "",
    )
    streak = 0
    for p in reversed(resolved):
        if p.status == "miss":
            streak += 1
        else:
            break
    return TheoryLedgerStats(
        total=len(mine),
        pending=sum(1 for p in mine if p.status == "pending"),
        hits=sum(1 for p in mine if p.status == "hit"),
        misses=sum(1 for p in mine if p.status == "miss"),
        consecutive_misses=streak,
    )


def stats_by_theory(predictions: list[Prediction]) -> dict[str, TheoryLedgerStats]:
    return {
        tid: stats_for(predictions, tid)
        for tid in {p.theory_id for p in predictions}
    }


def decide_stability(
    st: TheoryLedgerStats | None, window_promoted: bool,
) -> Literal["tentative", "stable", "weakened"]:
    """Phase T 状态决策 (§七.1/.2), 供 recompute 调用:

    1. 台账连败 ≥ WEAKEN_MISS_STREAK → weakened (无论复现窗口)
    2. 无任何登记预测 → tentative (叙事级准入门槛: motif ≠ theory)
    3. 有预测且复现窗口满足 → stable
    4. 其余 → tentative
    """
    if st is not None and st.weakened:
        return "weakened"
    if st is None or st.total == 0:
        return "tentative"
    return "stable" if window_promoted else "tentative"


def apply_ledger_overlay(theories, stats: dict[str, TheoryLedgerStats]) -> list:
    """台账覆盖回溯估计 (§七.2): 有已结算预测的理论, predictive_power 改用
    台账命中率, 来源标 "ledger"; 其余保留 falsifiability 回溯估计。

    Args:
        theories: list[Theory] (避免循环 import, 鸭子类型)。
    """
    from dataclasses import replace

    out = []
    for t in theories:
        st = stats.get(t.id)
        if st is not None and st.predictive_power is not None:
            out.append(replace(
                t,
                predictive_power=st.predictive_power,
                predictive_power_source="ledger",
            ))
        else:
            out.append(t)
    return out


def due_predictions(
    predictions: list[Prediction], today: str | None = None,
) -> list[Prediction]:
    """已到期仍 pending 的预测 (CLI 列表标红 / session 启动提醒用)。"""
    today = today or datetime.now(UTC).strftime("%Y-%m-%d")
    return [
        p for p in predictions
        if p.status == "pending" and p.deadline is not None and p.deadline <= today
    ]


def settle_retrodictions(
    storage,
    embedder,
    threshold: float = RETRO_MATCH_THRESHOLD,
) -> list[tuple[Prediction, bool]]:
    """自动结算 pending 的 retrodiction 预测 (§七.2 MVP 回溯预测)。

    判定: 断言 embedding 与"理论未参与构建的历史 session"的任一 L0 现象
    name embedding 余弦 ≥ threshold → hit; 有 held-out session 但无匹配 → miss;
    没有 held-out session → 跳过 (维持 pending, 等更多 session 积累)。

    Returns:
        [(已结算的 prediction, hit)] — 跳过的不在内。
    """
    import numpy as np

    from explain_engine.engines.theory.cache import get_active_theories
    from explain_engine.engines.theory.loader import load_all_session_graphs
    from explain_engine.persistence.session import SessionStore

    predictions = load_ledger(storage)
    pending_retro = [
        p for p in predictions
        if p.status == "pending" and p.method == "retrodiction"
    ]
    if not pending_retro:
        return []

    cache = get_active_theories(storage, embedder=None)
    theory_by_id = {
        t.id: t for t in [*cache.stable_theories, *cache.tentative_theories]
    }
    all_sids = sorted(m.session_id for m in SessionStore().list())

    settled: list[tuple[Prediction, bool]] = []
    for pred in pending_retro:
        theory = theory_by_id.get(pred.theory_id)
        supporting = set(theory.supporting_sessions) if theory else set()
        held_out = [sid for sid in all_sids if sid not in supporting]
        if not held_out:
            continue  # 无对照 session, 维持 pending
        try:
            graphs = load_all_session_graphs(held_out, storage)
        except Exception:
            continue
        l0_names = [
            n.name
            for g in graphs.values()
            for n in g.nodes.values()
            if getattr(n, "abstraction_level", -1) == 0
        ]
        if not l0_names:
            continue
        q = embedder.encode([pred.assertion])
        corpus = embedder.encode(l0_names)

        def _norm(x):
            norms = np.linalg.norm(x, axis=1, keepdims=True)
            return x / np.where(norms > 0, norms, 1)

        sims = _norm(q) @ _norm(corpus).T
        hit = bool((sims >= threshold).any())
        resolve_prediction(
            storage, pred.id, hit=hit,
            note=f"retrodiction 自动结算 (对照 {len(held_out)} 个 session)",
        )
        settled.append((pred, hit))
    return settled
