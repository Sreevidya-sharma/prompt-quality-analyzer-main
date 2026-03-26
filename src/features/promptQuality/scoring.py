from __future__ import annotations

import logging
import re
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src.utils.config_loader import load_config
from src.utils.paths import project_root

logger = logging.getLogger(__name__)


def _config_path() -> str:
    return str(project_root() / "configs" / "base.yaml")


def load_scoring_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if config is not None:
        return config
    return load_config(_config_path())


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _tokens(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]


def _normalize_action_text(text: str) -> str:
    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    # Ignore leading newlines, bullets, and similar formatting noise.
    cleaned_lines = [
        re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s*", "", line)
        for line in text.splitlines()
    ]
    clean = " ".join(cleaned_lines).strip().lower()
    clean = re.sub(r"\s+", " ", clean)
    return clean


def _has_meaningful_action(clean: str) -> bool:
    action_words = ["explain", "list", "compare", "describe", "analyze", "give", "show", "outline"]
    return any(word in clean for word in action_words)


def _compute_prompt_sq(
    text: str,
    *,
    has_action: bool,
    has_constraint: bool,
) -> float:
    tokens = _tokens(text)
    if not tokens:
        return 0.0

    token_count = len(tokens)
    unique_ratio = len(set(tokens)) / max(token_count, 1)
    sentence_count = len(_sentences(text))
    action_score = 1.0 if has_action else 0.0
    structure_score = 1.0 if has_constraint else (0.75 if sentence_count >= 1 and token_count >= 4 else 0.4)
    specificity_score = _clamp01(token_count / 6.0)
    clarity_score = _clamp01(unique_ratio / 0.85)

    return _clamp01(
        0.45 * action_score
        + 0.25 * structure_score
        + 0.20 * specificity_score
        + 0.10 * clarity_score
    )


def _tfidf_cosine(a: str, b: str) -> float:
    if not a.strip() or not b.strip():
        return 0.0

    a_expanded = a + " " + b
    mat = TfidfVectorizer().fit_transform([a_expanded, b])
    return float(cosine_similarity(mat[0:1], mat[1:2])[0, 0])


