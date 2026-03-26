from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.config_loader import load_config


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def config(project_root: Path) -> dict:
    return load_config(str(project_root / "configs" / "base.yaml"))


@pytest.fixture(scope="session")
def keywords(config: dict) -> list[str]:
    return list(config["keywords"])
