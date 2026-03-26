from __future__ import annotations

import re
from collections import Counter
from typing import Any

from src.utils.config_loader import load_config
from src.utils.paths import project_root

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than",
    "this", "that", "these", "those", "is", "are", "was", "were",
    "be", "been", "being", "to", "of", "in", "on", "for", "with",
    "as", "at", "by", "it", "its", "from",
}

REASONING_WORDS = {
    "because",
    "therefore",
    "thus",
    "so",
    "hence",
    "since",
    "thereby",
    "consequently",
    "then",
}

STEP_MARKERS = {
    "first",
    "second",
    "third",
    "next",
    "then",
    "finally",
    "lastly",
}


def _config_path() -> str:
    return str(project_root() / "configs" / "base.yaml")


def load_evaluation_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if config is not None:
        return config
    return load_config(_config_path())


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _tokens(text: str) -> list[str]:
    return re.findall(r"\b[\w']+\b", text.lower())


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _negation_words(cfg: dict[str, Any]) -> set[str]:
    m2 = cfg.get("evaluation", {}).get("m2", {})
    words = m2.get("negation_words")
    if isinstance(words, list):
        return {str(w).lower() for w in words}
    return {
        "not", "no", "never", "none", "nor", "neither", "nothing", "nowhere",
    }


def _negation_count(sentence: str, neg_words: set[str]) -> int:
    tokens = _tokens(sentence)
    n = 0
    for t in tokens:
        if t in neg_words or t.endswith("n't"):
            n += 1
    return n


def _core_content_tokens(sentence: str, neg_words: set[str]) -> set[str]:
    tokens = _tokens(sentence)
    out: set[str] = set()
    for t in tokens:
        if t in neg_words or t.endswith("n't"):
            continue
        if len(t) > 2 and t not in STOPWORDS:
            out.add(t)
    return out


def _pair_contradicts(s1: str, s2: str, cfg: dict[str, Any]) -> bool:
    m2 = cfg.get("evaluation", {}).get("m2", {})
    neg_words = _negation_words(cfg)
    min_shared = int(m2.get("pair_min_shared_tokens", 2))
    jmin = float(m2.get("pair_jaccard_min", 0.32))

    c1 = _core_content_tokens(s1, neg_words)
    c2 = _core_content_tokens(s2, neg_words)
    shared = c1 & c2
    if len(shared) < min_shared:
        return False
    union = c1 | c2
    if not union:
        return False
    jacc = len(shared) / len(union)
    if jacc < jmin:
        return False
    n1 = _negation_count(s1, neg_words)
    n2 = _negation_count(s2, neg_words)
    return (n1 % 2) != (n2 % 2)


def _same_sentence_always_never(sentence: str) -> bool:
    sl = sentence.lower()
    return "always" in sl and "never" in sl


def detect_thought_skipping(text: str) -> float:
    sentences = _sentences(text)
    tokens = set(_tokens(text))

    if len(sentences) > 1 and not any(word in tokens for word in REASONING_WORDS):
        return 1.0

    return 0.0


def compute_m1(text: str, config: dict[str, Any] | None = None) -> float:
    cfg = load_evaluation_config(config)
    m1 = cfg.get("evaluation", {}).get("m1", {})
    w = m1.get("weights", {})
    ws = float(w.get("sentence", 0.35))
    wr = float(w.get("reasoning", 0.35))
    wst = float(w.get("structure", 0.30))
    skip_fac = float(m1.get("skip_penalty_factor", 0.7))
    rn = float(m1.get("reasoning_norm", 4.0))
    sss = float(m1.get("single_sentence_score", 0.4))
    ideal = float(m1.get("ideal_sentences", 4))
    sdd = float(m1.get("sentence_decay_divisor", 6.0))
    s2 = float(m1.get("structure_two_sent", 0.4))
    s3 = float(m1.get("structure_three_sent", 0.3))
    stw = float(m1.get("structure_step_weight", 0.3))
    std = float(m1.get("structure_step_divisor", 2.0))

    tokens = _tokens(text)
    sentences = _sentences(text)

    if not tokens:
        return 0.0

    skip_penalty = detect_thought_skipping(text)
    sentence_count = len(sentences)
    reasoning_hits = len({tok for tok in tokens if tok in REASONING_WORDS})
    step_hits = sum(
        1 for marker in STEP_MARKERS
        if re.search(rf"\b{re.escape(marker)}\b", text.lower())
    )

    if sentence_count == 1:
        sentence_score = sss
    elif 2 <= sentence_count <= 5:
        sentence_score = 1.0
    else:
        sentence_score = max(0.0, 1.0 - abs(sentence_count - ideal) / sdd)

    reasoning_score = min(1.0, (reasoning_hits + step_hits) / rn)

    structure_score = 0.0
    if sentence_count >= 2:
        structure_score += s2
    if sentence_count >= 3:
        structure_score += s3
    structure_score += stw * min(1.0, step_hits / std)
    structure_score = _clamp01(structure_score)

    score = (
        ws * sentence_score
        + wr * reasoning_score
        + wst * structure_score
    )
    if skip_penalty > 0:
        score *= skip_fac

    return _clamp01(score)


def compute_m2(text: str, config: dict[str, Any] | None = None) -> float:
    cfg = load_evaluation_config(config)
    m2 = cfg.get("evaluation", {}).get("m2", {})
    w_rep = float(m2.get("weights", {}).get("repetition", 0.60))
    w_con = float(m2.get("weights", {}).get("contradiction", 0.40))
    floor = float(m2.get("contradiction_floor", 0.2))
    min_len = int(m2.get("min_content_token_len", 3))
    anchor_div = float(m2.get("anchor_ratio_divisor", 0.3))
    ow = float(m2.get("overlap_weight", 0.7))
    aw = float(m2.get("anchor_weight", 0.3))
    dpw = float(m2.get("duplicate_penalty_weight", 0.5))
    onorm = float(m2.get("overlap_norm", 0.25))

    tokens = _tokens(text)
    sentences = _sentences(text)

    if not tokens:
        return 0.0

    content_tokens = [t for t in tokens if len(t) > min_len and t not in STOPWORDS]
    counts = Counter(content_tokens)

    repeated_concepts = sum(1 for c in counts.values() if c >= 2)
    anchor_ratio = repeated_concepts / max(len(counts), 1)

    if len(sentences) < 2:
        overlap_score = 1.0
        duplicate_penalty = 0.0
    else:
        sent_sets = [
            {t for t in _tokens(s) if len(t) > min_len and t not in STOPWORDS}
            for s in sentences
        ]
        overlaps = [
            _jaccard(sent_sets[i], sent_sets[i + 1])
            for i in range(len(sent_sets) - 1)
        ]
        avg_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0

        overlap_score = min(1.0, avg_overlap / onorm)

        duplicate_count = len(sentences) - len({s.lower() for s in sentences})
        duplicate_penalty = duplicate_count / len(sentences)

    repetition_stability = _clamp01(
        ow * overlap_score
        + aw * min(1.0, anchor_ratio / anchor_div)
        - dpw * duplicate_penalty
    )

    contradiction_hits = 0
    if m2.get("same_sentence_always_never", True):
        for s in sentences:
            if _same_sentence_always_never(s):
                contradiction_hits += 1

    for i in range(len(sentences)):
        for j in range(i + 1, len(sentences)):
            if _pair_contradicts(sentences[i], sentences[j], cfg):
                contradiction_hits += 1

    if contradiction_hits > 0:
        return floor

    contradiction_score = 1.0

    score = w_rep * repetition_stability + w_con * contradiction_score
    return _clamp01(score)
