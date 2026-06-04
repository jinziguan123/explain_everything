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


def _theory_to_slim(theory: Any) -> dict[str, Any]:
    return {
        "id": theory.id,
        "summary": theory.natural_language_summary,
        "motif_type": theory.motif_type,
        "predictive_power": theory.predictive_power,
        "stability_status": theory.stability_status,
        "supporting_session_count": len(theory.supporting_sessions),
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
    cache = get_active_theories(StorageV2(), embedder=None)
    stable, tentative = _active_theories(cache)
    return [_theory_to_slim(t) for t in stable + tentative]


@router.get("/knowledge/overview")
async def knowledge_overview() -> dict[str, Any]:
    session_count = len(SessionStore().list())

    variables = _fetch_normalized_variables()

    cache = get_active_theories(StorageV2(), embedder=None)
    stable, tentative = _active_theories(cache)
    theories = [_theory_to_slim(t) for t in stable + tentative]

    return {
        "session_count": session_count,
        "variable_count": len(variables),
        "theory_count": {
            "stable": len(stable),
            "tentative": len(tentative),
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
