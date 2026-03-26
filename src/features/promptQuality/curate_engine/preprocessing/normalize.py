from __future__ import annotations

import re
import uuid
from typing import Any


def normalize_text(text: str) -> str:
    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    s = text.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    text = str(record.get("text", "") or "")
    source = str(record.get("source", "") or "")
    rid = str(record.get("id", "") or "").strip() or str(uuid.uuid4())
    nt = normalize_text(text)
    return {
        "id": rid,
        "text": text,
        "source": source,
        "normalized_text": nt,
    }
