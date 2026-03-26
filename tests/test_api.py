from __future__ import annotations

from typing import Any
from time import perf_counter

import pytest
from fastapi.testclient import TestClient

from src.services.model_adapter import ModelAdapter


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    def fake_infer(self: ModelAdapter, prompt: str) -> dict[str, Any]:
        return {"response": "", "tokens": 0, "latency": 0.0, "model_version": "v1.0"}

    monkeypatch.setattr(ModelAdapter, "infer", fake_infer)
    monkeypatch.setattr("src.pipeline.save_run", lambda _data: True)

    from backend.api.app import app

    with TestClient(app) as c:
        yield c


def _analyze_schema(data: dict[str, Any]) -> None:
    required = {
        "success",
        "error",
        "score",
        "feedback",
        "run_id",
        "created_at",
        "prompt",
        "response",
        "decision",
        "reason",
        "suggestion",
        "ed_score",
        "sq_score",
        "m1",
        "m2",
        "model_version",
        "dataset_snapshot_id",
        "failure_tags",
        "failure_severity",
        "scores",
        "prompt_logged",
    }
    assert required.issubset(set(data.keys()))
    assert isinstance(data["success"], bool)
    assert data["error"] is None or isinstance(data["error"], str)
    assert isinstance(data["score"], (int, float))
    assert isinstance(data["feedback"], list)
    assert isinstance(data["prompt"], str)
    assert isinstance(data["response"], str)
    assert data["decision"] in ("accept", "reject", "review")
    assert isinstance(data["ed_score"], (int, float))
    assert isinstance(data["sq_score"], (int, float))
    assert isinstance(data["scores"], dict)
    assert isinstance(data["scores"]["ed"], (int, float))
    assert isinstance(data["scores"]["sq"], (int, float))


def test_analyze_ok(client: TestClient) -> None:
    r = client.post(
        "/analyze",
        json={"prompt": "Why does sleep help memory? Give steps and an example."},
    )
    assert r.status_code == 200
    _analyze_schema(r.json())
    assert r.json()["success"] is True
    assert r.json()["decision"] == "accept"


def test_analyze_empty_body_400(client: TestClient) -> None:
    r = client.post("/analyze", json={"prompt": "   "})
    assert r.status_code == 400
    body = r.json()
    _analyze_schema(body)
    assert body["success"] is False
    assert isinstance(body["error"], str)


def test_analyze_missing_text_422(client: TestClient) -> None:
    r = client.post("/analyze", json={})
    assert r.status_code == 400
    body = r.json()
    _analyze_schema(body)
    assert body["success"] is False


def test_analyze_invalid_json(client: TestClient) -> None:
    r = client.post(
        "/analyze",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    body = r.json()
    _analyze_schema(body)
    assert body["success"] is False


def test_analyze_wrong_type(client: TestClient) -> None:
    r = client.post("/analyze", json={"prompt": 123})
    assert r.status_code == 400
    body = r.json()
    _analyze_schema(body)
    assert body["success"] is False


def test_analyze_edge_random_chars(client: TestClient) -> None:
    r = client.post("/analyze", json={"prompt": "asdfasdf qwerty zxcvbn"})
    assert r.status_code == 200
    body = r.json()
    _analyze_schema(body)
    assert body["decision"] in ("accept", "reject", "review")


def test_analyze_edge_long_input(client: TestClient) -> None:
    text = ("Why is sleep important? " * 200).strip()
    r = client.post("/analyze", json={"prompt": text})
    assert r.status_code == 200
    _analyze_schema(r.json())


def test_analyze_legacy_text_field_still_supported(client: TestClient) -> None:
    r = client.post("/analyze", json={"text": "Explain why sleep improves recall."})
    assert r.status_code == 200
    _analyze_schema(r.json())


def test_analyze_null_prompt_rejected(client: TestClient) -> None:
    r = client.post("/analyze", json={"prompt": None})
    assert r.status_code == 400
    body = r.json()
    _analyze_schema(body)
    assert body["success"] is False


def test_analyze_special_characters_ok(client: TestClient) -> None:
    prompt = "!!@@##$$%%^^&&**(())) -- <>?/\\|~` 😀"
    r = client.post("/analyze", json={"text": prompt})
    assert r.status_code == 200
    body = r.json()
    _analyze_schema(body)
    assert body["success"] is True


def test_analyze_non_english_ok(client: TestClient) -> None:
    prompt = "¿Cómo mejora el sueño la memoria? Explica con ejemplos."
    r = client.post("/analyze", json={"text": prompt})
    assert r.status_code == 200
    body = r.json()
    _analyze_schema(body)
    assert body["success"] is True


def test_analyze_too_long_rejected(client: TestClient) -> None:
    prompt = "A" * 10001
    r = client.post("/analyze", json={"text": prompt})
    assert r.status_code == 400
    body = r.json()
    _analyze_schema(body)
    assert body["success"] is False


def test_stats_recent_ok(client: TestClient) -> None:
    analyze = client.post("/analyze", json={"prompt": "Why does sleep matter for memory?"})
    assert analyze.status_code == 200

    stats = client.get("/stats")
    assert stats.status_code == 200
    stats_body = stats.json()
    assert "total_runs" in stats_body
    assert "ed_sq_series" in stats_body

    recent = client.get("/recent")
    assert recent.status_code == 200
    recent_body = recent.json()
    assert isinstance(recent_body, list)


def test_reject_suggestion_not_positive(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.pipeline.compute_score",
        lambda *_args, **_kwargs: {
            "decision": "reject",
            "reason": "Prompt quality is too low for a reliable result",
            "suggestion": "Good prompt!",
            "ed": {"score": 0.2, "threshold": 0.6, "passes": False},
            "sq": {"score": 0.3, "threshold": 0.65, "passes": False},
        },
    )

    r = client.post("/analyze", json={"prompt": "unclear text"})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "reject"
    assert "Good prompt" not in body["suggestion"]


def test_same_prompt_is_deterministic_across_repeats(client: TestClient) -> None:
    prompt = "Explain API rate limiting in detail"
    observed: list[tuple[Any, ...]] = []
    for _ in range(5):
        response = client.post("/analyze", json={"prompt": prompt})
        assert response.status_code == 200
        body = response.json()
        observed.append(
            (
                body["decision"],
                body["reason"],
                body["suggestion"],
                round(float(body["sq_score"]), 6),
                round(float(body["ed_score"]), 6),
                    round(float(body["scores"]["sq"]), 6),
                    round(float(body["scores"]["ed"]), 6),
            )
        )
    assert len(set(observed)) == 1


def test_analyze_repeated_requests_stability_and_latency(client: TestClient) -> None:
    prompt = "Explain spaced repetition for long-term memory retention."
    timings_ms: list[float] = []
    for _ in range(25):
        start = perf_counter()
        response = client.post("/analyze", json={"text": prompt})
        timings_ms.append((perf_counter() - start) * 1000.0)
        assert response.status_code == 200
        body = response.json()
        _analyze_schema(body)
        assert body["success"] is True
    avg_ms = sum(timings_ms) / len(timings_ms)
    assert avg_ms < 500.0


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Explain API rate limiting in detail", "accept"),
        ("Explain API rate limiting", {"accept", "review"}),
        ("api fast", "reject"),
    ],
)
def test_validation_prompts_are_classified_consistently(client: TestClient, prompt: str, expected: Any) -> None:
    response = client.post("/analyze", json={"prompt": prompt})
    assert response.status_code == 200
    decision = response.json()["decision"]
    if isinstance(expected, set):
        assert decision in expected
    else:
        assert decision == expected
