from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.db.storage import save_dataset_snapshot
from src.features.analysis.analyzers import analyze_failures
from src.features.analysis.evaluation_metrics import compute_m1, compute_m2
from src.features.analysis.task_manager.task_manager import load_all_tasks, sample_tasks
from src.services.model_adapter import ModelAdapter

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _outputs_match(expected: str, response: str) -> bool:
    e = _norm(expected)
    r = _norm(response)
    if not e:
        return not r
    if e == r:
        return True
    if e in r:
        return True
    e2 = re.sub(r"[^\w\d]", "", e)
    r2 = re.sub(r"[^\w\d]", "", r)
    if e2 and e2 in r2:
        return True
    return False


def _prompt_for_task(task: dict[str, Any]) -> str:
    t = str(task.get("type", "")).lower()
    if "input" in task and t == "reasoning":
        return (
            "Answer with only the final answer on one line (no extra words).\n\n"
            f"{task['input']}"
        )
    if "context" in task and "question" in task:
        return (
            "Use the context to answer. Reply with a short phrase only.\n\n"
            f"Context:\n{task['context']}\n\nQuestion:\n{task['question']}"
        )
    return str(task.get("input", task.get("question", "")))


def _cfg_model_version(config: dict[str, Any] | None) -> str:
    if not isinstance(config, dict):
        return "v1.0"
    m = config.get("model") if isinstance(config.get("model"), dict) else {}
    return str(m.get("model_version") or config.get("model_version") or "v1.0")


def run_task_batch(
    model_adapter: ModelAdapter,
    tasks: list[dict[str, Any]],
    config: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for task in tasks:
        prompt = _prompt_for_task(task)
        try:
            inf = model_adapter.infer(prompt)
        except Exception:
            inf = {"response": "", "latency": 0.0, "tokens": 0, "model_version": _cfg_model_version(config)}
        response = str(inf.get("response", "") or "")
        latency = float(inf.get("latency", 0.0) or 0.0)
        tokens = int(inf.get("tokens", 0) or 0)
        model_version = str(inf.get("model_version") or _cfg_model_version(config))
        exp = str(task.get("expected_output", "") or "")
        correct = _outputs_match(exp, response)
        m1 = compute_m1(response, config)
        m2 = compute_m2(response, config)
        try:
            fa = analyze_failures(task, response, exp)
        except Exception:
            fa = {"failure_tags": [], "severity": "low"}
        out.append(
            {
                "task_id": task.get("id", ""),
                "response": response,
                "expected_output": exp,
                "correct": correct,
                "latency": latency,
                "tokens": tokens,
                "m1": m1,
                "m2": m2,
                "model_version": model_version,
                "failure_tags": fa.get("failure_tags") or [],
            }
        )
    return out


def run_evaluation_suite(
    model: ModelAdapter,
    config: dict[str, Any],
    sample_n: int | None = None,
) -> dict[str, Any]:
    mv_default = _cfg_model_version(config)
    if sample_n is not None and sample_n > 0:
        tasks = sample_tasks(sample_n)
    else:
        tasks = load_all_tasks()
    dataset_snapshot_id = save_dataset_snapshot(len(tasks), "evaluation-benchmark")
    results = run_task_batch(model, tasks, config)
    n = len(results)
    if n == 0:
        return {
            "run_id": str(uuid.uuid4()),
            "model_version": mv_default,
            "dataset_snapshot_id": dataset_snapshot_id,
            "total_tasks": 0,
            "accuracy": 0.0,
            "avg_m1": 0.0,
            "avg_m2": 0.0,
            "avg_latency": 0.0,
            "avg_tokens": 0.0,
            "results": [],
        }
    correct_n = sum(1 for r in results if r["correct"])
    model_version = str(results[0].get("model_version") or mv_default)
    out = {
        "run_id": str(uuid.uuid4()),
        "model_version": model_version,
        "dataset_snapshot_id": dataset_snapshot_id,
        "total_tasks": n,
        "accuracy": correct_n / n,
        "avg_m1": sum(r["m1"] for r in results) / n,
        "avg_m2": sum(r["m2"] for r in results) / n,
        "avg_latency": sum(r["latency"] for r in results) / n,
        "avg_tokens": sum(r["tokens"] for r in results) / n,
        "results": results,
    }
    try:
        from src.features.analysis.drift.alerts import trigger_alert
        from src.features.analysis.drift.drift_detector import detect_drift
        from src.features.analysis.drift.time_series import get_metrics_window, store_metric

        ts = datetime.now(timezone.utc).isoformat()
        fd: dict[str, int] = {}
        for row in results:
            for t in row.get("failure_tags") or []:
                if isinstance(t, str):
                    fd[t] = fd.get(t, 0) + 1
        store_metric(
            out["run_id"],
            ts,
            out["avg_m1"],
            out["avg_m2"],
            out["accuracy"],
            failure_distribution=fd or None,
        )
        dcfg = config.get("drift") if isinstance(config.get("drift"), dict) else {}
        w = int(dcfg.get("drift_window_size", 10))
        window = get_metrics_window(w)
        dr = detect_drift(window, config)
        trigger_alert(dr, config)
    except Exception:
        logger.exception("evaluation drift recording failed")
    return out
