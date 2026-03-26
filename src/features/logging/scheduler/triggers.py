from __future__ import annotations

import logging
from typing import Any

from src.features.logging.scheduler.scheduler import run_evaluation_locked
from src.services.model_adapter import ModelAdapter

logger = logging.getLogger(__name__)


def trigger_manual_run(config: dict[str, Any], model: ModelAdapter) -> dict[str, Any] | None:
    return run_evaluation_locked(config, model)


def trigger_on_new_data(config: dict[str, Any], model: ModelAdapter) -> None:
    """Stub: hook when new ingested data arrives."""
    logger.debug("trigger_on_new_data: not implemented")


def trigger_on_model_update(config: dict[str, Any], model: ModelAdapter) -> None:
    """Stub: hook when model weights or adapter config change."""
    logger.debug("trigger_on_model_update: not implemented")
