from __future__ import annotations

import pytest

from src.features.analysis import compute_m1, compute_m2


def test_m1_structured_high(config: dict) -> None:
    text = (
        "Sleep improves memory because the brain consolidates information. "
        "Therefore, good sleep helps learning. "
        "First, deep sleep stabilizes facts. "
        "Second, REM supports integration. "
        "Finally, a regular schedule helps both."
    )
    m = compute_m1(text, config)
    assert m >= 0.85


def test_m1_unstructured_low(config: dict) -> None:
    m = compute_m1("yeah ok", config)
    assert m < 0.25


def test_m1_empty(config: dict) -> None:
    assert compute_m1("", config) == 0.0


def test_m1_single_sentence(config: dict) -> None:
    m = compute_m1("Sleep helps memory.", config)
    assert m < 0.35


def test_m2_consistent_high(config: dict) -> None:
    text = (
        "One idea about sleep. "
        "Another idea about exercise. "
        "A third idea about diet and hydration."
    )
    m = compute_m2(text, config)
    assert m >= 0.9


def test_m2_contradictory_low(config: dict) -> None:
    text = "The sky is blue. The sky is not blue."
    assert compute_m2(text, config) <= 0.25


def test_m2_repetition_penalized_vs_clean(config: dict) -> None:
    clean = (
        "One idea about sleep. Two ideas about food. Three ideas about exercise."
    )
    repeated = "Sleep helps memory. Sleep helps memory. Sleep helps memory."
    assert compute_m2(repeated, config) < compute_m2(clean, config)