def _is_gibberish(text: str, cur: dict[str, Any]) -> bool:
    words = re.findall(r"\b\w+\b", text.lower())
    if not words:
        return True
    min_wl = int(cur.get("gibberish_min_word_len", 5))
    max_vr = float(cur.get("gibberish_max_vowel_ratio", 0.12))
    run = int(cur.get("gibberish_repeat_char_run", 4))
    run_pat = re.compile(r"(.)\1{" + str(max(2, run - 1)) + r",}")

    bad_long = 0
    for w in words:
        if run_pat.search(w):
            return True
        if len(w) >= min_wl:
            vowels = sum(1 for c in w if c in "aeiou")
            if vowels / max(len(w), 1) < max_vr:
                bad_long += 1

    return bad_long >= max(1, (len(words) + 1) // 2)


def compute_ed(text: str, keywords: list[str], config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = load_scoring_config(config)
    edc = cfg["ed"]
    t_ed = float(cfg["thresholds"]["ed_threshold"])

    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    text = text.strip()

    tokens = _tokens(text)
    spam_penalty = 0.0

    if "!!!" in text or (len(text) > 3 and text.isupper()):
        spam_penalty += float(edc.get("spam_penalty_exclaim", 0.3))

    n_tok = len(tokens)
    if n_tok > 0:
        uniq_ratio = len(set(tokens)) / n_tok
        if uniq_ratio < 0.5:
            spam_penalty += float(edc.get("spam_penalty_repeat_ratio", 0.3))
        if uniq_ratio < float(edc.get("spam_heavy_unique_ratio", 0.35)):
            spam_penalty += float(edc.get("spam_heavy_penalty", 0.5))

    sentences = _sentences(text)
    n = n_tok
    s = len(sentences)

    keyword_tokens = {tok for kw in keywords for tok in _tokens(kw)}
    h = sum(1 for tok in tokens if tok in keyword_tokens)
    d = float(h) / max(n, 1)

    d_min = float(edc.get("kw_density_min", 0.05))
    d_max = float(edc.get("kw_density_max", 0.12))
    d_div = float(edc.get("kw_penalty_divisor", 0.12))
    tmin = int(edc.get("token_len_ideal_min", 40))
    tmax = int(edc.get("token_len_ideal_max", 120))
    t_under = float(edc.get("token_len_under_scale", 40.0))
    t_over = float(edc.get("token_len_over_scale", 120.0))
    smin = int(edc.get("sent_ideal_min", 2))
    smax = int(edc.get("sent_ideal_max", 5))
    s_under = float(edc.get("sent_under_scale", 2.0))
    s_over = float(edc.get("sent_over_scale", 5.0))

    w_len = float(edc.get("weight_len", 0.35))
    w_rep = float(edc.get("weight_rep", 0.30))
    w_kw = float(edc.get("weight_kw", 0.25))
    w_sent = float(edc.get("weight_sent", 0.10))

    if n == 0:
        f_len = f_rep = f_sent = 0.0
    else:
        f_len = (
            1.0
            if tmin <= n <= tmax
            else (n / t_under if n < tmin else max(0.0, 1.0 - (n - tmax) / t_over))
        )
        f_rep = len(set(tokens)) / n
        f_sent = (
            1.0
            if smin <= s <= smax
            else (s / s_under if s < smin else max(0.0, 1.0 - (s - smax) / s_over))
        )

    if d_min <= d <= d_max:
        f_kw = 1.0
    else:
        f_kw = max(0.0, 1.0 - min(abs(d - d_min), abs(d - d_max)) / d_div)

    ed = (
        w_len * _clamp01(f_len)
        + w_rep * _clamp01(f_rep)
        + w_kw * f_kw
        + w_sent * _clamp01(f_sent)
    ) - spam_penalty

    ed = _clamp01(ed)

    return {
        "score": ed,
        "threshold": t_ed,
        "passes": ed >= t_ed,
        "token_count": n,
        "sentence_count": s,
    }


def compute_sq(text: str, prompt: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = load_scoring_config(config)
    sqc = cfg["sq"]
    t_sq = float(cfg["thresholds"]["sq_threshold"])

    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    if not isinstance(prompt, str):
        prompt = str(prompt) if prompt is not None else ""
    text = text.strip()
    prompt = prompt.strip()

    tokens = _tokens(text)
    unique_tokens = len(set(tokens))
    total_tokens = len(tokens)

    info_density = unique_tokens / max(total_tokens, 1)
    low_info_penalty = 0.0

    lit = float(sqc.get("low_info_density_threshold", 0.5))
    if info_density < lit:
        low_info_penalty += float(sqc.get("low_info_penalty_density", 0.2))

    mtl = int(sqc.get("min_tokens_low_info", 5))
    if total_tokens < mtl:
        low_info_penalty += float(sqc.get("low_info_penalty_short", 0.3))

    sentences = _sentences(text)
    f_rel = _tfidf_cosine(prompt, text)

    w_rel = float(sqc.get("weight_rel", 0.35))
    w_coh = float(sqc.get("weight_coh", 0.25))
    w_read = float(sqc.get("weight_read", 0.20))
    w_red = float(sqc.get("weight_red", 0.10))
    w_id = float(sqc.get("weight_info_density", 0.10))

    if not tokens:
        c_raw = f_read = f_red = 0.0
    else:
        avg_sent_len = len(tokens) / max(len(sentences), 1)
        avg_word_len = sum(len(t) for t in tokens) / len(tokens)

        ast = float(sqc.get("avg_sent_len_target", 20.0))
        ass = float(sqc.get("avg_sent_len_scale", 20.0))
        awt = float(sqc.get("avg_word_len_target", 6.0))
        aws = float(sqc.get("avg_word_len_scale", 4.0))
        rw_s = float(sqc.get("read_sent_weight", 0.6))
        rw_w = float(sqc.get("read_word_weight", 0.4))

        sent_term = 1.0 if avg_sent_len <= ast else max(0.0, 1.0 - (avg_sent_len - ast) / ass)
        word_term = 1.0 if avg_word_len <= awt else max(0.0, 1.0 - (avg_word_len - awt) / aws)
        f_read = rw_s * sent_term + rw_w * word_term

        floor = float(sqc.get("coherence_floor", 0.3))
        onorm = float(sqc.get("overlap_norm", 0.25))

        if len(sentences) < 2:
            c_raw = 1.0
            f_red = 1.0
        else:
            mat = TfidfVectorizer().fit_transform(sentences)
            sims = cosine_similarity(mat)

            consec = [float(sims[i, i + 1]) for i in range(len(sentences) - 1)]
            pairs = [
                float(sims[i, j])
                for i in range(len(sentences))
                for j in range(i + 1, len(sentences))
            ]

            c_raw = max(floor, sum(consec) / len(consec))
            f_red = max(0.0, 1.0 - (sum(pairs) / len(pairs)))

    f_coh = _clamp01(c_raw)

    sq = (
        w_rel * _clamp01(f_rel)
        + w_coh * f_coh
        + w_read * _clamp01(f_read)
        + w_red * _clamp01(f_red)
        + w_id * info_density
    ) - low_info_penalty

    sq = _clamp01(sq)

    return {
        "score": sq,
        "threshold": t_sq,
        "passes": sq >= t_sq,
    }


def compute_score(
    prompt_text: str,
    keywords: list[str],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    print("SCORING FILE USED:", __file__)
    logger.info("SCORING FILE USED: %s", __file__)
    result = curate_text(prompt_text, keywords, prompt_text, config)
    print("SQ:", float(result["sq"]["score"]), "ED:", float(result["ed"]["score"]))
    logger.info("SQ: %s ED: %s", float(result["sq"]["score"]), float(result["ed"]["score"]))
    return result


def curate_text(
    text: str,
    keywords: list[str],
    prompt: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = load_scoring_config(config)
    cur = cfg["curate"]
    thr = cfg["thresholds"]

    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    text = text.strip()

    t_ed = float(thr["ed_threshold"])
    t_sq = float(thr["sq_threshold"])
    min_words = int(cur.get("min_words", 3))
    reason_short = str(cur.get("reason_too_short", "too short"))

    def _reject(short_ed: float, short_sq: float, decision: str, reason: str, suggestion: str) -> dict[str, Any]:
        return {
            "decision": decision,
            "reason": reason,
            "suggestion": suggestion,
            "ed": {"score": short_ed, "threshold": t_ed, "passes": short_ed >= t_ed},
            "sq": {"score": short_sq, "threshold": t_sq, "passes": short_sq >= t_sq},
        }

    if not text:
        return _reject(0.0, 0.0, "reject", reason_short, "Provide a non-empty response.")

    tokens = _tokens(text)
    if len(tokens) < min_words:
        return _reject(0.2, 0.2, "reject", reason_short, "Add more detail and a clear task (e.g., explain, list, steps)")

    if _is_gibberish(text, cur):
        return _reject(
            0.15,
            0.15,
            "reject",
            str(cur.get("reason_gibberish", "Non-meaningful or random text")),
            "Use real words and coherent sentences.",
        )

    text_lower = text.lower()
    prompt_clean = _normalize_action_text(prompt)
    text_clean = _normalize_action_text(text)
    clean = prompt_clean or text_clean
    has_action = _has_meaningful_action(clean) or _has_meaningful_action(text_clean)
    print("Processed text:", clean)
    print("Action detected:", has_action)
    logger.info("Processed text: %s", clean)
    logger.info("Action detected: %s", has_action)

    intent_keywords = ["how", "what", "steps", "recipe"]
    has_intent = has_action or any(word in clean for word in intent_keywords) or any(
        word in text_lower for word in intent_keywords
    )

    constraint_keywords = ["step", "time", "example", "detail", "include"]
    has_constraint = any(word in clean for word in constraint_keywords) or any(
        word in text_lower for word in constraint_keywords
    )

    ed_result = compute_ed(text, keywords, cfg)
    prompt_sq = _compute_prompt_sq(
        text,
        has_action=has_intent,
        has_constraint=has_constraint,
    )
    sq_result = {
        "score": prompt_sq,
        "threshold": t_sq,
        "passes": prompt_sq >= t_sq,
    }

    ed = float(ed_result["score"])
    sq = float(sq_result["score"])

    vague_phrases = ["something", "anything", "stuff"]
    if any(v in text_lower for v in vague_phrases) and not has_constraint:
        sq = _clamp01(sq - float(cur.get("vague_penalty", 0.2)))

    sq_result["score"] = sq
    sq_result["passes"] = sq >= t_sq

    if not has_intent:
        decision = "reject"
        reason = "No clear action (e.g., explain, list, compare)"
        suggestion = "Start with a clear action like 'explain', 'list', or 'compare'"

    elif sq >= 0.7:
        decision = "accept"
        reason = "Clear, structured, and actionable prompt"
        suggestion = "Proceed with this prompt."

    elif sq >= 0.5:
        decision = "review"
        reason = "Good prompt, but could be improved in consistency"
        suggestion = (
            "Add tighter constraints, examples, or specific requirements"
            if not has_constraint
            else "Add a bit more structure or specificity to strengthen the prompt"
        )

    else:
        decision = "reject"
        reason = "Prompt quality is too low for a reliable result"
        suggestion = "Rewrite with a clearer action and more specific detail"

    ed_result["score"] = ed
    ed_result["passes"] = ed >= t_ed

    return {
        "decision": decision,
        "reason": reason,
        "suggestion": suggestion,
        "ed": ed_result,
        "sq": sq_result,
    }
