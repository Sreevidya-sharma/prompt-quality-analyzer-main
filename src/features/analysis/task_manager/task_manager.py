from __future__ import annotations

import random
from typing import Any

from src.features.analysis.benchmarks.arc.arc_loader import load_arc_tasks
from src.features.analysis.benchmarks.ruler.ruler_loader import load_ruler_tasks


def load_all_tasks() -> list[dict[str, Any]]:
    return load_arc_tasks() + load_ruler_tasks()


def sample_tasks(n: int) -> list[dict[str, Any]]:
    all_t = load_all_tasks()
    if not all_t:
        return []
    if n >= len(all_t):
        return list(all_t)
    return random.sample(all_t, n)


def get_tasks_by_type(task_type: str) -> list[dict[str, Any]]:
    t = str(task_type or "").strip().lower()
    return [x for x in load_all_tasks() if str(x.get("type", "")).lower() == t]
