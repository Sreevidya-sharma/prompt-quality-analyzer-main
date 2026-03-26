from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.utils.paths import project_root

_ROOT = project_root()


def load_arc_tasks(json_path: str | Path | None = None) -> list[dict[str, Any]]:
    p = Path(json_path) if json_path else _ROOT / "data" / "arc_tasks.json"
    if not p.is_file():
        return []
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "id": str(item.get("id", "")),
                "input": str(item.get("input", "")),
                "expected_output": str(item.get("expected_output", "")),
                "type": str(item.get("type", "reasoning")),
            }
        )
    return out
