from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.db.storage import save_dataset_snapshot
from src.features.promptQuality.curate_engine.ingestion.connectors import (
    load_local_json,
    load_text_file,
)
from src.features.promptQuality.curate_engine.preprocessing.deduplicate import (
    deduplicate_records,
)
from src.features.promptQuality.curate_engine.preprocessing.normalize import (
    normalize_record,
)
from src.utils.paths import project_root

_PROJECT_ROOT = project_root()


def _resolve_path(path: str) -> Path | None:
    p = Path(str(path))
    if p.is_file():
        return p
    alt = _PROJECT_ROOT / path
    if alt.is_file():
        return alt
    return None


def fetch_records(source_config: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(source_config, dict):
        return []
    stype = str(source_config.get("type", "") or "").lower().strip()
    path = source_config.get("path", "")
    if not path:
        return []

    p = _resolve_path(str(path))
    if p is None:
        return []

    if stype == "json":
        return load_local_json(p)
    if stype in ("text", "txt", "plain"):
        return load_text_file(p)
    return []


def run_ingestion_pipeline(
    source_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    raw = fetch_records(source_config)
    if not raw:
        return [], save_dataset_snapshot(0, "ingestion-empty")
    normalized = [normalize_record(r) for r in raw]
    deduped = deduplicate_records(normalized)
    path_hint = str(source_config.get("path", "") or "")
    dataset_snapshot_id = save_dataset_snapshot(len(deduped), f"ingestion:{path_hint}")
    return deduped, dataset_snapshot_id
