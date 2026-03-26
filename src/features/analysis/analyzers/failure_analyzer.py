from __future__ import annotations

import re
from typing import Any, Literal

from src.features.analysis.analyzers.contradiction_detector import detect_contradictions
from src.features.analysis.analyzers.shortcut_detector import detect_shortcuts
from src.features.analysis.analyzers.thought_skipping import detect_thought_skipping

FailureSeverity = Literal["low", "medium", "high"]

TAXONOMY = (
    "THOUGHT_SKIPPING",
    "CONTRADICTION",
    "SHORTCUT_REASONING",
    "INCOMPLETE_REASONING",
    "IRRELEVANT_OUTPUT",
)


def _dedupe(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t in TAXONOMY and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _detect_incomplete(response: str) -> list[str]:
    r = (response or "").strip()
    if not r:
        return ["INCOMPLETE_REASONING"]
    if len(r.split()) < 4:
        return ["INCOMPLETE_REASONING"]
    return []


def _detect_irrelevant(task: dict[str, Any], response: str, expected_output: str) -> list[str]:
    r = (response or "").strip()
    if not r:
        return []
    low = r.lower()
    blob = " ".join(
        str(task.get(k, "") or "")
        for k in ("input", "question", "context")
        if task.get(k)
    ).lower()
    exp = (expected_output or "").lower()
    keywords = set(re.findall(r"[a-z]{4,}", blob + " " + exp))
    keywords.discard("that")
    keywords.discard("this")
    keywords.discard("what")
    keywords.discard("when")
    keywords.discard("where")
    keywords.discard("which")
    keywords.discard("answer")
    keywords.discard("question")
    keywords.discard("context")
    if len(keywords) < 2:
        return []
    hits = sum(1 for k in keywords if k in low)
    if hits == 0 and len(r) > 15:
        return ["IRRELEVANT_OUTPUT"]
    return []


def _severity(tags: list[str]) -> FailureSeverity:
    if "CONTRADICTION" in tags:
        return "high"
    if len(tags) >= 3:
        return "high"
    if "SHORTCUT_REASONING" in tags or "IRRELEVANT_OUTPUT" in tags:
        return "medium"
    if "THOUGHT_SKIPPING" in tags or "INCOMPLETE_REASONING" in tags:
        return "medium"
    if tags:
        return "low"
    return "low"


def analyze_failures(
    task: dict[str, Any],
    response: str,
    expected_output: str,
) -> dict[str, Any]:
    tags: list[str] = []
    tags.extend(detect_thought_skipping(response, task=task))
    tags.extend(detect_contradictions(response))
    tags.extend(detect_shortcuts(response, expected_output))
    tags.extend(_detect_incomplete(response))
    tags.extend(_detect_irrelevant(task, response, expected_output))
    tags = _dedupe(tags)
    return {
        "failure_tags": tags,
        "severity": _severity(tags),
    }
