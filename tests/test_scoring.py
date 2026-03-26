from __future__ import annotations

from typing import Any

import pytest

from src.features.promptQuality.scoring import curate_text


def _scores(r: dict[str, Any]) -> tuple[float, float]:
    return float(r["ed"]["score"]), float(r["sq"]["score"])


def test_curate_valid_accept(config: dict, keywords: list[str]) -> None:
    text = (
        "Explain how sleep benefits memory consolidation. "
        "Include three clear steps and one concrete example for students."
    )
    r = curate_text(text, keywords, "user question about memory", config)
    assert r["decision"] == "accept"
    ed, sq = _scores(r)
    assert 0.0 <= ed <= 1.0 and 0.0 <= sq <= 1.0
    assert ed >= float(config["thresholds"]["ed_threshold"])
    assert sq >= float(config["thresholds"]["sq_threshold"])


def test_curate_review_weak_structure(config: dict, keywords: list[str]) -> None:
    text = "Explain what sleep is."
    r = curate_text(text, keywords, "prompt", config)
    assert r["decision"] in ("accept", "review")
    ed, sq = _scores(r)
    assert 0.0 <= ed <= 1.0 and 0.0 <= sq <= 1.0


def test_curate_reject_no_intent(config: dict, keywords: list[str]) -> None:
    text = (
        "The mitochondria is the powerhouse of the cell. "
        "It generates ATP through respiration."
    )
    r = curate_text(text, keywords, "biology", config)
    assert r["decision"] == "reject"
    ed, sq = _scores(r)
    assert 0.0 <= ed <= 1.0 and 0.0 <= sq <= 1.0


def test_curate_accepts_action_with_leading_formatting(config: dict, keywords: list[str]) -> None:
    text = "\n\n  - Explain how sleep supports memory.\n  - Include one example and clear steps."
    r = curate_text(text, keywords, "prompt", config)
    assert r["decision"] in ("accept", "review")
    assert r["reason"] != "No clear action (e.g., explain, list, compare)"


def test_curate_detects_action_anywhere_in_prompt_field(config: dict, keywords: list[str]) -> None:
    generated_text = "Here is a short response."
    prompt = "\n\n- For my assignment, please explain how sleep improves memory with examples."
    r = curate_text(generated_text, keywords, prompt, config)
    assert r["reason"] != "No clear action (e.g., explain, list, compare)"


def test_curate_reject_too_short(config: dict, keywords: list[str]) -> None:
    r = curate_text("hi x", keywords, "prompt", config)
    assert r["decision"] == "reject"
    ed, sq = _scores(r)
    assert ed <= 0.3 and sq <= 0.3


def test_curate_reject_spam(config: dict, keywords: list[str]) -> None:
    text = "Buy now!!! This is amazing amazing amazing!!! Limited offer!!!"
    r = curate_text(text, keywords, "product description", config)
    assert r["decision"] == "reject"
    ed, sq = _scores(r)
    assert 0.0 <= ed <= 1.0 and 0.0 <= sq <= 1.0


def test_curate_reject_empty(config: dict, keywords: list[str]) -> None:
    r = curate_text("", keywords, "prompt", config)
    assert r["decision"] == "reject"
    assert _scores(r) == (0.0, 0.0)


def test_curate_reject_gibberish(config: dict, keywords: list[str]) -> None:
    r = curate_text("asdfasdf qwerty zxcvbn nonsense", keywords, "prompt", config)
    assert r["decision"] == "reject"
    ed, sq = _scores(r)
    assert 0.0 <= ed <= 1.0 and 0.0 <= sq <= 1.0


def test_curate_long_input_no_crash(config: dict, keywords: list[str]) -> None:
    chunk = "Sleep improves memory and supports learning. "
    text = chunk * 400
    r = curate_text(text, keywords, "health", config)
    assert r["decision"] in ("accept", "reject", "review")
    ed, sq = _scores(r)
    assert 0.0 <= ed <= 1.0 and 0.0 <= sq <= 1.0


def test_curate_repeated_phrases(config: dict, keywords: list[str]) -> None:
    text = "wake wake wake work work work routine routine routine " * 5
    r = curate_text(text, keywords, "routine", config)
    assert r["decision"] in ("accept", "reject", "review")
    ed, sq = _scores(r)
    assert 0.0 <= ed <= 1.0 and 0.0 <= sq <= 1.0
