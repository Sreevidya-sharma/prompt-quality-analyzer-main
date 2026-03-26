from __future__ import annotations

from typing import Any


def _drift_cfg(config: dict[str, Any]) -> dict[str, float | int]:
    d = config.get("drift") if isinstance(config.get("drift"), dict) else {}
    return {
        "drift_window_size": int(d.get("drift_window_size", 10)),
        "m1_drop_threshold": float(d.get("m1_drop_threshold", 0.08)),
        "m2_drop_threshold": float(d.get("m2_drop_threshold", 0.08)),
    }


def _severity(drop: float, threshold: float) -> str:
    if threshold <= 0:
        return "low"
    ratio = drop / threshold
    if ratio >= 2.0:
        return "high"
    if ratio >= 1.5:
        return "medium"
    return "low"


def detect_drift(metrics_window: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    cfg = _drift_cfg(config)
    t1 = float(cfg["m1_drop_threshold"])
    t2 = float(cfg["m2_drop_threshold"])

    if len(metrics_window) < 2:
        return {
            "drift_detected": False,
            "metric": "m1",
            "drop_value": 0.0,
            "severity": "low",
        }

    current = metrics_window[-1]
    previous = metrics_window[:-1]

    def _mean(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    m1_prev = [float(r["m1"]) for r in previous if r.get("m1") is not None]
    m2_prev = [float(r["m2"]) for r in previous if r.get("m2") is not None]

    out: dict[str, Any] = {
        "drift_detected": False,
        "metric": "m1",
        "drop_value": 0.0,
        "severity": "low",
    }

    cm1 = current.get("m1")
    if cm1 is not None and m1_prev:
        base_m1 = _mean(m1_prev)
        drop_m1 = base_m1 - float(cm1)
        if drop_m1 > t1:
            out["drift_detected"] = True
            out["metric"] = "m1"
            out["drop_value"] = round(drop_m1, 6)
            out["severity"] = _severity(drop_m1, t1)
            return out

    cm2 = current.get("m2")
    if cm2 is not None and m2_prev:
        base_m2 = _mean(m2_prev)
        drop_m2 = base_m2 - float(cm2)
        if drop_m2 > t2:
            out["drift_detected"] = True
            out["metric"] = "m2"
            out["drop_value"] = round(drop_m2, 6)
            out["severity"] = _severity(drop_m2, t2)
            return out

    return out
