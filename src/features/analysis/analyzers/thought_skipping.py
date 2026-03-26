from __future__ import annotations

import re
from typing import Any

_REASONING_MARKERS = (
    "because",
    "therefore",
    "thus",
    "since",
    "first",
    "second",
    "third",
    "step",
    "hence",
    "so that",
    "as a result",
    "consequently",
    "this means",
    "for example",
    "specifically",
)


def detect_thought_skipping(response: str, *, task: dict[str, Any] | None = None) -> list[str]:
    """Heuristic detection of skipped intermediate reasoning."""
    r = (response or "").strip()
    if not r:
        return []
    low = r.lower()
    words = r.split()
    wc = len(words)
    sentences = [s.strip() for s in re.split(r"[.!?]+", r) if s.strip()]
    n_sent = max(1, len(sentences))

    tags: list[str] = []

    task_type = str((task or {}).get("type", "") or "").lower()
    complex_task = "reason" in task_type or task_type == "reasoning"

    has_marker = any(m in low for m in _REASONING_MARKERS)
    has_steps = bool(re.search(r"\b(1\.|2\.|first|second|step|finally)\b", low))

    if complex_task and wc < 18 and not has_marker and not has_steps:
        tags.append("THOUGHT_SKIPPING")

    if wc >= 30 and n_sent <= 1 and not has_marker:
        tags.append("THOUGHT_SKIPPING")

    if wc > 20 and not has_marker and not has_steps and n_sent <= 2:
        tags.append("THOUGHT_SKIPPING")

    return tags
