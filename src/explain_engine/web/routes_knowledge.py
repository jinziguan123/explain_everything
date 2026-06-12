"""Task C1: 全局知识端点 (knowledge overview + theories 列表 / reject).

只读聚合现有引擎: SessionStore (session 数), lexicon dispatcher (变量),
theory cache (理论)。薄包, 本地单用户。

注意 — 不阻塞 event loop:
- get_active_theories 显式传 embedder=None: cache miss 时返 stale/empty cache,
  不触发同步 BGE-M3 重算 (无 embedding 进请求路径)。
- lexicon / theory cache 读是 file/PG IO, 跟现有 session 端点同级别。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from explain_engine.engines.lexicon import get_lexicon_top_k_for_compress
from explain_engine.engines.theory.cache import get_active_theories, reject_theory
from explain_engine.persistence.session import SessionStore
from explain_engine.persistence.storage_v2 import StorageV2
from explain_engine.web.serializers import lexicon_to_cytoscape

router = APIRouter(prefix="/api")


def _normalize_variable(row: Any) -> dict[str, Any]:
    """统一两种 lexicon backend shape 为 {global_id, name, reuse_count, abstraction_level}.

    - PG backend: list[dict] (SELECT * dict_row), 含 global_id / name /
      canonical_mechanism / reuse_count / abstraction_level 全列。
    - JSON backend: list[tuple] (global_id, canonical_mechanism, reuse_count) ——
      无 name / abstraction_level, 用 global_id 兜 name, level 默认 0。
    """
    if isinstance(row, dict):
        return {
            "global_id": row.get("global_id", ""),
            "name": row.get("name") or row.get("global_id", ""),
            "reuse_count": row.get("reuse_count", 0),
            "abstraction_level": row.get("abstraction_level", 0),
        }
    # JSON tuple: (global_id, canonical_mechanism, reuse_count)
    global_id, _canonical_mechanism, reuse_count = row
    return {
        "global_id": global_id,
        "name": global_id,
        "reuse_count": reuse_count,
        "abstraction_level": 0,
    }


def _fetch_normalized_variables() -> list[dict[str, Any]]:
    """拉全量 lexicon 变量并归一化为 {global_id, name, reuse_count, abstraction_level}."""
    return [
        _normalize_variable(row)
        for row in get_lexicon_top_k_for_compress(StorageV2(), k=100000)
    ]


def _theory_to_slim(theory: Any, ledger_stats: dict[str, Any] | None = None) -> dict[str, Any]:
    st = (ledger_stats or {}).get(theory.id)
    return {
        "id": theory.id,
        "summary": theory.natural_language_summary,
        "motif_type": theory.motif_type,
        "predictive_power": theory.predictive_power,
        "predictive_power_source": theory.predictive_power_source,
        "stability_status": theory.stability_status,
        "supporting_session_count": len(theory.supporting_sessions),
        # Phase T (§七): 台账聚合 — 无登记预测 = 叙事级 (motif ≠ theory)
        "predictions": {
            "total": st.total if st else 0,
            "pending": st.pending if st else 0,
            "hits": st.hits if st else 0,
            "misses": st.misses if st else 0,
        },
    }


def _active_theories(cache: Any) -> tuple[list[Any], list[Any]]:
    """过滤掉用户 reject 的理论 (cache 只 mark 不删, 见 theory/cache.reject_theory).

    返 (stable, tentative) 两个去 rejected 后的列表 — 与 CLI `theories` 命令一致。
    """
    rejected = cache.rejected_theory_ids
    stable = [t for t in cache.stable_theories if t.id not in rejected]
    tentative = [t for t in cache.tentative_theories if t.id not in rejected]
    return stable, tentative


def _slim_theories() -> list[dict[str, Any]]:
    from explain_engine.engines.theory.ledger import load_ledger, stats_by_theory
    from explain_engine.engines.theory.ranking import competition_rank

    storage = StorageV2()
    cache = get_active_theories(storage, embedder=None)
    stable, tentative = _active_theories(cache)
    ledger_stats = stats_by_theory(load_ledger(storage))
    n_total = max(len(cache.session_ids_snapshot), 1)
    # §七.3: 竞争序展示 (predictive_power → 综合分 → 复现数)
    ordered = competition_rank(stable + tentative, n_total)
    return [_theory_to_slim(t, ledger_stats) for t in ordered]


@router.get("/knowledge/overview")
async def knowledge_overview() -> dict[str, Any]:
    session_count = len(SessionStore().list())

    variables = _fetch_normalized_variables()

    cache = get_active_theories(StorageV2(), embedder=None)
    stable, tentative = _active_theories(cache)
    theories = _slim_theories()

    # Phase T: H3 复用率持续测量 (设计预期-修正版 §四 H3) —
    # 复用 ≥2 次的变量占比是"跨 session 累积价值"的最便宜代理。
    reused = sum(1 for v in variables if v.get("reuse_count", 0) >= 2)
    weakened = sum(1 for t in tentative if t.stability_status == "weakened")

    return {
        "session_count": session_count,
        "variable_count": len(variables),
        "theory_count": {
            "stable": len(stable),
            "tentative": len(tentative) - weakened,
            "weakened": weakened,
        },
        "h3_reuse": {
            "vars_reused": reused,
            "reuse_rate": round(reused / len(variables), 3) if variables else 0.0,
        },
        "top_variables": variables[:30],
        "theories": theories,
    }


@router.get("/knowledge/graph")
async def knowledge_graph() -> dict[str, Any]:
    """跨 session 知识图: 变量节点 + theory motif 边。只读, embedder=None 不重算。"""
    variables = _fetch_normalized_variables()
    cache = get_active_theories(StorageV2(), embedder=None)
    return lexicon_to_cytoscape(variables, cache)


@router.get("/theories")
async def list_theories() -> list[dict[str, Any]]:
    return _slim_theories()


@router.post("/theories/{theory_id}/reject")
async def reject(theory_id: str) -> dict[str, bool]:
    rejected = reject_theory(StorageV2(), theory_id)
    return {"rejected": rejected}


# ── Phase X1: 预测台账 Web 化 (机器提案、人签字) ──────────────────


class PredictionAddBody(BaseModel):
    theory_id: str
    assertion: str
    method: str = "search"
    deadline: str | None = None


class PredictionResolveBody(BaseModel):
    hit: bool
    note: str | None = None


class PredictionDraftBody(BaseModel):
    theory_id: str | None = None


def _prediction_to_dict(p: Any, due_ids: set[str]) -> dict[str, Any]:
    d = p.model_dump()
    d["due"] = p.id in due_ids
    return d


@router.get("/predictions")
async def list_predictions(theory: str | None = None) -> list[dict[str, Any]]:
    from explain_engine.engines.theory.ledger import due_predictions, load_ledger

    predictions = load_ledger(StorageV2())
    if theory:
        predictions = [p for p in predictions if p.theory_id == theory]
    due_ids = {p.id for p in due_predictions(predictions)}
    return [_prediction_to_dict(p, due_ids) for p in predictions]


@router.post("/predictions")
async def add_prediction_endpoint(body: PredictionAddBody) -> dict[str, Any]:
    from fastapi import HTTPException

    from explain_engine.engines.theory.ledger import add_prediction

    storage = StorageV2()
    cache = get_active_theories(storage, embedder=None)
    known = {t.id for t in cache.stable_theories + cache.tentative_theories}
    if body.theory_id not in known:
        raise HTTPException(status_code=404, detail=f"理论 {body.theory_id} 不存在")
    if body.method not in ("retrodiction", "search", "time_window"):
        raise HTTPException(status_code=422, detail=f"非法 method: {body.method}")
    try:
        pred = add_prediction(
            storage, body.theory_id, body.assertion,
            method=body.method, deadline=body.deadline,  # type: ignore[arg-type]
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return pred.model_dump()


@router.post("/predictions/{prediction_id}/resolve")
async def resolve_prediction_endpoint(
    prediction_id: str, body: PredictionResolveBody,
) -> dict[str, Any]:
    from fastapi import HTTPException

    from explain_engine.engines.theory.ledger import (
        load_ledger,
        resolve_prediction,
        stats_for,
    )

    storage = StorageV2()
    try:
        pred = resolve_prediction(storage, prediction_id, hit=body.hit, note=body.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    st = stats_for(load_ledger(storage), pred.theory_id)
    return {
        "prediction": pred.model_dump(),
        "theory_stats": {
            "hits": st.hits, "misses": st.misses, "pending": st.pending,
            "predictive_power": st.predictive_power,
            "weakened": st.weakened,
        },
    }


@router.post("/predictions/draft")
async def draft_predictions_endpoint(body: PredictionDraftBody) -> list[dict[str, Any]]:
    """机器提案: LLM 起草并登记 (origin=llm)。LLM 调用, 数十秒。"""
    from fastapi import HTTPException

    from explain_engine.config import make_light_llm_client
    from explain_engine.engines.theory.ledger import add_prediction
    from explain_engine.engines.theory.prediction_draft import (
        auto_draft_predictions,
        draft_for_theory,
    )

    storage = StorageV2()
    light = make_light_llm_client()
    try:
        if body.theory_id:
            cache = get_active_theories(storage, embedder=None)
            match = next(
                (t for t in [*cache.stable_theories, *cache.tentative_theories]
                 if t.id == body.theory_id), None,
            )
            if match is None:
                raise HTTPException(
                    status_code=404, detail=f"理论 {body.theory_id} 不存在",
                )
            items = await draft_for_theory(match, light)
            drafted = [
                add_prediction(storage, body.theory_id, it.assertion,
                               method=it.method, deadline=it.deadline, origin="llm")
                for it in items
            ]
        else:
            drafted = await auto_draft_predictions(storage, light)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"起草失败: {type(exc).__name__}: {exc}",
        ) from exc
    return [p.model_dump() for p in drafted]
