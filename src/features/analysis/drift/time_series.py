from __future__ import annotations

from typing import Any

from backend.db.storage import get_metrics_over_time
from backend.db.storage import get_metrics_window as _get_metrics_window_storage
from backend.db.storage import list_alerts_recent as _list_alerts_recent
from backend.db.storage import store_metric as _store_metric_storage


def store_metric(
    run_id: str,
    timestamp: str,
    m1: float | None,
    m2: float | None,
    accuracy: float | None = None,
    failure_distribution: dict[str, int] | None = None,
) -> None:
    _store_metric_storage(
        run_id, timestamp, m1, m2, accuracy, failure_distribution=failure_distribution
    )


def get_recent_metrics(limit: int) -> list[dict[str, Any]]:
    return get_metrics_over_time(limit=limit)


def get_metrics_window(window_size: int) -> list[dict[str, Any]]:
    return _get_metrics_window_storage(window_size)


def get_m1_m2_trend(limit: int = 30) -> list[dict[str, Any]]:
    rows = get_metrics_over_time(limit=limit)
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "run_id": r.get("run_id"),
                "t": r.get("timestamp"),
                "m1": r.get("m1"),
                "m2": r.get("m2"),
                "accuracy": r.get("accuracy"),
            }
        )
    return out


def get_drift_panel(config: dict[str, Any]) -> dict[str, Any]:
    from src.features.analysis.drift.drift_detector import detect_drift

    dcfg = config.get("drift") if isinstance(config.get("drift"), dict) else {}
    w = int(dcfg.get("drift_window_size", 10))
    window = get_metrics_window(w)
    drift_status = detect_drift(window, config)
    trend = get_m1_m2_trend(max(30, w + 5))
    return {
        "metrics_m1_m2_trend": trend,
        "drift_status": drift_status,
    }


def get_recent_alerts(limit: int) -> list[dict[str, Any]]:
    return _list_alerts_recent(limit)
