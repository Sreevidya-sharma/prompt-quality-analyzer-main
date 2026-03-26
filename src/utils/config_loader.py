from pathlib import Path
from typing import Any

import yaml

from src.utils.paths import project_root


def default_config_path() -> str:
    return str(project_root() / "configs" / "base.yaml")


def load_config(path: str | None = None) -> dict[str, Any]:
    cfg_path = path or default_config_path()
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return {}
    return data
