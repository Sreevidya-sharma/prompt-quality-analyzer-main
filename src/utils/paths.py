from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """Repository root (contains ``configs``, ``src``, ``backend``)."""
    return Path(__file__).resolve().parents[2]
