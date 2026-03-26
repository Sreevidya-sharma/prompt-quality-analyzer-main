from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.db.storage import save_dataset_snapshot, save_run
from src.features.promptQuality.scoring import compute_score
from src.services.model_adapter import ModelAdapter
from src.utils.config_loader import load_config

logger = logging.getLogger("pipeline")

_ACTION_VERBS = ("explain", "list", "compare", "describe", "analyze", "outline", "summarize")
_CONSTRAINT_HINTS = ("step", "example", "limit", "format", "tone", "bullet", "word")


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", str(text or "").lower())


def _compute_breakdown(prompt_text: str, *, ed_score: float, sq_score: float) -> dict[str, float]:
    tokens = _tokenize(prompt_text)
    token_count = len(tokens)
    unique_ratio = (len(set(tokens)) / token_count) if token_count else 0.0
    lower_text = str(prompt_text or "").lower()
    has_action = any(v in lower_text for v in _ACTION_VERBS)
    has_constraint = any(k in lower_text for k in _CONSTRAINT_HINTS)

    clarity = _clamp01(0.6 * unique_ratio + 0.4 * sq_score)
    structure = _clamp01(0.45 * ed_score + 0.35 * sq_score + (0.2 if has_constraint else 0.0))
    actionability = _clamp01(0.5 * sq_score + (0.35 if has_action else 0.0) + (0.15 if has_constraint else 0.0))
    return {
        "clarity": round(clarity, 4),
        "structure": round(structure, 4),
        "actionability": round(actionability, 4),
    }


def _generate_reject_suggestion(prompt_text: str) -> str:
    raw = str(prompt_text or "").strip()
    if not raw:
        return "Try: Explain the topic, include key context, and provide 3 concise bullet points."
    lower_text = raw.lower()
    has_action = any(v in lower_text for v in _ACTION_VERBS)
    has_constraint = any(k in lower_text for k in _CONSTRAINT_HINTS)

    improved = raw
    if not has_action:
        improved = f"Explain {improved[0].lower() + improved[1:]}" if len(improved) > 1 else f"Explain {improved}"
    if "context:" not in improved.lower():
        improved = f"{improved}. Context: include relevant background details."
    if not has_constraint:
        improved = f"{improved} Constraints: use 3 bullet points and one example."
    return f"Try this improved prompt: {improved}"


def _cfg_model_version(config: dict[str, Any]) -> str:
    m = config.get("model") if isinstance(config.get("model"), dict) else {}
    return str(m.get("model_version") or config.get("model_version") or "v1.0")


def _finalize_decision(
    base_decision: str,
    reason: str,
    suggestion: str,
    *,
    sq_score: float,
    ed_score: float,
) -> tuple[str, str, str]:
    decision = str(base_decision or "reject")
    final_reason = str(reason or "")
    final_suggestion = str(suggestion or "")

    if decision == "accept":
        final_reason = "Clear, structured, and actionable prompt"
        final_suggestion = "Proceed with this prompt."
    elif decision == "review" and "consistency" not in final_reason.lower():
        final_reason = "Good prompt, but could be improved in consistency"
        if not final_suggestion:
            final_suggestion = "Add tighter constraints or examples to improve consistency"
    elif decision == "reject" and "good prompt" in final_suggestion.lower():
        final_suggestion = "Rewrite with a clearer action and more specific detail"

    print("SQ:", sq_score, "ED:", ed_score, "M2:", None, "Decision:", decision)
    return decision, final_reason, final_suggestion


