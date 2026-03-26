from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from src.services.model_adapter import ModelAdapter

logger = logging.getLogger(__name__)

_stop = threading.Event()
_worker: threading.Thread | None = None
_eval_lock = threading.Lock()
_eval_busy = False


def _interval_seconds(config: dict[str, Any]) -> int:
    try:
        return max(60, int(config.get("evaluation_interval_seconds", 3600)))
    except (TypeError, ValueError):
        return 3600


def run_evaluation_locked(
    config: dict[str, Any],
    model: ModelAdapter,
) -> dict[str, Any] | None:
    global _eval_busy
    with _eval_lock:
        if _eval_busy:
            logger.info("evaluation skipped: another run is in progress")
            return None
        _eval_busy = True
    t0 = time.perf_counter()
    started = datetime.now(timezone.utc).isoformat()
    ok = False
    out: dict[str, Any] | None = None
    try:
        from src.features.analysis.runner.evaluation_runner import run_evaluation_suite

        sn = config.get("evaluation_sample_n")
        if sn is None or (isinstance(sn, str) and not str(sn).strip()):
            sample_n = None
        else:
            try:
                sample_n = int(sn)
            except (TypeError, ValueError):
                sample_n = None
        if sample_n is not None and sample_n <= 0:
            sample_n = None
        logger.info("evaluation run start at=%s", started)
        out = run_evaluation_suite(model, config, sample_n=sample_n)
        ok = True
        return out
    except Exception:
        logger.exception("evaluation run failed")
        return {"error": "evaluation_failed", "run_id": None}
    finally:
        elapsed = time.perf_counter() - t0
        ended = datetime.now(timezone.utc).isoformat()
        logger.info(
            "evaluation run end at=%s elapsed_s=%.2f success=%s",
            ended,
            elapsed,
            ok,
        )
        with _eval_lock:
            _eval_busy = False


def _scheduler_loop(config: dict[str, Any], model: ModelAdapter) -> None:
    while not _stop.is_set():
        run_evaluation_locked(config, model)
        sec = _interval_seconds(config)
        if _stop.wait(timeout=sec):
            break


def start_scheduler(config: dict[str, Any], model: ModelAdapter) -> None:
    global _worker
    if bool(config.get("scheduler_enabled")) is not True:
        logger.info("scheduler disabled in config")
        return
    if _worker is not None and _worker.is_alive():
        logger.info("scheduler already running")
        return
    _stop.clear()

    def _run() -> None:
        logger.info("scheduler worker started")
        try:
            _scheduler_loop(config, model)
        finally:
            logger.info("scheduler worker stopped")

    _worker = threading.Thread(target=_run, name="eval-scheduler", daemon=True)
    _worker.start()


def stop_scheduler() -> None:
    global _worker
    _stop.set()
    if _worker is not None:
        _worker.join(timeout=15.0)
    _worker = None


def get_scheduler_status() -> str:
    if _worker is not None and _worker.is_alive():
        return "running"
    return "stopped"
