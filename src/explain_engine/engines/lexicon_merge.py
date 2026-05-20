"""Phase 13 Wave 2: cosine similarity merge logic for lexicon dedup."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np

LEXICON_MERGE_THRESHOLD = 0.85
"""Cosine similarity threshold for merging lexicon entries.

Hard-coded MVP. BGE-M3 中文同义句典型 cosine 0.85+. False merge 风险
低于 0.05 在 manual smoke; 边界 case 由 audit log 事后修正.
"""


def find_duplicate(
    new_emb: np.ndarray,
    existing_matrix: np.ndarray,
    threshold: float = LEXICON_MERGE_THRESHOLD,
) -> int | None:
    """Return existing_matrix row index of max cosine sim if > threshold, else None.

    Args:
        new_emb: shape (1024,), candidate vector
        existing_matrix: shape (N, 1024), N = lexicon entries with embedding
        threshold: cosine cutoff (default 0.85)

    Returns:
        int idx if max(cos) > threshold, else None
    """
    if existing_matrix.shape[0] == 0:
        return None

    # Cosine
    new_norm = np.linalg.norm(new_emb)
    existing_norms = np.linalg.norm(existing_matrix, axis=1)
    denoms = existing_norms * new_norm
    denoms = np.maximum(denoms, 1e-9)  # avoid div0

    sims = (existing_matrix @ new_emb) / denoms
    max_idx = int(np.argmax(sims))
    return max_idx if sims[max_idx] > threshold else None


def write_merge_audit(
    log_dir: Path,
    merged_into: str,
    merged_from: str,
    sim: float,
    evidence_ids: list[str],
) -> None:
    """Append JSONL record to logs/lexicon_merge_<YYYY-MM-DD>.jsonl.

    Each merge writes 1 line for post-hoc audit. Failures swallowed
    (warning) — audit log shouldn't block lexicon writes.

    Args:
        log_dir: directory containing the date-stamped JSONL file
            (auto-created if missing)
        merged_into: lexicon entry global_id that absorbed evidence
        merged_from: canonical_mechanism (or short summary) of the
            new candidate that was merged
        sim: cosine similarity at merge time
        evidence_ids: list of evidence ids newly appended
    """
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_path = log_dir / f"lexicon_merge_{date_str}.jsonl"
        record = {
            "timestamp": datetime.now().isoformat(),
            "merged_into": merged_into,
            "merged_from": merged_from,
            "sim": float(sim),
            "evidence_ids": evidence_ids,
        }
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logging.warning(f"audit log write failed: {exc}")
