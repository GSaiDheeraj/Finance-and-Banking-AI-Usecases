"""
Logging configuration for the portfolio-monitoring pipeline.

A single named logger (`portfolio_monitor`) writes human-readable lines to stderr, and —
when `PM_LOG_FILE` is set — also appends to that file. This is where the reasoning trace is
recorded: what market data was fetched, which sleeves drifted, which limits breached, what
the constructor selected and why, and where guardrails corrected an LLM proposal.

Env vars
--------
* `PM_LOG_LEVEL` — DEBUG | INFO | WARNING | ERROR (default INFO).
* `PM_LOG_FILE`  — optional path; logs are appended here as well as stderr.
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False
_LOGGER_NAME = "portfolio_monitor"


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    """Return the package logger, configuring handlers once.

    `name` selects a child logger under the package namespace so call sites can
    use ``get_logger(__name__)``; configuration is still applied once at the root.
    """
    global _CONFIGURED
    logger = logging.getLogger(name)
    root = logging.getLogger(_LOGGER_NAME)
    if not _CONFIGURED:
        level_name = (os.getenv("PM_LOG_LEVEL", "INFO") or "INFO").upper()
        level = getattr(logging, level_name, None)
        if not isinstance(level, int):
            level = logging.INFO
        root.setLevel(level)

        log_formatter = logging.Formatter(
            "%(asctime)s [portfolio_monitor] %(levelname)-7s %(message)s", "%H:%M:%S"
        )
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(log_formatter)
        root.addHandler(stream_handler)

        log_file = os.getenv("PM_LOG_FILE")
        if log_file:
            try:
                file_handler = logging.FileHandler(log_file, encoding="utf-8")
                file_handler.setFormatter(log_formatter)
                root.addHandler(file_handler)
            except OSError as exc:
                root.warning("Could not open log file %r: %s", log_file, exc)

        root.propagate = False
        _CONFIGURED = True
    return logger
