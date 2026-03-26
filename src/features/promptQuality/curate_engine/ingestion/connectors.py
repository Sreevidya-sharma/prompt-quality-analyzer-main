from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


def _record(id_val: str, text: str, source: str) -> dict[str, Any]:
    return {"id": str(id_val), "text": str(text), "source": str(source)}


def _records_from_list(items: list[Any], default_source: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            out.append(_record(str(uuid.uuid4()), item.strip(), default_source))
        elif isinstance(item, dict):
            tid = item.get("id") or str(uuid.uuid4())
            txt = item.get("text") or item.get("content") or ""
            so = item.get("source") or default_source
            out.append(_record(str(tid), str(txt), str(so)))
    return out


def load_local_json(file_path: str | Path) -> list[dict[str, Any]]:
    path = Path(file_path)
    if not path.is_file():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    src = str(path)
    if isinstance(data, list):
        return _records_from_list(data, src)
    if isinstance(data, dict):
        recs = data.get("records")
        if isinstance(recs, list):
            return _records_from_list(recs, src)
    return []


def load_text_file(file_path: str | Path) -> list[dict[str, Any]]:
    path = Path(file_path)
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []

    src = str(path)
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return []

    return [_record(f"{path.stem}-{i}", line, src) for i, line in enumerate(lines)]
