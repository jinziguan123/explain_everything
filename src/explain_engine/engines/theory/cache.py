"""Phase 16: TheoriesCache lazy invalidation + atomic write."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from explain_engine.engines.theory.theory import Theme, Theory


@dataclass
class TheoriesCache:
    themes: list[Theme] = field(default_factory=list)
    tentative_theories: list[Theory] = field(default_factory=list)
    stable_theories: list[Theory] = field(default_factory=list)
    rejected_theory_ids: set[str] = field(default_factory=set)
    session_ids_snapshot: list[str] = field(default_factory=list)
    cold_start_threshold: int = 3
    stability_window_size: int = 5
    computed_at: str = ""


def _empty_cache_obj() -> TheoriesCache:
    return TheoriesCache(computed_at=_now_iso())


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def get_active_theories(
    storage,
    embedder=None,
    *,
    force_recompute: bool = False,
) -> TheoriesCache:
    """Single source 入口 — chat / cli / bootstrap inject 都调.

    流程:
      1. 读 theories.json (无 → empty cache)
      2. compare cache.session_ids_snapshot 跟 SessionStore().list() set
      3. 不一致 OR force_recompute → _recompute_all + atomic write
      4. embedder=None + cache miss → 返 stale cache (bootstrap inject 路径, degraded)
    """
    from explain_engine.persistence.session import SessionStore
    cache_path = storage.knowledge_dir() / "theories.json"
    cache = _load_cache(cache_path) if cache_path.exists() else _empty_cache_obj()

    current_sids = sorted(m.session_id for m in SessionStore().list())
    needs_recompute = (
        force_recompute
        or set(cache.session_ids_snapshot) != set(current_sids)
        or not cache_path.exists()
    )

    if needs_recompute:
        if embedder is None:
            # bootstrap inject 路径 — 返 stale (best-effort, 不阻塞 bootstrap)
            # 注: 即便 stale, 也确保 snapshot 更新为 current 减少未来无意义 invalidation?
            #     no — 保持 stale 让下次有 embedder 时再 recompute.
            return cache
        from explain_engine.engines.theory.recompute import _recompute_all
        cache = _recompute_all(
            sessions=current_sids, storage=storage, embedder=embedder,
            preserve_rejected=cache.rejected_theory_ids,
        )
        _atomic_write_cache(cache, cache_path)
    return cache


def reject_theory(storage, theory_id: str) -> bool:
    """加入 rejected_theory_ids, 持久化. theory 不删, 仅 mark.

    Returns:
        True 若 id 存在 + 被 mark (or already rejected, idempotent)
        False 若 id 不存在 in cache
    """
    cache_path = storage.knowledge_dir() / "theories.json"
    if not cache_path.exists():
        return False
    cache = _load_cache(cache_path)
    all_ids = {t.id for t in cache.tentative_theories + cache.stable_theories}
    if theory_id not in all_ids:
        return False
    if theory_id in cache.rejected_theory_ids:
        return True  # idempotent
    cache.rejected_theory_ids.add(theory_id)
    _atomic_write_cache(cache, cache_path)
    return True


def _atomic_write_cache(cache: TheoriesCache, path: Path) -> None:
    """临时文件 + rename, 防中断写到一半破损 JSON."""
    tmp = path.with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(_cache_to_dict(cache), indent=2, ensure_ascii=False))
    tmp.replace(path)


def _load_cache(path: Path) -> TheoriesCache:
    d = json.loads(path.read_text())
    return TheoriesCache(
        themes=[Theme(
            id=th["id"], name=th["name"],
            member_global_ids=tuple(th["member_global_ids"]),
            centroid_summary=th["centroid_summary"],
        ) for th in d.get("themes", [])],
        tentative_theories=[_theory_from_dict(t) for t in d.get("tentative_theories", [])],
        stable_theories=[_theory_from_dict(t) for t in d.get("stable_theories", [])],
        rejected_theory_ids=set(d.get("rejected_theory_ids", [])),
        session_ids_snapshot=d.get("session_ids_snapshot", []),
        cold_start_threshold=d.get("cold_start_threshold", 3),
        stability_window_size=d.get("stability_window_size", 5),
        computed_at=d.get("computed_at", ""),
    )


def _cache_to_dict(cache: TheoriesCache) -> dict:
    return {
        "version": "1.0", "computed_at": cache.computed_at,
        "session_ids_snapshot": cache.session_ids_snapshot,
        "cold_start_threshold": cache.cold_start_threshold,
        "stability_window_size": cache.stability_window_size,
        "themes": [_theme_to_dict(th) for th in cache.themes],
        "tentative_theories": [_theory_to_dict(t) for t in cache.tentative_theories],
        "stable_theories": [_theory_to_dict(t) for t in cache.stable_theories],
        "rejected_theory_ids": sorted(cache.rejected_theory_ids),
    }


def _theme_to_dict(th: Theme) -> dict:
    return {
        "id": th.id, "name": th.name,
        "member_global_ids": list(th.member_global_ids),
        "centroid_summary": th.centroid_summary,
    }


def _theory_to_dict(t: Theory) -> dict:
    return {
        "id": t.id, "motif_type": t.motif_type,
        "theme_ids": list(t.theme_ids), "node_ids": list(t.node_ids),
        "edges": [list(e) for e in t.edges],
        "supporting_sessions": list(t.supporting_sessions),
        "natural_language_summary": t.natural_language_summary,
        "structure_complexity": t.structure_complexity,
        "first_seen_session": t.first_seen_session,
        "last_seen_session": t.last_seen_session,
        "predictive_power": t.predictive_power,
        "stability_status": t.stability_status,
        "stable_promoted_at_session": t.stable_promoted_at_session,
    }


def _theory_from_dict(d: dict) -> Theory:
    return Theory(
        id=d["id"], motif_type=d["motif_type"],
        theme_ids=tuple(d["theme_ids"]), node_ids=tuple(d["node_ids"]),
        edges=tuple(tuple(e) for e in d["edges"]),
        supporting_sessions=tuple(d["supporting_sessions"]),
        natural_language_summary=d["natural_language_summary"],
        structure_complexity=d["structure_complexity"],
        first_seen_session=d["first_seen_session"],
        last_seen_session=d["last_seen_session"],
        predictive_power=d.get("predictive_power", 0.0),
        stability_status=d.get("stability_status", "tentative"),
        stable_promoted_at_session=d.get("stable_promoted_at_session"),
    )
