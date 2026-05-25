"""Phase 16: _recompute_all 7-step orchestrator (scaffold). Task 12 填完整 pipeline."""
from __future__ import annotations


def _recompute_all(sessions, storage, embedder, preserve_rejected):
    """完整 7-step pipeline. Task 12 实施.

    当前 scaffold: cold-start 路径 (sessions < threshold) 返 empty cache.
    完整 pipeline 在 Task 12.
    """
    from explain_engine.engines.theory.cache import TheoriesCache, _now_iso
    cold_start = max(3, len(sessions) // 3)
    window_size = 5

    if len(sessions) < cold_start:
        return TheoriesCache(
            rejected_theory_ids=preserve_rejected,
            session_ids_snapshot=sessions,
            cold_start_threshold=cold_start,
            stability_window_size=window_size,
            computed_at=_now_iso(),
        )

    # TODO Task 12: load sessions → cluster → motif → predict → promote → rank
    return TheoriesCache(
        rejected_theory_ids=preserve_rejected,
        session_ids_snapshot=sessions,
        cold_start_threshold=cold_start,
        stability_window_size=window_size,
        computed_at=_now_iso(),
    )
