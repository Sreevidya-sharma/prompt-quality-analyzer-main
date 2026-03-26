from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.db.storage import save_alert

logger = logging.getLogger(__name__)


def trigger_alert(drift_result: dict[str, Any], config: dict[str, Any] | None = None) -> None:
    if not drift_result.get("drift_detected"):
        return
    aid = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    metric = str(drift_result.get("metric", ""))
    sev = str(drift_result.get("severity", "low"))
    dv = float(drift_result.get("drop_value", 0.0))
    msg = f"Metric {metric} dropped vs baseline: delta={dv:.4f} (severity={sev})"
    save_alert(aid, ts, metric, sev, msg)
    logger.warning("DRIFT %s", msg)
    print(f"[DRIFT ALERT] {msg}")
