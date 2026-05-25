"""Phase 16: _recompute_all 完整 7-step orchestrator.

Pipeline:
  1. load_all_session_graphs(sids, storage)
  2. cluster_lexicon_themes(lexicon, embedder)
  3-4. find_motifs_per_theme(sessions, theme, min_freq) per theme
  5. evaluate_predictive_power(motif, sessions, embedder) → Theory (含 predictive_power)
  6. maybe_promote_to_stable(theory, sessions, window_size) → stable / tentative
  7. rank_topk_with_mmr(stable, k=20, lambda_=0.7) 排序
"""
from __future__ import annotations


def _recompute_all(sessions, storage, embedder, preserve_rejected):
    """完整 7-step pipeline.

    cold-start (sessions < threshold) 短路返 empty cache.
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

    # Step 1: load sessions
    from explain_engine.engines.theory.loader import load_all_session_graphs
    try:
        session_graphs = load_all_session_graphs(sessions, storage)
    except Exception:
        # session 加载失败 → 返 empty (best-effort)
        return TheoriesCache(
            rejected_theory_ids=preserve_rejected,
            session_ids_snapshot=sessions,
            cold_start_threshold=cold_start,
            stability_window_size=window_size,
            computed_at=_now_iso(),
        )

    # Step 2: theme cluster
    from explain_engine.engines.theory.clustering import cluster_lexicon_themes
    lexicon = _load_lexicon(storage)
    themes = cluster_lexicon_themes(lexicon, embedder, cosine_threshold=0.85)

    if not themes:
        return TheoriesCache(
            themes=[],
            rejected_theory_ids=preserve_rejected,
            session_ids_snapshot=sessions,
            cold_start_threshold=cold_start,
            stability_window_size=window_size,
            computed_at=_now_iso(),
        )

    # Step 3-4: per-theme motif mining
    from explain_engine.engines.theory.motif_mining import find_motifs_per_theme
    raw_motifs = []
    for theme in themes:
        raw_motifs.extend(find_motifs_per_theme(session_graphs, theme, min_freq=cold_start))

    # Step 5: predictive_power evaluation (JEPA a)
    from explain_engine.engines.theory.falsifiability import evaluate_predictive_power
    from explain_engine.engines.theory.theory import Theory, _compute_theory_id

    def _resolve_theme_ids(motif, themes_list):
        """motif.nodes 内 lexicon global_id 反查它涉及的 theme.id list."""
        node_set = set(motif.nodes)
        return tuple(
            th.id for th in themes_list
            if node_set & set(th.member_global_ids)
        )

    def _generate_summary(motif, themes_list, _lex):
        """natural_language_summary 简化: " → ".join(theme name 或 var name)."""
        theme_names = []
        for th in themes_list:
            if set(motif.nodes) & set(th.member_global_ids):
                theme_names.append(th.name)
        return " → ".join(theme_names) if theme_names else "motif"

    enriched: list[Theory] = []
    for m in raw_motifs:
        theory_id = _compute_theory_id(m.motif_type, m.edges)
        predictive_power = evaluate_predictive_power(m, session_graphs, embedder)
        sorted_supporting = sorted(m.supporting_sessions)
        enriched.append(Theory(
            id=theory_id,
            motif_type=m.motif_type,
            theme_ids=_resolve_theme_ids(m, themes),
            node_ids=m.nodes,
            edges=m.edges,
            supporting_sessions=tuple(sorted_supporting),
            natural_language_summary=_generate_summary(m, themes, lexicon),
            structure_complexity=len(m.nodes),
            first_seen_session=sorted_supporting[0] if sorted_supporting else "",
            last_seen_session=sorted_supporting[-1] if sorted_supporting else "",
            predictive_power=predictive_power,
        ))

    # Step 6: promote stable / tentative (JEPA b)
    from explain_engine.engines.theory.ranking import (
        compute_score,
        maybe_promote_to_stable,
        rank_topk_with_mmr,
    )
    tentative: list[Theory] = []
    stable: list[Theory] = []
    for t in enriched:
        if maybe_promote_to_stable(t, sessions, window_size):
            # 复制 stability_status 升级 (Theory frozen, 构造新实例)
            stable.append(Theory(
                id=t.id,
                motif_type=t.motif_type,
                theme_ids=t.theme_ids,
                node_ids=t.node_ids,
                edges=t.edges,
                supporting_sessions=t.supporting_sessions,
                natural_language_summary=t.natural_language_summary,
                structure_complexity=t.structure_complexity,
                first_seen_session=t.first_seen_session,
                last_seen_session=t.last_seen_session,
                predictive_power=t.predictive_power,
                stability_status="stable",
                stable_promoted_at_session=sessions[-1],
            ))
        else:
            tentative.append(t)

    # Step 7: MMR ranking — 只对 stable 排, tentative 全保留 (按 score 排)
    n_total = len(sessions)
    stable = rank_topk_with_mmr(stable, k=20, lambda_=0.7, n_sessions_total=n_total)
    tentative = sorted(tentative, key=lambda t: -compute_score(t, n_total))[:20]

    return TheoriesCache(
        themes=themes,
        tentative_theories=tentative,
        stable_theories=stable,
        rejected_theory_ids=preserve_rejected,
        session_ids_snapshot=sessions,
        cold_start_threshold=cold_start,
        stability_window_size=window_size,
        computed_at=_now_iso(),
    )


def _load_lexicon(storage) -> dict:
    """加载 ~/.explain/projects/<proj>/knowledge/variables.json (Phase 10 lexicon).

    无 file 时返 empty lexicon (不致错, 让 cluster 自然返 empty themes).
    """
    import json
    path = storage.knowledge_dir() / "variables.json"
    if not path.exists():
        return {"version": "1.0", "variables": []}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"version": "1.0", "variables": []}
