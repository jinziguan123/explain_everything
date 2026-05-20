"""Phase 13 Wave 3: compute reuse vs new statistics for /compress UI.

After `propose_candidates` adds new L1 c_xxx nodes to the session graph,
this module embeds each candidate's `f"{name} - {description}"` text and
compares to existing lexicon embeddings. Returns counts of how many
candidates are cosine-similar to existing lexicon entries vs truly new.

Used by W3.4 `/compress` slash UI to display:
    Compress: X candidates → Y reused (embedding) / Z new

Not coupled to actual lexicon merge — purely observational. Real merge
happens later in flush_to_lexicon (Phase 13 W2.3).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import numpy as np

from explain_engine.engines.lexicon import (
    _build_embeddings_matrix,
    _load_lexicon,
)
from explain_engine.engines.lexicon_merge import LEXICON_MERGE_THRESHOLD, find_duplicate

if TYPE_CHECKING:
    from explain_engine.persistence.storage_v2 import StorageV2
    from explain_engine.schema.state import CognitiveState


def compute_compress_dedup_stats(
    state: CognitiveState,
    storage: StorageV2,
    new_node_ids: list[str],
    display_threshold: float | None = None,
) -> dict[str, int]:
    """Phase 13 Wave 3 Task 3: count how many new candidates are cosine-dup of lexicon.

    For each node id in `new_node_ids`, build proxy text from name + description,
    embed via BGE-M3, check cosine vs existing lexicon embeddings. Count
    reused (cos > threshold) vs new.

    Args:
        state: CognitiveState (for graph node lookup).
        storage: StorageV2 (for lexicon JSON path).
        new_node_ids: IDs of newly added L1 candidate nodes (from propose_candidates).
        display_threshold: Optional cosine cutoff for UI counting. Defaults to
            LEXICON_MERGE_THRESHOLD (0.85, identical to real merge). Caller can
            pass a lower value (e.g. 0.75) for a more honest reuse count in
            `/compress` UI — the proxy "name - description" text format
            mismatches the lexicon's canonical_mechanism format, which
            artificially lowers cosine by ~0.1 vs what real merge sees. This
            param decouples UI display from the binding merge threshold.

    Returns:
        {"reused": N, "new": M} where N + M == len(new_node_ids).
        Missing node IDs / EXPLAIN_EMBEDDING_DISABLED=1 / embedder failure /
        empty lexicon → all "new".
    """
    if not new_node_ids:
        return {"reused": 0, "new": 0}

    # Fallback: disabled env → no embedding load
    if os.environ.get("EXPLAIN_EMBEDDING_DISABLED") == "1":
        return {"reused": 0, "new": len(new_node_ids)}

    # Load lexicon + matrix
    path = storage.knowledge_dir() / "variables.json"
    lexicon = _load_lexicon(path)
    matrix, _id_to_idx = _build_embeddings_matrix(lexicon)
    if matrix.shape[0] == 0:
        # Empty lexicon — nothing to dedup against
        return {"reused": 0, "new": len(new_node_ids)}

    # Build proxy texts for each candidate
    texts: list[str] = []
    valid_ids: list[str] = []
    for nid in new_node_ids:
        node = state.graph.nodes.get(nid)
        if node is None:
            continue
        valid_ids.append(nid)
        # TODO(W3.4): proxy "name - description" format mismatches lexicon's
        # canonical_mechanism format → cosine artificially lowered by ~0.1.
        # Caller can pass display_threshold=0.75 for more honest reuse count.
        texts.append(f"{node.name} - {node.description}")

    if not texts:
        return {"reused": 0, "new": len(new_node_ids)}

    # Batch embed (best-effort)
    try:
        from explain_engine.embedding.bge_m3 import get_embedder
        embedder = get_embedder()
        vecs = embedder.embed(texts)
    except Exception as exc:
        logging.warning(
            f"compute_compress_dedup_stats embed failed: {type(exc).__name__}: {exc}. "
            "Falling back to all-new."
        )
        return {"reused": 0, "new": len(new_node_ids)}

    # Count reused vs new
    threshold = (
        display_threshold
        if display_threshold is not None
        else LEXICON_MERGE_THRESHOLD
    )
    reused = 0
    new_count = 0
    for vec in vecs:
        emb = np.asarray(vec, dtype=np.float32)
        if find_duplicate(emb, matrix, threshold=threshold) is not None:
            reused += 1
        else:
            new_count += 1
    # Account for missing nodes (treat as new — they don't exist to be dup)
    missing = len(new_node_ids) - len(valid_ids)
    new_count += missing
    return {"reused": reused, "new": new_count}
