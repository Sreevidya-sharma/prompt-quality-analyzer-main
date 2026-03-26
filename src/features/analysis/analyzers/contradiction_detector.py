from __future__ import annotations

import re


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def detect_contradictions(response: str) -> list[str]:
    """Heuristic detection of opposing statements within one response."""
    r = (response or "").strip()
    if not r:
        return []
    sents = _sentences(r)
    if len(sents) < 2:
        low = r.lower()
        if " always " in f" {low} " and " never " in f" {low} ":
            return ["CONTRADICTION"]
        if re.search(r"\bis\b", low) and re.search(r"\bis not\b|\bisn't\b|\bnot\b", low):
            if len(r) > 80:
                return ["CONTRADICTION"]
        return []

    joined = " " + " ".join(s.lower() for s in sents) + " "
    if " always " in joined and " never " in joined:
        return ["CONTRADICTION"]

    for i, a in enumerate(sents):
        la = a.lower()
        for b in sents[i + 1 :]:
            lb = b.lower()
            if _pair_contradicts(la, lb):
                return ["CONTRADICTION"]
    return []


def _pair_contradicts(a: str, b: str) -> bool:
    pairs = [
        (" increases ", " decreases "),
        (" improves ", " worsens "),
        (" is true", " is false"),
        (" beneficial ", " harmful "),
        (" helps ", " harms "),
        (" yes ", " no "),
    ]
    for x, y in pairs:
        if x in f" {a} " and y in f" {b} ":
            return True
        if y in f" {a} " and x in f" {b} ":
            return True

    if re.search(r"\bis\b", a) and re.search(r"\bis not\b|\bisn't\b", b):
        toks_a = set(re.findall(r"[a-z]{4,}", a))
        toks_b = set(re.findall(r"[a-z]{4,}", b))
        if len(toks_a & toks_b) >= 2:
            return True
    return False