def _run_pipeline_single(
    input_text: str,
    *,
    config: dict[str, Any],
    model: ModelAdapter,
    persist: bool = True,
    dataset_snapshot_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    run_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    if dataset_snapshot_id is None:
        dataset_snapshot_id = save_dataset_snapshot(0, "pipeline-single")

    keywords = config["keywords"]
    del model
    response_text = ""
    model_version = _cfg_model_version(config)

    curation = compute_score(input_text, keywords, config)

    decision = str(curation.get("decision", "reject"))
    reason = str(curation.get("reason", ""))
    suggestion = str(curation.get("suggestion", ""))

    ed_score = float(curation["ed"]["score"])
    sq_score = float(curation["sq"]["score"])

    m1 = None
    m2 = None
    decision, reason, suggestion = _finalize_decision(
        decision,
        reason,
        suggestion,
        sq_score=sq_score,
        ed_score=ed_score,
    )
    if decision == "reject":
        suggestion = _generate_reject_suggestion(input_text)

    model_name = str(config.get("model", {}).get("openai_model", "default"))
    breakdown = _compute_breakdown(
        input_text,
        ed_score=ed_score,
        sq_score=sq_score,
    )
    score = round((ed_score + sq_score) / 2.0, 6)

    out: dict[str, Any] = {
        "run_id": run_id,
        "created_at": created_at,
        "prompt": input_text,
        "response": response_text,
        "decision": decision,
        "reason": reason,
        "suggestion": suggestion,
        "score": score,
        "ed_score": ed_score,
        "sq_score": sq_score,
        "breakdown": breakdown,
        "m1": m1,
        "m2": m2,
        "model_version": model_version,
        "dataset_snapshot_id": dataset_snapshot_id,
        "failure_tags": [],
        "failure_severity": "",
    }

    logged = False
    if persist:
        logger.info(
            "PIPELINE BEFORE SAVE run_id=%s prompt_len=%d decision=%s ed=%.4f sq=%.4f model_version=%s dataset_snapshot_id=%s",
            run_id,
            len(input_text),
            decision,
            ed_score,
            sq_score,
            model_version,
            dataset_snapshot_id,
        )
        logged = bool(save_run({**out, "model_name": model_name}))
        if logged:
            logger.info("PIPELINE SAVE RESULT run_id=%s saved=True", run_id)
        else:
            logger.error(
                "PIPELINE SAVE RESULT run_id=%s saved=False prompt_preview=%r decision=%s",
                run_id,
                input_text[:120],
                decision,
            )

    return out, logged


def run_pipeline_dataset(
    source_config: dict[str, Any],
    *,
    config: dict[str, Any],
    model: ModelAdapter,
    persist: bool = True,
) -> list[dict[str, Any]]:
    from src.features.promptQuality.curate_engine.ingestion.ingestion_pipeline import (
        run_ingestion_pipeline,
    )

    records, dataset_snapshot_id = run_ingestion_pipeline(source_config)
    if not records:
        return []
    out: list[dict[str, Any]] = []
    for rec in records:
        result, logged = _run_pipeline_single(
            rec["text"],
            config=config,
            model=model,
            persist=persist,
            dataset_snapshot_id=dataset_snapshot_id,
        )
        out.append(
            {
                "id": rec["id"],
                "text": rec["text"],
                "source": rec["source"],
                "normalized_text": rec["normalized_text"],
                **result,
                "prompt_logged": logged,
            }
        )
    return out


def run_pipeline(
    input_text: str = "",
    *,
    config: dict[str, Any],
    model: ModelAdapter,
    persist: bool = True,
    dataset_mode: bool = False,
    source_config: dict[str, Any] | None = None,
    evaluation_mode: bool | None = None,
    evaluation_sample_n: int | None = None,
) -> tuple[dict[str, Any], bool] | list[dict[str, Any]] | dict[str, Any]:
    """
    Single-record: infer → curate(response) → M2 gate → M1/M2 → optional persist.

    With ``dataset_mode=True`` and ``source_config``, runs ingestion then scores each record.

    With ``evaluation_mode`` (or config ``evaluation_mode: true``), runs benchmark suite.
    """
    ev = evaluation_mode if evaluation_mode is not None else bool(config.get("evaluation_mode", False))
    if ev:
        from src.features.analysis.runner.evaluation_runner import run_evaluation_suite

        sn = evaluation_sample_n
        if sn is None and config.get("evaluation_sample_n") is not None:
            try:
                sn = int(config["evaluation_sample_n"])
            except (TypeError, ValueError):
                sn = None
        return run_evaluation_suite(model, config, sample_n=sn)
    if dataset_mode and source_config is not None:
        return run_pipeline_dataset(source_config, config=config, model=model, persist=persist)
    return _run_pipeline_single(input_text, config=config, model=model, persist=persist)


if __name__ == "__main__":
    from src.utils.paths import project_root

    _cfg = load_config(str(project_root() / "configs" / "base.yaml"))
    _model = ModelAdapter()
    _demo = "Why is sleep important for memory?"
    _out, _ = run_pipeline(_demo, config=_cfg, model=_model, persist=False)
    print(json.dumps(_out, indent=2))
