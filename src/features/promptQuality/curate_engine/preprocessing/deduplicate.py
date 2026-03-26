from __future__ import annotations

import difflib
from typing import Any

_SIM_THRESHOLD = 0.92


def deduplicate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not records:
        return []

    seen_exact: set[str] = set()
    unique: list[dict[str, Any]] = []
    for rec in records:
        nt = str(rec.get("normalized_text", "") or "")
        if not nt:
            continue
        if nt in seen_exact:
            continue
        seen_exact.add(nt)
        unique.append(rec)

    if len(unique) <= 1:
        return unique

    kept: list[dict[str, Any]] = []
    for rec in unique:
        nt = str(rec.get("normalized_text", "") or "")
        is_dup = False
        for other in kept:
            ot = str(other.get("normalized_text", "") or "")
            if difflib.SequenceMatcher(None, nt, ot).ratio() >= _SIM_THRESHOLD:
                is_dup = True
                break
        if not is_dup:
            kept.append(rec)
    return kept
