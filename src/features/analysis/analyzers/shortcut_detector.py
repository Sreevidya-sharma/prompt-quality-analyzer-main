from __future__ import annotations

import re

_GENERIC_PATTERNS = (
    "it depends",
    "as an ai",
    "as a language model",
    "i cannot",
    "i can't",
    "i don't know",
    "cannot say",
    "not able to",
    "no single answer",
    "there is no one",
)


def detect_shortcuts(response: str, expected_output: str) -> list[str]:
    """Heuristic detection of guess-like or overly generic answers."""
    r = (response or "").strip()
    if not r:
        return []
    low = r.lower()
    tags: list[str] = []

    if any(p in low for p in _GENERIC_PATTERNS):
        tags.append("SHORTCUT_REASONING")

    wc = len(r.split())
    exp = (expected_output or "").strip()
    exp_wc = len(exp.split()) if exp else 0

    if exp_wc >= 4 and wc <= 3:
        tags.append("SHORTCUT_REASONING")

    if exp and exp_wc > 0:
        exp_tokens = set(re.findall(r"[a-z0-9]{3,}", exp.lower()))
        resp_tokens = set(re.findall(r"[a-z0-9]{3,}", low))
        if exp_tokens and len(resp_tokens & exp_tokens) == 0 and wc <= 12:
            tags.append("SHORTCUT_REASONING")

    if re.match(r"^(yes|no|maybe|perhaps|sure|ok|n/a)\.?$", low.strip()):
        if exp_wc > 2:
            tags.append("SHORTCUT_REASONING")

    return tags
