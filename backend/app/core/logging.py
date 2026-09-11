"""Minimal logging configuration for the API process."""

from __future__ import annotations

import logging

from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    """Apply the configured log level to the root logger."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
